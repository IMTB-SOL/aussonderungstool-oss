"""Auth-Router: E-Mail/Passwort Registrierung/Login/Logout + Session-Middleware."""

import logging
import os
import re
import time
from collections import deque

from config import COOKIE_SECURE
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from models import Session, engine, get_db
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from utils.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    azure_aktiv,
    ermittle_auth_modus,
    erzeuge_login_url,
    erzeuge_session_cookie,
    ist_azure_konfiguriert,
    lade_user,
    passwort_aktiv,
    pruefe_session,
    registriere_user,
    upsert_azure_user,
    verarbeite_callback,
    verifiziere_login,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Einfache E-Mail-Syntaxprüfung (kein voller RFC — reicht zur Eingabevalidierung).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _passwort_min() -> int:
    """Mindestlänge des Passworts (pro Aufruf gelesen, damit testbar)."""
    try:
        return int(os.getenv("AUTH_PASSWORT_MIN", "12"))
    except ValueError:
        return 12


# Pfade ohne Authentifizierung. /metrics wird separat per Bearer-Token geschützt
# (siehe main.py); /health und /ready ruft das Kubelet auf.
_AUTH_AUSNAHMEN = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/config",
    "/api/auth/azure/login",
    "/api/auth/callback",
    "/health",
    "/ready",
    "/metrics",
    "/impressum",
    "/datenschutz",
}
_STATISCHE_ENDUNGEN = {
    ".html",
    ".js",
    ".jsx",
    ".css",
    ".svg",
    ".png",
    ".jpg",
    ".ico",
    ".woff",
    ".woff2",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Prüft das Session-Cookie und legt den User in request.state.user (dict)."""

    async def dispatch(self, request: Request, call_next):
        pfad = request.url.path
        if (
            pfad in _AUTH_AUSNAHMEN
            or pfad == "/"
            # Statische Dateien sind öffentlich — aber nie unterhalb von /api/.
            or (not pfad.startswith("/api/") and any(pfad.endswith(e) for e in _STATISCHE_ENDUNGEN))
        ):
            return await call_next(request)

        user = _user_aus_request(request)
        if not user:
            if pfad.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "Nicht angemeldet."})
            base_path = os.getenv("BASE_PATH", "").rstrip("/")
            return RedirectResponse(url=f"{base_path}/login.html")

        request.state.user = user
        return await call_next(request)


def _user_aus_request(request: Request) -> dict | None:
    """Liest das Session-Cookie und lädt den freigeschalteten User frisch aus der DB."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    payload = pruefe_session(cookie) if cookie else None
    if not payload or "id" not in payload:
        return None
    db = Session(engine)
    try:
        user = lade_user(db, payload["id"])
        if user is None or not user.approved:
            return None
        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "is_admin": user.is_admin,
            "provider": user.provider,
        }
    finally:
        db.close()


# --- Rate-Limiting (In-Memory, pro Client-IP) -------------------------------
_rate_store: dict[str, deque] = {}

_RATE_STORE_MAX = 10_000  # Obergrenze, ab der verwaiste Einträge aufgeräumt werden


def _bereinige_rate_store(jetzt: float) -> None:
    """Entfernt Rate-Limit-Einträge, deren jüngster Zeitstempel älter als das
    größte konfigurierte Fenster ist — verhindert unbegrenztes Wachstum von
    _rate_store (v. a. bei wechselnden IPs)."""
    max_fenster = max(
        int(os.getenv("AUTH_REGISTER_WINDOW", "600")),
        int(os.getenv("AUTH_LOGIN_WINDOW", "300")),
    )
    for k in list(_rate_store):
        dq = _rate_store[k]
        if not dq or dq[-1] < jetzt - max_fenster:
            del _rate_store[k]


def _rate_limit_ok(bucket: str, key: str) -> bool:
    """True, wenn der Aufruf im Limit liegt. Limits über Env konfigurierbar."""
    grenzen = {
        "register": (
            int(os.getenv("AUTH_REGISTER_MAX", "3")),
            int(os.getenv("AUTH_REGISTER_WINDOW", "600")),
        ),
        "login": (
            int(os.getenv("AUTH_LOGIN_MAX", "10")),
            int(os.getenv("AUTH_LOGIN_WINDOW", "300")),
        ),
    }
    maximal, fenster = grenzen[bucket]
    jetzt = time.time()
    if len(_rate_store) > _RATE_STORE_MAX:
        _bereinige_rate_store(jetzt)
    q = _rate_store.setdefault(f"{bucket}:{key}", deque())
    while q and q[0] < jetzt - fenster:
        q.popleft()
    if len(q) >= maximal:
        return False
    q.append(jetzt)
    return True


def _client_ip(request: Request) -> str:
    """Client-IP für das Rate-Limiting.

    Hinter dem Reverse-Proxy (OpenShift-HAProxy-Router) hängt der vertrauenswürdige
    Proxy die tatsächliche Client-IP RECHTS an X-Forwarded-For an. Der ganz linke
    Wert ist dagegen client-kontrolliert (spoofbar) — deshalb wird der RECHTE
    (vom Proxy gesetzte) Eintrag genutzt, sonst der direkte Peer.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        teile = [t.strip() for t in fwd.split(",") if t.strip()]
        if teile:
            return teile[-1]
    return request.client.host if request.client else "unbekannt"


class AuthBody(BaseModel):
    email: str
    passwort: str


@router.post("/api/auth/register")
async def auth_register(body: AuthBody, request: Request, db=Depends(get_db)):
    """Registriert einen neuen Nutzer. Login erst nach Admin-Freischaltung."""
    if not passwort_aktiv():
        raise HTTPException(
            status_code=403, detail="Registrierung per E-Mail/Passwort ist deaktiviert."
        )
    if not _rate_limit_ok("register", _client_ip(request)):
        raise HTTPException(
            status_code=429, detail="Zu viele Registrierungs-Versuche. Bitte später erneut."
        )
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Bitte eine gültige E-Mail-Adresse angeben.")
    if len(body.passwort) < _passwort_min():
        raise HTTPException(
            status_code=400, detail=f"Passwort muss mindestens {_passwort_min()} Zeichen haben."
        )
    try:
        neuer_user = registriere_user(db, email, body.passwort)
    except ValueError:
        raise HTTPException(status_code=409, detail="Diese E-Mail-Adresse ist bereits registriert.")
    logger.info("Registrierung: user_id=%s", neuer_user.id)
    return {
        "status": "pending",
        "message": "Registrierung erhalten. Ihr Zugang wird von einem Administrator freigeschaltet.",
    }


@router.post("/api/auth/login")
async def auth_login(body: AuthBody, request: Request, db=Depends(get_db)):
    """Meldet einen freigeschalteten Nutzer an und setzt das Session-Cookie."""
    # Wie bei der Registrierung: ist nur Microsoft-Login erlaubt (oder die
    # Anmeldung abgeschaltet), darf kein Passwort-Login mehr möglich sein.
    if not passwort_aktiv():
        raise HTTPException(
            status_code=403, detail="Anmeldung per E-Mail/Passwort ist deaktiviert."
        )
    if not _rate_limit_ok("login", _client_ip(request)):
        raise HTTPException(status_code=429, detail="Zu viele Login-Versuche. Bitte später erneut.")
    user = verifiziere_login(db, body.email, body.passwort)
    if user is None:
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch.")
    if not user.approved:
        raise HTTPException(
            status_code=403,
            detail="Ihr Zugang wurde noch nicht freigeschaltet. Bitte wenden Sie sich an einen Administrator.",
        )
    logger.info("Login erfolgreich: user_id=%s", user.id)
    cookie = erzeuge_session_cookie({"id": user.id})
    response = JSONResponse(
        {"user": {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin}}
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=COOKIE_SECURE,
    )
    return response


@router.get("/api/auth/logout")
async def auth_logout():
    """Löscht das Session-Cookie und leitet zur Startseite."""
    base_path = os.getenv("BASE_PATH", "").rstrip("/")
    response = RedirectResponse(url=f"{base_path}/")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        samesite="lax",
        path="/",
        secure=COOKIE_SECURE,
    )
    return response


@router.get("/api/auth/me")
async def auth_me(request: Request):
    """Gibt den aktuellen Nutzer zurück oder 401."""
    if ermittle_auth_modus() == "disabled":
        return {
            "name": "Entwickler",
            "email": "dev@localhost",
            "is_admin": True,
            "provider": "dev",
            "auth_aktiv": False,
        }
    user = _user_aus_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet.")
    return {**user, "auth_aktiv": True}


@router.get("/api/auth/config")
async def auth_config():
    """Verfügbare Anmeldeverfahren — steuert die Login-Seite."""
    return {"azure_aktiv": azure_aktiv(), "passwort_aktiv": passwort_aktiv()}


def _azure_redirect_uri(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", request.url.netloc
    )
    base_path = os.getenv("BASE_PATH", "")
    if base_path:
        scheme = "https"
    return f"{scheme}://{host}{base_path}/api/auth/callback"


@router.get("/api/auth/azure/login")
async def auth_azure_login(request: Request):
    """Leitet zum Microsoft-Login weiter (Authorization Code Flow)."""
    if not ist_azure_konfiguriert():
        raise HTTPException(status_code=501, detail="Microsoft-Anmeldung nicht konfiguriert.")
    redirect_uri = _azure_redirect_uri(request)
    auth_url, state = erzeuge_login_url(redirect_uri)
    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        "auth_state", state, max_age=600, httponly=True, samesite="lax", secure=COOKIE_SECURE
    )
    return response


@router.get("/api/auth/callback")
async def auth_azure_callback(request: Request, db=Depends(get_db)):
    """Verarbeitet den Microsoft-Callback: User upserten, Session-Cookie setzen."""
    if not ist_azure_konfiguriert():
        raise HTTPException(status_code=501, detail="Microsoft-Anmeldung nicht konfiguriert.")
    state = request.cookies.get("auth_state")
    if not state:
        raise HTTPException(
            status_code=400, detail="Kein Login-State gefunden. Bitte erneut anmelden."
        )
    redirect_uri = _azure_redirect_uri(request)
    try:
        claims = verarbeite_callback(dict(request.query_params), state, redirect_uri)
    except ValueError as e:
        logger.warning("Azure-Callback fehlgeschlagen: %s", e)
        raise HTTPException(status_code=401, detail="Anmeldung fehlgeschlagen.")
    user = upsert_azure_user(db, claims)
    logger.info("Microsoft-Login erfolgreich: user_id=%s", user.id)
    session_cookie = erzeuge_session_cookie({"id": user.id})
    base_path = os.getenv("BASE_PATH", "")
    response = RedirectResponse(url=f"{base_path}/app.html")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_cookie,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=COOKIE_SECURE,
    )
    response.delete_cookie("auth_state")
    return response
