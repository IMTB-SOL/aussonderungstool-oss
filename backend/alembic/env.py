"""Alembic-Environment für das Aussonderungstool.

Bindet das SQLAlchemy-MetaData unserer Modelle ein und liest die DATABASE_URL
aus .env — damit funktionieren `alembic upgrade head` und `alembic revision
--autogenerate` ohne weitere Konfiguration in alembic.ini.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Damit Alembic die Modelle findet, muss backend/ im sys.path liegen.
# env.py lebt unter backend/alembic/env.py — eine Ebene hoch reicht.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# .env laden, damit DATABASE_URL für lokales 'alembic upgrade head' greift.
# Im Container kommt die Variable aus der Pod-Spec — load_dotenv() ist dort no-op.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND_DIR.parent / ".env")

# Modelle importieren — Base.metadata enthält alle Tabellen-Definitionen.
from models import Base  # noqa: E402

config = context.config

# alembic.ini-Logging-Konfig aktivieren (auskommentiert in der Default-Config,
# wir verlassen uns auf das Anwendungs-Logging).
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        # logging-Sektion ist optional — wenn sie fehlt oder fehlerhaft ist,
        # läuft Alembic trotzdem mit Default-Loggern weiter.
        pass

# DATABASE_URL aus der Umgebung in die Alembic-Config injizieren.
# Sonst müsste die Connection-URL doppelt in alembic.ini gepflegt werden.
_db_url = os.getenv("DATABASE_URL", "sqlite:///./aussonderung.db")
config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Migrationen ohne DB-Verbindung — gibt SQL-Statements aus.

    Nützlich für Reviews und für Production-Deploys, bei denen ein DBA die
    SQL-Befehle gegen die DB ausführt.
    """
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Wichtig für SQLite: ALTER TABLE rendern via Batch-Modus, weil
        # SQLite kein vollwertiges ALTER TABLE kennt.
        render_as_batch=_db_url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Migrationen mit echter DB-Verbindung."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite-Batch-Modus auch online: ALTER TABLE/DROP COLUMN funktioniert
            # sonst nicht.
            render_as_batch=_db_url.startswith("sqlite"),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
