"""Tests für das strukturierte JSON-Logging."""

import json
import logging

from utils.logging_setup import (
    JsonFormatter,
    _AccessLogPIIFilter,
    konfiguriere_logging,
    pseudonymisiere_dateiname,
)


def test_json_formatter_erzeugt_gueltiges_json():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Testmeldung %s",
        args=("X",),
        exc_info=None,
    )
    ausgabe = formatter.format(record)
    daten = json.loads(ausgabe)
    assert daten["level"] == "INFO"
    assert daten["logger"] == "test.logger"
    assert daten["message"] == "Testmeldung X"
    assert "zeit" in daten


def test_pseudonymisiere_dateiname_verbirgt_klarnamen():
    ausgabe = pseudonymisiere_dateiname("Vermerk_Telefonat_Mustermann_final.pdf")
    assert "Mustermann" not in ausgabe
    assert "pdf" in ausgabe
    # stabil: gleicher Name -> gleiche Kennung
    assert ausgabe == pseudonymisiere_dateiname("Vermerk_Telefonat_Mustermann_final.pdf")
    # Pfad wird auf den Basisnamen reduziert
    assert pseudonymisiere_dateiname(
        "/app/data/jobs/x/Mustermann.pdf"
    ) == pseudonymisiere_dateiname("Mustermann.pdf")


def _record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_pii_filter_maskiert_query_dateinamen():
    filt = _AccessLogPIIFilter()
    record = _record(
        '%s - "%s" %s',
        "127.0.0.1:0",
        "GET /api/job/abc/datei?name=Mustermann_Bescheid.pdf HTTP/1.1",
        200,
    )
    filt.filter(record)
    formatiert = record.getMessage()
    assert "Mustermann_Bescheid" not in formatiert
    assert "name=<redacted>" in formatiert


def test_pii_filter_maskiert_mehrere_keys():
    filt = _AccessLogPIIFilter()
    record = _record(
        '%s - "%s" %s',
        "10.0.0.1:0",
        "GET /api/job/x/datei?name=Geheim.pdf&titel=Frau+Schmidt HTTP/1.1",
        200,
    )
    filt.filter(record)
    formatiert = record.getMessage()
    assert "Geheim" not in formatiert
    assert "Schmidt" not in formatiert
    assert formatiert.count("<redacted>") == 2


def test_pii_filter_laesst_unkritische_query_intakt():
    filt = _AccessLogPIIFilter()
    record = _record('%s - "%s" %s', "1.2.3.4:0", "GET /api/jobs?status=fertig HTTP/1.1", 200)
    filt.filter(record)
    assert "status=fertig" in record.getMessage()


def test_pii_filter_unterbricht_logging_nie():
    """Auch bei nicht-string-args muss filter() True zurückgeben."""
    filt = _AccessLogPIIFilter()
    record = _record("Statisches: %d", 42)
    assert filt.filter(record) is True


def test_konfiguriere_logging_biegt_uvicorn_logger_um():
    """uvicorn-Logger werden an den Root umgebogen (keine Klartext-Handler mehr)."""
    root = logging.getLogger()
    uv = logging.getLogger("uvicorn.access")
    sicherung_root = root.handlers[:]
    sicherung_uv_handler = uv.handlers[:]
    sicherung_uv_propagate = uv.propagate
    try:
        uv.handlers = [logging.StreamHandler()]
        uv.propagate = False
        konfiguriere_logging()
        assert uv.handlers == []
        assert uv.propagate is True
    finally:
        root.handlers[:] = sicherung_root
        uv.handlers[:] = sicherung_uv_handler
        uv.propagate = sicherung_uv_propagate
