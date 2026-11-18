"""Gemeinsame FastAPI-Abhängigkeiten und Hilfsfunktionen für alle Router-Module."""

import json
from typing import Literal

from fastapi import HTTPException, Request
from models import Job
from sqlalchemy.orm import Session
from utils.zeit import jetzt_utc

AgentName = Literal["formal", "format", "inhalt", "bewertung", "erschliessung"]


def _hole_job(job_id: str, db: Session) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job nicht gefunden: {job_id}")
    return job


def _audit_log(job: "Job", aktion: str, details: str = "") -> None:
    """Fügt einen Eintrag zum Audit-Log des Jobs hinzu."""
    eintraege = json.loads(job.audit_log) if job.audit_log else []
    eintraege.append(
        {
            "zeitpunkt": jetzt_utc().isoformat(),
            "aktion": aktion,
            "details": details,
        }
    )
    job.audit_log = json.dumps(eintraege, ensure_ascii=False)


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# utils.auth-Import steht hier unten, um einen Import-Zyklus beim Modul-Laden zu
# vermeiden (dependencies wird von Routern importiert, nicht umgekehrt).
from utils.auth import ermittle_auth_modus


def require_admin(request: Request) -> dict:
    """FastAPI-Dependency: erlaubt nur Admins. Im Modus 'disabled' Dev-Admin.

    Wirft 403, wenn kein angemeldeter Admin vorliegt.
    """
    if ermittle_auth_modus() == "disabled":
        return {"id": 0, "name": "Entwickler", "email": "dev@localhost", "is_admin": True}
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict) or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Adminrechte erforderlich.")
    return user
