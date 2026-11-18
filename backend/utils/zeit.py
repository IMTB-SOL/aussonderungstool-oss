"""Zeit-Helfer — eine zentrale Stelle für UTC-Zeitstempel.

`datetime.utcnow()` ist seit Python 3.12 deprecated, weil es ein
*offset-naives* datetime liefert, das den UTC-Bezug nur implizit hat
(quelle ständiger Vergleichs-Bugs). Empfohlen ist
`datetime.now(timezone.utc)` — das liefert allerdings ein *offset-aware*
datetime.

Unsere SQLite-Spalten sind als `DateTime` ohne `timezone=True` definiert,
speichern also naive Werte. Vergleiche mit aware-Werten würden
`TypeError: can't subtract offset-naive and offset-aware datetimes`
werfen — z. B. in `utils.retention`. Diese Helfer liefern deshalb wieder
naive UTC, sind aber intern auf den modernen Aufruf umgestellt.

Sobald wir auf Postgres + `DateTime(timezone=True)` umstellen, kann
`jetzt_utc()` zu `datetime.now(timezone.utc)` werden — alle Aufrufstellen
bleiben unverändert.
"""

from datetime import datetime, timezone


def jetzt_utc() -> datetime:
    """Aktuelle UTC-Zeit als naive datetime (SQLite-kompatibel)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
