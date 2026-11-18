# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die Dateisuche in Paketen mit eingebetteten Containern.

Bei nscale eGOV und elKA liegen die Schriftstücke nicht
direkt im hochgeladenen ZIP, sondern in einem eingebetteten Container. Die
Vorschau muss sie trotzdem finden — sonst zeigt die Strukturansicht Dokumente,
die sich nicht öffnen lassen.
"""

import zipfile
from pathlib import Path

import pytest
from utils.zip_handler import finde_datei_in_paket

PDF_INHALT = b"%PDF-1.4 Testinhalt"


def _baue_paket_mit_container(tmp_path: Path, container_name: str) -> Path:
    """Baut ein ZIP, das ein weiteres Archiv mit einer PDF enthält."""
    innen = tmp_path / "innen.bin"
    with zipfile.ZipFile(innen, "w") as zf:
        zf.writestr("akte_Aussonderung.Aussonderung.0503.xml", "<xdomea:Test/>")
        zf.writestr("schriftstueck.pdf", PDF_INHALT)

    aussen = tmp_path / "paket.zip"
    with zipfile.ZipFile(aussen, "w") as zf:
        zf.writestr("Lieferverzeichnis.xml", "<domeaAussonderungListen/>")
        zf.write(innen, container_name)
    return aussen


def test_datei_direkt_im_zip_wird_gefunden(tmp_path):
    paket = tmp_path / "paket.zip"
    with zipfile.ZipFile(paket, "w") as zf:
        zf.writestr("direkt.pdf", PDF_INHALT)

    assert finde_datei_in_paket(paket, "direkt.pdf") == PDF_INHALT


def test_datei_in_xdomea_container_wird_gefunden(tmp_path):
    """nscale eGOV: der Container trägt die Endung '.xdomea'."""
    paket = _baue_paket_mit_container(tmp_path, "akte_Aussonderung.Aussonderung.0503.xdomea")

    assert finde_datei_in_paket(paket, "schriftstueck.pdf") == PDF_INHALT


def test_datei_in_zip_container_wird_gefunden(tmp_path):
    """elKA: das Sammel-ZIP enthält die Pakete als verschachtelte ZIPs."""
    paket = _baue_paket_mit_container(tmp_path, "unterpaket.zip")

    assert finde_datei_in_paket(paket, "schriftstueck.pdf") == PDF_INHALT


def test_unbekannte_datei_liefert_none(tmp_path):
    paket = _baue_paket_mit_container(tmp_path, "unterpaket.zip")

    assert finde_datei_in_paket(paket, "gibtsnicht.pdf") is None


def test_nur_basisname_zaehlt_kein_pfad(tmp_path):
    """Aus der Eingabe darf kein Pfad konstruiert werden."""
    paket = _baue_paket_mit_container(tmp_path, "unterpaket.zip")

    assert finde_datei_in_paket(paket, "../../etc/passwd") is None


def test_zu_grosse_datei_wird_abgelehnt(tmp_path):
    paket = tmp_path / "paket.zip"
    with zipfile.ZipFile(paket, "w") as zf:
        zf.writestr("gross.pdf", b"x" * 2048)

    with pytest.raises(ValueError, match="zu groß"):
        finde_datei_in_paket(paket, "gross.pdf", max_groesse=1024)
