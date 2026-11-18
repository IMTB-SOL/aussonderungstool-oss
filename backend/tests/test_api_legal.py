# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die Legal-Routes (/impressum, /datenschutz).

Die Seiten sind ohne Login erreichbar und zeigen die vom Betreiber
hinterlegten Inhalte — oder einen neutralen Hinweis, wenn nichts hinterlegt ist.
"""

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("pfad", ["/impressum", "/datenschutz"])
def test_seite_erreichbar(client, pfad):
    resp = client.get(pfad)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in resp.text


@pytest.mark.parametrize(
    ("pfad", "variable"),
    [("/impressum", "IMPRESSUM_DATEI"), ("/datenschutz", "DATENSCHUTZ_DATEI")],
)
def test_platzhalter_ohne_betreiberangaben(client, monkeypatch, pfad, variable):
    monkeypatch.delenv(variable, raising=False)
    html = client.get(pfad).text
    assert variable in html
    assert "noch kein Inhalt hinterlegt" in html


@pytest.mark.parametrize(
    ("pfad", "variable"),
    [("/impressum", "IMPRESSUM_DATEI"), ("/datenschutz", "DATENSCHUTZ_DATEI")],
)
def test_betreiberinhalt_wird_ausgeliefert(client, monkeypatch, tmp_path, pfad, variable):
    datei = tmp_path / "inhalt.html"
    datei.write_text("<p>Musterbehörde, Musterstraße 1, 12345 Musterstadt</p>", encoding="utf-8")
    monkeypatch.setenv(variable, str(datei))
    html = client.get(pfad).text
    assert "Musterbehörde, Musterstraße 1" in html
    assert "noch kein Inhalt hinterlegt" not in html


def test_fehlende_datei_faellt_auf_platzhalter_zurueck(client, monkeypatch, tmp_path):
    monkeypatch.setenv("IMPRESSUM_DATEI", str(tmp_path / "gibt-es-nicht.html"))
    resp = client.get("/impressum")
    assert resp.status_code == 200
    assert "noch kein Inhalt hinterlegt" in resp.text


def test_seiten_verlinken_einander(client):
    assert 'href="/datenschutz"' in client.get("/impressum").text
    assert 'href="/impressum"' in client.get("/datenschutz").text
