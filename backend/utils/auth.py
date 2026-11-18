"""Authentifizierung: Microsoft (MSAL) und E-Mail/Passwort (PBKDF2),
User-Verwaltung, signierte Session-Cookies und Auth-Modus-Bestimmung.

Das signierte Cookie-Verfahren (itsdangerous) bleibt providerunabhängig
erhalten.
"""

import hashlib
import hmac
import logging
import os
import secrets
import ssl

import msal
import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from models import User
from requests.adapters import HTTPAdapter
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# --- Session-Konfiguration --------------------------------------------------
SESSION_COOKIE_NAME = "aussonderung_session"
SESSION_MAX_AGE = 8 * 60 * 60  # 8 Stunden in Sekunden

# PBKDF2-Parameter (OWASP-Empfehlung für PBKDF2-HMAC-SHA256).
_PBKDF2_ITER = 600_000
_PBKDF2_ALGO = "sha256"

SESSION_SECRET_MIN = 32
_PLATZHALTER = ("bitte-ersetzen", "changeme", "change-me", "example", "beispiel", "<", ">")


def ist_platzhalter_oder_zu_kurz(wert: str, mindestlaenge: int) -> bool:
    """Erkennt leere, zu kurze oder unverändert übernommene Beispielwerte."""
    wert = (wert or "").strip()
    if len(wert) < mindestlaenge:
        return True
    klein = wert.lower()
    return any(p in klein for p in _PLATZHALTER)


# Schlüssel zum Signieren der Session-Cookies. SESSION_SECRET muss in Produktion
# gesetzt sein; fehlt er, wird pro Prozessstart ein zufälliger Schlüssel erzeugt
# (sicher, aber alle Sessions sterben beim Neustart).
_SECRET_KEY = os.getenv("SESSION_SECRET", "").strip()
_IN_PRODUKTION = os.getenv("APP_ENV", "development").strip().lower() == "production"
_AUTH_AUS = os.getenv("AUTH_MODUS", "").strip().lower() == "disabled"
if (
    _IN_PRODUKTION
    and not _AUTH_AUS
    and ist_platzhalter_oder_zu_kurz(_SECRET_KEY, SESSION_SECRET_MIN)
):
    # Ein bekannter oder erratbarer Schlüssel erlaubt das Fälschen von Sessions
    # (auch Admin). In Produktion deshalb Start verweigern statt warnen.
    raise EnvironmentError(
        f"SESSION_SECRET fehlt, ist ein Platzhalter oder kürzer als {SESSION_SECRET_MIN} Zeichen. "
        'Erzeugen mit: python -c "import secrets; print(secrets.token_hex(32))"'
    )
if not _SECRET_KEY:
    _SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        "SESSION_SECRET nicht gesetzt — verwende einen zufälligen Schlüssel. "
        "Sessions überleben keinen Neustart. Für Produktion SESSION_SECRET in .env setzen."
    )

_serializer = URLSafeTimedSerializer(_SECRET_KEY)


# --- Azure AD / Microsoft Login (MSAL) --------------------------------------
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
AZURE_AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
AZURE_SCOPES = ["User.Read"]


def ist_azure_konfiguriert() -> bool:
    """Prüft ob Azure AD Credentials konfiguriert sind."""
    return bool(AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_TENANT_ID)


class _SystemSSLContextAdapter(HTTPAdapter):
    """HTTPS-Adapter, der den SSL-Context des Systems verwendet.

    Umgeht ein umgebungsspezifisches Problem: Mit dem von urllib3
    voreingestellten SSL-Context beantwortet login.microsoftonline.com die
    MSAL-Anfragen auf manchen Netzwerkpfaden mit HTTP 404. Der
    System-Default-Context (ssl.create_default_context()) kommt sauber durch.
    """

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        super().init_poolmanager(*args, **kwargs)


def _msal_http_client() -> requests.Session:
    """requests-Session für MSAL — mit System-SSL-Context (siehe Adapter oben)."""
    session = requests.Session()
    session.mount("https://", _SystemSSLContextAdapter())
    return session


def _erstelle_msal_app(redirect_uri: str | None = None) -> "msal.ConfidentialClientApplication":
    """Erstellt eine MSAL ConfidentialClientApplication."""
    return msal.ConfidentialClientApplication(
        AZURE_CLIENT_ID,
        authority=AZURE_AUTHORITY,
        client_credential=AZURE_CLIENT_SECRET,
        http_client=_msal_http_client(),
    )


def erzeuge_login_url(redirect_uri: str) -> tuple[str, str]:
    """Erzeugt die Microsoft-Login-URL (Authorization Code Flow).

    Returns:
        (auth_url, state) — URL zum Redirect und signierter Flow-State.
    """
    app = _erstelle_msal_app()
    flow = app.initiate_auth_code_flow(scopes=AZURE_SCOPES, redirect_uri=redirect_uri)
    state = _serializer.dumps(flow)
    return flow["auth_uri"], state


def verarbeite_callback(auth_response: dict, state: str, redirect_uri: str) -> dict:
    """Verarbeitet den Microsoft-Callback und gibt User-Claims zurück.

    Returns:
        dict mit name, email, oid.

    Raises:
        ValueError: bei ungültigem State oder fehlgeschlagenem Token-Tausch.
    """
    try:
        flow = _serializer.loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as e:
        raise ValueError(f"Ungültiger oder abgelaufener Login-State: {e}") from e

    app = _erstelle_msal_app()
    result = app.acquire_token_by_auth_code_flow(flow, auth_response)
    if "error" in result:
        raise ValueError(f"Token-Fehler: {result.get('error_description', result['error'])}")

    claims = result.get("id_token_claims", {})
    return {
        "name": claims.get("name", "Unbekannt"),
        "email": claims.get("preferred_username", ""),
        "oid": claims.get("oid", ""),
    }


# --- Passwort-Hashing -------------------------------------------------------
def hash_passwort(passwort: str) -> tuple[str, str]:
    """Hasht ein Passwort mit PBKDF2-HMAC-SHA256 und frischem Salt.

    Returns:
        (hash_hex, salt_hex) — beide als Hex-String zum Speichern in der DB.
    """
    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, passwort.encode(), salt.encode(), _PBKDF2_ITER
    ).hex()
    return pw_hash, salt


def pruefe_passwort(passwort: str, hash_hex: str | None, salt_hex: str | None) -> bool:
    """Prüft ein Passwort gegen den gespeicherten Hash — konstante Zeit.

    Azure-Konten haben keinen Hash (None/leer) → immer False (nicht per
    Passwort anmeldbar).
    """
    if not hash_hex or not salt_hex:
        return False
    vergleich = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, passwort.encode(), salt_hex.encode(), _PBKDF2_ITER
    ).hex()
    return hmac.compare_digest(vergleich, hash_hex)


# --- Session-Cookies --------------------------------------------------------
def erzeuge_session_cookie(payload: dict) -> str:
    """Erzeugt ein signiertes Session-Cookie (enthält nur die user_id)."""
    return _serializer.dumps(payload)


def pruefe_session(cookie_wert: str) -> dict | None:
    """Prüft ein Session-Cookie und gibt das Payload zurück, sonst None."""
    if not cookie_wert:
        return None
    try:
        return _serializer.loads(cookie_wert, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# --- User-Verwaltung --------------------------------------------------------
def registriere_user(db: Session, email: str, passwort: str, name: str | None = None) -> User:
    """Legt einen neuen, noch nicht freigeschalteten User an.

    Raises:
        ValueError: wenn die E-Mail bereits vergeben ist.
    """
    email = email.strip().lower()
    if db.query(User).filter_by(email=email).first() is not None:
        raise ValueError("E-Mail bereits vergeben.")
    pw_hash, salt = hash_passwort(passwort)
    user = User(
        email=email,
        password_hash=pw_hash,
        salt=salt,
        name=name or email.split("@", 1)[0],
        approved=False,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def upsert_azure_user(db: Session, claims: dict) -> User:
    """Find-or-create eines Azure-Nutzers anhand der E-Mail.

    Azure-Nutzer sind automatisch freigeschaltet und haben kein nutzbares Passwort
    (leerer Hash → nie per Passwort anmeldbar). Ein noch nicht freigeschaltetes
    (pending) lokales Konto wird durch die Microsoft-Anmeldung in ein Azure-Konto
    überführt und ein evtl. bei der Selbstregistrierung gesetztes Passwort ENTWERTET
    (Azure hat es nie verifiziert). Ein bereits freigeschaltetes lokales Konto
    behält sein Passwort (der Nutzer hat es legitim eingerichtet).
    """
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Azure-Claims enthalten keine E-Mail-Adresse.")
    name = claims.get("name") or email.split("@", 1)[0]
    user = db.query(User).filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            password_hash="",  # leer statt None: funktioniert auch auf (noch)
            salt="",  # NOT NULL gesetzten Passwortspalten von Bestands-DBs
            name=name,
            approved=True,
            is_admin=False,
            provider="azure",
        )
        db.add(user)
    else:
        if not user.approved:
            # pending lokales Konto -> Azure-Konto; fremdes/unverifiziertes Passwort entwerten
            user.password_hash = ""
            user.salt = ""
            user.provider = "azure"
            user.approved = True
        if name and user.name != name:
            user.name = name
    db.commit()
    db.refresh(user)
    return user


def verifiziere_login(db: Session, email: str, passwort: str) -> User | None:
    """Prüft Anmeldedaten. Gibt den User zurück oder None bei falschen Daten."""
    user = db.query(User).filter_by(email=email.strip().lower()).first()
    if user is None:
        return None
    if not pruefe_passwort(passwort, user.password_hash, user.salt):
        return None
    return user


def lade_user(db: Session, user_id: int) -> User | None:
    """Lädt einen User anhand seiner ID."""
    return db.get(User, user_id)


# --- Auth-Modus -------------------------------------------------------------
def ermittle_auth_modus() -> str:
    """Bestimmt den Authentifizierungsmodus aus der Umgebung.

    Rückgabe: "beide" | "passwort" | "azure" | "disabled".

    - beide    : Microsoft UND E-Mail/Passwort (Azure muss konfiguriert sein,
                 sonst Rückfall auf passwort mit Warnung).
    - azure    : nur Microsoft (erfordert Azure-Konfiguration, sonst Fehler).
    - passwort : nur E-Mail/Passwort.
    - disabled : keine Authentifizierung (Schutz auf Netzwerkebene).
    - nicht gesetzt: production → "beide" falls Azure konfiguriert, sonst
      "passwort"; sonst "disabled" (mit Warnung).
    """
    modus = os.getenv("AUTH_MODUS", "").strip().lower()
    if modus in ("beide", "passwort", "azure", "disabled"):
        if modus in ("azure", "beide") and not ist_azure_konfiguriert():
            if modus == "azure":
                raise EnvironmentError(
                    "AUTH_MODUS=azure, aber Azure ist nicht konfiguriert. "
                    "Bitte AZURE_CLIENT_ID, AZURE_TENANT_ID und AZURE_CLIENT_SECRET setzen."
                )
            logger.warning(
                "AUTH_MODUS=beide, aber Azure ist nicht konfiguriert — nur E-Mail/Passwort aktiv."
            )
            return "passwort"
        return modus
    if modus:
        raise EnvironmentError(
            f"Ungültiger AUTH_MODUS: '{modus}'. Erlaubt: 'beide', 'passwort', 'azure', 'disabled'."
        )
    # Sicherer Default: ohne Angabe ist die Anmeldung immer aktiv. Abschalten
    # geht nur ausdrücklich über AUTH_MODUS=disabled.
    return "beide" if ist_azure_konfiguriert() else "passwort"


def azure_aktiv() -> bool:
    """Ob der Microsoft-Login angeboten wird."""
    return ermittle_auth_modus() in ("beide", "azure") and ist_azure_konfiguriert()


def passwort_aktiv() -> bool:
    """Ob der E-Mail/Passwort-Login angeboten wird."""
    return ermittle_auth_modus() in ("beide", "passwort")
