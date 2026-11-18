# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Persistente Lauf-Konfiguration pro Job (ohne DB-Schema-Änderung).

Wird als kleine JSON-Datei im Job-Verzeichnis abgelegt. So lässt sich z.B. die
gewählte Bewertungs-/Erschließungsebene zwischen Router (Schreiben beim
Agentenstart) und Agent (Lesen) teilen, ohne eine neue DB-Spalte und damit eine
Migration zu erfordern (init_db nutzt create_all, kein Alembic-Upgrade).
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DATEINAME = "run_config.json"
ERLAUBTE_EBENEN = {"akte", "teilakte", "vorgang", "auto"}


def _pfad(job_verzeichnis: Path) -> Path:
    return Path(job_verzeichnis) / _DATEINAME


def lese_job_config(job_verzeichnis: Path) -> dict:
    """Liest die Lauf-Konfiguration eines Jobs (leeres Dict, wenn keine da)."""
    try:
        p = _pfad(job_verzeichnis)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # defekte/teilgeschriebene Datei ist kein harter Fehler
        logger.warning("Job-Config nicht lesbar (%s): %s", job_verzeichnis, e)
    return {}


def schreibe_job_config(job_verzeichnis: Path, **felder) -> None:
    """Merged die übergebenen Felder in die Lauf-Konfiguration des Jobs."""
    try:
        verzeichnis = Path(job_verzeichnis)
        verzeichnis.mkdir(parents=True, exist_ok=True)
        bestehend = lese_job_config(verzeichnis)
        bestehend.update({k: v for k, v in felder.items() if v is not None})
        _pfad(verzeichnis).write_text(json.dumps(bestehend, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("Job-Config nicht schreibbar (%s): %s", job_verzeichnis, e)


def lese_bewertungsebene(job_verzeichnis: Path) -> str:
    """Liefert die gewählte Bewertungsebene oder 'auto'."""
    ebene = lese_job_config(job_verzeichnis).get("bewertungsebene")
    return ebene if ebene in ERLAUBTE_EBENEN else "auto"
