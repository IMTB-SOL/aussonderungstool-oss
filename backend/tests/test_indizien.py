"""Tests für Indizien-Extraktoren und Scoring."""

from unittest.mock import MagicMock

import pytest
from utils.indizien import (
    IndizTreffer,
    extrahiere_archivvermerk_behoerde,
    extrahiere_aufbewahrungsdauer_30_plus,
    extrahiere_besondere_oe,
    extrahiere_dickitaet_hoch,
    extrahiere_federfuehrung,
    extrahiere_hierarchie_ministerium,
    extrahiere_hoechste_zeichnung_minister,
    extrahiere_ifg_ueg_vermerk,
    extrahiere_viele_versionen,
    extrahiere_vorbewertung_gleicher_az,
)


def test_indiz_treffer_dataclass():
    t = IndizTreffer(schluessel="x", erfuellt=True, evidenz="weil-test")
    assert t.schluessel == "x"
    assert t.erfuellt is True
    assert t.evidenz == "weil-test"


def test_federfuehrung_erfuellt():
    vorgang = {"federfuehrung": "Eigene Stelle"}
    treffer = extrahiere_federfuehrung(vorgang, profil=None)
    assert treffer.schluessel == "federfuehrung_eigene_stelle"
    assert treffer.erfuellt is True
    assert "Eigene Stelle" in treffer.evidenz


def test_federfuehrung_nicht_erfuellt():
    vorgang = {"federfuehrung": "Fremde Behörde XY"}
    treffer = extrahiere_federfuehrung(vorgang, profil=None)
    assert treffer.erfuellt is False


def test_federfuehrung_leer():
    vorgang = {"federfuehrung": ""}
    treffer = extrahiere_federfuehrung(vorgang, profil=None)
    assert treffer.erfuellt is False
    assert "kein eintrag" in treffer.evidenz.lower()


def test_archivvermerk_erfuellt():
    vorgang = {"aussonderungsart": "Archivieren"}
    t = extrahiere_archivvermerk_behoerde(vorgang, None)
    assert t.erfuellt is True


def test_archivvermerk_nicht_erfuellt():
    vorgang = {"aussonderungsart": "Vernichten"}
    t = extrahiere_archivvermerk_behoerde(vorgang, None)
    assert t.erfuellt is False


def test_archivvermerk_code_A():
    """XDOMEA-Codeliste 0026: 'A' steht für Archivieren."""
    assert extrahiere_archivvermerk_behoerde({"aussonderungsart": "A"}, None).erfuellt is True
    assert extrahiere_archivvermerk_behoerde({"aussonderungsart": "a"}, None).erfuellt is True
    assert extrahiere_archivvermerk_behoerde({"aussonderungsart": "V"}, None).erfuellt is False
    assert extrahiere_archivvermerk_behoerde({"aussonderungsart": "B"}, None).erfuellt is False
    assert extrahiere_archivvermerk_behoerde({"aussonderungsart": ""}, None).erfuellt is False


def test_minister_zeichnung_erfuellt():
    vorgang = {"betreff": "Vorlage zur Zeichnung durch den Minister"}
    t = extrahiere_hoechste_zeichnung_minister(vorgang, None)
    assert t.erfuellt is True


def test_minister_zeichnung_nicht_erfuellt():
    vorgang = {"betreff": "Routine"}
    t = extrahiere_hoechste_zeichnung_minister(vorgang, None)
    assert t.erfuellt is False


def test_aufbewahrungsdauer_30_plus():
    assert extrahiere_aufbewahrungsdauer_30_plus({"aufbewahrungsdauer": "30"}, None).erfuellt
    assert extrahiere_aufbewahrungsdauer_30_plus({"aufbewahrungsdauer": "P50Y"}, None).erfuellt
    assert not extrahiere_aufbewahrungsdauer_30_plus({"aufbewahrungsdauer": "5"}, None).erfuellt
    assert not extrahiere_aufbewahrungsdauer_30_plus({"aufbewahrungsdauer": ""}, None).erfuellt


def test_dickitaet():
    assert extrahiere_dickitaet_hoch({"dokumente": [{} for _ in range(20)]}, None).erfuellt
    assert not extrahiere_dickitaet_hoch({"dokumente": [{} for _ in range(19)]}, None).erfuellt


def test_viele_versionen():
    vorgang = {"dokumente": [{"versionen": 4}, {"versionen": 5}, {"versionen": 3}]}
    assert extrahiere_viele_versionen(vorgang, None).erfuellt  # avg = 4.0
    assert not extrahiere_viele_versionen({"dokumente": [{"versionen": 1}]}, None).erfuellt


def test_ifg_ueg_im_betreff():
    assert extrahiere_ifg_ueg_vermerk({"betreff": "IFG-Anfrage zu Bauantrag"}, None).erfuellt
    assert extrahiere_ifg_ueg_vermerk(
        {"betreff": "Umweltinformationsgesetz Vorgang"}, None
    ).erfuellt
    assert not extrahiere_ifg_ueg_vermerk({"betreff": "Urlaubsantrag"}, None).erfuellt


# ---------------------------------------------------------------------------
# Profil- und Historien-basierte Extraktoren
# ---------------------------------------------------------------------------


def test_hierarchie_ministerium_erfuellt():
    profil = MagicMock(hierarchie_stufe="ministerium")
    assert extrahiere_hierarchie_ministerium({}, profil).erfuellt


def test_hierarchie_ministerium_nicht_erfuellt():
    profil = MagicMock(hierarchie_stufe="unterbehoerde")
    assert not extrahiere_hierarchie_ministerium({}, profil).erfuellt


def test_hierarchie_ohne_profil():
    assert not extrahiere_hierarchie_ministerium({}, None).erfuellt


def test_besondere_oe_treffer():
    profil = MagicMock(besondere_oe=["Leitungsstab", "Z-Abteilung"])
    vorgang = {"betreff": "Vorlage aus dem Leitungsstab"}
    assert extrahiere_besondere_oe(vorgang, profil).erfuellt


def test_besondere_oe_kein_treffer():
    profil = MagicMock(besondere_oe=["Leitungsstab"])
    assert not extrahiere_besondere_oe({"betreff": "Routine"}, profil).erfuellt


def test_vorbewertung_findet_treffer():
    db = MagicMock()
    vorherige = MagicMock(klassifikation_final="A", aktenzeichen="12-34/2023")
    db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = vorherige

    treffer = extrahiere_vorbewertung_gleicher_az(
        {"aktenzeichen": "12-34/2024"},
        None,
        db=db,
    )
    assert treffer.erfuellt
    assert "12-34" in treffer.evidenz


def test_vorbewertung_kein_treffer():
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None
    assert not extrahiere_vorbewertung_gleicher_az({"aktenzeichen": "X/2024"}, None, db=db).erfuellt


# ---------------------------------------------------------------------------
# Scoring-Engine
# ---------------------------------------------------------------------------

from utils.indizien import berechne_score


def _make_katalog(eintraege):
    """eintraege: list of (schluessel, punkte, typ)."""
    return [
        MagicMock(schluessel=s, punkte=p, typ=t, aktiv=True, bezeichnung=s) for s, p, t in eintraege
    ]


def test_score_einfache_addition():
    katalog = _make_katalog(
        [
            ("federfuehrung_eigene_stelle", 25, "ko_deckel"),
            ("aufbewahrungsdauer_30_plus", 10, "boost"),
        ]
    )
    vorgang = {"federfuehrung": "Eigene Stelle", "aufbewahrungsdauer": "30", "dokumente": []}
    ergebnis = berechne_score(vorgang, profil=None, katalog=katalog, schwelle_a=70, schwelle_v=30)
    assert ergebnis["rohscore"] == 35
    assert ergebnis["finaler_score"] == 35
    assert ergebnis["klassifikation"] == "B"


def test_ko_deckel_verhindert_a():
    katalog = _make_katalog(
        [
            ("federfuehrung_eigene_stelle", 25, "ko_deckel"),
            ("aufbewahrungsdauer_30_plus", 100, "boost"),
        ]
    )
    vorgang = {"federfuehrung": "Fremde Stelle", "aufbewahrungsdauer": "30", "dokumente": []}
    ergebnis = berechne_score(vorgang, None, katalog, schwelle_a=70, schwelle_v=30)
    assert ergebnis["rohscore"] == 100
    assert ergebnis["finaler_score"] == 69
    assert ergebnis["klassifikation"] == "B"


def test_ok_boden_erzwingt_a():
    katalog = _make_katalog(
        [
            ("federfuehrung_eigene_stelle", 25, "ko_deckel"),
            ("archivvermerk_behoerde", 40, "ok_boden"),
        ]
    )
    vorgang = {"federfuehrung": "", "aussonderungsart": "Archivieren", "dokumente": []}
    ergebnis = berechne_score(vorgang, None, katalog, schwelle_a=70, schwelle_v=30)
    assert ergebnis["finaler_score"] >= 70
    assert ergebnis["klassifikation"] == "A"


def test_ok_boden_ueberstimmt_ko_deckel():
    katalog = _make_katalog(
        [
            ("federfuehrung_eigene_stelle", 25, "ko_deckel"),
            ("archivvermerk_behoerde", 40, "ok_boden"),
        ]
    )
    vorgang = {"federfuehrung": "Fremde Stelle", "aussonderungsart": "Archivieren", "dokumente": []}
    ergebnis = berechne_score(vorgang, None, katalog, schwelle_a=70, schwelle_v=30)
    assert ergebnis["klassifikation"] == "A"


def test_klassifikation_v_bei_niedrigem_score():
    katalog = _make_katalog([("federfuehrung_eigene_stelle", 25, "ko_deckel")])
    vorgang = {"federfuehrung": "", "dokumente": []}
    ergebnis = berechne_score(vorgang, None, katalog, schwelle_a=70, schwelle_v=30)
    assert ergebnis["finaler_score"] == 0
    assert ergebnis["klassifikation"] == "V"


def test_inaktive_indizien_werden_ignoriert():
    katalog = [
        MagicMock(
            schluessel="federfuehrung_eigene_stelle",
            punkte=25,
            typ="ko_deckel",
            aktiv=False,
            bezeichnung="x",
        ),
    ]
    vorgang = {"federfuehrung": "Eigene Stelle", "dokumente": []}
    ergebnis = berechne_score(vorgang, None, katalog, schwelle_a=70, schwelle_v=30)
    assert ergebnis["rohscore"] == 0
    assert ergebnis["treffer"] == []
