"""Strukturiertes JSON-Logging — eine Logzeile ist ein JSON-Objekt auf stdout."""

import json
import logging
import re
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Formatiert Log-Records als einzeilige JSON-Objekte."""

    def format(self, record: logging.LogRecord) -> str:
        eintrag = {
            "zeit": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            eintrag["exception"] = self.formatException(record.exc_info)
        return json.dumps(eintrag, ensure_ascii=False)


# Query-Werte, die personenbezogene Daten enthalten können (Dateinamen,
# Vorgangstitel etc.) und deshalb in Access-Logs maskiert werden müssen.
_PII_QUERY_KEYS = ("name", "datei", "titel", "filename", "pfad", "path", "suche")
_QUERY_MASK = re.compile(
    rf"\b({'|'.join(_PII_QUERY_KEYS)})=[^&\"\s]+",
    flags=re.IGNORECASE,
)


class _AccessLogPIIFilter(logging.Filter):
    """Entfernt potenziell personenbezogene Query-Parameter aus uvicorn-Access-Logs.

    uvicorn.access loggt die komplette HTTP-Request-Line samt Query-String.
    Dateinamen aus Archiv-Akten landen darüber sonst im Klartext in Loki.
    Der Filter ersetzt bekannte Schlüssel durch '<key>=<redacted>'.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.args = tuple(
                    _QUERY_MASK.sub(lambda m: f"{m.group(1)}=<redacted>", str(a))
                    if isinstance(a, str)
                    else a
                    for a in record.args
                )
            except Exception:
                # Filter darf nie eine Log-Ausgabe verhindern.
                pass
        return True


def konfiguriere_logging(level: int = logging.INFO) -> None:
    """Richtet strukturiertes JSON-Logging auf stdout ein.

    Konfiguriert den Root-Logger mit dem JsonFormatter und biegt zusätzlich
    die uvicorn-eigenen Logger an den Root-Logger um — sonst würden uvicorns
    Lifecycle- und Access-Logs weiterhin als Klartext ausgegeben.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    wurzel = logging.getLogger()
    wurzel.handlers.clear()
    wurzel.setLevel(level)
    wurzel.addHandler(handler)

    # uvicorn richtet eigene Logger mit Klartext-Handlern und propagate=False
    # ein. Deren Handler entfernen und die Logger an den Root weiterreichen,
    # damit auch uvicorn-Logs als JSON auf stdout erscheinen. konfiguriere_logging()
    # läuft beim App-Import — also nach uvicorns eigener Logging-Konfiguration.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # PII-Filter für uvicorn.access (Query-String-Werte maskieren).
    pii_filter = _AccessLogPIIFilter()
    logging.getLogger("uvicorn.access").addFilter(pii_filter)
    # Filter auch am Root anhängen, damit der Format-String der formatierten
    # Nachricht maskiert wird, falls Records durchpropagieren.
    handler.addFilter(pii_filter)


def pseudonymisiere_dateiname(name: str | None) -> str:
    """Gibt eine pseudonymisierte Kennung für einen Dateinamen zurück.

    Dateinamen von Archivdokumenten können personenbezogene Daten enthalten
    (Klarnamen) und dürfen nicht im Klartext in Logs erscheinen.
    Akzeptiert auch einen vollständigen Pfad — es wird nur der Basisname
    verwendet. Rückgabe: Dateiendung + stabiler Kurz-Hash, z. B. "<pdf#a1b2c3d4>".
    """
    import hashlib
    from pathlib import Path

    if not name:
        return "<unbenannt>"
    basis = Path(str(name)).name
    endung = Path(basis).suffix.lstrip(".").lower() or "ohne"
    kurz = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8]
    return f"<{endung}#{kurz}>"
