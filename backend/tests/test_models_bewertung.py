"""Tests für die neuen Bewertungsmodelle."""

import pytest
from models import (
    Base,
    BehoerdenProfil,
    Bewertung,
    BewertungSchwellwerte,
    DokumentBewertung,
    IndizKatalog,
    VorgangBewertung,
    VorgangIndizTreffer,
)
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_behoerdenprofil_anlegen(db):
    profil = BehoerdenProfil(
        name="BSP",
        kuerzel="BSP",
        hierarchie_stufe="ministerium",
        besondere_oe=["Leitungsstab"],
        aktenzeichen_praefix="BSP",
    )
    db.add(profil)
    db.commit()
    assert profil.id is not None


def test_schwellwerte_singleton(db):
    sw = BewertungSchwellwerte(schwelle_a=70, schwelle_v=30, aktualisiert_von="admin")
    db.add(sw)
    db.commit()
    assert sw.id == 1

    # Zweite Zeile mit nur_eins="global" muss einen IntegrityError auslösen
    sw2 = BewertungSchwellwerte(nur_eins="global", schwelle_a=80, schwelle_v=40)
    db.add(sw2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_indizkatalog_unique_schluessel(db):
    db.add(IndizKatalog(schluessel="ko_test", bezeichnung="x", punkte=10, typ="boost", aktiv=True))
    db.commit()
    db.add(IndizKatalog(schluessel="ko_test", bezeichnung="y", punkte=5, typ="boost", aktiv=True))
    with pytest.raises(Exception):
        db.commit()


def test_vorgang_bewertung_mit_treffern(db):
    vb = VorgangBewertung(
        job_id="job-1",
        vorgang_id="vg-1",
        aktenzeichen="12-34/2024",
        betreff="Test",
        rohscore=50,
        finaler_score=50,
        klassifikation_vorschlag="B",
    )
    db.add(vb)
    db.commit()
    treffer = VorgangIndizTreffer(
        vorgang_bewertung_id=vb.id,
        indiz_schluessel="federfuehrung_eigene_stelle",
        erfuellt=True,
        punkte_beitrag=25,
        evidenz="aus xdomea:Federfuehrung",
    )
    db.add(treffer)
    db.commit()
    assert treffer.id is not None


def test_vorgang_bewertung_unique_job_vorgang(db):
    """Zwei VorgangBewertung mit derselben (job_id, vorgang_id) müssen IntegrityError werfen."""
    vb1 = VorgangBewertung(
        job_id="job-2",
        vorgang_id="vg-1",
        rohscore=0,
        finaler_score=0,
        klassifikation_vorschlag="B",
    )
    db.add(vb1)
    db.commit()

    vb2 = VorgangBewertung(
        job_id="job-2",
        vorgang_id="vg-1",
        rohscore=10,
        finaler_score=10,
        klassifikation_vorschlag="A",
    )
    db.add(vb2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_dokument_bewertung_enum(db):
    """DokumentBewertung.bewertung akzeptiert Bewertung-Enum-Werte und hat erstellt_am."""
    vb = VorgangBewertung(
        job_id="job-3",
        vorgang_id="vg-1",
        rohscore=0,
        finaler_score=0,
        klassifikation_vorschlag="B",
    )
    db.add(vb)
    db.commit()

    dok = DokumentBewertung(
        vorgang_bewertung_id=vb.id,
        dateiname="bescheid.pdf",
        bewertung=Bewertung.archivwuerdig,
        begruendung="Wichtiges Dokument.",
    )
    db.add(dok)
    db.commit()
    assert dok.id is not None
    assert dok.erstellt_am is not None
    assert dok.bewertung == Bewertung.archivwuerdig
