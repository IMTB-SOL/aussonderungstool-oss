"""Tests für Agent 3 – Inhaltsvergleich (Seitenanzahl + Textähnlichkeit)."""

from pathlib import Path

import pytest
from agents.agent_inhalt import (
    _finde_dateipaare,
    _normalisiere_text,
    _vergleiche_paar,
    vergleiche_inhalt,
)


@pytest.fixture
def arbeitsverzeichnis_ohne_paare(tmp_path: Path) -> Path:
    (tmp_path / "nur_ein.pdf").write_bytes(b"%PDF-1.4 Testinhalt")
    (tmp_path / "anderer.docx").write_bytes(b"PK fake docx")
    return tmp_path


def test_finde_dateipaare_keine(arbeitsverzeichnis_ohne_paare: Path):
    paare = _finde_dateipaare(arbeitsverzeichnis_ohne_paare)
    assert len(paare) == 0


def test_normalisiere_text():
    assert _normalisiere_text("  Hallo   Welt  ") == "hallo welt"
    assert _normalisiere_text("Test\u00adwort") == "testwort"
    assert _normalisiere_text("") == ""


def test_vergleiche_paar_identisch():
    text = "Dies ist ein Testdokument mit wichtigem Inhalt."
    ergebnis = _vergleiche_paar("test", 3, 3, text, text)
    assert ergebnis["identisch"] is True
    assert ergebnis["aehnlichkeit"] == 100


def test_vergleiche_paar_seitenanzahl_abweichung():
    text = "Gleicher Text in beiden Dokumenten."
    ergebnis = _vergleiche_paar("test", 5, 3, text, text)
    assert ergebnis["identisch"] is False
    assert any("Seitenanzahl" in a for a in ergebnis["abweichungen"])


def test_vergleiche_paar_text_abweichung():
    docx = "Dies ist der originale Text mit wichtigen Informationen."
    pdf = "Komplett anderer Inhalt ohne Bezug."
    ergebnis = _vergleiche_paar("test", None, 1, docx, pdf)
    assert ergebnis["identisch"] is False
    assert ergebnis["aehnlichkeit"] < 90
    assert any("Textähnlichkeit" in a for a in ergebnis["abweichungen"])


def test_vergleiche_paar_hohe_aehnlichkeit():
    docx = "Dies ist ein langer Text mit vielen Wörtern und Sätzen die den Inhalt beschreiben."
    pdf = "Dies ist ein langer Text mit vielen Wörtern und Sätzen die den Inhalt beschreiben."
    ergebnis = _vergleiche_paar("test", None, 1, docx, pdf)
    assert ergebnis["identisch"] is True
    assert ergebnis["aehnlichkeit"] >= 90


@pytest.mark.asyncio
async def test_inhalt_keine_paare(arbeitsverzeichnis_ohne_paare: Path):
    ergebnis = await vergleiche_inhalt("test-job-1", arbeitsverzeichnis_ohne_paare)
    assert ergebnis["status"] == "ok"
    assert ergebnis["paare"] == []
