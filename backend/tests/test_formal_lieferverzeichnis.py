# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die formale Prüfung von DOMEA-Lieferverzeichnissen.

nscale eGOV listet im Lieferverzeichnis <akte>-Einträge
statt <vorgang>. Ohne Unterstützung dafür meldet der Formal-Agent ein
fehlerfreies Paket als fehlerhaft — der Archivar würde es zu Unrecht
zurückweisen.
"""

import hashlib
import zipfile
from pathlib import Path

import pytest
from agents.agent_formal import pruefe_formal
from utils.agent_runner import markiere_sse_beendet
from utils.zip_handler import entpacke_container_archive

NS30 = 'xmlns:xdomea="urn:xoev-de:xdomea:schema:3.0.0"'


def _inneres_xml(kennzeichen: str, datei: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 {NS30}>
  <xdomea:Schriftgutobjekt>
    <xdomea:Akte>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Kennzeichen>{kennzeichen}</xdomea:Kennzeichen>
        <xdomea:Betreff>Akte {kennzeichen}</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:Akteninhalt>
        <xdomea:Dokument>
          <xdomea:Identifikation><xdomea:ID>{kennzeichen}-d1</xdomea:ID></xdomea:Identifikation>
          <xdomea:Version><xdomea:Format><xdomea:Primaerdokument>
            <xdomea:Dateiname>{datei}</xdomea:Dateiname>
          </xdomea:Primaerdokument></xdomea:Format></xdomea:Version>
        </xdomea:Dokument>
      </xdomea:Akteninhalt>
    </xdomea:Akte>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>"""


def _baue_lv_paket(wurzel: Path) -> Path:
    """Baut ein nscale-artiges Bundle: LV mit <akte> + .xdomea-Container."""
    wurzel.mkdir(parents=True, exist_ok=True)
    container = wurzel / "akte1_Aussonderung.Aussonderung.0503.xdomea"
    with zipfile.ZipFile(container, "w") as zf:
        zf.writestr("akte1_Aussonderung.Aussonderung.0503.xml", _inneres_xml("900.01", "doc.pdf"))
        zf.writestr("doc.pdf", b"%PDF-1.4 dummy")
    hashwert = hashlib.sha256(container.read_bytes()).hexdigest().upper()

    (wurzel / "Lieferverzeichnis.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<bas:domeaAussonderungListen xmlns:bas="http://dips.bundesarchiv.de/schema">
  <aussonderung><listentyp>Lieferverzeichnis</listentyp></aussonderung>
  <akte>
    <id>00000000-0000-4000-8000-000000000001</id>
    <aktenzeichen>900.01/000002</aktenzeichen>
    <aktenbetreff>Aussonderung - Akte 12</aktenbetreff>
    <dateiname>{container.name}</dateiname>
    <hashwert><algorithmus>SHA256</algorithmus><wert>{hashwert}</wert></hashwert>
  </akte>
</bas:domeaAussonderungListen>""",
        encoding="utf-8",
    )
    return wurzel


@pytest.mark.asyncio
async def test_lieferverzeichnis_mit_akten_wird_nicht_als_leer_gemeldet(tmp_path):
    """Ein LV mit <akte>-Einträgen ist gültig — kein "ohne Einträge"-Fehlbefund."""
    arbeit = _baue_lv_paket(tmp_path / "nscale")
    entpacke_container_archive(arbeit)
    markiere_sse_beendet("test-lv")

    ergebnis = await pruefe_formal("test-lv", arbeit)

    fehler = ergebnis.get("fehler") or []
    assert not any("keine <vorgang>" in f or "ohne Einträge" in f for f in fehler), fehler


@pytest.mark.asyncio
async def test_akten_eintrag_wird_gegen_den_container_geprueft(tmp_path):
    """Der <akte>-Eintrag muss seinen Container finden und den Hash bestätigen."""
    arbeit = _baue_lv_paket(tmp_path / "nscale")
    entpacke_container_archive(arbeit)
    markiere_sse_beendet("test-lv2")

    ergebnis = await pruefe_formal("test-lv2", arbeit)

    eintraege = ergebnis.get("lv_vorgaenge") or []
    assert len(eintraege) == 1, ergebnis
    assert eintraege[0]["aktenzeichen"] == "900.01/000002"
    # Container gefunden und Hash korrekt — "fehlt"/"hash_fehler" wären Befunde
    assert eintraege[0]["status"] not in ("fehlt", "hash_fehler"), eintraege[0]


@pytest.mark.asyncio
async def test_leeres_lieferverzeichnis_bleibt_ein_fehler(tmp_path):
    """Ein Verzeichnis ganz ohne Einträge ist weiterhin ein Befund."""
    arbeit = tmp_path / "leer"
    arbeit.mkdir(parents=True)
    (arbeit / "Lieferverzeichnis.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<bas:domeaAussonderungListen xmlns:bas="http://dips.bundesarchiv.de/schema">'
        "<aussonderung><listentyp>Lieferverzeichnis</listentyp></aussonderung>"
        "</bas:domeaAussonderungListen>",
        encoding="utf-8",
    )
    markiere_sse_beendet("test-lv-leer")

    ergebnis = await pruefe_formal("test-lv-leer", arbeit)

    assert ergebnis["status"] == "fehler"
    assert any("keine" in f.lower() for f in (ergebnis.get("fehler") or []))
