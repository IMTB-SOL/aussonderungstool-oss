"""Tests für die OpenShift-/Deployment-Anpassungen (Container-Umbau)."""

import pytest
from fastapi.testclient import TestClient
from main import app
from utils import auth


@pytest.fixture
def client():
    return TestClient(app)


def test_metrics_ohne_token_liefert_503(client, monkeypatch):
    """Ist METRICS_TOKEN leer, liefert /metrics 503 — sicherer Default."""
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    r = client.get("/metrics")
    assert r.status_code == 503


def test_metrics_ohne_bearer_liefert_401(client, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "geheim")
    r = client.get("/metrics")
    assert r.status_code == 401


def test_metrics_mit_falschem_token_liefert_401(client, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "geheim")
    r = client.get("/metrics", headers={"Authorization": "Bearer falsch"})
    assert r.status_code == 401


def test_metrics_mit_gueltigem_token_liefert_prometheus_text(client, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "geheim")
    r = client.get("/metrics", headers={"Authorization": "Bearer geheim"})
    assert r.status_code == 200
    # Prometheus-Exposition-Format enthält HELP-/TYPE-Kommentare
    assert "# HELP" in r.text


def test_auth_modus_disabled_explizit(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "disabled")
    assert auth.ermittle_auth_modus() == "disabled"


def test_auth_modus_passwort_explizit(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    assert auth.ermittle_auth_modus() == "passwort"


def test_auth_modus_beide_mit_azure(monkeypatch):
    monkeypatch.setattr(auth, "ist_azure_konfiguriert", lambda: True)
    monkeypatch.setenv("AUTH_MODUS", "beide")
    assert auth.ermittle_auth_modus() == "beide"


def test_auth_modus_azure_ohne_config_wirft(monkeypatch):
    monkeypatch.setattr(auth, "ist_azure_konfiguriert", lambda: False)
    monkeypatch.setenv("AUTH_MODUS", "azure")
    with pytest.raises(EnvironmentError):
        auth.ermittle_auth_modus()


def test_auth_modus_ungueltig_wirft(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "quatsch")
    with pytest.raises(EnvironmentError):
        auth.ermittle_auth_modus()


def test_auth_modus_default_production(monkeypatch):
    monkeypatch.delenv("AUTH_MODUS", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(auth, "ist_azure_konfiguriert", lambda: False)
    assert auth.ermittle_auth_modus() == "passwort"


def test_auth_modus_default_development(monkeypatch):
    monkeypatch.delenv("AUTH_MODUS", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(auth, "ist_azure_konfiguriert", lambda: False)
    # Fail-closed: ohne AUTH_MODUS ist die Anmeldung auch in der Entwicklung an.
    assert auth.ermittle_auth_modus() == "passwort"


def test_csp_header_gesetzt_und_ohne_cdn(client):
    r = client.get("/health")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "unpkg.com" not in csp
    assert "unsafe-eval" not in csp


def test_basepath_wird_in_index_injiziert(client, monkeypatch):
    monkeypatch.setenv("BASE_PATH", "/aussonderungstool")
    r = client.get("/")
    assert r.status_code == 200
    assert '<base href="/aussonderungstool/" />' in r.text


def test_basepath_leer_bleibt_root(client, monkeypatch):
    monkeypatch.delenv("BASE_PATH", raising=False)
    r = client.get("/")
    assert r.status_code == 200
    assert '<base href="/" />' in r.text


def test_sichere_auslieferung_html_wird_download():
    """Hochgeladene HTML-Dateien dürfen nicht inline gerendert werden (Stored XSS)."""
    from routers.jobs import _sichere_auslieferung

    medientyp, disposition = _sichere_auslieferung("boese.html")
    assert medientyp == "application/octet-stream"
    assert disposition == "attachment"


def test_sichere_auslieferung_svg_wird_download():
    """SVG kann aktiven Skriptcode tragen — ebenfalls nur als Download."""
    from routers.jobs import _sichere_auslieferung

    _, disposition = _sichere_auslieferung("aktiv.svg")
    assert disposition == "attachment"


def test_sichere_auslieferung_pdf_inline():
    """PDF ist skriptsicher und wird weiterhin inline ausgeliefert."""
    from routers.jobs import _sichere_auslieferung

    medientyp, disposition = _sichere_auslieferung("bericht.pdf")
    assert medientyp == "application/pdf"
    assert disposition == "inline"


def test_app_html_nutzt_gebaute_skripte(client):
    """Die Tool-SPA lädt vorab gebautes JS — kein Babel-im-Browser mehr."""
    r = client.get("/app.html")
    assert r.status_code == 200
    assert "text/babel" not in r.text
    assert "babel.min.js" not in r.text
    assert "tool/build/app.js" in r.text


def test_csp_strikt_ohne_unsafe_eval():
    """CSP-Konstante: streng, ohne unsafe-eval."""
    from config import CSP_STRICT

    assert "unsafe-eval" not in CSP_STRICT
    assert "default-src 'self'" in CSP_STRICT


def test_startseite_liefert_tool_spa(client):
    """'/' liefert die Tool-SPA mit CSP ohne unsafe-eval."""
    r = client.get("/")
    assert r.status_code == 200
    assert "tool/build/app.js" in r.text
    assert "unsafe-eval" not in r.headers.get("Content-Security-Policy", "")
