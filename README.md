# Aussonderungstool

Werkzeug zur Prüfung, Bewertung und Erschließung digitaler Akten im
XDOMEA-Format — für Archive und abgebende Stellen der öffentlichen Verwaltung.

Das Tool nimmt ein XDOMEA-Aussonderungspaket (ZIP) entgegen und führt
Archivarinnen und Archivare in fünf Schritten durch die Prüfung. Jeder Schritt
wird bestätigt, bevor der nächste beginnt; die Entscheidung über die
Archivwürdigkeit trifft immer ein Mensch.

Lizenz: [EUPL-1.2](./LICENSE)

## Funktionsumfang

| Schritt | Was geschieht | Sprachmodell |
|---|---|---|
| 1. Formale Prüfung | XDOMEA-Version erkennen, gegen die XSD-Schemata validieren, referenzierte Dateien auf Vollständigkeit prüfen, reparierbare Fehler beheben | nein |
| 2. Formatprüfung | Dateitypen bestimmen, PDF/A-Konformität mit veraPDF prüfen, auf Wunsch nach PDF/A konvertieren (PDF, Office-Dokumente, Bilder) | nein |
| 3. Inhaltsvergleich | DOCX- und PDF-Fassung desselben Dokuments vergleichen (Seitenzahl, Textähnlichkeit, Bilder, Datumsangaben) — deterministisch | nein |
| 4. Archivbewertung | Regelbasierte Indizien mit Punktwerten je Vorgang (Vorschlag A/B/V), optional vertiefte Prüfung per Sprachmodell | optional |
| 5. Erschließung | Titel, Inhaltsangabe, Schlagworte, Zeitraum u. a. vorschlagen; nach Bearbeitung ins XDOMEA-XML zurückschreiben | ja |

Dazu: Protokoll je Schritt, Rückfragen per Chat, Prüfbericht, Historie,
Audit-Log der Entscheidungen, automatische Löschung nach Aufbewahrungsfrist,
Verwaltungsoberfläche für Schwellwerte, Indizien, Behördenprofile und Prompts.

Unterstützte XDOMEA-Versionen: 2.1, 2.2, 2.3, 2.4, 3.0.

Der mitgelieferte Indizienkatalog und die Schwellwerte (A ab 70, V bis 30
Punkte) sind ein **Beispiel**. Sie sind in der Verwaltungsoberfläche änderbar
und müssen an das Bewertungsmodell des eigenen Archivs angepasst werden.

## Architektur

```
Browser ──HTTPS──▶ Reverse-Proxy/Route ──▶ FastAPI-Anwendung (ein Container)
                                            ├─ SQLite + Arbeitsdateien (Volume)
                                            ├─ veraPDF, LibreOffice, Ghostscript, Tesseract (lokale Prozesse)
                                            └─▶ Sprachmodell über OpenAI-kompatible API (z. B. Ollama)
```

- **Backend:** Python 3.12, FastAPI, SQLAlchemy, SQLite
- **Frontend:** React, mit esbuild vorab gebaut (kein Build im Browser)
- **Sprachmodell:** jede OpenAI-kompatible Schnittstelle; Standard ist eine
  lokale Ollama-Instanz, sodass keine Akteninhalte das eigene Netz verlassen
- **Betrieb:** ein Container, eine Instanz (SQLite); Helm-Chart für
  OpenShift/Kubernetes liegt bei

Details: [docs/architektur.md](./docs/architektur.md)

## Voraussetzungen

- Docker oder Podman (für den Betrieb als Container), **oder** für die
  Installation ohne Container: Python 3.12, Node.js 22, Java 17+,
  LibreOffice, Ghostscript, Tesseract (mit deutschem Sprachpaket), libmagic
- ein Sprachmodell hinter einer OpenAI-kompatiblen API, z. B.
  [Ollama](https://ollama.com) — nur für die Schritte 4 (vertiefte Prüfung)
  und 5 nötig
- etwa 4 GB RAM für die Anwendung; der Bedarf des Sprachmodells kommt hinzu

## Installation (Container)

```bash
git clone https://github.com/IMTB-SOL/aussonderungstool-oss.git
cd aussonderungstool-oss
cp .env.example .env
```

In `.env` mindestens setzen:

```env
APP_ENV=production
SESSION_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
INITIAL_ADMIN_EMAIL=<ihre Adresse>
INITIAL_ADMIN_PASSWORD=<mindestens 12 Zeichen>
OLLAMA_MODEL=<Name des installierten Modells>
```

Starten:

```bash
docker compose up --build -d
```

Die Anwendung läuft danach unter http://localhost:8000. Melden Sie sich mit
dem Erst-Admin an. Der Port ist nur lokal gebunden; für den Zugriff aus dem
Netz gehört ein Reverse-Proxy mit TLS davor.

Installation ohne Container, Kubernetes/OpenShift, Datensicherung und
Updates: [docs/betrieb.md](./docs/betrieb.md)

## Konfiguration

Alle Einstellungen erfolgen über Umgebungsvariablen. Die vollständige,
kommentierte Liste steht in [`.env.example`](./.env.example). Die wichtigsten:

| Variable | Bedeutung |
|---|---|
| `APP_ENV` | `production` oder `development` |
| `AUTH_MODUS` | `passwort`, `azure`, `beide` oder `disabled`; ohne Angabe ist die Anmeldung aktiv |
| `SESSION_SECRET` | Schlüssel für Session-Cookies, in Produktion Pflicht (mind. 32 Zeichen) |
| `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD` | Erst-Admin beim ersten Start |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Sprachmodell-Endpunkt und Modellname |
| `DATA_DIR`, `DATABASE_URL` | Ablage der Arbeitsdateien und der Datenbank |
| `JOB_RETENTION_TAGE` | automatische Löschung abgeschlossener Vorgänge |
| `IMPRESSUM_DATEI`, `DATENSCHUTZ_DATEI` | Impressum und Datenschutzerklärung des Betreibers |

Der aktive Sprachmodell-Anbieter lässt sich zur Laufzeit in der
Verwaltungsoberfläche umschalten.

## Beispielanwendung

1. Anmelden und unter „Upload“ ein XDOMEA-Paket hochladen. Zum Ausprobieren
   liegt ein synthetisches Paket unter `backend/tests/fixtures/gueltig.zip`.
2. Die Prüfschritte nacheinander starten, das jeweilige Protokoll ansehen und
   bestätigen. Schritte können übersprungen werden.
3. In der Archivbewertung je Vorgang den Vorschlag übernehmen oder mit
   Begründung ändern.
4. Erschließungsangaben prüfen und bearbeiten.
5. Das Ergebnispaket (ZIP mit Protokollen und ergänztem XDOMEA-XML) und den
   Prüfbericht herunterladen.

## Tests

```bash
cd backend
pip install --require-hashes -r requirements-dev.txt
pytest -q
```

Die Tests benötigen weder ein Sprachmodell noch Netzwerkzugriff.
Entwicklungsumgebung, Lint und Sicherheitsprüfungen:
[docs/entwicklung.md](./docs/entwicklung.md)

## Betrieb und Sicherheit

- Das Tool verarbeitet Verwaltungsakten mit personenbezogenen Daten. Der
  Betreiber ist für Schutzbedarfsfeststellung, Zugriffskonzept und
  Datenschutz verantwortlich.
- Alle freigeschalteten Nutzer sehen alle Vorgänge (ein gemeinsamer
  Arbeitsbereich je Installation). Getrennte Mandanten brauchen getrennte
  Installationen.
- Sicherheitslücken bitte vertraulich melden: [SECURITY.md](./SECURITY.md)

## Mitwirken

Beiträge sind willkommen: [CONTRIBUTING.md](./CONTRIBUTING.md)

## Lizenz

Copyright (c) 2026 IMTB Solutions GmbH

Lizenziert unter der European Union Public Licence 1.2 (EUPL-1.2):
[LICENSE](./LICENSE) (englisch), [LIZENZ](./LIZENZ) (deutsch).
Komponenten Dritter und deren Lizenzen: [NOTICE](./NOTICE) und
[THIRD_PARTY_LICENSES.md](./THIRD_PARTY_LICENSES.md).
Eine Stückliste der Python-Abhängigkeiten (CycloneDX) liegt unter
[`sbom/backend.cdx.json`](./sbom/backend.cdx.json).
