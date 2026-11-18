# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für sicherheitskritische Schutzmaßnahmen.

Deckt ab: sichere Dateisuche (kein Ausbruch aus dem Arbeitsverzeichnis, keine
Platzhalter), Erkennung von Platzhalter-Geheimnissen, Erst-Admin-Anlage,
Upload-Größenlimit, Auth-Ausnahme für statische Dateien und das Beenden
hängender Subprozesse.
"""

import asyncio
import io
import sys
import zipfile

import pytest
from utils import auth
from utils.job_dateien import finde_datei, finde_dateien
from utils.prozess import kommuniziere_mit_timeout


# --- Sichere Dateisuche -----------------------------------------------------
def test_finde_dateien_findet_exakten_namen(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "bericht.docx").write_text("x")
    (tmp_path / "bericht.pdf").write_text("x")
    treffer = finde_dateien(tmp_path, "bericht.docx")
    assert [p.name for p in treffer] == ["bericht.docx"]


def test_finde_dateien_verlaesst_das_verzeichnis_nicht(tmp_path):
    arbeit = tmp_path / "job-1" / "arbeit"
    arbeit.mkdir(parents=True)
    fremd = tmp_path / "job-2"
    fremd.mkdir()
    (fremd / "geheim.docx").write_text("x")
    assert finde_dateien(arbeit, "../../job-2/geheim.docx") == []
    assert finde_datei(arbeit, "../../job-2/geheim.docx") is None


def test_finde_dateien_wertet_keine_platzhalter_aus(tmp_path):
    (tmp_path / "eins.docx").write_text("x")
    (tmp_path / "zwei.docx").write_text("x")
    assert finde_dateien(tmp_path, "*.docx") == []
    assert finde_dateien(tmp_path, "*") == []
    assert finde_dateien(tmp_path, "") == []
    assert finde_dateien(tmp_path, None) == []
    assert finde_dateien(tmp_path, "..") == []


def test_finde_dateien_verwirft_pfadanteil(tmp_path):
    (tmp_path / "unter").mkdir()
    (tmp_path / "unter" / "akte.zip").write_text("x")
    assert finde_datei(tmp_path, "irgendwo/akte.zip") == tmp_path / "unter" / "akte.zip"
    assert finde_datei(tmp_path, "irgendwo\\akte.zip") == tmp_path / "unter" / "akte.zip"


# --- Platzhalter-Geheimnisse ------------------------------------------------
@pytest.mark.parametrize(
    "wert",
    ["", "kurz", "BITTE-ERSETZEN-BITTE-ERSETZEN-BITTE-ERSETZEN", "<token_hex(32)>" + "a" * 32],
)
def test_platzhalter_und_kurze_werte_werden_erkannt(wert):
    assert auth.ist_platzhalter_oder_zu_kurz(wert, 32) is True


def test_starker_wert_wird_akzeptiert():
    assert auth.ist_platzhalter_oder_zu_kurz("f3" * 32, 32) is False


def test_erst_admin_wird_mit_platzhalter_passwort_nicht_angelegt(monkeypatch):
    from models import Base, User, engine
    from sqlalchemy.orm import Session
    from utils.seed_admin import seede_admin

    Base.metadata.create_all(bind=engine)
    db = Session(engine)
    try:
        db.query(User).delete()
        db.commit()
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "BITTE-ERSETZEN")
        seede_admin(db)
        assert db.query(User).count() == 0

        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Ein-wirklich-langes-Kennwort-42")
        seede_admin(db)
        assert db.query(User).filter_by(is_admin=True).count() == 1
    finally:
        db.query(User).delete()
        db.commit()
        db.close()


# --- Upload-Limit -----------------------------------------------------------
def test_upload_ueber_limit_wird_abgebrochen_und_aufgeraeumt(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from main import app
    from routers import jobs

    monkeypatch.setattr(jobs, "MAX_UPLOAD_MB", 1)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)

    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("gross.bin", b"\0" * (3 * 1024 * 1024))

    with TestClient(app) as client:
        r = client.post(
            "/api/upload",
            files={"datei": ("gross.zip", puffer.getvalue(), "application/zip")},
        )
    assert r.status_code == 413
    # Kein halb geschriebenes Job-Verzeichnis zurücklassen
    jobs_dir = tmp_path / "jobs"
    assert not jobs_dir.exists() or not any(jobs_dir.iterdir())


# --- Auth-Ausnahme für statische Dateien ------------------------------------
def test_statische_endung_oeffnet_keine_api_pfade():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.auth import AuthMiddleware

    mini = FastAPI()
    mini.add_middleware(AuthMiddleware)

    @mini.get("/api/geheim.js")
    def _geheim():
        return {"ok": True}

    @mini.get("/datei.js")
    def _statisch():
        return {"ok": True}

    with TestClient(mini) as client:
        assert client.get("/api/geheim.js").status_code == 401
        assert client.get("/datei.js").status_code == 200


# --- Subprozesse ------------------------------------------------------------
def test_haengender_prozess_wird_beendet():
    async def ablauf():
        prozess = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with pytest.raises(asyncio.TimeoutError):
            await kommuniziere_mit_timeout(prozess, 0.5)
        return prozess.returncode

    # Nach dem Timeout ist der Prozess beendet (returncode gesetzt), nicht verwaist.
    assert asyncio.run(ablauf()) is not None
