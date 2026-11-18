"""Tests für den XDOMEA-Parser."""

from pathlib import Path

import pytest
from utils.xdomea_parser import (
    lese_referenzierte_dateien,
    lese_xdomea_version,
    validiere_gegen_xsd,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
XSD_DIR = Path(__file__).parent.parent / "assets" / "xsd"


def test_xdomea_version_23():
    """Erkennt XDOMEA 2.3 korrekt."""
    version = lese_xdomea_version(FIXTURES_DIR / "gueltig_xdomea.xml")
    assert version == "2.3"


def test_xdomea_version_unbekannt(tmp_path: Path):
    """Wirft ValueError bei unbekanntem Namespace."""
    xml = tmp_path / "unbekannt.xml"
    xml.write_text('<?xml version="1.0"?><Root xmlns="urn:unbekannt"/>', encoding="utf-8")
    with pytest.raises(ValueError, match="Unbekannte XDOMEA-Version"):
        lese_xdomea_version(xml)


def test_validiere_gegen_xsd_gueltig():
    """Gültige XML hat keine XSD-Fehler."""
    xsd_pfad = XSD_DIR / "xdomea_2.3.xsd"
    if not xsd_pfad.exists():
        pytest.skip("XSD-Schema nicht vorhanden.")
    fehler = validiere_gegen_xsd(FIXTURES_DIR / "gueltig_xdomea.xml", xsd_pfad)
    assert fehler == []


def test_validiere_gegen_xsd_ungueltig():
    """Ungültige XML liefert XSD-Fehler."""
    xsd_pfad = XSD_DIR / "xdomea_2.3.xsd"
    if not xsd_pfad.exists():
        pytest.skip("XSD-Schema nicht vorhanden.")
    fehler = validiere_gegen_xsd(FIXTURES_DIR / "ungueltig_xdomea.xml", xsd_pfad)
    assert len(fehler) > 0


def test_lese_referenzierte_dateien():
    """Liest Dateireferenzen aus gültiger XDOMEA-XML."""
    referenzen = lese_referenzierte_dateien(FIXTURES_DIR / "gueltig_xdomea.xml")
    assert "testdokument.pdf" in referenzen
    assert "testdokument.docx" in referenzen
    assert len(referenzen) == 2


from utils.xdomea_parser import lese_alle_vorgaenge


def test_lese_alle_vorgaenge_liefert_liste(tmp_path):
    xml = tmp_path / "test.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Aktenplan xmlns="urn:xoev-de:xdomea:schema:3.0.0">
  <Vorgang>
    <Kennzeichen>12-34/2024</Kennzeichen>
    <Bemerkung>Vorgang A</Bemerkung>
    <Federfuehrung>Eigene Stelle</Federfuehrung>
    <Aufbewahrungsdauer>30</Aufbewahrungsdauer>
    <Dokument><Kennzeichen>D1</Kennzeichen><Dateiname>d1.pdf</Dateiname></Dokument>
    <Dokument><Kennzeichen>D2</Kennzeichen><Dateiname>d2.pdf</Dateiname></Dokument>
  </Vorgang>
  <Vorgang>
    <Kennzeichen>99-12/2024</Kennzeichen>
    <Bemerkung>Vorgang B</Bemerkung>
  </Vorgang>
</Aktenplan>
""",
        encoding="utf-8",
    )

    vorgaenge = lese_alle_vorgaenge(xml)

    assert len(vorgaenge) == 2
    assert vorgaenge[0]["aktenzeichen"] == "12-34/2024"
    assert vorgaenge[0]["betreff"] == "Vorgang A"
    assert vorgaenge[0]["federfuehrung"] == "Eigene Stelle"
    assert vorgaenge[0]["aufbewahrungsdauer"] == "30"
    assert len(vorgaenge[0]["dokumente"]) == 2
    assert vorgaenge[0]["dokumente"][0]["dateiname"] == "d1.pdf"
    assert vorgaenge[1]["aktenzeichen"] == "99-12/2024"
    assert vorgaenge[1]["dokumente"] == []


def test_lese_alle_vorgaenge_leer_bei_fehler(tmp_path):
    xml = tmp_path / "kaputt.xml"
    xml.write_text("<<<kein-xml", encoding="utf-8")
    assert lese_alle_vorgaenge(xml) == []


def test_lese_alle_vorgaenge_domea_lieferverzeichnis(tmp_path):
    """DOMEA-Lieferverzeichnis (Bundesarchiv) mit eingebetteten Vorgangs-ZIPs."""
    xml = tmp_path / "lv.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<ns:domeaAussonderungListen xmlns:ns="http://dips.bundesarchiv.de/schema">
  <aussonderung>
    <listentyp>Lieferverzeichnis</listentyp>
    <abgebendeStelle>9999 Beispielbehörde</abgebendeStelle>
  </aussonderung>
  <vorgang>
    <id>VG-1</id>
    <vorgangszeichen>0000-0001#2026/0002-9999 001</vorgangszeichen>
    <vorgangsbetreff>Test Paket 1</vorgangsbetreff>
    <federfuehrung>9999 001</federfuehrung>
    <bewertungsentscheidung>A</bewertungsentscheidung>
    <dateiname>paket1.zip</dateiname>
    <laufzeit><anlegedatumDesVorgangs>2026-04-15</anlegedatumDesVorgangs></laufzeit>
  </vorgang>
  <vorgang>
    <id>VG-2</id>
    <vorgangszeichen>0000-0001#2026/0001-9999 001</vorgangszeichen>
    <vorgangsbetreff>Test Paket 2</vorgangsbetreff>
    <federfuehrung>9999 001</federfuehrung>
    <bewertungsentscheidung>V</bewertungsentscheidung>
    <dateiname>paket2.zip</dateiname>
  </vorgang>
</ns:domeaAussonderungListen>
""",
        encoding="utf-8",
    )

    einheiten = lese_alle_vorgaenge(xml)
    assert len(einheiten) == 2
    assert einheiten[0]["aktenzeichen"] == "0000-0001#2026/0002-9999 001"
    assert einheiten[0]["betreff"] == "Test Paket 1"
    assert einheiten[0]["federfuehrung"] == "9999 001"
    assert einheiten[0]["aussonderungsart"] == "A"
    assert einheiten[0]["quelle_zip"] == "paket1.zip"
    assert einheiten[1]["aussonderungsart"] == "V"


def test_lese_alle_vorgaenge_fallback_auf_akte_in_xdomea_3(tmp_path):
    """XDOMEA 3.0 hat Dokumente direkt unter der Akte — Parser muss auf Akten-Ebene fallen."""
    xml = tmp_path / "v3.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Aussonderung xmlns="urn:xoev-de:xdomea:schema:3.0.0">
  <Schriftgutobjekt>
    <Akte>
      <Identifikation>af07-akte-1</Identifikation>
      <Kennzeichen>T 2:Akte3</Kennzeichen>
      <Aktenbetreff>Akte3 zum Archivieren</Aktenbetreff>
      <Dokument><Kennzeichen>D1</Kennzeichen><Dateiname>d1.pdf</Dateiname></Dokument>
      <Dokument><Kennzeichen>D2</Kennzeichen><Dateiname>d2.pdf</Dateiname></Dokument>
    </Akte>
  </Schriftgutobjekt>
</Aussonderung>
""",
        encoding="utf-8",
    )

    einheiten = lese_alle_vorgaenge(xml)
    assert len(einheiten) == 1
    assert einheiten[0]["aktenzeichen"] == "T 2:Akte3"
    assert einheiten[0]["betreff"] == "Akte3 zum Archivieren"


def test_sicher_parsen_blockiert_xxe(tmp_path: Path):
    """XXE-Entities werden nicht aufgelöst (sicher_parsen blockt sie).

    Hintergrund: lxml löst externe Entities per Default auf — ein hochgeladenes
    XDOMEA-Paket könnte sonst eine lokale Datei (z.B. /etc/passwd) auslesen.
    """
    from utils.xdomea_parser import sicher_parsen

    geheim = tmp_path / "geheim.txt"
    geheim.write_text("STREICHELZIEL", encoding="utf-8")

    bose_xml = tmp_path / "boese.xml"
    bose_xml.write_text(
        f'<?xml version="1.0"?>\n'
        f'<!DOCTYPE r [ <!ENTITY xxe SYSTEM "file://{geheim}"> ]>\n'
        f"<r>&xxe;</r>",
        encoding="utf-8",
    )

    baum = sicher_parsen(bose_xml)
    inhalt = baum.getroot().text or ""
    assert "STREICHELZIEL" not in inhalt, "XXE-Entity wurde aufgelöst — Safe-Parser greift nicht."


def test_sicher_parsen_blockiert_billion_laughs(tmp_path: Path):
    """Rekursive Entity-Expansion wird abgelehnt (Billion Laughs)."""
    from lxml import etree as _etree
    from utils.xdomea_parser import sicher_parsen

    bose_xml = tmp_path / "lol.xml"
    bose_xml.write_text(
        '<?xml version="1.0"?>\n'
        "<!DOCTYPE lolz [\n"
        '  <!ENTITY lol "lol">\n'
        '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
        "]>\n"
        "<lolz>&lol3;</lolz>",
        encoding="utf-8",
    )

    # Akzeptables Verhalten: Entweder wirft lxml eine Syntax-Exception (Loop
    # erkannt), oder die Entities werden nicht expandiert.
    try:
        baum = sicher_parsen(bose_xml)
    except _etree.XMLSyntaxError:
        return
    inhalt = baum.getroot().text or ""
    assert "lollollol" not in inhalt, (
        "Billion-Laughs-Entity wurde expandiert — Safe-Parser greift nicht."
    )


# ---------------------------------------------------------------------------
# Paket-Struktur (hierarchisch: Akte → Vorgang → Dokument)
# ---------------------------------------------------------------------------


def _schreibe_xml(tmp_path: Path, inhalt: str) -> Path:
    pfad = tmp_path / "paket.xml"
    pfad.write_text(inhalt, encoding="utf-8")
    return pfad


def test_paket_struktur_akten_mit_vorgaengen(tmp_path: Path):
    """Akten mit Vorgängen mit Dokumenten — Hierarchie bleibt erhalten."""
    from utils.xdomea_parser import lese_paket_struktur

    xml = _schreibe_xml(
        tmp_path,
        """<?xml version="1.0"?>
        <Aussonderung xmlns="urn:xoev-de:xdomea:schema:3.0.0">
          <Schriftgutobjekt>
            <Akte>
              <Kennzeichen>AZ-1</Kennzeichen>
              <Betreff>Erste Akte</Betreff>
              <Akteninhalt>
                <Vorgang>
                  <Kennzeichen>VG-1</Kennzeichen>
                  <Betreff>Vorgang A</Betreff>
                  <Dokument><Kennzeichen>D1</Kennzeichen><Dateiname>a.pdf</Dateiname></Dokument>
                  <Dokument><Kennzeichen>D2</Kennzeichen><Dateiname>b.pdf</Dateiname></Dokument>
                </Vorgang>
                <Vorgang>
                  <Kennzeichen>VG-2</Kennzeichen>
                  <Betreff>Vorgang B</Betreff>
                  <Dokument><Kennzeichen>D3</Kennzeichen><Dateiname>c.pdf</Dateiname></Dokument>
                </Vorgang>
              </Akteninhalt>
            </Akte>
            <Akte>
              <Kennzeichen>AZ-2</Kennzeichen>
              <Betreff>Zweite Akte</Betreff>
            </Akte>
          </Schriftgutobjekt>
        </Aussonderung>
        """,
    )

    s = lese_paket_struktur(xml)
    assert s["ebene"] == "akten"
    assert len(s["akten"]) == 2

    a1 = s["akten"][0]
    assert a1["aktenzeichen"] == "AZ-1"
    assert a1["betreff"] == "Erste Akte"
    assert len(a1["vorgaenge"]) == 2
    assert a1["vorgaenge"][0]["aktenzeichen"] == "VG-1"
    assert len(a1["vorgaenge"][0]["dokumente"]) == 2
    assert a1["vorgaenge"][0]["dokumente"][0]["dateiname"] == "a.pdf"
    assert len(a1["vorgaenge"][1]["dokumente"]) == 1
    # Akte ohne Inhalt → leere Vorgangsliste, kein Crash
    assert s["akten"][1]["vorgaenge"] == []


def test_paket_struktur_akte_mit_direktem_dokument(tmp_path: Path):
    """Dokumente, die direkt unter <Akte> hängen (ohne <Vorgang>), bleiben separat."""
    from utils.xdomea_parser import lese_paket_struktur

    xml = _schreibe_xml(
        tmp_path,
        """<?xml version="1.0"?>
        <Aussonderung xmlns="urn:xoev-de:xdomea:schema:3.0.0">
          <Akte>
            <Kennzeichen>AZ-X</Kennzeichen>
            <Dokument><Kennzeichen>LOSE</Kennzeichen><Dateiname>lose.pdf</Dateiname></Dokument>
            <Vorgang>
              <Kennzeichen>VG-X</Kennzeichen>
              <Dokument><Kennzeichen>IN-VG</Kennzeichen><Dateiname>in_vg.pdf</Dateiname></Dokument>
            </Vorgang>
          </Akte>
        </Aussonderung>
        """,
    )

    s = lese_paket_struktur(xml)
    assert s["ebene"] == "akten"
    a = s["akten"][0]
    # Lose Dokumente: nur das eine ohne Vorgang-Vorfahr
    assert len(a["dokumente"]) == 1
    assert a["dokumente"][0]["dateiname"] == "lose.pdf"
    # Dokumente im Vorgang: das andere
    assert len(a["vorgaenge"]) == 1
    assert len(a["vorgaenge"][0]["dokumente"]) == 1
    assert a["vorgaenge"][0]["dokumente"][0]["dateiname"] == "in_vg.pdf"


def test_paket_struktur_nur_vorgaenge_xdomea_2x(tmp_path: Path):
    """Klassisches XDOMEA-2.x ohne <Akte> — Vorgänge sind die Wurzel."""
    from utils.xdomea_parser import lese_paket_struktur

    xml = _schreibe_xml(
        tmp_path,
        """<?xml version="1.0"?>
        <Aussonderung xmlns="urn:xoev-de:xdomea:schema:2.3.0">
          <Schriftgutobjekt>
            <Vorgang>
              <Kennzeichen>VG-A</Kennzeichen>
              <Dokument><Kennzeichen>D1</Kennzeichen><Dateiname>a.pdf</Dateiname></Dokument>
            </Vorgang>
            <Vorgang>
              <Kennzeichen>VG-B</Kennzeichen>
              <Dokument><Kennzeichen>D2</Kennzeichen><Dateiname>b.pdf</Dateiname></Dokument>
            </Vorgang>
          </Schriftgutobjekt>
        </Aussonderung>
        """,
    )

    s = lese_paket_struktur(xml)
    assert s["ebene"] == "vorgaenge"
    assert len(s["vorgaenge"]) == 2
    assert s["vorgaenge"][0]["aktenzeichen"] == "VG-A"
    assert s["vorgaenge"][0]["dokumente"][0]["dateiname"] == "a.pdf"


def test_paket_struktur_leeres_xml(tmp_path: Path):
    """Leeres oder unbekanntes XML liefert 'leer', ohne Crash."""
    from utils.xdomea_parser import lese_paket_struktur

    xml = _schreibe_xml(tmp_path, '<?xml version="1.0"?><Root/>')
    s = lese_paket_struktur(xml)
    assert s["ebene"] == "leer"
