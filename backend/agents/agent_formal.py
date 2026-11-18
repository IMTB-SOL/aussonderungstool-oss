"""Agent 1 – Formale Prüfung: XDOMEA-Validierung und Vollständigkeit."""

import argparse
import asyncio
import hashlib
import logging
from pathlib import Path

from dotenv import load_dotenv
from lxml import etree

load_dotenv()

from utils.job_dateien import finde_datei
from utils.logging_setup import pseudonymisiere_dateiname

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
XSD_VERZEICHNIS = ASSETS_DIR / "xsd"

XSD_DATEIEN = {
    "2.1": XSD_VERZEICHNIS / "v2.1" / "xdomea-Nachrichten-VBS.xsd",
    "2.2": XSD_VERZEICHNIS / "v2.2" / "xdomea-Nachrichten-VBS.xsd",
    "2.3": XSD_VERZEICHNIS / "v2.3" / "xdomea-Nachrichten-AussonderungDurchfuehren.xsd",
    "2.4": XSD_VERZEICHNIS / "v2.4" / "xdomea-Nachrichten-VBS.xsd",
    "3.0": XSD_VERZEICHNIS
    / "v3.0"
    / "xdomea_3-0-0_XML-Schemata"
    / "xdomea-Nachrichten-AussonderungDurchfuehren.xsd",
}

# Bekannte XDOMEA-Versionen – werden erkannt aber evtl. ohne XSD-Validierung verarbeitet
BEKANNTE_VERSIONEN = {"2.1", "2.2", "2.3", "2.4", "3.0"}


async def pruefe_formal(job_id: str, arbeitsverzeichnis: Path) -> dict:
    """
    Führt die formale Prüfung für den angegebenen Job durch.

    Schritte:
    1. XDOMEA-XML-Dateien im Arbeitsverzeichnis finden
    2. XDOMEA-Version auslesen
    3. XSD-Validierung durchführen
    4. Referenzierte Dateien vs. vorhandene Dateien abgleichen
    5. Protokoll-XML erstellen

    Args:
        job_id: Job-ID.
        arbeitsverzeichnis: Pfad zum entpackten ZIP-Inhalt.

    Returns:
        Ergebnis-Dictionary mit Feldern: status, fehler, xdomea_version,
        xsd_fehler, referenz_abgleich.
    """
    from utils.agent_runner import sende_sse_event
    from utils.protokoll import erstelle_protokoll
    from utils.xdomea_parser import (
        lese_referenzierte_dateien,
        lese_xdomea_version,
        validiere_gegen_xsd,
    )

    meldungen: list[dict] = []
    fehler: list[str] = []
    gesamt_status = "ok"
    xml_reparierbar = False  # Wird True wenn XML repariert werden könnte
    reparaturoptionen: dict = {"encoding": False, "referenzen": [], "xsd": []}

    try:
        # 1. XML-Dateien finden
        xml_dateien = list(arbeitsverzeichnis.rglob("*.xml"))
        xml_dateien = [x for x in xml_dateien if not x.name.startswith("protokoll_")]

        # Alle Dateien im ZIP zählen
        alle_dateien = [
            d for d in arbeitsverzeichnis.rglob("*") if d.is_file() and d.suffix != ".xml"
        ]
        await sende_sse_event(
            job_id,
            "formal",
            "laufend",
            f"{len(alle_dateien)} Dateien + {len(xml_dateien)} XML im ZIP",
            10,
        )

        # ---- DOMEA-Lieferverzeichnis-Sonderfall ----
        # Erkannt am Root-Tag 'domeaAussonderungListen'; statt XDOMEA-XSD-Validierung
        # prüfen wir Vollständigkeit der referenzierten Vorgangs-ZIPs und ihre MD5-Hashes.
        lv_xml = _finde_lieferverzeichnis(xml_dateien)
        if lv_xml is not None:
            return await _pruefe_lieferverzeichnis(
                job_id,
                lv_xml,
                arbeitsverzeichnis,
                meldungen,
                fehler,
                sende_sse_event,
                erstelle_protokoll,
            )

        if not xml_dateien:
            fehler.append("Keine XML-Datei im ZIP gefunden.")
            meldungen.append({"typ": "fehler", "text": "Keine XML-Datei im ZIP gefunden."})
            erstelle_protokoll(job_id, "formal", "fehler", meldungen, arbeitsverzeichnis)
            return {
                "status": "fehler",
                "fehler": fehler,
                "xdomea_version": None,
                "xsd_fehler": [],
                "referenz_abgleich": {},
            }

        meldungen.append({"typ": "info", "text": f"{len(xml_dateien)} XML-Datei(en) gefunden."})

        alle_xsd_fehler: list[str] = []
        alle_referenz_ergebnisse: dict[str, dict] = {}
        xdomea_version: str | None = None

        # Global referenzierte Dateinamen über ALLE XMLs sammeln. Bei Paketen
        # mit mehreren XMLs (z.B. enaio Originalformat + PDFA-Rendition)
        # referenziert jede XML nur ihren eigenen Satz; eine pro-XML-Prüfung
        # würde die Dateien der jeweils anderen XML fälschlich als "überzählig"
        # melden. Für den Überzählig-Abgleich zählt daher die Vereinigung.
        global_referenzierte: set[str] = set()
        for xml_pfad in xml_dateien:
            try:
                global_referenzierte.update(lese_referenzierte_dateien(xml_pfad))
            except RuntimeError:
                pass

        for xml_pfad in xml_dateien:
            logger.info("[Job %s] Prüfe XML: %s", job_id, pseudonymisiere_dateiname(xml_pfad.name))

            # 2. Version auslesen
            await sende_sse_event(job_id, "formal", "laufend", f"Lese {xml_pfad.name}…", 20)
            try:
                version = lese_xdomea_version(xml_pfad)
                xdomea_version = version
                await sende_sse_event(job_id, "formal", "laufend", f"XDOMEA {version} erkannt", 30)
                meldungen.append(
                    {"typ": "info", "text": f"XDOMEA-Version: {version} ({xml_pfad.name})"}
                )
            except (ValueError, RuntimeError) as e:
                # XML beschädigt — Fehler transparent zeigen, Reparatur prüfen aber NICHT durchführen
                await sende_sse_event(job_id, "formal", "laufend", f"XML-Fehler erkannt: {e}", 25)
                meldungen.append({"typ": "fehler", "text": f"XML-Fehler in {xml_pfad.name}: {e}"})

                # Prüfe ob eine Reparatur möglich wäre (ohne Datei zu verändern)
                from utils.xdomea_parser import pruefe_xml_reparierbar

                reparierbar, reparatur_details = pruefe_xml_reparierbar(xml_pfad)

                if reparierbar:
                    xml_reparierbar = True
                    await sende_sse_event(
                        job_id,
                        "formal",
                        "laufend",
                        "XML ist reparierbar — Archivar entscheidet.",
                        28,
                    )
                    meldungen.append(
                        {"typ": "warnung", "text": f"XML-Reparatur möglich: {reparatur_details}"}
                    )
                    meldungen.append(
                        {
                            "typ": "info",
                            "text": "Die Reparatur wird erst nach Bestätigung durch den Archivar durchgeführt.",
                        }
                    )
                else:
                    meldungen.append(
                        {"typ": "fehler", "text": f"XML nicht reparierbar: {reparatur_details}"}
                    )
                    meldungen.append(
                        {
                            "typ": "fehler",
                            "text": "Empfehlung: Prüfen Sie die XML-Datei manuell oder fordern Sie eine korrigierte Version an.",
                        }
                    )

                fehler.append(f"XML beschädigt ({xml_pfad.name}): {e}")
                gesamt_status = "fehler"
                continue

            # 3. XSD-Validierung (nur wenn Schema vorhanden)
            xsd_pfad = XSD_DATEIEN.get(version)
            if xsd_pfad and xsd_pfad.exists():
                xsd_fehler = validiere_gegen_xsd(xml_pfad, xsd_pfad)
                if xsd_fehler:
                    alle_xsd_fehler.extend(xsd_fehler)
                    # Fehler menschenlesbar aufbereiten
                    kritische, hinweise, reparierbare = _klassifiziere_xsd_fehler(xsd_fehler)
                    if reparierbare:
                        reparaturoptionen["xsd"].extend(reparierbare)
                    if kritische:
                        gesamt_status = "fehler"
                        for f in kritische:
                            meldungen.append({"typ": "fehler", "text": f})
                    if hinweise:
                        if gesamt_status == "ok":
                            gesamt_status = "ok"  # Hinweise allein sind kein Fehler
                        for h in hinweise:
                            meldungen.append({"typ": "warnung", "text": h})
                    # Tatsächliche Vorkommen zählen (read-only) für vollständige Vorschau
                    if reparierbare:
                        from utils.xdomea_parser import zaehle_reparierbare_probleme

                        zaehler = zaehle_reparierbare_probleme(xml_pfad)
                        if zaehler["camelcase_vorkommen"] > 0:
                            meldungen.append(
                                {
                                    "typ": "warnung",
                                    "text": f"Reparierbar: {zaehler['camelcase_vorkommen']} CamelCase-Attribut(e) müssen korrigiert werden.",
                                }
                            )
                        if zaehler["importbestaetigung"] > 0:
                            meldungen.append(
                                {
                                    "typ": "warnung",
                                    "text": f"Reparierbar: {zaehler['importbestaetigung']} Importbestaetigung-Wert(e) von 'true' auf '1' zu korrigieren.",
                                }
                            )
                        if zaehler["unbekannte_attribute_vorkommen"] > 0:
                            meldungen.append(
                                {
                                    "typ": "warnung",
                                    "text": f"Reparierbar: {zaehler['unbekannte_attribute_vorkommen']} unbekannte(s) Attribut(e) zu entfernen ({', '.join(zaehler['unbekannte_attribute_namen'])}).",
                                }
                            )
                    if not kritische:
                        meldungen.append(
                            {
                                "typ": "info",
                                "text": f"XSD-Validierung: {len(hinweise)} Kompatibilitätshinweis(e), keine kritischen Fehler.",
                            }
                        )
                else:
                    meldungen.append(
                        {
                            "typ": "info",
                            "text": "XSD-Validierung erfolgreich — Dokument ist vollständig schema-konform.",
                        }
                    )
            else:
                bekannt = version in BEKANNTE_VERSIONEN
                hinweis = (
                    f"XDOMEA {version} erkannt. Für diese Version ist kein XSD-Schema hinterlegt — "
                    f"die Schemavalidierung wird übersprungen. Strukturprüfung und Vollständigkeitsprüfung "
                    f"werden dennoch durchgeführt."
                    if bekannt
                    else f"Unbekannte XDOMEA-Version '{version}' — Schemavalidierung nicht möglich. "
                    f"Strukturprüfung und Vollständigkeitsprüfung werden dennoch durchgeführt."
                )
                meldungen.append({"typ": "warnung", "text": hinweis})
                logger.warning("[Job %s] %s", job_id, hinweis)

            # 4. Referenzabgleich
            await sende_sse_event(
                job_id, "formal", "laufend", "Prüfe Vollständigkeit der Dateien…", 60
            )
            try:
                referenzierte = lese_referenzierte_dateien(xml_pfad)
                vorhandene = {
                    d.name
                    for d in arbeitsverzeichnis.rglob("*")
                    if d.is_file() and d.suffix != ".xml"
                }

                fehlende = [r for r in referenzierte if r not in vorhandene]
                # Überzählig = im ZIP vorhanden, aber von KEINER XML referenziert.
                ueberzaehlige = [
                    v
                    for v in vorhandene
                    if v not in global_referenzierte and not v.startswith("protokoll_")
                ]

                ergebnis_ref = {
                    "referenziert": referenzierte,
                    "vorhanden": sorted(vorhandene),
                    "fehlend": fehlende,
                    "ueberzaehlig": ueberzaehlige,
                }
                alle_referenz_ergebnisse[xml_pfad.name] = ergebnis_ref

                if fehlende:
                    gesamt_status = "fehler"
                    reparaturoptionen["referenzen"].extend(fehlende)
                    for f_datei in fehlende:
                        text = f"Referenzierte Datei fehlt: {f_datei}"
                        fehler.append(text)
                        meldungen.append({"typ": "fehler", "text": text})
                    meldungen.append(
                        {
                            "typ": "warnung",
                            "text": f"Referenzen auf {len(fehlende)} fehlende Datei(en) können aus dem XML entfernt werden.",
                        }
                    )
                else:
                    meldungen.append(
                        {
                            "typ": "info",
                            "text": f"Alle referenzierten Dateien vorhanden ({xml_pfad.name}).",
                        }
                    )

                if ueberzaehlige:
                    for u_datei in ueberzaehlige:
                        meldungen.append(
                            {"typ": "warnung", "text": f"Nicht referenzierte Datei: {u_datei}"}
                        )

            except RuntimeError as e:
                fehler.append(f"Referenzabgleich fehlgeschlagen ({xml_pfad.name}): {e}")
                meldungen.append({"typ": "fehler", "text": str(e)})
                gesamt_status = "fehler"

        # 5. Protokoll erstellen
        erstelle_protokoll(job_id, "formal", gesamt_status, meldungen, arbeitsverzeichnis)

        # Reparierbarkeit: Encoding ODER Referenzen ODER XSD-Kompatibilitaet
        reparierbar = (
            xml_reparierbar
            or bool(reparaturoptionen["referenzen"])
            or bool(reparaturoptionen["xsd"])
        )
        reparaturoptionen["encoding"] = xml_reparierbar

        return {
            "status": gesamt_status,
            "fehler": fehler,
            "xdomea_version": xdomea_version,
            "xsd_fehler": alle_xsd_fehler,
            "referenz_abgleich": alle_referenz_ergebnisse,
            "xml_reparierbar": xml_reparierbar,  # Abwaertskompatibilitaet
            "reparierbar": reparierbar,
            "reparaturoptionen": reparaturoptionen,
        }

    except Exception as e:
        logger.exception("[Job %s] Unerwarteter Fehler im Agent 'formal': %s", job_id, e)
        meldungen.append({"typ": "fehler", "text": f"Unerwarteter Fehler: {e}"})
        erstelle_protokoll(job_id, "formal", "fehler", meldungen, arbeitsverzeichnis)
        return {
            "status": "fehler",
            "fehler": [str(e)],
            "xdomea_version": None,
            "xsd_fehler": [],
            "referenz_abgleich": {},
        }


def _klassifiziere_xsd_fehler(fehler_liste: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Klassifiziert XSD-Fehler in kritische Fehler, Kompatibilitätshinweise und reparierbare Fehler."""
    import re

    # Bekannte DMS-Kompatibilitätsprobleme (nicht kritisch)
    BEKANNTE_CAMELCASE = {
        "codeListAgencyName",
        "codelistAgencyName",
        "codeListName",
        "codelistName",
        "codeListVersionIdentifier",
        "codelistVersionIdentifier",
        "languageCode",
        "languagecode",
    }

    # Elemente die im Schema als Pflicht stehen, aber in der Praxis oft fehlen
    OPTIONAL_IN_PRAXIS = {
        "Hinweis",
        "Empfangsbestaetigung",
        "WeitererEmpfaenger",
        "AnwendungsspezifischeErweiterung",
        "Format",
    }

    kritische: list[str] = []
    hinweise: list[str] = []
    reparierbare: list[str] = []
    camelcase_attribute: set[str] = set()
    hat_importbestaetigung = False

    for fehler in fehler_liste:
        fehler_str = str(fehler)

        # CamelCase-Attribut-Fehler erkennen und sammeln
        ist_camelcase = False
        for attr_name in BEKANNTE_CAMELCASE:
            if attr_name in fehler_str:
                ist_camelcase = True
                camelcase_attribute.add(attr_name)
                break

        if ist_camelcase:
            continue  # Wird am Ende als ein Sammel-Hinweis ausgegeben

        elif "Missing child element" in fehler_str:
            elem_match = re.search(r"Element '\{[^}]*\}(\w+)'", fehler_str)
            elem = elem_match.group(1) if elem_match else "?"
            # Alle erwarteten Elemente extrahieren
            erwartete = (
                re.findall(r"\{[^}]*\}(\w+)", fehler_str.split("Expected is")[-1])
                if "Expected is" in fehler_str
                else []
            )
            # Prüfen ob es nur "in der Praxis optionale" Elemente sind
            if erwartete and all(e in OPTIONAL_IN_PRAXIS for e in erwartete):
                hinweise.append(
                    f"Optionales Element fehlt: Im Element '{elem}' fehlt '{', '.join(erwartete)}' — "
                    f"laut Schema erwartet, in der Praxis aber häufig nicht vorhanden."
                )
            elif erwartete:
                praxis_optional = [e for e in erwartete if e in OPTIONAL_IN_PRAXIS]
                echt_fehlend = [e for e in erwartete if e not in OPTIONAL_IN_PRAXIS]
                if echt_fehlend:
                    kritische.append(
                        f"Pflicht-Element fehlt: Im Element '{elem}' wird '{', '.join(echt_fehlend)}' erwartet."
                    )
                if praxis_optional:
                    hinweise.append(
                        f"Optionales Element fehlt: '{', '.join(praxis_optional)}' in '{elem}' — in der Praxis häufig nicht vorhanden."
                    )
            else:
                kritische.append(f"Pflicht-Element fehlt im Element '{elem}'.")

        elif "is not allowed" in fehler_str:
            attr_match = re.search(r"attribute '([^']+)'", fehler_str)
            attr = attr_match.group(1) if attr_match else "unbekannt"
            if attr in BEKANNTE_CAMELCASE:
                camelcase_attribute.add(attr)
            else:
                hinweise.append(f"Unbekanntes Attribut '{attr}' — wird vom Schema nicht erwartet.")
                reparierbare.append(f"Unbekanntes Attribut '{attr}' entfernen")

        elif "is required but missing" in fehler_str:
            attr_match = re.search(r"attribute '([^']+)'", fehler_str)
            attr = attr_match.group(1) if attr_match else "unbekannt"
            if attr in BEKANNTE_CAMELCASE:
                camelcase_attribute.add(attr)
            else:
                hinweise.append(f"Pflicht-Attribut '{attr}' fehlt.")

        elif "Importbestaetigung" in fehler_str and "fixed" in fehler_str.lower():
            hat_importbestaetigung = True
            # DMS schreibt "true"/"false" statt "1"/"0" — inhaltlich identisch (xs:boolean),
            # aber fixed="1" erzwingt lexikalische Gleichheit.
            hinweise.append(
                "Importbestaetigung enthält 'true'/'false' statt '1'/'0' — inhaltlich identisch "
                "(xs:boolean), aber laut Schema wird der feste Wert '1' erwartet. "
                "Bekanntes DMS-Kompatibilitätsproblem."
            )

        elif (
            "SCHEMAV_CVC_ENUMERATION_VALID" in fehler_str
            and "'code'" in fehler_str
            and "The value ''" in fehler_str
        ):
            # Leeres <code/>-Element: manche DMS (z.B. enaio) schreiben den
            # Codelisten-Wert nicht als <code>, sondern als separates <name>.
            # Für die Archivierung unkritisch — die Metadaten werden ohnehin
            # namensraum-tolerant ausgelesen.
            hinweise.append(
                "Leeres <code>-Element (Codelisten-Wert fehlt) — bekanntes DMS-Exportverhalten "
                "(z.B. enaio: Wert steht im begleitenden <name>-Element)."
            )

        elif "Element 'name'" in fehler_str and "not expected" in fehler_str:
            # Zusätzliches <name> neben <code> in Code-Typen — Gegenstück zum
            # leeren <code/> oben. Ebenfalls bekanntes DMS-Exportverhalten.
            hinweise.append(
                "Zusätzliches <name>-Element neben <code> — bekanntes DMS-Exportverhalten "
                "(z.B. enaio); vom XDOMEA-Schema an dieser Stelle nicht vorgesehen."
            )

        elif "SCHEMAV_CVC_PATTERN_VALID" in fehler_str and (
            "}ID'" in fehler_str or "'ID'" in fehler_str
        ):
            # DMS-interne numerische IDs statt UUID-Muster. Für die Archivierung
            # unkritisch — die ID dient nur der internen Referenzierung.
            hinweise.append(
                "ID im DMS-internen Format (kein UUID) — bekanntes DMS-Exportverhalten "
                "(z.B. enaio); vom XDOMEA-Schema wird ein UUID-Muster erwartet."
            )

        elif "Primaerdokument'" in fehler_str and "not expected" in fehler_str:
            # Reihenfolge-/Content-Model-Abweichung beim Primärdokument: enaio
            # bettet u.a. HistorienProtokollInformationen ins <Dokument> ein,
            # wodurch das Primärdokument an unerwarteter Position steht. Die
            # Datei-Referenzen selbst werden separat im Vollständigkeitsabgleich
            # geprüft — daher informativ statt kritisch.
            hinweise.append(
                "Abweichende Dokumentstruktur (Primärdokument an unerwarteter Position) — "
                "bekanntes DMS-Exportverhalten (z.B. enaio mit eingebetteter Historie)."
            )

        else:
            bereinigt = re.sub(r"data/jobs/[^/]+/arbeit_formal/", "", fehler_str)
            bereinigt = re.sub(r"\{[^}]+\}", "", bereinigt)
            kritische.append(f"Schema-Fehler: {bereinigt[:200]}")

    # CamelCase-Hinweise als ein Sammel-Eintrag
    if camelcase_attribute:
        hinweise.insert(
            0,
            f"Schreibweise weicht ab bei {len(camelcase_attribute)} Attribut-Typen "
            f"(CamelCase statt Kleinschreibung) — bekanntes DMS-Exportproblem, nicht kritisch.",
        )
        reparierbare.append(
            f"Attribut-Schreibweise angleichen: {len(camelcase_attribute)} Attribut-Typ(en) "
            f"stehen in CamelCase, das Schema erwartet Kleinschreibung"
        )

    if hat_importbestaetigung:
        reparierbare.append(
            "Importbestätigung korrigieren: steht auf 'true', das Schema verlangt '1'"
        )

    # Duplikate entfernen
    kritische = list(dict.fromkeys(kritische))
    hinweise = list(dict.fromkeys(hinweise))
    reparierbare = list(dict.fromkeys(reparierbare))

    return kritische, hinweise, reparierbare


async def repariere_lieferverzeichnis(
    arbeit: Path,
    lv_xml: Path,
    rep_optionen: dict,
) -> tuple[int, list[dict], list[str]]:
    """Repariert ein DOMEA-Lieferverzeichnis-Bundle.

    Für jeden Vorgang in rep_optionen['lv_vorgaenge']:
      1. Inneres ZIP entpacken
      2. XSD-Inkompatibilitäten reparieren
      3. Fehlende Referenzen aus dem inneren XDOMEA-XML entfernen
      4. Inneres ZIP neu packen (überschreibt das Original)
      5. MD5 neu berechnen und in der LV-XML aktualisieren

    Schließlich wird die LV-XML neu geschrieben.

    Returns:
        (anzahl_repariert, meldungen, fehler_nach_reparatur)
    """
    import tempfile

    from utils.xdomea_parser import (
        entferne_fehlende_referenzen,
        lese_xdomea_version,
        repariere_xsd_kompatibilitaet,
        sicher_parsen,
        validiere_gegen_xsd,
    )
    from utils.zip_handler import entpacke_zip, packe_zip

    meldungen: list[dict] = []
    fehler_nach_reparatur: list[str] = []
    repariert = 0

    lv_vorgaenge = rep_optionen.get("lv_vorgaenge", [])
    if not lv_vorgaenge:
        meldungen.append(
            {"typ": "info", "text": "Keine reparierbaren Vorgänge im Lieferverzeichnis."}
        )
        return 0, meldungen, fehler_nach_reparatur

    # LV-XML parsen für späteres Hash-Update
    lv_baum = sicher_parsen(lv_xml)
    lv_wurzel = lv_baum.getroot()
    # Map: dateiname → <hashwert>/<wert>-Element für späteres Update
    hash_elemente: dict[str, etree._Element] = {}
    for vg_el in lv_wurzel.findall("{*}vorgang") or lv_wurzel.findall("vorgang"):
        dateiname_el = vg_el.find(".//{*}dateiname") or vg_el.find(".//dateiname")
        wert_el = vg_el.find(".//{*}wert") or vg_el.find(".//wert")
        if dateiname_el is not None and dateiname_el.text and wert_el is not None:
            hash_elemente[dateiname_el.text.strip()] = wert_el

    for vg_rep in lv_vorgaenge:
        az = vg_rep.get("aktenzeichen", "?")
        dateiname = vg_rep.get("dateiname")
        fehlende = vg_rep.get("fehlende_referenzen", [])
        xsd_fix = vg_rep.get("xsd_reparierbar", False)

        if not dateiname:
            continue
        if not fehlende and not xsd_fix:
            continue

        zip_pfad = finde_datei(arbeit, dateiname)
        if zip_pfad is None:
            fehler_nach_reparatur.append(f"Vorgang {az}: ZIP '{dateiname}' nicht gefunden.")
            continue

        with tempfile.TemporaryDirectory(dir=str(arbeit)) as tmp:
            tmp_path = Path(tmp)
            try:
                entpacke_zip(zip_pfad, tmp_path)
            except Exception as e:
                fehler_nach_reparatur.append(f"Vorgang {az}: Entpacken fehlgeschlagen: {e}")
                continue

            inneres_xml_pfade = [
                x for x in tmp_path.rglob("*.xml") if not x.name.startswith("protokoll_")
            ]
            wurden_geaendert = False
            for inner_xml in inneres_xml_pfade:
                # 1. XSD-Reparatur
                if xsd_fix:
                    erfolg, protokoll_eintraege = repariere_xsd_kompatibilitaet(inner_xml)
                    if erfolg:
                        wurden_geaendert = True
                    for m in protokoll_eintraege:
                        meldungen.append(
                            {
                                "typ": m.get("typ", "info"),
                                "text": f"Vorgang {az}: {m.get('text', '')}",
                            }
                        )
                # 2. Fehlende Referenzen entfernen
                if fehlende:
                    erfolg, protokoll_eintraege = entferne_fehlende_referenzen(inner_xml, fehlende)
                    if erfolg:
                        wurden_geaendert = True
                    for m in protokoll_eintraege:
                        meldungen.append(
                            {
                                "typ": m.get("typ", "info"),
                                "text": f"Vorgang {az}: {m.get('text', '')}",
                            }
                        )

                # 3. Re-Validierung
                try:
                    version = lese_xdomea_version(inner_xml)
                    xsd_pfad = XSD_DATEIEN.get(version)
                    if xsd_pfad and xsd_pfad.exists():
                        rest_fehler = validiere_gegen_xsd(inner_xml, xsd_pfad)
                        kritische, _, _ = (
                            _klassifiziere_xsd_fehler(rest_fehler) if rest_fehler else ([], [], [])
                        )
                        for k in kritische[:3]:
                            fehler_nach_reparatur.append(f"Vorgang {az}: Nach Reparatur: {k}")
                except Exception as e:
                    meldungen.append(
                        {
                            "typ": "warnung",
                            "text": f"Vorgang {az}: Re-Validierung übersprungen: {e}",
                        }
                    )

            if not wurden_geaendert:
                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"Vorgang {az}: nichts repariert (Vorgang war ok oder Reparatur nicht möglich).",
                    }
                )
                continue

            # 4. Inneres ZIP neu packen — über das Original
            try:
                zip_pfad.unlink()
                packe_zip(tmp_path, zip_pfad, komprimieren=False)
            except Exception as e:
                fehler_nach_reparatur.append(
                    f"Vorgang {az}: Neupacken des inneren ZIPs fehlgeschlagen: {e}"
                )
                continue

            # 5. MD5-Prüfsumme neu berechnen — XDOMEA-Formatvorgabe für die
            #    Primärdokument-Prüfsumme, kein Sicherheits-Hash (usedforsecurity=False).
            hasher = hashlib.md5(usedforsecurity=False)
            with zip_pfad.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    hasher.update(chunk)
            neuer_hash = hasher.hexdigest().lower()

            # 6. Hash in LV-XML updaten
            wert_el = hash_elemente.get(dateiname)
            if wert_el is not None:
                alter = wert_el.text
                wert_el.text = neuer_hash
                meldungen.append(
                    {
                        "typ": "info",
                        "text": f"Vorgang {az}: ZIP repariert, MD5 aktualisiert ({(alter or '')[:8]}… → {neuer_hash[:8]}…)",
                    }
                )
            else:
                meldungen.append(
                    {
                        "typ": "warnung",
                        "text": f"Vorgang {az}: ZIP repariert, aber Hash-Element in LV-XML nicht gefunden.",
                    }
                )

            repariert += 1

    # LV-XML zurückschreiben
    if repariert > 0:
        lv_baum.write(str(lv_xml), pretty_print=False, xml_declaration=True, encoding="UTF-8")
        meldungen.append(
            {"typ": "info", "text": "Lieferverzeichnis-XML mit aktualisierten Hashes geschrieben."}
        )

    return repariert, meldungen, fehler_nach_reparatur


def _pruefe_inneren_vorgang(zip_pfad: Path, ziel_dir: Path) -> dict:
    """Entpackt das innere Vorgangs-ZIP und validiert dessen XDOMEA-XML.

    Returns:
        {
          status: "ok" | "warnung" | "fehler",
          version: str | None,
          meldungen: [{typ, text}],
          fehler: [str],
          fehlende_referenzen: [str],  # für Reparatur
          xsd_reparierbar: bool,
        }
    """
    from utils.xdomea_parser import (
        lese_referenzierte_dateien,
        lese_xdomea_version,
        validiere_gegen_xsd,
    )
    from utils.zip_handler import entpacke_zip

    meldungen: list[dict] = []
    fehler: list[str] = []
    fehlende_referenzen: list[str] = []
    xsd_reparierbar = False
    reparierbare_befunde: list[str] = []
    status = "ok"
    version = None

    try:
        if not ziel_dir.exists() or not any(ziel_dir.rglob("*.xml")):
            ziel_dir.mkdir(parents=True, exist_ok=True)
            entpacke_zip(zip_pfad, ziel_dir)

        xml_dateien = [x for x in ziel_dir.rglob("*.xml") if not x.name.startswith("protokoll_")]
        if not xml_dateien:
            return {
                "status": "fehler",
                "version": None,
                "meldungen": [{"typ": "fehler", "text": "Inneres ZIP enthält keine XDOMEA-XML."}],
                "fehler": ["Inneres ZIP enthält keine XDOMEA-XML."],
                "fehlende_referenzen": [],
                "xsd_reparierbar": False,
            }

        for xml_pfad in xml_dateien:
            try:
                version = lese_xdomea_version(xml_pfad)
                meldungen.append(
                    {"typ": "info", "text": f"Inneres XDOMEA {version} erkannt ({xml_pfad.name})"}
                )
            except (ValueError, RuntimeError) as e:
                meldungen.append({"typ": "fehler", "text": f"Inneres XML beschädigt: {e}"})
                fehler.append(f"Inneres XML beschädigt: {e}")
                status = "fehler"
                continue

            xsd_pfad = XSD_DATEIEN.get(version)
            if xsd_pfad and xsd_pfad.exists():
                xsd_fehler = validiere_gegen_xsd(xml_pfad, xsd_pfad)
                kritische, hinweise, reparierbare = (
                    _klassifiziere_xsd_fehler(xsd_fehler) if xsd_fehler else ([], [], [])
                )
                if reparierbare:
                    xsd_reparierbar = True
                    # Befunde merken, damit die Oberfläche benennen kann, was
                    # repariert würde — statt pauschal "XML-Fehler" zu melden.
                    for befund in reparierbare:
                        if befund not in reparierbare_befunde:
                            reparierbare_befunde.append(befund)
                if kritische:
                    status = "fehler"
                    for k in kritische[:3]:
                        meldungen.append({"typ": "fehler", "text": f"XSD: {k}"})
                        fehler.append(f"XSD: {k}")
                    if len(kritische) > 3:
                        meldungen.append(
                            {"typ": "fehler", "text": f"XSD: … und {len(kritische) - 3} weitere"}
                        )
                if hinweise:
                    if status == "ok":
                        status = "warnung"
                    meldungen.append(
                        {
                            "typ": "warnung",
                            "text": f"XSD: {len(hinweise)} Kompatibilitätshinweis(e)",
                        }
                    )
                if not xsd_fehler:
                    meldungen.append({"typ": "info", "text": "XSD-Validierung erfolgreich"})

            try:
                referenzierte = lese_referenzierte_dateien(xml_pfad)
                vorhandene = {
                    d.name for d in ziel_dir.rglob("*") if d.is_file() and d.suffix != ".xml"
                }
                fehlende = [r for r in referenzierte if r not in vorhandene]
                if fehlende:
                    status = "fehler"
                    fehlende_referenzen.extend(fehlende)
                    for f_ in fehlende[:3]:
                        meldungen.append(
                            {"typ": "fehler", "text": f"Referenzierte Datei fehlt: {f_}"}
                        )
                        fehler.append(f"Referenzierte Datei fehlt: {f_}")
                    if len(fehlende) > 3:
                        meldungen.append(
                            {
                                "typ": "fehler",
                                "text": f"… und {len(fehlende) - 3} weitere fehlende Dateien",
                            }
                        )
                else:
                    meldungen.append(
                        {
                            "typ": "info",
                            "text": f"Alle {len(referenzierte)} referenzierten Dateien vorhanden",
                        }
                    )
            except RuntimeError as e:
                status = "fehler"
                fehler.append(f"Referenzabgleich fehlgeschlagen: {e}")
                meldungen.append({"typ": "fehler", "text": f"Referenzabgleich fehlgeschlagen: {e}"})

    except Exception as e:
        return {
            "status": "fehler",
            "version": None,
            "meldungen": [
                {"typ": "fehler", "text": f"Inneres ZIP konnte nicht geprüft werden: {e}"}
            ],
            "fehler": [str(e)],
            "fehlende_referenzen": [],
            "xsd_reparierbar": False,
            "reparierbare": [],
        }

    return {
        "status": status,
        "version": version,
        "meldungen": meldungen,
        "fehler": fehler,
        "fehlende_referenzen": fehlende_referenzen,
        "xsd_reparierbar": xsd_reparierbar,
        "reparierbare": reparierbare_befunde,
    }


def aggregiere_reparaturoptionen(ergebnis_pro_vorgang: list[dict]) -> dict:
    """Fasst die reparierbaren Befunde aller Container eines Bundles zusammen.

    Die konkreten Befunde werden durchgereicht, nicht nur ein Boolean — sonst
    kann die Oberfläche nur pauschal "XML-Fehler" melden, obwohl es sich um
    benennbare Kompatibilitätsprobleme handelt (z.B. Importbestaetigung mit
    Wert 'true' statt '1'). Derselbe Befund aus mehreren Containern erscheint
    nur einmal.
    """
    xsd_befunde: list[str] = []
    referenzen: list[str] = []
    for vorgang in ergebnis_pro_vorgang:
        for befund in vorgang.get("reparierbare") or []:
            if befund not in xsd_befunde:
                xsd_befunde.append(befund)
        for datei in vorgang.get("fehlende_referenzen") or []:
            if datei not in referenzen:
                referenzen.append(datei)

    betroffene = [
        {
            "aktenzeichen": v["aktenzeichen"],
            "dateiname": v["dateiname"],
            "fehlende_referenzen": v.get("fehlende_referenzen", []),
            "xsd_reparierbar": v.get("xsd_reparierbar", False),
        }
        for v in ergebnis_pro_vorgang
        if v.get("fehlende_referenzen") or v.get("xsd_reparierbar")
    ]

    return {
        "encoding": False,
        "referenzen": referenzen,
        "xsd": xsd_befunde,
        "lv_vorgaenge": betroffene,
    }


def _finde_lieferverzeichnis(xml_dateien: list[Path]) -> Path | None:
    """Erkennt eine DOMEA-Lieferverzeichnis-XML am Root-Tag.

    Returns:
        Pfad zur Lieferverzeichnis-XML oder None.
    """
    from utils.xdomea_parser import sicher_parsen

    for x in xml_dateien:
        try:
            baum = sicher_parsen(x)
            local = etree.QName(baum.getroot()).localname
            if "domeaAussonderungListen" in local or "AussonderungListen" in local:
                return x
        except Exception:
            continue
    return None


async def _pruefe_lieferverzeichnis(
    job_id: str,
    lv_xml: Path,
    arbeitsverzeichnis: Path,
    meldungen: list[dict],
    fehler: list[str],
    sende_sse_event,
    erstelle_protokoll,
) -> dict:
    """Validiert ein DOMEA-Lieferverzeichnis: ZIP-Vollständigkeit + MD5-Hashabgleich."""
    await sende_sse_event(
        job_id,
        "formal",
        "laufend",
        "Lieferverzeichnis (DOMEA-Bundle) erkannt — prüfe Vollständigkeit und Hashes…",
        15,
    )
    meldungen.append({"typ": "info", "text": f"Lieferverzeichnis erkannt: {lv_xml.name}"})

    from utils.xdomea_parser import sicher_parsen

    baum = sicher_parsen(lv_xml)
    wurzel = baum.getroot()
    # Je nach DMS listet das Verzeichnis Vorgänge (Bundesarchiv-Referenzfall)
    # oder Akten (z.B. nscale eGOV). Beide Ebenen sind
    # gültig und verweisen gleichermaßen auf die inneren Container.
    vorgaenge: list = []
    for tag in ("vorgang", "akte"):
        vorgaenge = wurzel.findall(f"{{*}}{tag}") or wurzel.findall(tag)
        if vorgaenge:
            break

    if not vorgaenge:
        fehler.append("Lieferverzeichnis enthält keine <vorgang>- oder <akte>-Einträge.")
        meldungen.append({"typ": "fehler", "text": "Lieferverzeichnis ohne Einträge."})
        erstelle_protokoll(job_id, "formal", "fehler", meldungen, arbeitsverzeichnis)
        return {
            "status": "fehler",
            "fehler": fehler,
            "xdomea_version": "DOMEA-LV",
            "xsd_fehler": [],
            "referenz_abgleich": {},
        }

    gesamt_status = "ok"
    ergebnis_pro_vorgang: list[dict] = []

    def _feld(el, name: str) -> str:
        kind = el.find(f".//{{*}}{name}")
        if kind is None:
            kind = el.find(f".//{name}")
        return kind.text.strip() if kind is not None and kind.text else ""

    for idx, vg_el in enumerate(vorgaenge, 1):
        az = _feld(vg_el, "vorgangszeichen") or _feld(vg_el, "aktenzeichen") or "?"
        dateiname = _feld(vg_el, "dateiname")
        erwarteter_hash = (_feld(vg_el, "wert") or "").lower()
        hash_algorithmus = (_feld(vg_el, "algorithmus") or "MD5").upper()

        progress = 15 + int(80 * idx / max(len(vorgaenge), 1))
        await sende_sse_event(
            job_id,
            "formal",
            "laufend",
            f"Vorgang {idx}/{len(vorgaenge)} ({az}) — prüfe {dateiname or '?'}…",
            progress,
        )

        if not dateiname:
            fehler.append(f"Vorgang {az}: Kein <dateiname> im Lieferverzeichnis.")
            meldungen.append({"typ": "fehler", "text": f"Vorgang {az}: <dateiname> fehlt."})
            gesamt_status = "fehler"
            continue

        kandidat = finde_datei(arbeitsverzeichnis, dateiname)
        if kandidat is None:
            fehler.append(f"Vorgang {az}: ZIP '{dateiname}' nicht im Bundle gefunden.")
            meldungen.append({"typ": "fehler", "text": f"Vorgang {az}: ZIP '{dateiname}' fehlt."})
            ergebnis_pro_vorgang.append(
                {"aktenzeichen": az, "dateiname": dateiname, "status": "fehlt"}
            )
            gesamt_status = "fehler"
            continue

        # Hash berechnen
        if hash_algorithmus not in {"MD5", "SHA-1", "SHA1", "SHA-256", "SHA256"}:
            meldungen.append(
                {
                    "typ": "warnung",
                    "text": f"Vorgang {az}: Unbekannter Hash-Algorithmus '{hash_algorithmus}' — Hash-Prüfung übersprungen.",
                }
            )
            ergebnis_pro_vorgang.append(
                {"aktenzeichen": az, "dateiname": dateiname, "status": "ok_ohne_hash"}
            )
            continue

        hasher = {
            "MD5": hashlib.md5,
            "SHA-1": hashlib.sha1,
            "SHA1": hashlib.sha1,
            "SHA-256": hashlib.sha256,
            "SHA256": hashlib.sha256,
        }[hash_algorithmus]()
        with kandidat.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)
        ist_hash = hasher.hexdigest().lower()

        hash_status = "ok_ohne_hash"
        if not erwarteter_hash:
            meldungen.append(
                {
                    "typ": "warnung",
                    "text": f"Vorgang {az}: ZIP vorhanden, aber kein Soll-Hash im Lieferverzeichnis — Integrität nicht verifiziert.",
                }
            )
        elif ist_hash == erwarteter_hash:
            meldungen.append(
                {
                    "typ": "info",
                    "text": f"Vorgang {az}: ZIP vorhanden, {hash_algorithmus}-Hash stimmt.",
                }
            )
            hash_status = "ok"
        else:
            fehler.append(
                f"Vorgang {az}: {hash_algorithmus}-Hash weicht ab "
                f"(Soll: {erwarteter_hash}, Ist: {ist_hash})."
            )
            meldungen.append(
                {
                    "typ": "fehler",
                    "text": f"Vorgang {az}: {hash_algorithmus}-Hash stimmt nicht (Soll: {erwarteter_hash[:16]}…, Ist: {ist_hash[:16]}…).",
                }
            )
            ergebnis_pro_vorgang.append(
                {
                    "aktenzeichen": az,
                    "dateiname": dateiname,
                    "status": "hash_fehler",
                    "soll": erwarteter_hash,
                    "ist": ist_hash,
                }
            )
            gesamt_status = "fehler"
            continue

        # Inneres XDOMEA validieren (Version, XSD, Referenzabgleich)
        innere_pruefung = _pruefe_inneren_vorgang(
            kandidat,
            arbeitsverzeichnis / f"_lv_inner_{idx}",
        )
        for m in innere_pruefung["meldungen"]:
            meldungen.append({"typ": m["typ"], "text": f"Vorgang {az}: {m['text']}"})
        if innere_pruefung["status"] == "fehler":
            gesamt_status = "fehler"
            for f_text in innere_pruefung["fehler"]:
                fehler.append(f"Vorgang {az}: {f_text}")

        eintrag_status = innere_pruefung["status"] if hash_status == "ok" else hash_status
        if eintrag_status == "ok" and innere_pruefung["status"] != "ok":
            eintrag_status = innere_pruefung["status"]
        ergebnis_pro_vorgang.append(
            {
                "aktenzeichen": az,
                "dateiname": dateiname,
                "status": eintrag_status,
                "hash_status": hash_status,
                "inneres_status": innere_pruefung["status"],
                "inneres_version": innere_pruefung.get("version"),
                "inneres_fehler": innere_pruefung.get("fehler", []),
                "fehlende_referenzen": innere_pruefung.get("fehlende_referenzen", []),
                "xsd_reparierbar": innere_pruefung.get("xsd_reparierbar", False),
                "reparierbare": innere_pruefung.get("reparierbare", []),
            }
        )

    # Kurzfazit
    n_ok = sum(1 for r in ergebnis_pro_vorgang if r["status"] == "ok")
    n_fehler = sum(
        1 for r in ergebnis_pro_vorgang if r["status"] in ("fehlt", "hash_fehler", "fehler")
    )
    n_warnung = sum(1 for r in ergebnis_pro_vorgang if r["status"] in ("ok_ohne_hash", "warnung"))
    fazit = f"{len(vorgaenge)} Vorgänge im Lieferverzeichnis: {n_ok} vollständig ok, {n_warnung} mit Hinweis, {n_fehler} fehlerhaft."
    meldungen.append({"typ": "info", "text": fazit})

    erstelle_protokoll(job_id, "formal", gesamt_status, meldungen, arbeitsverzeichnis)

    reparaturoptionen = aggregiere_reparaturoptionen(ergebnis_pro_vorgang)
    reparierbar = bool(reparaturoptionen["lv_vorgaenge"])
    return {
        "status": gesamt_status,
        "fehler": fehler,
        "xdomea_version": "DOMEA-Lieferverzeichnis",
        "xsd_fehler": [],
        "referenz_abgleich": {lv_xml.name: {"vorgaenge": ergebnis_pro_vorgang}},
        "lieferverzeichnis": True,
        "lv_vorgaenge": ergebnis_pro_vorgang,
        "fazit": fazit,
        "reparierbar": reparierbar,
        "reparaturoptionen": reparaturoptionen,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 1: Formale Prüfung")
    parser.add_argument("--job-id", required=True, help="Job-ID")
    parser.add_argument("--pfad", default=".", help="Arbeitsverzeichnis (entpacktes ZIP)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    ergebnis = asyncio.run(pruefe_formal(args.job_id, Path(args.pfad)))
    print(ergebnis)
