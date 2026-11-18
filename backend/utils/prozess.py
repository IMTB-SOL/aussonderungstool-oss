# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Hilfsfunktion für externe Prozesse (veraPDF, LibreOffice, Ghostscript, OCR)."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def kommuniziere_mit_timeout(
    prozess: asyncio.subprocess.Process, timeout: float
) -> tuple[bytes, bytes]:
    """Wartet auf das Prozessende und beendet den Prozess bei Zeitüberschreitung.

    `asyncio.wait_for(prozess.communicate(), …)` allein bricht nur das Warten
    ab — der Kindprozess läuft weiter. Hängende Konverter würden sich so
    ansammeln und Speicher und CPU binden.

    Args:
        prozess: Mit `asyncio.create_subprocess_exec` gestarteter Prozess.
        timeout: Maximale Laufzeit in Sekunden.

    Returns:
        (stdout, stderr) des Prozesses.

    Raises:
        asyncio.TimeoutError: Der Prozess wurde wegen Zeitüberschreitung beendet.
    """
    try:
        return await asyncio.wait_for(prozess.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            prozess.kill()
            await prozess.wait()
        except ProcessLookupError:
            pass  # Prozess hat sich zwischenzeitlich selbst beendet
        logger.warning("Externer Prozess nach %s s Zeitüberschreitung beendet.", timeout)
        raise
