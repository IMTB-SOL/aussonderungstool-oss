"""Tests für die Seed-Funktion."""

import pytest
from models import Base, BewertungSchwellwerte, IndizKatalog, SystemPrompt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from utils.seed_bewertung import seede_bewertungsdaten


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seed_legt_schwellwerte_an(db):
    seede_bewertungsdaten(db)
    sw = db.query(BewertungSchwellwerte).one()
    assert sw.schwelle_a == 70
    assert sw.schwelle_v == 30


def test_seed_legt_indizienkatalog_an(db):
    seede_bewertungsdaten(db)
    schluessel = {i.schluessel for i in db.query(IndizKatalog).all()}
    assert "federfuehrung_eigene_stelle" in schluessel
    assert "archivvermerk_behoerde" in schluessel
    assert len(schluessel) == 10


def test_seed_ist_idempotent(db):
    seede_bewertungsdaten(db)
    seede_bewertungsdaten(db)
    assert db.query(BewertungSchwellwerte).count() == 1
    assert db.query(IndizKatalog).count() == 10


def test_seed_legt_system_prompts_an(db):
    seede_bewertungsdaten(db)
    schluessel = {p.schluessel for p in db.query(SystemPrompt).all()}
    assert schluessel == {"erschliessung", "bewertung_begruendung"}
    for p in db.query(SystemPrompt).all():
        assert p.inhalt.strip(), f"Prompt {p.schluessel} hat leeren Inhalt"


def test_seed_system_prompts_idempotent_ohne_ueberschreiben(db):
    seede_bewertungsdaten(db)
    p = db.query(SystemPrompt).filter_by(schluessel="erschliessung").one()
    p.inhalt = "manuell angepasst"
    db.commit()

    seede_bewertungsdaten(db)
    assert db.query(SystemPrompt).count() == 2
    p_neu = db.query(SystemPrompt).filter_by(schluessel="erschliessung").one()
    assert p_neu.inhalt == "manuell angepasst"
