"""Erstellt Protokoll-XML-Dateien für jeden Agenten-Lauf."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)


def erstelle_protokoll(
    job_id: str,
    agent: str,
    status: str,
    meldungen: list[dict],
    ziel_verzeichnis: Path,
) -> Path:
    """
    Erstellt ein Protokoll-XML für einen Agenten-Lauf.

    Args:
        job_id: Job-ID.
        agent: Name des Agenten (z.B. "formal", "format").
        status: Gesamtstatus ("ok" oder "fehler").
        meldungen: Liste von {"typ": "info|warnung|fehler", "text": "..."}.
        ziel_verzeichnis: Verzeichnis, in dem das XML gespeichert wird.

    Returns:
        Pfad zur erstellten Protokolldatei.

    Raises:
        RuntimeError: Bei Schreibfehlern.
    """
    try:
        wurzel = etree.Element("Protokoll")
        wurzel.set("jobId", job_id)
        wurzel.set("agent", agent)
        wurzel.set("status", status)
        wurzel.set("erstellt", datetime.now(timezone.utc).isoformat())

        for meldung in meldungen:
            el = etree.SubElement(wurzel, "Meldung")
            el.set("typ", meldung.get("typ", "info"))
            el.text = meldung.get("text", "")

        baum = etree.ElementTree(wurzel)
        dateiname = f"protokoll_{agent}.xml"
        ziel_pfad = ziel_verzeichnis / dateiname
        baum.write(
            str(ziel_pfad),
            pretty_print=True,
            xml_declaration=True,
            encoding="UTF-8",
        )
        logger.info("Protokoll erstellt: %s", ziel_pfad)
        return ziel_pfad
    except Exception as e:
        raise RuntimeError(f"Fehler beim Erstellen des Protokolls für Agent '{agent}': {e}") from e


def ergaenze_protokoll(
    agent: str,
    status: str,
    meldungen: list[dict],
    ziel_verzeichnis: Path,
) -> Path:
    """
    Ergaenzt ein bestehendes Protokoll-XML um neue Meldungen (z.B. nach Reparatur).
    Bestehende Meldungen bleiben erhalten. Wenn kein Protokoll existiert, wird ein
    neues erstellt.

    Args:
        agent: Name des Agenten.
        status: Neuer Gesamtstatus.
        meldungen: Neue Meldungen die angehaengt werden.
        ziel_verzeichnis: Verzeichnis mit dem Protokoll.

    Returns:
        Pfad zur aktualisierten Protokolldatei.
    """
    dateiname = f"protokoll_{agent}.xml"
    ziel_pfad = ziel_verzeichnis / dateiname

    try:
        if ziel_pfad.exists():
            from utils.xdomea_parser import sicher_parsen

            baum = sicher_parsen(ziel_pfad)
            wurzel = baum.getroot()

            # Status aktualisieren
            wurzel.set("status", status)

            # Trennlinie als Marker einfuegen
            trenner = etree.SubElement(wurzel, "Meldung")
            trenner.set("typ", "info")
            trenner.text = (
                f"── Reparatur ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}) ──"
            )

            # Neue Meldungen anhaengen
            for meldung in meldungen:
                el = etree.SubElement(wurzel, "Meldung")
                el.set("typ", meldung.get("typ", "info"))
                el.text = meldung.get("text", "")

            baum.write(
                str(ziel_pfad),
                pretty_print=True,
                xml_declaration=True,
                encoding="UTF-8",
            )
            logger.info("Protokoll ergaenzt: %s (+%d Meldungen)", ziel_pfad, len(meldungen))
        else:
            # Kein bestehendes Protokoll — neues erstellen
            return erstelle_protokoll("", agent, status, meldungen, ziel_verzeichnis)

        return ziel_pfad
    except Exception as e:
        logger.warning("Protokoll-Ergaenzung fehlgeschlagen (%s): %s", ziel_pfad, e)
        # Fallback: neues Protokoll erstellen
        return erstelle_protokoll("", agent, status, meldungen, ziel_verzeichnis)
