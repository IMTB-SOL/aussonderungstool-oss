# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die konsolidierte Paketstruktur-Analyse über mehrere DMS-Formate.

Deckt die Eigenheiten ab, die beim Import von Fremd-DMS-Paketen auftreten:
  - 3-stufige Akten mit Teilakten (z.B. regisafe)
  - parallele Renditionen desselben Inhalts (z.B. enaio Originalformat + PDFA)
  - leere/doppelte Kennzeichen, die eindeutige Bewertungs-IDs erfordern
"""

from pathlib import Path

from utils.xdomea_parser import (
    analysiere_paket_struktur,
    dedupe_rendition_xmls,
    finde_xdomea_xmls,
    lese_bewertungseinheiten,
)

NS = 'xmlns:xdomea="urn:xoev-de:xdomea:schema:3.0.0"'


def _schreibe(pfad: Path, inhalt: str) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    return pfad


def _dokument(betreff: str, datei: str) -> str:
    return f"""
      <xdomea:Dokument>
        <xdomea:Identifikation><xdomea:ID>{betreff}-id</xdomea:ID></xdomea:Identifikation>
        <xdomea:AllgemeineMetadaten><xdomea:Betreff>{betreff}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
        <xdomea:Version><xdomea:Format><xdomea:Primaerdokument>
          <xdomea:Dateiname>{datei}</xdomea:Dateiname>
        </xdomea:Primaerdokument></xdomea:Format></xdomea:Version>
      </xdomea:Dokument>"""


# --- regisafe-artig: Akte > Teilakte > Dokument (3-stufig, kein Vorgang) ----


def _regisafe_xml(praefix: str = "") -> str:
    teilakten = "".join(
        f"""
        <xdomea:Teilakte>
          <xdomea:AllgemeineMetadaten><xdomea:Betreff>{praefix}Teilakte {i}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
          <xdomea:Akteninhalt>{_dokument(f"{praefix}Dok {i}", f"{praefix}d{i}.pdf")}</xdomea:Akteninhalt>
        </xdomea:Teilakte>"""
        for i in (1, 2, 3)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:AllgemeineMetadaten><xdomea:Kennzeichen>{praefix}AKTE-3</xdomea:Kennzeichen>
        <xdomea:Betreff>{praefix}3-stufige Akte</xdomea:Betreff></xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>{teilakten}</xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>"""


def test_teilakten_struktur_3_stufig(tmp_path):
    xml = _schreibe(tmp_path / "regi" / "a.xml", _regisafe_xml())
    s = analysiere_paket_struktur([xml])
    assert s["ebene"] == "teilakten"
    assert s["statistik"]["akten"] == 1
    assert s["statistik"]["teilakten"] == 3
    assert s["statistik"]["vorgaenge"] == 0
    assert s["statistik"]["dokumente"] == 3
    akte = s["akten"][0]
    assert len(akte["teilakten"]) == 3
    # Dokumente hängen an den Teilakten, nicht lose an der Akte
    assert akte["dokumente"] == []
    assert all(len(t["dokumente"]) == 1 for t in akte["teilakten"])


# --- enaio-artig: parallele Renditionen (Originalformat + PDFA) -------------


def _enaio_akte_xml(betreff: str, mit_vorgaengen: bool) -> str:
    if mit_vorgaengen:
        inhalt = "".join(
            f"""
        <xdomea:Vorgang>
          <xdomea:AllgemeineMetadaten><xdomea:Betreff>{vb}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
          {_dokument(f"{vb}-dok", f"{vb}.pdf")}
        </xdomea:Vorgang>"""
            for vb in ("10 Anforderung", "20 Anfrage")
        )
    else:
        inhalt = _dokument("loses Dok", "los.pdf")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:AllgemeineMetadaten><xdomea:Betreff>{betreff}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>{inhalt}</xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>"""


def _baue_enaio(tmp_path: Path) -> Path:
    """2 Akten, jeweils als Originalformat- und PDFA-Rendition (4 XMLs)."""
    root = tmp_path / "enaio"
    for rendition in ("Originalformat", "PDFA"):
        _schreibe(root / rendition / "Akte A" / "x.xml", _enaio_akte_xml("Akte A", False))
        _schreibe(root / rendition / "Akte B" / "x.xml", _enaio_akte_xml("Akte B", True))
    return root


def test_renditions_dedup(tmp_path):
    root = _baue_enaio(tmp_path)
    xmls = finde_xdomea_xmls(root)
    assert len(xmls) == 4
    kanonisch, entfernt = dedupe_rendition_xmls(xmls)
    assert entfernt == 2
    assert len(kanonisch) == 2


def test_enaio_struktur_konsolidiert(tmp_path):
    root = _baue_enaio(tmp_path)
    s = analysiere_paket_struktur(finde_xdomea_xmls(root))
    assert s["renditions_entfernt"] == 2
    assert s["statistik"]["akten"] == 2
    assert s["statistik"]["vorgaenge"] == 2  # nur Akte B hat Vorgänge
    assert s["statistik"]["dokumente"] == 3  # 1 lose (Akte A) + 2 (Akte B)
    assert s["ebene"] == "akten_mit_vorgaengen"


def test_bewertungseinheiten_eindeutige_ids(tmp_path):
    root = _baue_enaio(tmp_path)
    einheiten = lese_bewertungseinheiten(finde_xdomea_xmls(root))
    # Akte A (1 Einheit, da kein Vorgang) + Akte B (2 Vorgänge) = 3
    assert len(einheiten) == 3
    ids = [e["vorgang_id"] for e in einheiten]
    assert len(set(ids)) == len(ids), "vorgang_ids müssen eindeutig sein"
    assert all(i for i in ids), "keine leeren vorgang_ids"


def test_bewertungseinheiten_suffix_bei_leeren_kennzeichen(tmp_path):
    # Zwei Vorgänge mit komplett leeren Metadaten → müssen trotzdem eindeutige IDs erhalten
    xml = _schreibe(
        tmp_path / "leer" / "a.xml",
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Vorgang><xdomea:AllgemeineMetadaten/></xdomea:Vorgang>
    <xdomea:Vorgang><xdomea:AllgemeineMetadaten/></xdomea:Vorgang>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>""",
    )
    einheiten = lese_bewertungseinheiten([xml])
    assert len(einheiten) == 2
    ids = [e["vorgang_id"] for e in einheiten]
    assert len(set(ids)) == 2
