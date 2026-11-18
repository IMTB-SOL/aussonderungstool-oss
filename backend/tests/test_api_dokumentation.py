"""Tests: Die FastAPI-Doku ist im Produktivbetrieb abgeschaltet.

Swagger (/docs), ReDoc (/redoc) und das Schema (/openapi.json) sind bei
FastAPI standardmäßig aktiv. Im Produktivbetrieb darf die vollständige
API-Beschreibung nicht ausgeliefert werden — auch dann nicht, wenn die
Auth-Middleware fehlt (AUTH_MODUS=disabled registriert sie gar nicht).
"""

import importlib

import config
import pytest
from fastapi.testclient import TestClient

DOKU_PFADE = ("/docs", "/redoc", "/openapi.json")


# --- Schalter ---------------------------------------------------------------


def test_doku_in_produktion_abgeschaltet(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert config.api_dokumentation_aktiv() is False


def test_doku_gross_geschrieben_ebenfalls_abgeschaltet(monkeypatch):
    """APP_ENV wird groß/klein-unabhängig ausgewertet."""
    monkeypatch.setenv("APP_ENV", "Production")
    assert config.api_dokumentation_aktiv() is False


def test_doku_in_entwicklung_aktiv(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    assert config.api_dokumentation_aktiv() is True


def test_doku_ohne_app_env_aktiv(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert config.api_dokumentation_aktiv() is True


# --- Verdrahtung in der App -------------------------------------------------


@pytest.fixture
def produktions_app(monkeypatch):
    """Baut die App neu, als liefe sie in Produktion ohne Authentifizierung.

    AUTH_MODUS=disabled ist der ungünstigste Fall: `main` registriert die
    AuthMiddleware dann nicht, die Doku-Pfade sind also durch nichts gedeckt.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODUS", "disabled")
    import main

    importlib.reload(main)
    yield main.app
    # Umgebung zurücksetzen, bevor `main` wieder in den Testzustand gebaut wird
    monkeypatch.undo()
    importlib.reload(main)


@pytest.mark.parametrize("pfad", DOKU_PFADE)
def test_produktion_liefert_404(produktions_app, pfad):
    antwort = TestClient(produktions_app).get(pfad)
    assert antwort.status_code == 404


def test_produktion_gibt_kein_schema_preis(produktions_app):
    """Kein Endpunkt darf die Routenliste ausliefern."""
    antwort = TestClient(produktions_app).get("/openapi.json")
    assert "/api/upload" not in antwort.text


def test_entwicklung_liefert_schema_aus():
    """Gegenprobe: lokal bleibt die Doku ein nutzbares Werkzeug."""
    import main

    client = TestClient(main.app)
    antwort = client.get("/openapi.json")
    assert antwort.status_code == 200
    assert "/api/upload" in antwort.text
