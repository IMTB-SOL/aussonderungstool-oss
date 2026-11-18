"""System-Endpoints: Gesundheits- und Bereitschaftsprüfung."""

import os
from pathlib import Path

from config import VERSION
from fastapi import APIRouter, Depends, HTTPException
from models import get_db
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """Gesundheitsprüfung für Load-Balancer und Monitoring."""
    return {"status": "healthy", "version": VERSION}


@router.get("/ready")
def readiness_check(db: Session = Depends(get_db)) -> dict:
    """Bereitschaftsprüfung: DB-Verbindung + erforderliche Tools."""
    probleme: list[str] = []

    # DB-Verbindung testen
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        probleme.append("Datenbankverbindung fehlgeschlagen")

    # veraPDF prüfen
    verapdf = os.getenv("VERAPDF_PATH", "")
    if verapdf and not Path(verapdf).exists():
        # Kein Pfad in der Antwort — /ready ist ohne Anmeldung erreichbar.
        probleme.append("veraPDF nicht gefunden")

    if probleme:
        raise HTTPException(status_code=503, detail={"ready": False, "probleme": probleme})
    return {"ready": True}
