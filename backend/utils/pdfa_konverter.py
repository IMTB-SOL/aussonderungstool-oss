"""Konvertiert Dateien (DOCX, PPTX, XLSX, JPEG, PNG, PDF) nach PDF/A (konfigurierbare Version)."""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from utils.job_dateien import finde_dateien
from utils.logging_setup import pseudonymisiere_dateiname
from utils.prozess import kommuniziere_mit_timeout

logger = logging.getLogger(__name__)

CPU_KERNE = max(2, (os.cpu_count() or 4) // 2)
GS_PFAD = shutil.which("gs")
SOFFICE_PFAD = shutil.which("soffice")
VERAPDF_PFAD = os.getenv("VERAPDF_PATH") or shutil.which("verapdf") or "/opt/verapdf/bin/verapdf"

OFFICE_SUFFIXE = {".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt", ".odt", ".ods", ".odp"}
BILD_SUFFIXE = {".jpeg", ".jpg", ".png", ".tiff", ".tif", ".bmp"}


def _finde_gs_pdfa_ressourcen() -> tuple[str | None, str | None]:
    """Findet Ghostscript PDFA_def.ps und sRGB ICC-Profil."""
    import glob

    # Ghostscript-Lib-Verzeichnis finden
    gs_share = None
    for basis in [
        "/opt/homebrew/Cellar/ghostscript",
        "/opt/homebrew/share/ghostscript",
        "/usr/share/ghostscript",
        "/usr/local/share/ghostscript",
    ]:
        treffer = glob.glob(f"{basis}/**/PDFA_def.ps", recursive=True)
        if treffer:
            gs_share = str(Path(treffer[0]).parent.parent)
            break

    if not gs_share:
        return None, None

    pdfa_def = Path(gs_share) / "lib" / "PDFA_def.ps"
    icc_profil = Path(gs_share) / "iccprofiles" / "srgb.icc"

    if not pdfa_def.exists():
        # Suche rekursiv
        treffer = glob.glob(f"{gs_share}/**/PDFA_def.ps", recursive=True)
        pdfa_def = Path(treffer[0]) if treffer else None

    if not icc_profil.exists():
        treffer = glob.glob(f"{gs_share}/**/srgb.icc", recursive=True)
        icc_profil = Path(treffer[0]) if treffer else None

    return (
        str(pdfa_def) if pdfa_def and pdfa_def.exists() else None,
        str(icc_profil) if icc_profil and icc_profil.exists() else None,
    )


# Beim Import einmalig suchen
_GS_PDFA_DEF, _GS_ICC_PROFIL = _finde_gs_pdfa_ressourcen()
if _GS_PDFA_DEF:
    logger.info("Ghostscript PDFA_def.ps: %s", _GS_PDFA_DEF)
    logger.info("Ghostscript ICC-Profil: %s", _GS_ICC_PROFIL)
else:
    logger.warning(
        "Ghostscript PDFA_def.ps nicht gefunden — PDF/A-Konvertierung evtl. eingeschraenkt."
    )


async def konvertiere_nach_pdfa(
    arbeitsverzeichnis: Path,
    dateien: list[dict],
    meldungen: list[dict],
    sende_sse_event=None,
    job_id: str = "",
    zielformat: str = "2b",
) -> dict[str, dict]:
    """
    Konvertiert alle nicht-PDF/A-Dateien nach PDF/A (konfigurierbare Version).

    Args:
        arbeitsverzeichnis: Verzeichnis mit den Dateien.
        dateien: Liste der Datei-Ergebnisse aus der Formatprüfung.
        meldungen: Protokoll-Meldungen.
        sende_sse_event: SSE-Event-Funktion.
        job_id: Job-ID für Logging.
        zielformat: PDF/A-Zielversion, z.B. "1b", "2b", "3b" (Default: "2b").

    Returns:
        Dict von Dateiname → {"konvertiert": bool, "pdfa_pfad": str, "details": str}
    """
    # Ghostscript-Parameter aus Zielformat ableiten (1b→1, 2b→2, 3b→3)
    gs_pdfa_version = zielformat[0] if zielformat and zielformat[0] in "123" else "2"
    pdfa_label = f"PDF/A-{zielformat}"
    ergebnisse: dict[str, dict] = {}

    # Dateien nach Typ gruppieren
    office_dateien: list[Path] = []
    bild_dateien: list[Path] = []
    pdf_dateien: list[Path] = []

    for d in dateien:
        pfad = arbeitsverzeichnis / Path(str(d["dateiname"])).name
        if not pfad.exists():
            # Suche in Unterverzeichnissen
            gefunden = finde_dateien(arbeitsverzeichnis, d["dateiname"])
            pfad = gefunden[0] if gefunden else pfad

        if not pfad.exists():
            ergebnisse[d["dateiname"]] = {"konvertiert": False, "details": "Datei nicht gefunden."}
            continue

        suffix = pfad.suffix.lower()
        if suffix in OFFICE_SUFFIXE:
            office_dateien.append(pfad)
        elif suffix in BILD_SUFFIXE:
            bild_dateien.append(pfad)
        elif suffix == ".pdf" and not d.get("pdfa_valid"):
            pdf_dateien.append(pfad)

    gesamt = len(office_dateien) + len(bild_dateien) + len(pdf_dateien)
    if gesamt == 0:
        meldungen.append({"typ": "info", "text": "Keine Dateien zur Konvertierung gefunden."})
        return ergebnisse

    meldungen.append(
        {
            "typ": "info",
            "text": f"{gesamt} Datei(en) zur PDF/A-Konvertierung: {len(office_dateien)} Office, {len(bild_dateien)} Bilder, {len(pdf_dateien)} PDFs.",
        }
    )
    fertig = 0

    # 1. Office-Dateien per LibreOffice Batch → PDF
    if office_dateien and SOFFICE_PFAD:
        if sende_sse_event:
            await sende_sse_event(
                job_id,
                "format",
                "laufend",
                f"Konvertiere {len(office_dateien)} Office-Dateien nach PDF…",
                10,
            )

        ausgabe_dir = arbeitsverzeichnis / "_konvertiert"
        ausgabe_dir.mkdir(exist_ok=True)

        try:
            cmd = [
                SOFFICE_PFAD,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(ausgabe_dir),
            ] + [str(p) for p in office_dateien]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await kommuniziere_mit_timeout(proc, 300)

            for pfad in office_dateien:
                pdf_name = pfad.stem + ".pdf"
                pdf_pfad = ausgabe_dir / pdf_name
                if pdf_pfad.exists():
                    pdf_dateien.append(pdf_pfad)  # Wird im nächsten Schritt nach PDF/A konvertiert
                    ergebnisse[pfad.name] = {
                        "konvertiert": True,
                        "pdfa_pfad": "",
                        "details": "Nach PDF konvertiert, PDF/A folgt.",
                    }
                    fertig += 1
                else:
                    ergebnisse[pfad.name] = {
                        "konvertiert": False,
                        "details": "LibreOffice-Konvertierung fehlgeschlagen.",
                    }
                    fertig += 1

            meldungen.append(
                {
                    "typ": "info",
                    "text": f"{sum(1 for e in ergebnisse.values() if e.get('konvertiert'))} Office-Dateien nach PDF konvertiert.",
                }
            )
        except asyncio.TimeoutError:
            meldungen.append(
                {"typ": "fehler", "text": "LibreOffice-Timeout bei Office-Konvertierung."}
            )
        except Exception as e:
            meldungen.append({"typ": "fehler", "text": f"LibreOffice-Fehler: {e}"})
    elif office_dateien and not SOFFICE_PFAD:
        meldungen.append(
            {
                "typ": "warnung",
                "text": "LibreOffice nicht installiert — Office-Dateien können nicht konvertiert werden.",
            }
        )

    # 2. Bilder → PDF (via img2pdf oder Pillow)
    if bild_dateien:
        if sende_sse_event:
            await sende_sse_event(
                job_id, "format", "laufend", f"Konvertiere {len(bild_dateien)} Bilder nach PDF…", 30
            )

        for pfad in bild_dateien:
            try:
                import img2pdf

                ausgabe_dir = arbeitsverzeichnis / "_konvertiert"
                ausgabe_dir.mkdir(exist_ok=True)
                pdf_pfad = ausgabe_dir / (pfad.stem + ".pdf")

                # img2pdf für verlustfreie Konvertierung
                with open(pdf_pfad, "wb") as f:
                    f.write(img2pdf.convert(str(pfad)))

                pdf_dateien.append(pdf_pfad)
                ergebnisse[pfad.name] = {
                    "konvertiert": True,
                    "pdfa_pfad": "",
                    "details": "Nach PDF konvertiert, PDF/A folgt.",
                }
            except Exception as e:
                ergebnisse[pfad.name] = {
                    "konvertiert": False,
                    "details": f"Bildkonvertierung fehlgeschlagen: {e}",
                }
            fertig += 1

    # 3. Alle PDFs → PDF/A per Ghostscript (parallel)
    if pdf_dateien and GS_PFAD:
        if sende_sse_event:
            await sende_sse_event(
                job_id,
                "format",
                "laufend",
                f"Konvertiere {len(pdf_dateien)} PDFs nach {pdfa_label}…",
                50,
            )

        semaphore = asyncio.Semaphore(CPU_KERNE)
        pdfa_dir = arbeitsverzeichnis / "_pdfa"
        pdfa_dir.mkdir(exist_ok=True)

        # PDFA_def.ps mit korrektem ICC-Pfad vorbereiten (einmalig pro Konvertierungslauf)
        pdfa_def_custom = None
        if _GS_PDFA_DEF and _GS_ICC_PROFIL:
            pdfa_def_custom = pdfa_dir / "PDFA_def.ps"
            original = Path(_GS_PDFA_DEF).read_text()
            # ICC-Pfad in PDFA_def.ps auf absoluten Pfad setzen
            angepasst = original.replace(
                "/ICCProfile (srgb.icc)", f"/ICCProfile ({_GS_ICC_PROFIL})"
            )
            pdfa_def_custom.write_text(angepasst)

        async def konvertiere_pdf(pfad: Path) -> tuple[str, dict]:
            async with semaphore:
                pdfa_pfad = pdfa_dir / pfad.name
                try:
                    gs_args = [
                        GS_PFAD,
                        f"-dPDFA={gs_pdfa_version}",
                        "-dBATCH",
                        "-dNOPAUSE",
                        "-dQUIET",
                        "-sDEVICE=pdfwrite",
                        "-sColorConversionStrategy=RGB",
                        "-dPDFACompatibilityPolicy=2",
                        f"-sOutputFile={pdfa_pfad}",
                    ]
                    if pdfa_def_custom and _GS_ICC_PROFIL:
                        gs_args.append(f"--permit-file-read={_GS_ICC_PROFIL}")
                        gs_args.append(str(pdfa_def_custom))
                    gs_args.append(str(pfad))

                    proc = await asyncio.create_subprocess_exec(
                        *gs_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await kommuniziere_mit_timeout(proc, 180)

                    if proc.returncode != 0:
                        stderr_text = (
                            stderr.decode("utf-8", errors="replace")[:500] if stderr else ""
                        )
                        logger.warning(
                            "Ghostscript-Fehler bei %s (rc=%d): %s",
                            pseudonymisiere_dateiname(pfad.name),
                            proc.returncode,
                            stderr_text,
                        )
                        return pfad.name, {
                            "konvertiert": False,
                            "details": f"Ghostscript-Fehler: {stderr_text[:200]}",
                        }

                    if not pdfa_pfad.exists() or pdfa_pfad.stat().st_size <= 100:
                        return pfad.name, {
                            "konvertiert": False,
                            "details": "Ghostscript-Konvertierung fehlgeschlagen (leere Ausgabe).",
                        }

                    return pfad.name, {
                        "konvertiert": True,
                        "pdfa_pfad": str(pdfa_pfad),
                        "details": f"Nach {pdfa_label} konvertiert.",
                    }

                except asyncio.TimeoutError:
                    return pfad.name, {"konvertiert": False, "details": "Ghostscript-Timeout."}
                except Exception as e:
                    return pfad.name, {"konvertiert": False, "details": f"Fehler: {e}"}

        # Ghostscript-Konvertierungen mit Fortschritts-Updates
        gs_ergebnisse_liste: list[tuple[str, dict]] = []
        gs_fertig = 0
        gs_gesamt = len(pdf_dateien)

        # In Gruppen konvertieren fuer Fortschritts-Updates
        GS_GRUPPE = max(CPU_KERNE * 2, 10)
        for gruppe_start in range(0, gs_gesamt, GS_GRUPPE):
            gruppe = pdf_dateien[gruppe_start : gruppe_start + GS_GRUPPE]
            batch_ergebnisse = await asyncio.gather(*[konvertiere_pdf(p) for p in gruppe])
            gs_ergebnisse_liste.extend(batch_ergebnisse)
            gs_fertig += len(gruppe)
            if sende_sse_event:
                prozent = int(50 + 30 * gs_fertig / gs_gesamt)
                await sende_sse_event(
                    job_id,
                    "format",
                    "laufend",
                    f"Konvertierung: {gs_fertig}/{gs_gesamt} PDFs…",
                    prozent,
                )

        erfolge = 0
        konvertierte_pfade: list[Path] = []
        for dateiname, erg in gs_ergebnisse_liste:
            # Original-Dateiname finden (könnte aus Office-Konvertierung kommen)
            orig_name = dateiname
            for d in dateien:
                if (
                    d["dateiname"]
                    .replace(".docx", ".pdf")
                    .replace(".pptx", ".pdf")
                    .replace(".xlsx", ".pdf")
                    == dateiname
                ):
                    orig_name = d["dateiname"]
                    break
            if orig_name in ergebnisse:
                ergebnisse[orig_name]["pdfa_pfad"] = erg.get("pdfa_pfad", "")
                ergebnisse[orig_name]["details"] = erg["details"]
                if not erg["konvertiert"]:
                    ergebnisse[orig_name]["konvertiert"] = False
            else:
                ergebnisse[orig_name] = erg
            if erg["konvertiert"]:
                erfolge += 1
                if erg.get("pdfa_pfad"):
                    konvertierte_pfade.append(Path(erg["pdfa_pfad"]))

        meldungen.append(
            {
                "typ": "info",
                "text": f"{erfolge} von {len(pdf_dateien)} PDFs erfolgreich nach {pdfa_label} konvertiert.",
            }
        )

        # Re-Validierung + OCR-Fallback gepipelinet
        # Strategie: Re-Validierung in Batches, Failures sofort an OCR-Queue weiterleiten
        if konvertierte_pfade and Path(VERAPDF_PFAD).exists():
            from agents.agent_format import _parse_verapdf_batch_output

            # Lookup: pdfa-Pfad → Original-Key
            pfad_zu_key: dict[str, str] = {}
            for key, erg in ergebnisse.items():
                if erg.get("pdfa_pfad"):
                    pfad_zu_key[Path(erg["pdfa_pfad"]).name] = key

            BATCH_SIZE = 30
            re_val_ok = 0
            re_val_fehler = 0
            ocr_ok = 0
            ocr_fehler = 0
            gesamt_fertig = 0
            gesamt_total = len(konvertierte_pfade)

            # Render-Fallback-Tasks: Failures werden sofort hier reingeschoben
            # OCR-Fallback: ocrmypdf mit --force-ocr erzeugt durchsuchbare PDF/A
            ocr_tasks: list[asyncio.Task] = []
            OCRMYPDF_PFAD = shutil.which("ocrmypdf")
            # Parallelitaet: max 3 OCR-Prozesse gleichzeitig (jeder nutzt --jobs 4 intern)
            ocr_semaphore = asyncio.Semaphore(max(1, CPU_KERNE // 3))

            async def ocr_konvertiere(orig_key: str, pdfa_pfad: Path) -> tuple[bool, str]:
                """OCR-Fallback: ocrmypdf erzeugt durchsuchbare PDF/A-2b."""
                async with ocr_semaphore:
                    try:
                        ocr_out = pdfa_pfad.parent / f"_ocr_{pdfa_pfad.name}"
                        ocr_proc = await asyncio.create_subprocess_exec(
                            OCRMYPDF_PFAD,
                            "--force-ocr",
                            "--output-type",
                            "pdfa-2",
                            "--pdfa-image-compression",
                            "jpeg",
                            "-l",
                            "deu+eng",
                            "--jobs",
                            "4",
                            "--optimize",
                            "0",
                            str(pdfa_pfad),
                            str(ocr_out),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await kommuniziere_mit_timeout(ocr_proc, 300)
                        if ocr_out.exists() and ocr_out.stat().st_size > 100:
                            pdfa_pfad.unlink(missing_ok=True)
                            ocr_out.rename(pdfa_pfad)
                            return True, orig_key
                        return False, orig_key
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning(
                            "OCR-Fallback fehlgeschlagen bei %s: %s",
                            pseudonymisiere_dateiname(pdfa_pfad.name),
                            e,
                        )
                        return False, orig_key

            if sende_sse_event:
                await sende_sse_event(
                    job_id,
                    "format",
                    "laufend",
                    f"Validierung + Reparatur: 0/{gesamt_total} PDFs…",
                    85,
                )

            # Re-Validierung in Batches — Failures sofort an OCR-Fallback weiterleiten
            for batch_start in range(0, gesamt_total, BATCH_SIZE):
                batch = konvertierte_pfade[batch_start : batch_start + BATCH_SIZE]
                try:
                    cmd = [VERAPDF_PFAD, "--format", "xml", "--flavour", zielformat] + [
                        str(p) for p in batch
                    ]
                    verapdf_proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    timeout = max(120, len(batch) * 15)
                    verapdf_out, _ = await kommuniziere_mit_timeout(verapdf_proc, timeout)
                    verapdf_text = verapdf_out.decode("utf-8", errors="replace")
                    re_val_ergebnisse = _parse_verapdf_batch_output(verapdf_text, batch)

                    for pdfa_pfad in batch:
                        ziel_key = pfad_zu_key.get(pdfa_pfad.name)
                        if not ziel_key:
                            continue
                        ist_valide = re_val_ergebnisse.get(pdfa_pfad.name, {}).get("valid", False)
                        ergebnisse[ziel_key]["re_validiert"] = ist_valide
                        if ist_valide:
                            ergebnisse[ziel_key]["details"] = (
                                f"Nach {pdfa_label} konvertiert und validiert."
                            )
                            re_val_ok += 1
                        elif OCRMYPDF_PFAD:
                            task = asyncio.create_task(ocr_konvertiere(ziel_key, pdfa_pfad))
                            ocr_tasks.append(task)
                        else:
                            ergebnisse[ziel_key]["details"] = (
                                f"Nach {pdfa_label} konvertiert. Nicht PDF/A-konform."
                            )
                            re_val_fehler += 1

                except Exception as e:
                    logger.warning("Re-Validierungs-Batch fehlgeschlagen: %s", e)
                    for pdfa_pfad in batch:
                        ziel_key = pfad_zu_key.get(pdfa_pfad.name)
                        if ziel_key:
                            ergebnisse[ziel_key]["re_validiert"] = False
                            re_val_fehler += 1

                gesamt_fertig += len(batch)
                if sende_sse_event:
                    ocr_bisher = sum(1 for t in ocr_tasks if t.done())
                    prozent = int(85 + 13 * gesamt_fertig / gesamt_total)
                    await sende_sse_event(
                        job_id,
                        "format",
                        "laufend",
                        f"Validierung: {gesamt_fertig}/{gesamt_total} — OCR: {ocr_bisher}/{len(ocr_tasks)}…",
                        prozent,
                    )

            # Auf verbleibende OCR-Tasks warten
            if ocr_tasks:
                ocr_gesamt = len(ocr_tasks)
                while not all(t.done() for t in ocr_tasks):
                    await asyncio.sleep(2)
                    if sende_sse_event:
                        ocr_bisher = sum(1 for t in ocr_tasks if t.done())
                        prozent = int(95 + 4 * ocr_bisher / ocr_gesamt)
                        await sende_sse_event(
                            job_id,
                            "format",
                            "laufend",
                            f"OCR-Reparatur: {ocr_bisher}/{ocr_gesamt} PDFs…",
                            prozent,
                        )

                ocr_ok = 0
                ocr_fehler_count = 0
                for task in ocr_tasks:
                    erfolg, orig_key = await task
                    if erfolg:
                        ergebnisse[orig_key]["re_validiert"] = True
                        ergebnisse[orig_key]["details"] = (
                            f"Nach {pdfa_label} konvertiert (OCR) und validiert."
                        )
                        ocr_ok += 1
                        re_val_ok += 1
                    else:
                        ergebnisse[orig_key]["details"] = (
                            f"Nach {pdfa_label} konvertiert. Nicht PDF/A-konform."
                        )
                        ocr_fehler_count += 1
                        re_val_fehler += 1

                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"OCR-Reparatur: {ocr_ok} von {ocr_gesamt} PDFs per OCR repariert (durchsuchbar).",
                    }
                )
                if ocr_fehler_count > 0:
                    meldungen.append(
                        {
                            "typ": "warnung",
                            "text": f"{ocr_fehler_count} PDF(s) konnten nicht repariert werden.",
                        }
                    )

            meldungen.append(
                {
                    "typ": "info",
                    "text": f"Re-Validierung: {re_val_ok} von {gesamt_total} PDFs sind PDF/A-konform.",
                }
            )

        if sende_sse_event:
            await sende_sse_event(
                job_id,
                "format",
                "laufend",
                f"PDF/A-Konvertierung abgeschlossen: {erfolge}/{len(pdf_dateien)} erfolgreich",
                98,
            )

    elif pdf_dateien and not GS_PFAD:
        meldungen.append(
            {
                "typ": "warnung",
                "text": "Ghostscript nicht installiert — PDFs können nicht nach PDF/A konvertiert werden.",
            }
        )

    # Aufräumen: PDF/A-Dateien ersetzen Originale, Temp-Ordner löschen
    _ersetze_originale(arbeitsverzeichnis, ergebnisse, dateien, meldungen)

    return ergebnisse


def _ersetze_originale(
    arbeitsverzeichnis: Path,
    ergebnisse: dict[str, dict],
    dateien: list[dict],
    meldungen: list[dict],
) -> None:
    """Ersetzt Originaldateien durch PDF/A-Versionen und räumt Temp-Ordner auf."""
    ersetzt = 0

    for orig_name, erg in ergebnisse.items():
        if not erg.get("konvertiert"):
            continue

        pdfa_pfad = Path(erg.get("pdfa_pfad", ""))
        if not pdfa_pfad.exists():
            continue

        # Ziel: PDF/A-Datei auf Root-Ebene mit _pdfa2b Suffix
        ziel_name = Path(orig_name).stem + "_pdfa2b.pdf"
        ziel_pfad = arbeitsverzeichnis / ziel_name

        try:
            # PDF/A-Datei an die Stelle des Originals kopieren
            shutil.copy2(pdfa_pfad, ziel_pfad)
            ersetzt += 1

            # Original-Nicht-PDF-Datei löschen (z.B. .docx, .jpg), falls verschieden
            if orig_name != ziel_name:
                orig_pfad = arbeitsverzeichnis / orig_name
                if orig_pfad.exists():
                    orig_pfad.unlink()
                    logger.debug(
                        "Original entfernt: %s → %s",
                        pseudonymisiere_dateiname(orig_name),
                        pseudonymisiere_dateiname(ziel_name),
                    )
        except Exception as e:
            logger.warning("Konnte %s nicht ersetzen: %s", pseudonymisiere_dateiname(orig_name), e)

    # Temp-Ordner löschen
    for ordner_name in ("_konvertiert", "_pdfa"):
        temp_ordner = arbeitsverzeichnis / ordner_name
        if temp_ordner.exists():
            shutil.rmtree(temp_ordner, ignore_errors=True)

    if ersetzt:
        meldungen.append(
            {"typ": "info", "text": f"{ersetzt} Datei(en) durch PDF/A-Version ersetzt."}
        )
