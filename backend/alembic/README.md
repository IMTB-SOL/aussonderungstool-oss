# Datenbank-Migrationen mit Alembic

Schema-Änderungen werden ab jetzt über Alembic versioniert. Die historische
`models._migriere_fehlende_spalten`-Auto-Migration lebt zur Übergangszeit
weiter — sie wird entfernt, sobald alle Deployments auf Alembic umgestellt
sind.

## Befehle

```bash
cd backend

# Aktuellen Migrations-Stand der DB anzeigen
alembic current

# Auf den neuesten Stand migrieren (idempotent — bei aktueller DB no-op)
alembic upgrade head

# Eine Migration zurück
alembic downgrade -1

# Neue Migration nach Schema-Änderung in models.py erzeugen (auto-detect)
alembic revision --autogenerate -m "kurze_beschreibung"

# Migration als SQL-Skript ausgeben (für Reviews / DBA-Deploys)
alembic upgrade head --sql > /tmp/migration.sql
```

Nach einem `--autogenerate`-Lauf die generierte Datei in `versions/`
**immer durchlesen** — Alembic erkennt nicht alle Änderungen (z. B.
Server-Defaults, Enum-Werte) korrekt.

## Bestehende DBs migrieren (einmaliger Bootstrap)

Wer eine bestehende DB hat, die schon mit `Base.metadata.create_all` +
`_migriere_fehlende_spalten` aufgesetzt wurde, soll die DB einmalig als
"auf dem aktuellen Stand" markieren — **ohne** dass Alembic versucht,
die Tabellen erneut anzulegen:

```bash
cd backend
alembic stamp head
```

`alembic stamp head` schreibt nur in die neue `alembic_version`-Tabelle
und führt **keine DDL** aus.

## SQLite-Spezifika

`alembic/env.py` aktiviert automatisch `render_as_batch=True` für SQLite,
weil SQLite kein vollwertiges `ALTER TABLE` kann. Damit funktionieren
`drop column`/`alter type` über die Standard-Alembic-Operationen.

Beim Wechsel auf Postgres entfällt der Batch-Modus automatisch — die
URL-Erkennung in `env.py` reicht.

## Wo liegt was

```
backend/
├── alembic.ini                       # Alembic-Konfiguration (sqlalchemy.url wird zur Laufzeit überschrieben)
├── alembic/
│   ├── env.py                         # Lädt Modelle + DATABASE_URL aus .env
│   ├── script.py.mako                 # Template für neue Migrations-Files
│   ├── README.md                      # Diese Datei
│   └── versions/
│       └── <hash>_initial_schema.py   # Vollständiges Initial-Schema (8 Tabellen + Indizes)
```
