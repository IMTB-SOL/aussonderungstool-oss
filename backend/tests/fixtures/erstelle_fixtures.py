"""Erstellt Test-ZIP-Dateien für die Agenten-Tests."""

import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


def erstelle_gueltiges_zip() -> Path:
    """Erstellt ein gültiges XDOMEA-ZIP mit XML und referenzierten Dateien."""
    zip_pfad = FIXTURES_DIR / "gueltig.zip"
    with zipfile.ZipFile(zip_pfad, "w") as zf:
        zf.write(FIXTURES_DIR / "gueltig_xdomea.xml", "aussonderung.xml")
        zf.writestr("testdokument.pdf", b"%PDF-1.4 Testinhalt")
        zf.writestr("testdokument.docx", b"PK Testinhalt DOCX")
    return zip_pfad


def erstelle_ungueltiges_xml_zip() -> Path:
    """Erstellt ein ZIP mit ungültiger XDOMEA-XML."""
    zip_pfad = FIXTURES_DIR / "ungueltig_xml.zip"
    with zipfile.ZipFile(zip_pfad, "w") as zf:
        zf.write(FIXTURES_DIR / "ungueltig_xdomea.xml", "aussonderung.xml")
    return zip_pfad


def erstelle_fehlende_dateien_zip() -> Path:
    """Erstellt ein ZIP dessen XML Dateien referenziert, die nicht vorhanden sind."""
    zip_pfad = FIXTURES_DIR / "fehlende_dateien.zip"
    with zipfile.ZipFile(zip_pfad, "w") as zf:
        zf.write(FIXTURES_DIR / "gueltig_xdomea.xml", "aussonderung.xml")
        # testdokument.pdf und testdokument.docx fehlen absichtlich
    return zip_pfad


if __name__ == "__main__":
    erstelle_gueltiges_zip()
    erstelle_ungueltiges_xml_zip()
    erstelle_fehlende_dateien_zip()
    print("Fixtures erstellt.")
