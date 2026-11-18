"""FastAPI-Hauptanwendung – App-Setup und Router-Einbindung."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from config import COOKIE_SECURE, CSP_STRICT, DATA_DIR, VERSION, api_dokumentation_aktiv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from models import get_db, init_db
from utils.logging_setup import konfiguriere_logging

konfiguriere_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — ersetzt die deprecated @app.on_event("startup"/"shutdown")
# ---------------------------------------------------------------------------

_retention_task: asyncio.Task | None = None


async def _retention_schleife() -> None:
    """Löscht alle 24 h abgelaufene Jobs (Daten-Retention)."""
    from utils.retention import loesche_abgelaufene_jobs

    retention_tage = int(os.getenv("JOB_RETENTION_TAGE", "30"))
    while True:
        try:
            db = next(get_db())
            try:
                anzahl = loesche_abgelaufene_jobs(db, retention_tage)
                if anzahl:
                    logger.info("Retention-Lauf: %d Job(s) gelöscht.", anzahl)
            finally:
                db.close()
        except Exception:
            logger.exception("Retention-Lauf fehlgeschlagen.")
        await asyncio.sleep(24 * 60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup + Shutdown in einem Kontextmanager (FastAPI ≥ 0.93)."""
    # ---- Startup ---------------------------------------------------------
    init_db()
    # Seed-Daten für Bewertungsagent
    from utils.seed_bewertung import seede_bewertungsdaten

    _db = next(get_db())
    try:
        seede_bewertungsdaten(_db)
    except Exception:
        logger.exception(
            "Seed-Daten für Bewertungsagent konnten nicht angelegt werden — Startup wird trotzdem fortgesetzt."
        )
    finally:
        _db.close()

    # Erst-Admin aus INITIAL_ADMIN_* anlegen (idempotent).
    from utils.seed_admin import seede_admin

    _db_admin = next(get_db())
    try:
        seede_admin(_db_admin)
    except Exception:
        logger.exception("Erst-Admin konnte nicht angelegt werden — Startup wird fortgesetzt.")
    finally:
        _db_admin.close()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Anwendung gestartet. Datenverzeichnis: %s", DATA_DIR)

    global _retention_task
    _retention_task = asyncio.create_task(_retention_schleife())

    try:
        yield
    finally:
        # ---- Shutdown ----------------------------------------------------
        if _retention_task is not None and not _retention_task.done():
            _retention_task.cancel()
            try:
                await _retention_task
            except (asyncio.CancelledError, Exception):
                pass

        # Laufende Agenten-Tasks abwarten. Das Register ist ein Dict
        # job_id -> Task in utils.agent_runner; gewartet wird auf die Tasks.
        from utils.agent_runner import _laufende_tasks

        offene_tasks = list(_laufende_tasks.values())
        if offene_tasks:
            logger.info("Warte auf %d laufende Tasks…", len(offene_tasks))
            await asyncio.gather(*offene_tasks, return_exceptions=True)
        logger.info("Anwendung beendet.")


# Doku-Endpunkte in Produktion abschalten (siehe config.api_dokumentation_aktiv).
_DOKU_AKTIV = api_dokumentation_aktiv()
app = FastAPI(
    title="XDOMEA Aussonderungstool",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _DOKU_AKTIV else None,
    redoc_url="/redoc" if _DOKU_AKTIV else None,
    openapi_url="/openapi.json" if _DOKU_AKTIV else None,
)

from prometheus_fastapi_instrumentator import Instrumentator

# Prometheus-Metriken unter /metrics exponieren (für OpenShift-ServiceMonitor)
Instrumentator().instrument(app).expose(app, include_in_schema=False)


# ---------------------------------------------------------------------------
# /metrics-Endpoint per Bearer-Token härten
# ---------------------------------------------------------------------------
# Plattform-Vorgabe: ServiceMonitor authentifiziert sich per Bearer-Token am
# Anwendungs-/metrics-Endpoint. Das Token kommt über die Umgebungsvariable
# METRICS_TOKEN (im Secret abgelegt). Ist kein Token gesetzt, wird /metrics
# blockiert — ein versehentlich ungeschütztes Metrics-Endpoint wäre ein
# Compliance-Verstoß und würde sensible interne Daten leaken können.
import hmac as _hmac

if not os.getenv("METRICS_TOKEN", "").strip():
    logger.warning(
        "METRICS_TOKEN ist leer — /metrics liefert 503. "
        "In Produktion ein zufälliges Token im Secret hinterlegen."
    )


@app.middleware("http")
async def _metrics_auth(request: Request, call_next):
    """Erzwingt Bearer-Token für /metrics (Konstanzzeit-Vergleich).

    Das Token wird pro Request aus der Umgebung gelesen, damit Tests den
    Wert per monkeypatch setzen können und ein Secret-Rotation-Restart
    nicht zwingend nötig wird, wenn das Secret zur Laufzeit aktualisiert
    und der Pod ohnehin neu startet.
    """
    if request.url.path != "/metrics":
        return await call_next(request)

    erwartet = os.getenv("METRICS_TOKEN", "").strip()
    if not erwartet:
        return JSONResponse(
            status_code=503, content={"detail": "Metrics-Endpoint nicht konfiguriert."}
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return JSONResponse(status_code=401, content={"detail": "Bearer-Token erforderlich."})

    uebergeben = auth_header.split(" ", 1)[1].strip()
    if not _hmac.compare_digest(uebergeben, erwartet):
        return JSONResponse(status_code=401, content={"detail": "Ungültiges Metrics-Token."})

    return await call_next(request)


# CORS nur aktivieren, wenn explizit Origins gesetzt sind. Kein "*"-Fallback —
# Frontend und Backend teilen dieselbe Origin, Same-Origin braucht kein CORS.
ERLAUBTE_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if ERLAUBTE_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ERLAUBTE_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

# ---------------------------------------------------------------------------
# Auth-Middleware und Auth-Router einbinden
# ---------------------------------------------------------------------------

from routers.auth import AuthMiddleware
from routers.auth import router as auth_router
from utils.auth import ermittle_auth_modus

_AUTH_MODUS = ermittle_auth_modus()
if _AUTH_MODUS != "disabled":
    app.add_middleware(AuthMiddleware)
    logger.info("Authentifizierung aktiviert (Modus: %s).", _AUTH_MODUS)
else:
    logger.warning(
        "Authentifizierung deaktiviert (AUTH_MODUS=disabled) — "
        "der Zugriffsschutz muss auf Netzwerkebene sichergestellt sein."
    )


@app.exception_handler(Exception)
async def _unbehandelter_fehler(request: Request, exc: Exception):
    """Fängt unbehandelte Fehler ab und liefert eine generische 500-Antwort.

    Exception-Details landen ausschließlich im Server-Log, nie beim Client —
    verhindert das Leaken interner Pfade/Komponenten. HTTPException wird von
    FastAPI weiterhin separat behandelt.
    """
    logger.exception("Unbehandelter Fehler bei %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Interner Serverfehler."})


@app.middleware("http")
async def _security_header(request: Request, call_next):
    """Setzt Standard-Security-Header — greift auch ohne vorgeschalteten nginx."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", CSP_STRICT)
    # HSTS nur, wenn die Anwendung hinter TLS betrieben wird (COOKIE_SECURE=true).
    if COOKIE_SECURE:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


from routers import admin as admin_router
from routers import agenten as agenten_router
from routers import bewertung as bewertung_router
from routers import frontend as frontend_router
from routers import jobs as jobs_router
from routers import legal as legal_router
from routers import system as system_router
from routers.frontend import FRONTEND_DIR

app.include_router(auth_router)
app.include_router(system_router.router)
app.include_router(legal_router.router)
app.include_router(admin_router.router)
app.include_router(agenten_router.router)
app.include_router(bewertung_router.router)
app.include_router(jobs_router.router)
app.include_router(frontend_router.router)

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
