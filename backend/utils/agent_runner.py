"""Generischer Agent-Runner: Führt einen Agenten aus und verwaltet ZIP/DB/SSE."""

import asyncio
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from models import AgentStatus, Job, JobStatus, engine
from sqlalchemy.orm import Session

from utils.logging_setup import pseudonymisiere_dateiname
from utils.zip_handler import entpacke_zip, packe_zip

logger = logging.getLogger(__name__)

# Globales Dict: job_id → asyncio.Queue für SSE-Events.
# Sentinel-Wert _SSE_BEENDET markiert Jobs, deren Stream bereits geschlossen
# wurde — neue Events werden dann verworfen statt eine neue Queue lazy
# anzulegen, die nie wieder ein Consumer findet (Memory-Leak-Schutz).
_SSE_BEENDET: object = object()
sse_queues: dict[str, asyncio.Queue | object] = {}


# ---------------------------------------------------------------------------
# Task-Registry — welcher Agent läuft gerade für welchen Job?
#
# Vorher lagen die Tasks in einem Set ohne Job-Bezug; ein bestimmter Job ließ
# sich damit gar nicht adressieren. Für den Abbruch muss die Zuordnung stehen.
# ---------------------------------------------------------------------------

_laufende_tasks: dict[str, asyncio.Task] = {}


def registriere_task(job_id: str, task: asyncio.Task) -> None:
    """Merkt sich den laufenden Agent-Task eines Jobs.

    Ein bereits registrierter Task wird ersetzt — pro Job läuft nur einer.
    Der Eintrag räumt sich selbst ab, sobald der Task endet.
    """
    _laufende_tasks[job_id] = task

    def _aufraeumen(beendeter: asyncio.Task, _job_id: str = job_id) -> None:
        if _laufende_tasks.get(_job_id) is beendeter:
            _laufende_tasks.pop(_job_id, None)

    task.add_done_callback(_aufraeumen)


def laeuft(job_id: str) -> bool:
    """True, wenn für den Job gerade ein Agent-Task läuft."""
    task = _laufende_tasks.get(job_id)
    return task is not None and not task.done()


def brich_task_ab(job_id: str) -> bool:
    """Bricht den laufenden Agent-Task eines Jobs ab.

    Returns:
        True, wenn ein laufender Task abgebrochen wurde. False, wenn keiner
        registriert war — das ist der Normalfall bei einem Job, der nach einem
        Neustart nur noch in der DB auf "läuft" steht.
    """
    task = _laufende_tasks.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    logger.info("[Job %s] Agent-Task abgebrochen.", job_id)
    return True


def hole_sse_queue(job_id: str) -> asyncio.Queue | None:
    """Gibt die SSE-Queue für einen Job zurück oder erstellt sie bei Bedarf.

    Rückgabe `None`, wenn der Stream bereits beendet wurde — Sender müssen
    das prüfen und Events dann verwerfen.
    """
    bestehend = sse_queues.get(job_id)
    if bestehend is _SSE_BEENDET:
        return None
    if bestehend is None:
        neu: asyncio.Queue = asyncio.Queue()
        sse_queues[job_id] = neu
        return neu
    return bestehend  # type: ignore[return-value]


def markiere_sse_beendet(job_id: str) -> None:
    """Markiert die Queue als beendet — vom SSE-Generator im finally aufrufen."""
    sse_queues[job_id] = _SSE_BEENDET


async def sende_sse_event(
    job_id: str,
    agent: str,
    status: str,
    message: str = "",
    progress: int = 0,
    extra: dict | None = None,
) -> None:
    """Sendet ein SSE-Event in die Queue des Jobs.

    Optionales `extra`-Dict wird in das Event gemerged (z.B. um eine
    Tiefenprüfung einem konkreten Vorgang zuzuordnen). Pflichtfelder
    werden nicht überschrieben.

    Wenn der zugehörige SSE-Stream bereits geschlossen ist, wird das Event
    verworfen — sonst würde sich eine Queue ohne Consumer mit Events füllen.
    """
    queue = hole_sse_queue(job_id)
    if queue is None:
        logger.debug("[Job %s] SSE-Event verworfen (Stream bereits beendet).", job_id)
        return
    event = {
        "agent": agent,
        "status": status,
        "message": message,
        "progress": progress,
    }
    if extra:
        for k, v in extra.items():
            if k not in event:
                event[k] = v
    await queue.put(event)
    logger.debug("[Job %s] SSE-Event: %s", job_id, event)


async def fuehre_agent_aus(
    job_id: str,
    agent_name: str,
    agent_funktion: Callable[..., Any],
) -> None:
    """
    Führt einen Agenten aus und verwaltet den kompletten Lebenszyklus:

    1. Job aus DB laden, aktuelles_zip lesen
    2. ZIP in Arbeitsverzeichnis entpacken
    3. SSE-Event: Agent gestartet
    4. Agent-Funktion aufrufen
    5. Ergebnis als JSON in DB speichern
    6. Neues ZIP mit Suffix packen, aktuelles_zip aktualisieren
    7. SSE-Event: Agent abgeschlossen
    """
    db = Session(engine)
    try:
        job = db.get(Job, job_id)
        if not job:
            logger.error("[Job %s] Job nicht gefunden.", job_id)
            return

        zip_pfad = Path(job.aktuelles_zip)
        if not zip_pfad.exists():
            logger.error(
                "[Job %s] ZIP nicht gefunden: %s", job_id, pseudonymisiere_dateiname(str(zip_pfad))
            )
            _setze_fehler(db, job, agent_name, "ZIP-Datei nicht gefunden.")
            await sende_sse_event(job_id, agent_name, "fehler", "ZIP-Datei nicht gefunden.")
            return

        # Arbeitsverzeichnis vorbereiten
        arbeitsverzeichnis = zip_pfad.parent / f"arbeit_{agent_name}"
        if arbeitsverzeichnis.exists():
            shutil.rmtree(arbeitsverzeichnis)
        arbeitsverzeichnis.mkdir(parents=True)

        await sende_sse_event(job_id, agent_name, "laufend", "Entpacke ZIP...", 5)
        # ZIP-Operationen in Thread-Pool — blockiert nicht den Event-Loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, entpacke_zip, zip_pfad, arbeitsverzeichnis)

        # Agent ausführen
        await sende_sse_event(job_id, agent_name, "laufend", f"Agent '{agent_name}' läuft...", 30)
        ergebnis = await agent_funktion(job_id, arbeitsverzeichnis)

        # Ergebnis in DB speichern
        ergebnis_json = json.dumps(ergebnis, ensure_ascii=False)
        setattr(job, f"ergebnis_{agent_name}", ergebnis_json)

        # Neues ZIP packen
        agent_status = ergebnis.get("status", "ok")
        suffix = "ok" if agent_status == "ok" else "fehler"
        neues_zip = zip_pfad.parent / f"{zip_pfad.stem}_{agent_name}_{suffix}.zip"

        await sende_sse_event(job_id, agent_name, "laufend", "Packe Ergebnis-ZIP...", 80)
        # ZIP packen im Thread-Pool (blockiert nicht den Event-Loop)
        await loop.run_in_executor(None, packe_zip, arbeitsverzeichnis, neues_zip, False)

        # Altes Zwischen-ZIP aufräumen (spart Disk I/O bei großen Dateien)
        altes_zip = zip_pfad
        job.aktuelles_zip = str(neues_zip)
        # Status korrekt setzen: "wartet" bei Erfolg, "fehler" bei Fehler
        if agent_status == "ok":
            setattr(job, f"status_{agent_name}", AgentStatus.wartet)
        else:
            setattr(job, f"status_{agent_name}", AgentStatus.fehler)
        db.commit()

        if altes_zip.exists() and altes_zip != neues_zip:
            try:
                altes_zip.unlink()
                logger.debug(
                    "[Job %s] Altes ZIP gelöscht: %s",
                    job_id,
                    pseudonymisiere_dateiname(altes_zip.name),
                )
            except OSError:
                pass  # Nicht kritisch

        sse_status = "wartet" if agent_status == "ok" else "fehler"
        await sende_sse_event(
            job_id,
            agent_name,
            sse_status,
            "Prüfschritt abgeschlossen — bitte prüfen und bestätigen.",
            100,
        )
        logger.info(
            "[Job %s] Agent '%s' abgeschlossen mit Status: %s", job_id, agent_name, agent_status
        )

        # Arbeitsverzeichnis aufräumen
        shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)

    except asyncio.CancelledError:
        # Abbruch durch den Archivar. CancelledError erbt von BaseException und
        # wird deshalb NICHT vom except-Exception-Zweig erfasst — ohne diesen
        # Block bliebe das Arbeitsverzeichnis liegen und der Teilstatus stehen.
        logger.info("[Job %s] Agent '%s' abgebrochen.", job_id, agent_name)
        try:
            job = db.get(Job, job_id)
            if job:
                setattr(job, f"status_{agent_name}", AgentStatus.ausstehend)
                setattr(job, f"ergebnis_{agent_name}", None)
                job.status = JobStatus.ausstehend
                db.commit()
        except Exception:
            logger.exception("[Job %s] Status nach Abbruch nicht zurückgesetzt.", job_id)
        try:
            shutil.rmtree(zip_pfad.parent / f"arbeit_{agent_name}", ignore_errors=True)
        except Exception:
            logger.exception("[Job %s] Arbeitsverzeichnis nicht aufgeräumt.", job_id)
        raise  # Task endet als cancelled — der Aufrufer soll das sehen

    except Exception as e:
        logger.exception("[Job %s] Fehler im Agent '%s': %s", job_id, agent_name, e)
        try:
            job = db.get(Job, job_id)
            if job:
                _setze_fehler(db, job, agent_name, str(e))
        except Exception:
            logger.exception("[Job %s] Fehler beim Speichern des Fehlerstatus.", job_id)
        await sende_sse_event(job_id, agent_name, "fehler", "Verarbeitung fehlgeschlagen.")
    finally:
        db.close()


def _setze_fehler(db: Session, job: Job, agent_name: str, nachricht: str) -> None:
    """Setzt den Agentenstatus auf Fehler und speichert die Fehlermeldung."""
    setattr(job, f"status_{agent_name}", AgentStatus.fehler)
    setattr(
        job,
        f"ergebnis_{agent_name}",
        json.dumps(
            {"status": "fehler", "fehler": nachricht},
            ensure_ascii=False,
        ),
    )
    job.status = JobStatus.fehler
    db.commit()
