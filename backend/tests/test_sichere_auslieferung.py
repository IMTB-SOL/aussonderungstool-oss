# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die Auslieferung von Paket-Dateien in der Vorschau.

Eine Datei darf nur dann inline mit ihrem Medientyp ausgeliefert werden, wenn
der Inhalt die Endung auch bestätigt. Sonst behauptet die Vorschau ein Format,
das der Viewer nicht rendern kann (Praxisfall: XAIP-XML mit Endung ".pdf").
"""

from routers.jobs import _sichere_auslieferung

PDF = b"%PDF-1.4\ntrailer\n%%EOF\n"
XML = b'<?xml version="1.0"?>\n<XAIP/>\n'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_echtes_pdf_wird_inline_ausgeliefert():
    medientyp, disposition = _sichere_auslieferung("dokument.pdf", PDF)

    assert medientyp == "application/pdf"
    assert disposition == "inline"


def test_xml_mit_pdf_endung_wird_nicht_als_pdf_ausgeliefert():
    """Der Praxisfall: Inhalt widerspricht der Endung."""
    medientyp, disposition = _sichere_auslieferung("dokument.pdf", XML)

    assert medientyp == "application/octet-stream"
    assert disposition == "attachment"


def test_echtes_png_wird_inline_ausgeliefert():
    medientyp, disposition = _sichere_auslieferung("bild.png", PNG)

    assert medientyp == "image/png"
    assert disposition == "inline"


def test_html_bleibt_download_auch_bei_passendem_inhalt():
    """Aktive Formate werden nie inline ausgeliefert (Stored XSS)."""
    medientyp, disposition = _sichere_auslieferung("seite.html", b"<html><body>hi</body></html>")

    assert medientyp == "application/octet-stream"
    assert disposition == "attachment"


def test_ohne_inhalt_gilt_die_endung():
    """Ohne Daten bleibt das bisherige, endungsbasierte Verhalten."""
    medientyp, disposition = _sichere_auslieferung("dokument.pdf", None)

    assert medientyp == "application/pdf"
    assert disposition == "inline"
