"""Seed-Daten für den Bewertungsagenten: Schwellwerte und Indizienkatalog."""

import logging

from models import BewertungSchwellwerte, IndizKatalog, IndizTyp, SystemPrompt
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


DEFAULT_INDIZIEN = [
    (
        "federfuehrung_eigene_stelle",
        "Eigene Stelle ist federführend",
        25,
        IndizTyp.ko_deckel,
        "Wenn nicht federführend: Vorgang kann nicht A werden (Deckel auf B-Maximum).",
    ),
    (
        "archivvermerk_behoerde",
        "Archivierungsvermerk der Behörde vorhanden",
        40,
        IndizTyp.ok_boden,
        "Wenn vorhanden: Vorgang wird mindestens A (überstimmt KO-Deckel).",
    ),
    (
        "hoechste_zeichnung_minister",
        "Höchste Zeichnung auf Ministerebene",
        20,
        IndizTyp.boost,
        "Boost wenn Minister/Staatssekretär in Zeichnungskette.",
    ),
    (
        "hierarchie_ministerium",
        "Behörde ist Ministerium",
        15,
        IndizTyp.boost,
        "Boost basierend auf Behördenprofil-Hierarchiestufe.",
    ),
    (
        "besondere_oe",
        "Besondere Organisationseinheit beteiligt",
        15,
        IndizTyp.boost,
        "Boost wenn OE im Behördenprofil als besonders markiert ist.",
    ),
    (
        "aufbewahrungsdauer_30_plus",
        "Aufbewahrungsdauer ≥ 30 Jahre",
        10,
        IndizTyp.boost,
        "Lange Aufbewahrungsfrist als Hinweis auf Relevanz.",
    ),
    (
        "dickitaet_hoch",
        "Hohe Dickität (≥ 20 Dokumente)",
        10,
        IndizTyp.boost,
        "Umfangreiche Akte als schwacher Relevanz-Indikator.",
    ),
    (
        "viele_versionen",
        "Viele Dokumentversionen (Ø > 3)",
        5,
        IndizTyp.boost,
        "Häufige Überarbeitung deutet auf inhaltliche Tragweite.",
    ),
    (
        "ifg_ueg_vermerk",
        "IFG/UEG-Vermerk vorhanden",
        15,
        IndizTyp.boost,
        "Informationsfreiheits- oder Umweltinformationsbezug.",
    ),
    (
        "vorbewertung_gleicher_az",
        "Vorbewertung gleicher Aktenzeichen-Stamm war A",
        20,
        IndizTyp.boost,
        "Historischer Treffer aus früheren Bewertungsläufen.",
    ),
]


def seede_bewertungsdaten(db: Session) -> None:
    """Legt Default-Schwellwerte und Default-Indizienkatalog an, idempotent."""
    try:
        if db.query(BewertungSchwellwerte).count() == 0:
            db.add(BewertungSchwellwerte(schwelle_a=70, schwelle_v=30, aktualisiert_von="system"))
            logger.info("Default-Schwellwerte angelegt (A=70, V=30).")

        vorhandene = {i.schluessel for i in db.query(IndizKatalog).all()}
        neu = 0
        for schluessel, bez, punkte, typ, beschr in DEFAULT_INDIZIEN:
            if schluessel not in vorhandene:
                db.add(
                    IndizKatalog(
                        schluessel=schluessel,
                        bezeichnung=bez,
                        punkte=punkte,
                        typ=typ,
                        beschreibung=beschr,
                        aktiv=True,
                    )
                )
                neu += 1
        if neu:
            logger.info("Indizienkatalog: %d neue Einträge angelegt.", neu)

        # System-Prompts: bestehende Einträge nie überschreiben.
        from utils.openai_client import SYSTEM_PROMPT_REGISTRY

        vorhandene_prompts = {p.schluessel for p in db.query(SystemPrompt).all()}
        neue_prompts = 0
        for schluessel, eintrag in SYSTEM_PROMPT_REGISTRY.items():
            if schluessel not in vorhandene_prompts:
                db.add(
                    SystemPrompt(
                        schluessel=schluessel,
                        inhalt=eintrag["default"],
                        aktualisiert_von="system",
                    )
                )
                neue_prompts += 1
        if neue_prompts:
            logger.info("System-Prompts: %d neue Einträge angelegt.", neue_prompts)

        db.commit()
    except Exception:
        logger.exception("Seed fehlgeschlagen — Rollback.")
        db.rollback()
        raise
