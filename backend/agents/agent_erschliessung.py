"""Agent 5 – Erschließung: Sprachmodell-gestützte Erschließungsinformationen."""

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _leere_erschliessung(status: str = "ok") -> dict:
    return {
        "status": status,
        "titel": "",
        "inhaltsangabe": "",
        "schlagworte": [],
        "dokumenttyp": "nicht ermittelbar",
        "personen": [],
        "stellen": [],
        "orte": [],
        "zeitraum": "",
        "aktenzeichen": "",
        "vorgangsbezug": "nicht ermittelbar",
        "bezuege": "",
    }


def _erschliessung_aus_gpt(gpt: dict) -> dict:
    return {
        "titel": gpt.get("titel", ""),
        "inhaltsangabe": gpt.get("inhaltsangabe", ""),
        "schlagworte": gpt.get("schlagworte", []),
        "dokumenttyp": gpt.get("dokumenttyp", "nicht ermittelbar"),
        "personen": gpt.get("personen", []),
        "stellen": gpt.get("stellen", []),
        "orte": gpt.get("orte", []),
        "zeitraum": gpt.get("zeitraum", ""),
        "aktenzeichen": gpt.get("aktenzeichen", ""),
        "vorgangsbezug": gpt.get("vorgangsbezug", "nicht ermittelbar"),
        "bezuege": gpt.get("bezuege", ""),
    }


async def erschliesse_vorgang(job_id: str, arbeitsverzeichnis: Path) -> dict:
    """Erstellt Erschließungsinformationen **pro Einheit** auf der gewählten
    Ebene (Akte/Teilakte/Vorgang) per Sprachmodell.

    Für jede Einheit werden nur deren Dokumenttexte als Kontext genutzt, ein
    LLM-Aufruf erzeugt die Erschließung, und diese wird in genau das zugehörige
    XDOMEA-Element geschrieben.

    Returns:
        {"status", "ebene", "einheiten": [{einheit_id, aktenzeichen, betreff,
         titel, inhaltsangabe, schlagworte, …}]} — plus die Felder der ersten
        Einheit flach (Abwärtskompatibilität für ältere Aufrufer).
    """
    from utils.job_config import lese_bewertungsebene
    from utils.openai_client import lade_system_prompt, llm_anfrage
    from utils.protokoll import erstelle_protokoll
    from utils.text_extraktion import extrahiere_eine_datei, kuerze_intelligent
    from utils.xdomea_parser import (
        finde_xdomea_xmls,
        lese_einheiten_mit_element,
        schreibe_erschliessung_in_element,
    )
    from utils.zip_handler import entpacke_lv_bundle_falls_vorhanden

    meldungen: list[dict] = []

    try:
        from utils.agent_runner import sende_sse_event

        n_bundle = entpacke_lv_bundle_falls_vorhanden(arbeitsverzeichnis)
        if n_bundle:
            meldungen.append(
                {
                    "typ": "info",
                    "text": f"Lieferverzeichnis-Bundle erkannt — {n_bundle} innere Vorgangs-ZIPs entpackt.",
                }
            )
            await sende_sse_event(
                job_id, "erschliessung", "laufend", f"Bundle mit {n_bundle} Vorgängen entpackt", 5
            )

        # 1. Einheiten der gewählten Ebene bestimmen.
        ebene = lese_bewertungsebene(arbeitsverzeichnis.parent)
        xml_dateien = finde_xdomea_xmls(arbeitsverzeichnis)
        eintraege = lese_einheiten_mit_element(xml_dateien, ebene)
        if not eintraege:
            meldungen.append(
                {"typ": "warnung", "text": "Keine Einheiten zur Erschließung gefunden."}
            )
            erstelle_protokoll(job_id, "erschliessung", "fehler", meldungen, arbeitsverzeichnis)
            return {
                "status": "fehler",
                "ebene": ebene,
                "einheiten": [],
                **_leere_erschliessung("fehler"),
            }

        n = len(eintraege)
        await sende_sse_event(
            job_id, "erschliessung", "laufend", f"{n} Einheit(en) auf Ebene '{ebene}'", 8
        )
        meldungen.append(
            {"typ": "info", "text": f"{n} Einheit(en) werden auf Ebene '{ebene}' erschlossen."}
        )

        # 2. Texte aller Dateien einmal extrahieren (Basename -> Text), gecacht.
        import json as _json

        cache_pfad = arbeitsverzeichnis.parent / "datei_texte_cache.json"
        datei_texte: dict[str, str] = {}
        if cache_pfad.exists():
            try:
                datei_texte = _json.loads(cache_pfad.read_text(encoding="utf-8")) or {}
            except Exception:
                datei_texte = {}
        if not datei_texte:
            await sende_sse_event(job_id, "erschliessung", "laufend", "Extrahiere Texte...", 12)
            alle_dateien = [
                d
                for d in arbeitsverzeichnis.rglob("*")
                if d.is_file() and d.suffix.lower() != ".xml"
            ]

            def _extract_all():
                m: dict[str, str] = {}
                for d in alle_dateien:
                    try:
                        txt, _, _ = extrahiere_eine_datei(d)
                        m[d.name] = txt or ""
                    except Exception:
                        m[d.name] = ""
                return m

            loop = asyncio.get_event_loop()
            datei_texte = await loop.run_in_executor(None, _extract_all)
            try:
                cache_pfad.write_text(
                    _json.dumps(datei_texte, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

        # 3. Pro Einheit: Kontext bauen + Sprachmodell + ins Element schreiben.
        import os as _os

        sem = asyncio.Semaphore(max(1, int(_os.getenv("ERSCHLIESSUNG_LLM_PARALLEL", "2"))))
        fertig = {"n": 0}
        prompt = lade_system_prompt("erschliessung")

        async def _eine_einheit(idx: int, eintrag: dict) -> dict:
            einheit = eintrag["einheit"]
            az = einheit.get("aktenzeichen") or ""
            betreff = einheit.get("betreff") or ""
            dateinamen: list[str] = []
            for dok in einheit.get("dokumente", []) or []:
                dateinamen.extend(dok.get("alle_dateinamen", []) or [])
            texte = []
            for dn in dateinamen:
                t = datei_texte.get(Path(dn).name, "").strip()
                if t:
                    texte.append(t)
            kontext = (
                f"Aktenzeichen: {az}\nBetreff: {betreff}\n"
                f"Anzahl Dokumente: {len(einheit.get('dokumente', []) or [])}\n"
                f"=== DOKUMENTTEXTE ===\n" + "\n\n".join(texte)
            )
            if len(kontext) > 120_000:
                kontext = kuerze_intelligent(kontext, 120_000)
            async with sem:
                try:
                    gpt = await llm_anfrage(prompt, kontext, job_id, f"Erschliessung {idx + 1}/{n}")
                    erschl = _erschliessung_aus_gpt(gpt if isinstance(gpt, dict) else {})
                except Exception as e:
                    logger.warning("Erschliessung Einheit %s fehlgeschlagen: %s", az or betreff, e)
                    erschl = _erschliessung_aus_gpt({})
                    erschl["inhaltsangabe"] = (
                        "Erschliessung (Sprachmodell) nicht verfuegbar - bitte manuell ergaenzen."
                    )
            try:
                schreibe_erschliessung_in_element(eintrag["element"], erschl)
            except Exception as e:
                logger.warning("Erschliessung-Write Einheit %s: %s", az or betreff, e)
            fertig["n"] += 1
            await sende_sse_event(
                job_id,
                "erschliessung",
                "laufend",
                f"Einheit {fertig['n']}/{n} erschlossen: {betreff[:40] or az or '-'}",
                10 + int(80 * fertig["n"] / max(n, 1)),
            )
            return {
                "einheit_id": einheit["vorgang_id"],
                "aktenzeichen": az,
                "betreff": betreff,
                **erschl,
            }

        einheiten_ergebnis = await asyncio.gather(
            *(_eine_einheit(i, e) for i, e in enumerate(eintraege))
        )

        # 4. Geaenderte Baeume speichern (je Datei einmal).
        gespeichert: set[str] = set()
        for eintrag in eintraege:
            schluessel = str(eintrag["xml"])
            if schluessel in gespeichert:
                continue
            try:
                eintrag["baum"].write(
                    schluessel, pretty_print=True, xml_declaration=True, encoding="UTF-8"
                )
            except Exception as e:
                logger.warning("XML speichern fehlgeschlagen (%s): %s", schluessel, e)
            gespeichert.add(schluessel)

        erstelle_protokoll(job_id, "erschliessung", "ok", meldungen, arbeitsverzeichnis)
        erste = dict(einheiten_ergebnis[0]) if einheiten_ergebnis else _leere_erschliessung()
        erste.pop("einheit_id", None)
        return {"status": "ok", "ebene": ebene, "einheiten": list(einheiten_ergebnis), **erste}

    except Exception as e:
        logger.exception("[Job %s] Unerwarteter Fehler im Agent 'erschliessung': %s", job_id, e)
        meldungen.append({"typ": "fehler", "text": f"Unerwarteter Fehler: {e}"})
        erstelle_protokoll(job_id, "erschliessung", "fehler", meldungen, arbeitsverzeichnis)
        return {
            "status": "fehler",
            "ebene": "auto",
            "einheiten": [],
            **_leere_erschliessung("fehler"),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 5: Erschließung")
    parser.add_argument("--job-id", required=True, help="Job-ID")
    parser.add_argument("--pfad", default=".", help="Arbeitsverzeichnis (entpacktes ZIP)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    ergebnis = asyncio.run(erschliesse_vorgang(args.job_id, Path(args.pfad)))
    print(ergebnis)
