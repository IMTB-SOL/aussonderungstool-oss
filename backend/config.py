"""Konfigurationsmodul – lädt .env und prüft Pflichtfelder beim Import."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Pflichtfelder prüfen
_PFLICHTFELDER = ["VERAPDF_PATH", "DATA_DIR"]
for _feld in _PFLICHTFELDER:
    if not os.getenv(_feld):
        raise EnvironmentError(
            f"Pflichtfeld fehlt in .env: {_feld}. Bitte .env.example als Vorlage nutzen."
        )

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
# Secure-Flag der Cookies: in Produktion standardmäßig an (Betrieb hinter TLS),
# in der Entwicklung aus. Ein explizit gesetzter Wert hat immer Vorrang.
COOKIE_SECURE = (
    os.getenv("COOKIE_SECURE", "true" if APP_ENV == "production" else "false").strip().lower()
    == "true"
)
VERSION = "0.1.0"


# --- Content-Security-Policy -------------------------------------------------
_CSP_RUMPF = (
    "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'self'; form-action 'self'"
)
# CSP für Tool-SPA, API und alle Datei-Endpoints — ohne unsafe-eval.
CSP_STRICT = "default-src 'self'; script-src 'self' 'unsafe-inline'; " + _CSP_RUMPF


def api_dokumentation_aktiv() -> bool:
    """Ob Swagger (/docs), ReDoc (/redoc) und /openapi.json ausgeliefert werden.

    Im Produktivbetrieb abgeschaltet. Die vollständige API-Beschreibung ist für
    Anwender ohne Nutzen und für einen Angreifer eine fertige Landkarte. Der
    Schutz darf nicht allein an der Auth-Middleware hängen: bei
    AUTH_MODUS=disabled registriert `main` sie gar nicht, und ob ein Pfad
    gedeckt ist, entscheidet sonst eine Ausnahmeliste in `routers/auth.py`.

    Wird pro Aufruf frisch gelesen (nicht beim Import eingefroren), damit der
    Schalter testbar bleibt.
    """
    return os.getenv("APP_ENV", "development").strip().lower() != "production"
