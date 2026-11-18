"""Tests für Agent 1 – Formale Prüfung."""

import shutil
from pathlib import Path

import pytest
from agents.agent_formal import pruefe_formal
from utils.zip_handler import entpacke_zip

FIXTURES_DIR = Path(__file__).parent / "fixtures"
# Die Fixtures sind als minimales XDOMEA-Beispiel gebaut (<Aussonderung>-Root).
# Das echte XDOMEA-2.3-XSD erwartet Root-Tags wie <Aussonderung.Aussonderung.0503>.
# Für die Tests nutzen wir daher das mitgelieferte Stub-XSD xdomea_2.3.xsd, das
# explizit als Test-Schema markiert ist.
STUB_XSD_23 = Path(__file__).parent.parent / "assets" / "xsd" / "xdomea_2.3.xsd"


@pytest.fixture(autouse=True)
def _stub_xsd_fuer_23(monkeypatch):
    """Ersetzt den XSD-Pfad für Version 2.3 durch den Test-Stub."""
    from agents import agent_formal

    if STUB_XSD_23.exists():
        gepatchte_map = dict(agent_formal.XSD_DATEIEN)
        gepatchte_map["2.3"] = STUB_XSD_23
        monkeypatch.setattr(agent_formal, "XSD_DATEIEN", gepatchte_map)


@pytest.fixture
def arbeitsverzeichnis_gueltig(tmp_path: Path) -> Path:
    """Entpackt das gültige Test-ZIP in ein temporäres Verzeichnis."""
    zip_pfad = FIXTURES_DIR / "gueltig.zip"
    if not zip_pfad.exists():
        pytest.skip("Test-Fixture gueltig.zip nicht vorhanden.")
    ziel = tmp_path / "arbeit_formal"
    ziel.mkdir()
    entpacke_zip(zip_pfad, ziel)
    return ziel


@pytest.fixture
def arbeitsverzeichnis_ungueltig_xml(tmp_path: Path) -> Path:
    """Entpackt das ZIP mit ungültiger XML in ein temporäres Verzeichnis."""
    zip_pfad = FIXTURES_DIR / "ungueltig_xml.zip"
    if not zip_pfad.exists():
        pytest.skip("Test-Fixture ungueltig_xml.zip nicht vorhanden.")
    ziel = tmp_path / "arbeit_formal"
    ziel.mkdir()
    entpacke_zip(zip_pfad, ziel)
    return ziel


@pytest.fixture
def arbeitsverzeichnis_fehlende_dateien(tmp_path: Path) -> Path:
    """Entpackt das ZIP mit fehlenden Dateien in ein temporäres Verzeichnis."""
    zip_pfad = FIXTURES_DIR / "fehlende_dateien.zip"
    if not zip_pfad.exists():
        pytest.skip("Test-Fixture fehlende_dateien.zip nicht vorhanden.")
    ziel = tmp_path / "arbeit_formal"
    ziel.mkdir()
    entpacke_zip(zip_pfad, ziel)
    return ziel


@pytest.mark.asyncio
async def test_formal_happy_path(arbeitsverzeichnis_gueltig: Path):
    """Gültiges ZIP: Status ok, keine Fehler, Version erkannt."""
    ergebnis = await pruefe_formal("test-job-1", arbeitsverzeichnis_gueltig)

    assert ergebnis["status"] == "ok"
    assert ergebnis["fehler"] == []
    assert ergebnis["xdomea_version"] == "2.3"
    assert ergebnis["xsd_fehler"] == []

    # Referenzabgleich: alle Dateien vorhanden
    for xml_name, ref in ergebnis["referenz_abgleich"].items():
        assert ref["fehlend"] == []


@pytest.mark.asyncio
async def test_formal_ungueltige_xml(arbeitsverzeichnis_ungueltig_xml: Path):
    """Ungültige XML: Status fehler, XSD-Fehler vorhanden."""
    ergebnis = await pruefe_formal("test-job-2", arbeitsverzeichnis_ungueltig_xml)

    assert ergebnis["status"] == "fehler"
    assert len(ergebnis["xsd_fehler"]) > 0


@pytest.mark.asyncio
async def test_formal_fehlende_dateien(arbeitsverzeichnis_fehlende_dateien: Path):
    """Fehlende Dateien: Status fehler, fehlende Dateien gemeldet."""
    ergebnis = await pruefe_formal("test-job-3", arbeitsverzeichnis_fehlende_dateien)

    assert ergebnis["status"] == "fehler"

    # Mindestens eine fehlende Datei im Referenzabgleich
    hat_fehlende = False
    for xml_name, ref in ergebnis["referenz_abgleich"].items():
        if ref["fehlend"]:
            hat_fehlende = True
    assert hat_fehlende


@pytest.mark.asyncio
async def test_formal_keine_xml(tmp_path: Path):
    """Leeres Verzeichnis: Status fehler, Meldung 'Keine XML-Datei'."""
    ergebnis = await pruefe_formal("test-job-4", tmp_path)

    assert ergebnis["status"] == "fehler"
    assert any("Keine XML-Datei" in f for f in ergebnis["fehler"])


# ---------------------------------------------------------------------------
# Fremd-DMS-Kompatibilität: bekannte Schema-Abweichungen sind Hinweise,
# echte Strukturfehler bleiben kritisch.
# ---------------------------------------------------------------------------


def test_dms_schemaabweichungen_sind_hinweise():
    """Bekannte enaio-Export-Muster werden als Hinweise (nicht kritisch) eingestuft."""
    from agents.agent_formal import _klassifiziere_xsd_fehler

    NS = "{urn:xoev-de:xdomea:schema:3.0.0}"
    fehler = [
        "x.xml:6:0:ERROR:SCHEMASV:SCHEMAV_CVC_ENUMERATION_VALID: Element 'code': "
        "[facet 'enumeration'] The value '' is not an element of the set {'0101'}.",
        "x.xml:36:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: Element 'name': "
        "This element is not expected.",
        f"x.xml:721:0:ERROR:SCHEMASV:SCHEMAV_CVC_PATTERN_VALID: Element '{NS}ID': "
        "[facet 'pattern'] The value '25609217' is not accepted by the pattern '[0-9A-Fa-f]{8}-'.",
        f"x.xml:676:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: Element '{NS}Primaerdokument': "
        f"This element is not expected. Expected is one of ( {NS}SonstigerName, {NS}Version ).",
    ]
    kritische, hinweise, _ = _klassifiziere_xsd_fehler(fehler)
    assert kritische == [], f"Erwartete keine kritischen Fehler, bekam: {kritische}"
    assert len(hinweise) == 4


def test_unbekanntes_element_bleibt_kritisch():
    """Ein wirklich unbekanntes Element bleibt ein kritischer Schema-Fehler."""
    from agents.agent_formal import _klassifiziere_xsd_fehler

    fehler = [
        "x.xml:5:0:ERROR:SCHEMASV:SCHEMAV_ELEMENT_CONTENT: Element 'UnbekanntesFeld': "
        "This element is not expected.",
    ]
    kritische, _, _ = _klassifiziere_xsd_fehler(fehler)
    assert len(kritische) == 1
