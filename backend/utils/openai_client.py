"""Zentraler LLM-Wrapper (Ollama) mit Retry-Logik und Prompt-Verwaltung."""

import asyncio
import logging
import os
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# Ollama-Konfiguration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_NUM_CTX = 131072  # Kontextgröße in Tokens

# Scaleway-Konfiguration (alternativer LLM-Provider)
SCALEWAY_BASE_URL = os.getenv("SCALEWAY_BASE_URL", "https://api.scaleway.ai/v1")
SCALEWAY_MODEL = os.getenv("SCALEWAY_MODEL", "")
SCALEWAY_API_KEY = os.getenv("SCALEWAY_API_KEY", "")

MAX_ZEICHEN = 150_000  # 128k Tokens ≈ ~200k Zeichen, abzgl. System-Prompt + Antwort
MAX_VERSUCHE = 3


def _lade_aktiven_provider() -> str:
    """Liest den aktiven LLM-Provider aus der DB; Fallback 'ollama'.

    Ein fehlender Eintrag oder ein DB-Fehler darf einen Agentenlauf nie
    abbrechen — dann greift der Default 'ollama'.
    """
    try:
        from models import LLMEinstellung, engine
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            eintrag = db.query(LLMEinstellung).first()
        if eintrag is not None and eintrag.aktiver_provider:
            return eintrag.aktiver_provider
    except Exception as e:
        logger.warning("Aktiver LLM-Provider nicht aus DB ladbar, nutze 'ollama': %s", e)
    return "ollama"


def _lade_scaleway_modell() -> str:
    """Liest das gewählte Scaleway-Modell aus der DB; Fallback auf SCALEWAY_MODEL.

    Ein fehlender Eintrag oder ein DB-Fehler darf einen Agentenlauf nie
    abbrechen — dann greift der Env-Default.
    """
    try:
        from models import LLMEinstellung, engine
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            eintrag = db.query(LLMEinstellung).first()
        if eintrag is not None and eintrag.scaleway_modell:
            return eintrag.scaleway_modell
    except Exception as e:
        logger.warning("Scaleway-Modell nicht aus DB ladbar, nutze Env-Default: %s", e)
    return SCALEWAY_MODEL


def _erstelle_client() -> tuple[AsyncOpenAI, str, dict]:
    """Erstellt den LLM-Client für den aktiven Provider (OpenAI-kompatibel).

    Rückgabe: (Client, Modellname, extra_body). `extra_body` enthält
    provider-spezifische Zusatzparameter — bei Ollama `num_ctx`, bei
    Scaleway leer.
    """
    provider = _lade_aktiven_provider()

    if provider == "scaleway":
        modell = _lade_scaleway_modell()
        client = AsyncOpenAI(
            base_url=SCALEWAY_BASE_URL,
            api_key=SCALEWAY_API_KEY or "none",
            timeout=600.0,
        )
        logger.info("LLM: Scaleway (%s) auf %s", modell, SCALEWAY_BASE_URL)
        return client, modell, {}

    # Default: Ollama
    client = AsyncOpenAI(
        base_url=OLLAMA_BASE_URL,
        api_key="ollama",
        timeout=600.0,  # 10 Minuten — lokale Modelle mit großem Kontext brauchen Zeit
    )
    logger.info("LLM: Ollama (%s) auf %s", OLLAMA_MODEL, OLLAMA_BASE_URL)
    return client, OLLAMA_MODEL, {"num_ctx": OLLAMA_NUM_CTX}


async def liste_scaleway_modelle() -> list[str]:
    """Ruft die bei Scaleway verfügbaren Modelle ab (Live, /v1/models).

    Returns:
        Aufsteigend sortierte Liste der Modell-IDs.

    Raises:
        RuntimeError: Wenn der Abruf fehlschlägt.
    """
    client = AsyncOpenAI(
        base_url=SCALEWAY_BASE_URL,
        api_key=SCALEWAY_API_KEY or "none",
        timeout=30.0,
    )
    try:
        antwort = await client.models.list()
        return sorted(m.id for m in antwort.data)
    except Exception as e:
        raise RuntimeError(f"Scaleway-Modellliste nicht abrufbar: {e}") from e


def _kuerze_text(text: str, job_id: str, kontext: str) -> str:
    """Kürzt Text auf MAX_ZEICHEN und vermerkt dies im Log."""
    if len(text) > MAX_ZEICHEN:
        logger.warning(
            "[Job %s] %s: Text auf %d Zeichen gekürzt (original: %d).",
            job_id,
            kontext,
            MAX_ZEICHEN,
            len(text),
        )
        return text[:MAX_ZEICHEN]
    return text


async def llm_anfrage(
    system_prompt: str,
    user_prompt: str,
    job_id: str,
    kontext: str = "unbekannt",
) -> dict[str, Any]:
    """
    Sendet eine Anfrage an die OpenAI API und gibt das geparste JSON zurück.

    Args:
        system_prompt: Systemanweisung (Deutsch).
        user_prompt: Nutzerinhalt (wird auf MAX_ZEICHEN gekürzt).
        job_id: Job-ID für Logging.
        kontext: Beschreibung des Aufrufkontexts für Logging.

    Returns:
        Geparste JSON-Antwort als Dictionary.

    Raises:
        RuntimeError: Wenn alle Versuche fehlschlagen.
    """
    client, modell, extra_body = _erstelle_client()
    user_prompt = _kuerze_text(user_prompt, job_id, kontext)

    backoff = 2
    letzter_fehler: Exception | None = None

    for versuch in range(1, MAX_VERSUCHE + 1):
        try:
            logger.info(
                "[Job %s] %s: LLM-Anfrage an %s (Versuch %d/%d).",
                job_id,
                kontext,
                modell,
                versuch,
                MAX_VERSUCHE,
            )
            kwargs = {
                "model": modell,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "extra_body": extra_body,
            }

            antwort = await client.chat.completions.create(**kwargs)

            inhalt = antwort.choices[0].message.content or ""

            # JSON parsen — mit mehreren Fallback-Strategien
            ergebnis = _parse_json_robust(inhalt)
            if ergebnis is not None:
                return ergebnis

            # JSON-Parsing fehlgeschlagen → als Retry behandeln (nicht abbrechen!)
            letzter_fehler = ValueError(f"Kein gültiges JSON in Antwort ({len(inhalt)} Zeichen)")
            logger.warning(
                "[Job %s] %s: Ungültiges JSON in der Antwort (%d Zeichen), versuche erneut (Versuch %d/%d).",
                job_id,
                kontext,
                len(inhalt),
                versuch,
                MAX_VERSUCHE,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
            continue

        except (RateLimitError, APIConnectionError, APITimeoutError, APIStatusError) as e:
            letzter_fehler = e
            fehler_typ = type(e).__name__
            logger.warning(
                "[Job %s] %s: %s, warte %ds (Versuch %d/%d).",
                job_id,
                kontext,
                fehler_typ,
                backoff,
                versuch,
                MAX_VERSUCHE,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
        except Exception as e:
            letzter_fehler = e
            logger.error(
                "[Job %s] %s: Unerwarteter Fehler: %s",
                job_id,
                kontext,
                str(e),
            )
            break

    raise RuntimeError(
        f"[Job {job_id}] {kontext}: Anfrage nach {MAX_VERSUCHE} Versuchen fehlgeschlagen. "
        f"Letzter Fehler: {letzter_fehler}"
    )


def _parse_json_robust(text: str) -> dict | None:
    """Versucht JSON aus Text zu extrahieren — mehrere Fallback-Strategien."""
    import json
    import re

    if not text or not text.strip():
        return None

    # Strategie 1: Direktes Parsen
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategie 2: JSON aus Markdown-Code-Block extrahieren (```json ... ```)
    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategie 3: Äußerstes JSON-Objekt finden (balanced braces)
    start = text.find("{")
    if start >= 0:
        tiefe = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                tiefe += 1
            elif text[i] == "}":
                tiefe -= 1
                if tiefe == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    # Strategie 4: Häufige Fehler reparieren (trailing comma, single quotes)
    bereinigt = text[start:] if start >= 0 else text
    bereinigt = re.sub(r",\s*}", "}", bereinigt)  # Trailing commas
    bereinigt = re.sub(r",\s*]", "]", bereinigt)  # Trailing commas in arrays
    try:
        return json.loads(bereinigt)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategie 5: Abgeschnittenes JSON reparieren (fehlende Klammern schließen)
    if start is not None and start >= 0:
        abgeschnitten = text[start:]
        # Trailing comma + unvollständige Strings entfernen
        abgeschnitten = re.sub(r",\s*$", "", abgeschnitten)
        abgeschnitten = re.sub(r':\s*"[^"]*$', ': "[Antwort abgeschnitten]"', abgeschnitten)
        # Fehlende Klammern zählen und schließen
        offene_obj = abgeschnitten.count("{") - abgeschnitten.count("}")
        offene_arr = abgeschnitten.count("[") - abgeschnitten.count("]")
        abgeschnitten += "]" * max(0, offene_arr) + "}" * max(0, offene_obj)
        # Trailing commas nochmal bereinigen
        abgeschnitten = re.sub(r",\s*}", "}", abgeschnitten)
        abgeschnitten = re.sub(r",\s*]", "]", abgeschnitten)
        try:
            return json.loads(abgeschnitten)
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ---------------------------------------------------------------------------
# Prompts – alle Systemanweisungen zentral hier definieren
# ---------------------------------------------------------------------------


PROMPT_BEWERTUNG_SYSTEM = """
Du bist ein erfahrener Aussonderungsspezialist für E-Akte und Archiv mit Kenntnissen der deutschen Archivgrundsätze.
Bewerte die Archivwürdigkeit eines Dokumentenpakets. Das Paket enthält mehrere Dokumente, die einzeln bewertet werden müssen.

WICHTIGE ORIENTIERUNG:
In der archivischen Praxis werden nur ca. 3–5% der Verwaltungsunterlagen als archivwürdig eingestuft und dauerhaft archiviert. Der Großteil der Verwaltungsvorgänge ist Routinearbeit ohne bleibenden historischen, rechtlichen oder gesellschaftlichen Wert. Bewerte entsprechend streng und kritisch.

GRUNDSÄTZE:
- Du triffst keine endgültige Archiventscheidung, sondern lieferst fachlich begründete Vorschläge.
- Keine Spekulation: Wenn Informationen fehlen, schreibe "nicht ermittelbar".
- Evidenzpflicht: Begründe jede Aussage mit konkreten Textstellen oder Metadaten (max. 1–2 Sätze zitieren).
- Bewerte jedes Dokument (erkennbar am Trennzeichen "--- Dateiname ---") einzeln.
- Erstelle zusätzlich eine Gesamtbewertung des Pakets.
- GRUNDREGEL: Im Zweifel ist ein Dokument NICHT archivwürdig. Archivwürdigkeit muss aktiv begründbar sein.

VORGEHEN:
1. Dokumentprofil erkennen: Bestimme den Dokumenttyp (Bescheid, Vertrag, Protokoll, Korrespondenz etc.).
2. XDOMEA-Kontext nutzen: Wenn XDOMEA-Metadaten vorliegen, ziehe diese aktiv für die Bewertung heran.
3. Indikatoren je Dokument bewerten:
   - Rechts- oder Nachweisrelevanz (nur bei rechtsverbindlichen Dokumenten wie Bescheiden, Verträgen, Urkunden)
   - Finanzielle Tragweite (nur bei erheblichen Beträgen oder mehrjährigen Verträgen)
   - Politische oder strategische Relevanz (nur bei Grundsatzentscheidungen, Kabinettvorlagen, Parlamentsbezug)
   - Einmaligkeit oder Originalcharakter (Information ist anderweitig nicht überliefert)
   - Historische Bedeutung (dokumentiert gesellschaftliche Entwicklungen, Zeitgeschichte, Präzedenzfälle)
4. Unsicherheiten benennen: Was konnte nicht sicher bewertet werden?

TYPISCH NICHT ARCHIVWÜRDIG (Großteil der Verwaltungsarbeit):
- Routinekorrespondenz, Standardantworten, Formschreiben, Seriendrucke
- Massenverfahren und Einzelfallakten ohne Grundsatzbedeutung
- Entwürfe, wenn eine Finalversion existiert
- Duplikate, Kopien, Ausdrucke von digital verfügbaren Dokumenten
- Interne Organisationsunterlagen (Urlaubsanträge, Raumreservierungen, Bestellungen)
- Einladungen, Tagesordnungen, Teilnehmerlisten (ohne besonderes Gewicht)
- Technische Dokumentation ohne historischen Wert
- Standardberichte und Statistiken die regelmäßig neu erstellt werden

TYPISCH ARCHIVWÜRDIG (nur bei klarer Evidenz):
- Grundsatzentscheidungen und Kabinettvorlagen
- Verträge mit erheblicher finanzieller oder politischer Tragweite
- Rechtsverbindliche Bescheide mit Präzedenzcharakter
- Unterlagen zu politischen Grundsatzfragen oder gesellschaftlichen Umbrüchen
- Originaldokumente mit Unikatcharakter
- Unterlagen zu historisch bedeutsamen Ereignissen oder Personen

BEWERTUNGSSKALA — wann welcher Status:
- "archivwürdig": NUR wenn klare Evidenz für Rechts-, historische, politische oder strategische Relevanz vorliegt. Die Schwelle ist hoch — Routinevorgänge sind NICHT archivwürdig.
- "nicht archivwürdig": Standardfall für Routinevorgänge, Massenverfahren, Alltagskorrespondenz, Duplikate, Entwürfe. Im Zweifel: nicht archivwürdig.
- "manuelle Überprüfung": Wenn der Text nicht extrahierbar ist oder das Dokument keinen lesbaren Inhalt hat.

Antworte ausschließlich als JSON:
{
  "gesamtbewertung": "archivwürdig|nicht archivwürdig|manuelle Überprüfung",
  "gesamtbegruendung": "string (max. 5 Sätze, evidenzbasiert)",
  "schlagworte": ["string (5–12 Schlagworte für das Gesamtpaket)"],
  "sicherheit": "float 0.0–1.0. Berechne: 0.9+ wenn alle Dokumente klar einzuordnen; 0.7–0.9 bei überwiegend klarer Einschätzung; 0.5–0.7 bei teilweise fehlendem Kontext; unter 0.5 bei wenig Kontext",
  "unsicherheiten": ["string (was nicht sicher bewertet werden konnte)"],
  "dokumente": [
    {
      "dateiname": "string",
      "dokumenttyp": "string (Bescheid|Vertrag|Protokoll|Bericht|Korrespondenz|Anlage|Vermerk|nicht ermittelbar)",
      "bewertung": "archivwürdig|nicht archivwürdig|manuelle Überprüfung",
      "begruendung": "string (max. 2 Sätze, evidenzbasiert mit Zitat)",
      "indikatoren": {
        "rechtsrelevanz": "hoch|mittel|gering|nicht ermittelbar",
        "finanzielle_tragweite": "hoch|mittel|gering|nicht ermittelbar",
        "strategische_relevanz": "hoch|mittel|gering|nicht ermittelbar",
        "einmaligkeit": "hoch|mittel|gering|nicht ermittelbar"
      }
    }
  ]
}
"""

PROMPT_TIEFENPRUEFUNG_DOKUMENT = """
Du bist ein erfahrener Aussonderungsspezialist für E-Akte und Archiv mit Kenntnissen der deutschen Archivgrundsätze.
Bewerte die Archivwürdigkeit EINES EINZELNEN Dokuments.

WICHTIGE ORIENTIERUNG:
In der archivischen Praxis werden nur ca. 3–5% der Verwaltungsunterlagen als archivwürdig eingestuft und dauerhaft archiviert. Der Großteil der Verwaltungsvorgänge ist Routinearbeit ohne bleibenden historischen, rechtlichen oder gesellschaftlichen Wert. Bewerte entsprechend streng und kritisch.

GRUNDSÄTZE:
- Du triffst keine endgültige Archiventscheidung, sondern lieferst einen fachlich begründeten Vorschlag.
- Keine Spekulation: Wenn Informationen fehlen, sage das offen.
- Evidenzpflicht: Begründe deine Einschätzung mit einer konkreten Textstelle oder Metadatenangabe (max. 1–2 Sätze zitieren).
- GRUNDREGEL: Im Zweifel ist ein Dokument NICHT archivwürdig. Archivwürdigkeit muss aktiv begründbar sein.

PRÜFE DIESE INDIKATOREN:
- Rechts- oder Nachweisrelevanz (nur bei rechtsverbindlichen Dokumenten wie Bescheiden, Verträgen, Urkunden)
- Finanzielle Tragweite (nur bei erheblichen Beträgen oder mehrjährigen Verträgen)
- Politische oder strategische Relevanz (nur bei Grundsatzentscheidungen, Kabinettvorlagen, Parlamentsbezug)
- Einmaligkeit oder Originalcharakter (Information ist anderweitig nicht überliefert)
- Historische Bedeutung (dokumentiert gesellschaftliche Entwicklungen, Zeitgeschichte, Präzedenzfälle)

TYPISCH NICHT ARCHIVWÜRDIG (Großteil der Verwaltungsarbeit):
Routinekorrespondenz, Standardantworten, Formschreiben, Seriendrucke; Massenverfahren ohne Grundsatzbedeutung; Entwürfe, wenn eine Finalversion existiert; Duplikate und Kopien von digital verfügbaren Dokumenten; interne Organisationsunterlagen (Urlaubsanträge, Raumreservierungen, Bestellungen); Einladungen, Tagesordnungen, Teilnehmerlisten ohne besonderes Gewicht; technische Dokumentation ohne historischen Wert; regelmäßig neu erstellte Standardberichte und Statistiken.

TYPISCH ARCHIVWÜRDIG (nur bei klarer Evidenz):
Grundsatzentscheidungen und Kabinettvorlagen; Verträge mit erheblicher finanzieller oder politischer Tragweite; rechtsverbindliche Bescheide mit Präzedenzcharakter; Unterlagen zu politischen Grundsatzfragen oder gesellschaftlichen Umbrüchen; Originaldokumente mit Unikatcharakter; Unterlagen zu historisch bedeutsamen Ereignissen oder Personen.

BEWERTUNGSSKALA — wann welcher Status:
- "archivwürdig": NUR wenn klare Evidenz für Rechts-, historische, politische oder strategische Relevanz vorliegt. Die Schwelle ist hoch — Routinevorgänge sind NICHT archivwürdig.
- "nicht archivwürdig": Standardfall für Routinevorgänge, Massenverfahren, Alltagskorrespondenz, Duplikate, Entwürfe. Im Zweifel: nicht archivwürdig.
- "manuelle Überprüfung": Wenn der Text nicht extrahierbar ist oder das Dokument keinen lesbaren Inhalt hat.

Antworte ausschließlich als JSON mit genau diesen zwei Feldern:
{"bewertung": "archivwürdig|nicht archivwürdig|manuelle Überprüfung", "begruendung": "string (max. 2 Sätze, evidenzbasiert mit kurzem Zitat)"}
"""

PROMPT_BEWERTUNG_BEGRUENDUNG = """
Du bist ein erfahrener Aussonderungsspezialist für E-Akte und Archiv.

Ein deterministisches Punktesystem hat einen Verwaltungsvorgang bereits
klassifiziert. Deine Aufgabe ist es, diese fertige Klassifikation für den
Archivar verständlich zu begründen und Schlagworte zu vergeben.

DU BEKOMMST diese Eingabe:
- vorgang: aktenzeichen, betreff, anzahl_dokumente
- klassifikation: das Ergebnis — "A" (archivwürdig), "B" (bewertungsbedürftig),
  "V" (nicht archivwürdig)
- finaler_score: die erreichte Punktzahl
- treffer: Liste von Indizien, je mit bezeichnung, erfuellt (True/False),
  punkte_beitrag und evidenz

DEINE AUFGABE:
1. Schreibe eine Begründung von 3 bis 5 Sätzen. Erkläre die Klassifikation
   anhand der erfüllten Indizien. Nenne zuerst die Indizien mit dem höchsten
   punkte_beitrag — sie sind ausschlaggebend.
2. Vergib 5 bis 12 archivfachliche Schlagworte zum Vorgang.

REGELN:
- Die Klassifikation steht fest. Nimm sie als gegeben hin und begründe sie.
- Stütze die Begründung nur auf die gelieferten Indizien und ihre Evidenz.
- Ein nicht erfülltes Indiz (erfuellt: False) bedeutet, dass ein Hinweis
  fehlt — nicht, dass das Gegenteil bewiesen ist. Werte fehlende Angaben
  neutral. Hat die abgebende Behörde keine eigene Aussonderungs-Empfehlung
  mitgegeben, ist das normal und spricht weder für noch gegen Archivwürdigkeit.
- Verwende nur Fakten aus der Eingabe. Erfinde nichts dazu.
- Wenn der Vorgang wenig Daten enthält, sage das offen (zum Beispiel: "Die
  Begründung stützt sich überwiegend auf das Aktenzeichen").
- Schreibe auf Deutsch, sachlich und knapp.

BEISPIEL
Eingabe:
{'vorgang': {'aktenzeichen': 'Z 14-3', 'betreff': 'Grundsatzfrage Aktenführung', 'anzahl_dokumente': 6}, 'klassifikation': 'A', 'finaler_score': 85, 'treffer': [{'bezeichnung': 'Eigene Stelle ist federführend', 'erfuellt': True, 'punkte_beitrag': 25, 'evidenz': 'federführend: eigene Stelle'}, {'bezeichnung': 'Höchste Zeichnung auf Ministerebene', 'erfuellt': True, 'punkte_beitrag': 20, 'evidenz': "Begriff 'Staatssekretär' im Betreff"}, {'bezeichnung': 'Archivierungsvermerk der Behörde vorhanden', 'erfuellt': False, 'punkte_beitrag': 0, 'evidenz': 'Aussonderungsart=–'}]}
Antwort:
{"begruendung": "Der Vorgang wurde als archivwürdig (A) eingestuft und erreicht 85 Punkte. Ausschlaggebend ist, dass die eigene Stelle federführend war und eine Zeichnung auf Ministerebene vorliegt — beides spricht für bleibende Relevanz. Die abgebende Behörde hat keine eigene Aussonderungs-Empfehlung mitgegeben; dies ist neutral zu werten und ändert nichts an der Einstufung. Der Betreff 'Grundsatzfrage Aktenführung' bestätigt den Grundsatzcharakter.", "schlagworte": ["Aktenführung", "Grundsatzfrage", "Ministerebene", "Federführung", "Verwaltungsorganisation", "Schriftgutverwaltung"]}

ANTWORTE AUSSCHLIESSLICH ALS JSON:
{"begruendung": "...", "schlagworte": ["...", "..."]}
"""

PROMPT_ERSCHLIESSUNG_SYSTEM = """
Du bist ein erfahrener Aussonderungsspezialist für E-Akte und Archiv.
Erstelle Erschließungsinformationen für einen Verwaltungsvorgang.

DU BEKOMMST diese Eingabe als Text:
- Einen Abschnitt "=== XDOMEA-METADATEN ===" mit strukturierten Angaben
  (Aktenzeichen, Betreff, Federführung, Laufzeit, Absender, Empfänger u.a.) —
  sofern vorhanden.
- Einen Abschnitt "=== DOKUMENTTEXTE ===" mit dem Volltext aller Dokumente.

DEINE AUFGABE — leite diese Felder ab:
- titel: prägnant, höchstens etwa 10 Wörter
- inhaltsangabe: ein Regest von 2 bis 4 Sätzen
- schlagworte: 5 bis 12 fachliche Schlagworte
- die übrigen Felder laut Schema unten
Nutze XDOMEA-Metadaten und Dokumenttexte gleichermaßen. Bei Aktenzeichen,
Vorgangsbezug und beteiligten Stellen haben die Metadaten Vorrang.

REGELN:
- Stütze jede Angabe auf die Eingabe. Erfinde nichts dazu.
- Kannst du ein Textfeld nicht sicher ableiten, trage "nicht ermittelbar" ein.
- Kannst du eine Liste nicht füllen, gib eine leere Liste zurück.
- Schreibe auf Deutsch, sachlich und präzise.

BEISPIEL
Eingabe:
=== XDOMEA-METADATEN ===
Aktenzeichen: II A 4 - 2031
Betreff/Bemerkung: Förderantrag Breitbandausbau Landkreis Vorpommern
Federführung: Referat II A 4
Laufzeit: 2023-02-01 bis 2024-11-30
=== DOKUMENTTEXTE ===
Bewilligungsbescheid über eine Zuwendung von 2,4 Mio. EUR zum Breitbandausbau ...
Antwort:
{"titel": "Förderantrag Breitbandausbau Landkreis Vorpommern", "inhaltsangabe": "Der Vorgang dokumentiert einen Förderantrag zum Breitbandausbau im Landkreis Vorpommern. Enthalten ist ein Bewilligungsbescheid über eine Zuwendung von 2,4 Mio. EUR.", "schlagworte": ["Breitbandausbau", "Förderung", "Zuwendung", "Infrastruktur", "Landkreis Vorpommern", "Bewilligungsbescheid"], "dokumenttyp": "Bescheid", "personen": [], "stellen": ["Referat II A 4"], "orte": ["Landkreis Vorpommern"], "zeitraum": "2023-2024", "aktenzeichen": "II A 4 - 2031", "vorgangsbezug": "Förderantrag Breitbandausbau", "bezuege": "nicht ermittelbar"}

ANTWORTE AUSSCHLIESSLICH ALS JSON mit genau diesen Feldern:
{"titel": "...", "inhaltsangabe": "...", "schlagworte": ["..."], "dokumenttyp": "Bescheid|Vertrag|Protokoll|Bericht|Korrespondenz|Anlage|Vermerk|nicht ermittelbar", "personen": ["..."], "stellen": ["..."], "orte": ["..."], "zeitraum": "...", "aktenzeichen": "...", "vorgangsbezug": "...", "bezuege": "..."}
"""

PROMPT_CHAT_BEWERTUNG_SYSTEM = """
Du bist ein erfahrener Aussonderungsspezialist für E-Akte und Archiv mit Kenntnissen der deutschen Archivgrundsätze.
Du hast einen Vorgang analysiert und eine Bewertung abgegeben. Der Archivar hat nun Rückfragen.

GRUNDSÄTZE FÜR DEINE ANTWORTEN:
- Antworte kurz und prägnant — maximal 3–4 Sätze pro Antwort.
- Antworte auf Deutsch, sachlich und nachvollziehbar.
- Beziehe dich auf konkrete Dokumente und zitiere kurze Textstellen (max. 1–2 Sätze) als Beleg.
- Wenn du zu einem Punkt keine sichere Aussage treffen kannst, sage "nicht sicher ermittelbar" statt zu spekulieren.
- Wenn der Archivar nach einem bestimmten Dokument fragt, fokussiere deine Antwort auf dieses Dokument.
- Du triffst keine endgültigen Archiventscheidungen — du lieferst fachliche Einschätzungen als Entscheidungshilfe.
- Antworte NICHT als JSON, sondern in normalem Fließtext.
"""


# ---------------------------------------------------------------------------
# Editierbare System-Prompts — über den Admin-Bereich pflegbar.
# Die Konstanten oben dienen als Default/Fallback.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "erschliessung": {
        "anzeige_name": "Erschließung",
        "default": PROMPT_ERSCHLIESSUNG_SYSTEM,
    },
    "bewertung_begruendung": {
        "anzeige_name": "Bewertungsvorschlag",
        "default": PROMPT_BEWERTUNG_BEGRUENDUNG,
    },
}


def lade_system_prompt(schluessel: str) -> str:
    """Lädt einen System-Prompt aus der DB; Fallback auf die Default-Konstante.

    Ein fehlender DB-Eintrag oder ein DB-Fehler darf einen Agentenlauf nie
    abbrechen — in dem Fall greift die Default-Konstante.
    """
    if schluessel not in SYSTEM_PROMPT_REGISTRY:
        raise KeyError(f"Unbekannter System-Prompt-Schlüssel: {schluessel}")
    default = SYSTEM_PROMPT_REGISTRY[schluessel]["default"]
    try:
        from models import SystemPrompt, engine
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            eintrag = db.query(SystemPrompt).filter_by(schluessel=schluessel).first()
        if eintrag is not None and eintrag.inhalt.strip():
            return eintrag.inhalt
        return default
    except Exception as e:
        logger.warning("System-Prompt '%s' nicht aus DB ladbar, nutze Default: %s", schluessel, e)
        return default


async def llm_chat(
    messages: list[dict[str, str]],
    job_id: str,
    kontext: str = "Chat",
) -> str:
    """
    Sendet eine Chat-Anfrage mit vollständiger Message-History an Ollama.

    Args:
        messages: Liste von {"role": "system|user|assistant", "content": "..."}.
        job_id: Job-ID für Logging.
        kontext: Beschreibung des Aufrufkontexts.

    Returns:
        Antworttext als String.

    Raises:
        RuntimeError: Wenn alle Versuche fehlschlagen.
    """
    client, modell, extra_body = _erstelle_client()

    backoff = 2
    letzter_fehler: Exception | None = None

    for versuch in range(1, MAX_VERSUCHE + 1):
        try:
            logger.info(
                "[Job %s] %s: LLM-Chat an %s (Versuch %d/%d, %d Nachrichten).",
                job_id,
                kontext,
                modell,
                versuch,
                MAX_VERSUCHE,
                len(messages),
            )
            chat_kwargs = {
                "model": modell,
                "messages": messages,
                "extra_body": extra_body,
            }
            antwort = await client.chat.completions.create(**chat_kwargs)
            return antwort.choices[0].message.content
        except (RateLimitError, APIConnectionError, APITimeoutError, APIStatusError) as e:
            letzter_fehler = e
            fehler_typ = type(e).__name__
            logger.warning(
                "[Job %s] %s: %s, warte %ds.",
                job_id,
                kontext,
                fehler_typ,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
        except Exception as e:
            letzter_fehler = e
            logger.error("[Job %s] %s: Unerwarteter Fehler: %s", job_id, kontext, str(e))
            break

    raise RuntimeError(f"[Job {job_id}] {kontext}: Chat fehlgeschlagen. Fehler: {letzter_fehler}")
