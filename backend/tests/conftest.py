"""Pytest-Konfiguration.

Lenkt die Datenbank für die gesamte Test-Sitzung auf eine temporäre SQLite-Datei
um — BEVOR `models` zum ersten Mal importiert wird (models.py liest DATABASE_URL
auf Modulebene). So fassen API-/Agenten-Tests nie die Entwickler-DB an.
"""

import os
import tempfile

_tmp_db = tempfile.NamedTemporaryFile(prefix="testdb_", suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
# Die Anwendung ist ohne Angabe abgesichert (AUTH_MODUS-Default: Anmeldung an).
# Die API-Tests laufen bewusst ohne Anmeldung; Auth-Tests setzen den Modus selbst.
os.environ.setdefault("AUTH_MODUS", "disabled")

# Pflichtfelder der Konfiguration: Die Tests müssen in einem frischen Klon ohne
# .env laufen. Gesetzte Werte (z. B. in der CI) haben Vorrang.
_tmp_dir = tempfile.mkdtemp(prefix="aussonderung_test_")
os.environ.setdefault("DATA_DIR", os.path.join(_tmp_dir, "data"))
if not os.environ.get("VERAPDF_PATH"):
    # Aufruf um das mitgelieferte veraPDF-JAR. Fehlt Java, melden die
    # Formatprüfungen eine Warnung statt eines Ergebnisses.
    _jar = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "vendor",
        "verapdf",
        "greenfield-apps-1.28.2.jar",
    )
    _wrapper = os.path.join(_tmp_dir, "verapdf")
    with open(_wrapper, "w", encoding="utf-8") as _f:
        _f.write(
            "#!/bin/sh\n"
            f'exec java -cp "{os.path.normpath(_jar)}" '
            'org.verapdf.apps.GreenfieldCliWrapper "$@"\n'
        )
    os.chmod(_wrapper, 0o700)
    os.environ["VERAPDF_PATH"] = _wrapper
