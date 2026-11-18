# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die Aufbereitung der Paketstruktur zur Anzeige im Frontend."""

from utils.paket_anzeige import struktur_zu_anzeige


def _dok(betreff: str, datei: str) -> dict:
    return {"betreff": betreff, "dateiname": datei, "alle_dateinamen": [datei], "typ": ""}


def test_akte_mit_vorgang_und_dokument():
    struktur = {
        "ebene": "akten_mit_vorgaengen",
        "akten": [
            {
                "aktenzeichen": "AZ-1",
                "betreff": "Akte 1",
                "vorgaenge": [
                    {
                        "aktenzeichen": "VG-1",
                        "betreff": "Vorgang 1",
                        "dokumente": [_dok("D1", "d1.pdf")],
                    }
                ],
                "teilakten": [],
                "dokumente": [],
            }
        ],
    }

    akten, dateien, fehlend = struktur_zu_anzeige(struktur, {"d1.pdf"})

    assert len(akten) == 1
    assert akten[0]["az"] == "AZ-1"
    assert akten[0]["vorgaenge"][0]["dokumente"][0]["datei"] == "d1.pdf"
    assert dateien == 1
    assert fehlend == 0


def test_fehlende_datei_wird_gezaehlt():
    struktur = {
        "ebene": "vorgaenge",
        "vorgaenge": [
            {"aktenzeichen": "VG-1", "betreff": "V", "dokumente": [_dok("D1", "weg.pdf")]}
        ],
    }

    akten, dateien, fehlend = struktur_zu_anzeige(struktur, set())

    assert dateien == 1
    assert fehlend == 1
    assert akten[0]["dokumente"][0]["exist"] is False


def test_lose_dokumente_werden_angezeigt():
    """XDOMEA-2.0.x-Abgaben ohne Akte/Vorgang dürfen nicht leer dargestellt werden."""
    struktur = {
        "ebene": "dokumente",
        "vorgaenge": [
            {
                "aktenzeichen": "",
                "betreff": "Einzugsermaechtigung SEPA",
                "dokumente": [_dok("Einzugsermaechtigung SEPA", "sepa.pdf")],
            }
        ],
    }

    akten, dateien, fehlend = struktur_zu_anzeige(struktur, {"sepa.pdf"})

    assert len(akten) == 1, "Das lose Dokument muss als Einheit erscheinen"
    assert akten[0]["betreff"] == "Einzugsermaechtigung SEPA"
    assert len(akten[0]["dokumente"]) == 1
    assert dateien == 1
    assert fehlend == 0


def test_dokument_mit_mehreren_dateien_zaehlt_alle():
    """Ein XDOMEA-Dokument kann mehrere Formate bündeln (z.B. regisafe)."""
    struktur = {
        "ebene": "vorgaenge",
        "vorgaenge": [
            {
                "aktenzeichen": "VG-1",
                "betreff": "V",
                "dokumente": [
                    {
                        "betreff": "D1",
                        "dateiname": "a.docx",
                        "alle_dateinamen": ["a.docx", "a.docx.pdf"],
                        "typ": "",
                    }
                ],
            }
        ],
    }

    akten, dateien, fehlend = struktur_zu_anzeige(struktur, {"a.docx"})

    assert dateien == 2
    assert fehlend == 1
    assert akten[0]["dokumente"][0]["exist"] is False


def test_leeres_paket_liefert_keine_akten():
    akten, dateien, fehlend = struktur_zu_anzeige({"ebene": "leer"}, set())

    assert akten == []
    assert dateien == 0
    assert fehlend == 0
