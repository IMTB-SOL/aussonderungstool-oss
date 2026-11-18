"""Zentrale Textextraktion aus DOCX-, PDF-, PPTX- und Bilddateien."""

import logging
import os
from pathlib import Path

import pytesseract
from docx import Document
from PIL import Image
from pptx import Presentation

from utils.pdf_extraktion import extrahiere_pdf_text

logger = logging.getLogger(__name__)

BILD_SUFFIXE = {".jpeg", ".jpg", ".png", ".tiff", ".tif", ".bmp", ".gif"}


def extrahiere_eine_datei(datei: Path) -> tuple[str, str, list[dict]]:
    """Extrahiert Text aus genau einer Datei.

    Modul-globale Funktion — serialisierbar für ProcessPoolExecutor.

    Returns:
        (dateiname, text, meldungen) — meldungen sind neue Protokoll-Einträge,
        text ist bereits mit "--- {dateiname} ---"-Header versehen (oder leer).
    """
    dateiname = datei.name
    meldungen: list[dict] = []
    suffix = datei.suffix.lower()
    text = ""

    if suffix == ".docx":
        try:
            doc = Document(str(datei))
            inhalt = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            if inhalt:
                text = f"--- {dateiname} ---\n{inhalt}"
                meldungen.append({"typ": "info", "text": f"Text extrahiert: {dateiname}"})
        except Exception as e:
            meldungen.append(
                {"typ": "warnung", "text": f"DOCX-Extraktion fehlgeschlagen: {dateiname} ({e})"}
            )

    elif suffix == ".pdf":
        inhalt, pdf_status = extrahiere_pdf_text(datei)
        text = f"--- {dateiname} ---\n{inhalt}"
        if pdf_status == "ok":
            meldungen.append({"typ": "info", "text": f"Text extrahiert: {dateiname}"})
        elif pdf_status == "warnung":
            meldungen.append(
                {"typ": "warnung", "text": f"Text über repair extrahiert: {dateiname}"}
            )
        else:
            meldungen.append(
                {
                    "typ": "warnung",
                    "text": f"Malformierte PDF dokumentiert: {dateiname} ({pdf_status})",
                }
            )

    elif suffix == ".pptx":
        try:
            prs = Presentation(str(datei))
            folien_texte: list[str] = []
            for i, folie in enumerate(prs.slides, 1):
                folie_text: list[str] = []
                for shape in folie.shapes:
                    if shape.has_text_frame:
                        for absatz in shape.text_frame.paragraphs:
                            if absatz.text.strip():
                                folie_text.append(absatz.text.strip())
                if folie_text:
                    folien_texte.append(f"[Folie {i}]\n" + "\n".join(folie_text))
            if folien_texte:
                text = f"--- {dateiname} ---\n" + "\n\n".join(folien_texte)
                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"Text extrahiert: {dateiname} ({len(prs.slides)} Folien)",
                    }
                )
            else:
                text = f"--- {dateiname} ---\n[Präsentation ohne extrahierbaren Text]"
                meldungen.append({"typ": "warnung", "text": f"Kein Text in PPTX: {dateiname}"})
        except Exception as e:
            meldungen.append(
                {"typ": "warnung", "text": f"PPTX-Extraktion fehlgeschlagen: {dateiname} ({e})"}
            )

    elif suffix in BILD_SUFFIXE:
        ocr_lang = os.getenv("OCR_LANG", "deu")
        try:
            bild = Image.open(str(datei))
            ocr_text = pytesseract.image_to_string(bild, lang=ocr_lang).strip()
            if ocr_text:
                text = f"--- {dateiname} ---\n[OCR-Text aus Bilddatei]\n{ocr_text}"
                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"OCR-Text extrahiert: {dateiname} ({len(ocr_text)} Zeichen)",
                    }
                )
            else:
                text = f"--- {dateiname} ---\n[Bilddatei — kein Text per OCR erkannt]"
                meldungen.append(
                    {"typ": "info", "text": f"Bilddatei ohne erkennbaren Text: {dateiname}"}
                )
        except Exception as e:
            text = f"--- {dateiname} ---\n[Bilddatei — OCR fehlgeschlagen]"
            meldungen.append({"typ": "warnung", "text": f"OCR fehlgeschlagen für {dateiname}: {e}"})

    return dateiname, text, meldungen


def sammle_dokumenttexte(
    verzeichnis: Path, meldungen: list[dict], fortschritt_callback=None
) -> str:
    """Sammelt Texte aus allen unterstützten Dateien im Verzeichnis — parallel.

    Verteilt die per-Datei-Extraktion über einen ProcessPoolExecutor
    (Worker-Anzahl aus EXTRAKTION_PARALLEL-Env, Default = os.cpu_count()).
    Fällt bei Pool-Start-Fehler geräuschlos auf eine sequenzielle Verarbeitung
    zurück.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    alle_dateien = [d for d in sorted(verzeichnis.rglob("*")) if d.is_file()]
    gesamt = len(alle_dateien)
    if gesamt == 0:
        return ""

    standard = os.cpu_count() or 4
    worker_n = max(1, int(os.getenv("EXTRAKTION_PARALLEL", str(standard))))
    # Mehr Worker als Dateien bringt nichts.
    worker_n = min(worker_n, gesamt)

    # Index pro Datei merken, damit wir am Ende die Original-Reihenfolge wiederherstellen.
    indexierte: list[tuple[int, Path]] = list(enumerate(alle_dateien))

    ergebnisse: dict[int, str] = {}
    fertig = 0

    try:
        with ProcessPoolExecutor(max_workers=worker_n) as pool:
            future_to_index = {
                pool.submit(extrahiere_eine_datei, datei): idx for idx, datei in indexierte
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    _name, text, neue_meldungen = future.result()
                except Exception as e:
                    # Im Idealfall fängt extrahiere_eine_datei alles ab; falls doch
                    # eine Ausnahme durchkommt, vermerken und weitermachen.
                    datei = indexierte[idx][1]
                    text = ""
                    neue_meldungen = [
                        {
                            "typ": "warnung",
                            "text": f"Extraktion fehlgeschlagen: {datei.name} ({e})",
                        }
                    ]
                if text:
                    ergebnisse[idx] = text
                meldungen.extend(neue_meldungen)
                fertig += 1
                if fortschritt_callback:
                    fortschritt_callback(fertig, gesamt)
    except Exception as e:
        # ProcessPool konnte nicht starten — Fallback auf sequenziell.
        logger.warning(
            "ProcessPool für Textextraktion nicht verfügbar (%s), sequenzieller Fallback.",
            e,
        )
        meldungen.append(
            {
                "typ": "warnung",
                "text": "Parallele Textextraktion nicht verfügbar — sequenzieller Fallback.",
            }
        )
        for idx, datei in indexierte:
            _name, text, neue_meldungen = extrahiere_eine_datei(datei)
            if text:
                ergebnisse[idx] = text
            meldungen.extend(neue_meldungen)
            fertig += 1
            if fortschritt_callback and fertig % 10 == 0:
                fortschritt_callback(fertig, gesamt)

    # Texte in Original-Reihenfolge zusammenfügen.
    texte_in_reihenfolge = [ergebnisse[idx] for idx, _ in indexierte if idx in ergebnisse]
    return "\n\n".join(texte_in_reihenfolge)


def kuerze_intelligent(text: str, max_zeichen: int) -> str:
    """Kürzt Text gleichmäßig pro Dokument — JEDES Dokument bleibt vertreten.

    Dokumente werden anhand des Trennzeichens '--- dateiname ---' identifiziert.
    Pro Dokument wird Anfang, Mitte und Ende anteilig behalten, damit
    Unterschriften am Ende erhalten bleiben.
    """
    teile = text.split("\n\n--- ")
    if len(teile) <= 1:
        return text[:max_zeichen]

    erster = teile[0]
    rest = teile[1:]
    anz_dokumente = len(rest)

    overhead = 100 + anz_dokumente * 20
    verfuegbar = max_zeichen - min(len(erster), 2000) - overhead
    pro_dok = max(300, verfuegbar // max(anz_dokumente, 1))

    gekuerzte = [erster[:2000]]
    for teil in rest:
        zeilen = teil.split("\n", 1)
        dateiname = zeilen[0] if zeilen else ""
        inhalt = zeilen[1] if len(zeilen) > 1 else ""

        budget_inhalt = pro_dok - len(dateiname) - 10
        if len(inhalt) > budget_inhalt and budget_inhalt > 100:
            anfang_len = int(budget_inhalt * 0.4)
            ende_len = int(budget_inhalt * 0.3)
            mitte_start = len(inhalt) // 2 - int(budget_inhalt * 0.15)
            mitte_len = int(budget_inhalt * 0.3)
            anfang = inhalt[:anfang_len]
            mitte = (
                inhalt[max(0, mitte_start) : mitte_start + mitte_len]
                if mitte_start > anfang_len
                else ""
            )
            ende = inhalt[-ende_len:]
            gekuerzt_text = (
                f"{anfang}\n[… Mitte gekürzt …]\n{mitte}\n[… gekürzt …]\n{ende}"
                if mitte
                else f"{anfang}\n[… gekürzt …]\n{ende}"
            )
            gekuerzte.append(
                f"--- {dateiname}\n{gekuerzt_text} "
                f"[gekürzt auf ~{budget_inhalt} von {len(inhalt)} Zeichen]"
            )
        elif len(inhalt) > budget_inhalt:
            gekuerzte.append(f"--- {dateiname}\n{inhalt[:budget_inhalt]}… [gekürzt]")
        else:
            gekuerzte.append(f"--- {dateiname}\n{inhalt}")

    logger.info(
        "Intelligente Kürzung: %d Dokumente, %d Zeichen/Dok, Gesamt ~%d Zeichen",
        anz_dokumente,
        pro_dok,
        sum(len(t) for t in gekuerzte),
    )
    return "\n\n".join(gekuerzte)
