# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die ausgelieferten XDOMEA-Schemadateien.

Die Schemata liegen unter backend/assets/xsd/. Ein fehlgeschlagener
Download hinterlässt leicht eine HTML-Fehlerseite mit .xsd-Endung — dann
schlägt die Validierung für die ganze XDOMEA-Version fehl, ohne dass ein
Paket schuld wäre. Genau das war bei v2.4 der Fall.
"""

from pathlib import Path

import pytest
from lxml import etree
from utils.xdomea_parser import safe_xml_parser

XSD_WURZEL = Path(__file__).resolve().parent.parent / "assets" / "xsd"

# Je Version das Schema, gegen das der Formal-Agent Aussonderungen prüft.
AUSSONDERUNG_SCHEMATA = sorted(
    p
    for p in XSD_WURZEL.glob("v*/*.xsd")
    if "AussonderungDurchfuehren" in p.name and "Nachrichten" in p.name
)


def test_schemata_sind_vorhanden():
    """Ohne Schemata kann der Formal-Agent nichts validieren."""
    assert AUSSONDERUNG_SCHEMATA, f"Keine Aussonderungs-Schemata unter {XSD_WURZEL}"


@pytest.mark.parametrize("xsd", AUSSONDERUNG_SCHEMATA, ids=lambda p: p.parent.name)
def test_schema_laesst_sich_laden(xsd: Path):
    """Jedes Schema muss samt seiner Importe auflösbar sein."""
    try:
        etree.XMLSchema(etree.parse(str(xsd), safe_xml_parser()))
    except Exception as e:
        pytest.fail(f"{xsd.parent.name}/{xsd.name} nicht ladbar: {str(e)[:200]}")


def test_keine_html_fehlerseiten_als_schema():
    """Ein fehlgeschlagener Download darf nicht als .xsd liegenbleiben."""
    kaputt = []
    for p in XSD_WURZEL.rglob("*.xsd"):
        kopf = p.read_bytes()[:200].lstrip().lower()
        if kopf.startswith(b"<!doctype html") or b"404 not found" in kopf:
            kaputt.append(str(p.relative_to(XSD_WURZEL)))
    assert not kaputt, f"HTML-Fehlerseiten mit .xsd-Endung: {kaputt}"


def test_keine_absoluten_schema_importe():
    """Importe müssen relativ sein — Netzwerkzugriff ist beim Parsen gesperrt."""
    absolut = []
    for p in XSD_WURZEL.rglob("*.xsd"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if 'schemaLocation="http' in text:
            absolut.append(str(p.relative_to(XSD_WURZEL)))
    assert not absolut, f"Absolute schemaLocation-Importe (nicht auflösbar): {absolut}"
