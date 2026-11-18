"""Legt beim Start einen ersten Admin aus INITIAL_ADMIN_* an (idempotent)."""

import logging
import os

from models import User
from sqlalchemy.orm import Session

from utils.auth import hash_passwort, ist_platzhalter_oder_zu_kurz

logger = logging.getLogger(__name__)


def seede_admin(db: Session) -> None:
    """Erstellt einen freigeschalteten Admin, falls noch keiner existiert.

    Gesteuert über INITIAL_ADMIN_EMAIL + INITIAL_ADMIN_PASSWORD. Existiert bereits
    irgendein Admin, passiert nichts. Ist die angegebene E-Mail schon (als
    Nicht-Admin) vorhanden, wird sie zum freigeschalteten Admin erhoben.
    """
    email = os.getenv("INITIAL_ADMIN_EMAIL", "").strip().lower()
    passwort = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    if not email or not passwort:
        return
    mindestlaenge = int(os.getenv("AUTH_PASSWORT_MIN", "12"))
    if ist_platzhalter_oder_zu_kurz(passwort, mindestlaenge):
        logger.error(
            "INITIAL_ADMIN_PASSWORD ist ein Platzhalter oder kürzer als %d Zeichen — "
            "es wird kein Erst-Admin angelegt.",
            mindestlaenge,
        )
        return
    if db.query(User).filter_by(is_admin=True).first() is not None:
        return

    vorhandener = db.query(User).filter_by(email=email).first()
    if vorhandener is not None:
        vorhandener.is_admin = True
        vorhandener.approved = True
        db.commit()
        logger.info("Bestehender Nutzer zum Admin erhoben: user_id=%s", vorhandener.id)
        return

    pw_hash, salt = hash_passwort(passwort)
    admin = User(
        email=email,
        password_hash=pw_hash,
        salt=salt,
        name=email.split("@", 1)[0],
        approved=True,
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    logger.info("Erst-Admin angelegt: user_id=%s", admin.id)
