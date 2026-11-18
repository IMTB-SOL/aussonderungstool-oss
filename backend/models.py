"""SQLAlchemy-Datenbankmodelle für das Aussonderungstool."""

import enum
import os

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from utils.zeit import jetzt_utc

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aussonderung.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    ausstehend = "ausstehend"
    laufend = "laufend"
    abgeschlossen = "abgeschlossen"
    fehler = "fehler"


class AgentStatus(str, enum.Enum):
    ausstehend = "ausstehend"
    laufend = "laufend"
    wartet = "wartet"  # Agent fertig, wartet auf Bestätigung des Archivars
    ok = "ok"  # Vom Archivar bestätigt
    fehler = "fehler"
    uebersprungen = "uebersprungen"


class Bewertung(str, enum.Enum):
    archivwuerdig = "archivwürdig"
    nicht_archivwuerdig = "nicht archivwürdig"
    manuelle_ueberpruefung = "manuelle Überprüfung"


class IndizTyp(str, enum.Enum):
    boost = "boost"
    malus = "malus"
    ko_deckel = "ko_deckel"
    ok_boden = "ok_boden"


class HierarchieStufe(str, enum.Enum):
    ministerium = "ministerium"
    oberbehoerde = "oberbehoerde"
    mittelbehoerde = "mittelbehoerde"
    unterbehoerde = "unterbehoerde"


class Job(Base):
    """Repräsentiert einen Aussonderungsauftrag (hochgeladenes ZIP)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_erstellt_am", "erstellt_am"),
        Index("ix_jobs_status", "status"),
    )

    id = Column(String, primary_key=True)
    dateiname = Column(String, nullable=False)
    erstellt_am = Column(DateTime, default=jetzt_utc)
    status = Column(Enum(JobStatus), default=JobStatus.ausstehend)

    # Agentenstatusfelder
    status_formal = Column(Enum(AgentStatus), default=AgentStatus.ausstehend)
    status_format = Column(Enum(AgentStatus), default=AgentStatus.ausstehend)
    status_inhalt = Column(Enum(AgentStatus), default=AgentStatus.ausstehend)
    status_bewertung = Column(Enum(AgentStatus), default=AgentStatus.ausstehend)
    status_erschliessung = Column(Enum(AgentStatus), default=AgentStatus.ausstehend)

    # Ergebnisse (JSON als Text gespeichert)
    ergebnis_formal = Column(Text, nullable=True)
    ergebnis_format = Column(Text, nullable=True)
    ergebnis_inhalt = Column(Text, nullable=True)
    ergebnis_bewertung = Column(Text, nullable=True)
    ergebnis_erschliessung = Column(Text, nullable=True)

    # Archivar-Entscheidung (Human in the Loop)
    archivar_bewertung = Column(Enum(Bewertung), nullable=True)
    archivar_begruendung = Column(
        Text, nullable=True
    )  # Editierte Begründung (KI-Vorlage + Archivar-Änderungen)
    archivar_kommentar = Column(Text, nullable=True)
    entscheidung_am = Column(DateTime, nullable=True)

    # Chat-Verlauf pro Agent (JSON-Array von {role, content})
    chat_formal = Column(Text, nullable=True)
    chat_format = Column(Text, nullable=True)
    chat_inhalt = Column(Text, nullable=True)
    chat_bewertung = Column(Text, nullable=True)
    chat_erschliessung = Column(Text, nullable=True)

    # Archivar-Anmerkungen pro Agent
    anmerkung_formal = Column(Text, nullable=True)
    anmerkung_format = Column(Text, nullable=True)
    anmerkung_inhalt = Column(Text, nullable=True)
    anmerkung_bewertung = Column(Text, nullable=True)
    anmerkung_erschliessung = Column(Text, nullable=True)

    # Aktueller Schritt im Wizard (0-4)
    aktueller_schritt = Column(String, default="formal")

    # Pfad zum aktuell aktiven ZIP (wird nach jedem Agenten aktualisiert)
    aktuelles_zip = Column(String, nullable=True)

    # Pfad zum ursprünglich hochgeladenen ZIP. Wird nie überschrieben und
    # nicht gelöscht — nur damit lässt sich ein Vorgang zurücksetzen.
    # NULL bei Jobs, die vor Einführung dieser Spalte angelegt wurden; deren
    # Original ist bereits durch eine Reparatur ersetzt worden.
    original_zip = Column(String, nullable=True)

    # Gewählte Agenten (JSON-Array, z.B. '["formal","format","bewertung"]')
    gewaehlte_agenten = Column(Text, nullable=True)

    # Audit-Log: JSON-Array von Aktionen [{zeitpunkt, aktion, details}]
    audit_log = Column(Text, nullable=True)


class BehoerdenProfil(Base):
    """Stammdaten einer Behörde für die Archivbewertung."""

    __tablename__ = "behoerden_profil"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    kuerzel = Column(String, nullable=True)
    hierarchie_stufe = Column(Enum(HierarchieStufe), nullable=False)
    besondere_oe = Column(JSON, nullable=False, default=list)
    aktenzeichen_praefix = Column(String, nullable=True, index=True)
    erstellt_am = Column(DateTime, default=jetzt_utc)


class BewertungSchwellwerte(Base):
    """Globale Schwellwerte für A/B/V-Klassifikation (Singleton)."""

    __tablename__ = "bewertung_schwellwerte"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nur_eins = Column(String, nullable=False, default="global", unique=True)
    schwelle_a = Column(Integer, nullable=False, default=70)
    schwelle_v = Column(Integer, nullable=False, default=30)
    aktualisiert_am = Column(DateTime, default=jetzt_utc, onupdate=jetzt_utc)
    aktualisiert_von = Column(String, nullable=True)


class LLMEinstellung(Base):
    """Aktiver LLM-Provider — über den Admin-Bereich umschaltbar (Singleton)."""

    __tablename__ = "llm_einstellung"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nur_eins = Column(String, nullable=False, default="global", unique=True)
    aktiver_provider = Column(String, nullable=False, default="ollama")
    scaleway_modell = Column(String, nullable=True)
    aktualisiert_am = Column(DateTime, default=jetzt_utc, onupdate=jetzt_utc)
    aktualisiert_von = Column(String, nullable=True)


class SystemPrompt(Base):
    """Editierbarer LLM-System-Prompt (über den Admin-Bereich pflegbar)."""

    __tablename__ = "system_prompt"
    __table_args__ = (UniqueConstraint("schluessel", name="uq_system_prompt_schluessel"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    schluessel = Column(String, nullable=False)
    inhalt = Column(Text, nullable=False)
    aktualisiert_am = Column(DateTime, default=jetzt_utc, onupdate=jetzt_utc)
    aktualisiert_von = Column(String, nullable=True)


class IndizKatalog(Base):
    """Katalog der bewertbaren Indizien mit Punktwerten."""

    __tablename__ = "indiz_katalog"
    __table_args__ = (UniqueConstraint("schluessel", name="uq_indiz_schluessel"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    schluessel = Column(String, nullable=False)
    bezeichnung = Column(String, nullable=False)
    punkte = Column(Integer, nullable=False, default=0)
    typ = Column(Enum(IndizTyp), nullable=False)
    beschreibung = Column(Text, nullable=True)
    aktiv = Column(Boolean, nullable=False, default=True)


class User(Base):
    """Anwendungsnutzer für die E-Mail/Passwort-Anmeldung."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, nullable=False, index=True)  # kleingeschrieben gespeichert
    password_hash = Column(String, nullable=True)
    salt = Column(String, nullable=True)
    name = Column(String, nullable=False)
    approved = Column(Boolean, nullable=False, default=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    provider = Column(String, nullable=False, default="passwort")  # "passwort" | "azure"
    erstellt_am = Column(DateTime, default=jetzt_utc)


class VorgangBewertung(Base):
    """Bewertung eines einzelnen Vorgangs aus einem XDOMEA-Paket."""

    __tablename__ = "vorgang_bewertung"
    __table_args__ = (
        UniqueConstraint("job_id", "vorgang_id", name="uq_vorgang_bewertung_job_vorgang"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    vorgang_id = Column(String, nullable=False)
    aktenzeichen = Column(String, nullable=True, index=True)
    betreff = Column(Text, nullable=True)
    rohscore = Column(Integer, nullable=False, default=0)
    finaler_score = Column(Integer, nullable=False, default=0)
    klassifikation_vorschlag = Column(String, nullable=False)  # A|B|V
    klassifikation_final = Column(String, nullable=True)  # A|B|V
    llm_begruendung = Column(Text, nullable=True)
    schlagworte = Column(JSON, nullable=False, default=list)
    override_begruendung = Column(Text, nullable=True)
    tiefenpruefung_gemacht = Column(Boolean, nullable=False, default=False)
    entschieden_am = Column(DateTime, nullable=True)
    entschieden_von = Column(String, nullable=True)
    erstellt_am = Column(DateTime, default=jetzt_utc)

    treffer = relationship(
        "VorgangIndizTreffer", backref="vorgang_bewertung", cascade="all, delete-orphan"
    )
    dokumente = relationship(
        "DokumentBewertung", backref="vorgang_bewertung", cascade="all, delete-orphan"
    )


class VorgangIndizTreffer(Base):
    """Welches Indiz hat bei einem Vorgang gezündet — Audit-Trail."""

    __tablename__ = "vorgang_indiz_treffer"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vorgang_bewertung_id = Column(
        Integer, ForeignKey("vorgang_bewertung.id"), nullable=False, index=True
    )
    indiz_schluessel = Column(String, nullable=False)
    erfuellt = Column(Boolean, nullable=False)
    punkte_beitrag = Column(Integer, nullable=False, default=0)
    evidenz = Column(Text, nullable=True)


class DokumentBewertung(Base):
    """Einzeldokument-Bewertung aus der Tiefenprüfung."""

    __tablename__ = "dokument_bewertung"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vorgang_bewertung_id = Column(
        Integer, ForeignKey("vorgang_bewertung.id"), nullable=False, index=True
    )
    dateiname = Column(String, nullable=False)
    bewertung = Column(Enum(Bewertung), nullable=False)
    begruendung = Column(Text, nullable=True)
    erstellt_am = Column(DateTime, default=jetzt_utc)


def get_db():
    """Datenbankverbindung als Dependency für FastAPI."""
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Erstellt alle Tabellen, falls noch nicht vorhanden, und migriert fehlende Spalten."""
    Base.metadata.create_all(bind=engine)
    _migriere_fehlende_spalten()


def _migriere_fehlende_spalten() -> None:
    """Fügt fehlende Spalten zur bestehenden DB hinzu (einfache Auto-Migration)."""
    import logging
    import sqlite3

    logger = logging.getLogger(__name__)
    db_pfad = DATABASE_URL.replace("sqlite:///", "")

    try:
        conn = sqlite3.connect(db_pfad)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(jobs)")
        vorhandene = {row[1] for row in cursor.fetchall()}

        migrationen = {
            "archivar_begruendung": "ALTER TABLE jobs ADD COLUMN archivar_begruendung TEXT",
            "gewaehlte_agenten": "ALTER TABLE jobs ADD COLUMN gewaehlte_agenten TEXT",
            "audit_log": "ALTER TABLE jobs ADD COLUMN audit_log TEXT",
            # Bleibt bei Bestandsjobs bewusst NULL: Deren Original wurde beim
            # ersten Reparaturschritt gelöscht, der aktuelle Stand ist nicht
            # der Ursprungszustand. Zurücksetzen ist dort nicht möglich.
            "original_zip": "ALTER TABLE jobs ADD COLUMN original_zip TEXT",
        }

        for spalte, sql in migrationen.items():
            if spalte not in vorhandene:
                cursor.execute(sql)
                logger.info("DB-Migration: Spalte '%s' hinzugefügt.", spalte)

        # llm_einstellung: scaleway_modell (Modellauswahl im Admin-Panel)
        cursor.execute("PRAGMA table_info(llm_einstellung)")
        vorhandene_llm = {row[1] for row in cursor.fetchall()}
        if "scaleway_modell" not in vorhandene_llm:
            cursor.execute("ALTER TABLE llm_einstellung ADD COLUMN scaleway_modell TEXT")
            logger.info("DB-Migration: Spalte 'scaleway_modell' hinzugefügt.")

        # users: provider (Dual-Auth). Bestands-DBs ohne Alembic nachziehen.
        # (Nur die Spalte; die Nullable-Änderung der Passwortfelder macht die
        # Alembic-Migration b2c3d4e5f6a7 — SQLite kann Spalten nicht ALTER'n.)
        cursor.execute("PRAGMA table_info(users)")
        vorhandene_users = {row[1] for row in cursor.fetchall()}
        if vorhandene_users and "provider" not in vorhandene_users:
            cursor.execute("ALTER TABLE users ADD COLUMN provider TEXT NOT NULL DEFAULT 'passwort'")
            logger.info("DB-Migration: Spalte 'provider' zu users hinzugefügt.")

        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("DB-Migration fehlgeschlagen: %s", e)
