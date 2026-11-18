# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für DMS-Paketvarianten aus der Praxis.

Jeder Test bildet eine Eigenheit ab, die bei in der Praxis beobachteten
Paketvarianten auftritt und beim Einlesen bisher zu Datenverlust geführt hat:

  - nscale eGOV: Lieferverzeichnis mit <akte>-Einträgen,
    innere Container tragen die Endung ".xdomea" statt ".zip"
  - elKA: Sammel-ZIP, das die eigentlichen Pakete als
    verschachtelte ZIPs enthält
  - Winyard: XDOMEA 2.0.1, Dokument hängt direkt unter
    <Schriftgutobjekt> ohne Akte/Vorgang
  - regisafe: mehrstufig geschachtelte Teilakten
"""

import zipfile
from pathlib import Path

from utils.xdomea_parser import (
    analysiere_paket_struktur,
    finde_xdomea_xmls,
    lese_bewertungseinheiten,
    lese_xdomea_version,
)
from utils.zip_handler import entpacke_lv_bundle_falls_vorhanden

NS30 = 'xmlns:xdomea="urn:xoev-de:xdomea:schema:3.0.0"'


def _schreibe(pfad: Path, inhalt: str) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    return pfad


# --------------------------------------------------------------------------
# nscale eGOV: Lieferverzeichnis + ".xdomea"-Container
# --------------------------------------------------------------------------

LIEFERVERZEICHNIS_MIT_AKTEN = """<?xml version="1.0" encoding="UTF-8"?>
<bas:domeaAussonderungListen xmlns:bas="http://dips.bundesarchiv.de/schema">
  <aussonderung>
    <listentyp>Lieferverzeichnis</listentyp>
    <abgebendeStelle>Musterstadt</abgebendeStelle>
  </aussonderung>
  <akte>
    <id>00000000-0000-4000-8000-000000000001</id>
    <aktenzeichen>900.01/000002</aktenzeichen>
    <aktenbetreff>Aussonderung - Akte 12</aktenbetreff>
    <federfuehrung>Sachbearbeiter/in 99.11</federfuehrung>
    <bewertungsentscheidung>A</bewertungsentscheidung>
    <dateiname>innen_Aussonderung.Aussonderung.0503.xdomea</dateiname>
  </akte>
  <akte>
    <id>00000000-0000-4000-8000-000000000002</id>
    <aktenzeichen>900.01/000001</aktenzeichen>
    <aktenbetreff>Aussonderung - Akte 11</aktenbetreff>
    <bewertungsentscheidung>A</bewertungsentscheidung>
    <dateiname>innen2_Aussonderung.Aussonderung.0503.xdomea</dateiname>
  </akte>
</bas:domeaAussonderungListen>"""


def _inneres_akten_xml(kennzeichen: str, betreff: str, datei: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS30}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Kennzeichen>{kennzeichen}</xdomea:Kennzeichen>
        <xdomea:Betreff>{betreff}</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>
        <xdomea:Dokument>
          <xdomea:Identifikation><xdomea:ID>{kennzeichen}-d1</xdomea:ID></xdomea:Identifikation>
          <xdomea:AllgemeineMetadaten><xdomea:Betreff>Dok zu {betreff}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
          <xdomea:Version><xdomea:Format><xdomea:Primaerdokument>
            <xdomea:Dateiname>{datei}</xdomea:Dateiname>
          </xdomea:Primaerdokument></xdomea:Format></xdomea:Version>
        </xdomea:Dokument>
      </xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>"""


def _baue_nscale_paket(wurzel: Path) -> Path:
    """Baut ein nscale-Bundle: LV-XML + zwei ZIPs mit Endung '.xdomea'."""
    wurzel.mkdir(parents=True, exist_ok=True)
    _schreibe(wurzel / "Lieferverzeichnis.xml", LIEFERVERZEICHNIS_MIT_AKTEN)
    for name, kennz, betreff in (
        ("innen_Aussonderung.Aussonderung.0503.xdomea", "900.01/000002", "Aussonderung - Akte 12"),
        ("innen2_Aussonderung.Aussonderung.0503.xdomea", "900.01/000001", "Aussonderung - Akte 11"),
    ):
        with zipfile.ZipFile(wurzel / name, "w") as zf:
            zf.writestr(
                name.replace(".xdomea", ".xml"),
                _inneres_akten_xml(kennz, betreff, f"{kennz.replace('/', '_')}.pdf"),
            )
            zf.writestr(f"{kennz.replace('/', '_')}.pdf", b"%PDF-1.4 dummy")
    return wurzel


def test_xdomea_endung_wird_als_container_entpackt(tmp_path):
    """nscale liefert innere Pakete mit Endung '.xdomea' — trotzdem ZIPs."""
    wurzel = _baue_nscale_paket(tmp_path / "nscale")

    anzahl = entpacke_lv_bundle_falls_vorhanden(wurzel)

    assert anzahl == 2, "Beide .xdomea-Container müssen entpackt werden"


def test_nscale_paket_liefert_beide_akten(tmp_path):
    """Nach dem Entpacken müssen die Akten aus den .xdomea-Containern zählen."""
    wurzel = _baue_nscale_paket(tmp_path / "nscale")
    entpacke_lv_bundle_falls_vorhanden(wurzel)

    struktur = analysiere_paket_struktur(finde_xdomea_xmls(wurzel))

    assert struktur["statistik"]["akten"] == 2
    assert struktur["statistik"]["dokumente"] == 2


def test_entpacktes_lieferverzeichnis_erzeugt_keine_dubletten(tmp_path):
    """Sind die Container entpackt, ist das Verzeichnis nur noch Inhaltsverzeichnis.

    Seine Einträge dürfen dann keine zusätzlichen — dokumentlosen — Einheiten
    erzeugen, sonst bewertet der Archivar jede Akte doppelt.
    """
    wurzel = _baue_nscale_paket(tmp_path / "nscale")
    entpacke_lv_bundle_falls_vorhanden(wurzel)

    einheiten = lese_bewertungseinheiten(finde_xdomea_xmls(wurzel))

    assert len(einheiten) == 2, "Erwartet die 2 echten Akten, nicht zusätzlich die LV-Einträge"
    assert all(e["dokumente"] for e in einheiten), "Keine Einheit darf dokumentlos sein"


def test_lieferverzeichnis_mit_akten_liefert_einheiten(tmp_path):
    """Das Bundesarchiv-Lieferverzeichnis kann <akte> statt <vorgang> führen."""
    xml = _schreibe(tmp_path / "Lieferverzeichnis.xml", LIEFERVERZEICHNIS_MIT_AKTEN)

    einheiten = lese_bewertungseinheiten([xml])

    assert len(einheiten) == 2
    betreffs = {e["betreff"] for e in einheiten}
    assert betreffs == {"Aussonderung - Akte 12", "Aussonderung - Akte 11"}
    assert einheiten[0]["aktenzeichen"] == "900.01/000002"


# --------------------------------------------------------------------------
# elKA: Sammel-ZIP mit verschachtelten Paket-ZIPs
# --------------------------------------------------------------------------


def test_verschachtelte_paket_zips_werden_entpackt(tmp_path):
    """Ein Sammel-ZIP ohne Lieferverzeichnis enthält die Pakete als ZIPs."""
    wurzel = tmp_path / "elka"
    (wurzel / "XDOMEA 3.0").mkdir(parents=True)
    inneres = wurzel / "XDOMEA 3.0" / "paket_Aussonderung.Aussonderung.0503.zip"
    with zipfile.ZipFile(inneres, "w") as zf:
        zf.writestr(
            "paket_Aussonderung.Aussonderung.0503.xml",
            _inneres_akten_xml("90.00.00/01-1", "Mustermann, Erika", "mustermann.pdf"),
        )
        zf.writestr("mustermann.pdf", b"%PDF-1.4 dummy")

    entpacke_lv_bundle_falls_vorhanden(wurzel)
    struktur = analysiere_paket_struktur(finde_xdomea_xmls(wurzel))

    assert struktur["statistik"]["akten"] == 1
    assert struktur["statistik"]["dokumente"] == 1


# --------------------------------------------------------------------------
# Winyard: XDOMEA 2.0.1, Dokument direkt unter Schriftgutobjekt
# --------------------------------------------------------------------------

XDOMEA_201_ABGABE = """<?xml version="1.0"?>
<Abgabe.Abgabe.0401 xmlns="http://www.xdomea.de/V2.0.1">
  <Kopf>
    <ProzessID>00000000-0000-4000-8000-000000000003</ProzessID>
    <Erstellungszeitpunkt>2024-05-06T14:16:43</Erstellungszeitpunkt>
  </Kopf>
  <Schriftgutobjekt>
    <Dokument>
      <Identifikation><ID>0a1b2c3d-0000-4000-8000-000000000004</ID></Identifikation>
      <AllgemeineMetadaten><Betreff>Beispieldokument SEPA</Betreff></AllgemeineMetadaten>
      <Version><Nummer>1</Nummer><Format><Primaerdokument>
        <Dateiname>0a1b2c3d_Beispieldokument.pdf</Dateiname>
      </Primaerdokument></Format></Version>
    </Dokument>
  </Schriftgutobjekt>
</Abgabe.Abgabe.0401>"""


def test_xdomea_2_0_1_version_wird_erkannt(tmp_path):
    """Ältere Exporte nutzen den Namespace http://www.xdomea.de/V2.0.1."""
    xml = _schreibe(tmp_path / "XDOM_abgabe.xml", XDOMEA_201_ABGABE)

    assert lese_xdomea_version(xml) == "2.0.1"


def test_loses_dokument_unter_schriftgutobjekt_wird_dargestellt(tmp_path):
    """Ohne Akte/Vorgang darf das Dokument nicht unsichtbar werden."""
    xml = _schreibe(tmp_path / "XDOM_abgabe.xml", XDOMEA_201_ABGABE)

    struktur = analysiere_paket_struktur([xml])

    assert struktur["ebene"] != "leer"
    assert struktur["statistik"]["dokumente"] == 1


def test_loses_dokument_wird_zur_bewertungseinheit(tmp_path):
    """Das Dokument muss bewertbar sein, sonst läuft die Pipeline leer."""
    xml = _schreibe(tmp_path / "XDOM_abgabe.xml", XDOMEA_201_ABGABE)

    einheiten = lese_bewertungseinheiten([xml])

    assert len(einheiten) == 1
    assert einheiten[0]["betreff"] == "Beispieldokument SEPA"


# --------------------------------------------------------------------------
# regisafe: mehrstufig geschachtelte Teilakten
# --------------------------------------------------------------------------


def _dok(betreff: str, datei: str) -> str:
    return f"""
      <xdomea:Dokument>
        <xdomea:Identifikation><xdomea:ID>{betreff}-id</xdomea:ID></xdomea:Identifikation>
        <xdomea:AllgemeineMetadaten><xdomea:Betreff>{betreff}</xdomea:Betreff></xdomea:AllgemeineMetadaten>
        <xdomea:Version><xdomea:Format><xdomea:Primaerdokument>
          <xdomea:Dateiname>{datei}</xdomea:Dateiname>
        </xdomea:Primaerdokument></xdomea:Format></xdomea:Version>
      </xdomea:Dokument>"""


# Akte
#  └─ Teilakte "oben"           (1 eigenes Dokument)
#      └─ Teilakte "mitte"      (1 eigenes Dokument)
#          └─ Teilakte "unten"  (1 eigenes Dokument)
# Gesamt: 3 Teilakten, 3 Dokumente — jedes genau einmal.
GESCHACHTELTE_TEILAKTEN = f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS30}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:AllgemeineMetadaten><xdomea:Kennzeichen>AKTE-1</xdomea:Kennzeichen>
        <xdomea:Betreff>Akte mit tiefen Teilakten</xdomea:Betreff></xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>
        <xdomea:Teilakte>
          <xdomea:AllgemeineMetadaten><xdomea:Betreff>oben</xdomea:Betreff></xdomea:AllgemeineMetadaten>
          <xdomea:Akteninhalt>
            {_dok("Dok oben", "oben.pdf")}
            <xdomea:Teilakte>
              <xdomea:AllgemeineMetadaten><xdomea:Betreff>mitte</xdomea:Betreff></xdomea:AllgemeineMetadaten>
              <xdomea:Akteninhalt>
                {_dok("Dok mitte", "mitte.pdf")}
                <xdomea:Teilakte>
                  <xdomea:AllgemeineMetadaten><xdomea:Betreff>unten</xdomea:Betreff></xdomea:AllgemeineMetadaten>
                  <xdomea:Akteninhalt>{_dok("Dok unten", "unten.pdf")}</xdomea:Akteninhalt>
                </xdomea:Teilakte>
              </xdomea:Akteninhalt>
            </xdomea:Teilakte>
          </xdomea:Akteninhalt>
        </xdomea:Teilakte>
      </xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>"""


def test_geschachtelte_teilakten_zaehlen_dokumente_nur_einmal(tmp_path):
    """Auf Teilakten-Ebene darf kein Dokument mehrfach bewertet werden."""
    xml = _schreibe(tmp_path / "regi.xml", GESCHACHTELTE_TEILAKTEN)

    einheiten = lese_bewertungseinheiten([xml], "teilakte")

    assert len(einheiten) == 3
    dokumente_gesamt = sum(len(e["dokumente"]) for e in einheiten)
    assert dokumente_gesamt == 3, (
        f"Erwartet 3 Dokumente über alle Teilakten, gezählt {dokumente_gesamt} "
        "— geschachtelte Teilakten wurden mehrfach gezählt"
    )


def test_geschachtelte_teilakten_behalten_eigene_dokumente(tmp_path):
    """Jede Teilakte führt genau ihr eigenes Dokument."""
    xml = _schreibe(tmp_path / "regi.xml", GESCHACHTELTE_TEILAKTEN)

    einheiten = lese_bewertungseinheiten([xml], "teilakte")

    nach_betreff = {e["betreff"]: e for e in einheiten}
    assert set(nach_betreff) == {"oben", "mitte", "unten"}
    assert [d["betreff"] for d in nach_betreff["unten"]["dokumente"]] == ["Dok unten"]
    assert [d["betreff"] for d in nach_betreff["mitte"]["dokumente"]] == ["Dok mitte"]
