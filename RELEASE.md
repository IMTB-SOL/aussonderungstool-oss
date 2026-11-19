# Freigabe zur Veröffentlichung

Dieses Dokument hält fest, was vor der Veröffentlichung des Repositorys
geprüft wurde. Stand: 19.11.2026.

## Rechtliche Freigabe

| Punkt | Ergebnis |
|---|---|
| Vertragslage | Es besteht kein Vertrag, der einer Veröffentlichung entgegensteht. |
| Rechteinhaber | IMTB Solutions GmbH |
| Freigabe der Geschäftsführung | erteilt am 19.11.2026 |
| Kundenbezug | keiner; eine Zustimmung Dritter ist nicht erforderlich |

## Inhalt

| Punkt | Ergebnis |
|---|---|
| Geheimnisse im aktuellen Stand | Secret-Scan (gitleaks) ohne echten Fund; drei Treffer sind Test- und Platzhalterwerte |
| `.env` von der Versionsverwaltung ausgeschlossen | ja (`.gitignore`) |
| Testdaten | ohne echte Personen, Organisationen oder Aktenzeichen |
| Interne Hostnamen und Adressen | keine; Suche nach internen Domains und privaten IP-Bereichen ohne Fund |

## Git-Historie

Das Repository beginnt mit einer neuen Historie. Die frühere Entwicklung
ist nicht enthalten. Der Secret-Scan lief über alle Commits.

## Lizenz

| Punkt | Ergebnis |
|---|---|
| `LICENSE` | unveränderter Text der EUPL-1.2 |
| Abhängigkeiten | in [`THIRD_PARTY_LICENSES.md`](./THIRD_PARTY_LICENSES.md) mit Lizenz aufgeführt; Stückliste unter `sbom/` |
| SPDX-Angaben | für alle Dateien vorhanden, `reuse lint` ohne Beanstandung |
| Copyright | IMTB Solutions GmbH |

## Commit-Identität

Alle Commits tragen eine dienstliche Adresse. Eine `.mailmap` ist deshalb
nicht nötig.

## Dokumentation

`README.md`, `CONTRIBUTING.md` (mit DCO), `SECURITY.md` und `CHANGELOG.md`
liegen vor. Sicherheitsmeldungen laufen über GitHub Security Advisories;
eine E-Mail-Adresse wird bewusst nicht genannt.

## Technische Prüfung

| Prüfung | Ergebnis |
|---|---|
| Tests (pytest) | 327 bestanden, ohne `.env` und ohne Netzwerk |
| Lint und Format (ruff), statische Analyse (bandit) | ohne Befund |
| Abhängigkeiten (pip-audit, osv-scanner, npm audit) | ohne bekannte Schwachstelle |
| Image-Scan (trivy) | keine Befunde der Stufen HIGH oder CRITICAL |
| Helm-Chart (`helm lint`) | ohne Fehler |
| Container | Build und Start geprüft; läuft ohne Root-Rechte |

## Offene Punkte

- **xdomea-Schemata:** Die XSD-Dateien unter `backend/assets/xsd/` stammen
  nicht von IMTB und tragen keinen Lizenzvermerk. Sie sind gesondert
  gekennzeichnet und fallen nicht unter die EUPL-1.2.
- **CI-Pipeline:** `.github/workflows/ci.yml` ist auf öffentliche Runner
  ausgelegt, aber in diesem Repository noch nicht gelaufen.
- **Basis-Images** sind per Tag festgelegt, nicht per Digest.
