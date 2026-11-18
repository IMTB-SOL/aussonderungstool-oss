"""Agent 3 – Inhaltsvergleich: DOCX ↔ PDF Seitenanzahl + Textähnlichkeit."""

import argparse
import asyncio
import logging
import re
import zipfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv

load_dotenv()

from utils.logging_setup import pseudonymisiere_dateiname

logger = logging.getLogger(__name__)


async def vergleiche_inhalt(job_id: str, arbeitsverzeichnis: Path) -> dict:
    """
    Vergleicht DOCX- und PDF-Dokumentpaare programmatisch.

    Prüft Seitenanzahl und Textähnlichkeit (difflib.SequenceMatcher).
    Kein LLM — deterministisch, keine Halluzinationen.
    """
    from utils.protokoll import erstelle_protokoll
    from utils.zip_handler import entpacke_lv_bundle_falls_vorhanden

    meldungen: list[dict] = []
    paare_ergebnisse: list[dict] = []
    gesamt_status = "ok"

    try:
        n_bundle = entpacke_lv_bundle_falls_vorhanden(arbeitsverzeichnis)
        if n_bundle:
            meldungen.append(
                {
                    "typ": "info",
                    "text": f"Lieferverzeichnis-Bundle erkannt — {n_bundle} innere Vorgangs-ZIPs entpackt.",
                }
            )

        paare = _finde_dateipaare(arbeitsverzeichnis)

        if not paare:
            meldungen.append(
                {"typ": "info", "text": "Keine DOCX↔PDF-Paare gefunden – Prüfung übersprungen."}
            )
            erstelle_protokoll(job_id, "inhalt", "ok", meldungen, arbeitsverzeichnis)
            return {"status": "ok", "paare": []}

        meldungen.append({"typ": "info", "text": f"{len(paare)} Dokumentpaar(e) gefunden."})

        from utils.agent_runner import sende_sse_event

        await sende_sse_event(
            job_id, "inhalt", "laufend", f"{len(paare)} DOCX↔PDF-Paare gefunden", 15
        )

        # Daten extrahieren (im Thread-Pool)
        def _extrahiere_alle() -> list[dict]:
            ergebnis = []
            for name, pfade in paare.items():
                docx_seiten = _ermittle_docx_seitenanzahl(pfade["docx"])
                docx_text = _extrahiere_docx_text(pfade["docx"])
                docx_bilder = _zaehle_docx_bilder(pfade["docx"])
                pdf_seiten, pdf_text, pdf_bilder = _extrahiere_pdf_daten(pfade["pdf"])
                ergebnis.append(
                    {
                        "name": name,
                        "docx_seiten": docx_seiten,
                        "pdf_seiten": pdf_seiten,
                        "docx_text": docx_text,
                        "pdf_text": pdf_text,
                        "docx_bilder": docx_bilder,
                        "pdf_bilder": pdf_bilder,
                    }
                )
            return ergebnis

        loop = asyncio.get_event_loop()
        paar_daten = await loop.run_in_executor(None, _extrahiere_alle)

        await sende_sse_event(job_id, "inhalt", "laufend", "Vergleiche Inhalt…", 50)

        # Vergleich pro Paar
        for paar in paar_daten:
            vergleich = _vergleiche_paar(
                paar["name"],
                paar["docx_seiten"],
                paar["pdf_seiten"],
                paar["docx_text"],
                paar["pdf_text"],
                paar["docx_bilder"],
                paar["pdf_bilder"],
            )
            paare_ergebnisse.append(vergleich)

            if vergleich["identisch"]:
                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"{paar['name']}: {vergleich['aehnlichkeit']}% Übereinstimmung",
                    }
                )
            else:
                gesamt_status = "fehler"
                abw = " | ".join(vergleich.get("abweichungen", [])[:3])
                meldungen.append({"typ": "warnung", "text": f"{paar['name']}: {abw}"})

        await sende_sse_event(job_id, "inhalt", "laufend", "Vergleich abgeschlossen", 90)

        erstelle_protokoll(job_id, "inhalt", gesamt_status, meldungen, arbeitsverzeichnis)
        return {"status": gesamt_status, "paare": paare_ergebnisse}

    except Exception as e:
        logger.exception("[Job %s] Unerwarteter Fehler im Agent 'inhalt': %s", job_id, e)
        meldungen.append({"typ": "fehler", "text": f"Unerwarteter Fehler: {e}"})
        erstelle_protokoll(job_id, "inhalt", "fehler", meldungen, arbeitsverzeichnis)
        return {"status": "fehler", "paare": paare_ergebnisse}


# ---------------------------------------------------------------------------
# Dateipaare finden
# ---------------------------------------------------------------------------


def _finde_dateipaare(verzeichnis: Path) -> dict[str, dict[str, Path]]:
    """Findet DOCX↔PDF-Paare eines Pakets.

    Zwei Quellen, in dieser Reihenfolge:

    1. Das XDOMEA-XML. Führt ein <Dokument> genau eine DOCX und genau eine
       PDF, sind das Original und Rendition desselben Dokuments — unabhängig
       davon, wie die Dateien heißen. Nur so lassen sich Exporte paaren, die
       Original und Rendition verschiedene UUIDs geben (Vogtlandkreis).
       Bündelt ein <Dokument> dagegen mehrere fachlich verschiedene Anlagen
       (regisafe), bleibt es bewusst ungepaart statt falsch verglichen.
    2. Der Dateiname, für alles, was das XML nicht abdeckt. Beide gängigen
       Konventionen zählen: "Bericht.docx" ↔ "Bericht.pdf" und — bei
       DMS-Exporten die Regel — "Bericht.docx" ↔ "Bericht.docx.pdf".

    Dateien, die Schritt 1 bereits zugeordnet hat, werden in Schritt 2 nicht
    erneut gepaart.
    """
    index = _index_dateien(verzeichnis)
    paare = _paare_aus_xml(verzeichnis, index)
    _ergaenze_paare_ueber_namen(index, paare)
    return paare


def _index_dateien(verzeichnis: Path) -> dict[str, list[Path]]:
    """Basisname (kleingeschrieben) → alle Pfade mit diesem Namen."""
    index: dict[str, list[Path]] = defaultdict(list)
    for datei in verzeichnis.rglob("*"):
        if not datei.is_file():
            continue
        # macOS Resource Forks und versteckte Dateien ignorieren
        if datei.name.startswith("._") or datei.name.startswith("."):
            continue
        index[datei.name.lower()].append(datei)
    return index


def _loese_datei(dateiname: str, xml_pfad: Path, index: dict[str, list[Path]]) -> Path | None:
    """Löst einen im XML referenzierten Dateinamen zu einem echten Pfad auf.

    Kommt der Name mehrfach vor (Sammel-Pakete mit mehreren Containern), gilt
    die Datei aus der Umgebung der referenzierenden XML — sonst würde die
    Variante eines fremden Containers verglichen.
    """
    kandidaten = index.get(Path(dateiname).name.lower(), [])
    if not kandidaten:
        return None
    if len(kandidaten) == 1:
        return kandidaten[0]

    gleiches_verzeichnis = [p for p in kandidaten if p.parent == xml_pfad.parent]
    if gleiches_verzeichnis:
        return gleiches_verzeichnis[0]
    im_teilbaum = [p for p in kandidaten if p.is_relative_to(xml_pfad.parent)]
    return im_teilbaum[0] if im_teilbaum else kandidaten[0]


def _paare_aus_xml(verzeichnis: Path, index: dict[str, list[Path]]) -> dict[str, dict[str, Path]]:
    """Leitet Paare aus der Dokumentstruktur der XDOMEA-XMLs ab."""
    from utils.xdomea_parser import finde_xdomea_xmls, sicher_parsen

    paare: dict[str, dict[str, Path]] = {}
    for xml_pfad in finde_xdomea_xmls(verzeichnis):
        try:
            wurzel = sicher_parsen(xml_pfad).getroot()
        except Exception as e:
            logger.debug(
                "Paarbildung: %s nicht lesbar (%s)", pseudonymisiere_dateiname(xml_pfad.name), e
            )
            continue

        for dok in wurzel.findall(".//{*}Dokument"):
            namen = [d.text.strip() for d in dok.findall(".//{*}Dateiname") if d.text]
            docx = [n for n in namen if n.lower().endswith(".docx")]
            pdf = [n for n in namen if n.lower().endswith(".pdf")]
            # Nur eindeutige Original/Rendition-Konstellationen paaren.
            if len(docx) != 1 or len(pdf) != 1:
                continue
            docx_pfad = _loese_datei(docx[0], xml_pfad, index)
            pdf_pfad = _loese_datei(pdf[0], xml_pfad, index)
            if docx_pfad is None or pdf_pfad is None:
                continue
            paare.setdefault(Path(docx[0]).stem, {"docx": docx_pfad, "pdf": pdf_pfad})
    return paare


def _ergaenze_paare_ueber_namen(
    index: dict[str, list[Path]], paare: dict[str, dict[str, Path]]
) -> None:
    """Ergänzt Paare nach Dateinamen — nur für noch nicht zugeordnete Dateien."""
    vergeben = {p for paar in paare.values() for p in paar.values()}

    docx_dateien: dict[str, Path] = {}
    pdf_nach_stamm: dict[str, Path] = {}
    pdf_nach_vollname: dict[str, Path] = {}

    for pfade in index.values():
        for datei in pfade:
            if datei in vergeben:
                continue
            suffix = datei.suffix.lower()
            if suffix == ".docx":
                docx_dateien.setdefault(datei.stem, datei)
            elif suffix == ".pdf":
                pdf_nach_stamm.setdefault(datei.stem, datei)
                # "Bericht.docx.pdf" → Schlüssel "Bericht.docx"
                pdf_nach_vollname.setdefault(datei.name[: -len(datei.suffix)], datei)

    for stamm, docx in docx_dateien.items():
        if stamm in paare:
            continue
        pdf = pdf_nach_vollname.get(docx.name) or pdf_nach_stamm.get(stamm)
        if pdf is not None:
            paare[stamm] = {"docx": docx, "pdf": pdf}


# ---------------------------------------------------------------------------
# Extraktion
# ---------------------------------------------------------------------------


def _normalisiere_text(text: str) -> str:
    """Normalisiert Text für Vergleich: Whitespace vereinheitlichen, Kleinbuchstaben."""
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = text.replace("\u00ad", "")  # Soft Hyphen
    text = text.replace("\u200b", "")  # Zero-Width Space
    text = text.replace("\ufeff", "")  # BOM
    return text


def _ermittle_docx_seitenanzahl(pfad: Path) -> int | None:
    """Ermittelt die Seitenanzahl einer DOCX-Datei.

    Strategie:
    1. docProps/app.xml prüfen (schnell, aber oft nicht gesetzt)
    2. Fallback: LibreOffice headless konvertiert zu PDF und zählt Seiten
    """
    # 1. Schneller Weg: app.xml
    try:
        from utils.xdomea_parser import sicher_parsen

        with zipfile.ZipFile(pfad) as zf:
            if "docProps/app.xml" in zf.namelist():
                app_xml = sicher_parsen(zf.open("docProps/app.xml"))
                ns = {
                    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
                }
                pages_el = app_xml.find(".//ep:Pages", ns)
                if pages_el is not None and pages_el.text:
                    seiten = int(pages_el.text)
                    if seiten > 0:
                        return seiten
    except Exception:
        pass

    # 2. Fallback: LibreOffice headless → PDF → Seiten zählen
    try:
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp_dir, str(pfad)],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                pdf_dateien = list(Path(tmp_dir).glob("*.pdf"))
                if pdf_dateien:
                    with pdfplumber.open(str(pdf_dateien[0])) as pdf:
                        return len(pdf.pages)
    except Exception as e:
        logger.debug(
            "DOCX-Seitenanzahl via LibreOffice fehlgeschlagen für %s: %s",
            pseudonymisiere_dateiname(pfad.name),
            e,
        )

    return None


def _extrahiere_docx_text(pfad: Path) -> str:
    """Extrahiert den gesamten Text aus einer DOCX-Datei via XML-Parsing.

    Erfasst allen Text aus: Absätzen, Tabellen, Header/Footer,
    Content Controls (Formularfelder), Textboxen und Feldwerten.
    """
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    teile: list[str] = []

    try:
        with zipfile.ZipFile(pfad) as zf:
            # Alle relevanten XML-Dateien im DOCX durchsuchen
            xml_dateien = [
                "word/document.xml",  # Haupttext
                "word/header1.xml",
                "word/header2.xml",
                "word/header3.xml",
                "word/footer1.xml",
                "word/footer2.xml",
                "word/footer3.xml",
            ]
            for xml_name in xml_dateien:
                if xml_name not in zf.namelist():
                    continue
                try:
                    from utils.xdomea_parser import sicher_parsen

                    baum = sicher_parsen(zf.open(xml_name))
                    # Alle w:t Elemente = jeder Textknoten in Word
                    for t_el in baum.findall(".//w:t", ns):
                        if t_el.text:
                            teile.append(t_el.text)
                except Exception:
                    pass

        return " ".join(teile)
    except Exception as e:
        logger.warning(
            "DOCX-Textextraktion fehlgeschlagen für %s: %s", pseudonymisiere_dateiname(pfad.name), e
        )
        return ""


def _zaehle_docx_bilder(pfad: Path) -> int:
    """Zählt eingebettete Bilder in einer DOCX-Datei (via word/media/ im ZIP)."""
    bild_endungen = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".emf", ".wmf"}
    try:
        with zipfile.ZipFile(pfad) as zf:
            return sum(
                1
                for name in zf.namelist()
                if name.startswith("word/media/") and Path(name).suffix.lower() in bild_endungen
            )
    except Exception as e:
        logger.debug(
            "DOCX-Bilder nicht zählbar für %s: %s", pseudonymisiere_dateiname(pfad.name), e
        )
        return 0


def _extrahiere_pdf_daten(pfad: Path) -> tuple[int, str, int]:
    """Extrahiert Seitenanzahl, Text und Bilderanzahl aus einer PDF-Datei."""
    try:
        with pdfplumber.open(str(pfad)) as pdf:
            seitenanzahl = len(pdf.pages)
            texte = []
            bild_ids: set[str] = set()
            for seite in pdf.pages:
                try:
                    text = seite.extract_text()
                    if text:
                        texte.append(text)
                except Exception:
                    pass
                try:
                    for img in seite.images or []:
                        # Deduplizieren über Position+Größe
                        bild_ids.add(
                            f"{img.get('x0', '')}-{img.get('top', '')}-{img.get('width', '')}-{img.get('height', '')}"
                        )
                except Exception:
                    pass
            return seitenanzahl, "\n".join(texte), len(bild_ids)
    except Exception as e:
        logger.warning(
            "PDF-Extraktion fehlgeschlagen für %s: %s", pseudonymisiere_dateiname(pfad.name), e
        )
        return 0, "", 0


# ---------------------------------------------------------------------------
# Vergleich
# ---------------------------------------------------------------------------


def _extrahiere_datumsangaben(text: str) -> list[str]:
    """Extrahiert alle Datumsangaben aus einem Text (DD.MM.YYYY, DD.MM.YY, YYYY-MM-DD)."""
    muster = [
        r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b",  # 22.04.2026
        r"\b(\d{1,2}\.\d{1,2}\.\d{2})\b",  # 22.04.26
        r"\b(\d{4}-\d{2}-\d{2})\b",  # 2026-04-22
        r"\b(\d{1,2}/\d{1,2}/\d{4})\b",  # 22/04/2026
    ]
    gefunden: list[str] = []
    for m in muster:
        gefunden.extend(re.findall(m, text))
    return sorted(set(gefunden))


def _vergleiche_datumsangaben(docx_daten: list[str], pdf_daten: list[str]) -> list[str]:
    """Vergleicht Datumsangaben aus DOCX und PDF."""
    if not docx_daten and not pdf_daten:
        return []

    nur_in_docx = set(docx_daten) - set(pdf_daten)
    nur_in_pdf = set(pdf_daten) - set(docx_daten)

    abweichungen: list[str] = []
    if nur_in_docx or nur_in_pdf:
        teile = []
        if nur_in_docx:
            teile.append(f"nur in DOCX: {', '.join(sorted(nur_in_docx))}")
        if nur_in_pdf:
            teile.append(f"nur in PDF: {', '.join(sorted(nur_in_pdf))}")
        abweichungen.append(f"Datumsangaben weichen ab — {'; '.join(teile)}")

    return abweichungen


def _text_diff_segmente(
    docx_norm: str, pdf_norm: str, max_segmente: int = 60, max_wort_lauf: int = 40
) -> list[dict]:
    """Erzeugt einen wortweisen Diff zwischen DOCX- und PDF-Text.

    Liefert Segmente, mit denen das Frontend transparent zeigen kann, WO der
    Text abweicht:
      - {"typ": "gleich",   "text": …}  — übereinstimmender Abschnitt (gekürzt)
      - {"typ": "nur_docx", "text": …}  — nur im DOCX (im PDF entfallen)
      - {"typ": "nur_pdf",  "text": …}  — nur im PDF (im DOCX nicht vorhanden)
    """
    docx_w = docx_norm.split()
    pdf_w = pdf_norm.split()
    if not docx_w or not pdf_w:
        return []

    def kuerze(woerter: list[str]) -> str:
        if len(woerter) > max_wort_lauf:
            haelfte = max_wort_lauf // 2
            return " ".join(woerter[:haelfte]) + " … " + " ".join(woerter[-haelfte:])
        return " ".join(woerter)

    segmente: list[dict] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, docx_w, pdf_w).get_opcodes():
        if tag == "equal":
            segmente.append({"typ": "gleich", "text": kuerze(docx_w[i1:i2])})
        elif tag == "delete":
            segmente.append({"typ": "nur_docx", "text": kuerze(docx_w[i1:i2])})
        elif tag == "insert":
            segmente.append({"typ": "nur_pdf", "text": kuerze(pdf_w[j1:j2])})
        elif tag == "replace":
            segmente.append({"typ": "nur_docx", "text": kuerze(docx_w[i1:i2])})
            segmente.append({"typ": "nur_pdf", "text": kuerze(pdf_w[j1:j2])})
        if len(segmente) >= max_segmente:
            segmente.append({"typ": "info", "text": "… weitere Unterschiede gekürzt"})
            break
    return segmente


def _vergleiche_paar(
    name: str,
    docx_seiten: int | None,
    pdf_seiten: int,
    docx_text: str,
    pdf_text: str,
    docx_bilder: int = 0,
    pdf_bilder: int = 0,
) -> dict:
    """Vergleicht Seitenanzahl, Bilder und Textähnlichkeit."""
    abweichungen: list[str] = []

    # Seitenanzahl
    if docx_seiten is not None and pdf_seiten and docx_seiten != pdf_seiten:
        abweichungen.append(f"Seitenanzahl: DOCX {docx_seiten}, PDF {pdf_seiten}")

    # Bilder — nur melden wenn PDF weniger hat (DOCX ist das Original)
    if docx_bilder > 0 and pdf_bilder < docx_bilder:
        abweichungen.append(
            f"Bilder: DOCX hat {docx_bilder}, PDF hat nur {pdf_bilder} — Abbildung(en) fehlen"
        )

    # Datumsangaben gezielt vergleichen
    docx_daten = _extrahiere_datumsangaben(docx_text)
    pdf_daten = _extrahiere_datumsangaben(pdf_text)
    datum_abweichungen = _vergleiche_datumsangaben(docx_daten, pdf_daten)
    if datum_abweichungen:
        abweichungen.extend(datum_abweichungen)

    # Textähnlichkeit
    docx_norm = _normalisiere_text(docx_text)
    pdf_norm = _normalisiere_text(pdf_text)

    if docx_norm and pdf_norm:
        aehnlichkeit = SequenceMatcher(None, docx_norm, pdf_norm).ratio()
        aehnlichkeit_prozent = round(aehnlichkeit * 100)
    elif not docx_norm and not pdf_norm:
        aehnlichkeit_prozent = 100  # Beide leer = identisch
    else:
        aehnlichkeit_prozent = 0

    docx_woerter = len(docx_norm.split()) if docx_norm else 0
    pdf_woerter = len(pdf_norm.split()) if pdf_norm else 0

    if aehnlichkeit_prozent < 90:
        if docx_woerter == 0 and pdf_woerter == 0:
            abweichungen.append("Kein Text extrahierbar aus beiden Dokumenten")
        elif docx_woerter == 0:
            abweichungen.append(f"DOCX: kein Text extrahierbar (PDF hat {pdf_woerter} Wörter)")
        elif pdf_woerter == 0:
            abweichungen.append(
                f"PDF: kein Text extrahierbar (DOCX hat {docx_woerter} Wörter) — möglicherweise gescannt"
            )
        else:
            abweichungen.append(
                f"Textähnlichkeit nur {aehnlichkeit_prozent}% (DOCX: {docx_woerter} Wörter, PDF: {pdf_woerter} Wörter)"
            )

    identisch = len(abweichungen) == 0

    # Wort-Diff erzeugen, sobald es echte Wort-Unterschiede gibt — auch bei hoher
    # Gesamtähnlichkeit (z.B. nur ein geändertes Datum). Nur behalten, wenn der
    # Diff tatsächlich abweichende Stellen enthält; bei identischem Text (z.B.
    # reiner Bild-Unterschied) bleibt er leer → kein "Unterschiede"-Button.
    text_diff: list[dict] = []
    if not identisch and docx_woerter > 0 and pdf_woerter > 0:
        segmente = _text_diff_segmente(docx_norm, pdf_norm)
        if any(s["typ"] in ("nur_docx", "nur_pdf") for s in segmente):
            text_diff = segmente

    return {
        "name": name,
        "identisch": identisch,
        "aehnlichkeit": aehnlichkeit_prozent,
        "begruendung": f"{aehnlichkeit_prozent}% Textübereinstimmung."
        if identisch
        else f"{len(abweichungen)} Abweichung(en) gefunden.",
        "abweichungen": abweichungen,
        "text_diff": text_diff,
        "details": {
            "docx_seitenanzahl": docx_seiten,
            "pdf_seitenanzahl": pdf_seiten,
            "aehnlichkeit_prozent": aehnlichkeit_prozent,
            "docx_woerter": docx_woerter,
            "pdf_woerter": pdf_woerter,
            "docx_bilder": docx_bilder,
            "pdf_bilder": pdf_bilder,
            "docx_daten": docx_daten,
            "pdf_daten": pdf_daten,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 3: Inhaltsvergleich")
    parser.add_argument("--job-id", required=True, help="Job-ID")
    parser.add_argument("--pfad", default=".", help="Arbeitsverzeichnis (entpacktes ZIP)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    ergebnis = asyncio.run(vergleiche_inhalt(args.job_id, Path(args.pfad)))
    print(ergebnis)
