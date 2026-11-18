# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Tests für die Aufbereitung der Reparaturhinweise der formalen Prüfung.

Beim Lieferverzeichnis-Pfad (nscale eGOV) wurden die reparierbaren Befunde der
inneren Container auf ein Boolean reduziert. Die Oberfläche meldete daraufhin
pauschal "XML-Fehler — automatische Reparatur verfügbar", während die
Zusammenfassung "Keine Schema-Verletzungen festgestellt" sagte. Beides
stimmte technisch, zusammen war es für den Archivar unverständlich.
"""

from agents.agent_formal import aggregiere_reparaturoptionen


def test_reparierbare_befunde_der_container_werden_durchgereicht():
    """Der Archivar muss sehen, WAS repariert würde."""
    vorgaenge = [
        {
            "aktenzeichen": "900.01/000002",
            "dateiname": "akte1.xdomea",
            "fehlende_referenzen": [],
            "xsd_reparierbar": True,
            "reparierbare": ["Importbestaetigung-Wert 'true' auf '1' korrigieren"],
        }
    ]

    optionen = aggregiere_reparaturoptionen(vorgaenge)

    assert optionen["xsd"] == ["Importbestaetigung-Wert 'true' auf '1' korrigieren"]
    assert len(optionen["lv_vorgaenge"]) == 1


def test_befunde_mehrerer_container_werden_ohne_dubletten_gesammelt():
    """Derselbe DMS-Fehler in mehreren Containern ergibt einen Hinweis."""
    vorgaenge = [
        {
            "aktenzeichen": "A",
            "dateiname": "a.xdomea",
            "fehlende_referenzen": [],
            "xsd_reparierbar": True,
            "reparierbare": ["Importbestaetigung-Wert 'true' auf '1' korrigieren"],
        },
        {
            "aktenzeichen": "B",
            "dateiname": "b.xdomea",
            "fehlende_referenzen": [],
            "xsd_reparierbar": True,
            "reparierbare": ["Importbestaetigung-Wert 'true' auf '1' korrigieren"],
        },
    ]

    optionen = aggregiere_reparaturoptionen(vorgaenge)

    assert optionen["xsd"] == ["Importbestaetigung-Wert 'true' auf '1' korrigieren"]
    assert len(optionen["lv_vorgaenge"]) == 2


def test_fehlende_referenzen_werden_uebernommen():
    vorgaenge = [
        {
            "aktenzeichen": "A",
            "dateiname": "a.xdomea",
            "fehlende_referenzen": ["anlage.pdf"],
            "xsd_reparierbar": False,
            "reparierbare": [],
        }
    ]

    optionen = aggregiere_reparaturoptionen(vorgaenge)

    assert optionen["referenzen"] == ["anlage.pdf"]
    assert optionen["xsd"] == []


def test_ohne_befunde_bleiben_die_optionen_leer():
    vorgaenge = [
        {
            "aktenzeichen": "A",
            "dateiname": "a.xdomea",
            "fehlende_referenzen": [],
            "xsd_reparierbar": False,
            "reparierbare": [],
        }
    ]

    optionen = aggregiere_reparaturoptionen(vorgaenge)

    assert optionen["xsd"] == []
    assert optionen["referenzen"] == []
    assert optionen["lv_vorgaenge"] == []


def test_encoding_ist_im_lieferverzeichnis_pfad_immer_falsch():
    """Die äußere LV-XML wird separat geprüft — hier kein Encoding-Fall."""
    optionen = aggregiere_reparaturoptionen([])

    assert optionen["encoding"] is False
