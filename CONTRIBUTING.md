# Mitwirken

Beiträge sind willkommen — Fehlerberichte, Verbesserungsvorschläge und
Pull Requests.

## Ablauf

1. Bei größeren Änderungen zuerst ein Issue eröffnen, damit Richtung und
   Aufwand geklärt sind.
2. Einen Branch von `main` abzweigen (`feature/<thema>`, `fix/<thema>`,
   `docs/<thema>`).
3. Änderung umsetzen und Tests ergänzen. Vor dem Pull Request müssen Tests
   und Lint lokal durchlaufen — siehe [docs/entwicklung.md](./docs/entwicklung.md).
4. Pull Request gegen `main` stellen und beschreiben, **was** sich ändert,
   **warum** und **wie** es geprüft wurde.

Kommentare, Meldungen und Oberflächentexte sind auf Deutsch; technische
Bezeichner dürfen englisch sein.

## Lizenz der Beiträge und Herkunftsnachweis (DCO)

Beiträge werden unter der EUPL-1.2 eingebracht, derselben Lizenz wie das
Projekt. Ein Contributor License Agreement gibt es nicht. Stattdessen
bestätigen Sie mit einer `Signed-off-by:`-Zeile in jedem Commit das
[Developer Certificate of Origin 1.1](https://developercertificate.org/):
dass Sie den Beitrag selbst erstellt haben oder ihn unter dieser Lizenz
einreichen dürfen — auch gegenüber Ihrem Arbeitgeber.

```bash
git commit -s -m "fix(format): Dateiendung bei PDF/A-Prüfung berücksichtigen"
```

Pull Requests ohne diese Zeile können nicht übernommen werden.

## Keine vertraulichen Inhalte

Bitte keine echten Akten, personenbezogenen Daten, Zugangsdaten oder
internen Adressen in Issues, Pull Requests oder Testdaten. Testdaten müssen
synthetisch sein.

## Sicherheitslücken

Nicht öffentlich melden — siehe [SECURITY.md](./SECURITY.md).
