# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die Paarbildung des Inhaltsvergleichs.

DMS-Exporte legen die PDF-Rendition eines Dokuments als "<name>.docx.pdf"
neben das Original "<name>.docx" — die Endung wird angehängt, nicht ersetzt.
Wird nur auf gleichen Dateistamm geprüft, findet der Agent kein einziges Paar
und der gesamte Prüfschritt läuft leer.
"""

from agents.agent_inhalt import _finde_dateipaare


def _lege_an(verzeichnis, *namen):
    verzeichnis.mkdir(parents=True, exist_ok=True)
    for n in namen:
        (verzeichnis / n).write_bytes(b"inhalt")


def test_rendition_mit_angehaengter_endung_wird_gepaart(tmp_path):
    """Der Praxisfall: 823AC8FB_Aktenvermerk.docx + ....docx.pdf"""
    _lege_an(tmp_path, "823AC8FB_Aktenvermerk.docx", "823AC8FB_Aktenvermerk.docx.pdf")

    paare = _finde_dateipaare(tmp_path)

    assert len(paare) == 1, paare
    paar = next(iter(paare.values()))
    assert paar["docx"].name == "823AC8FB_Aktenvermerk.docx"
    assert paar["pdf"].name == "823AC8FB_Aktenvermerk.docx.pdf"


def test_klassische_paarung_mit_ersetzter_endung_bleibt(tmp_path):
    """Die bisherige Konvention <name>.docx / <name>.pdf muss weiter greifen."""
    _lege_an(tmp_path, "Bericht.docx", "Bericht.pdf")

    paare = _finde_dateipaare(tmp_path)

    assert len(paare) == 1, paare
    paar = next(iter(paare.values()))
    assert paar["docx"].name == "Bericht.docx"
    assert paar["pdf"].name == "Bericht.pdf"


def test_docx_ohne_pdf_ergibt_kein_paar(tmp_path):
    _lege_an(tmp_path, "Allein.docx", "Fremd.pdf")

    assert _finde_dateipaare(tmp_path) == {}


def test_kein_doppeltes_paar_wenn_beide_konventionen_vorliegen(tmp_path):
    """Liegen .pdf UND .docx.pdf vor, entsteht genau ein Paar je Dokument."""
    _lege_an(tmp_path, "Doku.docx", "Doku.pdf", "Doku.docx.pdf")

    paare = _finde_dateipaare(tmp_path)

    assert len(paare) == 1, paare


def test_macos_resource_forks_werden_ignoriert(tmp_path):
    _lege_an(tmp_path, "._Bericht.docx", "._Bericht.docx.pdf")

    assert _finde_dateipaare(tmp_path) == {}
