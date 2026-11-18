"""Tests für Agent 5 – Erschließung (pro Einheit auf gewählter Ebene)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from agents.agent_erschliessung import erschliesse_vorgang

MINIMAL_XDOMEA = """<?xml version="1.0" encoding="UTF-8"?>
<xdomea:Aussonderung.Aussonderung.0503 xmlns:xdomea="urn:xoev-de:xdomea:schema:3.0.0">
  <xdomea:Schriftgutobjekt>
    <xdomea:Vorgang>
      <xdomea:AllgemeineMetadaten>
        <xdomea:Kennzeichen>AZ-2025-001</xdomea:Kennzeichen>
        <xdomea:Betreff>Umbau Stadtbibliothek</xdomea:Betreff>
      </xdomea:AllgemeineMetadaten>
      <xdomea:Dokument>
        <xdomea:Version><xdomea:Format><xdomea:Primaerdokument>
          <xdomea:Dateiname>umbau_bibliothek.docx</xdomea:Dateiname>
        </xdomea:Primaerdokument></xdomea:Format></xdomea:Version>
      </xdomea:Dokument>
    </xdomea:Vorgang>
  </xdomea:Schriftgutobjekt>
</xdomea:Aussonderung.Aussonderung.0503>
"""


@pytest.fixture
def arbeitsverzeichnis_mit_docs(tmp_path: Path) -> Path:
    """Verzeichnis mit einem DOCX-Dokument + minimaler XDOMEA-XML (eine Einheit)."""
    from docx import Document

    arbeit = tmp_path / "arbeit_erschliessung"
    arbeit.mkdir()
    doc = Document()
    doc.add_paragraph("Aktenzeichen: AZ-2025-001")
    doc.add_paragraph("Betrifft: Umbau der Stadtbibliothek Musterstadt.")
    doc.add_paragraph("Zeitraum: 01.03.2025 – 30.06.2025")
    doc.save(str(arbeit / "umbau_bibliothek.docx"))
    (arbeit / "Aussonderung.Aussonderung.0503.xml").write_text(MINIMAL_XDOMEA, encoding="utf-8")
    return arbeit


@pytest.mark.asyncio
async def test_erschliessung_happy_path(arbeitsverzeichnis_mit_docs: Path):
    """Sprachmodell liefert Erschließung — eine Einheit, Ergebnis enthält die Liste."""
    mock_gpt_antwort = {
        "titel": "Umbau Stadtbibliothek Musterstadt",
        "inhaltsangabe": "Planung und Durchführung des Umbaus der Stadtbibliothek.",
        "schlagworte": ["Bibliothek", "Umbau", "Musterstadt", "Kultur", "Infrastruktur"],
        "personen": ["Max Mustermann"],
        "zeitraum": "2025",
        "aktenzeichen": "AZ-2025-001",
        "bezuege": "Stadtratsbeschluss 2024/12",
    }

    with patch(
        "utils.openai_client.llm_anfrage", new_callable=AsyncMock, return_value=mock_gpt_antwort
    ):
        ergebnis = await erschliesse_vorgang("test-job-ersch-1", arbeitsverzeichnis_mit_docs)

    assert ergebnis["status"] == "ok"
    einheiten = ergebnis["einheiten"]
    assert len(einheiten) == 1
    assert einheiten[0]["titel"] == "Umbau Stadtbibliothek Musterstadt"
    assert einheiten[0]["betreff"] == "Umbau Stadtbibliothek"
    assert len(einheiten[0]["schlagworte"]) >= 5
    # Abwärtskompatibilität: Felder der ersten Einheit auch flach vorhanden.
    assert ergebnis["titel"] == "Umbau Stadtbibliothek Musterstadt"


@pytest.mark.asyncio
async def test_erschliessung_keine_einheiten(tmp_path: Path):
    """Ohne XDOMEA-Struktur: keine Einheiten → Status fehler."""
    leer = tmp_path / "arbeit_leer"
    leer.mkdir()
    ergebnis = await erschliesse_vorgang("test-job-ersch-2", leer)

    assert ergebnis["status"] == "fehler"
    assert ergebnis["einheiten"] == []


@pytest.mark.asyncio
async def test_erschliessung_llm_fehler_degradiert(arbeitsverzeichnis_mit_docs: Path):
    """Sprachmodell-Fehler degradiert die Einheit (Hinweistext), bricht aber nicht ab."""
    with patch(
        "utils.openai_client.llm_anfrage",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Timeout"),
    ):
        ergebnis = await erschliesse_vorgang("test-job-ersch-3", arbeitsverzeichnis_mit_docs)

    assert ergebnis["status"] == "ok"
    assert len(ergebnis["einheiten"]) == 1
    assert "nicht verf" in ergebnis["einheiten"][0]["inhaltsangabe"].lower()
