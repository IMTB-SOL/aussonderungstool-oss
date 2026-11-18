"""Agent 2 – Formatprüfung: MIME-Typ und PDF/A-Validierung via veraPDF."""

import argparse
import asyncio
import logging
import os
import re
from pathlib import Path

import magic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

VERAPDF_PATH = os.getenv("VERAPDF_PATH", "/opt/verapdf/bin/verapdf")

# Gewünschtes PDF/A-Zielformat (z.B. "2b" für PDF/A-2b) — konfigurierbar via .env
PDFA_ZIELFORMAT = os.getenv("PDFA_ZIELFORMAT", "2b")

# CPU-Kerne für Parallelität (Standard: Hälfte der verfügbaren Kerne, min. 2)
CPU_KERNE = max(2, (os.cpu_count() or 4) // 2)

# Dateien, die bei der Formatprüfung ignoriert werden
IGNORIERTE_SUFFIXE = {".xml"}
IGNORIERTE_PREFIXE = {"protokoll_"}

# Langzeitarchivfähige Formate nach deutschen Archivstandards (NESTOR/BSI TR-ESOR)
ARCHIVFAEHIGE_MIME_TYPEN = {
    "application/pdf",  # PDF (wird zusätzlich gegen PDF/A geprüft)
    "image/tiff",  # TIFF
    "image/png",  # PNG
    "text/plain",  # Plain Text (UTF-8)
    "text/xml",  # XML
    "application/xml",  # XML
}

ARCHIV_EMPFEHLUNGEN = {
    "image/jpeg": "JPEG ist verlustbehaftet – empfohlen: TIFF oder PNG.",
    "image/gif": "GIF ist veraltet – empfohlen: PNG.",
    "image/bmp": "BMP ist nicht standardisiert – empfohlen: TIFF oder PNG.",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX ist proprietär – empfohlen: PDF/A.",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX ist proprietär – empfohlen: PDF/A oder CSV.",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX ist proprietär – empfohlen: PDF/A.",
    "application/msword": "DOC ist proprietär (Legacy) – empfohlen: PDF/A.",
    "application/vnd.ms-excel": "XLS ist proprietär (Legacy) – empfohlen: PDF/A oder CSV.",
    "application/vnd.ms-powerpoint": "PPT ist proprietär (Legacy) – empfohlen: PDF/A.",
    "application/zip": "ZIP-Container – Inhalt muss einzeln geprüft werden.",
    "application/x-rar-compressed": "RAR ist proprietär – empfohlen: ZIP.",
}

# Sprechende Dateityp-Labels für die UI (statt technischer MIME-Typen).
MIME_DATEITYP_LABELS = {
    "application/pdf": "PDF",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/tiff": "TIFF",
    "image/gif": "GIF",
    "image/bmp": "BMP",
    "text/plain": "Text",
    "text/xml": "XML",
    "application/xml": "XML",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
    "application/msword": "DOC",
    "application/vnd.ms-excel": "XLS",
    "application/vnd.ms-powerpoint": "PPT",
    "application/zip": "ZIP",
    "application/x-rar-compressed": "RAR",
}


# Welche MIME-Typen zu einer Dateiendung passen. Nur Endungen, bei denen eine
# Abweichung wirklich ein Befund ist — unbekannte Endungen werden nie
# beanstandet, damit keine Falschmeldungen entstehen.
ENDUNG_ERWARTETE_MIME: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".gif": {"image/gif"},
    ".bmp": {"image/bmp", "image/x-ms-bmp"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".tif": {"image/tiff"},
    ".tiff": {"image/tiff"},
    ".rtf": {"application/rtf", "text/rtf"},
    ".txt": {"text/plain"},
    ".xml": {"text/xml", "application/xml"},
    # OOXML sind ZIP-Container — je nach libmagic-Version wird der
    # spezifische Typ oder schlicht application/zip gemeldet.
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    },
    ".doc": {"application/msword", "application/x-ole-storage"},
    ".xls": {"application/vnd.ms-excel", "application/x-ole-storage"},
    ".ppt": {"application/vnd.ms-powerpoint", "application/x-ole-storage"},
}


def passt_mime_zur_endung(endung: str, mime_typ: str | None) -> bool:
    """Prüft, ob der erkannte MIME-Typ zur Dateiendung passt.

    Liefert True, wenn kein Widerspruch feststellbar ist — also auch bei
    unbekannter Endung oder fehlendem MIME-Typ. So entstehen keine
    Falschbefunde bei Formaten, die wir nicht kennen.
    """
    if not mime_typ or not endung:
        return True
    erwartet = ENDUNG_ERWARTETE_MIME.get(endung.lower())
    if not erwartet:
        return True
    return mime_typ in erwartet


def mime_zu_dateityp(mime: str | None) -> str:
    """Liefert ein verständliches Dateityp-Label für einen MIME-Typ.

    Bei bekannten MIME-Typen das vordefinierte Label; bei unbekannten den
    Subtype in Großbuchstaben (z. B. 'application/foobar' → 'FOOBAR');
    bei None/leerem String 'unbekannt'.
    """
    if not mime:
        return "unbekannt"
    if mime in MIME_DATEITYP_LABELS:
        return MIME_DATEITYP_LABELS[mime]
    if "/" in mime:
        return mime.split("/", 1)[1].upper()
    return mime.upper()


async def pruefe_format(job_id: str, arbeitsverzeichnis: Path) -> dict:
    """
    Führt die Formatprüfung für den angegebenen Job durch.

    Schritte:
    1. Dateien im Arbeitsverzeichnis lesen
    2. MIME-Typ per python-magic bestimmen + Archivfähigkeit prüfen
    3. Alle PDFs per veraPDF-Batch auf PDF/A-Konformität prüfen
    4. Ergebnis pro Datei zusammenstellen
    """
    from utils.agent_runner import sende_sse_event
    from utils.protokoll import erstelle_protokoll
    from utils.zip_handler import entpacke_lv_bundle_falls_vorhanden

    meldungen: list[dict] = []
    gesamt_status = "ok"

    try:
        # 0. Wenn Lieferverzeichnis-Bundle: innere ZIPs entpacken, damit alle
        # Vorgangs-Dokumente per rglob sichtbar sind.
        n_bundle = entpacke_lv_bundle_falls_vorhanden(arbeitsverzeichnis)
        if n_bundle:
            meldungen.append(
                {
                    "typ": "info",
                    "text": f"Lieferverzeichnis-Bundle erkannt — {n_bundle} innere Vorgangs-ZIPs entpackt.",
                }
            )

        # 1. Relevante Dateien sammeln (ZIPs jetzt zusätzlich ignorieren)
        dateien = [
            d
            for d in arbeitsverzeichnis.rglob("*")
            if d.is_file()
            and d.suffix.lower() not in IGNORIERTE_SUFFIXE
            and d.suffix.lower() != ".zip"
            and not any(d.name.startswith(p) for p in IGNORIERTE_PREFIXE)
        ]

        if not dateien:
            meldungen.append({"typ": "warnung", "text": "Keine prüfbaren Dateien im ZIP gefunden."})
            erstelle_protokoll(job_id, "format", "ok", meldungen, arbeitsverzeichnis)
            return {"status": "ok", "dateien": []}

        meldungen.append(
            {
                "typ": "info",
                "text": f"{len(dateien)} Datei(en) zur Formatprüfung. ({CPU_KERNE} CPU-Kerne genutzt)",
            }
        )
        await sende_sse_event(
            job_id, "format", "laufend", f"{len(dateien)} Dateien werden geprüft…", 10
        )

        # 2. MIME-Typ + Archivfähigkeit für alle Dateien bestimmen
        dateiergebnisse: list[dict] = []
        pdf_dateien: list[Path] = []

        for datei in dateien:
            ergebnis = _pruefe_mime_und_archiv(datei, meldungen)
            dateiergebnisse.append(ergebnis)
            if ergebnis["mime_typ"] == "application/pdf":
                pdf_dateien.append(datei)

        await sende_sse_event(
            job_id,
            "format",
            "laufend",
            f"MIME-Typen bestimmt. {len(pdf_dateien)} PDFs für PDF/A-Prüfung…",
            30,
        )

        # 3. veraPDF-Batch für alle PDFs
        verapdf_verfuegbar = Path(VERAPDF_PATH).exists()
        if verapdf_verfuegbar and pdf_dateien:
            pdfa_ergebnisse = await _pruefe_pdfa_batch(
                job_id, pdf_dateien, meldungen, sende_sse_event
            )

            # Ergebnisse den Dateien zuordnen
            ergebnis_map = {d["dateiname"]: d for d in dateiergebnisse}
            pdfa_ok = []
            pdfa_fehler = []
            pdfa_abweichungen = []
            for dateiname, pdfa in pdfa_ergebnisse.items():
                if dateiname in ergebnis_map:
                    ergebnis_map[dateiname]["pdfa_valid"] = pdfa["valid"]
                    ergebnis_map[dateiname]["details"] = pdfa["details"]
                    ergebnis_map[dateiname]["pdfa_profil"] = pdfa.get("pdfa_profil")
                    ergebnis_map[dateiname]["archivfaehig"] = pdfa["valid"]
                    ergebnis_map[dateiname]["fehler_anzahl"] = pdfa.get("fehler_anzahl", 0)
                    ergebnis_map[dateiname]["fehler_details"] = pdfa.get("fehler_details", [])
                    if pdfa["valid"]:
                        pdfa_ok.append(dateiname)
                        if pdfa.get("ziel_abweichung"):
                            pdfa_abweichungen.append((dateiname, pdfa["ziel_abweichung"]))
                    else:
                        ergebnis_map[dateiname]["empfehlung"] = (
                            f"PDF ist nicht PDF/A-konform – empfohlen: Konvertierung nach PDF/A-{PDFA_ZIELFORMAT}."
                        )
                        pdfa_fehler.append(dateiname)

            # Zusammengefasste Meldungen statt einzeln pro Datei
            if pdfa_ok:
                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"{len(pdfa_ok)} von {len(pdf_dateien)} PDFs sind PDF/A-konform – langzeitarchivfähig.",
                    }
                )
            if pdfa_fehler:
                meldungen.append(
                    {
                        "typ": "fehler",
                        "text": f"{len(pdfa_fehler)} von {len(pdf_dateien)} PDFs sind nicht PDF/A-konform – Konvertierung nach PDF/A-{PDFA_ZIELFORMAT} empfohlen.",
                    }
                )

            # Pro nicht-konformer Datei nur die Anzahl der Verletzungen vermerken —
            # die einzelnen Regeln sind strukturiert in fehler_details und werden
            # im Frontend in der ausklappbaren Detail-Zeile angezeigt.
            for dateiname in pdfa_fehler:
                pdfa = pdfa_ergebnisse.get(dateiname, {})
                fehler_anzahl = pdfa.get("fehler_anzahl", 0)
                if fehler_anzahl:
                    meldungen.append(
                        {
                            "typ": "warnung",
                            "text": f"{dateiname}: {fehler_anzahl} Regelverletzung(en) — Details in der Tabelle einklappbar.",
                        }
                    )
            if pdfa_abweichungen:
                meldungen.append(
                    {
                        "typ": "warnung",
                        "text": f"{len(pdfa_abweichungen)} PDF(s) sind PDF/A-konform, aber nicht im gewünschten Zielformat PDF/A-{PDFA_ZIELFORMAT}: {', '.join(d[1] for d in pdfa_abweichungen[:5])}{'…' if len(pdfa_abweichungen) > 5 else ''}",
                    }
                )
        elif not verapdf_verfuegbar and pdf_dateien:
            meldungen.append(
                {
                    "typ": "warnung",
                    "text": f"veraPDF nicht gefunden unter {VERAPDF_PATH} – PDF/A-Prüfung wird übersprungen.",
                }
            )
            for d in dateiergebnisse:
                if d["mime_typ"] == "application/pdf":
                    d["details"] = "PDF/A-Prüfung übersprungen (veraPDF nicht verfügbar)."
                    d["archivfaehig"] = False
                    d["empfehlung"] = "PDF/A-Status unbekannt – veraPDF nicht verfügbar."

        # Zusammenfassung nach Dateityp
        from collections import Counter

        typ_zaehler = Counter(d["mime_typ"] for d in dateiergebnisse if d["mime_typ"])
        archiv_ok = sum(1 for d in dateiergebnisse if d.get("archivfaehig"))
        nicht_archiv = [
            d
            for d in dateiergebnisse
            if not d.get("archivfaehig")
            and d.get("mime_typ")
            and d["mime_typ"] != "application/pdf"
        ]

        typ_text = ", ".join(f"{v}× {mime_zu_dateityp(k)}" for k, v in typ_zaehler.most_common())
        meldungen.append({"typ": "info", "text": f"Dateitypen: {typ_text}"})
        meldungen.append(
            {
                "typ": "info",
                "text": f"Langzeitarchivfähig: {archiv_ok} von {len(dateiergebnisse)} Dateien.",
            }
        )

        if nicht_archiv:
            typen = Counter(d["mime_typ"] for d in nicht_archiv)
            nicht_text = ", ".join(f"{v}× {mime_zu_dateityp(k)}" for k, v in typen.most_common())
            meldungen.append(
                {
                    "typ": "warnung",
                    "text": f"{len(nicht_archiv)} Datei(en) nicht langzeitarchivfähig: {nicht_text}",
                }
            )

        # Gesamtstatus bestimmen
        for ergebnis_datei in dateiergebnisse:
            if ergebnis_datei.get("pdfa_valid") is False:
                gesamt_status = "fehler"
            elif not ergebnis_datei.get("archivfaehig") and ergebnis_datei.get("mime_typ"):
                if gesamt_status == "ok":
                    gesamt_status = "warnung"

        erstelle_protokoll(job_id, "format", gesamt_status, meldungen, arbeitsverzeichnis)
        return {"status": gesamt_status, "dateien": dateiergebnisse}

    except Exception as e:
        logger.exception("[Job %s] Unerwarteter Fehler im Agent 'format': %s", job_id, e)
        meldungen.append({"typ": "fehler", "text": f"Unerwarteter Fehler: {e}"})
        erstelle_protokoll(job_id, "format", "fehler", meldungen, arbeitsverzeichnis)
        return {
            "status": "fehler",
            "dateien": dateiergebnisse if "dateiergebnisse" in dir() else [],
        }


def _pruefe_mime_und_archiv(datei: Path, meldungen: list[dict]) -> dict:
    """Bestimmt MIME-Typ und Archivfähigkeit einer Datei."""
    ergebnis: dict = {
        "dateiname": datei.name,
        "mime_typ": None,
        "dateityp": "unbekannt",
        "pdfa_valid": None,
        "archivfaehig": False,
        "endung_passt": True,
        "empfehlung": "",
        "details": "",
    }

    try:
        mime_typ = magic.from_file(str(datei), mime=True)
        ergebnis["mime_typ"] = mime_typ
        ergebnis["dateityp"] = mime_zu_dateityp(mime_typ)
    except Exception as e:
        ergebnis["details"] = f"MIME-Typ-Erkennung fehlgeschlagen: {e}"
        meldungen.append(
            {"typ": "warnung", "text": f"{datei.name}: MIME-Erkennung fehlgeschlagen: {e}"}
        )
        return ergebnis

    # Widerspruch zwischen Endung und Inhalt: Findmittel und Viewer im Archiv
    # richten sich nach der Endung, gelesen wird aber der echte Inhalt.
    # (Praxisfall: Winyard liefert eine XAIP-XML mit der Endung ".pdf".)
    if not passt_mime_zur_endung(datei.suffix, mime_typ):
        ergebnis["endung_passt"] = False
        erwartet = mime_zu_dateityp(next(iter(ENDUNG_ERWARTETE_MIME[datei.suffix.lower()])))
        hinweis = (
            f"Dateiendung {datei.suffix.lower()} verspricht {erwartet}, "
            f"der Inhalt ist aber {ergebnis['dateityp']}."
        )
        ergebnis["details"] = (ergebnis["details"] + " " + hinweis).strip()
        meldungen.append({"typ": "warnung", "text": f"{datei.name}: {hinweis}"})

    if mime_typ in ARCHIVFAEHIGE_MIME_TYPEN:
        ergebnis["archivfaehig"] = True
    elif mime_typ in ARCHIV_EMPFEHLUNGEN:
        ergebnis["empfehlung"] = ARCHIV_EMPFEHLUNGEN[mime_typ]
    else:
        ergebnis["empfehlung"] = (
            "Unbekanntes Format – Eignung für Langzeitarchivierung manuell prüfen."
        )

    return ergebnis


async def _pruefe_pdfa_batch(
    job_id: str,
    pdf_dateien: list[Path],
    meldungen: list[dict],
    sende_sse_event,
) -> dict[str, dict]:
    """Prüft alle PDFs per veraPDF-Batch (ein Prozess, alle Dateien auf einmal)."""
    ergebnisse: dict[str, dict] = {}
    gesamt = len(pdf_dateien)

    # veraPDF in Batches à 20 PDFs aufrufen, max CPU_KERNE Batches gleichzeitig
    batch_groesse = 20
    batches = [pdf_dateien[i : i + batch_groesse] for i in range(0, gesamt, batch_groesse)]
    batch_gesamt = len(batches)
    fertig_pdfs = 0

    await sende_sse_event(
        job_id, "format", "laufend", f"PDF/A-Prüfung: 0/{gesamt} PDFs ({batch_gesamt} Batches)…", 30
    )

    # Batches in Gruppen von CPU_KERNE parallel verarbeiten
    for gruppen_start in range(0, batch_gesamt, CPU_KERNE):
        gruppe = batches[gruppen_start : gruppen_start + CPU_KERNE]

        async def verarbeite_batch(batch: list[Path]) -> dict[str, dict]:
            try:
                cmd = [VERAPDF_PATH, "--format", "xml", "--flavour", PDFA_ZIELFORMAT] + [
                    str(p) for p in batch
                ]
                prozess = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                timeout = max(120, len(batch) * 30)
                stdout, stderr = await asyncio.wait_for(prozess.communicate(), timeout=timeout)
                ausgabe = stdout.decode("utf-8", errors="replace")
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    logger.debug("[Job %s] veraPDF stderr: %d Zeichen", job_id, len(stderr_text))
                logger.debug("[Job %s] veraPDF stdout: %d Bytes empfangen", job_id, len(ausgabe))
                return _parse_verapdf_batch_output(ausgabe, batch)
            except asyncio.TimeoutError:
                logger.warning("[Job %s] veraPDF Batch-Timeout", job_id)
                return {pdf.name: {"valid": False, "details": "veraPDF-Timeout."} for pdf in batch}
            except Exception as e:
                logger.warning("[Job %s] veraPDF Batch-Fehler: %s", job_id, e)
                return {
                    pdf.name: {"valid": False, "details": f"veraPDF-Fehler: {e}"} for pdf in batch
                }

        gruppen_ergebnisse = await asyncio.gather(*[verarbeite_batch(b) for b in gruppe])

        for batch_ergebnis in gruppen_ergebnisse:
            ergebnisse.update(batch_ergebnis)
            fertig_pdfs += sum(1 for _ in batch_ergebnis)

        await sende_sse_event(
            job_id,
            "format",
            "laufend",
            f"PDF/A-Prüfung: {fertig_pdfs}/{gesamt} PDFs geprüft…",
            int(30 + 50 * fertig_pdfs / gesamt),
        )

    return ergebnisse


def _parse_pdfa_flavour(flavour: str) -> tuple[str | None, str | None]:
    """
    Parst einen veraPDF-Flavour-String wie 'PDFA_2_B' oder 'PDF/A-2b'.

    Returns:
        (version, stufe) z.B. ("2", "b") oder (None, None) bei Fehler.
    """
    if not flavour:
        return None, None
    # Format 1: "PDFA_2_B" (veraPDF XML-Attribut)
    match = re.match(r"PDFA_(\d)_([ABUFE])", flavour, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2).lower()
    # Format 2: "PDF/A-2b" (menschenlesbar)
    match = re.match(r"PDF/A-(\d)([abufe])", flavour, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2).lower()
    return None, None


def _parse_verapdf_batch_output(ausgabe: str, batch: list[Path]) -> dict[str, dict]:
    """Parst die veraPDF-XML-Ausgabe via lxml und ordnet Ergebnisse den Dateinamen zu."""
    from lxml import etree
    from utils.xdomea_parser import safe_xml_parser

    ergebnisse: dict[str, dict] = {}
    _fehlerergebnis = {
        "valid": False,
        "details": "Prüfung fehlgeschlagen.",
        "pdfa_version": None,
        "pdfa_stufe": None,
        "pdfa_profil": None,
        "ziel_abweichung": None,
    }

    try:
        # veraPDF gibt manchmal mehrere Root-Elemente aus — in Wrapper packen
        xml_text = (
            f"<wrapper>{ausgabe}</wrapper>" if not ausgabe.strip().startswith("<?xml") else ausgabe
        )
        # Falls mit XML-Deklaration: Wrapper um Body
        if ausgabe.strip().startswith("<?xml"):
            # Deklaration entfernen, dann wrappen
            ohne_decl = re.sub(r"^<\?xml[^?]*\?>\s*", "", ausgabe.strip())
            xml_text = f"<wrapper>{ohne_decl}</wrapper>"

        root = etree.fromstring(xml_text.encode("utf-8"), safe_xml_parser())

        # Suche alle <job> oder <report> Blöcke (veraPDF-Versionen variieren)
        jobs = root.findall(".//job")
        if not jobs:
            jobs = [root]  # Fallback: gesamtes Dokument durchsuchen

        for job_el in jobs:
            # Dateiname: <item><name>...</name></item> oder <name>...</name>
            name_el = job_el.find(".//name")
            if name_el is None or not name_el.text:
                continue
            dateiname = Path(name_el.text.strip()).name

            # Validierungsergebnis: <validationReport isCompliant="..." flavour="...">
            report = job_el.find(".//validationReport")
            if report is None:
                # Auch <batchSummary> oder direkte Attribute prüfen
                report = job_el.find(".//{http://www.verapdf.org/report}validationReport")

            pdfa_version: str | None = None
            pdfa_stufe: str | None = None
            pdfa_profil: str | None = None

            if report is not None:
                compliant = report.get("isCompliant", "").lower()
                flavour = report.get("flavour", "")
                ist_konform = compliant == "true"

                if flavour:
                    pdfa_version, pdfa_stufe = _parse_pdfa_flavour(flavour)
                    if pdfa_version and pdfa_stufe:
                        pdfa_profil = f"{pdfa_version}{pdfa_stufe}"

                if ist_konform and pdfa_profil:
                    details = f"PDF/A-{pdfa_profil} konform."
                elif ist_konform:
                    details = "PDF/A-konform."
                elif pdfa_profil:
                    details = f"Nicht PDF/A-konform (geprüft gegen PDF/A-{pdfa_profil})."
                else:
                    details = "Nicht PDF/A-konform."

                ziel_abweichung: str | None = None
                if ist_konform and pdfa_profil and pdfa_profil != PDFA_ZIELFORMAT:
                    ziel_abweichung = (
                        f"PDF/A-{pdfa_profil} statt gewünschtem PDF/A-{PDFA_ZIELFORMAT}"
                    )

                # Fehlerdetails aus veraPDF-Report extrahieren
                fehler_anzahl = 0
                fehler_checks = 0
                fehler_details: list[str] = []

                if not ist_konform:
                    details_el = report.find(".//details")
                    if details_el is not None:
                        fehler_anzahl = int(details_el.get("failedRules", "0"))
                        fehler_checks = int(details_el.get("failedChecks", "0"))

                    # Einzelne Rule-Violations sammeln
                    regeln = report.findall(".//rule[@status='failed']")
                    for regel in regeln:
                        clause = regel.get("clause", "?")
                        spec = regel.get("specification", "")
                        desc_el = regel.find("description")
                        desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
                        spec_kurz = spec.split(":")[-1].strip() if spec else ""
                        text = (
                            f"Regel {clause} ({spec_kurz}): {desc}"
                            if spec_kurz
                            else f"Regel {clause}: {desc}"
                        )
                        fehler_details.append(
                            {
                                "clause": clause,
                                "specification": spec_kurz,
                                "description": desc,
                                "text": text,
                            }
                        )

                ergebnisse[dateiname] = {
                    "valid": ist_konform,
                    "details": details,
                    "pdfa_version": pdfa_version,
                    "pdfa_stufe": pdfa_stufe,
                    "pdfa_profil": pdfa_profil,
                    "ziel_abweichung": ziel_abweichung,
                    "fehler_anzahl": fehler_anzahl,
                    "fehler_checks": fehler_checks,
                    "fehler_details": fehler_details,
                }
            else:
                ergebnisse[dateiname] = {
                    "valid": False,
                    "details": "Konformität konnte nicht geprüft werden.",
                    "pdfa_version": None,
                    "pdfa_stufe": None,
                    "pdfa_profil": None,
                    "ziel_abweichung": None,
                    "fehler_anzahl": 0,
                    "fehler_checks": 0,
                    "fehler_details": [],
                }

    except etree.XMLSyntaxError as e:
        logger.warning("veraPDF-XML-Parsing fehlgeschlagen, Fallback auf Regex: %s", e)
        # Fallback auf einfache Regex-Extraktion für Robustheit
        for pdf in batch:
            if pdf.name in ergebnisse:
                continue
            if 'isCompliant="true"' in ausgabe:
                ergebnisse[pdf.name] = {
                    "valid": True,
                    "details": "PDF/A-konform (Regex-Fallback).",
                    "pdfa_version": None,
                    "pdfa_stufe": None,
                    "pdfa_profil": None,
                    "ziel_abweichung": None,
                }
            elif 'isCompliant="false"' in ausgabe:
                ergebnisse[pdf.name] = {
                    "valid": False,
                    "details": "Nicht PDF/A-konform (Regex-Fallback).",
                    "pdfa_version": None,
                    "pdfa_stufe": None,
                    "pdfa_profil": None,
                    "ziel_abweichung": None,
                }
            else:
                ergebnisse[pdf.name] = dict(_fehlerergebnis)

    # Dateien ohne Ergebnis
    for pdf in batch:
        if pdf.name not in ergebnisse:
            ergebnisse[pdf.name] = dict(_fehlerergebnis)

    return ergebnisse


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 2: Formatprüfung")
    parser.add_argument("--job-id", required=True, help="Job-ID")
    parser.add_argument("--pfad", default=".", help="Arbeitsverzeichnis (entpacktes ZIP)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    ergebnis = asyncio.run(pruefe_format(args.job_id, Path(args.pfad)))
    print(ergebnis)
