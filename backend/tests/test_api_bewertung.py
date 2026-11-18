"""Tests für Bewertungs-API-Endpoints."""

import uuid

import pytest
from fastapi.testclient import TestClient
from main import app
from models import Base, VorgangBewertung, VorgangIndizTreffer, engine, get_db


@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    yield TestClient(app)


@pytest.fixture
def vorgang_in_db():
    db = next(get_db())
    test_job = f"api-{uuid.uuid4().hex[:8]}"
    vb = VorgangBewertung(
        job_id=test_job,
        vorgang_id=f"vg-{test_job}",
        aktenzeichen=f"AZ-{test_job}/2024",
        betreff="Test",
        rohscore=78,
        finaler_score=78,
        klassifikation_vorschlag="A",
        llm_begruendung="weil",
        schlagworte=["x"],
    )
    db.add(vb)
    db.commit()
    db.add(
        VorgangIndizTreffer(
            vorgang_bewertung_id=vb.id,
            indiz_schluessel="federfuehrung_eigene_stelle",
            erfuellt=True,
            punkte_beitrag=25,
            evidenz="ev",
        )
    )
    db.commit()
    vb_id = vb.id
    job_id = vb.job_id
    db.close()
    yield {"id": vb_id, "job_id": job_id}


def test_vorgaenge_listen(client, vorgang_in_db):
    r = client.get(f"/api/bewertung/vorgaenge/{vorgang_in_db['job_id']}")
    assert r.status_code == 200
    daten = r.json()
    assert len(daten) == 1
    assert daten[0]["klassifikation_vorschlag"] == "A"
    assert len(daten[0]["treffer"]) == 1


def test_entscheidung_speichern(client, vorgang_in_db):
    r = client.post(
        f"/api/bewertung/vorgang/{vorgang_in_db['id']}/entscheidung",
        json={"klassifikation_final": "B", "begruendung": "manuell heruntergestuft"},
    )
    assert r.status_code == 200

    db = next(get_db())
    refreshed = db.query(VorgangBewertung).get(vorgang_in_db["id"])
    assert refreshed.klassifikation_final == "B"
    assert refreshed.override_begruendung == "manuell heruntergestuft"
    db.close()


def test_entscheidung_override_ohne_begruendung_fehler(client, vorgang_in_db):
    """Wenn final ≠ vorschlag, ist Begründung Pflicht."""
    r = client.post(
        f"/api/bewertung/vorgang/{vorgang_in_db['id']}/entscheidung",
        json={"klassifikation_final": "V", "begruendung": ""},
    )
    assert r.status_code == 400
