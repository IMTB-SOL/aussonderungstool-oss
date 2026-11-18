# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die XML-gestützte Paarbildung des Inhaltsvergleichs.

Namensheuristik allein reicht nicht: Der Vogtlandkreis vergibt Original und
PDF-Rendition *unterschiedliche* UUIDs im Dateinamen — zusammen gehören sie
nur, weil sie im selben <Dokument> stehen. Umgekehrt bündelt regisafe unter
einem <Dokument> mehrere fachlich verschiedene Anlagen; dort darf gerade
kein Paar entstehen.
"""

from pathlib import Path

from agents.agent_inhalt import _finde_dateipaare

NS = 'xmlns:xdomea="urn:xoev-de:xdomea:schema:2.3.0"'


def _dokument(betreff: str, dateien: list[str]) -> str:
    formate = "".join(
        f"""
        <xdomea:Format><xdomea:Primaerdokument>
          <xdomea:Dateiname>{d}</xdomea:Dateiname>
        </xdomea:Primaerdokument></xdomea:Format>"""
        for d in dateien
    )
    return f"""
    <xdomea:Dokument>
      <xdomea:Identifikation><xdomea:ID>{betreff}-id</xdomea:ID></xdomea:Identifikation>
      <xdomea:AllgemeineMetadaten><xdomea:Betreff>{betreff}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
      <xdomea:Version><xdomea:Nummer>1</xdomea:Nummer>{formate}</xdomea:Version>
    </xdomea:Dokument>"""


def _paket(tmp_path: Path, dokumente: list[str], dateien: list[str]) -> Path:
    """Legt XML + die physischen Dateien an."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "paket_Aussonderung.Aussonderung.0503.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:AllgemeineMetadaten><xdomea:Kennzeichen>AZ-1</xdomea:Kennzeichen></xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>{"".join(dokumente)}</xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>""",
        encoding="utf-8",
    )
    for d in dateien:
        (tmp_path / d).write_bytes(b"inhalt")
    return tmp_path


def test_paar_trotz_unterschiedlicher_uuids(tmp_path):
    """Der Vogtlandkreis-Fall — nur das XML verbindet die beiden Dateien."""
    arbeit = _paket(
        tmp_path / "vogtland",
        [_dokument("Schreiben", ["AAA-1111_Brief.docx", "BBB-2222_Brief.docx.pdf"])],
        ["AAA-1111_Brief.docx", "BBB-2222_Brief.docx.pdf"],
    )

    paare = _finde_dateipaare(arbeit)

    assert len(paare) == 1, paare
    paar = next(iter(paare.values()))
    assert paar["docx"].name == "AAA-1111_Brief.docx"
    assert paar["pdf"].name == "BBB-2222_Brief.docx.pdf"


def test_mehrere_anlagen_im_dokument_ergeben_kein_paar(tmp_path):
    """Der regisafe-Fall: verschiedene Anlagen, keine Renditionen."""
    arbeit = _paket(
        tmp_path / "regisafe",
        [
            _dokument(
                "Posteingang",
                ["a_Word.docx", "b_Schema.pdf", "c_Beispiel.pdf", "d_Rechnung.xlsx"],
            )
        ],
        ["a_Word.docx", "b_Schema.pdf", "c_Beispiel.pdf", "d_Rechnung.xlsx"],
    )

    paare = _finde_dateipaare(arbeit)

    assert paare == {}, f"Fremde Anlagen dürfen nicht gepaart werden: {paare}"


def test_namensheuristik_ergaenzt_was_das_xml_nicht_abdeckt(tmp_path):
    """Dateien ohne XML-Zuordnung werden weiterhin über den Namen gepaart."""
    arbeit = _paket(
        tmp_path / "gemischt",
        [_dokument("Papier", [])],  # Dokument ohne Dateien
        ["Extern_Bericht.docx", "Extern_Bericht.docx.pdf"],
    )

    paare = _finde_dateipaare(arbeit)

    assert len(paare) == 1, paare
    assert next(iter(paare.values()))["docx"].name == "Extern_Bericht.docx"


def test_keine_doppelzaehlung_wenn_beide_wege_greifen(tmp_path):
    """Ein über das XML gefundenes Paar darf nicht zusätzlich per Name entstehen."""
    arbeit = _paket(
        tmp_path / "beides",
        [_dokument("Bericht", ["X_Bericht.docx", "X_Bericht.docx.pdf"])],
        ["X_Bericht.docx", "X_Bericht.docx.pdf"],
    )

    paare = _finde_dateipaare(arbeit)

    assert len(paare) == 1, paare


def test_im_xml_referenzierte_aber_fehlende_datei_ergibt_kein_paar(tmp_path):
    """Ohne physische Datei kann nicht verglichen werden."""
    arbeit = _paket(
        tmp_path / "unvollstaendig",
        [_dokument("Schreiben", ["AAA_Brief.docx", "BBB_Brief.docx.pdf"])],
        ["AAA_Brief.docx"],  # PDF fehlt
    )

    assert _finde_dateipaare(arbeit) == {}


def test_dateien_werden_bevorzugt_aus_dem_xml_verzeichnis_geloest(tmp_path):
    """Bei gleichnamigen Dateien in mehreren Containern zählt die eigene."""
    wurzel = tmp_path / "bundle"
    eigen = _paket(
        wurzel / "paket_a",
        [_dokument("Bericht", ["Doku.docx", "Doku.docx.pdf"])],
        ["Doku.docx", "Doku.docx.pdf"],
    )
    fremd = wurzel / "paket_b"
    fremd.mkdir(parents=True)
    (fremd / "Doku.docx").write_bytes(b"andere variante")
    (fremd / "Doku.docx.pdf").write_bytes(b"andere variante")

    paare = _finde_dateipaare(wurzel)

    treffer = [p for p in paare.values() if p["docx"].parent == eigen]
    assert treffer, f"Paar sollte aus {eigen.name} stammen: {paare}"
