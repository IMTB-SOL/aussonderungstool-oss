# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für den Abgleich zwischen Dateiendung und tatsächlichem Inhalt.

Anlass: Ein in der Praxis beobachtetes Winyard-Export-Paket enthält eine
XAIP-XML-Datei mit der Endung ".pdf" — das XDOMEA-XML deklariert dazu Format
"018 = pdf". Ein Archiv, das sich auf Endung oder Deklaration verlässt, kann
die Datei nicht rendern. Der Widerspruch muss als Befund auffallen.
"""

from agents.agent_format import _pruefe_mime_und_archiv, passt_mime_zur_endung

XAIP_INHALT = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<XAIP xmlns="urn:oasis:names:tc:dss">\n'
    b"  <packageHeader><AOID>test</AOID></packageHeader>\n</XAIP>\n"
)


def test_xaip_mit_pdf_endung_erzeugt_befund(tmp_path):
    """Der Praxisfall (XAIP-XML als .pdf) muss im Formatprotokoll als Warnung auftauchen."""
    datei = tmp_path / "0a1b2c3d_Beispieldokument.pdf"
    datei.write_bytes(XAIP_INHALT)
    meldungen: list[dict] = []

    ergebnis = _pruefe_mime_und_archiv(datei, meldungen)

    assert ergebnis["endung_passt"] is False
    warnungen = [m["text"] for m in meldungen if m["typ"] == "warnung"]
    assert any(".pdf" in t and "PDF" in t for t in warnungen), warnungen


def test_echtes_pdf_erzeugt_keinen_befund(tmp_path):
    datei = tmp_path / "echt.pdf"
    datei.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n")
    meldungen: list[dict] = []

    ergebnis = _pruefe_mime_und_archiv(datei, meldungen)

    assert ergebnis["endung_passt"] is True
    assert not [m for m in meldungen if m["typ"] == "warnung"]


def test_pdf_endung_mit_pdf_inhalt_passt():
    assert passt_mime_zur_endung(".pdf", "application/pdf") is True


def test_pdf_endung_mit_xml_inhalt_passt_nicht():
    """Der Praxisfall: XAIP-XML als .pdf ausgeliefert."""
    assert passt_mime_zur_endung(".pdf", "text/xml") is False


def test_docx_endung_mit_zip_inhalt_passt():
    """OOXML-Formate sind ZIP-Container — magic meldet je nach Version beides."""
    assert (
        passt_mime_zur_endung(
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        is True
    )
    assert passt_mime_zur_endung(".docx", "application/zip") is True


def test_jpg_und_jpeg_gelten_gleich():
    assert passt_mime_zur_endung(".jpg", "image/jpeg") is True
    assert passt_mime_zur_endung(".jpeg", "image/jpeg") is True


def test_unbekannte_endung_wird_nicht_beanstandet():
    """Ohne Erwartungswert darf nichts gemeldet werden — keine Falschbefunde."""
    assert passt_mime_zur_endung(".vif", "application/octet-stream") is True
    assert passt_mime_zur_endung("", "application/pdf") is True


def test_fehlender_mime_wird_nicht_beanstandet():
    assert passt_mime_zur_endung(".pdf", None) is True


def test_doc_endung_mit_ole_inhalt_passt():
    assert passt_mime_zur_endung(".doc", "application/msword") is True
