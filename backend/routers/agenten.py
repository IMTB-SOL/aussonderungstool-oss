"""Agenten-Router: Endpoints zum Starten, Bestätigen und Reparieren von Agenten-Läufen."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Literal

from agents.agent_bewertung import bewerte_vorgang
from agents.agent_erschliessung import erschliesse_vorgang
from agents.agent_formal import pruefe_formal
from agents.agent_format import pruefe_format
from agents.agent_inhalt import vergleiche_inhalt
from dependencies import AgentName, _audit_log, _hole_job
from fastapi import APIRouter, Depends, HTTPException
from models import (
    AgentStatus,
    JobStatus,
    get_db,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from utils.agent_runner import (
    brich_task_ab,
    fuehre_agent_aus,
    laeuft,
    registriere_task,
    sende_sse_event,
)
from utils.job_dateien import finde_dateien, schuetze_original
from utils.logging_setup import pseudonymisiere_dateiname
from utils.prozess import kommuniziere_mit_timeout

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request-Schemas — eingehende JSON-Bodies werden validiert
# ---------------------------------------------------------------------------


class AgentStartBody(BaseModel):
    """Optional beim Start eines Agenten: gewählte Agenten + Bewertungsebene."""

    gewaehlte_agenten: list[str] | None = None
    # "akte" | "teilakte" | "vorgang" — gilt für Bewertung und Erschließung.
    bewertungsebene: Literal["akte", "teilakte", "vorgang"] | None = None


class BestaetigenBody(BaseModel):
    """Optional beim Bestätigen eines Prüfschritts: freie Anmerkung des Archivars."""

    anmerkung: str = ""


class DateinameBody(BaseModel):
    """Eine konkrete Datei aus dem Paket anwählen (z. B. zum Entfernen oder Neukonvertieren)."""

    dateiname: str = Field(..., min_length=1)


class PdfaKonvertierungBody(BaseModel):
    """Optional: Zielformat für die PDF/A-Konvertierung."""

    zielformat: Literal["1b", "2b", "3b"] | None = None


# ---------------------------------------------------------------------------
# Konstanten und Hilfsfunktionen
# ---------------------------------------------------------------------------

AGENT_FUNKTIONEN: dict[str, callable] = {
    "formal": pruefe_formal,
    "format": pruefe_format,
    "inhalt": vergleiche_inhalt,
    "bewertung": bewerte_vorgang,
    "erschliessung": erschliesse_vorgang,
}

AGENT_REIHENFOLGE = ["bewertung", "formal", "inhalt", "format", "erschliessung"]


def _naechster_agent(aktuell: str, job=None) -> str | None:
    """Gibt den nächsten gewählten Agent zurück, oder None wenn fertig."""
    gewaehlte = None
    if job and job.gewaehlte_agenten:
        try:
            gewaehlte = json.loads(job.gewaehlte_agenten)
        except (json.JSONDecodeError, TypeError):
            pass

    idx = AGENT_REIHENFOLGE.index(aktuell)
    for i in range(idx + 1, len(AGENT_REIHENFOLGE)):
        kandidat = AGENT_REIHENFOLGE[i]
        if gewaehlte is None or kandidat in gewaehlte:
            return kandidat
    return None


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/run/{agent}
# ---------------------------------------------------------------------------


ALLE_AGENTEN = ["formal", "format", "inhalt", "bewertung", "erschliessung"]


def _setze_alle_schritte_zurueck(job) -> None:
    """Setzt Status, Ergebnis, Chat und Anmerkung aller Agenten zurück."""
    for name in ALLE_AGENTEN:
        setattr(job, f"status_{name}", AgentStatus.ausstehend)
        setattr(job, f"ergebnis_{name}", None)
        setattr(job, f"chat_{name}", None)
        setattr(job, f"anmerkung_{name}", None)


@router.post("/api/job/{job_id}/abbrechen")
async def agent_abbrechen(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Bricht den laufenden Prüfschritt ab und lässt den Job stehen.

    Das Teilergebnis wird verworfen, der Schritt geht auf "ausstehend" zurück;
    bereits abgeschlossene Schritte bleiben erhalten.

    Der Endpoint antwortet auch dann mit 200, wenn gar kein Task läuft: Nach
    einem Neustart steht ein Job unter Umständen nur noch in der Datenbank auf
    "läuft". Genau dieser festhängende Zustand wird hier aufgelöst.
    """
    job = _hole_job(job_id, db)

    gestoppt = brich_task_ab(job_id)
    agent = job.aktueller_schritt or ""

    if agent in ALLE_AGENTEN:
        setattr(job, f"status_{agent}", AgentStatus.ausstehend)
        setattr(job, f"ergebnis_{agent}", None)

    # Der Job wartet danach wieder auf eine Aktion des Archivars.
    job.status = JobStatus.ausstehend
    _audit_log(job, "Prüfschritt abgebrochen", agent or "unbekannt")
    db.commit()

    await sende_sse_event(job_id, agent or "formal", "abgebrochen", "Prüfschritt abgebrochen.", 0)
    logger.info("[Job %s] Abbruch (Task gestoppt: %s, Agent: %s).", job_id, gestoppt, agent)

    return {"job_id": job_id, "abgebrochen": True, "task_gestoppt": gestoppt, "agent": agent}


@router.post("/api/job/{job_id}/zuruecksetzen")
async def job_zuruecksetzen(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Setzt den Vorgang auf den Zustand unmittelbar nach dem Upload zurück.

    Der Job behält ID, Dateiname und Audit-Log; alles andere — Ergebnisse,
    Bewertungen, Anmerkungen, Archivar-Entscheidung und sämtliche ZIP-Stände
    außer dem Original — wird verworfen.
    """
    from pathlib import Path

    from models import DokumentBewertung, VorgangBewertung, VorgangIndizTreffer
    from utils.job_dateien import loesche_zwischenstaende

    job = _hole_job(job_id, db)

    if not job.original_zip or not Path(job.original_zip).exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "Für diesen Vorgang ist kein Original-Paket mehr vorhanden — "
                "er stammt aus der Zeit vor der Aufbewahrung. Zurücksetzen ist "
                "nicht möglich; bitte das Paket erneut hochladen."
            ),
        )

    # Läuft noch ein Agent, wird er zuerst gestoppt.
    if laeuft(job_id):
        brich_task_ab(job_id)

    # Bewertungsdaten des Jobs entfernen (Treffer/Dokumente hängen an der
    # Vorgangsbewertung und gehen per cascade mit).
    vorgang_ids = [
        v.id for v in db.query(VorgangBewertung).filter(VorgangBewertung.job_id == job_id).all()
    ]
    if vorgang_ids:
        db.query(VorgangIndizTreffer).filter(
            VorgangIndizTreffer.vorgang_bewertung_id.in_(vorgang_ids)
        ).delete(synchronize_session=False)
        db.query(DokumentBewertung).filter(
            DokumentBewertung.vorgang_bewertung_id.in_(vorgang_ids)
        ).delete(synchronize_session=False)
    db.query(VorgangBewertung).filter(VorgangBewertung.job_id == job_id).delete(
        synchronize_session=False
    )

    _setze_alle_schritte_zurueck(job)
    job.archivar_bewertung = None
    job.archivar_begruendung = None
    job.archivar_kommentar = None
    job.entscheidung_am = None
    job.gewaehlte_agenten = None
    job.aktueller_schritt = "formal"
    job.status = JobStatus.ausstehend
    job.aktuelles_zip = job.original_zip

    entfernt = loesche_zwischenstaende(Path(job.original_zip).parent, job.original_zip)

    _audit_log(job, "Vorgang zurückgesetzt", f"{entfernt} Zwischenstand/Zwischenstände entfernt")
    db.commit()

    await sende_sse_event(job_id, "formal", "ausstehend", "Vorgang zurückgesetzt.", 0)
    logger.info("[Job %s] Vorgang zurückgesetzt.", job_id)

    return {"job_id": job_id, "zurueckgesetzt": True, "entfernt": entfernt}


@router.post("/api/job/{job_id}/run/{agent}")
async def agent_starten(
    job_id: str,
    agent: AgentName,
    body: AgentStartBody | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Startet einen bestimmten Agenten für den angegebenen Job."""
    job = _hole_job(job_id, db)

    # Gewählte Agenten speichern (beim ersten Aufruf vom Frontend gesendet)
    if body and body.gewaehlte_agenten:
        job.gewaehlte_agenten = json.dumps(body.gewaehlte_agenten)

    # Gewählte Bewertungs-/Erschließungsebene in die Job-Config schreiben
    # (kein DB-Schema, daher als Datei im Job-Verzeichnis).
    if body and body.bewertungsebene:
        from config import DATA_DIR
        from utils.job_config import schreibe_job_config

        schreibe_job_config(DATA_DIR / "jobs" / job_id, bewertungsebene=body.bewertungsebene)

    # Altes Ergebnis löschen bei Neustart (verhindert veraltete Daten bei Crash)
    setattr(job, f"ergebnis_{agent}", None)
    status_feld = f"status_{agent}"
    setattr(job, status_feld, AgentStatus.laufend)
    job.status = JobStatus.laufend
    job.aktueller_schritt = agent
    db.commit()

    agent_funktion = AGENT_FUNKTIONEN[agent]
    task = asyncio.create_task(fuehre_agent_aus(job_id, agent, agent_funktion))
    registriere_task(job_id, task)

    logger.info("[Job %s] Agent '%s' gestartet.", job_id, agent)
    return {"job_id": job_id, "agent": agent, "status": "laufend"}


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/bestaetigen/{agent}
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/bestaetigen/{agent}")
async def agent_bestaetigen(
    job_id: str,
    agent: AgentName,
    body: BestaetigenBody | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Archivar bestätigt den Prüfschritt. Nächster Agent wird gestartet."""
    job = _hole_job(job_id, db)

    # Anmerkung speichern falls vorhanden
    anmerkung = body.anmerkung if body else ""
    if anmerkung:
        setattr(job, f"anmerkung_{agent}", anmerkung)

    _audit_log(job, f"Agent '{agent}' bestätigt", anmerkung or "Ohne Anmerkung")

    # Status von "wartet" auf den tatsächlichen Status setzen (ok/fehler aus Ergebnis)
    ergebnis_json = getattr(job, f"ergebnis_{agent}")
    if ergebnis_json:
        ergebnis = json.loads(ergebnis_json)
        tatsaechlicher_status = ergebnis.get("status", "ok")
        setattr(
            job,
            f"status_{agent}",
            AgentStatus.ok if tatsaechlicher_status == "ok" else AgentStatus.fehler,
        )
    else:
        setattr(job, f"status_{agent}", AgentStatus.ok)

    db.commit()

    # Nächsten gewählten Agent starten (überspringe abgewählte)
    naechster = _naechster_agent(agent, job)
    if naechster:
        job.aktueller_schritt = naechster
        setattr(job, f"status_{naechster}", AgentStatus.laufend)
        db.commit()
        # Sofortiges SSE-Feedback bevor der Agent startet
        await sende_sse_event(
            job_id, naechster, "laufend", f"Agent '{naechster}' wird vorbereitet…", 0
        )
        agent_funktion = AGENT_FUNKTIONEN[naechster]
        task = asyncio.create_task(fuehre_agent_aus(job_id, naechster, agent_funktion))
        registriere_task(job_id, task)
        logger.info("[Job %s] Agent '%s' bestätigt → '%s' gestartet.", job_id, agent, naechster)
        return {"job_id": job_id, "bestaetigt": agent, "naechster": naechster}
    else:
        # Alle Agenten durch → Job abschließen
        job.status = JobStatus.abgeschlossen
        job.aktueller_schritt = "fertig"
        db.commit()
        await sende_sse_event(
            job_id, "system", "abgeschlossen", "Alle Prüfschritte abgeschlossen.", 100
        )
        logger.info("[Job %s] Alle Agenten bestätigt — Job abgeschlossen.", job_id)
        return {"job_id": job_id, "bestaetigt": agent, "naechster": None, "status": "abgeschlossen"}


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/entferne-docx — DOCX entfernen nach Inhaltsvergleich
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/entferne-docx")
async def entferne_docx(job_id: str, body: DateinameBody, db: Session = Depends(get_db)) -> dict:
    """Entfernt eine DOCX-Datei aus dem aktuellen ZIP (nach bestätigtem Inhaltsvergleich)."""
    job = _hole_job(job_id, db)
    dateiname = body.dateiname

    if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
        raise HTTPException(status_code=404, detail="Aktuelles ZIP nicht gefunden.")

    zip_pfad = Path(job.aktuelles_zip)
    data_dir = os.getenv("DATA_DIR", "./data")
    arbeitsverzeichnis = Path(data_dir) / "jobs" / job_id / "arbeit_docx_entfernen"

    try:
        # ZIP entpacken
        if arbeitsverzeichnis.exists():
            import shutil

            shutil.rmtree(arbeitsverzeichnis)
        arbeitsverzeichnis.mkdir(parents=True)

        from utils.zip_handler import entpacke_zip, packe_zip

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, entpacke_zip, zip_pfad, arbeitsverzeichnis)

        # DOCX-Datei finden und löschen
        docx_name = (
            dateiname if dateiname.endswith(".docx") else dateiname.rsplit(".", 1)[0] + ".docx"
        )
        docx_dateien = finde_dateien(arbeitsverzeichnis, docx_name)

        if not docx_dateien:
            raise HTTPException(status_code=404, detail=f"DOCX-Datei '{docx_name}' nicht gefunden.")

        for docx in docx_dateien:
            docx.unlink()
            logger.info("[Job %s] DOCX entfernt: %s", job_id, pseudonymisiere_dateiname(str(docx)))

        # ZIP neu packen
        neues_zip = zip_pfad.parent / f"{zip_pfad.stem}_ohne_docx.zip"
        await loop.run_in_executor(None, packe_zip, arbeitsverzeichnis, neues_zip, False)

        # Altes ZIP ablösen — das Original des Jobs bleibt erhalten,
        # sonst wäre der Vorgang später nicht mehr zurücksetzbar.
        if zip_pfad != neues_zip:
            schuetze_original(zip_pfad, job.original_zip)
        job.aktuelles_zip = str(neues_zip)
        _audit_log(job, "DOCX entfernt", docx_name)
        db.commit()

        return {"job_id": job_id, "entfernt": docx_name}

    finally:
        # Temporäres Arbeitsverzeichnis aufräumen
        import shutil

        if arbeitsverzeichnis.exists():
            shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/reparatur/xml — XDOMEA-XML reparieren (mit Archivar-Bestätigung)
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/reparatur/xml")
async def repariere_xml(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Repariert die XDOMEA-XML-Datei: Encoding, XSD-Kompatibilitaet, fehlende Referenzen."""
    from agents.agent_formal import XSD_DATEIEN, _klassifiziere_xsd_fehler
    from utils.protokoll import ergaenze_protokoll
    from utils.xdomea_parser import (
        entferne_fehlende_referenzen,
        lese_xdomea_version,
        repariere_xsd_kompatibilitaet,
        validiere_gegen_xsd,
        versuche_xml_reparatur,
    )
    from utils.zip_handler import entpacke_zip, packe_zip

    job = _hole_job(job_id, db)
    data_dir = os.getenv("DATA_DIR", "./data")
    arbeitsverzeichnis = Path(data_dir) / "jobs" / job_id / "arbeit_formal"

    # Lifecycle-Fix: ZIP neu entpacken wenn Arbeitsverzeichnis nicht mehr existiert
    if not arbeitsverzeichnis.exists():
        if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
            raise HTTPException(status_code=404, detail="Kein ZIP vorhanden.")
        arbeitsverzeichnis.mkdir(parents=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, entpacke_zip, Path(job.aktuelles_zip), arbeitsverzeichnis)

    xml_dateien = [
        x for x in arbeitsverzeichnis.rglob("*.xml") if not x.name.startswith("protokoll_")
    ]
    if not xml_dateien:
        raise HTTPException(status_code=404, detail="Keine XML-Datei im Arbeitsverzeichnis.")

    # Reparaturoptionen aus gespeichertem Ergebnis laden
    ergebnis_formal = json.loads(job.ergebnis_formal) if job.ergebnis_formal else {}
    rep_optionen = ergebnis_formal.get("reparaturoptionen", {})
    fehlende_referenzen = rep_optionen.get("referenzen", [])

    meldungen: list[dict] = []
    repariert = 0
    fehler_nach_reparatur: list[str] = []

    # ---- Lieferverzeichnis-Sonderpfad ----
    if ergebnis_formal.get("lieferverzeichnis"):
        from agents.agent_formal import _finde_lieferverzeichnis, repariere_lieferverzeichnis

        lv_xml = _finde_lieferverzeichnis(xml_dateien)
        if lv_xml is not None:
            lv_repariert, lv_meldungen, lv_fehler = await repariere_lieferverzeichnis(
                arbeitsverzeichnis,
                lv_xml,
                rep_optionen,
            )
            repariert += lv_repariert
            meldungen.extend(lv_meldungen)
            fehler_nach_reparatur.extend(lv_fehler)

            ergaenze_protokoll(
                "formal",
                "ok" if repariert > 0 and not fehler_nach_reparatur else "fehler",
                meldungen,
                arbeitsverzeichnis,
            )

            # Neues Bundle packen
            zip_pfad = Path(job.aktuelles_zip)
            neues_zip = zip_pfad.parent / f"{zip_pfad.stem}_repariert.zip"
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, packe_zip, arbeitsverzeichnis, neues_zip, False)
            if zip_pfad != neues_zip:
                try:
                    schuetze_original(zip_pfad, job.original_zip)
                except OSError:
                    pass
            job.aktuelles_zip = str(neues_zip)

            # Ergebnis aktualisieren: bei Erfolg neue Prüfung anstoßen empfehlen,
            # oder direkt Status auf 'ok' setzen wenn alles repariert ist
            if repariert > 0 and not fehler_nach_reparatur:
                ergebnis_formal["status"] = "ok"
                ergebnis_formal["fehler"] = []
                ergebnis_formal["reparierbar"] = False
                ergebnis_formal["reparaturoptionen"]["lv_vorgaenge"] = []
            elif repariert > 0:
                ergebnis_formal["fehler"] = fehler_nach_reparatur
            ergebnis_formal["xml_repariert"] = repariert > 0
            job.ergebnis_formal = json.dumps(ergebnis_formal, ensure_ascii=False)
            db.commit()

            import shutil

            shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)

            sse_status = "wartet" if repariert > 0 and not fehler_nach_reparatur else "fehler"
            await sende_sse_event(
                job_id,
                "formal",
                sse_status,
                f"Lieferverzeichnis-Reparatur: {repariert} Vorgang/Vorgänge repariert, "
                f"{len(fehler_nach_reparatur)} Probleme verblieben.",
                100,
            )
            logger.info(
                "[Job %s] LV-Reparatur: %d Reparaturen, %d Restfehler",
                job_id,
                repariert,
                len(fehler_nach_reparatur),
            )
            return {
                "job_id": job_id,
                "repariert": repariert,
                "fehler_nach_reparatur": fehler_nach_reparatur,
                "meldungen": meldungen,
            }

    for xml_pfad in xml_dateien:
        # 1. Encoding-Reparatur (bestehendes Verhalten)
        if rep_optionen.get("encoding"):
            erfolg, details = versuche_xml_reparatur(xml_pfad)
            if erfolg:
                repariert += 1
                meldungen.append(
                    {"typ": "info", "text": f"Encoding repariert: {xml_pfad.name} — {details}"}
                )
            else:
                meldungen.append(
                    {
                        "typ": "fehler",
                        "text": f"Encoding-Reparatur fehlgeschlagen ({xml_pfad.name}): {details}",
                    }
                )

        # 2. XSD-Kompatibilitaet reparieren (CamelCase, Importbestaetigung)
        if rep_optionen.get("xsd"):
            erfolg, protokoll_eintraege = repariere_xsd_kompatibilitaet(xml_pfad)
            if erfolg:
                repariert += 1
                meldungen.extend(protokoll_eintraege)
            elif protokoll_eintraege:
                meldungen.extend(protokoll_eintraege)

        # 3. Fehlende Referenzen entfernen
        if fehlende_referenzen:
            erfolg, protokoll_eintraege = entferne_fehlende_referenzen(
                xml_pfad, fehlende_referenzen
            )
            if erfolg:
                repariert += 1
                meldungen.extend(protokoll_eintraege)
            elif protokoll_eintraege:
                meldungen.extend(protokoll_eintraege)

        # 4. Re-Validierung nach allen Reparaturen
        try:
            version = lese_xdomea_version(xml_pfad)
            xsd_pfad = XSD_DATEIEN.get(version)
            if xsd_pfad and xsd_pfad.exists():
                xsd_fehler = validiere_gegen_xsd(xml_pfad, xsd_pfad)
                if xsd_fehler:
                    kritische, hinweise, _ = _klassifiziere_xsd_fehler(xsd_fehler)
                    if kritische:
                        for f in kritische:
                            meldungen.append({"typ": "fehler", "text": f"Nach Reparatur: {f}"})
                            fehler_nach_reparatur.append(f)
                    if hinweise:
                        for h in hinweise:
                            meldungen.append({"typ": "warnung", "text": h})
                    if not kritische:
                        meldungen.append(
                            {
                                "typ": "info",
                                "text": f"XSD-Validierung nach Reparatur: {len(hinweise)} Hinweis(e), keine kritischen Fehler.",
                            }
                        )
                else:
                    meldungen.append(
                        {"typ": "info", "text": "XSD-Validierung nach Reparatur: Schema-konform."}
                    )
            else:
                meldungen.append(
                    {
                        "typ": "warnung",
                        "text": f"Kein XSD-Schema für Version {version} — Re-Validierung übersprungen.",
                    }
                )
        except Exception as e:
            meldungen.append({"typ": "warnung", "text": f"Re-Validierung fehlgeschlagen: {e}"})

    # Protokoll ergaenzen (bestehende Meldungen bleiben erhalten)
    status = "ok" if repariert > 0 and not fehler_nach_reparatur else "fehler"
    ergaenze_protokoll("formal", status, meldungen, arbeitsverzeichnis)

    # Neues ZIP packen
    zip_pfad = Path(job.aktuelles_zip)
    neues_zip = zip_pfad.parent / f"{zip_pfad.stem}_repariert.zip"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, packe_zip, arbeitsverzeichnis, neues_zip, False)

    # Altes ZIP aufraeumen
    if zip_pfad != neues_zip:
        try:
            schuetze_original(zip_pfad, job.original_zip)
        except OSError:
            pass
    job.aktuelles_zip = str(neues_zip)

    # Ergebnis in DB aktualisieren
    if ergebnis_formal:
        ergebnis_formal["xml_reparierbar"] = False
        ergebnis_formal["reparierbar"] = False
        ergebnis_formal["reparaturoptionen"] = {"encoding": False, "referenzen": [], "xsd": []}
        ergebnis_formal["xml_repariert"] = repariert > 0
        if not fehler_nach_reparatur:
            ergebnis_formal["status"] = "ok"
            ergebnis_formal["fehler"] = [
                f
                for f in ergebnis_formal.get("fehler", [])
                if "XML beschädigt" not in f and "Referenzierte Datei fehlt" not in f
            ]
        job.ergebnis_formal = json.dumps(ergebnis_formal, ensure_ascii=False)

    db.commit()

    # Arbeitsverzeichnis aufraeumen
    import shutil

    shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)

    sse_status = "wartet" if status == "ok" else "fehler"
    await sende_sse_event(
        job_id,
        "formal",
        sse_status,
        f"XML-Reparatur abgeschlossen: {repariert} Reparatur(en) durchgeführt.",
        100,
    )

    logger.info(
        "[Job %s] XML-Reparatur: %d Reparaturen, %d Fehler nach Reparatur",
        job_id,
        repariert,
        len(fehler_nach_reparatur),
    )
    return {
        "job_id": job_id,
        "repariert": repariert,
        "fehler_nach_reparatur": fehler_nach_reparatur,
        "meldungen": meldungen,
    }


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/neukonvertierung — DOCX→PDF Neukonvertierung bei Abweichung
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/neukonvertierung")
async def neukonvertierung_docx(
    job_id: str, body: DateinameBody, db: Session = Depends(get_db)
) -> dict:
    """Konvertiert ein DOCX neu als PDF (bei Inhaltsabweichung)."""
    job = _hole_job(job_id, db)
    dateiname = body.dateiname

    from utils.zip_handler import entpacke_zip, packe_zip

    data_dir = os.getenv("DATA_DIR", "./data")
    arbeitsverzeichnis = Path(data_dir) / "jobs" / job_id / "arbeit_inhalt"

    # Arbeitsverzeichnis aus ZIP entpacken wenn nicht mehr vorhanden
    if not arbeitsverzeichnis.exists():
        if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
            raise HTTPException(status_code=404, detail="Kein ZIP vorhanden.")
        arbeitsverzeichnis.mkdir(parents=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, entpacke_zip, Path(job.aktuelles_zip), arbeitsverzeichnis)

    # DOCX-Datei finden
    docx_name = dateiname if dateiname.endswith(".docx") else dateiname.rsplit(".", 1)[0] + ".docx"
    docx_dateien = finde_dateien(arbeitsverzeichnis, docx_name)
    if not docx_dateien:
        raise HTTPException(status_code=404, detail=f"DOCX-Datei '{docx_name}' nicht gefunden.")

    docx_pfad = docx_dateien[0]
    soffice = shutil.which("soffice")
    if not soffice:
        raise HTTPException(status_code=500, detail="LibreOffice nicht installiert.")

    try:
        ausgabe_dir = arbeitsverzeichnis / "_neukonvertiert"
        ausgabe_dir.mkdir(exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(ausgabe_dir),
            str(docx_pfad),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await kommuniziere_mit_timeout(proc, 300)

        pdf_name = docx_pfad.stem + ".pdf"
        neues_pdf = ausgabe_dir / pdf_name

        if not neues_pdf.exists():
            raise HTTPException(status_code=500, detail="LibreOffice-Konvertierung fehlgeschlagen.")

        # Altes PDF im Arbeitsverzeichnis ersetzen
        alte_pdfs = finde_dateien(arbeitsverzeichnis, pdf_name)
        alte_pdfs = [
            p
            for p in alte_pdfs
            if p.parent.name not in ("_neukonvertiert", "_konvertiert", "_pdfa")
        ]
        if alte_pdfs:
            import shutil as _shutil

            _shutil.copy2(neues_pdf, alte_pdfs[0])
            logger.info(
                "[Job %s] PDF ersetzt: %s", job_id, pseudonymisiere_dateiname(str(alte_pdfs[0]))
            )
        else:
            # Kein altes PDF → neues PDF ins Hauptverzeichnis kopieren
            import shutil as _shutil

            _shutil.copy2(neues_pdf, arbeitsverzeichnis / pdf_name)

        # ZIP neu packen und DB aktualisieren
        zip_pfad = Path(job.aktuelles_zip)
        neues_zip = zip_pfad.parent / f"{zip_pfad.stem}_neukonv.zip"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, packe_zip, arbeitsverzeichnis, neues_zip, False)
        if zip_pfad != neues_zip:
            try:
                schuetze_original(zip_pfad, job.original_zip)
            except OSError:
                pass
        job.aktuelles_zip = str(neues_zip)

        # Ergebnis in DB aktualisieren: Paar als repariert markieren
        if job.ergebnis_inhalt:
            erg_inhalt = json.loads(job.ergebnis_inhalt)
            for paar in erg_inhalt.get("paare", []):
                if paar.get("name") == dateiname or paar.get("name") == docx_name.replace(
                    ".docx", ""
                ):
                    paar["repariert"] = True
            job.ergebnis_inhalt = json.dumps(erg_inhalt, ensure_ascii=False)
        db.commit()

        import shutil as _s

        _s.rmtree(arbeitsverzeichnis, ignore_errors=True)

        await sende_sse_event(job_id, "inhalt", "laufend", f"{pdf_name} wurde neu konvertiert.", 90)

        # Abschließendes "wartet"-Event: Die Neukonvertierung ist nur eine
        # Reparatur am bereits abgeschlossenen Prüfschritt — ohne dieses Event
        # bliebe der Agent im Frontend dauerhaft auf "läuft" hängen.
        await sende_sse_event(
            job_id,
            "inhalt",
            "wartet",
            "Neukonvertierung abgeschlossen — bitte prüfen und bestätigen.",
            100,
        )

        logger.info(
            "[Job %s] DOCX→PDF Neukonvertierung: %s → %s",
            job_id,
            pseudonymisiere_dateiname(dateiname),
            pseudonymisiere_dateiname(pdf_name),
        )
        return {"job_id": job_id, "konvertiert": True, "dateiname": pdf_name}

    except asyncio.TimeoutError:
        await sende_sse_event(
            job_id, "inhalt", "wartet", "Neukonvertierung abgebrochen (Timeout).", 100
        )
        raise HTTPException(status_code=500, detail="LibreOffice-Timeout.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[Job %s] Neukonvertierung fehlgeschlagen: %s", job_id, e)
        await sende_sse_event(job_id, "inhalt", "wartet", "Neukonvertierung fehlgeschlagen.", 100)
        raise HTTPException(status_code=500, detail="Neukonvertierung fehlgeschlagen.")


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/konvertieren-pdfa
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/konvertieren-pdfa")
async def konvertieren_pdfa(
    job_id: str,
    body: PdfaKonvertierungBody | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Konvertiert alle nicht-PDF/A-Dateien nach PDF/A (Zielformat wählbar)."""
    from utils.pdfa_konverter import konvertiere_nach_pdfa
    from utils.zip_handler import entpacke_zip, packe_zip

    job = _hole_job(job_id, db)
    if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
        raise HTTPException(status_code=404, detail="Kein ZIP vorhanden.")

    # Format-Ergebnis laden
    ergebnis_format = json.loads(job.ergebnis_format) if job.ergebnis_format else {}
    dateien = ergebnis_format.get("dateien", [])
    if not dateien:
        raise HTTPException(status_code=400, detail="Keine Formatprüfungs-Ergebnisse vorhanden.")

    # ZIP entpacken
    zip_pfad = Path(job.aktuelles_zip)
    arbeitsverzeichnis = zip_pfad.parent / "arbeit_konvertierung"
    if arbeitsverzeichnis.exists():
        import shutil

        shutil.rmtree(arbeitsverzeichnis)
    arbeitsverzeichnis.mkdir()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, entpacke_zip, zip_pfad, arbeitsverzeichnis)

    # Zielformat aus Body oder .env (Default: "2b"). Pydantic-Schema lässt nur 1b/2b/3b
    # zu — der Default-Fallback auf .env ist nur für den Fall, dass kein Body kommt.
    zielformat = (body.zielformat if body and body.zielformat else None) or os.getenv(
        "PDFA_ZIELFORMAT", "2b"
    )
    if zielformat not in ("1b", "2b", "3b"):
        raise HTTPException(
            status_code=422, detail=f"Ungültiges Zielformat: '{zielformat}'. Erlaubt: 1b, 2b, 3b."
        )

    meldungen: list[dict] = []
    await sende_sse_event(
        job_id, "format", "laufend", f"PDF/A-Konvertierung nach PDF/A-{zielformat} gestartet…", 5
    )

    ergebnisse = await konvertiere_nach_pdfa(
        arbeitsverzeichnis,
        dateien,
        meldungen,
        sende_sse_event,
        job_id,
        zielformat=zielformat,
    )

    import shutil as _shutil

    pdfa_dir = arbeitsverzeichnis / "_pdfa"
    konv_dir = arbeitsverzeichnis / "_konvertiert"
    nicht_archiv_suffixe = {
        ".docx",
        ".pptx",
        ".xlsx",
        ".xls",
        ".doc",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".jpeg",
        ".jpg",
        ".png",
        ".tiff",
        ".tif",
        ".bmp",
        ".gif",
    }

    # 1. PDF/A-Dateien ins Arbeitsverzeichnis übernehmen (ersetzen Original-PDFs)
    if pdfa_dir.exists():
        for pdfa_datei in pdfa_dir.iterdir():
            if pdfa_datei.is_file():
                for orig in finde_dateien(arbeitsverzeichnis, pdfa_datei.name):
                    if orig.parent != pdfa_dir and orig.parent.name != "_konvertiert":
                        _shutil.copy2(pdfa_datei, orig)
                        break

    # 2. Konvertierte Office/Bild-PDFs ins Arbeitsverzeichnis kopieren
    if konv_dir.exists():
        for konv_datei in konv_dir.iterdir():
            if konv_datei.is_file() and konv_datei.suffix.lower() == ".pdf":
                # PDF/A-Version davon aus pdfa_dir nehmen falls vorhanden
                pdfa_version = pdfa_dir / konv_datei.name if pdfa_dir.exists() else None
                quelle = pdfa_version if pdfa_version and pdfa_version.exists() else konv_datei
                # In Arbeitsverzeichnis kopieren (neben das Original)
                ziel = arbeitsverzeichnis / konv_datei.name
                if not ziel.exists():
                    _shutil.copy2(quelle, ziel)

    # 3. Nicht-archivfähige Originale entfernen (DOCX, PPTX, JPEG etc.)
    entfernt = 0
    for datei in list(arbeitsverzeichnis.rglob("*")):
        if datei.is_file() and datei.suffix.lower() in nicht_archiv_suffixe:
            if datei.parent != konv_dir and datei.parent != pdfa_dir:
                datei.unlink()
                entfernt += 1
    if entfernt > 0:
        meldungen.append(
            {
                "typ": "info",
                "text": f"{entfernt} nicht-archivfähige Originaldatei(en) entfernt (nur PDF/A-Versionen im ZIP).",
            }
        )

    # Temp-Verzeichnisse aufräumen
    if konv_dir.exists():
        _shutil.rmtree(konv_dir)
    if pdfa_dir.exists():
        _shutil.rmtree(pdfa_dir)

    erfolge = sum(1 for e in ergebnisse.values() if e.get("konvertiert"))
    gesamt = len(ergebnisse)

    # Format-Ergebnis in DB aktualisieren (Dateien als konvertiert markieren)
    if job.ergebnis_format:
        erg = json.loads(job.ergebnis_format)
        for d in erg.get("dateien", []):
            dateiname = d["dateiname"]
            # Direkt im Ergebnis?
            if dateiname in ergebnisse and ergebnisse[dateiname].get("konvertiert"):
                re_valide = ergebnisse[dateiname].get("re_validiert", False)
                d["archivfaehig"] = re_valide
                d["pdfa_valid"] = re_valide
                d["details"] = ergebnisse[dateiname].get(
                    "details", f"Nach PDF/A-{zielformat} konvertiert."
                )
                d["empfehlung"] = (
                    "" if re_valide else "Re-Validierung fehlgeschlagen — bitte manuell prüfen."
                )
            # Office-Datei → prüfe ob die PDF-Version konvertiert wurde
            else:
                # Stem-basierte Suche (LibreOffice kann Prefixe aendern)
                stem = dateiname.rsplit(".", 1)[0]
                pdf_match = None
                for erg_name, erg_val in ergebnisse.items():
                    if erg_val.get("konvertiert") and erg_name.endswith(".pdf"):
                        erg_stem = erg_name.rsplit(".", 1)[0]
                        if erg_stem == stem or stem.endswith(erg_stem) or erg_stem.endswith(stem):
                            pdf_match = erg_name
                            break
                if pdf_match:
                    re_valide = ergebnisse[pdf_match].get("re_validiert", False)
                    d["archivfaehig"] = re_valide
                    d["pdfa_valid"] = re_valide
                    d["details"] = ergebnisse[pdf_match].get(
                        "details", f"Nach PDF/A-{zielformat} konvertiert (via PDF)."
                    )
                    d["empfehlung"] = (
                        "" if re_valide else "Re-Validierung fehlgeschlagen — bitte manuell prüfen."
                    )
        # Pendants abgleichen: Wenn ein DOCX oder PDF archivfaehig ist, das Pendant auch markieren
        archiv_stems: dict[str, str] = {}  # stem → details
        for d in erg.get("dateien", []):
            if d.get("archivfaehig"):
                archiv_stems[d["dateiname"].rsplit(".", 1)[0]] = d.get("details", "")
        for d in erg.get("dateien", []):
            if not d.get("archivfaehig"):
                eigener_stem = d["dateiname"].rsplit(".", 1)[0]
                for stem, details in archiv_stems.items():
                    if (
                        eigener_stem == stem
                        or eigener_stem.endswith(stem.rsplit("_", 1)[-1])
                        or stem.endswith(eigener_stem.rsplit("_", 1)[-1])
                    ):
                        suffix = d["dateiname"].rsplit(".", 1)[-1].lower()
                        if suffix == "pdf":
                            d["details"] = "Original-PDF ersetzt durch konvertierte Version."
                        else:
                            d["details"] = details
                        d["archivfaehig"] = True
                        d["pdfa_valid"] = True
                        d["empfehlung"] = ""
                        break

        alle_ok = all(d.get("archivfaehig") for d in erg.get("dateien", []))
        erg["status"] = "ok" if alle_ok else erg.get("status", "fehler")
        job.ergebnis_format = json.dumps(erg, ensure_ascii=False)
        db.commit()

    # Re-Validierungsergebnis korrekt auswerten
    re_val_ok = sum(1 for e in ergebnisse.values() if e.get("re_validiert"))
    re_val_fehler = sum(
        1 for e in ergebnisse.values() if e.get("konvertiert") and not e.get("re_validiert")
    )

    # Wenn durch Pendant-Abgleich alle OK: Warnungen zu fehlgeschlagenen Reparaturen entfernen
    if alle_ok:
        meldungen = [
            m for m in meldungen if "konnten nicht repariert werden" not in m.get("text", "")
        ]

    # Neues Protokoll-XML mit Konvertierungsergebnissen erstellen
    from utils.protokoll import erstelle_protokoll

    meldungen.append(
        {"typ": "info", "text": f"PDF/A-Konvertierung: {erfolge} von {gesamt} erfolgreich."}
    )
    if alle_ok:
        meldungen.append(
            {"typ": "info", "text": "Alle Dateien sind jetzt PDF/A-konform — langzeitarchivfähig."}
        )
    protokoll_status = "ok" if alle_ok else "warnung"
    erstelle_protokoll(job_id, "format", protokoll_status, meldungen, arbeitsverzeichnis)

    # Neues ZIP packen (mit aktualisiertem Protokoll)
    neues_zip = zip_pfad.parent / f"{zip_pfad.stem}_pdfa.zip"
    await loop.run_in_executor(None, packe_zip, arbeitsverzeichnis, neues_zip, False)
    job.aktuelles_zip = str(neues_zip)
    db.commit()

    _shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)

    sse_text = (
        f"PDF/A-Konvertierung: {re_val_ok} von {erfolge} validiert."
        if re_val_fehler > 0
        else f"PDF/A-Konvertierung: {erfolge}/{gesamt} erfolgreich."
    )
    await sende_sse_event(job_id, "format", "wartet", sse_text, 100)

    logger.info(
        "[Job %s] PDF/A-Konvertierung: %d/%d konvertiert, %d re-validiert, %d fehlgeschlagen.",
        job_id,
        erfolge,
        gesamt,
        re_val_ok,
        re_val_fehler,
    )
    return {"erfolge": erfolge, "gesamt": gesamt, "ergebnisse": ergebnisse}
