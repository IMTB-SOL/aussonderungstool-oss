import pytest
from fastapi.testclient import TestClient
from main import app
from models import Base, BehoerdenProfil, IndizKatalog, engine, get_db
from utils.seed_bewertung import seede_bewertungsdaten


@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    db = next(get_db())
    try:
        seede_bewertungsdaten(db)
    finally:
        db.close()
    yield TestClient(app)


def test_get_schwellwerte(client):
    r = client.get("/api/admin/schwellwerte")
    assert r.status_code == 200
    assert "schwelle_a" in r.json()
    assert "schwelle_v" in r.json()


def test_put_schwellwerte(client):
    r = client.put("/api/admin/schwellwerte", json={"schwelle_a": 75, "schwelle_v": 25})
    assert r.status_code == 200
    r2 = client.get("/api/admin/schwellwerte")
    assert r2.json()["schwelle_a"] == 75
    assert r2.json()["schwelle_v"] == 25


def test_put_schwellwerte_invalid_range(client):
    r = client.put("/api/admin/schwellwerte", json={"schwelle_a": 20, "schwelle_v": 30})
    assert r.status_code == 400


def test_get_indizien(client):
    r = client.get("/api/admin/indizien")
    assert r.status_code == 200
    assert len(r.json()) == 10


def test_put_indiz_punkte(client):
    indizien = client.get("/api/admin/indizien").json()
    indiz_id = next(i["id"] for i in indizien if i["schluessel"] == "dickitaet_hoch")
    r = client.put(f"/api/admin/indizien/{indiz_id}", json={"punkte": 20, "aktiv": False})
    assert r.status_code == 200
    indizien_neu = client.get("/api/admin/indizien").json()
    eintrag = next(i for i in indizien_neu if i["id"] == indiz_id)
    assert eintrag["punkte"] == 20
    assert eintrag["aktiv"] is False


def test_get_prompts(client):
    r = client.get("/api/admin/prompts")
    assert r.status_code == 200
    prompts = r.json()
    schluessel = {p["schluessel"] for p in prompts}
    assert schluessel == {"erschliessung", "bewertung_begruendung"}
    for p in prompts:
        assert p["inhalt"].strip()
        assert p["anzeige_name"]


def test_put_prompt_aktualisiert(client):
    r = client.put("/api/admin/prompts/erschliessung", json={"inhalt": "Neuer Prompt-Text."})
    assert r.status_code == 200

    prompts = client.get("/api/admin/prompts").json()
    eintrag = next(p for p in prompts if p["schluessel"] == "erschliessung")
    assert eintrag["inhalt"] == "Neuer Prompt-Text."


def test_put_prompt_unbekannter_schluessel(client):
    r = client.put("/api/admin/prompts/gibt_es_nicht", json={"inhalt": "x"})
    assert r.status_code == 404


def test_put_prompt_leerer_inhalt(client):
    r = client.put("/api/admin/prompts/erschliessung", json={"inhalt": "   "})
    assert r.status_code == 400


def test_behoerden_crud(client):
    r = client.post(
        "/api/admin/behoerden",
        json={
            "name": "Test-Behörde",
            "kuerzel": "TB",
            "hierarchie_stufe": "ministerium",
            "besondere_oe": ["Leitungsstab"],
            "aktenzeichen_praefix": "TB",
        },
    )
    assert r.status_code == 200
    bid = r.json()["id"]

    r2 = client.get("/api/admin/behoerden")
    assert any(b["id"] == bid for b in r2.json())

    r3 = client.put(
        f"/api/admin/behoerden/{bid}",
        json={
            "name": "Test",
            "kuerzel": "TB",
            "hierarchie_stufe": "oberbehoerde",
            "besondere_oe": [],
            "aktenzeichen_praefix": "TB",
        },
    )
    assert r3.status_code == 200

    r4 = client.delete(f"/api/admin/behoerden/{bid}")
    assert r4.status_code == 200
