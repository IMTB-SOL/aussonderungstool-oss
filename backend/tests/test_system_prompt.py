"""Tests für editierbare System-Prompts: Laden mit DB-Inhalt und Fallback."""

import models
import pytest
from models import Base, SystemPrompt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def memory_engine(monkeypatch):
    """In-Memory-DB, die models.engine für die Dauer des Tests ersetzt."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    monkeypatch.setattr(models, "engine", eng)
    return eng


def test_lade_system_prompt_aus_db(memory_engine):
    """Wenn ein DB-Eintrag existiert, wird dessen Inhalt geliefert."""
    with Session(memory_engine) as db:
        db.add(SystemPrompt(schluessel="erschliessung", inhalt="DB-INHALT-XYZ"))
        db.commit()

    from utils.openai_client import lade_system_prompt

    assert lade_system_prompt("erschliessung") == "DB-INHALT-XYZ"


def test_lade_system_prompt_fallback_auf_default(memory_engine):
    """Ohne DB-Eintrag wird die Default-Konstante zurückgegeben."""
    from utils.openai_client import SYSTEM_PROMPT_REGISTRY, lade_system_prompt

    erwartet = SYSTEM_PROMPT_REGISTRY["bewertung_begruendung"]["default"]
    assert lade_system_prompt("bewertung_begruendung") == erwartet


def test_lade_system_prompt_leerer_db_eintrag_faellt_zurueck(memory_engine):
    """Ein leerer DB-Inhalt wird ignoriert; die Default-Konstante greift."""
    with Session(memory_engine) as db:
        db.add(SystemPrompt(schluessel="erschliessung", inhalt="   "))
        db.commit()

    from utils.openai_client import SYSTEM_PROMPT_REGISTRY, lade_system_prompt

    erwartet = SYSTEM_PROMPT_REGISTRY["erschliessung"]["default"]
    assert lade_system_prompt("erschliessung") == erwartet


def test_lade_system_prompt_unbekannter_schluessel(memory_engine):
    """Ein nicht registrierter Schlüssel wirft KeyError."""
    from utils.openai_client import lade_system_prompt

    with pytest.raises(KeyError):
        lade_system_prompt("gibt_es_nicht")
