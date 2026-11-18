"""Tests für die Pydantic-Request-Validierung in routers/jobs.py + routers/agenten.py.

Verifiziert, dass die Endpoints nach der Migration von 'payload: dict' auf
typisierte BaseModel-Schemas saubere 422-Antworten mit Feldfehler liefern,
statt die Validierung manuell im Handler durchzuführen.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from main import app
from models import Base, Job, engine, get_db


@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    yield TestClient(app)


@pytest.fixture
def job_id():
    job_id = f"val-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    db.add(Job(id=job_id, dateiname="test.zip"))
    db.commit()
    db.close()
    yield job_id


# ---------------------------------------------------------------------------
# AnmerkungBody — anmerkung ist Pflicht und muss nicht-leer sein
# ---------------------------------------------------------------------------


def test_protokoll_anmerkung_leer_string_gibt_422(client, job_id):
    """Leere anmerkung verstößt gegen min_length=1."""
    resp = client.post(f"/api/job/{job_id}/protokoll/formal/anmerkung", json={"anmerkung": ""})
    assert resp.status_code == 422
    assert "anmerkung" in resp.text


def test_protokoll_anmerkung_fehlend_gibt_422(client, job_id):
    """Komplett fehlende anmerkung verstößt gegen Pflichtfeld."""
    resp = client.post(f"/api/job/{job_id}/protokoll/formal/anmerkung", json={})
    assert resp.status_code == 422


def test_protokoll_anmerkung_happy_path(client, job_id):
    """Gültiger Body wird durchgereicht."""
    resp = client.post(
        f"/api/job/{job_id}/protokoll/formal/anmerkung",
        json={"anmerkung": "Sieht sauber aus."},
    )
    assert resp.status_code == 200
    assert resp.json()["gespeichert"] is True


# ---------------------------------------------------------------------------
# EntscheidungBody — bewertung ist Pflicht und muss aus dem Enum stammen
# ---------------------------------------------------------------------------


def test_entscheidung_ungueltige_bewertung_gibt_422(client, job_id):
    """Bewertungs-Wert außerhalb des Enums verstößt gegen das Schema."""
    resp = client.post(
        f"/api/job/{job_id}/entscheidung",
        json={"bewertung": "vielleicht-archivwürdig"},
    )
    assert resp.status_code == 422


def test_entscheidung_fehlende_bewertung_gibt_422(client, job_id):
    """bewertung ist Pflicht."""
    resp = client.post(f"/api/job/{job_id}/entscheidung", json={"begruendung": "..."})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DateinameBody — dateiname ist Pflicht und muss nicht-leer sein
# ---------------------------------------------------------------------------


def test_entferne_docx_leerer_dateiname_gibt_422(client, job_id):
    """dateiname='' verstößt gegen min_length=1."""
    resp = client.post(f"/api/job/{job_id}/entferne-docx", json={"dateiname": ""})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PdfaKonvertierungBody — zielformat darf nur 1b/2b/3b sein
# ---------------------------------------------------------------------------


def test_konvertieren_pdfa_falsches_zielformat_gibt_422(client, job_id):
    """zielformat='4b' verstößt gegen das Literal."""
    resp = client.post(f"/api/job/{job_id}/konvertieren-pdfa", json={"zielformat": "4b"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Optionale Bodies — fehlender Body darf nicht 422 sein
# ---------------------------------------------------------------------------


def test_agent_starten_ohne_body(client, job_id):
    """run/{agent} darf ohne Body aufgerufen werden (Body ist optional)."""
    resp = client.post(f"/api/job/{job_id}/run/formal")
    # 200 (oder 5xx wenn der Agent nicht starten kann — wir prüfen nur, dass es kein 422 ist)
    assert resp.status_code != 422


def test_agent_bestaetigen_ohne_body(client, job_id):
    """bestaetigen/{agent} darf ohne Body aufgerufen werden."""
    resp = client.post(f"/api/job/{job_id}/bestaetigen/formal")
    assert resp.status_code != 422
