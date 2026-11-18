"""XDOMEA XML lesen und schreiben mit lxml."""

import logging
from pathlib import Path

from lxml import etree

from utils.logging_setup import pseudonymisiere_dateiname

logger = logging.getLogger(__name__)

XDOMEA_NAMESPACES = {
    "xdomea": "urn:xoev-de:xdomea:schema:2.3.0",
    "xdomea30": "urn:xoev-de:xdomea:schema:3.0.0",
}


def safe_xml_parser(recover: bool = False, encoding: str | None = None) -> etree.XMLParser:
    """Liefert einen XXE-/Billion-Laughs-resistenten lxml-Parser.

    Soll überall verwendet werden, wo XML aus dem User-Upload geparst wird.
    """
    # resolve_entities=False blockt XXE; no_network=True blockt externe DTDs;
    # huge_tree=False schützt vor Billion-Laughs / quadratischer Aufblähung.
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        recover=recover,
        encoding=encoding,
    )


def sicher_parsen(quelle) -> etree._ElementTree:
    """Parst eine XML-Quelle (Pfad oder File-like) mit aktivem XXE-Schutz."""
    if isinstance(quelle, Path):
        return etree.parse(str(quelle), safe_xml_parser())
    return etree.parse(quelle, safe_xml_parser())


# Alias für interne Aufrufe (rückwärtskompatibel)
_safe_parser = safe_xml_parser


def pruefe_xml_reparierbar(xml_pfad: Path) -> tuple[bool, str]:
    """
    Prüft ob eine kaputte XML-Datei reparierbar wäre, OHNE die Datei zu verändern.

    Returns:
        (reparierbar, details) — True wenn Reparatur möglich, False wenn nicht.
    """
    try:
        rohtext = xml_pfad.read_bytes()
        for encoding in ["utf-8", "utf-16", "cp1252", "iso-8859-1"]:
            try:
                text = rohtext.decode(encoding)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            return False, "Kein gültiges Encoding erkannt."

        import re

        text = text.replace("&nbsp;", " ")
        text = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", text)

        parser = _safe_parser(recover=True, encoding="utf-8")
        baum = etree.fromstring(text.encode("utf-8"), parser)

        if baum is not None and len(list(baum)) > 0:
            return True, "XML kann repariert werden (Encoding + ungültige Zeichen bereinigbar)."
        else:
            return False, "XML konnte geparst werden, enthält aber keine verwertbaren Elemente."

    except Exception as e:
        logger.warning(
            "XML-Reparatur-Prüfung fehlgeschlagen (%s): %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
        return False, f"Reparatur-Prüfung fehlgeschlagen: {e}"


def versuche_xml_reparatur(xml_pfad: Path) -> tuple[bool, str]:
    """
    Führt die Reparatur einer kaputten XML-Datei durch (schreibt die Datei).

    Returns:
        (erfolg, details) — True wenn repariert, False wenn nicht möglich.
    """
    try:
        rohtext = xml_pfad.read_bytes()
        for encoding in ["utf-8", "utf-16", "cp1252", "iso-8859-1"]:
            try:
                text = rohtext.decode(encoding)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            return False, "Kein gültiges Encoding erkannt."

        import re

        text = text.replace("&nbsp;", " ")
        text = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", text)

        parser = _safe_parser(recover=True, encoding="utf-8")
        baum = etree.fromstring(text.encode("utf-8"), parser)

        if baum is not None and len(list(baum)) > 0:
            neuer_baum = etree.ElementTree(baum)
            neuer_baum.write(
                str(xml_pfad), pretty_print=True, xml_declaration=True, encoding="UTF-8"
            )
            logger.info("XML repariert: %s", pseudonymisiere_dateiname(str(xml_pfad)))
            return True, "XML wurde repariert (Encoding + ungültige Zeichen bereinigt)."
        else:
            return False, "XML konnte geparst werden, enthält aber keine verwertbaren Elemente."

    except Exception as e:
        logger.warning(
            "XML-Reparatur fehlgeschlagen (%s): %s", pseudonymisiere_dateiname(str(xml_pfad)), e
        )
        return False, f"Reparatur fehlgeschlagen: {e}"


# Bekannte CamelCase-Korrekturen (DMS-Exportsysteme schreiben oft falsche Schreibweise)
CAMELCASE_KORREKTUREN = {
    "codeListName": "codelistName",
    "codeListAgencyName": "codelistAgencyName",
    "codeListVersionIdentifier": "codelistVersionIdentifier",
    "languageCode": "languagecode",
}
# Auch umgekehrte Richtung
CAMELCASE_KORREKTUREN_REVERSE = {v: k for k, v in CAMELCASE_KORREKTUREN.items()}


def entferne_fehlende_referenzen(
    xml_pfad: Path, fehlende_dateien: list[str]
) -> tuple[bool, list[dict]]:
    """
    Entfernt Referenzen auf fehlende Dateien aus dem XDOMEA-XML.

    Hierarchie: Dokument > Version > Format > Dateiname
    - Format-Element wird entfernt wenn es den fehlenden Dateinamen enthaelt
    - Version-Element wird entfernt wenn danach keine Format-Kinder mehr
    - Dokument-Element wird entfernt wenn danach keine Version-Kinder mehr

    Args:
        xml_pfad: Pfad zur XDOMEA-XML.
        fehlende_dateien: Liste der Dateinamen die fehlen.

    Returns:
        (erfolg, protokoll_eintraege) — Liste der durchgefuehrten Aenderungen.
    """
    if not fehlende_dateien:
        return False, []

    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()
        protokoll: list[dict] = []
        fehlende_set = set(fehlende_dateien)
        geaendert = False

        # Alle Dateiname-Elemente finden
        dateiname_elemente = wurzel.findall(".//{*}Dateiname")

        for dateiname_el in dateiname_elemente:
            if dateiname_el.text and dateiname_el.text.strip() in fehlende_set:
                name = dateiname_el.text.strip()

                # Eltern-Hierarchie ermitteln: Dateiname -> Format -> Version -> Dokument
                # lxml hat kein direktes parent — wir muessen ueber getparent() gehen
                format_el = dateiname_el.getparent()
                if format_el is None:
                    continue

                # Dokument-Kontext fuer Protokoll ermitteln
                dok_name = _finde_dokument_kontext(format_el)

                # Format-Element entfernen
                version_el = format_el.getparent()
                if version_el is not None:
                    version_el.remove(format_el)
                    protokoll.append(
                        {
                            "typ": "info",
                            "text": f"[Reparatur] Referenz auf '{name}' entfernt (Dokument: {dok_name}).",
                        }
                    )
                    geaendert = True

                    # Version-Element entfernen wenn keine Format-Kinder mehr
                    format_kinder = version_el.findall("{*}Format")
                    if not format_kinder:
                        dokument_el = version_el.getparent()
                        if dokument_el is not None:
                            dokument_el.remove(version_el)
                            protokoll.append(
                                {
                                    "typ": "info",
                                    "text": f"[Reparatur] Leeres Version-Element entfernt (Dokument: {dok_name}).",
                                }
                            )

                            # Dokument-Element entfernen wenn keine Version-Kinder mehr
                            version_kinder = dokument_el.findall("{*}Version")
                            if not version_kinder:
                                dok_parent = dokument_el.getparent()
                                if dok_parent is not None:
                                    dok_parent.remove(dokument_el)
                                    protokoll.append(
                                        {
                                            "typ": "warnung",
                                            "text": f"[Reparatur] Leeres Dokument-Element '{dok_name}' vollstaendig entfernt.",
                                        }
                                    )

        if geaendert:
            baum.write(str(xml_pfad), pretty_print=True, xml_declaration=True, encoding="UTF-8")
            logger.info(
                "Fehlende Referenzen entfernt aus %s: %d Eintraege",
                pseudonymisiere_dateiname(str(xml_pfad)),
                len(protokoll),
            )

        return geaendert, protokoll

    except Exception as e:
        logger.warning(
            "Referenz-Reparatur fehlgeschlagen (%s): %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
        return False, [{"typ": "fehler", "text": f"Referenz-Reparatur fehlgeschlagen: {e}"}]


def _finde_dokument_kontext(element: etree._Element) -> str:
    """Ermittelt einen lesbaren Dokument-Kontext fuer Protokolleintraege."""
    current = element
    for _ in range(5):  # Maximal 5 Ebenen hoch
        if current is None:
            break
        tag = etree.QName(current.tag).localname if current.tag else ""
        if tag in ("Dokument", "Schriftgutobjekt"):
            # Betreff oder Kennzeichen als Identifikation
            betreff = current.find("{*}AllgemeineMetadaten/{*}Betreff")
            if betreff is None:
                betreff = current.find(".//{*}Betreff")
            if betreff is not None and betreff.text:
                return betreff.text.strip()[:80]
            kennz = current.find(".//{*}Kennzeichen")
            if kennz is not None and kennz.text:
                return kennz.text.strip()
            return tag
        current = current.getparent()
    return "unbekannt"


# Bekannte nicht-standardmaessige Attribute die DMS-Systeme hinzufuegen
UNBEKANNTE_ATTRIBUTE = {"codeName"}


def zaehle_reparierbare_probleme(xml_pfad: Path) -> dict:
    """
    Zaehlt reparierbare Probleme im XML (read-only, keine Aenderungen).

    Returns:
        Dict mit Zaehlerstaenden:
        {
            "camelcase_vorkommen": int,
            "camelcase_attribute": set[str],
            "importbestaetigung": int,
            "unbekannte_attribute_vorkommen": int,
            "unbekannte_attribute_namen": set[str],
        }
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()
    except Exception:
        return {
            "camelcase_vorkommen": 0,
            "camelcase_attribute": set(),
            "importbestaetigung": 0,
            "unbekannte_attribute_vorkommen": 0,
            "unbekannte_attribute_namen": set(),
        }

    alle_korrekturen = {**CAMELCASE_KORREKTUREN, **CAMELCASE_KORREKTUREN_REVERSE}
    camelcase_vorkommen = 0
    camelcase_attribute: set[str] = set()

    unbekannte_vorkommen = 0
    unbekannte_namen: set[str] = set()

    for element in wurzel.iter():
        for attr_name in element.attrib:
            if attr_name in alle_korrekturen:
                camelcase_vorkommen += 1
                camelcase_attribute.add(attr_name)
            if attr_name in UNBEKANNTE_ATTRIBUTE:
                unbekannte_vorkommen += 1
                unbekannte_namen.add(attr_name)

    import_fixes = 0
    for el in wurzel.findall(".//{*}Importbestaetigung"):
        if el.text and el.text.strip().lower() == "true":
            import_fixes += 1

    return {
        "camelcase_vorkommen": camelcase_vorkommen,
        "camelcase_attribute": camelcase_attribute,
        "importbestaetigung": import_fixes,
        "unbekannte_attribute_vorkommen": unbekannte_vorkommen,
        "unbekannte_attribute_namen": unbekannte_namen,
    }


def repariere_xsd_kompatibilitaet(xml_pfad: Path) -> tuple[bool, list[dict]]:
    """
    Korrigiert bekannte XSD-Kompatibilitaetsprobleme im XML:
    - CamelCase-Attribute umbenennen (bekannte DMS-Exportprobleme)
    - Importbestaetigung "true" -> "1" korrigieren
    - Unbekannte Attribute entfernen (z.B. codeName)

    Args:
        xml_pfad: Pfad zur XDOMEA-XML.

    Returns:
        (erfolg, protokoll_eintraege) — Liste der durchgefuehrten Korrekturen.
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()
        protokoll: list[dict] = []
        geaendert = False

        # 1. CamelCase-Attribute korrigieren
        alle_korrekturen = {**CAMELCASE_KORREKTUREN, **CAMELCASE_KORREKTUREN_REVERSE}
        camelcase_fixes = 0

        for element in wurzel.iter():
            attr_namen = list(element.attrib.keys())
            for attr_name in attr_namen:
                if attr_name in alle_korrekturen:
                    neuer_name = alle_korrekturen[attr_name]
                    wert = element.attrib[attr_name]
                    del element.attrib[attr_name]
                    element.attrib[neuer_name] = wert
                    camelcase_fixes += 1
                    geaendert = True

        if camelcase_fixes > 0:
            protokoll.append(
                {
                    "typ": "info",
                    "text": f"[Reparatur] {camelcase_fixes} CamelCase-Attribut(e) korrigiert.",
                }
            )

        # 2. Importbestaetigung "true" -> "1" korrigieren
        import_elemente = wurzel.findall(".//{*}Importbestaetigung")
        import_fixes = 0
        for el in import_elemente:
            if el.text and el.text.strip().lower() == "true":
                el.text = "1"
                import_fixes += 1
                geaendert = True

        if import_fixes > 0:
            protokoll.append(
                {
                    "typ": "info",
                    "text": f"[Reparatur] {import_fixes} Importbestaetigung-Wert(e) von 'true' auf '1' korrigiert.",
                }
            )

        # 3. Unbekannte Attribute entfernen (DMS-Datenmüll wie codeName)
        unbekannt_fixes = 0
        entfernte_attrs: set[str] = set()
        for element in wurzel.iter():
            attr_namen = list(element.attrib.keys())
            for attr_name in attr_namen:
                if attr_name in UNBEKANNTE_ATTRIBUTE:
                    del element.attrib[attr_name]
                    unbekannt_fixes += 1
                    entfernte_attrs.add(attr_name)
                    geaendert = True

        if unbekannt_fixes > 0:
            protokoll.append(
                {
                    "typ": "info",
                    "text": f"[Reparatur] {unbekannt_fixes} unbekannte(s) Attribut(e) entfernt ({', '.join(entfernte_attrs)}).",
                }
            )

        if geaendert:
            baum.write(str(xml_pfad), pretty_print=True, xml_declaration=True, encoding="UTF-8")
            logger.info(
                "XSD-Kompatibilitaet repariert in %s: %d CamelCase, %d Importbestaetigung, %d unbekannte",
                pseudonymisiere_dateiname(str(xml_pfad)),
                camelcase_fixes,
                import_fixes,
                unbekannt_fixes,
            )

        return geaendert, protokoll

    except Exception as e:
        logger.warning(
            "XSD-Kompatibilitaets-Reparatur fehlgeschlagen (%s): %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
        return False, [
            {"typ": "fehler", "text": f"XSD-Kompatibilitaets-Reparatur fehlgeschlagen: {e}"}
        ]


def lese_xdomea_version(xml_pfad: Path) -> str:
    """
    Liest die XDOMEA-Version aus der Namespace-Deklaration der XML-Datei.

    Returns:
        Versionsstring, z.B. "2.3" oder "3.0".

    Raises:
        ValueError: Wenn die Version nicht erkannt wird.
        RuntimeError: Bei XML-Parsefehlern.
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()
        # Alle Namespaces durchsuchen (Default + benannte Prefixe).
        # Reihenfolge von spezifisch nach allgemein: "2.0.1" muss vor "2.0"
        # greifen, sonst verliert ein Winyard-Export (http://www.xdomea.de/V2.0.1)
        # seine Patch-Stelle.
        alle_ns = list(wurzel.nsmap.values())
        bekannte_versionen = ["2.0.1", "2.0", "2.1", "2.2", "2.3", "2.4", "3.0"]
        for ns in alle_ns:
            for version in bekannte_versionen:
                if version in ns:
                    return version
        raise ValueError(f"Unbekannte XDOMEA-Version in Namespaces: {alle_ns}")
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"Fehler beim Lesen von {xml_pfad}: {e}") from e


def validiere_gegen_xsd(xml_pfad: Path, xsd_pfad: Path) -> list[str]:
    """
    Validiert eine XML-Datei gegen ein XSD-Schema.

    Returns:
        Liste der Validierungsfehler (leer = gültig).

    Raises:
        RuntimeError: Bei Parsefehlern.
    """
    try:
        schema_doc = etree.parse(str(xsd_pfad), safe_xml_parser())
        schema = etree.XMLSchema(schema_doc)
        doc = sicher_parsen(xml_pfad)
        schema.validate(doc)
        return [str(fehler) for fehler in schema.error_log]
    except Exception as e:
        raise RuntimeError(f"XSD-Validierung fehlgeschlagen für {xml_pfad}: {e}") from e


def lese_referenzierte_dateien(xml_pfad: Path) -> list[str]:
    """
    Liest alle in der XDOMEA-XML referenzierten Dateinamen.

    Returns:
        Liste der Dateinamen (ohne Pfad).

    Raises:
        RuntimeError: Bei Parsefehlern.
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()
        # Suche nach Dateireferenzen – angepasst an XDOMEA-Struktur
        referenzen = wurzel.findall(".//{*}Dateiname")
        return [ref.text.strip() for ref in referenzen if ref.text]
    except Exception as e:
        raise RuntimeError(f"Fehler beim Lesen der Referenzen in {xml_pfad}: {e}") from e


def lese_xdomea_metadaten(xml_pfad: Path) -> dict:
    """
    Extrahiert alle archivrelevanten Metadaten aus einer XDOMEA-XML.

    Returns:
        Dictionary mit Metadaten (leere Felder wenn nicht vorhanden).
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()

        def finde_text(element_name: str) -> str:
            """Sucht Element per Wildcard-Namespace und gibt Text zurück."""
            elemente = wurzel.findall(f".//{{*}}{element_name}")
            return elemente[0].text.strip() if elemente and elemente[0].text else ""

        def finde_alle_texte(element_name: str) -> list[str]:
            """Sucht alle Elemente per Wildcard-Namespace und gibt Texte zurück."""
            elemente = wurzel.findall(f".//{{*}}{element_name}")
            return [el.text.strip() for el in elemente if el.text and el.text.strip()]

        metadaten = {
            "aktenzeichen": finde_text("Kennzeichen"),
            "betreff": finde_text("Bemerkung") or finde_text("Betreff"),
            "federfuehrung": finde_text("Federfuehrung"),
            "aufbewahrungsdauer": finde_text("Aufbewahrungsdauer"),
            "aussonderungsart": finde_text("Aussonderungsart"),
            "prozess_id": finde_text("ProzessID"),
            "erstellungszeitpunkt": finde_text("Erstellungszeitpunkt"),
            "absender": [],
            "empfaenger": [],
            "laufzeit_beginn": finde_text("Beginn"),
            "laufzeit_ende": finde_text("Ende"),
            "dateinamen": finde_alle_texte("Dateiname"),
        }

        # Absender/Empfänger extrahieren
        for rolle in ["Absender", "Empfaenger"]:
            personen = wurzel.findall(f".//{{*}}{rolle}")
            for person in personen:
                name_teile = []
                vorname = person.find(".//{*}Vorname/{*}Name")
                nachname = person.find(".//{*}Familienname/{*}Name")
                if vorname is not None and vorname.text:
                    name_teile.append(vorname.text.strip())
                if nachname is not None and nachname.text:
                    name_teile.append(nachname.text.strip())
                kennung = person.find(".//{*}Kennung")
                email = kennung.text.strip() if kennung is not None and kennung.text else ""
                eintrag = " ".join(name_teile)
                if email:
                    eintrag += f" ({email})"
                if eintrag:
                    metadaten[rolle.lower()].append(eintrag)

        return metadaten

    except Exception as e:
        logger.warning(
            "XDOMEA-Metadaten konnten nicht gelesen werden aus %s: %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
        return {}


def _container_zu_dict(container_el, trenner_tags: set[str] | None = None) -> dict:
    """Liest ein Vorgang- oder Akte-Element in das Bewertungs-Dict-Format.

    `trenner_tags` benennt die localnames, die eine eigene Bewertungseinheit
    aufmachen. Ein Dokument wird nur der NÄCHSTGELEGENEN Einheit zugerechnet —
    sonst zählen mehrstufig geschachtelte Container (z.B. regisafe-Teilakten
    "313" > "3131") dasselbe Dokument mehrfach und der Archivar bewertet es
    doppelt. Ohne Angabe gilt der eigene Tag des Containers als Trenner.
    """
    if trenner_tags is None:
        trenner_tags = {etree.QName(container_el).localname}

    def feld(name: str) -> str:
        el = container_el.find(f".//{{*}}{name}")
        return el.text.strip() if el is not None and el.text else ""

    dokumente: list[dict] = []
    for dok_el in container_el.findall(".//{*}Dokument"):
        if not _direkt_zugeordnet(container_el, dok_el, trenner_tags):
            continue

        def dok_feld(name: str, _dok=dok_el) -> str:
            el = _dok.find(f".//{{*}}{name}")
            return el.text.strip() if el is not None and el.text else ""

        dateinamen = [d.text.strip() for d in dok_el.findall(".//{*}Dateiname") if d.text]
        versionen = len(dok_el.findall(".//{*}Version"))
        dokumente.append(
            {
                "kennzeichen": dok_feld("Kennzeichen"),
                "dateiname": dateinamen[0] if dateinamen else "",
                "alle_dateinamen": dateinamen,
                "typ": dok_feld("Typ"),
                "bearbeiter": dok_feld("Bearbeiter"),
                "betreff": dok_feld("Betreff") or dok_feld("Bemerkung"),
                "versionen": versionen,
            }
        )

    return {
        "vorgang_id": feld("ID") or feld("Identifikation") or feld("Kennzeichen"),
        "aktenzeichen": feld("Kennzeichen"),
        "betreff": feld("Betreff") or feld("Bemerkung") or feld("Aktenbetreff"),
        "federfuehrung": feld("Federfuehrung"),
        "aufbewahrungsdauer": feld("Aufbewahrungsdauer"),
        "aussonderungsart": feld("Aussonderungsart"),
        "laufzeit_beginn": feld("Beginn"),
        "laufzeit_ende": feld("Ende"),
        "dokumente": dokumente,
        "_element": container_el,
    }


def _domea_lieferverzeichnis_zu_dict(eintrag_el) -> dict:
    """Liest einen <vorgang>- oder <akte>-Eintrag aus einem DOMEA-Lieferverzeichnis.

    Im Lieferverzeichnis sind alle Metadaten direkt im Akten-XML hinterlegt;
    die einzelnen Container sind über <dateiname> referenziert. Je nach DMS
    listet das Verzeichnis Vorgänge (Bundesarchiv-Referenzfall) oder Akten
    (z.B. nscale eGOV) — beide Ebenen werden gelesen.
    """

    def feld(name: str) -> str:
        el = eintrag_el.find(f".//{{*}}{name}")
        if el is None:
            el = eintrag_el.find(name)  # auch ohne NS suchen
        return el.text.strip() if el is not None and el.text else ""

    return {
        "vorgang_id": feld("id"),
        "aktenzeichen": feld("vorgangszeichen") or feld("aktenzeichen"),
        "betreff": feld("vorgangsbetreff") or feld("aktenbetreff"),
        "federfuehrung": feld("federfuehrung"),
        "aufbewahrungsdauer": "",  # nicht im Lieferverzeichnis vorgesehen
        "aussonderungsart": feld("bewertungsentscheidung"),
        "laufzeit_beginn": feld("anlegedatumDesVorgangs") or feld("anlegedatumDerAkte"),
        "laufzeit_ende": feld("zdaDatumDesVorgangs") or feld("zdaDatumDerAkte"),
        # Im Lieferverzeichnis selbst sind keine Dokumente aufgeführt, nur der
        # Verweis auf die innere Container-Datei.
        "dokumente": [],
        "quelle_zip": feld("dateiname"),
        "_element": eintrag_el,
    }


def _loses_dokument_als_einheit(dok_el) -> dict:
    """Macht ein Dokument ohne übergeordnete Akte/Vorgang zur Bewertungseinheit.

    Das Dokument ist hier zugleich Container und Inhalt — es taucht deshalb
    auch in der eigenen Dokumentliste auf, damit die nachgelagerte Bewertung
    die zugehörigen Dateien findet.
    """
    dokument = _dokument_zu_dict(dok_el)

    def feld(name: str) -> str:
        el = dok_el.find(f".//{{*}}{name}")
        return el.text.strip() if el is not None and el.text else ""

    return {
        "vorgang_id": feld("ID") or dokument.get("kennzeichen", ""),
        "aktenzeichen": dokument.get("kennzeichen", ""),
        "betreff": dokument.get("betreff", ""),
        "federfuehrung": feld("Federfuehrung"),
        "aufbewahrungsdauer": feld("Aufbewahrungsdauer"),
        "aussonderungsart": feld("Aussonderungsart"),
        "laufzeit_beginn": feld("Beginn"),
        "laufzeit_ende": feld("Ende"),
        "dokumente": [dokument],
        "_element": dok_el,
    }


def lese_alle_vorgaenge(xml_pfad: Path) -> list[dict]:
    """
    Iteriert über alle Bewertungseinheiten eines XDOMEA-Pakets.

    Drei Formate werden erkannt:
      1. XDOMEA 2.x: <Vorgang>-Elemente direkt
      2. XDOMEA 3.0: nur <Akte>-Elemente (Dokumente direkt darunter)
      3. DOMEA-Lieferverzeichnis (Bundesarchiv): mehrere <vorgang>-Einträge
         mit Verweis auf eingebettete Vorgangs-ZIPs

    Jeder Eintrag enthält Metadaten plus eine Dokumentliste.
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()

        wurzel_local = etree.QName(wurzel).localname
        # Format 3: DOMEA-Lieferverzeichnis (Bundesarchiv-Schema)
        # Erkennung: Root-Tag enthält "domeaAussonderungListen" (NS ist
        # http://dips.bundesarchiv.de/schema, aber wir prüfen tag-agnostisch).
        if "domeaAussonderungListen" in wurzel_local or "AussonderungListen" in wurzel_local:
            # NS-agnostischer Match. {*}-Pattern findet Elemente in jedem
            # Namespace; auch No-Namespace-Elemente. Daher reicht ein einzelnes find.
            # Je nach DMS listet das Verzeichnis Vorgänge oder Akten.
            lv_eintraege: list = []
            for tag in ("vorgang", "akte"):
                lv_eintraege = wurzel.findall(f"{{*}}{tag}") or wurzel.findall(tag)
                if lv_eintraege:
                    break
            if lv_eintraege:
                logger.info(
                    "DOMEA-Lieferverzeichnis erkannt: %d Einträge in %s",
                    len(lv_eintraege),
                    pseudonymisiere_dateiname(xml_pfad.name),
                )
                return [_domea_lieferverzeichnis_zu_dict(el) for el in lv_eintraege]

        # Format 1: XDOMEA 2.x — Vorgänge
        vorgang_els = wurzel.findall(".//{*}Vorgang")
        if vorgang_els:
            return [_container_zu_dict(el) for el in vorgang_els]

        # Format 2: XDOMEA 3.0 — Akten als Fallback
        akte_els = wurzel.findall(".//{*}Akte")
        if akte_els:
            logger.info(
                "Keine Vorgang-Elemente in %s — fallback auf Akten-Ebene (%d Akten).",
                pseudonymisiere_dateiname(xml_pfad.name),
                len(akte_els),
            )
            return [_container_zu_dict(el) for el in akte_els]

        # Format 4: Dokumente ohne übergeordneten Container. Ältere Abgabe-
        # Nachrichten (XDOMEA 2.0.x) hängen ein Dokument direkt unter
        # <Schriftgutobjekt>. Ohne diesen Zweig bliebe das Paket unbewertbar.
        dok_els = wurzel.findall(".//{*}Dokument")
        if dok_els:
            logger.info(
                "Weder Vorgang noch Akte in %s — %d lose(s) Dokument(e) als Einheiten.",
                pseudonymisiere_dateiname(xml_pfad.name),
                len(dok_els),
            )
            return [_loses_dokument_als_einheit(el) for el in dok_els]

        return []

    except Exception as e:
        logger.warning(
            "Vorgänge konnten nicht gelesen werden aus %s: %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
        return []


def _direkt_zugeordnet(akte_el, kind, zwischen_tags: set[str]) -> bool:
    """True, wenn `kind` direkt zu `akte_el` gehört — also auf dem Weg nach
    oben kein Zwischen-Container (z.B. Vorgang/Teilakte) liegt.

    `zwischen_tags` enthält die localnames, die als trennende Container gelten.
    """
    vorfahr = kind.getparent()
    while vorfahr is not None and vorfahr is not akte_el:
        if etree.QName(vorfahr).localname in zwischen_tags:
            return False
        vorfahr = vorfahr.getparent()
    return True


def _teilakte_zu_dict(teilakte_el) -> dict:
    """Liest eine <Teilakte> samt Vorgängen und losen Dokumenten.

    Wird für 3-stufige Akten (Akte → Teilakte → Dokument/Vorgang) gebraucht,
    wie sie z.B. regisafe exportiert.
    """

    def feld(name: str) -> str:
        el = teilakte_el.find(f".//{{*}}{name}")
        return el.text.strip() if el is not None and el.text else ""

    vorgaenge = [_container_zu_dict(v) for v in teilakte_el.findall(".//{*}Vorgang")]
    dokumente: list[dict] = []
    for kind in teilakte_el.iter():
        if etree.QName(kind).localname != "Dokument":
            continue
        if _direkt_zugeordnet(teilakte_el, kind, {"Vorgang", "Teilakte"}):
            dokumente.append(_dokument_zu_dict(kind))

    return {
        "aktenzeichen": feld("Kennzeichen"),
        "betreff": feld("Betreff") or feld("Aktenbetreff") or feld("Bemerkung"),
        "federfuehrung": feld("Federfuehrung"),
        "aufbewahrungsdauer": feld("Aufbewahrungsdauer"),
        "aussonderungsart": feld("Aussonderungsart"),
        "laufzeit_beginn": feld("Beginn"),
        "laufzeit_ende": feld("Ende"),
        "vorgaenge": vorgaenge,
        "dokumente": dokumente,
        "_element": teilakte_el,
    }


def _akte_zu_dict(akte_el) -> dict:
    """Liest ein <Akte>-Element samt aller darin enthaltenen Vorgänge.

    Die echte XDOMEA-Hierarchie bleibt erhalten:
      - Dokumente in <Vorgang> werden dem jeweiligen Vorgang zugeordnet
      - <Teilakte> (3-stufige Akten, z.B. regisafe) werden als eigene
        Untereinheiten erfasst
      - Dokumente, die weder in einem Vorgang noch in einer Teilakte liegen,
        bleiben "lose" an der Akte hängen
    """

    def feld(name: str) -> str:
        el = akte_el.find(f".//{{*}}{name}")
        return el.text.strip() if el is not None and el.text else ""

    # Teilakten (können selbst Vorgänge/Dokumente enthalten)
    teilakten = [_teilakte_zu_dict(t) for t in akte_el.findall(".//{*}Teilakte")]

    # Vorgänge, die DIREKT unter der Akte hängen (nicht in einer Teilakte)
    vorgaenge: list[dict] = []
    for vorgang_el in akte_el.findall(".//{*}Vorgang"):
        if _direkt_zugeordnet(akte_el, vorgang_el, {"Teilakte"}):
            vorgaenge.append(_container_zu_dict(vorgang_el))

    # Lose Dokumente: weder in Vorgang noch in Teilakte
    direkte_dokumente: list[dict] = []
    for kind in akte_el.iter():
        if etree.QName(kind).localname != "Dokument":
            continue
        if _direkt_zugeordnet(akte_el, kind, {"Vorgang", "Teilakte"}):
            direkte_dokumente.append(_dokument_zu_dict(kind))

    return {
        "aktenzeichen": feld("Kennzeichen"),
        "betreff": feld("Betreff") or feld("Aktenbetreff") or feld("Bemerkung"),
        "federfuehrung": feld("Federfuehrung"),
        "aufbewahrungsdauer": feld("Aufbewahrungsdauer"),
        "aussonderungsart": feld("Aussonderungsart"),
        "laufzeit_beginn": feld("Beginn"),
        "laufzeit_ende": feld("Ende"),
        "teilakten": teilakten,
        "vorgaenge": vorgaenge,
        "dokumente": direkte_dokumente,
        "_element": akte_el,
    }


def _dokument_zu_dict(dok_el) -> dict:
    """Liest ein einzelnes <Dokument>-Element in das Browser-Dict-Format."""

    def feld(name: str) -> str:
        el = dok_el.find(f".//{{*}}{name}")
        return el.text.strip() if el is not None and el.text else ""

    dateinamen = [d.text.strip() for d in dok_el.findall(".//{*}Dateiname") if d.text]
    return {
        "kennzeichen": feld("Kennzeichen"),
        "dateiname": dateinamen[0] if dateinamen else "",
        "alle_dateinamen": dateinamen,
        "typ": feld("Typ"),
        "bearbeiter": feld("Bearbeiter"),
        "betreff": feld("Betreff") or feld("Bemerkung"),
        "versionen": len(dok_el.findall(".//{*}Version")),
    }


def lese_paket_struktur(xml_pfad: Path) -> dict:
    """Liefert die volle Hierarchie eines XDOMEA-Pakets für die Browser-Ansicht.

    Im Gegensatz zu `lese_alle_vorgaenge` (das eine flache Liste für die
    Bewertungs-Pipeline liefert) bleibt hier die echte Schachtelung erhalten:

      {
        "ebene": "akten" | "vorgaenge",
        "akten":     [ {az, betreff, …, vorgaenge: [...], dokumente: [...]} ],
        "vorgaenge": [ {az, betreff, …, dokumente: [...]} ],   # nur bei "ebene": "vorgaenge"
      }

    Erkennung:
      - <Akte> vorhanden → akten-basiertes Paket (Aktenebene ist die Wurzel)
      - sonst nur <Vorgang> → vorgangsbasiert (klassisches XDOMEA 2.x)
    """
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()

        akte_els = wurzel.findall(".//{*}Akte")
        if akte_els:
            return {
                "ebene": "akten",
                "akten": [_akte_zu_dict(el) for el in akte_els],
            }

        vorgang_els = wurzel.findall(".//{*}Vorgang")
        if vorgang_els:
            return {
                "ebene": "vorgaenge",
                "vorgaenge": [_container_zu_dict(el) for el in vorgang_els],
            }

        # Dokumente ohne Akte/Vorgang (XDOMEA-2.0.x-Abgaben) bilden jeweils
        # eine eigene Einheit — sie werden wie Vorgänge transportiert, die
        # Ebene weist sie aber korrekt als Dokumentebene aus.
        dok_els = wurzel.findall(".//{*}Dokument")
        if dok_els:
            return {
                "ebene": "dokumente",
                "vorgaenge": [_loses_dokument_als_einheit(el) for el in dok_els],
            }

        return {"ebene": "leer"}
    except Exception as e:
        logger.warning(
            "Paketstruktur konnte nicht gelesen werden aus %s: %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
        return {"ebene": "leer"}


# ---------------------------------------------------------------------------
# Multi-XML-Pakete: Renditions-Dedup + konsolidierte Strukturanalyse
#
# Manche DMS exportieren ein Aussonderungspaket nicht als eine zentrale XML,
# sondern verteilt: enaio z.B. legt pro Akte einen eigenen Ordner an und
# liefert zusätzlich eine zweite Kopie der gesamten Akte als PDF/A-Rendition
# (Ordner "Originalformat" + "PDFA"). Würde man jede XML separat zählen,
# erschienen Akten/Vorgänge doppelt. Die folgenden Funktionen erkennen solche
# parallelen Renditionen anhand einer inhaltlichen Signatur und konsolidieren
# das Paket auf seine logischen Einheiten.
# ---------------------------------------------------------------------------


def finde_xdomea_xmls(arbeitsverzeichnis: Path) -> list[Path]:
    """Findet alle XDOMEA-Nachrichten-XMLs in einem entpackten Paket.

    Protokoll-XMLs der Agentenläufe werden ausgeschlossen. Es werden nur
    XMLs zurückgegeben, deren Wurzel eine XDOMEA-Aussonderungsnachricht ist.
    """
    treffer: list[Path] = []
    for x in sorted(arbeitsverzeichnis.rglob("*.xml")):
        if x.name.lower().startswith("protokoll_"):
            continue
        try:
            wurzel = sicher_parsen(x).getroot()
        except Exception:
            continue
        if wurzel is None:
            continue
        local = etree.QName(wurzel).localname
        # XDOMEA-Aussonderung / DOMEA-Lieferverzeichnis am Root, ODER eine XML,
        # die XDOMEA-Container (Akte/Vorgang/Schriftgutobjekt) enthält. Letzteres
        # deckt abweichende Root-Tags fremder DMS und Minimal-XMLs ab.
        if "Aussonderung" in local or "AussonderungListen" in local:
            treffer.append(x)
            continue
        if (
            wurzel.find(".//{*}Schriftgutobjekt") is not None
            or wurzel.find(".//{*}Akte") is not None
            or wurzel.find(".//{*}Vorgang") is not None
        ):
            treffer.append(x)
    return treffer


def _paket_signatur(xml_pfad: Path) -> tuple:
    """Berechnet eine inhaltliche Signatur einer XDOMEA-XML.

    Die Signatur ist bewusst dateinamens-unabhängig (Renditionen unterscheiden
    sich nur in den Dateinamen/Formaten, nicht im fachlichen Inhalt). Sie
    besteht aus den Kennzeichen/Betreffs aller Akten, Vorgänge und Dokumente.
    Zwei XMLs mit gleicher Signatur sind Renditionen desselben Inhalts.
    """
    try:
        wurzel = sicher_parsen(xml_pfad).getroot()
    except Exception:
        return (xml_pfad.name,)

    teile: list[str] = []
    for tag in ("Akte", "Teilakte", "Vorgang", "Dokument"):
        werte: list[str] = []
        for el in wurzel.findall(f".//{{*}}{tag}"):
            kennz = el.find("{*}AllgemeineMetadaten/{*}Kennzeichen")
            if kennz is None:
                kennz = el.find(".//{*}Kennzeichen")
            betreff = el.find("{*}AllgemeineMetadaten/{*}Betreff")
            if betreff is None:
                betreff = el.find(".//{*}Betreff")
            k = kennz.text.strip() if kennz is not None and kennz.text else ""
            b = betreff.text.strip() if betreff is not None and betreff.text else ""
            werte.append(f"{k}|{b}")
        teile.append(tag + ":" + ";".join(sorted(werte)))
    return tuple(teile)


def dedupe_rendition_xmls(xml_pfade: list[Path]) -> tuple[list[Path], int]:
    """Reduziert eine XML-Liste auf je eine kanonische Nachricht pro Inhalt.

    Entfernt zwei Arten von Dubletten: parallele Renditionen desselben Inhalts
    (z.B. enaio Originalformat + PDFA) und Lieferverzeichnisse, deren Container
    bereits entpackt vorliegen.

    Liefert (kanonische_xmls, anzahl_entfernter_renditionen). Bei gleicher
    Signatur wird die zuerst einsortierte XML behalten (sortierte Pfade →
    "Originalformat" gewinnt typischerweise vor "PDFA").
    """
    gesehen: dict[tuple, Path] = {}
    entfernt = 0
    for x in sorted(ohne_redundante_lieferverzeichnisse(list(xml_pfade))):
        sig = _paket_signatur(x)
        if sig in gesehen:
            entfernt += 1
            logger.info(
                "Renditions-Dublette übersprungen: %s (gleicher Inhalt wie %s)",
                pseudonymisiere_dateiname(x.name),
                pseudonymisiere_dateiname(gesehen[sig].name),
            )
            continue
        gesehen[sig] = x
    return list(gesehen.values()), entfernt


def _ist_lieferverzeichnis(xml_pfad: Path) -> bool:
    """True, wenn die XML ein DOMEA-Lieferverzeichnis (Bundesarchiv-Schema) ist."""
    try:
        wurzel = sicher_parsen(xml_pfad).getroot()
    except Exception:
        return False
    return "AussonderungListen" in etree.QName(wurzel).localname


def ohne_redundante_lieferverzeichnisse(xml_pfade: list[Path]) -> list[Path]:
    """Filtert Lieferverzeichnisse heraus, sobald die Container entpackt sind.

    Ein Lieferverzeichnis beschreibt dieselben Akten/Vorgänge, die auch in den
    referenzierten Containern stehen. Liegen beide vor, würde jede Einheit
    doppelt erscheinen — einmal als dokumentloser Verzeichniseintrag. Sind die
    Container dagegen (noch) nicht entpackt, bleibt das Verzeichnis die einzige
    Informationsquelle und wird behalten.
    """
    verzeichnisse = [x for x in xml_pfade if _ist_lieferverzeichnis(x)]
    if not verzeichnisse:
        return list(xml_pfade)

    echte = [x for x in xml_pfade if x not in verzeichnisse]
    if not echte:
        return list(xml_pfade)

    logger.info(
        "%d Lieferverzeichnis(se) übersprungen — die referenzierten Container liegen entpackt vor.",
        len(verzeichnisse),
    )
    return echte


def _bestimme_ebene(akten: list[dict], vorgaenge: list[dict]) -> str:
    """Leitet die fachliche Bearbeitungsebene eines Pakets ab."""
    if not akten and not vorgaenge:
        return "leer"
    if not akten:
        return "vorgaenge"
    hat_vorgaenge = any(
        a.get("vorgaenge") or any(t.get("vorgaenge") for t in a.get("teilakten", [])) for a in akten
    )
    hat_teilakten = any(a.get("teilakten") for a in akten)
    if hat_vorgaenge and hat_teilakten:
        return "gemischt"
    if hat_teilakten:
        return "teilakten"
    if hat_vorgaenge:
        return "akten_mit_vorgaengen"
    return "akten"


def _zaehle_dokumente_in_akte(akte: dict) -> int:
    n = len(akte.get("dokumente", []) or [])
    for vg in akte.get("vorgaenge", []) or []:
        n += len(vg.get("dokumente", []) or [])
    for ta in akte.get("teilakten", []) or []:
        n += len(ta.get("dokumente", []) or [])
        for vg in ta.get("vorgaenge", []) or []:
            n += len(vg.get("dokumente", []) or [])
    return n


def _zaehle_vorgaenge_in_akte(akte: dict) -> int:
    n = len(akte.get("vorgaenge", []) or [])
    for ta in akte.get("teilakten", []) or []:
        n += len(ta.get("vorgaenge", []) or [])
    return n


def analysiere_paket_struktur(xml_pfade: list[Path]) -> dict:
    """Konsolidiert die Struktur eines (ggf. mehrdateiligen) XDOMEA-Pakets.

    Aggregiert über ALLE übergebenen XMLs, entfernt Renditions-Dubletten
    (z.B. enaio Originalformat/PDFA) und liefert eine zusammengeführte
    Hierarchie samt Zählungen und fachlicher Ebene:

      {
        "ebene": "akten" | "akten_mit_vorgaengen" | "teilakten" |
                 "gemischt" | "vorgaenge" | "leer",
        "akten":     [ {…, teilakten, vorgaenge, dokumente} ],
        "vorgaenge": [ … ],   # nur wenn das Paket keine Akten hat
        "statistik": {"akten", "teilakten", "vorgaenge", "dokumente"},
        "renditions_entfernt": int,
      }
    """
    kanonische, entfernt = dedupe_rendition_xmls(list(xml_pfade))

    akten: list[dict] = []
    vorgaenge: list[dict] = []
    nur_lose_dokumente = False
    for xml_pfad in kanonische:
        teil = lese_paket_struktur(xml_pfad)
        if teil.get("ebene") == "akten":
            akten.extend(teil.get("akten", []))
        elif teil.get("ebene") == "vorgaenge":
            vorgaenge.extend(teil.get("vorgaenge", []))
        elif teil.get("ebene") == "dokumente":
            vorgaenge.extend(teil.get("vorgaenge", []))
            nur_lose_dokumente = True

    ebene = _bestimme_ebene(akten, vorgaenge)
    if not akten and nur_lose_dokumente:
        ebene = "dokumente"

    teilakten_gesamt = sum(len(a.get("teilakten", []) or []) for a in akten)
    if akten:
        vorgaenge_gesamt = sum(_zaehle_vorgaenge_in_akte(a) for a in akten)
        dokumente_gesamt = sum(_zaehle_dokumente_in_akte(a) for a in akten)
    else:
        # Lose Dokumente sind keine Vorgänge — sonst zählt die Statistik
        # eine Vorgangsebene aus, die das Paket gar nicht hat.
        vorgaenge_gesamt = 0 if nur_lose_dokumente else len(vorgaenge)
        dokumente_gesamt = sum(len(v.get("dokumente", []) or []) for v in vorgaenge)

    return {
        "ebene": ebene,
        "akten": akten,
        "vorgaenge": vorgaenge,
        "statistik": {
            "akten": len(akten),
            "teilakten": teilakten_gesamt,
            "vorgaenge": vorgaenge_gesamt,
            "dokumente": dokumente_gesamt,
        },
        "renditions_entfernt": entfernt,
    }


# Mögliche Bewertungs-/Erschließungs-Ebenen (von grob nach fein).
EBENEN_REIHENFOLGE = ["akte", "teilakte", "vorgang"]
_EBENE_TAG = {"akte": "Akte", "teilakte": "Teilakte", "vorgang": "Vorgang"}


def verfuegbare_ebenen(xml_pfade: list[Path]) -> dict:
    """Ermittelt, welche Bewertungs-Ebenen ein Paket tatsächlich anbietet.

    Returns:
        {
          "ebenen":  Teilmenge von ["akte", "teilakte", "vorgang"],
          "default": feinste vorhandene Ebene (Vorgang > Teilakte > Akte) | None,
        }
    """
    kanonische, _ = dedupe_rendition_xmls(list(xml_pfade))
    hat = {"akte": False, "teilakte": False, "vorgang": False}
    for xml_pfad in kanonische:
        try:
            wurzel = sicher_parsen(xml_pfad).getroot()
        except Exception:
            continue
        for ebene, tag in _EBENE_TAG.items():
            if not hat[ebene] and wurzel.find(f".//{{*}}{tag}") is not None:
                hat[ebene] = True
    ebenen = [e for e in EBENEN_REIHENFOLGE if hat[e]]
    # feinste vorhandene Ebene als Vorauswahl
    default = ebenen[-1] if ebenen else None
    return {"ebenen": ebenen, "default": default}


def lese_bewertungseinheiten(xml_pfade: list[Path], ebene: str = "auto") -> list[dict]:
    """Liefert die flache Liste der Bewertungseinheiten eines Pakets.

    Über mehrere XMLs hinweg, mit Renditions-Dedup (z.B. enaio). Jede Einheit
    erhält eine garantiert eindeutige `vorgang_id` — auch wenn das Quell-DMS
    leere/doppelte Kennzeichen vergibt (sonst kollidiert die DB-Persistenz).

    Args:
        ebene: "akte" | "teilakte" | "vorgang" — die gewünschte Bewertungs-
            ebene. Jedes Element dieser Ebene wird zu einer Einheit; die
            Dokumente werden darunter aggregiert. Bei "auto" (Default) gilt die
            klassische Heuristik (Vorgang falls vorhanden, sonst Akte). Ist die
            gewählte Ebene in einer XML nicht vorhanden, fällt sie auf "auto"
            zurück.
    """
    kanonische, _ = dedupe_rendition_xmls(list(xml_pfade))
    tag = _EBENE_TAG.get(ebene)

    einheiten: list[dict] = []
    for xml_pfad in kanonische:
        if tag is None:  # "auto"
            einheiten.extend(lese_alle_vorgaenge(xml_pfad))
            continue
        try:
            wurzel = sicher_parsen(xml_pfad).getroot()
        except Exception:
            continue
        elemente = wurzel.findall(f".//{{*}}{tag}")
        if not elemente:
            # Gewählte Ebene in dieser XML nicht vorhanden → Auto-Fallback.
            einheiten.extend(lese_alle_vorgaenge(xml_pfad))
            continue
        einheiten.extend(_container_zu_dict(el) for el in elemente)

    _vergebe_eindeutige_vorgang_ids(einheiten)
    return einheiten


def _vergebe_eindeutige_vorgang_ids(einheiten: list[dict]) -> None:
    """Vergibt jeder Einheit eine garantiert eindeutige `vorgang_id` (in-place)."""
    vergeben: set[str] = set()
    for einheit in einheiten:
        basis = (
            einheit.get("vorgang_id")
            or einheit.get("aktenzeichen")
            or einheit.get("betreff")
            or "einheit"
        ).strip() or "einheit"
        kandidat = basis
        suffix = 1
        while kandidat in vergeben:
            suffix += 1
            kandidat = f"{basis}#{suffix}"
        vergeben.add(kandidat)
        einheit["vorgang_id"] = kandidat


def lese_einheiten_mit_element(xml_pfade: list[Path], ebene: str = "auto") -> list[dict]:
    """Wie `lese_bewertungseinheiten`, liefert aber zusätzlich pro Einheit das
    geparste XML-Element, den Baum und den Dateipfad — für den Write-Back der
    Erschließung in genau die richtige Einheit.

    Die vergebenen `vorgang_id` sind identisch zu `lese_bewertungseinheiten`
    (gleiche Ebene, gleiche Reihenfolge), sodass Bewertung und Erschließung
    dieselben Einheiten referenzieren.

    Returns:
        Liste von {"xml": Path, "baum": ElementTree, "element": _Element,
                   "einheit": <dict wie lese_bewertungseinheiten>}.
    """
    kanonische, _ = dedupe_rendition_xmls(list(xml_pfade))
    tag = _EBENE_TAG.get(ebene)

    eintraege: list[dict] = []
    for xml_pfad in kanonische:
        try:
            baum = sicher_parsen(xml_pfad)
        except Exception:
            continue
        wurzel = baum.getroot()
        if tag is None:  # "auto": Vorgang falls vorhanden, sonst Akte
            elemente = wurzel.findall(".//{*}Vorgang") or wurzel.findall(".//{*}Akte")
        else:
            elemente = wurzel.findall(f".//{{*}}{tag}")
            if not elemente:  # gewählte Ebene fehlt → Auto-Fallback
                elemente = wurzel.findall(".//{*}Vorgang") or wurzel.findall(".//{*}Akte")
        for el in elemente:
            eintraege.append(
                {"xml": xml_pfad, "baum": baum, "element": el, "einheit": _container_zu_dict(el)}
            )

    _vergebe_eindeutige_vorgang_ids([e["einheit"] for e in eintraege])
    return eintraege


def formatiere_metadaten_fuer_gpt(metadaten: dict, ohne_dateiliste: bool = False) -> str:
    """Formatiert XDOMEA-Metadaten als lesbaren Text für GPT-Kontext."""
    if not metadaten:
        return ""

    zeilen = ["=== XDOMEA-METADATEN ==="]
    if metadaten.get("aktenzeichen"):
        zeilen.append(f"Aktenzeichen: {metadaten['aktenzeichen']}")
    if metadaten.get("betreff"):
        zeilen.append(f"Betreff/Bemerkung: {metadaten['betreff']}")
    if metadaten.get("federfuehrung"):
        zeilen.append(f"Federführung: {metadaten['federfuehrung']}")
    if metadaten.get("aufbewahrungsdauer"):
        zeilen.append(f"Aufbewahrungsdauer: {metadaten['aufbewahrungsdauer']} Jahre")
    if metadaten.get("aussonderungsart"):
        zeilen.append(f"Aussonderungsart: {metadaten['aussonderungsart']}")
    if metadaten.get("laufzeit_beginn") or metadaten.get("laufzeit_ende"):
        zeilen.append(
            f"Laufzeit: {metadaten.get('laufzeit_beginn', '?')} bis {metadaten.get('laufzeit_ende', '?')}"
        )
    if metadaten.get("absender"):
        zeilen.append(f"Absender: {', '.join(metadaten['absender'])}")
    if metadaten.get("empfaenger"):
        zeilen.append(f"Empfänger: {', '.join(metadaten['empfaenger'])}")
    if metadaten.get("dateinamen") and not ohne_dateiliste:
        zeilen.append(
            f"Dokumente ({len(metadaten['dateinamen'])}): {', '.join(metadaten['dateinamen'][:20])}"
        )
        if len(metadaten["dateinamen"]) > 20:
            zeilen.append(f"  … und {len(metadaten['dateinamen']) - 20} weitere")
    if metadaten.get("erstellungszeitpunkt"):
        zeilen.append(f"Erstellt: {metadaten['erstellungszeitpunkt']}")

    return "\n".join(zeilen) + "\n"


def _xdomea_ns(wurzel) -> str:
    """Liefert das Namespace-Präfix `{uri}` der XDOMEA-Wurzel (oder "")."""
    for prefix, uri in (wurzel.nsmap or {}).items():
        if "xdomea" in (uri or "").lower() or prefix == "xdomea":
            return f"{{{uri}}}"
    return ""


def schreibe_erschliessung_in_element(container, erschliessung: dict) -> bool:
    """Schreibt Erschließungsdaten als Bemerkung in ein konkretes Container-
    Element (Akte/Teilakte/Vorgang). Gibt True bei Erfolg zurück.

    Wird sowohl von der paketweiten als auch von der einheiten-bezogenen
    Erschließung genutzt — der Aufrufer speichert den Baum.
    """
    if container is None:
        return False
    wurzel = container.getroottree().getroot()
    ns = _xdomea_ns(wurzel)

    metadaten = (
        container.find(f"{ns}AllgemeineMetadaten")
        if ns
        else container.find("{*}AllgemeineMetadaten")
    )
    if metadaten is None:
        metadaten = (
            container.find(f".//{ns}AllgemeineMetadaten")
            if ns
            else container.find(".//{*}AllgemeineMetadaten")
        )
    if metadaten is None:
        metadaten = etree.SubElement(
            container, f"{ns}AllgemeineMetadaten" if ns else "AllgemeineMetadaten"
        )

    teile = []
    if erschliessung.get("titel"):
        teile.append(f"Titel: {erschliessung['titel']}")
    if erschliessung.get("inhaltsangabe"):
        teile.append(f"Inhaltsangabe: {erschliessung['inhaltsangabe']}")
    if erschliessung.get("schlagworte"):
        schlagworte = erschliessung["schlagworte"]
        if isinstance(schlagworte, list):
            schlagworte = ", ".join(schlagworte)
        teile.append(f"Schlagworte: {schlagworte}")
    if erschliessung.get("personen"):
        personen = erschliessung["personen"]
        if isinstance(personen, list):
            personen = ", ".join(personen)
        teile.append(f"Personen: {personen}")
    if erschliessung.get("zeitraum"):
        teile.append(f"Zeitraum: {erschliessung['zeitraum']}")

    erschliessung_text = " | ".join(teile)

    bemerkung = metadaten.find(f"{ns}Bemerkung") if ns else metadaten.find("{*}Bemerkung")
    if bemerkung is None:
        bemerkung = etree.SubElement(metadaten, f"{ns}Bemerkung" if ns else "Bemerkung")

    # Eine evtl. frühere [Erschließung]-Notiz ersetzen statt anhängen.
    bestehend = bemerkung.text or ""
    if "[Erschließung]" in bestehend:
        bestehend = bestehend.split("[Erschließung]")[0].rstrip(" |")
    if bestehend and not bestehend.endswith(" | "):
        bestehend += " | "
    bemerkung.text = bestehend + f"[Erschließung] {erschliessung_text}"
    return True


def schreibe_erschliessung(xml_pfad: Path, erschliessung: dict) -> None:
    """Schreibt eine (paketweite) Erschließung in die erste Akte/Vorgang der XML."""
    try:
        baum = sicher_parsen(xml_pfad)
        wurzel = baum.getroot()
        container = (
            wurzel.find(".//{*}Vorgang")
            or wurzel.find(".//{*}Akte")
            or wurzel.find(".//{*}Schriftgutobjekt")
        )
        if not schreibe_erschliessung_in_element(container, erschliessung):
            logger.warning(
                "Kein Vorgang/Akte-Element gefunden in %s — Erschließung übersprungen.",
                pseudonymisiere_dateiname(str(xml_pfad)),
            )
            return
        baum.write(str(xml_pfad), pretty_print=True, xml_declaration=True, encoding="UTF-8")
        logger.info(
            "Erschließungsdaten in XDOMEA geschrieben: %s", pseudonymisiere_dateiname(str(xml_pfad))
        )
    except Exception as e:
        logger.warning(
            "Erschließung konnte nicht ins XML geschrieben werden (%s): %s",
            pseudonymisiere_dateiname(str(xml_pfad)),
            e,
        )
