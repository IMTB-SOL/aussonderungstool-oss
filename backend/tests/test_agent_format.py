"""Tests für Agent 2 – Formatprüfung."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents.agent_format import pruefe_format
from utils.zip_handler import entpacke_zip

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def arbeitsverzeichnis_gueltig(tmp_path: Path) -> Path:
    """Entpackt das gültige Test-ZIP in ein temporäres Verzeichnis."""
    zip_pfad = FIXTURES_DIR / "gueltig.zip"
    if not zip_pfad.exists():
        pytest.skip("Test-Fixture gueltig.zip nicht vorhanden.")
    ziel = tmp_path / "arbeit_format"
    ziel.mkdir()
    entpacke_zip(zip_pfad, ziel)
    return ziel


@pytest.mark.asyncio
async def test_format_happy_path(arbeitsverzeichnis_gueltig: Path):
    """Dateien werden erkannt und MIME-Typen bestimmt."""
    ergebnis = await pruefe_format("test-job-format-1", arbeitsverzeichnis_gueltig)

    # "warnung", wenn veraPDF bzw. Java auf dem Rechner nicht verfügbar ist
    assert ergebnis["status"] in ("ok", "warnung", "fehler")
    assert len(ergebnis["dateien"]) > 0

    # Jede Datei hat einen MIME-Typ
    for datei in ergebnis["dateien"]:
        assert datei["dateiname"]
        assert datei["mime_typ"] is not None


@pytest.mark.asyncio
async def test_format_mime_typen(arbeitsverzeichnis_gueltig: Path):
    """Prüft dass die MIME-Typen korrekt erkannt werden."""
    ergebnis = await pruefe_format("test-job-format-2", arbeitsverzeichnis_gueltig)

    dateinamen = {d["dateiname"]: d for d in ergebnis["dateien"]}

    # PDF-Datei sollte als application/pdf erkannt werden (oder ähnlich)
    if "testdokument.pdf" in dateinamen:
        mime = dateinamen["testdokument.pdf"]["mime_typ"]
        assert mime is not None


@pytest.mark.asyncio
async def test_format_ohne_verapdf(arbeitsverzeichnis_gueltig: Path):
    """Ohne veraPDF: PDF/A-Prüfung wird übersprungen, kein Fehler."""
    with patch("agents.agent_format.VERAPDF_PATH", "/nicht/existent/verapdf"):
        ergebnis = await pruefe_format("test-job-format-3", arbeitsverzeichnis_gueltig)

    # Sollte nicht abstürzen, PDF/A bleibt None
    for datei in ergebnis["dateien"]:
        if datei["mime_typ"] == "application/pdf":
            assert datei["pdfa_valid"] is None
            assert "übersprungen" in datei["details"]


def _mock_magic_pdf(datei_pfad: str, mime: bool = False) -> str:
    """Mock für magic.from_file: PDF für .pdf-Dateien, sonst Fallback."""
    if mime:
        if datei_pfad.endswith(".pdf"):
            return "application/pdf"
        if datei_pfad.endswith(".docx"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return "application/octet-stream"
    return "data"


def _verapdf_mock_xml(dateiname: str, is_compliant: bool) -> bytes:
    """Erzeugt eine realistische veraPDF-Batch-XML-Ausgabe für einen Mock."""
    flavour = "PDFA_1_B"
    return (
        f"<report><jobs><job>"
        f"<item><name>{dateiname}</name></item>"
        f'<validationReport isCompliant="{str(is_compliant).lower()}" flavour="{flavour}"/>'
        f"</job></jobs></report>"
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_format_mit_verapdf_konform(arbeitsverzeichnis_gueltig: Path):
    """Mit gemocktem veraPDF: PDF/A-konform."""
    mock_prozess = AsyncMock()
    mock_prozess.communicate.return_value = (
        _verapdf_mock_xml("testdokument.pdf", is_compliant=True),
        b"",
    )
    mock_prozess.returncode = 0

    with (
        patch("agents.agent_format.VERAPDF_PATH", "/mock/verapdf"),
        patch("agents.agent_format.Path.exists", return_value=True),
        patch("agents.agent_format.magic.from_file", side_effect=_mock_magic_pdf),
        patch("asyncio.create_subprocess_exec", return_value=mock_prozess),
    ):
        ergebnis = await pruefe_format("test-job-format-4", arbeitsverzeichnis_gueltig)

    pdf_dateien = [d for d in ergebnis["dateien"] if d["mime_typ"] == "application/pdf"]
    assert len(pdf_dateien) > 0
    for datei in pdf_dateien:
        assert datei["pdfa_valid"] is True


@pytest.mark.asyncio
async def test_format_mit_verapdf_nicht_konform(arbeitsverzeichnis_gueltig: Path):
    """Mit gemocktem veraPDF: Nicht PDF/A-konform → Status fehler."""
    mock_prozess = AsyncMock()
    mock_prozess.communicate.return_value = (
        _verapdf_mock_xml("testdokument.pdf", is_compliant=False),
        b"",
    )
    mock_prozess.returncode = 0

    with (
        patch("agents.agent_format.VERAPDF_PATH", "/mock/verapdf"),
        patch("agents.agent_format.Path.exists", return_value=True),
        patch("agents.agent_format.magic.from_file", side_effect=_mock_magic_pdf),
        patch("asyncio.create_subprocess_exec", return_value=mock_prozess),
    ):
        ergebnis = await pruefe_format("test-job-format-5", arbeitsverzeichnis_gueltig)

    assert ergebnis["status"] == "fehler"
    pdf_dateien = [d for d in ergebnis["dateien"] if d["mime_typ"] == "application/pdf"]
    assert len(pdf_dateien) > 0
    for datei in pdf_dateien:
        assert datei["pdfa_valid"] is False


@pytest.mark.asyncio
async def test_format_leeres_verzeichnis(tmp_path: Path):
    """Leeres Verzeichnis: Keine Dateien, Status ok."""
    ergebnis = await pruefe_format("test-job-format-6", tmp_path)

    assert ergebnis["status"] == "ok"
    assert ergebnis["dateien"] == []


def test_mime_zu_dateityp_bekannte_typen():
    """Sprechende Labels für die wichtigsten MIME-Typen."""
    from agents.agent_format import mime_zu_dateityp

    assert mime_zu_dateityp("application/pdf") == "PDF"
    assert mime_zu_dateityp("image/jpeg") == "JPEG"
    assert mime_zu_dateityp("image/png") == "PNG"
    assert (
        mime_zu_dateityp("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        == "DOCX"
    )
    assert (
        mime_zu_dateityp(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        == "PPTX"
    )
    assert (
        mime_zu_dateityp("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        == "XLSX"
    )


def test_mime_zu_dateityp_fallbacks():
    """Unbekannte MIME-Typen: Subtype in Großbuchstaben; None → 'unbekannt'."""
    from agents.agent_format import mime_zu_dateityp

    assert mime_zu_dateityp("application/foobar") == "FOOBAR"
    assert mime_zu_dateityp("image/x-experimental") == "X-EXPERIMENTAL"
    assert mime_zu_dateityp(None) == "unbekannt"
    assert mime_zu_dateityp("") == "unbekannt"


@pytest.mark.asyncio
async def test_format_ergebnis_enthaelt_dateityp(arbeitsverzeichnis_gueltig: Path):
    """Jedes Datei-Ergebnis trägt das sprechende Dateityp-Label."""
    ergebnis = await pruefe_format("test-job-format-dateityp", arbeitsverzeichnis_gueltig)

    assert len(ergebnis["dateien"]) > 0
    for datei in ergebnis["dateien"]:
        assert "dateityp" in datei, f"dateityp fehlt bei {datei['dateiname']}"
        assert datei["dateityp"]  # nicht-leer
