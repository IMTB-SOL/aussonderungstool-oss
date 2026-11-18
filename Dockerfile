# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

# syntax=docker/dockerfile:1

# Basis-Image über ARG austauschbar, z. B. gegen ein gehärtetes Debian-Image
# oder eine per Digest festgelegte Version (--build-arg BASE_IMAGE=debian@sha256:…).
ARG BASE_IMAGE=debian:13-slim

# ---------------------------------------------------------------------------
# Builder-Stage: Python-venv aufbauen, veraPDF-JAR einbetten
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip build-essential \
    libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Python-venv mit allen Runtime-Abhängigkeiten
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/requirements.txt /tmp/requirements.txt
# requirements.txt enthält alle direkten und transitiven Pakete mit Hash
# (erzeugt aus requirements.in) — pip bricht ab, wenn ein Paket abweicht.
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# veraPDF-JAR aus dem Repo übernehmen — kein Internet-Egress im Build (Kap. 4.5)
COPY vendor/verapdf/greenfield-apps-1.28.2.jar /opt/verapdf/verapdf.jar
RUN printf '#!/bin/sh\nexec java -cp /opt/verapdf/verapdf.jar org.verapdf.apps.GreenfieldCliWrapper "$@"\n' > /opt/verapdf/verapdf \
    && chmod +x /opt/verapdf/verapdf

# ---------------------------------------------------------------------------
# Frontend-Builder-Stage: Tool-SPA-JSX mit esbuild vorab transpilieren
# ---------------------------------------------------------------------------
# Node bewusst nicht über ARG BASE_IMAGE: es gibt kein DHI-Node-Äquivalent;
# diese Stage läuft nur im Build und landet nicht im Runtime-Image.
FROM node:22-slim AS frontend-builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY frontend/tool ./frontend/tool
RUN npm run build

# ---------------------------------------------------------------------------
# Runtime-Stage: schlankes Laufzeit-Image
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

# Nur Laufzeit-Pakete — kein Webserver, kein curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    libreoffice-nogui \
    tesseract-ocr tesseract-ocr-deu \
    ghostscript \
    default-jre-headless \
    libmagic1 \
    libxml2 libxslt1.1 \
    libjpeg62-turbo libpng16-16 \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# venv und veraPDF aus der Builder-Stage übernehmen
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/verapdf /opt/verapdf
ENV PATH="/opt/venv/bin:$PATH"

# Anwendungscode
WORKDIR /app
COPY backend/ ./backend/
COPY frontend/ ./frontend/
# Vorab gebaute Tool-SPA-Skripte aus der Frontend-Builder-Stage übernehmen
COPY --from=frontend-builder /build/frontend/tool/build ./frontend/tool/build

# Datenverzeichnis (PVC-Mountpoint)
RUN mkdir -p /app/data

# Non-root-User (UID >1000, Gruppe 0 für OpenShift-Arbitrary-UID-Support)
RUN useradd -u 1001 -g 0 -d /app -M -s /usr/sbin/nologin app \
    && chown -R 1001:0 /app /opt/venv /opt/verapdf \
    && chmod -R g=u /app /opt/venv /opt/verapdf

USER 1001:0

# HOME auf beschreibbaren Pfad — LibreOffice legt dort sein Profil an
# APP_ENV=production: sichere Voreinstellungen, falls der Container ohne
# weitere Konfiguration gestartet wird (Anmeldung an, API-Doku aus).
ENV HOME=/tmp \
    APP_ENV=production \
    DATA_DIR=/app/data \
    DATABASE_URL=sqlite:////app/data/aussonderung.db \
    VERAPDF_PATH=/opt/verapdf/verapdf

WORKDIR /app/backend
EXPOSE 8000

# HEALTHCHECK für Docker-/Podman-Laufzeiten (CIS-DI-0006). In Kubernetes
# greifen stattdessen die Helm-Probes. Python statt curl, weil curl bewusst
# nicht im Runtime-Image installiert ist.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)" \
    || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
