# Architektur

## Überblick

Das Aussonderungstool ist eine einzelne Webanwendung: ein FastAPI-Prozess
liefert die Oberfläche aus, nimmt Pakete entgegen und führt die Prüfschritte
aus. Zustand liegt in einer SQLite-Datenbank und im Dateisystem.

```
┌──────────┐  HTTPS   ┌───────────────┐        ┌─────────────────────────────┐
│ Browser  │ ───────▶ │ Reverse-Proxy │ ─────▶ │ FastAPI (uvicorn, 1 Worker) │
└──────────┘          └───────────────┘        │  Router · Prüfschritte      │
                                               └──────┬───────────┬──────────┘
                                 DATA_DIR (Volume)    │           │ HTTPS/HTTP
                              ┌───────────────────────▼─┐   ┌─────▼──────────────┐
                              │ SQLite · Pakete ·       │   │ Sprachmodell       │
                              │ Arbeitsverzeichnisse    │   │ (OpenAI-kompatibel)│
                              └─────────────────────────┘   └────────────────────┘
                 lokale Prozesse: veraPDF (Java) · LibreOffice · Ghostscript · Tesseract
```

## Verzeichnisse

| Pfad | Inhalt |
|---|---|
| `backend/main.py` | Anwendung, Middleware, Sicherheits-Header, Start/Stopp |
| `backend/config.py` | Konfiguration aus Umgebungsvariablen |
| `backend/models.py` | Datenmodell (SQLAlchemy) und Schema-Anlage |
| `backend/routers/` | HTTP-Schnittstelle: `auth`, `jobs` (Upload, Struktur, Vorschau, Chat, Download), `agenten` (Start, Reparatur, Konvertierung), `bewertung`, `admin`, `system`, `legal`, `frontend` |
| `backend/agents/` | Die fünf Prüfschritte; jeder auch einzeln per Kommandozeile aufrufbar |
| `backend/utils/` | XDOMEA-Parser, ZIP-Verarbeitung, Textextraktion, PDF/A-Konvertierung, LLM-Client, Anmeldung, Protokoll, Logging, Aufbewahrung |
| `backend/assets/xsd/` | XDOMEA-Schemata 2.1–3.0 |
| `backend/tests/` | Tests (pytest) mit synthetischen Paketen |
| `frontend/` | `app.html`, `login.html` und die React-Quellen unter `tool/` (Build nach `tool/build/`) |
| `deploy/helm/` | Helm-Chart |
| `vendor/verapdf/` | veraPDF als JAR, damit der Build keinen Download braucht |

## Ablauf einer Prüfung

1. **Upload** (`POST /api/upload`): Das ZIP wird in `DATA_DIR/jobs/<id>/`
   gespeichert und geprüft (Größe, Anzahl der Einträge, Pfade). Das Original
   bleibt für ein späteres Zurücksetzen erhalten.
2. **Prüfschritt starten** (`POST /api/job/<id>/run/<schritt>`): Der Schritt
   läuft als Hintergrundaufgabe im selben Prozess. Fortschritt wird über
   Server-Sent Events gemeldet (`GET /api/job/<id>/status`).
3. **Ergebnis und Protokoll:** Jeder Schritt schreibt sein Ergebnis in die
   Datenbank und ein Protokoll-XML in ein neu gepacktes ZIP.
4. **Bestätigung:** Der Mensch prüft, korrigiert bei Bedarf und bestätigt.
   Entscheidungen werden mit Zeitpunkt und Person im Audit-Log festgehalten.
5. **Download:** Das letzte ZIP enthält das ergänzte XDOMEA-XML und alle
   Protokolle.

## Die Prüfschritte

| Schritt | Modul | Vorgehen |
|---|---|---|
| Formale Prüfung | `agent_formal.py` | Version aus dem Namensraum, XSD-Validierung, Abgleich referenzierter Dateien, Reparatur von Kodierung und Schemaabweichungen; auch verschachtelte Pakete |
| Formatprüfung | `agent_format.py` | Dateityp per libmagic, PDF/A-Prüfung per veraPDF in Stapeln |
| Inhaltsvergleich | `agent_inhalt.py` | Paare DOCX/PDF finden und deterministisch vergleichen |
| Archivbewertung | `agent_bewertung.py` | Phase A: Indizien aus Metadaten mit Punktwerten → Vorschlag A/B/V. Phase B (optional): Sprachmodell liest die Dokumente eines Vorgangs und begründet einen Vorschlag |
| Erschließung | `agent_erschliessung.py` | Sprachmodell schlägt Erschließungsangaben vor; nach Bearbeitung Rückschreiben ins XML |

## Sprachmodell

Alle Aufrufe laufen über `backend/utils/openai_client.py`: ein Client für
OpenAI-kompatible Schnittstellen, Antworten als JSON, Kürzung langer Texte,
Wiederholungen bei Zeitüberschreitung. Die Prompts liegen dort und sind in
der Verwaltungsoberfläche überschreibbar. Ausgaben des Modells sind
Vorschläge und werden in der Oberfläche als Text dargestellt, nie als HTML.

## Sicherheit

- **Anmeldung:** signierte, zeitlich begrenzte Session-Cookies (HttpOnly);
  Passwörter mit PBKDF2-HMAC-SHA256 und eigenem Salt; optional Microsoft
  Entra ID. Ohne Konfiguration ist die Anmeldung aktiv.
- **Eingaben:** XML wird ohne externe Entitäten, ohne Netzwerkzugriff und
  ohne übergroße Bäume verarbeitet. ZIPs werden gegen Pfadausbruch,
  Verknüpfungen und Dekompressionsbomben geprüft. Dateinamen aus Anfragen
  und Paketen werden nur als exakte Namen gesucht.
- **Auslieferung:** Hochgeladene Dateien werden nur als PDF oder Rasterbild
  im Browser angezeigt, alles andere als Download. Content-Security-Policy
  und weitere Sicherheits-Header setzt die Anwendung selbst.
- **Externe Prozesse:** Aufruf mit Argumentlisten ohne Shell, mit
  Zeitbegrenzung.
- **Protokolle:** keine Inhalte, keine Zugangsdaten; Dateinamen gekürzt.
- **Container:** läuft ohne Root-Rechte, mit schreibgeschütztem
  Wurzeldateisystem im Helm-Chart.

## Bewusste Grenzen

- **Eine Instanz:** SQLite und im Prozess gehaltene Aufgaben erlauben keinen
  Parallelbetrieb mehrerer Instanzen.
- **Ein Arbeitsbereich:** keine Trennung der Vorgänge nach Nutzern oder
  Mandanten.
- **Kein Ersatz für die archivische Bewertung:** Regeln und Modell liefern
  Vorschläge; der Indizienkatalog ist ein anzupassendes Beispiel.
