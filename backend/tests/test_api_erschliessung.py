"""Tests für den Erschließungs-Speichern-Endpoint."""

import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from main import app
from models import Base, Job, engine, get_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    yield TestClient(app)


def test_erschliessung_speichern_mit_arbeitsverzeichnis(client, tmp_path, monkeypatch):
    """Regression: erschliessung_speichern brach mit NameError ab, sobald das
    arbeit_erschliessung-Verzeichnis existierte (undefinierte Variable)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    job_id = f"ersch-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    db.add(Job(id=job_id, dateiname="test.zip", aktuelles_zip="test.zip"))
    db.commit()
    db.close()

    arbeit = tmp_path / "jobs" / job_id / "arbeit_erschliessung"
    arbeit.mkdir(parents=True)
    shutil.copy(FIXTURES / "gueltig_xdomea.xml", arbeit / "aussonderung.xml")

    r = client.post(f"/api/job/{job_id}/erschliessung", json={"titel": "Neuer Titel"})
    assert r.status_code == 200


def test_erschliessung_speichern_ohne_arbeitsverzeichnis(client, tmp_path, monkeypatch):
    """Ohne Arbeitsverzeichnis werden die Daten nur in der DB aktualisiert."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    job_id = f"ersch-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    db.add(Job(id=job_id, dateiname="test.zip", aktuelles_zip="test.zip"))
    db.commit()
    db.close()

    r = client.post(f"/api/job/{job_id}/erschliessung", json={"titel": "Nur DB"})
    assert r.status_code == 200
