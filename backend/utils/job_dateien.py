# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Dateiverwaltung im Job-Verzeichnis: Original schützen, Zwischenstände räumen.

Jeder Reparatur- und Konvertierungsschritt packt ein neues ZIP und löscht das
vorherige. Genau dieses Löschen macht das ursprünglich hochgeladene Paket
unwiederbringlich — und damit ein Zurücksetzen unmöglich. Die Funktionen hier
halten das Original heraus.
"""

import logging
import shutil
from pathlib import Path

from utils.logging_setup import pseudonymisiere_dateiname

logger = logging.getLogger(__name__)


def schuetze_original(zip_pfad: Path, original_zip: Path | str | None) -> bool:
    """Löscht ein abgelöstes ZIP — außer es ist das Original des Jobs.

    Args:
        zip_pfad: Das ZIP, das nach dem Neupacken abgelöst wurde.
        original_zip: Das ursprünglich hochgeladene ZIP des Jobs. None bei
            Jobs, die vor Einführung der Aufbewahrung angelegt wurden.

    Returns:
        True, wenn die Datei gelöscht wurde.
    """
    if not zip_pfad.exists():
        return False

    if original_zip is not None:
        original = Path(original_zip)
        try:
            if original.exists() and zip_pfad.samefile(original):
                logger.info("Original-ZIP behalten: %s", pseudonymisiere_dateiname(zip_pfad.name))
                return False
        except OSError:
            # Kann nicht verglichen werden — im Zweifel behalten.
            if zip_pfad.resolve() == original.resolve():
                return False

    zip_pfad.unlink()
    return True


def loesche_zwischenstaende(job_verzeichnis: Path, original_zip: Path | str | None) -> int:
    """Räumt ein Job-Verzeichnis auf den Zustand nach dem Upload zurück.

    Entfernt alle ZIPs außer dem Original, sämtliche Arbeitsverzeichnisse und
    die Job-Konfiguration. Das Original bleibt unangetastet.

    Returns:
        Anzahl entfernter Einträge.
    """
    entfernt = 0
    original = Path(original_zip).resolve() if original_zip else None

    for eintrag in sorted(job_verzeichnis.iterdir()):
        if eintrag.is_dir():
            if eintrag.name.startswith("arbeit_"):
                shutil.rmtree(eintrag, ignore_errors=True)
                entfernt += 1
            continue
        if eintrag.suffix.lower() == ".zip":
            if original is not None and eintrag.resolve() == original:
                continue
            eintrag.unlink(missing_ok=True)
            entfernt += 1
        elif eintrag.name == "job_config.json":
            eintrag.unlink(missing_ok=True)
            entfernt += 1

    logger.info(
        "Job-Verzeichnis aufgeräumt: %d Eintrag/Einträge entfernt (%s)",
        entfernt,
        pseudonymisiere_dateiname(job_verzeichnis.name),
    )
    return entfernt


def finde_dateien(verzeichnis: Path, name: str | None) -> list[Path]:
    """Sucht Dateien mit exakt diesem Dateinamen unterhalb von `verzeichnis`.

    Ersatz für `verzeichnis.rglob(name)` mit Namen aus Request, XML oder ZIP:
    rglob wertet `..` und Platzhalter (`*`, `?`, `[`) im Muster aus und würde
    damit Dateien außerhalb des Arbeitsverzeichnisses bzw. beliebig viele
    Dateien treffen. Hier zählt nur der reine Dateiname, verglichen wird exakt.

    Args:
        verzeichnis: Wurzel der Suche (Arbeitsverzeichnis des Jobs).
        name: Gesuchter Dateiname; ein etwaiger Pfadanteil wird verworfen.

    Returns:
        Sortierte Liste der Treffer; leer bei ungültigem Namen.
    """
    if not name:
        return []
    basis = Path(str(name).replace("\\", "/")).name
    if not basis or basis in (".", ".."):
        return []
    try:
        return sorted(p for p in verzeichnis.rglob("*") if p.name == basis and p.is_file())
    except OSError as e:
        logger.warning("Dateisuche fehlgeschlagen: %s", type(e).__name__)
        return []


def finde_datei(verzeichnis: Path, name: str | None) -> Path | None:
    """Wie `finde_dateien`, liefert aber nur den ersten Treffer oder None."""
    treffer = finde_dateien(verzeichnis, name)
    return treffer[0] if treffer else None
