"""Tests für den neuen Bewertungsagenten (Indizien-Scoring)."""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_bewerte_vorgang_legt_vorgang_bewertung_an(tmp_path, monkeypatch):
    """Smoke-Test: Agent läuft auf einem Minimal-XDOMEA und persistiert vorgang_bewertung."""
    from agents.agent_bewertung import bewerte_vorgang
    from models import Base, engine, get_db
    from utils.seed_bewertung import seede_bewertungsdaten

    # Test-DB einrichten und seeden
    Base.metadata.create_all(engine)
    _db = next(get_db())
    try:
        seede_bewertungsdaten(_db)
    finally:
        _db.close()

    # Eindeutige Job-ID pro Testlauf vermeidet UNIQUE-Constraint-Kollision
    # mit der gemeinsamen aussonderung.db
    test_job_id = f"smoke-test-{uuid.uuid4().hex[:8]}"

    # Minimal-XDOMEA mit einem Vorgang
    xml = tmp_path / "test.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Aktenplan xmlns="urn:xoev-de:xdomea:schema:3.0.0">
  <Vorgang>
    <Kennzeichen>SMOKE-test/2024</Kennzeichen>
    <Bemerkung>Testvorgang</Bemerkung>
    <Federfuehrung>Eigene Stelle</Federfuehrung>
    <Aufbewahrungsdauer>30</Aufbewahrungsdauer>
    <Aussonderungsart>Archivieren</Aussonderungsart>
  </Vorgang>
</Aktenplan>
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "agents.agent_bewertung.llm_anfrage",
        AsyncMock(return_value={"begruendung": "Test", "schlagworte": ["x"]}),
    )
    monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())
    monkeypatch.setattr("agents.agent_bewertung.erstelle_protokoll", MagicMock())

    ergebnis = await bewerte_vorgang(test_job_id, tmp_path)

    assert ergebnis["status"] == "ok"
    assert ergebnis["anzahl_vorgaenge"] == 1
    # archivvermerk = OK-Boden → Klassifikation A
    assert ergebnis["vorgaenge"][0]["klassifikation_vorschlag"] == "A"

    # Auch DB-Persistenz verifizieren
    from models import VorgangBewertung, VorgangIndizTreffer

    db2 = next(get_db())
    try:
        gespeicherte = db2.query(VorgangBewertung).filter_by(job_id=test_job_id).all()
        assert len(gespeicherte) == 1
        assert gespeicherte[0].klassifikation_vorschlag == "A"

        treffer = (
            db2.query(VorgangIndizTreffer)
            .filter_by(
                vorgang_bewertung_id=gespeicherte[0].id,
            )
            .all()
        )
        assert len(treffer) >= 1
    finally:
        db2.close()


@pytest.mark.asyncio
async def test_tiefenpruefung_hebt_vorgang_auf_a(tmp_path, monkeypatch):
    """Wenn min. 1 Dokument der Tiefenprüfung archivwürdig ist, wird der Vorgang A."""
    import uuid

    from agents.agent_bewertung import tiefenpruefung_vorgang
    from models import Base, DokumentBewertung, VorgangBewertung, engine, get_db

    Base.metadata.create_all(engine)

    test_job = f"tief-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    try:
        vb = VorgangBewertung(
            job_id=test_job,
            vorgang_id="vg-1",
            aktenzeichen=f"TIEF-{test_job}/2024",
            betreff="Test",
            rohscore=50,
            finaler_score=50,
            klassifikation_vorschlag="B",
        )
        db.add(vb)
        db.commit()
        vb_id = vb.id

        antworten = [
            {"bewertung": "archivwürdig", "begruendung": "wichtig"},
            {"bewertung": "nicht archivwürdig", "begruendung": "routine"},
        ]

        async def fake_llm(*a, **kw):
            return antworten.pop(0)

        monkeypatch.setattr("agents.agent_bewertung.llm_anfrage", fake_llm)
        monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())

        vorgang = {
            "dokumente": [
                {"dateiname": "dok1.pdf"},
                {"dateiname": "dok2.pdf"},
            ],
            "aktenzeichen": vb.aktenzeichen,
        }
        ergebnis = await tiefenpruefung_vorgang(test_job, vb_id, vorgang, tmp_path)
        assert ergebnis["status"] == "ok"
        assert ergebnis["klassifikation_neu"] == "A"
    finally:
        db.close()

    db2 = next(get_db())
    try:
        vb_refreshed = db2.query(VorgangBewertung).get(vb_id)
        assert vb_refreshed.tiefenpruefung_gemacht is True
        assert vb_refreshed.klassifikation_vorschlag == "A"
        dokumente = db2.query(DokumentBewertung).filter_by(vorgang_bewertung_id=vb_id).all()
        assert len(dokumente) == 2
    finally:
        db2.close()


@pytest.mark.asyncio
async def test_end_to_end_bewertung_mit_fixture(tmp_path, monkeypatch):
    """End-to-End: XDOMEA → Vorgänge bewertet → in DB persistiert → API liefert sie."""
    import uuid

    from agents.agent_bewertung import bewerte_vorgang
    from fastapi.testclient import TestClient
    from main import app
    from models import Base, VorgangBewertung, engine, get_db
    from utils.seed_bewertung import seede_bewertungsdaten

    Base.metadata.create_all(engine)
    db = next(get_db())
    try:
        seede_bewertungsdaten(db)
    finally:
        db.close()

    test_job = f"smoke-{uuid.uuid4().hex[:8]}"

    xml = tmp_path / "xd.xml"
    xml.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Aktenplan xmlns="urn:xoev-de:xdomea:schema:3.0.0">
  <Vorgang>
    <Kennzeichen>BSP-{test_job}-A/2024</Kennzeichen>
    <Bemerkung>Ministervorlage</Bemerkung>
    <Federfuehrung>Eigene Stelle</Federfuehrung>
    <Aufbewahrungsdauer>30</Aufbewahrungsdauer>
    <Aussonderungsart>Archivieren</Aussonderungsart>
  </Vorgang>
  <Vorgang>
    <Kennzeichen>BSP-{test_job}-R/2024</Kennzeichen>
    <Bemerkung>Urlaubsantrag</Bemerkung>
    <Federfuehrung>Fremdstelle</Federfuehrung>
  </Vorgang>
</Aktenplan>
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "agents.agent_bewertung.llm_anfrage",
        AsyncMock(return_value={"begruendung": "auto", "schlagworte": ["x"]}),
    )
    monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())
    monkeypatch.setattr("agents.agent_bewertung.erstelle_protokoll", MagicMock())

    ergebnis = await bewerte_vorgang(test_job, tmp_path)
    assert ergebnis["status"] == "ok"
    assert ergebnis["anzahl_vorgaenge"] == 2

    # DB-Persistenz prüfen
    db = next(get_db())
    try:
        ministervorlage = (
            db.query(VorgangBewertung).filter_by(aktenzeichen=f"BSP-{test_job}-A/2024").one()
        )
        urlaub = db.query(VorgangBewertung).filter_by(aktenzeichen=f"BSP-{test_job}-R/2024").one()
        assert ministervorlage.klassifikation_vorschlag == "A"  # OK-Boden via Archivvermerk
        assert urlaub.klassifikation_vorschlag in ("B", "V")  # KO-Deckel via Nicht-Federführung
    finally:
        db.close()

    # API-Pfad: Vorgänge per HTTP abrufen
    client = TestClient(app)
    r = client.get(f"/api/bewertung/vorgaenge/{test_job}")
    assert r.status_code == 200
    daten = r.json()
    assert len(daten) == 2
    # Mindestens ein Vorgang muss A sein, mindestens einer nicht-A
    klassen = {v["klassifikation_vorschlag"] for v in daten}
    assert "A" in klassen


@pytest.mark.asyncio
async def test_tiefenpruefung_bewertet_alle_dokumente_in_reihenfolge(tmp_path, monkeypatch):
    """Alle Dokumente werden bewertet; DokumentBewertung-Zeilen in Original-Reihenfolge."""
    from agents.agent_bewertung import tiefenpruefung_vorgang
    from models import Base, DokumentBewertung, VorgangBewertung, engine, get_db

    Base.metadata.create_all(engine)
    test_job = f"tief-reihe-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    try:
        vb = VorgangBewertung(
            job_id=test_job,
            vorgang_id="vg-1",
            aktenzeichen=f"REIHE-{test_job}/2024",
            betreff="Test",
            rohscore=50,
            finaler_score=50,
            klassifikation_vorschlag="B",
        )
        db.add(vb)
        db.commit()
        vb_id = vb.id
    finally:
        db.close()

    # Antwort hängt nicht von der Aufruf-Reihenfolge ab.
    async def fake_llm(system_prompt, user_prompt, job_id, kontext):
        return {"bewertung": "nicht archivwürdig", "begruendung": "geprüft"}

    monkeypatch.setattr("agents.agent_bewertung.llm_anfrage", fake_llm)
    monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())

    namen = ["a.pdf", "b.pdf", "c.pdf", "d.pdf"]
    vorgang = {
        "dokumente": [{"dateiname": n} for n in namen],
        "aktenzeichen": f"REIHE-{test_job}/2024",
    }
    ergebnis = await tiefenpruefung_vorgang(test_job, vb_id, vorgang, tmp_path)
    assert ergebnis["status"] == "ok"

    db2 = next(get_db())
    try:
        zeilen = (
            db2.query(DokumentBewertung)
            .filter_by(vorgang_bewertung_id=vb_id)
            .order_by(DokumentBewertung.id)
            .all()
        )
        assert [z.dateiname for z in zeilen] == namen
    finally:
        db2.close()


@pytest.mark.asyncio
async def test_tiefenpruefung_isoliert_dokument_fehler(tmp_path, monkeypatch):
    """Ein fehlgeschlagener LLM-Aufruf bricht die anderen Dokumente nicht ab."""
    from agents.agent_bewertung import tiefenpruefung_vorgang
    from models import Base, DokumentBewertung, VorgangBewertung, engine, get_db

    Base.metadata.create_all(engine)
    test_job = f"tief-fehler-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    try:
        vb = VorgangBewertung(
            job_id=test_job,
            vorgang_id="vg-1",
            aktenzeichen=f"FEHLER-{test_job}/2024",
            betreff="Test",
            rohscore=50,
            finaler_score=50,
            klassifikation_vorschlag="B",
        )
        db.add(vb)
        db.commit()
        vb_id = vb.id
    finally:
        db.close()

    async def fake_llm(system_prompt, user_prompt, job_id, kontext):
        if "kaputt.pdf" in kontext:
            raise RuntimeError("LLM-Ausfall")
        return {"bewertung": "nicht archivwürdig", "begruendung": "ok"}

    monkeypatch.setattr("agents.agent_bewertung.llm_anfrage", fake_llm)
    monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())

    vorgang = {
        "dokumente": [
            {"dateiname": "ok1.pdf"},
            {"dateiname": "kaputt.pdf"},
            {"dateiname": "ok2.pdf"},
        ],
        "aktenzeichen": f"FEHLER-{test_job}/2024",
    }
    ergebnis = await tiefenpruefung_vorgang(test_job, vb_id, vorgang, tmp_path)
    assert ergebnis["status"] == "ok"

    db2 = next(get_db())
    try:
        zeilen = {
            z.dateiname: z
            for z in db2.query(DokumentBewertung).filter_by(vorgang_bewertung_id=vb_id).all()
        }
        assert set(zeilen) == {"ok1.pdf", "kaputt.pdf", "ok2.pdf"}
        assert "Fehler:" in zeilen["kaputt.pdf"].begruendung
        assert zeilen["ok1.pdf"].begruendung == "ok"
    finally:
        db2.close()


@pytest.mark.asyncio
async def test_tiefenpruefung_deckelt_nebenlaeufigkeit(tmp_path, monkeypatch):
    """Es laufen nie mehr LLM-Aufrufe gleichzeitig als TIEFENPRUEFUNG_LLM_PARALLEL."""
    import asyncio

    from agents.agent_bewertung import tiefenpruefung_vorgang
    from models import Base, VorgangBewertung, engine, get_db

    monkeypatch.setenv("TIEFENPRUEFUNG_LLM_PARALLEL", "2")

    Base.metadata.create_all(engine)
    test_job = f"tief-par-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    try:
        vb = VorgangBewertung(
            job_id=test_job,
            vorgang_id="vg-1",
            aktenzeichen=f"PAR-{test_job}/2024",
            betreff="Test",
            rohscore=50,
            finaler_score=50,
            klassifikation_vorschlag="B",
        )
        db.add(vb)
        db.commit()
        vb_id = vb.id
    finally:
        db.close()

    laufend = {"jetzt": 0, "max": 0}

    async def fake_llm(system_prompt, user_prompt, job_id, kontext):
        laufend["jetzt"] += 1
        laufend["max"] = max(laufend["max"], laufend["jetzt"])
        await asyncio.sleep(0.02)  # echten Suspension-Punkt erzwingen
        laufend["jetzt"] -= 1
        return {"bewertung": "nicht archivwürdig", "begruendung": "ok"}

    monkeypatch.setattr("agents.agent_bewertung.llm_anfrage", fake_llm)
    monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())

    vorgang = {
        "dokumente": [{"dateiname": f"d{i}.pdf"} for i in range(6)],
        "aktenzeichen": f"PAR-{test_job}/2024",
    }
    ergebnis = await tiefenpruefung_vorgang(test_job, vb_id, vorgang, tmp_path)
    assert ergebnis["status"] == "ok"
    assert laufend["max"] <= 2  # Semaphore deckelt die Nebenläufigkeit
    assert laufend["max"] >= 2  # bei 6 Dokumenten wird das Limit 2 auch erreicht


@pytest.mark.asyncio
async def test_tiefenpruefung_extraktion_blockiert_event_loop_nicht(tmp_path, monkeypatch):
    """Die synchrone Textextraktion läuft im Worker-Thread, nicht im Event-Loop."""
    import threading

    from agents.agent_bewertung import tiefenpruefung_vorgang
    from models import Base, VorgangBewertung, engine, get_db

    Base.metadata.create_all(engine)
    test_job = f"tief-thread-{uuid.uuid4().hex[:8]}"
    db = next(get_db())
    try:
        vb = VorgangBewertung(
            job_id=test_job,
            vorgang_id="vg-1",
            aktenzeichen=f"THREAD-{test_job}/2024",
            betreff="Test",
            rohscore=50,
            finaler_score=50,
            klassifikation_vorschlag="B",
        )
        db.add(vb)
        db.commit()
        vb_id = vb.id
    finally:
        db.close()

    haupt_thread = threading.get_ident()
    extraktions_threads = []

    def fake_extrahiere(arbeitsverzeichnis, dateiname):
        extraktions_threads.append(threading.get_ident())
        return "Dokumentinhalt"

    async def fake_llm(system_prompt, user_prompt, job_id, kontext):
        return {"bewertung": "nicht archivwürdig", "begruendung": "ok"}

    monkeypatch.setattr("agents.agent_bewertung._extrahiere_dokumenttext", fake_extrahiere)
    monkeypatch.setattr("agents.agent_bewertung.llm_anfrage", fake_llm)
    monkeypatch.setattr("agents.agent_bewertung.sende_sse_event", AsyncMock())

    vorgang = {
        "dokumente": [{"dateiname": "a.pdf"}, {"dateiname": "b.pdf"}],
        "aktenzeichen": f"THREAD-{test_job}/2024",
    }
    ergebnis = await tiefenpruefung_vorgang(test_job, vb_id, vorgang, tmp_path)
    assert ergebnis["status"] == "ok"
    # Extraktion wurde je Dokument aufgerufen — und nie im Haupt-/Event-Loop-Thread.
    assert len(extraktions_threads) == 2
    assert all(t != haupt_thread for t in extraktions_threads)
