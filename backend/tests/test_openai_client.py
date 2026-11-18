"""Tests für die provider-abhängige LLM-Client-Erstellung."""

from models import Base, LLMEinstellung, engine
from sqlalchemy.orm import Session
from utils import openai_client


def _setze_provider(provider: str) -> None:
    """Setzt den aktiven Provider direkt in der DB (Singleton-Zeile)."""
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        eintrag = db.query(LLMEinstellung).first()
        if eintrag is None:
            eintrag = LLMEinstellung()
            db.add(eintrag)
        eintrag.aktiver_provider = provider
        db.commit()


def test_lade_aktiven_provider_ollama():
    _setze_provider("ollama")
    assert openai_client._lade_aktiven_provider() == "ollama"


def test_lade_aktiven_provider_scaleway():
    _setze_provider("scaleway")
    assert openai_client._lade_aktiven_provider() == "scaleway"
    _setze_provider("ollama")  # Aufräumen


def test_erstelle_client_ollama():
    _setze_provider("ollama")
    client, modell, extra_body = openai_client._erstelle_client()
    assert "11434" in str(client.base_url)
    assert "num_ctx" in extra_body


def test_erstelle_client_scaleway(monkeypatch):
    # SCALEWAY_* sind Modul-Konstanten (beim Import gebunden) — daher per
    # setattr am Modul überschreiben, nicht per setenv.
    monkeypatch.setattr(openai_client, "SCALEWAY_BASE_URL", "https://api.scaleway.ai/v1")
    monkeypatch.setattr(openai_client, "SCALEWAY_MODEL", "test-modell")
    _setze_provider("scaleway")
    client, modell, extra_body = openai_client._erstelle_client()
    assert "scaleway.ai" in str(client.base_url)
    assert modell == "test-modell"
    assert extra_body == {}
    _setze_provider("ollama")  # Aufräumen


def test_erstelle_client_scaleway_modell_aus_db(monkeypatch):
    """_erstelle_client nutzt für Scaleway das in der DB gesetzte Modell."""
    monkeypatch.setattr(openai_client, "SCALEWAY_MODEL", "env-default-modell")
    _setze_provider("scaleway")
    with Session(engine) as db:
        eintrag = db.query(LLMEinstellung).first()
        eintrag.scaleway_modell = "db-modell-xyz"
        db.commit()
    try:
        _client, modell, _extra = openai_client._erstelle_client()
        assert modell == "db-modell-xyz"
    finally:
        with Session(engine) as db:
            eintrag = db.query(LLMEinstellung).first()
            eintrag.scaleway_modell = None
            db.commit()
        _setze_provider("ollama")


def test_erstelle_client_scaleway_modell_fallback_env(monkeypatch):
    """Ist kein scaleway_modell in der DB gesetzt, greift der Env-Default."""
    monkeypatch.setattr(openai_client, "SCALEWAY_MODEL", "env-default-modell")
    _setze_provider("scaleway")
    with Session(engine) as db:
        eintrag = db.query(LLMEinstellung).first()
        eintrag.scaleway_modell = None
        db.commit()
    try:
        _client, modell, _extra = openai_client._erstelle_client()
        assert modell == "env-default-modell"
    finally:
        _setze_provider("ollama")


def test_prompt_tiefenpruefung_dokument_struktur():
    """Der Tiefenprüfungs-Prompt verlangt eine minimale Zwei-Felder-Antwort."""
    from utils.openai_client import PROMPT_TIEFENPRUEFUNG_DOKUMENT

    prompt = PROMPT_TIEFENPRUEFUNG_DOKUMENT
    assert isinstance(prompt, str) and prompt.strip()
    # Die drei Bewertungs-Enum-Werte müssen wörtlich vorkommen,
    # weil agent_bewertung.py zeichengenau dagegen mappt.
    assert "archivwürdig" in prompt
    assert "nicht archivwürdig" in prompt
    assert "manuelle Überprüfung" in prompt
    # Genau die zwei Antwortfelder, die der Agent ausliest.
    assert '"bewertung"' in prompt
    assert '"begruendung"' in prompt
    # Die große Mehrdokument-Paket-Struktur darf NICHT mehr verlangt werden.
    assert "gesamtbewertung" not in prompt
    assert "indikatoren" not in prompt
