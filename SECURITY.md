# Sicherheit

## Sicherheitslücken melden

Bitte melden Sie Sicherheitslücken **nicht öffentlich** (kein Issue, kein
Pull Request), sondern vertraulich über „Report a vulnerability“ im Reiter
*Security* dieses Repositorys (GitHub Security Advisories).

Hilfreich sind: betroffene Version oder Commit, betroffene Komponente,
Schritte zum Nachvollziehen, vermutete Auswirkung.

Wir bestätigen den Eingang in der Regel innerhalb von fünf Werktagen und
melden uns mit einer ersten Einschätzung. Bitte geben Sie uns bis zu 90 Tage
Zeit für eine Behebung, bevor Sie Einzelheiten veröffentlichen. Auf Wunsch
nennen wir Sie im Sicherheitshinweis.

Unterstützt wird jeweils die neueste veröffentlichte Version.

## Hinweise für Betreiber

- **Anmeldung:** Ohne `AUTH_MODUS` ist die Anmeldung aktiv. `AUTH_MODUS=disabled`
  nur verwenden, wenn ein vorgeschalteter Dienst den Zugriff absichert.
- **Geheimnisse:** `SESSION_SECRET` (mind. 32 Zeichen), Admin-Passwort,
  API-Schlüssel und `METRICS_TOKEN` gehören in ein Secret bzw. die `.env`,
  nie in die Versionsverwaltung. Beispielwerte werden in Produktion abgelehnt.
- **Transport:** nur hinter TLS betreiben. In Produktion tragen Cookies das
  Secure-Flag, und die Anwendung sendet HSTS.
- **Berechtigungen:** Es gibt zwei Rollen (Nutzer, Admin). Alle
  freigeschalteten Nutzer sehen und bearbeiten alle Vorgänge der
  Installation. Getrennte Mandanten brauchen getrennte Installationen.
- **Sprachmodell:** Akteninhalte werden an den konfigurierten Endpunkt
  gesendet. Für vertrauliche Unterlagen ein selbst betriebenes Modell nutzen.
- **Uploads:** ZIP-Pakete werden auf Größe, Anzahl der Einträge, Pfade und
  Verknüpfungen geprüft; XML wird ohne externe Entitäten und ohne
  Netzwerkzugriff verarbeitet.
- **Aktualität:** Abhängigkeiten sind mit Hashes festgeschrieben
  (`backend/requirements.txt`). Bitte regelmäßig aktualisieren und das Image
  neu bauen, damit auch die Systempakete Sicherheitskorrekturen erhalten.
