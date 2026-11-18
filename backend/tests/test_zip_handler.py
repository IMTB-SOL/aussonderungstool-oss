"""Tests für zip_handler — Entpacken mit Zip-Slip- und Zip-Bomb-Schutz."""

import zipfile

import pytest
import utils.zip_handler as zh
from utils.zip_handler import entpacke_zip


def _erstelle_zip(pfad, eintraege: dict[str, bytes]) -> None:
    with zipfile.ZipFile(pfad, "w") as zf:
        for name, inhalt in eintraege.items():
            zf.writestr(name, inhalt)


def test_entpacke_zip_happy_path(tmp_path):
    """Ein normales ZIP wird korrekt in das Zielverzeichnis entpackt."""
    zip_pfad = tmp_path / "ok.zip"
    _erstelle_zip(zip_pfad, {"a.txt": b"hallo", "unter/b.txt": b"welt"})
    ziel = tmp_path / "ziel"
    ziel.mkdir()

    entpacke_zip(zip_pfad, ziel)

    assert (ziel / "a.txt").read_bytes() == b"hallo"
    assert (ziel / "unter" / "b.txt").read_bytes() == b"welt"


def test_entpacke_zip_lehnt_path_traversal_ab(tmp_path):
    """Ein ZIP-Eintrag mit '..' darf nicht außerhalb des Ziels schreiben."""
    zip_pfad = tmp_path / "boese.zip"
    _erstelle_zip(zip_pfad, {"../../entwischt.txt": b"x"})
    ziel = tmp_path / "ziel"
    ziel.mkdir()

    with pytest.raises(ValueError, match="(?i)traversal|außerhalb"):
        entpacke_zip(zip_pfad, ziel)
    assert not (tmp_path / "entwischt.txt").exists()


def test_entpacke_zip_lehnt_symlink_ab(tmp_path):
    """Ein Symlink-Eintrag im ZIP wird abgelehnt."""
    zip_pfad = tmp_path / "sym.zip"
    with zipfile.ZipFile(zip_pfad, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777) << 16  # S_IFLNK
        zf.writestr(info, "/etc/passwd")
    ziel = tmp_path / "ziel"
    ziel.mkdir()

    with pytest.raises(ValueError, match="(?i)symlink"):
        entpacke_zip(zip_pfad, ziel)


def test_entpacke_zip_lehnt_zu_viele_eintraege_ab(tmp_path, monkeypatch):
    """Zu viele ZIP-Einträge werden abgelehnt (Zip-Bomb-Schutz)."""
    monkeypatch.setattr(zh, "MAX_ZIP_EINTRAEGE", 3)
    zip_pfad = tmp_path / "viele.zip"
    _erstelle_zip(zip_pfad, {f"f{i}.txt": b"x" for i in range(5)})
    ziel = tmp_path / "ziel"
    ziel.mkdir()

    with pytest.raises(ValueError, match="(?i)eintr"):
        entpacke_zip(zip_pfad, ziel)


def test_entpacke_zip_lehnt_zu_grosse_gesamtgroesse_ab(tmp_path, monkeypatch):
    """Zu große unkomprimierte Gesamtgröße wird abgelehnt (Zip-Bomb-Schutz)."""
    monkeypatch.setattr(zh, "MAX_ZIP_UNKOMPRIMIERT", 10)
    zip_pfad = tmp_path / "gross.zip"
    _erstelle_zip(zip_pfad, {"a.txt": b"x" * 100})
    ziel = tmp_path / "ziel"
    ziel.mkdir()

    with pytest.raises(ValueError, match="(?i)gr[oö]ß|gross"):
        entpacke_zip(zip_pfad, ziel)
