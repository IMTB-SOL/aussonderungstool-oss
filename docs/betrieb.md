# Installation und Betrieb

Diese Anleitung richtet sich an Stellen, die das Aussonderungstool selbst
betreiben. Alle Einstellungen sind in [`.env.example`](../.env.example)
beschrieben.

## 1. Sprachmodell bereitstellen

Die Schritte „Archivbewertung (vertiefte Prüfung)“ und „Erschließung“
brauchen ein Sprachmodell hinter einer OpenAI-kompatiblen Schnittstelle. Die
übrigen Schritte laufen ohne.

Beispiel mit [Ollama](https://ollama.com) auf demselben Rechner:

```bash
ollama pull qwen3:8b
```

`OLLAMA_BASE_URL` zeigt auf den Endpunkt (Standard
`http://localhost:11434/v1`), `OLLAMA_MODEL` nennt das Modell. Empfohlen ist
ein Modell mit großem Kontextfenster, weil ganze Vorgänge übergeben werden.
Jeder andere OpenAI-kompatible Dienst funktioniert ebenso.

Akteninhalte werden an diesen Endpunkt gesendet. Für vertrauliche Unterlagen
sollte das Modell im eigenen Netz laufen.

## 2. Betrieb als Container (ein Rechner)

```bash
cp .env.example .env      # Werte setzen, siehe unten
docker compose up --build -d
docker compose logs -f app
```

Pflichtangaben in `.env` für den Produktivbetrieb:

| Variable | Wert |
|---|---|
| `APP_ENV` | `production` |
| `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD` | Erst-Admin (Passwort mind. 12 Zeichen) |
| `OLLAMA_MODEL` | Name des bereitgestellten Modells |

`docker-compose.yml` bindet Port 8000 nur an `127.0.0.1` und erwartet das
Sprachmodell auf dem Host. Für den Zugriff aus dem Netz einen Reverse-Proxy
mit TLS vorschalten (nginx, Caddy, Traefik) und an Port 8000 weiterleiten.
Für Live-Fortschritt (Server-Sent Events) im Proxy die Pufferung abschalten
und die Upload-Größe auf `MAX_UPLOAD_SIZE_MB` anheben.

In Produktion setzen Cookies das Secure-Flag. Ein Test über `http://` von
einem anderen Rechner aus funktioniert deshalb nicht — entweder TLS nutzen
oder zum Ausprobieren `COOKIE_SECURE=false` setzen.

## 3. Betrieb auf OpenShift/Kubernetes

Das Helm-Chart liegt unter `deploy/helm/aussonderungstool/`; die
einzustellenden Werte nennt dessen
[README](../deploy/helm/aussonderungstool/README.md).

```bash
docker build -t <registry>/aussonderungstool:<version> .
docker push <registry>/aussonderungstool:<version>
kubectl apply -f mein-secret.yaml        # Vorlage: secret.example.yaml
helm install aussonderungstool deploy/helm/aussonderungstool \
  --set image.repository=<registry>/aussonderungstool \
  --set image.tag=<version> \
  --set route.host=<hostname>
```

Die Anwendung nutzt SQLite auf einem ReadWriteOnce-Volume. Deshalb läuft
genau eine Instanz (`replicas: 1`, Strategie `Recreate`); bei Updates ist die
Anwendung kurz nicht erreichbar.

## 4. Installation ohne Container

Voraussetzungen (Debian/Ubuntu):

```bash
sudo apt install python3 python3-venv nodejs npm default-jre-headless \
  libreoffice-nogui ghostscript tesseract-ocr tesseract-ocr-deu libmagic1
```

Einrichten und starten:

```bash
npm ci && npm run build                       # Oberfläche bauen

# veraPDF-Aufruf um das mitgelieferte JAR
printf '#!/bin/sh\nexec java -cp %s/vendor/verapdf/greenfield-apps-1.28.2.jar org.verapdf.apps.GreenfieldCliWrapper "$@"\n' "$PWD" > verapdf
chmod +x verapdf

cd backend
python3 -m venv venv && . venv/bin/activate
pip install --require-hashes -r requirements.txt
cp ../.env.example .env                       # anpassen; VERAPDF_PATH=<repo>/verapdf
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Die Anwendung muss mit genau einem Worker laufen: Laufende Prüfungen und
Live-Fortschritt werden im Prozess gehalten.

## 5. Nutzer und Anmeldung

- `AUTH_MODUS=passwort`: Anmeldung mit E-Mail und Passwort. Der Erst-Admin
  entsteht beim ersten Start aus `INITIAL_ADMIN_*`. Weitere Personen
  registrieren sich selbst und werden im Admin-Bereich freigeschaltet.
- `AUTH_MODUS=azure` oder `beide`: Anmeldung über Microsoft Entra ID. Dazu
  eine App-Registrierung anlegen (Redirect-URI
  `https://<host>/api/auth/callback`) und `AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET` setzen.
- `AUTH_MODUS=disabled`: keine Anmeldung; jeder Aufrufer hat Adminrechte.
  Nur für lokale Entwicklung oder hinter einem Dienst, der den Zugriff
  bereits absichert.

Alle freigeschalteten Nutzer arbeiten in einem gemeinsamen Bereich und sehen
alle Vorgänge.

## 6. Impressum und Datenschutz

`/impressum` und `/datenschutz` sind ohne Anmeldung erreichbar. Die Inhalte
stellt der Betreiber als HTML-Fragmente bereit:

```env
IMPRESSUM_DATEI=/app/data/impressum.html
DATENSCHUTZ_DATEI=/app/data/datenschutz.html
```

## 7. Daten, Sicherung, Löschung

Alles Veränderliche liegt in `DATA_DIR` (Container: `/app/data`): die
SQLite-Datenbank und je Vorgang ein Verzeichnis mit dem hochgeladenen Paket
und den Arbeitsständen.

- **Sicherung:** Anwendung anhalten oder einen Volume-Snapshot nutzen und
  `DATA_DIR` vollständig sichern.
- **Löschung:** Vorgänge werden nach `JOB_RETENTION_TAGE` Tagen automatisch
  entfernt (Standard 30).
- **Speicherbedarf:** je Vorgang ein Mehrfaches der Paketgröße.

## 8. Überwachung

| Endpunkt | Zweck |
|---|---|
| `/health` | Anwendung läuft |
| `/ready` | Datenbank und veraPDF erreichbar |
| `/metrics` | Prometheus-Metriken; nur mit `Authorization: Bearer <METRICS_TOKEN>` |

Protokolle gehen als JSON auf die Standardausgabe. Dateinamen werden darin
gekürzt und mit einem Hash versehen, weil sie Personennamen enthalten können.

## 9. Updates

```bash
git pull
docker compose up --build -d
```

Das Datenbankschema wird beim Start ergänzt. Vor einem Update `DATA_DIR`
sichern. Das Image regelmäßig neu bauen, auch ohne Codeänderung, damit die
Systempakete Sicherheitskorrekturen erhalten.

## 10. Fehlersuche

| Beobachtung | Ursache und Abhilfe |
|---|---|
| Start bricht ab mit „SESSION_SECRET fehlt …“ | In Produktion ist ein eigener Schlüssel mit mind. 32 Zeichen Pflicht |
| Start bricht ab mit „Pflichtfeld fehlt“ | `VERAPDF_PATH` und `DATA_DIR` setzen |
| Anmeldung gelingt, danach wieder Login-Seite | Secure-Cookie über `http://` — TLS nutzen oder `COOKIE_SECURE=false` |
| Kein Erst-Admin vorhanden | Passwort zu kurz oder Beispielwert; Hinweis steht im Protokoll |
| Formatprüfung meldet Warnung statt Ergebnis | veraPDF nicht gefunden: `VERAPDF_PATH` und Java prüfen |
| Bewertung/Erschließung schlägt fehl | Sprachmodell nicht erreichbar oder Modellname falsch |
| Fortschritt bleibt stehen | Reverse-Proxy puffert Server-Sent Events |
