"""Tests für die Daten-Retention (Löschung abgelaufener Jobs)."""

from datetime import timedelta

import pytest
from models import Base, Job, engine
from sqlalchemy.orm import Session
from utils.retention import loesche_abgelaufene_jobs
from utils.zeit import jetzt_utc


@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    sitzung = Session(engine)
    yield sitzung
    sitzung.close()


def test_loescht_alte_jobs_behaelt_neue(db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db.add(Job(id="ret-alt", dateiname="alt.zip", erstellt_am=jetzt_utc() - timedelta(days=40)))
    db.add(Job(id="ret-neu", dateiname="neu.zip", erstellt_am=jetzt_utc() - timedelta(days=5)))
    db.commit()
    (tmp_path / "jobs" / "ret-alt").mkdir(parents=True)
    (tmp_path / "jobs" / "ret-neu").mkdir(parents=True)

    anzahl = loesche_abgelaufene_jobs(db, retention_tage=30)

    assert anzahl >= 1
    assert db.get(Job, "ret-alt") is None
    assert db.get(Job, "ret-neu") is not None
    assert not (tmp_path / "jobs" / "ret-alt").exists()
    assert (tmp_path / "jobs" / "ret-neu").exists()
