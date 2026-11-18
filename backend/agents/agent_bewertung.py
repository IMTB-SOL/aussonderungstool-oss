"""Agent 4 – Archivbewertung: deterministisches Scoring + LLM-Begründung."""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from models import (
    BehoerdenProfil,
    BewertungSchwellwerte,
    IndizKatalog,
    VorgangBewertung,
    VorgangIndizTreffer,
    get_db,
)
from utils.agent_runner import sende_sse_event
from utils.indizien import berechne_score
from utils.job_dateien import finde_datei, finde_dateien
from utils.logging_setup import pseudonymisiere_dateiname
from utils.openai_client import lade_system_prompt, llm_anfrage
from utils.protokoll import erstelle_protokoll
from utils.xdomea_parser import (
    finde_xdomea_xmls,
    lese_alle_vorgaenge,
    lese_bewertungseinheiten,
)

load_dotenv()

logger = logging.getLogger(__name__)


async def bewerte_vorgang(job_id: str, arbeitsverzeichnis: Path) -> dict:
    """Bewertet alle Vorgänge eines XDOMEA-Pakets deterministisch.

    Phasen:
      A — Vorgangs-Scan aus XDOMEA, Behördenprofil-Match per Aktenzeichen.
      B — pro Vorgang: Indizien extrahieren, Score berechnen, LLM für
          Begründung + Schlagworte, in DB persistieren.

    Returns:
        Dictionary mit Aggregat-Informationen für die SSE-Pipeline.
    """
    meldungen: list[dict] = []
    db = next(get_db())
    try:
        # ---- Phase A: Vorgangs-Scan ----
        # lese_bewertungseinheiten aggregiert über alle XMLs des Pakets,
        # entfernt Renditions-Dubletten (z.B. enaio Originalformat/PDFA) und
        # vergibt eindeutige vorgang_ids — sonst kollidiert die DB-Persistenz
        # bei DMS-Exporten mit leeren oder doppelten Kennzeichen.
        from utils.job_config import lese_bewertungsebene

        ebene = lese_bewertungsebene(arbeitsverzeichnis.parent)

        # Frühere Bewertung dieses Jobs entfernen — macht den Re-Run idempotent
        # (z.B. bei Ebenen-Wechsel); sonst akkumulieren/kollidieren die Einträge.
        # Treffer werden per cascade="all, delete-orphan" mitgelöscht.
        alte = db.query(VorgangBewertung).filter(VorgangBewertung.job_id == job_id).all()
        for vb_alt in alte:
            db.delete(vb_alt)
        if alte:
            db.commit()

        xml_dateien = finde_xdomea_xmls(arbeitsverzeichnis)
        alle_vorgaenge = lese_bewertungseinheiten(xml_dateien, ebene)
        if ebene != "auto":
            meldungen.append({"typ": "info", "text": f"Bewertungsebene: {ebene.capitalize()}."})

        if not alle_vorgaenge:
            meldungen.append({"typ": "warnung", "text": "Keine Vorgänge in XDOMEA gefunden."})
            erstelle_protokoll(job_id, "bewertung", "fehler", meldungen, arbeitsverzeichnis)
            return {"status": "fehler", "anzahl_vorgaenge": 0, "vorgaenge": []}

        n_vorgaenge = len(alle_vorgaenge)
        vorgaenge_wort = "Vorgang" if n_vorgaenge == 1 else "Vorgänge"
        await sende_sse_event(
            job_id,
            "bewertung",
            "laufend",
            f"{n_vorgaenge} {vorgaenge_wort} erkannt",
            10,
        )
        meldungen.append({"typ": "info", "text": f"{n_vorgaenge} {vorgaenge_wort} erkannt."})

        # ---- Konfiguration laden ----
        katalog = db.query(IndizKatalog).filter(IndizKatalog.aktiv.is_(True)).all()
        schwellen = db.query(BewertungSchwellwerte).first()
        schwelle_a = schwellen.schwelle_a if schwellen else 70
        schwelle_v = schwellen.schwelle_v if schwellen else 30

        # ---- Phase B: pro Vorgang bewerten ----
        ergebnisse: list[dict] = []
        behoerden_mit_praefix = (
            db.query(BehoerdenProfil)
            .filter(BehoerdenProfil.aktenzeichen_praefix.isnot(None))
            .filter(BehoerdenProfil.aktenzeichen_praefix != "")
            .all()
        )

        # Bei Lieferverzeichnis-Vorgängen liegt die Dokumentliste nicht in der
        # zentralen Akten-XML, sondern erst im inneren Vorgangs-ZIP. Wir laden
        # nur die XDOMEA-XML aus dem ZIP nach (kein vollständiges Entpacken),
        # damit Indizien wie dickitaet_hoch / viele_versionen sauber zünden.
        for vorgang in alle_vorgaenge:
            quelle_zip = vorgang.get("quelle_zip")
            if quelle_zip and not vorgang.get("dokumente"):
                inner = _lade_dokumente_aus_inner_zip(arbeitsverzeichnis, quelle_zip)
                if inner:
                    vorgang["dokumente"] = inner
                    logger.info(
                        "LV-Vorgang %s: %d Dokumente aus innerem ZIP nachgeladen.",
                        vorgang.get("aktenzeichen"),
                        len(inner),
                    )

        # Phase B1: Score deterministisch für alle Vorgänge (mit DB-Read für Vorbewertungs-Lookup)
        scoring_items: list[tuple[dict, dict]] = []  # [(vorgang, ergebnis), ...]
        for vorgang in alle_vorgaenge:
            az = vorgang.get("aktenzeichen") or ""
            profil = next(
                (p for p in behoerden_mit_praefix if az.startswith(p.aktenzeichen_praefix)),
                None,
            )
            ergebnis = berechne_score(vorgang, profil, katalog, schwelle_a, schwelle_v, db=db)
            scoring_items.append((vorgang, ergebnis))

        anzahl = len(scoring_items)

        # Phase B2: LLM-Begründungen parallel mit Semaphore (max LLM_PARALLEL gleichzeitig)
        LLM_PARALLEL = max(1, int(os.getenv("BEWERTUNG_LLM_PARALLEL", "2")))
        sem = asyncio.Semaphore(LLM_PARALLEL)
        fertig_counter = {"n": 0}

        # Initiale Voransicht: alle Vorgänge mit ihrer deterministischen
        # Klassifikation. Frontend kann darauf direkt eine Liste rendern und
        # pro Vorgang den Begründungs-Status nachziehen.
        voransicht = [
            {
                "vorgang_index": i + 1,
                "aktenzeichen": v.get("aktenzeichen") or "?",
                "betreff": (v.get("betreff") or "")[:80],
                "klassifikation": r["klassifikation"],
                "finaler_score": r["finaler_score"],
                "status": "wartet",
            }
            for i, (v, r) in enumerate(scoring_items)
        ]
        # Voransicht-Event ohne Log-Message (Frontend rendert die Liste daraus)
        await sende_sse_event(
            job_id,
            "bewertung",
            "laufend",
            "",  # leere message → nicht ins Live-Log
            10,
            extra={"phase": "bewertung_voransicht", "vorgaenge": voransicht},
        )

        async def _hole_llm_begruendung(idx: int, vorgang: dict, ergebnis: dict) -> dict:
            az = vorgang.get("aktenzeichen") or "?"
            klasse = ergebnis["klassifikation"]
            llm_input = {
                "vorgang": {
                    "aktenzeichen": vorgang.get("aktenzeichen"),
                    "betreff": vorgang.get("betreff"),
                    "anzahl_dokumente": len(vorgang.get("dokumente") or []),
                },
                "klassifikation": klasse,
                "finaler_score": ergebnis["finaler_score"],
                "treffer": [
                    {
                        "bezeichnung": t["bezeichnung"],
                        "erfuellt": t["erfuellt"],
                        "punkte_beitrag": t["punkte_beitrag"],
                        "evidenz": t["evidenz"],
                    }
                    for t in ergebnis["treffer"]
                ],
            }
            async with sem:
                # Start-Event ohne Log-Message: Frontend aktualisiert nur die
                # Voransicht-Liste (von "wartet" auf "läuft"). Das Live-Log
                # bleibt für tatsächliche Statusmeldungen sauber.
                await sende_sse_event(
                    job_id,
                    "bewertung",
                    "laufend",
                    "",
                    10 + int(80 * (idx - 0.5) / max(anzahl, 1)),
                    extra={
                        "phase": "bewertung_begruendung",
                        "vorgang_index": idx,
                        "aktenzeichen": az,
                        "klassifikation": klasse,
                        "vorgang_status": "laufend",
                    },
                )
                try:
                    antwort = await llm_anfrage(
                        lade_system_prompt("bewertung_begruendung"),
                        str(llm_input),
                        job_id,
                        f"Begründung Vorgang {idx}/{anzahl}",
                    )
                    if isinstance(antwort, str):
                        antwort = {"begruendung": antwort[:500], "schlagworte": []}
                    return {
                        "begruendung": antwort.get("begruendung", ""),
                        "schlagworte": antwort.get("schlagworte", []),
                    }
                except Exception as e:
                    logger.warning("LLM-Begründung fehlgeschlagen für Vorgang %s: %s", az, e)
                    return {
                        "begruendung": (
                            f"Klassifikation {klasse} (Score {ergebnis['finaler_score']}). "
                            f"LLM-Begründung nicht verfügbar."
                        ),
                        "schlagworte": [],
                    }
                finally:
                    fertig_counter["n"] += 1
                    progress = 10 + int(80 * fertig_counter["n"] / max(anzahl, 1))
                    # Fertig-Event mit kurzer Log-Message + Voransicht-Update
                    await sende_sse_event(
                        job_id,
                        "bewertung",
                        "laufend",
                        f"Vorgang {fertig_counter['n']}/{anzahl} bewertet: {az} → {klasse}",
                        progress,
                        extra={
                            "phase": "bewertung_begruendung",
                            "vorgang_index": idx,
                            "aktenzeichen": az,
                            "klassifikation": klasse,
                            "vorgang_status": "fertig",
                            "fertig": fertig_counter["n"],
                            "gesamt": anzahl,
                        },
                    )

        llm_resultate = await asyncio.gather(
            *(_hole_llm_begruendung(i + 1, v, r) for i, (v, r) in enumerate(scoring_items))
        )

        # Phase B3: DB-Persistenz sequentiell (eine Session)
        for (vorgang, ergebnis), llm_res in zip(scoring_items, llm_resultate):
            az = vorgang.get("aktenzeichen") or ""
            vb = VorgangBewertung(
                job_id=job_id,
                vorgang_id=vorgang.get("vorgang_id") or az,
                aktenzeichen=az,
                betreff=vorgang.get("betreff"),
                rohscore=ergebnis["rohscore"],
                finaler_score=ergebnis["finaler_score"],
                klassifikation_vorschlag=ergebnis["klassifikation"],
                llm_begruendung=llm_res["begruendung"],
                schlagworte=llm_res["schlagworte"],
            )
            db.add(vb)
            db.flush()

            for t in ergebnis["treffer"]:
                db.add(
                    VorgangIndizTreffer(
                        vorgang_bewertung_id=vb.id,
                        indiz_schluessel=t["schluessel"],
                        erfuellt=t["erfuellt"],
                        punkte_beitrag=t["punkte_beitrag"],
                        evidenz=t["evidenz"],
                    )
                )
            db.commit()

            erfuellte_treffer = sorted(
                (t for t in ergebnis["treffer"] if t["erfuellt"]),
                key=lambda t: t["punkte_beitrag"],
                reverse=True,
            )
            ergebnisse.append(
                {
                    "vorgang_bewertung_id": vb.id,
                    "aktenzeichen": az,
                    "betreff": vorgang.get("betreff") or "",
                    "klassifikation_vorschlag": ergebnis["klassifikation"],
                    "finaler_score": ergebnis["finaler_score"],
                    "rohscore": ergebnis["rohscore"],
                    "erfuellte_treffer": [
                        {
                            "bezeichnung": t.get("bezeichnung") or t["schluessel"],
                            "punkte_beitrag": t["punkte_beitrag"],
                            "evidenz": t["evidenz"],
                        }
                        for t in erfuellte_treffer
                    ],
                }
            )

        erstelle_protokoll(job_id, "bewertung", "ok", meldungen, arbeitsverzeichnis)
        n_erg = len(ergebnisse)
        erg_wort = "Vorgang" if n_erg == 1 else "Vorgänge"
        await sende_sse_event(
            job_id,
            "bewertung",
            "wartet",
            f"{n_erg} {erg_wort} bewertet — Archivar-Entscheidung steht aus",
            95,
        )
        return {
            "status": "ok",
            "anzahl_vorgaenge": len(ergebnisse),
            "vorgaenge": ergebnisse,
        }

    except Exception as e:
        logger.exception("[Job %s] Unerwarteter Fehler im Bewertungsagent: %s", job_id, e)
        meldungen.append({"typ": "fehler", "text": f"Unerwarteter Fehler: {e}"})
        erstelle_protokoll(job_id, "bewertung", "fehler", meldungen, arbeitsverzeichnis)
        return {"status": "fehler", "anzahl_vorgaenge": 0, "vorgaenge": []}
    finally:
        db.close()


def _lade_dokumente_aus_inner_zip(arbeitsverzeichnis: Path, zip_dateiname: str) -> list[dict]:
    """Liest die XDOMEA-XML aus einem inneren Vorgangs-ZIP und gibt dessen
    Dokumentliste zurück. Das ZIP wird nicht vollständig entpackt — wir
    extrahieren nur die XML in einen TempFile.

    Returns:
        Liste von Dokument-Dicts (wie von lese_alle_vorgaenge), oder [].
    """
    import tempfile
    import zipfile

    zip_pfad = finde_datei(arbeitsverzeichnis, zip_dateiname)
    if zip_pfad is None:
        return []

    try:
        with zipfile.ZipFile(zip_pfad) as zf:
            xml_namen = [
                n for n in zf.namelist() if n.endswith(".xml") and not n.startswith("protokoll_")
            ]
            if not xml_namen:
                return []
            # Größte XML = Haupt-XDOMEA
            xml_namen.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
            xml_data = zf.read(xml_namen[0])

        with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tmpf:
            tmpf.write(xml_data)
            tmpf.flush()
            innere_vorgaenge = lese_alle_vorgaenge(Path(tmpf.name))
            if innere_vorgaenge:
                return innere_vorgaenge[0].get("dokumente", []) or []
    except Exception as e:
        logger.warning(
            "Konnte Dokumente aus inneren ZIP '%s' nicht lesen: %s",
            pseudonymisiere_dateiname(zip_dateiname),
            e,
        )
    return []


def _extrahiere_dokumenttext(arbeitsverzeichnis: Path, dateiname: str) -> str:
    """Sammelt den Volltext eines Dokuments — synchron, für asyncio.to_thread.

    Läuft bewusst in einem Worker-Thread: sammle_dokumenttexte ist synchron und
    CPU-/IO-lastig (PDF-Parsing, ggf. OCR) und würde im Event-Loop die parallele
    Tiefenprüfung samt SSE-Fortschritt blockieren.
    """
    from utils.text_extraktion import sammle_dokumenttexte

    kandidaten = finde_dateien(arbeitsverzeichnis, dateiname)
    if not kandidaten:
        return ""
    meldungen: list[dict] = []
    text_sammlung = sammle_dokumenttexte(kandidaten[0].parent, meldungen)
    return text_sammlung[:80_000]


async def tiefenpruefung_vorgang(
    job_id: str,
    vorgang_bewertung_id: int,
    vorgang: dict,
    arbeitsverzeichnis: Path,
) -> dict:
    """Bewertet die Einzeldokumente eines Vorgangs per LLM — parallel.

    Die Dokumente werden mit beschränkter Nebenläufigkeit
    (TIEFENPRUEFUNG_LLM_PARALLEL, Default 4) gleichzeitig per LLM bewertet.
    Regel: Sobald ≥1 Dokument 'archivwürdig' ist, wird die Klassifikation des
    Vorgangs auf A gesetzt.
    """
    from models import Bewertung, DokumentBewertung
    from utils.openai_client import PROMPT_TIEFENPRUEFUNG_DOKUMENT

    db = next(get_db())
    try:
        vb = db.query(VorgangBewertung).get(vorgang_bewertung_id)
        if vb is None:
            return {"status": "fehler", "fehler": "vorgang_bewertung nicht gefunden"}

        # Nur Dokumente mit Dateiname — ohne sind sie nicht prüfbar.
        dokumente = [d for d in (vorgang.get("dokumente") or []) if d.get("dateiname")]
        anzahl_dok = len(dokumente)
        extra_base = {
            "phase": "tiefenpruefung",
            "vorgang_bewertung_id": vb.id,
            "aktenzeichen": vb.aktenzeichen,
            "dok_gesamt": anzahl_dok,
        }
        await sende_sse_event(
            job_id,
            "bewertung",
            "tiefenpruefung",
            f"Tiefenprüfung Vorgang {vb.aktenzeichen} startet ({anzahl_dok} Dokumente)…",
            0,
            extra={**extra_base, "dok_index": 0},
        )

        # vb-Felder VOR der parallelen Phase auslesen — innerhalb der
        # Coroutinen darf die db-Session nicht angefasst werden.
        az_vb = vb.aktenzeichen or "(ohne Aktenzeichen)"
        betreff_vb = vb.betreff or ""

        # Nebenläufigkeit deckeln. Scaleway verträgt hohe Werte, ein lokales
        # Ollama rechnet seriell — dort TIEFENPRUEFUNG_LLM_PARALLEL auf 1–2 senken.
        llm_parallel = max(1, int(os.getenv("TIEFENPRUEFUNG_LLM_PARALLEL", "4")))
        sem = asyncio.Semaphore(llm_parallel)
        fertig = {"n": 0}

        async def _pruefe_dokument(index: int, dok: dict) -> tuple:
            """Bewertet ein einzelnes Dokument per LLM.

            Wirft nie — ein LLM-Fehler wird zu einer 'nicht archivwürdig'-
            Bewertung mit Fehler-Begründung.
            """
            dateiname = dok["dateiname"]
            async with sem:
                # Start-Signal je Dokument — Balken und Meldung zeigen auch vor
                # dem ersten fertigen Dokument Leben.
                await sende_sse_event(
                    job_id,
                    "bewertung",
                    "tiefenpruefung",
                    f"Prüfe Dokument: {dateiname}",
                    int(100 * fertig["n"] / max(anzahl_dok, 1)),
                    extra={
                        **extra_base,
                        "dok_index": index + 1,
                        "dateiname": dateiname,
                        "schritt": "llm",
                    },
                )
                # Textextraktion in einen Worker-Thread auslagern: sie ist
                # synchron und blockierend (PDF-Parsing, ggf. OCR). Im
                # Event-Loop würde sie alle SSE-Events und die parallelen
                # LLM-Antworten ausbremsen — der Fortschrittsbalken fröre ein.
                text = await asyncio.to_thread(
                    _extrahiere_dokumenttext, arbeitsverzeichnis, dateiname
                )
                llm_input = (
                    f"Dokument: {dateiname}\nAktenzeichen: {az_vb}\n"
                    f"Betreff: {betreff_vb}\n\nInhalt:\n{text}"
                )
                try:
                    antwort = await llm_anfrage(
                        PROMPT_TIEFENPRUEFUNG_DOKUMENT,
                        llm_input,
                        job_id,
                        f"Tiefenprüfung Dokument {dateiname}",
                    )
                    if isinstance(antwort, dict):
                        bewertung_str = antwort.get("bewertung", "nicht archivwürdig")
                        begruendung = antwort.get("begruendung", "")
                    else:
                        bewertung_str, begruendung = "nicht archivwürdig", str(antwort)[:500]
                except Exception as e:
                    logger.warning(
                        "Tiefenprüfung Dokument %s fehlgeschlagen: %s",
                        pseudonymisiere_dateiname(dateiname),
                        e,
                    )
                    bewertung_str, begruendung = "nicht archivwürdig", f"Fehler: {e}"

            # Fortschritt über Fertig-Zähler — steigt bei paralleler Ausführung
            # monoton. Zwischen Inkrement und Lesen liegt kein await: atomar.
            fertig["n"] += 1
            await sende_sse_event(
                job_id,
                "bewertung",
                "tiefenpruefung",
                f"Dokument {fertig['n']}/{anzahl_dok} geprüft: {dateiname}",
                int(100 * fertig["n"] / max(anzahl_dok, 1)),
                extra={
                    **extra_base,
                    "dok_index": index + 1,
                    "dateiname": dateiname,
                    "schritt": "fertig",
                    "bewertung": bewertung_str,
                },
            )
            return dateiname, bewertung_str, begruendung

        # Alle Dokumente parallel (semaphoren-gedeckelt) bewerten.
        # gather erhält die Reihenfolge der Eingabeliste.
        roh_ergebnisse = await asyncio.gather(
            *[_pruefe_dokument(i, dok) for i, dok in enumerate(dokumente)],
            return_exceptions=True,
        )

        # Enum-Mapping und DB-Schreibzugriffe sequentiell NACH gather — so
        # bleibt die SQLAlchemy-Session koroutinen-sicher.
        archivwuerdig_gefunden = False
        dok_ergebnisse: list[tuple] = []  # (dateiname, bewertung_enum, begruendung)
        for i, dok in enumerate(dokumente):
            roh = roh_ergebnisse[i]
            if isinstance(roh, BaseException):
                # Eine der Coroutine entkommene Ausnahme (z. B. aus der
                # Textextraktion) — wie ein Dokument-Fehler behandeln.
                dateiname = dok["dateiname"]
                logger.warning(
                    "Tiefenprüfung Dokument %s fehlgeschlagen: %s",
                    pseudonymisiere_dateiname(dateiname),
                    roh,
                )
                bewertung_str, begruendung = "nicht archivwürdig", f"Fehler: {roh}"
            else:
                dateiname, bewertung_str, begruendung = roh

            # Explizites Mapping auf Bewertung-Enum-Member, um SQLAlchemy-Typfehler zu vermeiden
            if bewertung_str == Bewertung.archivwuerdig.value:
                bewertung_enum = Bewertung.archivwuerdig
            elif bewertung_str == Bewertung.manuelle_ueberpruefung.value:
                bewertung_enum = Bewertung.manuelle_ueberpruefung
            else:
                bewertung_enum = Bewertung.nicht_archivwuerdig

            db.add(
                DokumentBewertung(
                    vorgang_bewertung_id=vb.id,
                    dateiname=dateiname,
                    bewertung=bewertung_enum,
                    begruendung=begruendung,
                )
            )
            dok_ergebnisse.append((dateiname, bewertung_enum, begruendung))

            if bewertung_enum == Bewertung.archivwuerdig:
                archivwuerdig_gefunden = True

        # Sammelbegründung auf Vorgangs-Ebene: Die Bewertung erfolgt am Ende für
        # den gesamten Vorgang, nicht pro Dokument. Ist ein Dokument archivwürdig,
        # ist der ganze Vorgang A — begründet durch eine zusammenfassende Begründung.
        archivwuerdige = [(n, g) for (n, e, g) in dok_ergebnisse if e == Bewertung.archivwuerdig]
        az = vb.aktenzeichen or "(ohne Aktenzeichen)"
        if archivwuerdig_gefunden:
            zeilen = [
                f"Tiefenprüfung des Vorgangs {az}: {len(dok_ergebnisse)} Dokument(e) "
                f"einzeln geprüft.",
                f"{len(archivwuerdige)} davon archivwürdig — da mindestens ein Dokument "
                f"archivwürdig ist, ist der gesamte Vorgang archivwürdig (A).",
                "Ausschlaggebende Dokumente:",
            ]
            for name, grund in archivwuerdige[:6]:
                kurz = (grund or "").strip()
                zeilen.append(f"• {name}: {kurz}" if kurz else f"• {name}")
            sammelbegruendung = "\n".join(zeilen)
        else:
            sammelbegruendung = (
                f"Tiefenprüfung des Vorgangs {az}: {len(dok_ergebnisse)} Dokument(e) "
                f"einzeln geprüft. Kein Dokument wurde als archivwürdig bewertet — die "
                f"Klassifikation {vb.klassifikation_vorschlag} bleibt bestehen."
            )

        vb.tiefenpruefung_gemacht = True
        klassifikation_neu = "A" if archivwuerdig_gefunden else vb.klassifikation_vorschlag
        if archivwuerdig_gefunden:
            vb.klassifikation_vorschlag = "A"
        # Die Sammelbegründung ersetzt nach der Tiefenprüfung die Vorgangs-Begründung.
        vb.llm_begruendung = sammelbegruendung
        db.commit()

        await sende_sse_event(
            job_id,
            "bewertung",
            "tiefenpruefung",
            f"Tiefenprüfung abgeschlossen → Klassifikation {klassifikation_neu}",
            100,
            extra={
                **extra_base,
                "schritt": "abgeschlossen",
                "klassifikation_neu": klassifikation_neu,
            },
        )

        return {
            "status": "ok",
            "klassifikation_neu": klassifikation_neu,
            "sammelbegruendung": sammelbegruendung,
            "tiefenpruefung_gemacht": True,
        }
    except Exception as e:
        logger.exception("Tiefenprüfung fehlgeschlagen: %s", e)
        return {"status": "fehler", "fehler": str(e)}
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 4: Archivbewertung v2")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--pfad", default=".", help="Arbeitsverzeichnis (entpacktes ZIP)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(bewerte_vorgang(args.job_id, Path(args.pfad))))
