"""Tests für die per-Datei-Textextraktion und ihre Orchestrierung."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.text_extraktion import extrahiere_eine_datei, sammle_dokumenttexte


def test_extrahiere_eine_datei_existiert_und_signatur():
    """extrahiere_eine_datei ist modul-global verfügbar und gibt ein 3-Tupel zurück."""
    # Dummy-Pfad — kein File-Zugriff nötig, da unbekannter Suffix => leeres Ergebnis.
    dateiname, text, meldungen = extrahiere_eine_datei(Path("/tmp/unbekannt.xyz"))
    assert dateiname == "unbekannt.xyz"
    assert isinstance(text, str)
    assert isinstance(meldungen, list)


def test_ocr_lang_aus_env_default_deu(tmp_path: Path, monkeypatch):
    """Ohne OCR_LANG-Env wird tesseract mit lang='deu' aufgerufen."""
    monkeypatch.delenv("OCR_LANG", raising=False)
    bild = tmp_path / "test.png"
    bild.write_bytes(b"")  # Inhalt egal — PIL.Image.open wird gemockt

    mock_image_to_string = MagicMock(return_value="OCR-Treffer")
    with (
        patch("utils.text_extraktion.pytesseract.image_to_string", mock_image_to_string),
        patch("utils.text_extraktion.Image.open", return_value=MagicMock()),
    ):
        extrahiere_eine_datei(bild)

    mock_image_to_string.assert_called_once()
    assert mock_image_to_string.call_args.kwargs.get("lang") == "deu"


def test_ocr_lang_aus_env_ueberschrieben(tmp_path: Path, monkeypatch):
    """OCR_LANG aus der Env wird durchgereicht."""
    monkeypatch.setenv("OCR_LANG", "fra")
    bild = tmp_path / "test.png"
    bild.write_bytes(b"")

    mock_image_to_string = MagicMock(return_value="indices")
    with (
        patch("utils.text_extraktion.pytesseract.image_to_string", mock_image_to_string),
        patch("utils.text_extraktion.Image.open", return_value=MagicMock()),
    ):
        extrahiere_eine_datei(bild)

    assert mock_image_to_string.call_args.kwargs.get("lang") == "fra"


def test_extrahiere_eine_datei_ist_serialisierbar():
    """Die Funktion ist pickle-bar — Voraussetzung für ProcessPoolExecutor."""
    import pickle

    fn = pickle.loads(pickle.dumps(extrahiere_eine_datei))
    assert callable(fn)


def test_sammle_dokumenttexte_smoke_via_fixture():
    """Smoke: sammle_dokumenttexte liefert nicht-leeren Text auf der Fixture."""
    import tempfile

    import pytest
    from utils.zip_handler import entpacke_zip

    fixture = Path(__file__).parent / "fixtures" / "gueltig.zip"
    if not fixture.exists():
        pytest.skip("gueltig.zip nicht vorhanden")

    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp)
        entpacke_zip(fixture, ziel)
        meldungen: list[dict] = []
        text = sammle_dokumenttexte(ziel, meldungen)
        assert isinstance(text, str)
        assert len(text) > 0
        # Mindestens ein Dateinamen-Header taucht auf.
        assert "--- " in text
