"""Daten-Retention — löscht abgelaufene Jobs samt Dateien und DB-Einträgen."""

import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path

from models import Job, VorgangBewertung
from sqlalchemy.orm import Session

from utils.zeit import jetzt_utc

logger = logging.getLogger(__name__)


def loesche_abgelaufene_jobs(db: Session, retention_tage: int) -> int:
    """Löscht Jobs, deren erstellt_am älter als retention_tage ist.

    Entfernt je Job: das Verzeichnis DATA_DIR/jobs/<id>, die zugehörigen
    VorgangBewertung-Datensätze (mit Cascade-Kindern) und den Job-Eintrag.
    Rückgabe: Anzahl gelöschter Jobs.
    """
    grenze = jetzt_utc() - timedelta(days=retention_tage)
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    abgelaufen = db.query(Job).filter(Job.erstellt_am < grenze).all()

    geloescht = 0
    for job in abgelaufen:
        try:
            job_verzeichnis = data_dir / "jobs" / job.id
            if job_verzeichnis.exists():
                shutil.rmtree(job_verzeichnis, ignore_errors=True)
            for vb in db.query(VorgangBewertung).filter_by(job_id=job.id).all():
                db.delete(vb)
            db.delete(job)
            db.commit()
            geloescht += 1
            logger.info("Retention: Job %s gelöscht (erstellt %s).", job.id, job.erstellt_am)
        except Exception as e:
            db.rollback()
            logger.warning("Retention: Job %s konnte nicht gelöscht werden: %s", job.id, e)
    return geloescht
