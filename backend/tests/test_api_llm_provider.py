"""Tests für die LLM-Provider-Umschaltung (Admin-API)."""

import pytest
from fastapi.testclient import TestClient
from main import app
from models import Base, engine


@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    return TestClient(app)


def test_get_llm_provider_struktur(client):
    r = client.get("/api/admin/llm-provider")
    assert r.status_code == 200
    daten = r.json()
    assert daten["aktiver_provider"] in ("ollama", "scaleway")
    assert isinstance(daten["scaleway_verfuegbar"], bool)
    ids = {p["id"] for p in daten["provider"]}
    assert ids == {"ollama", "scaleway"}
    assert "scaleway_modell" in daten


def test_put_ollama(client):
    r = client.put("/api/admin/llm-provider", json={"aktiver_provider": "ollama"})
    assert r.status_code == 200
    assert client.get("/api/admin/llm-provider").json()["aktiver_provider"] == "ollama"


def test_put_scaleway_ohne_freigabe_400(client, monkeypatch):
    monkeypatch.delenv("SCALEWAY_ENABLED", raising=False)
    r = client.put("/api/admin/llm-provider", json={"aktiver_provider": "scaleway"})
    assert r.status_code == 400


def test_put_scaleway_mit_freigabe(client, monkeypatch):
    monkeypatch.setenv("SCALEWAY_ENABLED", "true")
    r = client.put("/api/admin/llm-provider", json={"aktiver_provider": "scaleway"})
    assert r.status_code == 200
    assert client.get("/api/admin/llm-provider").json()["aktiver_provider"] == "scaleway"
    # Aufräumen, damit andere Tests nicht beeinflusst werden.
    client.put("/api/admin/llm-provider", json={"aktiver_provider": "ollama"})


def test_put_speichert_scaleway_modell(client, monkeypatch):
    monkeypatch.setenv("SCALEWAY_ENABLED", "true")
    r = client.put(
        "/api/admin/llm-provider",
        json={"aktiver_provider": "scaleway", "scaleway_modell": "gpt-oss-120b"},
    )
    assert r.status_code == 200
    assert client.get("/api/admin/llm-provider").json()["scaleway_modell"] == "gpt-oss-120b"
    # Aufräumen — Modell leeren und zurück auf Ollama.
    client.put(
        "/api/admin/llm-provider", json={"aktiver_provider": "ollama", "scaleway_modell": ""}
    )


def test_get_scaleway_modelle_erfolg(client, monkeypatch):
    monkeypatch.setenv("SCALEWAY_ENABLED", "true")

    async def fake_liste():
        return ["gpt-oss-120b", "llama-3.3-70b-instruct"]

    monkeypatch.setattr("utils.openai_client.liste_scaleway_modelle", fake_liste)
    r = client.get("/api/admin/scaleway-modelle")
    assert r.status_code == 200
    assert r.json()["modelle"] == ["gpt-oss-120b", "llama-3.3-70b-instruct"]


def test_get_scaleway_modelle_fehler_502(client, monkeypatch):
    monkeypatch.setenv("SCALEWAY_ENABLED", "true")

    async def fake_liste_fehler():
        raise RuntimeError("Verbindung fehlgeschlagen")

    monkeypatch.setattr("utils.openai_client.liste_scaleway_modelle", fake_liste_fehler)
    r = client.get("/api/admin/scaleway-modelle")
    assert r.status_code == 502


def test_get_scaleway_modelle_ohne_freigabe_400(client, monkeypatch):
    monkeypatch.delenv("SCALEWAY_ENABLED", raising=False)
    r = client.get("/api/admin/scaleway-modelle")
    assert r.status_code == 400
