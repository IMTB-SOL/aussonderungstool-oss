<!--
SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH

SPDX-License-Identifier: EUPL-1.2
-->

# Lizenzen der Drittkomponenten

Das Aussonderungstool selbst steht unter der EUPL-1.2 (siehe `LICENSE`).
Die hier aufgeführten Komponenten Dritter stehen unter ihren eigenen
Lizenzen. Diese Übersicht ist eine Momentaufnahme; maßgeblich sind die
Lizenzangaben der jeweiligen Pakete. Neu erzeugen: siehe
`docs/entwicklung.md`, Abschnitt „Lizenzen und SBOM“.

## 1. Im Repository enthaltene Fremddateien

| Komponente | Pfad | Lizenz | Quelle |
|---|---|---|---|
| veraPDF Greenfield 1.28.2 | `vendor/verapdf/greenfield-apps-1.28.2.jar` | MPL-2.0 (dual MPL-2.0 / GPL-3.0-or-later, hier MPL-2.0 gewählt) | https://github.com/veraPDF/veraPDF-apps |
| React, ReactDOM (Production-Builds) | `frontend/assets/vendor/react*.min.js` | MIT | https://github.com/facebook/react |
| Inter (Schrift) | `frontend/assets/vendor/Inter*.woff2`, `inter.css` | OFL-1.1 | https://github.com/rsms/inter |
| xdomea-XML-Schemata 2.1–3.0 inkl. eingebundener Codelisten (UN/CEFACT, ISO, IANA, XÖV) | `backend/assets/xsd/v*/` | keine Lizenzangabe in den Dateien — siehe Hinweis | https://www.xoev.de/xdomea |

**Hinweis zu den xdomea-Schemata:** Die Schemadateien werden unverändert
weitergegeben. Herausgeber ist die Arbeitsgruppe xdomea des IT-Planungsrats,
Betrieb durch die KoSIT. Die Dateien enthalten keinen Lizenzvermerk; sie
fallen **nicht** unter die EUPL-1.2 dieses Projekts. Wer das Projekt
weitergibt, sollte die aktuellen Nutzungsbedingungen des Herausgebers prüfen
oder die Schemata direkt von dort beziehen.

Die Volltexte der Lizenzen liegen unter `LICENSES/`.

## 2. Python-Abhängigkeiten (Laufzeit)

Werden beim Build aus PyPI installiert (`backend/requirements.txt`, mit
Hashes festgeschrieben) und sind nicht Teil des Repositorys.

| Paket | Version | Lizenz | Quelle |
|---|---|---|---|
| alembic | 1.20.1 | MIT | https://alembic.sqlalchemy.org |
| annotated-doc | 0.0.7 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.16.0 | MIT | https://anyio.readthedocs.io/en/stable/versionhistory.html |
| attrs | 26.1.0 | MIT | https://www.attrs.org/en/stable/changelog.html |
| certifi | 2026.10.23 | MPL-2.0 | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | https://cffi.readthedocs.io/en/latest/whatsnew.html |
| charset-normalizer | 3.5.2 | MIT | https://github.com/jawah/charset_normalizer/blob/master/CHANGELOG.md |
| click | 8.5.1 | BSD-3-Clause | https://github.com/pallets/click/ |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| distro | 1.9.0 | Apache-2.0 | https://github.com/python-distro/distro |
| fastapi | 0.164.0 | MIT | https://github.com/fastapi/fastapi |
| fonttools | 4.68.0 | MIT | http://github.com/fonttools/fonttools |
| fpdf2 | 2.9.0 | LGPL-3.0-only | https://py-pdf.github.io/fpdf2/ |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore/ |
| httptools | 0.9.0 | MIT | https://github.com/MagicStack/httptools |
| httpx | 0.28.1 | BSD (siehe Paket) | https://github.com/encode/httpx |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| img2pdf | 0.6.3 | LGPL-3.0 | https://pypi.org/project/img2pdf/ |
| itsdangerous | 2.2.0 | BSD (siehe Paket) | https://github.com/pallets/itsdangerous/ |
| jiter | 0.19.0 | MIT | https://github.com/pydantic/jiter/ |
| jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema |
| jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| lxml | 6.1.3 | BSD-3-Clause | https://lxml.de/ |
| Mako | 1.4.3 | MIT | https://www.makotemplates.org/ |
| markdown-it-py | 4.3.0 | MIT | https://github.com/executablebooks/markdown-it-py |
| MarkupSafe | 3.0.4 | BSD-3-Clause | https://github.com/pallets/markupsafe/ |
| mdurl | 0.1.2 | MIT | https://github.com/executablebooks/mdurl |
| msal | 1.39.0 | MIT | https://github.com/AzureAD/microsoft-authentication-library-for-python |
| ocrmypdf | 17.14.2 | MPL-2.0 | https://github.com/ocrmypdf/OCRmyPDF |
| openai | 1.109.1 | Apache-2.0 | https://github.com/openai/openai-python |
| opentelemetry-api | 1.45.2 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pdfminer.six | 20260107 | MIT | https://github.com/pdfminer/pdfminer.six |
| pdfplumber | 0.11.10 | MIT | https://github.com/jsvine/pdfplumber |
| pikepdf | 10.18.2 | MPL-2.0 | https://github.com/pikepdf/pikepdf |
| pillow | 13.0.0 | MIT-CMU | https://python-pillow.org |
| pluggy | 1.7.0 | MIT | https://github.com/pytest-dev/pluggy |
| prometheus-fastapi-instrumentator | 8.1.0 | ISC | https://github.com/trallnag/prometheus-fastapi-instrumentator |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | https://github.com/prometheus/client_python |
| pycparser | 3.12 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.14.3 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.4 | MIT | https://github.com/pydantic |
| Pygments | 2.21.0 | BSD-2-Clause | https://pygments.org |
| PyJWT | 2.15.1 | MIT | https://github.com/jpadilla/pyjwt |
| pypdf | 7.1.1 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| pypdfium2 | 5.15.2 | BSD-3-Clause, Apache-2.0, dependency licenses | https://github.com/pypdfium2-team/pypdfium2 |
| pytesseract | 0.3.13 | Apache-2.0 | https://github.com/madmaze/pytesseract |
| python-docx | 1.2.0 | MIT | https://github.com/python-openxml/python-docx |
| python-dotenv | 1.2.4 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| python-magic | 0.4.27 | MIT | http://github.com/ahupp/python-magic |
| python-multipart | 0.0.34 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| python-pptx | 0.6.23 | MIT | https://github.com/scanny/python-pptx |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| referencing | 0.37.0 | MIT | https://github.com/python-jsonschema/referencing |
| requests | 2.34.2 | Apache-2.0 | https://github.com/psf/requests |
| rich | 15.0.0 | MIT | https://github.com/Textualize/rich |
| rpds-py | 2026.9.1 | MIT | https://github.com/crate-py/rpds |
| sniffio | 1.3.1 | Apache-2.0 OR MIT | https://github.com/python-trio/sniffio |
| SQLAlchemy | 2.0.58 | MIT | https://www.sqlalchemy.org |
| starlette | 1.10.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | https://tqdm.github.io |
| typing-inspection | 0.4.5 | MIT | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| uharfbuzz | 0.57.0 | Apache-2.0 | https://github.com/trufont/uharfbuzz |
| urllib3 | 2.8.0 | MIT | https://github.com/urllib3/urllib3/blob/main/CHANGES.rst |
| uvicorn | 0.54.0 | BSD-3-Clause | https://uvicorn.dev/ |
| uvloop | 0.23.0 | Apache-2.0 OR MIT | https://pypi.org/project/uvloop/ |
| watchfiles | 1.3.0 | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.4 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| xlsxwriter | 3.3.1 | BSD-2-Clause | https://github.com/jmcnamara/XlsxWriter |

## 3. Frontend-Build

| Paket | Lizenz | Verwendung |
|---|---|---|
| esbuild | MIT | Nur zur Build-Zeit (JSX → JS); nicht Teil des ausgelieferten Codes |

## 4. Systemkomponenten im Container-Image

Werden über die Paketverwaltung des Basis-Images (Debian) installiert und
als eigenständige Programme aufgerufen, nicht eingebunden.

| Komponente | Lizenz | Verwendung |
|---|---|---|
| Debian (Basis-Image) | diverse freie Lizenzen | Laufzeitumgebung |
| Python 3 | PSF-2.0 | Laufzeit |
| OpenJDK (JRE, headless) | GPL-2.0 mit Classpath Exception | Ausführung von veraPDF |
| LibreOffice | MPL-2.0 | Konvertierung von Office-Dokumenten |
| Ghostscript | AGPL-3.0-or-later | PDF/A-Konvertierung |
| Tesseract OCR inkl. Sprachdaten | Apache-2.0 | Texterkennung |
| libmagic, libxml2, libxslt, libjpeg-turbo, libpng | BSD-2-Clause / MIT / IJG / libpng | Bibliotheken |
| DejaVu Fonts | Bitstream-Vera-Lizenz (frei) | Schriften für die Konvertierung |

## 5. Bewertung der Vereinbarkeit mit der EUPL-1.2

- **MIT, BSD, ISC, Apache-2.0, PSF-2.0, OFL-1.1:** freizügige Lizenzen ohne
  Copyleft; Nutzung und Weitergabe zusammen mit EUPL-1.2-Code ist zulässig.
  Die Lizenz- und Urhebervermerke sind beizubehalten.
- **MPL-2.0 (veraPDF, LibreOffice, einzelne Python-Pakete):** dateibezogenes
  Copyleft. Die Komponenten werden unverändert genutzt; MPL-2.0 steht zudem
  in der Liste kompatibler Lizenzen im Anhang der EUPL-1.2.
- **LGPL-3.0 (einzelne Python-Pakete):** werden als eigenständige,
  austauschbare Bibliotheken installiert und nicht verändert; die LGPL
  erlaubt diese Nutzung. LGPL steht ebenfalls im EUPL-Anhang.
- **GPL-2.0 mit Classpath Exception (OpenJDK):** eigenständige Laufzeit.
- **AGPL-3.0 (Ghostscript):** wird als eigenständiges Programm per
  Prozessaufruf genutzt, der Quellcode dieses Projekts ist davon nicht
  erfasst. Wer ein Container-Image **weitergibt**, das Ghostscript enthält,
  muss für Ghostscript die AGPL-Pflichten erfüllen (insbesondere Zugang zum
  Quelltext, z. B. über die Debian-Quellpakete).

Ergebnis: Es wurde kein Lizenzkonflikt mit der EUPL-1.2 festgestellt. Ein
Wechsel auf MIT oder Apache-2.0 ist aus Kompatibilitätsgründen nicht
erforderlich. Diese Bewertung ersetzt keine Rechtsberatung.
