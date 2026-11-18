"""Job-Router: Alle job-bezogenen API-Endpoints des Aussonderungstools."""

import asyncio
import json
import logging
import os
import shutil
import uuid
import zipfile
from pathlib import Path

from config import DATA_DIR, MAX_UPLOAD_MB
from dependencies import AgentName, _audit_log, _hole_job, _parse_json
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from models import Bewertung, Job, JobStatus, engine, get_db
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from utils.agent_runner import hole_sse_queue, markiere_sse_beendet
from utils.logging_setup import pseudonymisiere_dateiname
from utils.xdomea_parser import safe_xml_parser
from utils.zeit import jetzt_utc

# Obergrenze für Protokoll-XML, das aus einem Job-ZIP in den Speicher gelesen wird.
_MAX_PROTOKOLL_BYTES = 20 * 1024 * 1024

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request-Schemas — eingehende JSON-Bodies werden validiert
# ---------------------------------------------------------------------------


class AnmerkungBody(BaseModel):
    """Archivar-Anmerkung zum Protokoll eines Agenten."""

    anmerkung: str = Field(..., min_length=1, description="Anmerkungstext (Pflicht, nicht leer)")


class ChatBody(BaseModel):
    """Nachricht im Chat zu einem Prüfschritt."""

    nachricht: str = Field(..., min_length=1, description="Frage an das Sprachmodell (Pflicht)")


class ErschliessungBody(BaseModel):
    """Archivar-Bearbeitung der Erschließungsdaten (alle Felder optional — Merge-Semantik)."""

    # Welche Einheit bearbeitet wird (Erschließung pro Ebene). Fehlt sie, wird
    # die erste/einzige Einheit angenommen (Abwärtskompatibilität).
    einheit_id: str | None = None
    titel: str | None = None
    inhaltsangabe: str | None = None
    schlagworte: list[str] | None = None
    personen: list[str] | None = None
    zeitraum: str | None = None
    aktenzeichen: str | None = None
    bezuege: str | None = None

    model_config = {"extra": "ignore"}


class EntscheidungBody(BaseModel):
    """Archivar-Endentscheidung über das gesamte Paket (Human-in-the-Loop)."""

    bewertung: Bewertung
    begruendung: str = ""
    kommentar: str = ""
    dok_entscheidungen: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# GET /api/jobs – Alle Jobs auflisten (Historie)
# ---------------------------------------------------------------------------


@router.get("/api/jobs")
def jobs_auflisten(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    suche: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """Gibt Jobs paginiert zurück (neueste zuerst). Optionaler Suchfilter auf Dateiname."""
    from sqlalchemy import desc

    query = db.query(Job)
    if suche:
        query = query.filter(Job.dateiname.ilike(f"%{suche}%"))
    gesamt = query.count()
    jobs = query.order_by(desc(Job.erstellt_am)).offset(offset).limit(limit).all()
    return {
        "gesamt": gesamt,
        "offset": offset,
        "limit": limit,
        "jobs": [
            {
                "id": job.id,
                "dateiname": job.dateiname,
                "erstellt_am": job.erstellt_am.isoformat() if job.erstellt_am else None,
                "status": job.status.value if job.status else None,
                "archivar_bewertung": job.archivar_bewertung.value
                if job.archivar_bewertung
                else None,
                "entscheidung_am": job.entscheidung_am.isoformat() if job.entscheidung_am else None,
            }
            for job in jobs
        ],
    }


# ---------------------------------------------------------------------------
# POST /api/upload
# ---------------------------------------------------------------------------


@router.post("/api/upload")
async def upload(datei: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    """Lädt eine XDOMEA-ZIP-Datei hoch und erstellt einen neuen Job."""
    job_id = str(uuid.uuid4())
    job_verzeichnis = DATA_DIR / "jobs" / job_id
    job_verzeichnis.mkdir(parents=True)

    # Dateinamen aus dem Upload säubern: nur Basename, Whitelist-Zeichen.
    # Verhindert Path-Traversal beim Schreiben und Header-Injection im Download.
    roh_name = Path(datei.filename or "upload.zip").name
    sicherer_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in roh_name).strip()
    if not sicherer_name or sicherer_name in (".", ".."):
        sicherer_name = "upload.zip"

    zip_pfad = job_verzeichnis / sicherer_name

    # Streaming-Upload: Datei in Chunks auf Disk schreiben statt alles in RAM
    # Das Größenlimit wird während des Schreibens geprüft — sonst ließe sich
    # die Platte mit einem einzigen übergroßen Upload füllen.
    groesse = 0
    chunk_size = 1024 * 1024  # 1 MB Chunks
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    zu_gross = False
    with open(zip_pfad, "wb") as f:
        while True:
            chunk = await datei.read(chunk_size)
            if not chunk:
                break
            groesse += len(chunk)
            if groesse > max_bytes:
                zu_gross = True
                break
            f.write(chunk)

    if zu_gross:
        shutil.rmtree(job_verzeichnis, ignore_errors=True)
        raise HTTPException(
            status_code=413,
            detail=f"Datei zu groß (Maximum: {MAX_UPLOAD_MB} MB).",
        )
    groesse_mb = groesse / (1024 * 1024)

    # ZIP-Sicherheitsprüfungen
    MAX_ZIP_EINTRAEGE = 10_000
    MAX_ZIP_UNKOMPRIMIERT = 5 * 1024 * 1024 * 1024  # 5 GB

    try:
        if not zipfile.is_zipfile(zip_pfad):
            raise ValueError("Die hochgeladene Datei ist kein gültiges ZIP-Archiv.")

        with zipfile.ZipFile(zip_pfad, "r") as zf:
            eintraege = zf.infolist()

            if len(eintraege) > MAX_ZIP_EINTRAEGE:
                raise ValueError(
                    f"ZIP enthält zu viele Einträge: {len(eintraege)} (Maximum: {MAX_ZIP_EINTRAEGE})."
                )

            gesamt_unkomprimiert = sum(info.file_size for info in eintraege)
            if gesamt_unkomprimiert > MAX_ZIP_UNKOMPRIMIERT:
                raise ValueError(
                    f"Unkomprimierte Gesamtgröße zu groß: "
                    f"{gesamt_unkomprimiert / (1024**3):.1f} GB (Maximum: 5 GB)."
                )

            for info in eintraege:
                if ".." in info.filename or info.filename.startswith("/"):
                    raise ValueError(f"Path-Traversal erkannt im ZIP-Eintrag: '{info.filename}'.")

    except ValueError as e:
        zip_pfad.unlink(missing_ok=True)
        shutil.rmtree(job_verzeichnis, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))
    except zipfile.BadZipFile:
        zip_pfad.unlink(missing_ok=True)
        shutil.rmtree(job_verzeichnis, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail="Die hochgeladene Datei ist beschädigt und kein gültiges ZIP-Archiv.",
        )

    # original_zip zeigt dauerhaft auf das hochgeladene Paket und wird nie
    # überschrieben — nur damit lässt sich der Vorgang später zurücksetzen.
    job = Job(
        id=job_id,
        dateiname=sicherer_name,
        aktuelles_zip=str(zip_pfad),
        original_zip=str(zip_pfad),
    )
    db.add(job)
    db.commit()

    logger.info(
        "Job erstellt: %s (Datei: %s, %.1f MB)",
        job_id,
        pseudonymisiere_dateiname(sicherer_name),
        groesse_mb,
    )
    return {"job_id": job_id}


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/status  – SSE
# ---------------------------------------------------------------------------


@router.get("/api/job/{job_id}/status")
async def job_status(job_id: str, db: Session = Depends(get_db)):
    """SSE-Stream: Liefert Live-Fortschritt des Jobs.

    Wichtig: Der Initial-Snapshot wird *vor* dem StreamingResponse aus der
    per Depends injizierten Session gelesen — diese Session wird beim
    Endpoint-Return geschlossen. Der nachfolgende Generator läuft potenziell
    Minuten/Stunden weiter und darf die Session deshalb nicht halten.
    Für eventuelle Re-Reads im Generator wird eine eigene kurze Session
    geöffnet und sofort wieder geschlossen.
    """
    _hole_job(job_id, db)

    # Initial-Snapshot sofort lesen — danach ist die Depends-Session entbehrlich.
    job = db.get(Job, job_id)
    agenten = ("formal", "format", "inhalt", "bewertung", "erschliessung")
    initial_events: list[dict] = []
    for agent in agenten:
        status = getattr(job, f"status_{agent}")
        initial_events.append(
            {
                "agent": agent,
                "status": status.value if hasattr(status, "value") else str(status),
                "message": "",
                "progress": 0,
            }
        )

    queue = hole_sse_queue(job_id)

    async def ereignis_generator():
        try:
            for event in initial_events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                    # Primärer Abschluss-Pfad: agent_runner pusht ein
                    # 'system/abgeschlossen'-Event, wenn alle Agenten bestätigt
                    # sind — dann ist hier Schluss.
                    if event.get("agent") == "system" and event.get("status") == "abgeschlossen":
                        break

                    # Sicherheits-Pfad: bei JobStatus.fehler wird kein globales
                    # System-Event gepusht, nur das Agent-Event. Wir prüfen
                    # deshalb den DB-Status mit einer EIGENEN kurzen Session
                    # (die Endpoint-Session lebt nicht so lange).
                    with Session(engine) as kurz_db:
                        job_aktuell = kurz_db.get(Job, job_id)
                        if job_aktuell and job_aktuell.status in (
                            JobStatus.abgeschlossen,
                            JobStatus.fehler,
                        ):
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "agent": "system",
                                        "status": "abgeschlossen",
                                        "message": "Alle Agenten abgeschlossen.",
                                        "progress": 100,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                            break
                except asyncio.TimeoutError:
                    # Heartbeat senden, damit die Verbindung offen bleibt
                    yield ": heartbeat\n\n"
        finally:
            # Queue als beendet markieren — verhindert, dass ein noch
            # laufender Agent eine neue Queue lazy anlegt, die niemand mehr liest.
            markiere_sse_beendet(job_id)
            logger.debug("SSE-Queue für Job %s als beendet markiert.", job_id)

    return StreamingResponse(ereignis_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/ergebnis
# ---------------------------------------------------------------------------


@router.get("/api/job/{job_id}/ergebnis")
def job_ergebnis(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Gibt alle Agentenergebnisse eines Jobs als JSON zurück."""
    job = _hole_job(job_id, db)
    return {
        "job_id": job_id,
        "status": job.status,
        "ergebnisse": {
            "formal": _parse_json(job.ergebnis_formal),
            "format": _parse_json(job.ergebnis_format),
            "inhalt": _parse_json(job.ergebnis_inhalt),
            "bewertung": _parse_json(job.ergebnis_bewertung),
            "erschliessung": _parse_json(job.ergebnis_erschliessung),
        },
        "anmerkungen": {
            "formal": job.anmerkung_formal or "",
            "format": job.anmerkung_format or "",
            "inhalt": job.anmerkung_inhalt or "",
            "bewertung": job.anmerkung_bewertung or "",
            "erschliessung": job.anmerkung_erschliessung or "",
        },
        "archivar_entscheidung": {
            "bewertung": job.archivar_bewertung,
            "begruendung": job.archivar_begruendung,
            "kommentar": job.archivar_kommentar,
            "entschieden_am": job.entscheidung_am,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/struktur — XDOMEA-Paketstruktur des aktuellen ZIP
# ---------------------------------------------------------------------------


@router.get("/api/job/{job_id}/struktur")
def job_struktur(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Liefert die Paketstruktur des aktuellen ZIP (Akten/Vorgänge/Dokumente).

    Öffnet das aktuelle ZIP des Jobs, parst — falls vorhanden — die XDOMEA-XML
    und gruppiert nach Akten/Vorgängen mit ihren Dokumenten. Jedes referenzierte
    Dokument wird gegen die tatsächlich im ZIP enthaltenen Dateien abgeglichen,
    um fehlende Dateien zu markieren.
    """
    import zipfile

    job = _hole_job(job_id, db)
    if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
        raise HTTPException(status_code=404, detail="Aktuelles ZIP des Jobs nicht gefunden.")

    zip_pfad = Path(job.aktuelles_zip)

    # 1. Vorhandene Dateien im ZIP einlesen (nur Basenamen für den Abgleich)
    try:
        with zipfile.ZipFile(zip_pfad, "r") as zf:
            zip_eintraege = [info.filename for info in zf.infolist() if not info.is_dir()]
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Das aktuelle ZIP-Archiv ist beschädigt.")
    except Exception as e:
        logger.warning("[Job %s] ZIP konnte nicht geöffnet werden: %s", job_id, e)
        raise HTTPException(status_code=500, detail="Das ZIP-Archiv konnte nicht geöffnet werden.")

    vorhandene_dateien = {Path(name).name for name in zip_eintraege}

    # 2. ZIP in temporäres Verzeichnis entpacken, um die XDOMEA-XML zu finden
    arbeitsverzeichnis = DATA_DIR / "jobs" / job_id / "arbeit_struktur"
    akten: list[dict] = []
    meta: dict = {}
    xdomea_version = ""

    try:
        if arbeitsverzeichnis.exists():
            shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)
        arbeitsverzeichnis.mkdir(parents=True)

        from utils.paket_anzeige import struktur_zu_anzeige
        from utils.xdomea_parser import (
            analysiere_paket_struktur,
            finde_xdomea_xmls,
            lese_xdomea_metadaten,
            lese_xdomea_version,
            verfuegbare_ebenen,
        )
        from utils.zip_handler import entpacke_container_archive, entpacke_zip

        try:
            entpacke_zip(zip_pfad, arbeitsverzeichnis)
        except Exception as e:
            logger.warning("[Job %s] ZIP konnte nicht entpackt werden: %s", job_id, e)
            raise HTTPException(
                status_code=500, detail="Das ZIP-Archiv konnte nicht entpackt werden."
            )

        # Eingebettete Container auspacken (Lieferverzeichnis-Bundles, nscale-
        # ".xdomea"-Container, Sammel-ZIPs). Sonst bliebe die Struktur leer,
        # obwohl die Agenten die Inhalte später sehr wohl verarbeiten.
        n_container = entpacke_container_archive(arbeitsverzeichnis)
        if n_container:
            logger.info("[Job %s] %d eingebettete Container ausgepackt.", job_id, n_container)
            # Die Dateien der Container stehen nicht in der ZIP-Eintragsliste —
            # der Vorhanden-Abgleich muss sie sonst fälschlich als fehlend melden.
            vorhandene_dateien.update(p.name for p in arbeitsverzeichnis.rglob("*") if p.is_file())

        # XDOMEA-XML(s) suchen — ein Paket kann mehrere XMLs enthalten (enaio
        # legt pro Akte eine XML an, plus eine PDF/A-Rendition desselben Inhalts).
        xml_kandidaten = finde_xdomea_xmls(arbeitsverzeichnis)

        if not xml_kandidaten:
            # Keine XDOMEA-XML — flache Dateiliste als Fallback zurückgeben
            logger.info("[Job %s] Keine XDOMEA-XML im ZIP — flache Dateiliste.", job_id)
            return {
                "job_id": job_id,
                "xdomea": False,
                "meta": {},
                "akten": [],
                "dateien": sorted(
                    {
                        Path(name).name
                        for name in zip_eintraege
                        if not Path(name).name.lower().endswith(".xml")
                    }
                ),
                "statistik": {
                    "akten": 0,
                    "dokumente": 0,
                    "fehlende_dateien": 0,
                },
            }

        # Größte XML-Datei für Metadaten/Version verwenden (meist die
        # Haupt-Aussonderungsdatei). Die Struktur wird dagegen über ALLE XMLs
        # konsolidiert (siehe analysiere_paket_struktur weiter unten).
        xml_pfad = max(xml_kandidaten, key=lambda p: p.stat().st_size)

        # Version + Metadaten lesen (fehlertolerant)
        try:
            xdomea_version = lese_xdomea_version(xml_pfad)
        except Exception:
            xdomea_version = ""

        meta_roh = lese_xdomea_metadaten(xml_pfad) or {}
        meta = {
            "namespace": "",
            "version": xdomea_version,
            "prozess_id": meta_roh.get("prozess_id", ""),
            "erstellt": meta_roh.get("erstellungszeitpunkt", ""),
            "absender": ", ".join(meta_roh.get("absender", []) or []),
            "empfaenger": ", ".join(meta_roh.get("empfaenger", []) or []),
            "aussonderungsart": meta_roh.get("aussonderungsart", ""),
        }

        # Volle Hierarchie über ALLE XMLs konsolidieren: Akten → (Teilakten →)
        # Vorgänge → Dokumente. Renditions-Dubletten (enaio Originalformat/PDFA)
        # werden dabei entfernt. Bei reinen Vorgangs-Paketen (XDOMEA 2.x ohne
        # <Akte>) wird die Akten-Ebene weggelassen.
        struktur = analysiere_paket_struktur(xml_kandidaten)

        ebene = struktur.get("ebene", "leer")
        akten, dateien_gesamt, fehlend = struktur_zu_anzeige(struktur, vorhandene_dateien)
        stat = struktur.get("statistik", {})

        ebenen_info = verfuegbare_ebenen(xml_kandidaten)

        return {
            "job_id": job_id,
            "xdomea": True,
            "ebene": ebene,
            # Bestandsjobs haben kein aufbewahrtes Original — dort ist ein
            # Zurücksetzen nicht möglich, die Oberfläche blendet es aus.
            "zuruecksetzbar": bool(job.original_zip and Path(job.original_zip).exists()),
            "meta": meta,
            "akten": akten,
            "renditions_entfernt": struktur.get("renditions_entfernt", 0),
            "bewertungsebenen": ebenen_info.get("ebenen", []),
            "bewertungsebene_default": ebenen_info.get("default"),
            "statistik": {
                "akten": stat.get("akten", len(akten)),
                "teilakten": stat.get("teilakten", 0),
                "vorgaenge": stat.get("vorgaenge", 0),
                "dokumente": stat.get("dokumente", 0),
                "dateien": dateien_gesamt,
                "fehlende_dateien": fehlend,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[Job %s] Struktur konnte nicht ermittelt werden: %s", job_id, e)
        raise HTTPException(
            status_code=500,
            detail="Die Paketstruktur konnte nicht ermittelt werden.",
        )
    finally:
        if arbeitsverzeichnis.exists():
            shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/datei — einzelne Datei aus dem ZIP (Inline-Vorschau)
# ---------------------------------------------------------------------------

# Nur skriptsichere Formate dürfen inline im Browser gerendert werden.
# Aktiv interpretierbare Dateien (HTML, SVG, XML …) aus hochgeladenen Paketen
# würden sonst als Skript in der App-Origin laufen (Stored XSS) — sie werden
# als Download (attachment, application/octet-stream) ausgeliefert.
_INLINE_MEDIENTYPEN: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


# Signaturen der inline-fähigen Formate. Nur wenn der Inhalt die Endung
# bestätigt, wird eine Datei mit ihrem Medientyp inline ausgeliefert.
_INLINE_SIGNATUREN: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
    ".webp": (b"RIFF",),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
}


def _sichere_auslieferung(dateiname: str, daten: bytes | None = None) -> tuple[str, str]:
    """Bestimmt Medientyp und Content-Disposition für eine Paket-Datei.

    Skriptsichere Formate (PDF, Rasterbilder) werden inline mit ihrem echten
    Medientyp ausgeliefert. Alle übrigen Formate — insbesondere HTML, SVG, XML —
    werden als Download (`application/octet-stream`, `attachment`) ausgeliefert,
    damit hochgeladene Dateien keinen aktiven Inhalt in der App-Origin ausführen.

    Sind die Daten bekannt, muss der Inhalt die Endung zusätzlich bestätigen.
    Sonst würde die Vorschau ein Format behaupten, das der Viewer nicht rendern
    kann — z.B. bei einer XAIP-XML, die als ".pdf" ausgeliefert wird.
    """
    endung = Path(dateiname).suffix.lower()
    medientyp = _INLINE_MEDIENTYPEN.get(endung)
    if not medientyp:
        return "application/octet-stream", "attachment"

    signaturen = _INLINE_SIGNATUREN.get(endung)
    if daten is not None and signaturen and not daten.startswith(signaturen):
        return "application/octet-stream", "attachment"

    return medientyp, "inline"


@router.get("/api/job/{job_id}/datei")
def job_datei(job_id: str, name: str, db: Session = Depends(get_db)):
    """Liefert eine einzelne Datei aus dem aktuellen ZIP des Jobs zur Vorschau.

    Es wird ausschließlich nach dem Basisnamen gegen die tatsächlichen
    ZIP-Einträge gesucht — aus der Eingabe wird kein Pfad konstruiert. Auch
    eingebettete Container werden durchsucht: bei nscale eGOV und elKA liegen
    die Schriftstücke nicht direkt im hochgeladenen ZIP.
    """
    import zipfile

    from utils.zip_handler import finde_datei_in_paket

    job = _hole_job(job_id, db)
    if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
        raise HTTPException(status_code=404, detail="Aktuelles ZIP des Jobs nicht gefunden.")

    gesucht = Path(name or "").name
    if not gesucht:
        raise HTTPException(status_code=400, detail="Kein Dateiname angegeben.")

    try:
        daten = finde_datei_in_paket(Path(job.aktuelles_zip), gesucht)
    except ValueError:
        raise HTTPException(status_code=413, detail="Datei zu groß für die Vorschau.")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Das ZIP-Archiv ist beschädigt.")

    if daten is None:
        raise HTTPException(status_code=404, detail=f"Datei '{gesucht}' nicht im Paket gefunden.")

    medientyp, disposition = _sichere_auslieferung(gesucht, daten)
    return Response(
        content=daten,
        media_type=medientyp,
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/protokoll/{agent}
# ---------------------------------------------------------------------------


@router.get("/api/job/{job_id}/protokoll/{agent}")
def agent_protokoll(job_id: str, agent: AgentName, db: Session = Depends(get_db)) -> dict:
    """Gibt das Protokoll-XML eines Agenten als JSON zurück."""
    import zipfile

    from lxml import etree

    job = _hole_job(job_id, db)
    dateiname = f"protokoll_{agent}.xml"

    # 1. In allen ZIPs des Jobs suchen (neueste zuerst)
    job_verzeichnis = DATA_DIR / "jobs" / job_id
    zip_dateien = sorted(
        job_verzeichnis.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
    )

    for zip_pfad in zip_dateien:
        try:
            with zipfile.ZipFile(zip_pfad, "r") as zf:
                if dateiname in zf.namelist():
                    # Protokolle sind klein — übergroße Einträge nicht in den RAM laden.
                    if zf.getinfo(dateiname).file_size > _MAX_PROTOKOLL_BYTES:
                        continue
                    xml_bytes = zf.read(dateiname)
                    wurzel = etree.fromstring(xml_bytes, safe_xml_parser())
                    meldungen = []
                    for meldung in wurzel.findall("Meldung"):
                        meldungen.append(
                            {
                                "typ": meldung.get("typ", "info"),
                                "text": meldung.text or "",
                            }
                        )
                    return {
                        "agent": agent,
                        "status": wurzel.get("status", "unbekannt"),
                        "erstellt": wurzel.get("erstellt", ""),
                        "meldungen": meldungen,
                    }
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Kein Protokoll für Agent '{agent}' gefunden.")


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/protokoll/{agent}/anmerkung — Anmerkung zum Protokoll
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/protokoll/{agent}/anmerkung")
def protokoll_anmerkung_speichern(
    job_id: str,
    agent: AgentName,
    body: AnmerkungBody,
    db: Session = Depends(get_db),
) -> dict:
    """Speichert eine Archivar-Anmerkung zum Protokoll eines Agenten."""
    job = _hole_job(job_id, db)
    anmerkung = body.anmerkung.strip()
    if not anmerkung:
        raise HTTPException(status_code=422, detail="Anmerkung darf nicht leer sein.")
    setattr(job, f"anmerkung_{agent}", anmerkung)
    _audit_log(job, f"Protokoll-Anmerkung ({agent})", anmerkung[:100])
    db.commit()
    logger.info("[Job %s] Protokoll-Anmerkung für '%s' gespeichert", job_id, agent)
    return {"job_id": job_id, "agent": agent, "gespeichert": True}


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/chat
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/chat/{agent}")
async def job_chat(
    job_id: str, agent: AgentName, body: ChatBody, db: Session = Depends(get_db)
) -> dict:
    """Chat mit dem Sprachmodell über einen bestimmten Prüfschritt."""
    from utils.openai_client import PROMPT_CHAT_BEWERTUNG_SYSTEM, llm_chat

    job = _hole_job(job_id, db)
    nachricht = body.nachricht.strip()
    if not nachricht:
        raise HTTPException(status_code=422, detail="Nachricht darf nicht leer sein.")

    # Kontext pro Agent aufbauen
    kontext_teile = []
    ergebnis_json = getattr(job, f"ergebnis_{agent}")
    if ergebnis_json:
        kontext_teile.append(f"ERGEBNIS DES PRÜFSCHRITTS '{agent}':\n{ergebnis_json}")

    # Dokumenttexte aus Cache laden (für Bewertung/Erschließung/Inhalt)
    if agent in ("bewertung", "erschliessung", "inhalt"):
        job_verzeichnis = DATA_DIR / "jobs" / job_id
        cache_pfad = job_verzeichnis / "texte_cache.json"
        if cache_pfad.exists():
            try:
                import json as _json

                from utils.text_extraktion import kuerze_intelligent

                cache = _json.loads(cache_pfad.read_text(encoding="utf-8"))
                text = cache.get("text", "")
                if text:
                    if len(text) > 300_000:
                        text = kuerze_intelligent(text, 300_000)
                    kontext_teile.append(f"DOKUMENTTEXTE:\n{text}")
            except Exception as e:
                logger.warning("[Job %s] Texte-Cache nicht lesbar: %s", job_id, e)

    kontext = "\n\n".join(kontext_teile)

    # Chat-Verlauf pro Agent laden
    chat_feld = f"chat_{agent}"
    verlauf: list[dict] = []
    chat_json = getattr(job, chat_feld, None)
    if chat_json:
        try:
            verlauf = json.loads(chat_json)
        except json.JSONDecodeError:
            verlauf = []

    # Messages zusammenbauen (max. 20 letzte Nachrichten um Kontextlimit zu vermeiden)
    MAX_CHAT_NACHRICHTEN = 20
    if len(verlauf) > MAX_CHAT_NACHRICHTEN:
        verlauf = verlauf[-MAX_CHAT_NACHRICHTEN:]

    agent_namen = {
        "formal": "Formale Prüfung",
        "format": "Formatprüfung",
        "inhalt": "Inhaltsvergleich",
        "bewertung": "Archivbewertung",
        "erschliessung": "Erschließung",
    }
    messages = [
        {"role": "system", "content": PROMPT_CHAT_BEWERTUNG_SYSTEM},
        {
            "role": "user",
            "content": f"Der Archivar hat Rückfragen zum Prüfschritt '{agent_namen.get(agent, agent)}'.\n\nKontext:\n{kontext}",
        },
        {
            "role": "assistant",
            "content": f"Ich habe die Ergebnisse des Prüfschritts '{agent_namen.get(agent, agent)}' analysiert. Welche Fragen haben Sie?",
        },
    ]
    messages.extend(verlauf)
    messages.append({"role": "user", "content": nachricht})

    try:
        antwort = await llm_chat(messages, job_id, f"Chat-{agent}")
    except RuntimeError as e:
        logger.warning("[Job %s] Chat-Anfrage fehlgeschlagen: %s", job_id, e)
        raise HTTPException(
            status_code=500, detail="Die Chat-Anfrage konnte nicht verarbeitet werden."
        )

    # Verlauf pro Agent speichern
    verlauf.append({"role": "user", "content": nachricht})
    verlauf.append({"role": "assistant", "content": antwort})
    setattr(job, chat_feld, json.dumps(verlauf, ensure_ascii=False))
    db.commit()

    logger.info("[Job %s] Chat für Agent '%s' beantwortet.", job_id, agent)
    return {"antwort": antwort}


# Abwärtskompatibilität: alter Chat-Endpoint leitet auf Bewertung weiter
@router.post("/api/job/{job_id}/chat")
async def job_chat_legacy(job_id: str, body: ChatBody, db: Session = Depends(get_db)) -> dict:
    """Legacy-Endpoint: Leitet auf Bewertungs-Chat weiter."""
    return await job_chat(job_id, "bewertung", body, db)


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/erschliessung — Archivar-Edits speichern + XDOMEA-Validierung
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/erschliessung")
def erschliessung_speichern(
    job_id: str,
    body: ErschliessungBody,
    db: Session = Depends(get_db),
) -> dict:
    """Speichert geänderte Erschließungsdaten pro Einheit, schreibt ins XDOMEA-XML und re-validiert."""
    from agents.agent_formal import XSD_DATEIEN, _klassifiziere_xsd_fehler
    from utils.job_config import lese_bewertungsebene
    from utils.protokoll import erstelle_protokoll
    from utils.xdomea_parser import (
        finde_xdomea_xmls,
        lese_einheiten_mit_element,
        lese_xdomea_version,
        schreibe_erschliessung_in_element,
        validiere_gegen_xsd,
    )

    job = _hole_job(job_id, db)

    # 1. Gespeichertes Erschließungsergebnis laden (neues Modell: einheiten[]).
    gespeichert_obj = json.loads(job.ergebnis_erschliessung) if job.ergebnis_erschliessung else {}
    einheiten = gespeichert_obj.get("einheiten") or []

    body_data = body.model_dump(exclude_unset=True)
    einheit_id = body_data.pop("einheit_id", None)

    # Zieleinheit bestimmen (über einheit_id, sonst erste; sonst flaches Objekt).
    ziel = None
    if einheit_id:
        ziel = next((u for u in einheiten if u.get("einheit_id") == einheit_id), None)
    if ziel is None and einheiten:
        ziel = einheiten[0]
    if ziel is None:
        ziel = gespeichert_obj  # alter Stand ohne Einheiten

    for feld, wert in body_data.items():
        ist_leer = (not wert) if not isinstance(wert, list) else len(wert) == 0
        if not ist_leer or feld not in ziel:
            ziel[feld] = wert
    ziel["archivar_bearbeitet"] = True
    job.ergebnis_erschliessung = json.dumps(gespeichert_obj, ensure_ascii=False)
    _audit_log(
        job, "Erschließungsdaten bearbeitet", f"Einheit: {ziel.get('betreff') or einheit_id or '-'}"
    )
    db.commit()

    # 2. In das XDOMEA-Element der Zieleinheit schreiben + re-validieren.
    arbeitsverzeichnis = DATA_DIR / "jobs" / job_id / "arbeit_erschliessung"
    validierung_warnung: str | None = None

    if arbeitsverzeichnis.exists():
        ebene = gespeichert_obj.get("ebene") or lese_bewertungsebene(DATA_DIR / "jobs" / job_id)
        eintraege = lese_einheiten_mit_element(finde_xdomea_xmls(arbeitsverzeichnis), ebene)
        treffer = None
        if einheit_id:
            treffer = next(
                (e for e in eintraege if e["einheit"].get("vorgang_id") == einheit_id), None
            )
        if treffer is None and eintraege:
            treffer = eintraege[0]

        if treffer is not None:
            try:
                schreibe_erschliessung_in_element(treffer["element"], ziel)
                treffer["baum"].write(
                    str(treffer["xml"]), pretty_print=True, xml_declaration=True, encoding="UTF-8"
                )
                try:
                    version = lese_xdomea_version(treffer["xml"])
                    xsd_pfad = XSD_DATEIEN.get(version)
                    if xsd_pfad and xsd_pfad.exists():
                        xsd_fehler = validiere_gegen_xsd(treffer["xml"], xsd_pfad)
                        if xsd_fehler:
                            kritische, _, _ = _klassifiziere_xsd_fehler(xsd_fehler)
                            if kritische:
                                validierung_warnung = (
                                    f"XDOMEA-Validierung nach Aenderung: {len(kritische)} "
                                    "kritische Fehler."
                                )
                except Exception as e:
                    logger.warning("[Job %s] Re-Validierung fehlgeschlagen: %s", job_id, e)
            except Exception as e:
                logger.warning("[Job %s] Erschliessung schreiben fehlgeschlagen: %s", job_id, e)

        meldungen = [
            {"typ": "info", "text": "Erschliessungsdaten vom Archivar aktualisiert."},
            {"typ": "info", "text": f"Einheit: {ziel.get('betreff') or '-'}"},
        ]
        if validierung_warnung:
            meldungen.append({"typ": "warnung", "text": validierung_warnung})
        erstelle_protokoll(job_id, "erschliessung", "ok", meldungen, arbeitsverzeichnis)

    logger.info("[Job %s] Erschliessungsdaten gespeichert", job_id)
    return {
        "job_id": job_id,
        "gespeichert": True,
        "validierung_warnung": validierung_warnung,
    }


# ---------------------------------------------------------------------------
# POST /api/job/{job_id}/entscheidung
# ---------------------------------------------------------------------------


@router.post("/api/job/{job_id}/entscheidung")
def entscheidung_speichern(
    job_id: str,
    body: EntscheidungBody,
    db: Session = Depends(get_db),
) -> dict:
    """Speichert die Archivar-Entscheidung (Human in the Loop).

    Pydantic validiert das Schema vor dem Handler: ungültige Bewertungs-Werte
    geben jetzt einen 422 mit präzisem Feldfehler statt der manuell formulierten
    'Erlaubt: [...]'-Meldung von vorher.
    """
    job = _hole_job(job_id, db)

    bewertung = body.bewertung.value  # Bewertung-Enum → String für DB-Spalte
    begruendung = body.begruendung
    kommentar = body.kommentar
    dok_entscheidungen = body.dok_entscheidungen

    job.archivar_bewertung = bewertung
    job.archivar_begruendung = begruendung
    job.archivar_kommentar = kommentar
    job.entscheidung_am = jetzt_utc()

    # Einzeldokument-Entscheidungen ins Bewertungsergebnis schreiben
    if dok_entscheidungen and job.ergebnis_bewertung:
        bew_ergebnis = json.loads(job.ergebnis_bewertung)
        dokumente = bew_ergebnis.get("dokumente", [])
        for idx_str, dok_bewertung in dok_entscheidungen.items():
            try:
                idx = int(idx_str)
                if 0 <= idx < len(dokumente):
                    dokumente[idx]["archivar_bewertung"] = dok_bewertung
            except (ValueError, IndexError):
                pass
        bew_ergebnis["dokumente"] = dokumente
        job.ergebnis_bewertung = json.dumps(bew_ergebnis, ensure_ascii=False)

    _audit_log(job, "Archivar-Entscheidung", f"{bewertung}: {begruendung[:100]}")
    db.commit()

    # Archivar-Entscheidung ins Protokoll schreiben
    try:
        _schreibe_entscheidung_ins_protokoll(job, bewertung, begruendung, kommentar)
    except Exception as e:
        logger.warning("[Job %s] Protokoll-Update fehlgeschlagen: %s", job_id, e)

    logger.info("[Job %s] Archivar-Entscheidung gespeichert: %s", job_id, bewertung)
    return {"job_id": job_id, "bewertung": bewertung}


def _schreibe_entscheidung_ins_protokoll(
    job, bewertung: str, begruendung: str, kommentar: str
) -> None:
    """Schreibt die Archivar-Entscheidung ins Bewertungs-Protokoll-XML."""
    from lxml import etree

    if not job.aktuelles_zip:
        return

    # Aktuelles Arbeitsverzeichnis ermitteln
    data_dir = os.getenv("DATA_DIR", "./data")
    arbeitsverzeichnis = Path(data_dir) / "jobs" / job.id / "arbeit_bewertung"
    protokoll_pfad = arbeitsverzeichnis / "protokoll_bewertung.xml"

    if not protokoll_pfad.exists():
        logger.warning("[Job %s] Bewertungs-Protokoll nicht gefunden: %s", job.id, protokoll_pfad)
        return

    try:
        from utils.xdomea_parser import sicher_parsen

        baum = sicher_parsen(protokoll_pfad)
        wurzel = baum.getroot()

        # Archivar-Entscheidung als neuen Block einfügen
        entscheidung = etree.SubElement(wurzel, "ArchivarEntscheidung")
        entscheidung.set("zeitpunkt", jetzt_utc().isoformat())

        el_bewertung = etree.SubElement(entscheidung, "Bewertung")
        el_bewertung.text = bewertung

        if begruendung:
            el_begruendung = etree.SubElement(entscheidung, "Begruendung")
            el_begruendung.text = begruendung

        if kommentar:
            el_kommentar = etree.SubElement(entscheidung, "Kommentar")
            el_kommentar.text = kommentar

        baum.write(str(protokoll_pfad), pretty_print=True, xml_declaration=True, encoding="UTF-8")
        logger.info("[Job %s] Archivar-Entscheidung ins Protokoll geschrieben", job.id)
    except Exception as e:
        logger.warning("[Job %s] Fehler beim Protokoll-Update: %s", job.id, e)


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/audit — Audit-Log abrufen
# ---------------------------------------------------------------------------


@router.get("/api/job/{job_id}/audit")
def audit_log_abrufen(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Gibt das Audit-Log eines Jobs zurück."""
    job = _hole_job(job_id, db)
    eintraege = json.loads(job.audit_log) if job.audit_log else []
    return {"job_id": job_id, "audit_log": eintraege}


# ---------------------------------------------------------------------------
# GET /api/job/{job_id}/download
# ---------------------------------------------------------------------------


@router.get("/api/job/{job_id}/download")
def download(job_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """Gibt das finale ZIP des Jobs zum Download zurück."""
    job = _hole_job(job_id, db)
    if not job.aktuelles_zip or not Path(job.aktuelles_zip).exists():
        raise HTTPException(status_code=404, detail="Kein finales ZIP verfügbar.")

    # Path-Traversal-Schutz: ZIP muss innerhalb des Datenverzeichnisses liegen
    zip_pfad = Path(job.aktuelles_zip).resolve()
    data_pfad = DATA_DIR.resolve()
    if zip_pfad != data_pfad and not zip_pfad.is_relative_to(data_pfad):
        logger.error(
            "[Job %s] Path-Traversal-Versuch: %s", job_id, pseudonymisiere_dateiname(str(zip_pfad))
        )
        raise HTTPException(status_code=403, detail="Zugriff verweigert.")

    return FileResponse(
        path=job.aktuelles_zip,
        media_type="application/zip",
        filename=job.dateiname,
    )
