# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für Abbruch und Zurücksetzen eines Vorgangs."""

import asyncio
import zipfile
from pathlib import Path

import pytest
from utils.agent_runner import brich_task_ab, laeuft, registriere_task
from utils.job_dateien import loesche_zwischenstaende, schuetze_original

# ---------------------------------------------------------------------------
# Task-Registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registrierter_task_gilt_als_laufend():
    async def schlafen():
        await asyncio.sleep(30)

    task = asyncio.create_task(schlafen())
    registriere_task("job-1", task)

    assert laeuft("job-1") is True

    brich_task_ab("job-1")
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_abbruch_stoppt_den_task():
    async def schlafen():
        await asyncio.sleep(30)

    task = asyncio.create_task(schlafen())
    registriere_task("job-2", task)

    assert brich_task_ab("job-2") is True
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert laeuft("job-2") is False


@pytest.mark.asyncio
async def test_abbruch_ohne_laufenden_task_meldet_false():
    """Der hängende Job nach Container-Neustart: kein Task, aber kein Fehler."""
    assert brich_task_ab("job-gibts-nicht") is False


@pytest.mark.asyncio
async def test_beendeter_task_gilt_nicht_mehr_als_laufend():
    async def sofort_fertig():
        return None

    task = asyncio.create_task(sofort_fertig())
    registriere_task("job-3", task)
    await task
    # done_callback räumt asynchron auf
    await asyncio.sleep(0)

    assert laeuft("job-3") is False


@pytest.mark.asyncio
async def test_zweiter_task_ersetzt_den_ersten():
    async def schlafen():
        await asyncio.sleep(30)

    erster = asyncio.create_task(schlafen())
    zweiter = asyncio.create_task(schlafen())
    registriere_task("job-4", erster)
    registriere_task("job-4", zweiter)

    abgebrochen = brich_task_ab("job-4")
    erster.cancel()  # der erste ist nicht mehr registriert — selbst aufräumen
    await asyncio.gather(erster, zweiter, return_exceptions=True)

    assert abgebrochen is True
    assert zweiter.cancelled(), "Der zuletzt registrierte Task muss adressiert werden"


# ---------------------------------------------------------------------------
# Original-ZIP schützen und Zwischenstände aufräumen
# ---------------------------------------------------------------------------


def _zip(pfad: Path) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pfad, "w") as zf:
        zf.writestr("inhalt.txt", "x")
    return pfad


def test_original_wird_beim_aufraeumen_nicht_geloescht(tmp_path):
    """Ohne diesen Schutz ist nach der ersten Reparatur kein Reset mehr möglich."""
    original = _zip(tmp_path / "paket.zip")

    schuetze_original(original, original)

    assert original.exists()


def test_zwischenstand_wird_geloescht(tmp_path):
    original = _zip(tmp_path / "paket.zip")
    zwischenstand = _zip(tmp_path / "paket_repariert.zip")

    schuetze_original(zwischenstand, original)

    assert not zwischenstand.exists()
    assert original.exists()


def test_ohne_bekanntes_original_wird_geloescht(tmp_path):
    """Bestandsjobs ohne original_zip verhalten sich wie bisher."""
    alt = _zip(tmp_path / "paket.zip")

    schuetze_original(alt, None)

    assert not alt.exists()


def test_zwischenstaende_aufraeumen_behaelt_nur_das_original(tmp_path):
    original = _zip(tmp_path / "paket.zip")
    _zip(tmp_path / "paket_repariert.zip")
    _zip(tmp_path / "paket_repariert_pdfa.zip")
    (tmp_path / "arbeit_formal").mkdir()
    (tmp_path / "arbeit_formal" / "spur.txt").write_text("x")
    (tmp_path / "job_config.json").write_text("{}")

    loesche_zwischenstaende(tmp_path, original)

    assert original.exists()
    assert sorted(p.name for p in tmp_path.glob("*.zip")) == ["paket.zip"]
    assert not (tmp_path / "arbeit_formal").exists()
    assert not (tmp_path / "job_config.json").exists()


# ---------------------------------------------------------------------------
# Endpoints: abbrechen und zurücksetzen
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app
    from models import Base, engine

    Base.metadata.create_all(engine)
    return TestClient(app)


def _lege_job_an(client, tmp_path, mit_original=True):
    """Legt einen Job über den Upload-Endpoint an und liefert (job_id, verzeichnis)."""
    paket = _zip(tmp_path / "paket.zip")
    with open(paket, "rb") as f:
        r = client.post("/api/upload", files={"datei": ("paket.zip", f, "application/zip")})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    from config import DATA_DIR
    from models import Job, Session, engine

    with Session(engine) as db:
        job = db.get(Job, job_id)
        if not mit_original:
            job.original_zip = None
            db.commit()
    return job_id, Path(DATA_DIR) / "jobs" / job_id


def test_upload_setzt_original_zip(client, tmp_path):
    job_id, _ = _lege_job_an(client, tmp_path)

    from models import Job, Session, engine

    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.original_zip
        assert job.original_zip == job.aktuelles_zip


def test_abbrechen_ohne_laufenden_task_korrigiert_den_status(client, tmp_path):
    """Der hängende Job: Status steht auf laufend, aber kein Task existiert."""
    job_id, _ = _lege_job_an(client, tmp_path)

    from models import AgentStatus, Job, JobStatus, Session, engine

    with Session(engine) as db:
        job = db.get(Job, job_id)
        job.status = JobStatus.laufend
        job.status_formal = AgentStatus.laufend
        job.aktueller_schritt = "formal"
        db.commit()

    r = client.post(f"/api/job/{job_id}/abbrechen")

    assert r.status_code == 200, r.text
    assert r.json()["task_gestoppt"] is False
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.status_formal == AgentStatus.ausstehend
        assert job.status != JobStatus.laufend


def test_zuruecksetzen_stellt_den_ausgangszustand_her(client, tmp_path):
    job_id, verzeichnis = _lege_job_an(client, tmp_path)

    from models import AgentStatus, Bewertung, Job, JobStatus, Session, engine

    # Zustand eines mitten im Prozess stehenden Jobs herstellen
    with Session(engine) as db:
        job = db.get(Job, job_id)
        job.status = JobStatus.laufend
        job.status_formal = AgentStatus.ok
        job.ergebnis_formal = '{"status": "ok"}'
        job.anmerkung_formal = "geprüft"
        job.archivar_bewertung = Bewertung.archivwuerdig
        job.gewaehlte_agenten = '["formal"]'
        job.aktueller_schritt = "format"
        zwischenstand = _zip(verzeichnis / "paket_repariert.zip")
        job.aktuelles_zip = str(zwischenstand)
        db.commit()
    (verzeichnis / "arbeit_formal").mkdir(exist_ok=True)

    r = client.post(f"/api/job/{job_id}/zuruecksetzen")

    assert r.status_code == 200, r.text
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.status_formal == AgentStatus.ausstehend
        assert job.ergebnis_formal is None
        assert job.anmerkung_formal is None
        assert job.archivar_bewertung is None
        assert job.gewaehlte_agenten is None
        assert job.aktuelles_zip == job.original_zip
        assert job.status == JobStatus.ausstehend
        # Audit-Log bleibt und dokumentiert das Zurücksetzen
        assert job.audit_log and "zurückgesetzt" in job.audit_log.lower()

    assert not (verzeichnis / "paket_repariert.zip").exists()
    assert not (verzeichnis / "arbeit_formal").exists()
    assert Path(job.original_zip).exists()


def test_zuruecksetzen_ohne_original_wird_abgelehnt(client, tmp_path):
    """Bestandsjobs haben kein Original — der Reset darf nichts vorspiegeln."""
    job_id, _ = _lege_job_an(client, tmp_path, mit_original=False)

    r = client.post(f"/api/job/{job_id}/zuruecksetzen")

    assert r.status_code == 409
    assert "original" in r.json()["detail"].lower()


def test_zuruecksetzen_loescht_die_bewertungen_des_jobs(client, tmp_path):
    job_id, _ = _lege_job_an(client, tmp_path)

    from models import Session, VorgangBewertung, engine

    with Session(engine) as db:
        db.add(
            VorgangBewertung(
                job_id=job_id,
                vorgang_id="v1",
                betreff="Test",
                klassifikation_vorschlag="A",
            )
        )
        db.commit()

    client.post(f"/api/job/{job_id}/zuruecksetzen")

    with Session(engine) as db:
        rest = db.query(VorgangBewertung).filter_by(job_id=job_id).all()
        assert rest == []


# ---------------------------------------------------------------------------
# Aufräumen beim Abbruch eines laufenden Agenten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abgebrochener_agent_raeumt_auf_und_setzt_status_zurueck(client, tmp_path):
    """Nach dem Abbruch darf kein Arbeitsverzeichnis und kein Teilstatus bleiben."""
    from pathlib import Path as P

    from models import AgentStatus, Job, Session, engine
    from utils.agent_runner import fuehre_agent_aus, markiere_sse_beendet

    job_id, verzeichnis = _lege_job_an(client, tmp_path)
    markiere_sse_beendet(job_id)

    gestartet = asyncio.Event()

    async def langsamer_agent(_job_id, _arbeitsverzeichnis):
        gestartet.set()
        await asyncio.sleep(30)
        return {"status": "ok"}

    task = asyncio.create_task(fuehre_agent_aus(job_id, "formal", langsamer_agent))
    registriere_task(job_id, task)
    await asyncio.wait_for(gestartet.wait(), timeout=10)

    assert brich_task_ab(job_id) is True
    await asyncio.gather(task, return_exceptions=True)

    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.status_formal == AgentStatus.ausstehend
        assert job.ergebnis_formal is None

    assert not (P(verzeichnis) / "arbeit_formal").exists(), "Arbeitsverzeichnis blieb liegen"


def test_bestands_datenbank_bekommt_original_zip_spalte(tmp_path, monkeypatch):
    """Die Auto-Migration muss die Spalte nachziehen — Alembic läuft im Container nicht."""
    import sqlite3

    import models

    db_datei = tmp_path / "alt.db"
    conn = sqlite3.connect(db_datei)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, dateiname TEXT, aktuelles_zip TEXT)")
    conn.execute("CREATE TABLE llm_einstellung (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(models, "DATABASE_URL", f"sqlite:///{db_datei}")
    models._migriere_fehlende_spalten()

    conn = sqlite3.connect(db_datei)
    spalten = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    conn.close()
    assert "original_zip" in spalten
