# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Aufbereitung der XDOMEA-Paketstruktur für die Anzeige im Frontend.

Übersetzt das Ergebnis von `xdomea_parser.analysiere_paket_struktur` in die
flache, ID-behaftete Form, die die Tool-SPA erwartet, und gleicht dabei jede
referenzierte Datei gegen den tatsächlichen ZIP-Inhalt ab.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _datei_info(dateiname: str, vorhandene_dateien: set[str]) -> dict:
    vorhanden = bool(dateiname) and Path(dateiname).name in vorhandene_dateien
    endung = Path(dateiname).suffix.lstrip(".").upper() if dateiname else ""
    return {"datei": dateiname, "format": endung or "—", "exist": vorhanden}


def _dokument(dok: dict, dok_id: str, vorhandene_dateien: set[str]) -> dict:
    # Ein XDOMEA-<Dokument> kann mehrere physische Dateien bündeln (mehrere
    # Versionen/Formate, z.B. regisafe). Alle ausweisen, damit die Anzeige der
    # ZIP-Realität entspricht.
    alle = dok.get("alle_dateinamen") or ([dok.get("dateiname")] if dok.get("dateiname") else [])
    dateien = [
        {"id": f"{dok_id}-f{i}", **_datei_info(dn, vorhandene_dateien)} for i, dn in enumerate(alle)
    ]
    primaer = dateien[0] if dateien else _datei_info("", vorhandene_dateien)
    return {
        "id": dok_id,
        "betreff": dok.get("betreff") or dok.get("kennzeichen") or "(ohne Betreff)",
        "datei": primaer["datei"],
        "format": primaer["format"],
        "exist": all(d["exist"] for d in dateien) if dateien else False,
        "typ": dok.get("typ", ""),
        "dateien": dateien,
    }


def _vorgang(vg: dict, vorgang_id: str, v_idx: int, vorhandene_dateien: set[str]) -> dict:
    return {
        "id": vorgang_id,
        "az": vg.get("aktenzeichen") or vg.get("vorgang_id") or f"Vorgang {v_idx + 1}",
        "betreff": vg.get("betreff") or "(ohne Betreff)",
        "laufzeit": vg.get("laufzeit_beginn", ""),
        "behoerde": vg.get("federfuehrung", ""),
        "dokumente": [
            _dokument(d, f"{vorgang_id}-d{d_idx}", vorhandene_dateien)
            for d_idx, d in enumerate(vg.get("dokumente", []) or [])
        ],
    }


def _alle_dokumente(akte: dict):
    yield from akte.get("dokumente", []) or []
    for vg in akte.get("vorgaenge", []) or []:
        yield from vg.get("dokumente", []) or []
    for ta in akte.get("teilakten", []) or []:
        yield from ta.get("dokumente", []) or []
        for vg in ta.get("vorgaenge", []) or []:
            yield from vg.get("dokumente", []) or []


def struktur_zu_anzeige(
    struktur: dict, vorhandene_dateien: set[str]
) -> tuple[list[dict], int, int]:
    """Bereitet die Paketstruktur für das Frontend auf.

    Args:
        struktur: Ergebnis von `analysiere_paket_struktur`.
        vorhandene_dateien: Basisnamen aller Dateien, die real im ZIP liegen.

    Returns:
        (akten, dateien_gesamt, fehlende_dateien) — `akten` ist die Anzeige-
        hierarchie; bei vorgangs- oder dokumentbasierten Paketen stehen die
        Einheiten aus Frontend-Kompatibilität ebenfalls in dieser Liste.
    """
    akten: list[dict] = []
    ebene = struktur.get("ebene", "leer")

    if struktur.get("akten"):
        for a_idx, akte in enumerate(struktur.get("akten", [])):
            akte_id = f"a{a_idx}"
            vorgaenge_out = [
                _vorgang(vg, f"{akte_id}-v{v_idx}", v_idx, vorhandene_dateien)
                for v_idx, vg in enumerate(akte.get("vorgaenge", []) or [])
            ]
            teilakten_out = []
            for t_idx, ta in enumerate(akte.get("teilakten", []) or []):
                ta_id = f"{akte_id}-t{t_idx}"
                teilakten_out.append(
                    {
                        "id": ta_id,
                        "az": ta.get("aktenzeichen") or f"Teilakte {t_idx + 1}",
                        "betreff": ta.get("betreff") or "(ohne Betreff)",
                        "laufzeit": ta.get("laufzeit_beginn", ""),
                        "vorgaenge": [
                            _vorgang(vg, f"{ta_id}-v{v_idx}", v_idx, vorhandene_dateien)
                            for v_idx, vg in enumerate(ta.get("vorgaenge", []) or [])
                        ],
                        "dokumente": [
                            _dokument(d, f"{ta_id}-d{d_idx}", vorhandene_dateien)
                            for d_idx, d in enumerate(ta.get("dokumente", []) or [])
                        ],
                    }
                )
            akten.append(
                {
                    "id": akte_id,
                    "az": akte.get("aktenzeichen") or f"Akte {a_idx + 1}",
                    "betreff": akte.get("betreff") or "(ohne Betreff)",
                    "laufzeit": akte.get("laufzeit_beginn", ""),
                    "behoerde": akte.get("federfuehrung", ""),
                    "teilakten": teilakten_out,
                    "vorgaenge": vorgaenge_out,
                    "dokumente": [
                        _dokument(d, f"{akte_id}-d{d_idx}", vorhandene_dateien)
                        for d_idx, d in enumerate(akte.get("dokumente", []) or [])
                    ],
                }
            )
    elif ebene in ("vorgaenge", "dokumente"):
        # Klassischer XDOMEA-2.x-Fall: Vorgänge sind die Wurzel, keine Akten-
        # Ebene. Ebenso ältere Abgaben, deren Dokumente ohne Akte/Vorgang
        # geliefert werden. Beide werden als "akten" ausgeliefert (für
        # Frontend-Kompatibilität), aber ohne Kind-Ebenen.
        for v_idx, vg in enumerate(struktur.get("vorgaenge", [])):
            vorgang_id = f"a{v_idx}"
            akten.append(
                {
                    "id": vorgang_id,
                    "az": vg.get("aktenzeichen") or vg.get("vorgang_id") or f"Vorgang {v_idx + 1}",
                    "betreff": vg.get("betreff") or "(ohne Betreff)",
                    "laufzeit": vg.get("laufzeit_beginn", ""),
                    "behoerde": vg.get("federfuehrung", ""),
                    "teilakten": [],
                    "vorgaenge": [],
                    "dokumente": [
                        _dokument(d, f"{vorgang_id}-d{d_idx}", vorhandene_dateien)
                        for d_idx, d in enumerate(vg.get("dokumente", []) or [])
                    ],
                }
            )

    # Zählung auf Datei-Ebene: ein Dokument kann mehrere Dateien bündeln.
    alle_dateien = [f for a in akten for d in _alle_dokumente(a) for f in (d.get("dateien") or [])]
    fehlend = sum(1 for f in alle_dateien if not f.get("exist"))
    return akten, len(alle_dateien), fehlend
