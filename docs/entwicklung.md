# Entwicklung

## Umgebung einrichten

Benötigt werden Python 3.12, Node.js 22, Java 17+ sowie LibreOffice,
Ghostscript, Tesseract und libmagic (Paketnamen siehe
[betrieb.md](./betrieb.md), Abschnitt 4; unter macOS per Homebrew:
`brew install ghostscript tesseract tesseract-lang libmagic` und
`brew install --cask libreoffice`).

```bash
# Oberfläche bauen (bei Änderungen an frontend/tool/*.jsx erneut)
npm ci
npm run build

# veraPDF-Aufruf um das mitgelieferte JAR
printf '#!/bin/sh\nexec java -cp %s/vendor/verapdf/greenfield-apps-1.28.2.jar org.verapdf.apps.GreenfieldCliWrapper "$@"\n' "$PWD" > verapdf
chmod +x verapdf

cd backend
python3 -m venv venv && . venv/bin/activate
pip install --require-hashes -r requirements-dev.txt
cp ../.env.example .env
```

In `backend/.env` für die lokale Arbeit: `VERAPDF_PATH=<repo>/verapdf` und
entweder `AUTH_MODUS=disabled` oder einen Erst-Admin setzen.

```bash
uvicorn main:app --reload
```

## Prüfungen vor einem Pull Request

```bash
cd backend
pytest -q                                   # Tests
ruff check . && ruff format --check .       # Lint und Format
bandit -r . -x ./venv,./tests --severity-level medium
pip-audit --strict --require-hashes -r requirements.txt
cd .. && npm run build && reuse lint
```

Dieselben Prüfungen laufen bei jedem Pull Request über GitHub Actions
(`.github/workflows/ci.yml`). Die Actions sind dort auf Commit-SHAs
festgeschrieben; der Kommentar hinter dem SHA nennt die zugehörige Version.

Die Tests brauchen kein Sprachmodell und kein Netzwerk; sie nutzen eine
temporäre Datenbank. Ohne erreichbares veraPDF melden die Formatprüfungen
eine Warnung statt eines Ergebnisses.

Einen Prüfschritt einzeln ausführen:

```bash
python agents/agent_formal.py --job-id <job_id>
```

## Konventionen

- Kommentare, Protokollmeldungen, Fehlermeldungen und Oberflächentexte auf
  Deutsch.
- Typangaben in Python; lange Operationen asynchron oder in einem
  Prozess-/Thread-Pool.
- Kein `print`, sondern `logging`. Dateinamen vor dem Protokollieren durch
  `pseudonymisiere_dateiname()` führen.
- Dateien, deren Name aus einer Anfrage, einem XML oder einem ZIP stammt,
  mit `utils.job_dateien.finde_datei(en)` suchen — nicht mit `rglob(name)`.
- XML nur mit `utils.xdomea_parser.safe_xml_parser()` einlesen.
- Externe Programme mit Argumentliste starten und über
  `utils.prozess.kommuniziere_mit_timeout()` begrenzen.
- Prompts liegen zentral in `utils/openai_client.py`.
- Testdaten sind synthetisch. Keine echten Akten, Namen oder Aktenzeichen.

## Abhängigkeiten

Direkte Abhängigkeiten stehen in `backend/requirements.in` bzw.
`requirements-dev.in`. Daraus entstehen die festgeschriebenen Dateien mit
allen transitiven Paketen und Hashes:

```bash
uv pip compile backend/requirements.in --universal --python-version 3.12 \
  --generate-hashes --upgrade -o backend/requirements.txt
uv pip compile backend/requirements-dev.in --universal --python-version 3.12 \
  --generate-hashes --upgrade -o backend/requirements-dev.txt
```

Danach Tests und `pip-audit` ausführen.

## Lizenzen und SBOM

Nach Änderungen an den Abhängigkeiten die Übersicht und die Stückliste
erneuern:

```bash
pip install pip-licenses cyclonedx-bom
pip-licenses --format=markdown --with-urls      # Grundlage für THIRD_PARTY_LICENSES.md, Abschnitt 2
cyclonedx-py requirements backend/requirements.txt --of JSON -o sbom/backend.cdx.json
reuse lint
```

Neue Fremddateien im Repository brauchen einen Eintrag in `REUSE.toml`, den
Lizenztext unter `LICENSES/` und eine Zeile in `THIRD_PARTY_LICENSES.md`.

## Container bauen

```bash
docker build -t aussonderungstool:lokal .
```

Der Build installiert die Python-Pakete mit `--require-hashes` und die
Frontend-Pakete mit `npm ci`; veraPDF kommt aus `vendor/`. Die Basis-Images
sind per Tag angegeben — für einen bitgenau wiederholbaren Build das
Basis-Image über `--build-arg BASE_IMAGE=debian@sha256:…` festlegen.
