"""Hilfsfunktion zur robusten PDF-Textextraktion mit Ghostscript-Reparatur."""

import logging
import shutil
import struct
import subprocess
from pathlib import Path

import pdfplumber

from utils.logging_setup import pseudonymisiere_dateiname

logger = logging.getLogger(__name__)

# Maximale Zeit pro PDF-Extraktion (Sekunden)
PDF_TIMEOUT = 30


def extrahiere_pdf_text(datei_pfad: Path) -> tuple[str, str]:
    """
    Extrahiert Text aus einer PDF-Datei.

    Versuch 1: Standard pdfplumber, seitenweise.
    Versuch 2: pdfplumber mit repair=True.
    Versuch 3: Ghostscript-Reparatur → pdfplumber auf reparierte Datei.

    Returns:
        (text, status) – status ist "ok", "warnung" oder "fehlgeschlagen: <grund>".
    """
    # Versuch 0: pypdf — schnell für sauber generierte Text-PDFs.
    # Macht keine Layout-Analyse, daher 5–10× schneller als pdfplumber für
    # den Standardfall. Bei Misserfolg / leerem Ergebnis / Exception fällt
    # der Code auf den pdfplumber-Pfad zurück.
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(datei_pfad))
        teile: list[str] = []
        for seite in reader.pages:
            try:
                t = seite.extract_text() or ""
                if t.strip():
                    teile.append(t)
            except Exception:
                continue
        if teile:
            return "\n".join(teile), "ok"
    except Exception as e:
        logger.debug(
            "pypdf-Versuch für %s erfolglos (%s), fällt auf pdfplumber zurück",
            pseudonymisiere_dateiname(datei_pfad.name),
            e,
        )

    # Versuch 1: normal, seitenweise Fehlerbehandlung (mit Timeout)
    try:
        teile: list[str] = []
        with pdfplumber.open(str(datei_pfad)) as pdf:
            for seite in pdf.pages:
                try:
                    t = seite.extract_text()
                    if t:
                        teile.append(t)
                except Exception:
                    continue
        if teile:
            return "\n".join(teile), "ok"
    except struct.error:
        logger.warning(
            "struct.error bei %s – versuche repair=True", pseudonymisiere_dateiname(datei_pfad.name)
        )
    except Exception as e:
        logger.warning(
            "PDF-Extraktion fehlgeschlagen bei %s: %s",
            pseudonymisiere_dateiname(datei_pfad.name),
            e,
        )

    # Versuch 2: repair=True für malformierte PDFs (mit Timeout — kann sehr langsam sein)
    try:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FuturesTimeout

        def _repair_extraktion():
            t_teile = []
            with pdfplumber.open(str(datei_pfad), repair=True) as pdf:
                for seite in pdf.pages:
                    try:
                        t = seite.extract_text()
                        if t:
                            t_teile.append(t)
                    except Exception:
                        continue
            return t_teile

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_repair_extraktion)
            teile = future.result(timeout=PDF_TIMEOUT)

        if teile:
            return "\n".join(teile), "warnung"
    except FuturesTimeout:
        logger.warning(
            "repair=True Timeout bei %s (> %ds) — überspringe",
            pseudonymisiere_dateiname(datei_pfad.name),
            PDF_TIMEOUT,
        )
    except Exception as e:
        logger.warning(
            "repair=True fehlgeschlagen bei %s: %s", pseudonymisiere_dateiname(datei_pfad.name), e
        )

    # Versuch 3: Ghostscript-Reparatur
    gs_pfad = shutil.which("gs")
    if gs_pfad:
        try:
            repariert = datei_pfad.parent / f"_gs_repariert_{datei_pfad.name}"
            ergebnis = subprocess.run(
                [
                    gs_pfad,
                    "-dNOPAUSE",
                    "-dBATCH",
                    "-dQUIET",
                    "-sDEVICE=pdfwrite",
                    f"-sOutputFile={repariert}",
                    str(datei_pfad),
                ],
                capture_output=True,
                timeout=30,
            )
            if repariert.exists() and repariert.stat().st_size > 100:
                teile = []
                with pdfplumber.open(str(repariert)) as pdf:
                    for seite in pdf.pages:
                        try:
                            t = seite.extract_text()
                            if t:
                                teile.append(t)
                        except Exception:
                            continue
                repariert.unlink(missing_ok=True)
                if teile:
                    logger.info(
                        "PDF per Ghostscript repariert und Text extrahiert: %s",
                        pseudonymisiere_dateiname(datei_pfad.name),
                    )
                    return "\n".join(teile), "warnung"
            repariert.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(
                "Ghostscript-Reparatur fehlgeschlagen bei %s: %s",
                pseudonymisiere_dateiname(datei_pfad.name),
                e,
            )

    return (
        "",
        f"fehlgeschlagen (Alle Reparaturversuche fehlgeschlagen bei {pseudonymisiere_dateiname(datei_pfad.name)})",
    )
