"""Indizien-Engine für den Bewertungsagenten.

Pro Indiz gibt es eine reine Extraktor-Funktion mit identischer Signatur:
    extrahiere_<schluessel>(vorgang: dict, profil: BehoerdenProfil | None) -> IndizTreffer

Die Funktion `berechne_score` setzt alle Extraktoren ein, wendet KO-/OK-Regeln an
und liefert die finale Klassifikation A/B/V.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IndizTreffer:
    schluessel: str
    erfuellt: bool
    evidenz: str


def extrahiere_federfuehrung(vorgang: dict, profil) -> IndizTreffer:
    """KO-Deckel: Vorgang kann nur A werden, wenn die eigene Stelle federführend ist.

    Heuristik im Erstausbau: 'eigene' oder 'eigene stelle' im federfuehrung-Feld.
    Wenn ein Behördenprofil mit Kürzel vorhanden ist, gilt auch ein Kürzel-Treffer.
    """
    federf = (vorgang.get("federfuehrung") or "").strip()
    if not federf:
        return IndizTreffer(
            "federfuehrung_eigene_stelle", False, "kein eintrag in xdomea:Federfuehrung"
        )

    bezeichnung_lower = federf.lower()
    eigene_indikatoren = ["eigene stelle", "eigene", "eigenes haus"]
    if profil is not None and profil.kuerzel:
        eigene_indikatoren.append(profil.kuerzel.lower())

    # Word-Boundaries: 'eigene' soll nicht in 'nicht eigene' o.ä. fälschlich
    # matchen, wenn die Negation grammatikalisch davorsteht. Wir matchen aber
    # weiterhin als Wort, nicht als Substring im Wort.
    def _wort_treffer(text: str, wort: str) -> bool:
        return re.search(r"\b" + re.escape(wort) + r"\b", text) is not None

    if any(_wort_treffer(bezeichnung_lower, ind) for ind in eigene_indikatoren):
        # Negation-Schutz: explizite 'nicht eigene'-Wendungen ausschließen.
        if re.search(r"\bnicht\s+eigene\b", bezeichnung_lower):
            return IndizTreffer(
                "federfuehrung_eigene_stelle",
                False,
                f"federführend: {federf} (explizit nicht eigene Stelle)",
            )
        return IndizTreffer("federfuehrung_eigene_stelle", True, f"federführend: {federf}")
    return IndizTreffer(
        "federfuehrung_eigene_stelle", False, f"federführend: {federf} (Fremdstelle)"
    )


def extrahiere_archivvermerk_behoerde(vorgang: dict, profil) -> IndizTreffer:
    """OK-Boden: Wenn Behörde 'Archivieren' empfiehlt, mindestens A.

    Akzeptiert sowohl den XDOMEA-Codeliste-Wert 'A' als auch Klartext-Varianten
    wie 'Archivieren'.
    """
    art_roh = (vorgang.get("aussonderungsart") or "").strip()
    art_lower = art_roh.lower()
    # XDOMEA-Codeliste 0026 nutzt 'A'/'a' (2.x) und numerische Codes '01'/'001' (3.0)
    # für 'Archivieren' / 'Archivwürdig'.
    numerische_archiv_codes = {"01", "001", "1"}
    erfuellt = (
        art_lower == "a"
        or "archivieren" in art_lower
        or "archivwürdig" in art_lower
        or "archivwuerdig" in art_lower
        or art_roh in numerische_archiv_codes
    )
    evidenz = f"Aussonderungsart={art_roh or '–'}"
    return IndizTreffer("archivvermerk_behoerde", erfuellt, evidenz)


def extrahiere_hoechste_zeichnung_minister(vorgang: dict, profil) -> IndizTreffer:
    """Boost: Minister/Staatssekretär in Betreff oder Zeichnungskette."""
    text = " ".join(
        [
            vorgang.get("betreff") or "",
            vorgang.get("aktenzeichen") or "",
        ]
    ).lower()
    indikatoren = ["minister", "staatssekretär", "staatssekretaer", "staatsminister"]
    treffer_wort = next((i for i in indikatoren if i in text), None)
    if treffer_wort:
        return IndizTreffer(
            "hoechste_zeichnung_minister",
            True,
            f"Begriff '{treffer_wort}' im Betreff/Aktenzeichen",
        )
    return IndizTreffer(
        "hoechste_zeichnung_minister", False, "kein Minister-/Sekretär-Bezug erkennbar"
    )


def extrahiere_aufbewahrungsdauer_30_plus(vorgang: dict, profil) -> IndizTreffer:
    """Boost: Aufbewahrungsdauer ≥ 30 Jahre. Akzeptiert '30', '30 Jahre', 'P30Y'."""
    roh = (vorgang.get("aufbewahrungsdauer") or "").strip()
    if not roh:
        return IndizTreffer(
            "aufbewahrungsdauer_30_plus", False, "keine Aufbewahrungsdauer angegeben"
        )
    treffer = re.search(r"(\d+)", roh)
    if not treffer:
        return IndizTreffer("aufbewahrungsdauer_30_plus", False, f"keine Zahl in '{roh}'")
    jahre = int(treffer.group(1))
    return IndizTreffer(
        "aufbewahrungsdauer_30_plus",
        jahre >= 30,
        f"Aufbewahrungsdauer {jahre} Jahre",
    )


def extrahiere_dickitaet_hoch(vorgang: dict, profil) -> IndizTreffer:
    """Boost: ≥ 20 Dokumente im Vorgang."""
    anzahl = len(vorgang.get("dokumente") or [])
    return IndizTreffer(
        "dickitaet_hoch",
        anzahl >= 20,
        f"{anzahl} Dokumente im Vorgang",
    )


def extrahiere_viele_versionen(vorgang: dict, profil) -> IndizTreffer:
    """Boost: durchschnittliche Versionsanzahl > 3."""
    dokumente = vorgang.get("dokumente") or []
    if not dokumente:
        return IndizTreffer("viele_versionen", False, "keine Dokumente")
    versionen_pro_dok = [d.get("versionen") or 1 for d in dokumente]
    avg = sum(versionen_pro_dok) / len(versionen_pro_dok)
    return IndizTreffer(
        "viele_versionen",
        avg > 3,
        f"Ø {avg:.1f} Versionen/Dokument",
    )


def extrahiere_ifg_ueg_vermerk(vorgang: dict, profil) -> IndizTreffer:
    """Boost: IFG- oder UEG-Bezug erkennbar."""
    text = " ".join(
        [
            vorgang.get("betreff") or "",
            vorgang.get("aktenzeichen") or "",
        ]
    ).lower()
    muster = ["ifg", "informationsfreiheit", "ueg", "uig", "umweltinformations"]
    treffer = next((m for m in muster if m in text), None)
    if treffer:
        return IndizTreffer("ifg_ueg_vermerk", True, f"Schlüsselwort '{treffer}' erkannt")
    return IndizTreffer("ifg_ueg_vermerk", False, "kein IFG/UEG-Bezug erkennbar")


def extrahiere_hierarchie_ministerium(vorgang: dict, profil) -> IndizTreffer:
    """Boost: Behörde ist auf Ministeriumsebene."""
    if profil is None:
        return IndizTreffer("hierarchie_ministerium", False, "kein Behördenprofil zugeordnet")
    # hierarchie_stufe ist Enum(HierarchieStufe) (str, enum.Enum) — Vergleich mit String funktioniert.
    stufe = profil.hierarchie_stufe
    stufe_str = stufe.value if hasattr(stufe, "value") else str(stufe or "")
    erfuellt = stufe_str.lower() == "ministerium"
    return IndizTreffer(
        "hierarchie_ministerium",
        erfuellt,
        f"Hierarchiestufe: {stufe_str or '–'}",
    )


def extrahiere_besondere_oe(vorgang: dict, profil) -> IndizTreffer:
    """Boost: Eine im Behördenprofil markierte OE taucht im Vorgangskontext auf."""
    if profil is None or not profil.besondere_oe:
        return IndizTreffer("besondere_oe", False, "keine besonderen OE im Profil hinterlegt")
    text = " ".join([vorgang.get("betreff") or "", vorgang.get("aktenzeichen") or ""]).lower()
    treffer = next(
        (
            oe
            for oe in profil.besondere_oe
            if re.search(r"\b" + re.escape(oe.lower()) + r"\b", text)
        ),
        None,
    )
    if treffer:
        return IndizTreffer("besondere_oe", True, f"OE '{treffer}' im Vorgangskontext erkannt")
    return IndizTreffer("besondere_oe", False, "keine besondere OE im Vorgang erkennbar")


def extrahiere_vorbewertung_gleicher_az(vorgang: dict, profil, db=None) -> IndizTreffer:
    """Boost wenn ein Vorgang mit gleichem Aktenzeichen-Stamm zuletzt A war.

    Stamm-Definition: Teil vor dem ersten '/' (z.B. '12-34' aus '12-34/2024').
    """
    if db is None:
        return IndizTreffer("vorbewertung_gleicher_az", False, "keine DB-Session")
    az = (vorgang.get("aktenzeichen") or "").strip()
    if not az:
        return IndizTreffer("vorbewertung_gleicher_az", False, "kein Aktenzeichen")
    stamm = az.split("/")[0].strip()
    if not stamm:
        return IndizTreffer("vorbewertung_gleicher_az", False, "Aktenzeichen-Stamm leer")

    from models import VorgangBewertung

    treffer_db = (
        db.query(VorgangBewertung)
        .filter(VorgangBewertung.aktenzeichen.like(f"{stamm}/%"))
        .filter(VorgangBewertung.klassifikation_final == "A")
        .order_by(VorgangBewertung.entschieden_am.desc())
        .first()
    )
    if treffer_db is None:
        return IndizTreffer(
            "vorbewertung_gleicher_az", False, f"kein A-Treffer für Stamm '{stamm}'"
        )
    return IndizTreffer(
        "vorbewertung_gleicher_az",
        True,
        f"vorheriger A-Treffer: {treffer_db.aktenzeichen} (Stamm '{stamm}')",
    )


# ---------------------------------------------------------------------------
# Scoring-Engine
# ---------------------------------------------------------------------------

# Mapping: Indiz-Schlüssel → Extraktor-Funktion
EXTRAKTOREN = {
    "federfuehrung_eigene_stelle": extrahiere_federfuehrung,
    "archivvermerk_behoerde": extrahiere_archivvermerk_behoerde,
    "hoechste_zeichnung_minister": extrahiere_hoechste_zeichnung_minister,
    "hierarchie_ministerium": extrahiere_hierarchie_ministerium,
    "besondere_oe": extrahiere_besondere_oe,
    "aufbewahrungsdauer_30_plus": extrahiere_aufbewahrungsdauer_30_plus,
    "dickitaet_hoch": extrahiere_dickitaet_hoch,
    "viele_versionen": extrahiere_viele_versionen,
    "ifg_ueg_vermerk": extrahiere_ifg_ueg_vermerk,
    "vorbewertung_gleicher_az": extrahiere_vorbewertung_gleicher_az,
}


def berechne_score(
    vorgang: dict,
    profil,
    katalog: list,
    schwelle_a: int,
    schwelle_v: int,
    db=None,
) -> dict:
    """Berechnet Score und Klassifikation für einen Vorgang.

    Returns:
        {
            "rohscore": int,
            "finaler_score": int,
            "klassifikation": "A"|"B"|"V",
            "treffer": [ {schluessel, erfuellt, punkte_beitrag, evidenz, typ, bezeichnung}, ... ],
        }
    """
    treffer_liste: list[dict] = []
    rohscore = 0
    federfuehrung_erfuellt = False
    archivvermerk_erfuellt = False

    for eintrag in katalog:
        if not eintrag.aktiv:
            continue
        extraktor = EXTRAKTOREN.get(eintrag.schluessel)
        if extraktor is None:
            logger.warning(
                "Kein Extraktor für Indiz '%s' definiert — übersprungen.", eintrag.schluessel
            )
            continue

        # Historien-Extraktor braucht db
        if eintrag.schluessel == "vorbewertung_gleicher_az":
            res = extraktor(vorgang, profil, db=db)
        else:
            res = extraktor(vorgang, profil)

        punkte_beitrag = eintrag.punkte if res.erfuellt else 0
        rohscore += punkte_beitrag

        # typ kann Enum oder String sein
        typ_val = eintrag.typ.value if hasattr(eintrag.typ, "value") else str(eintrag.typ or "")

        treffer_liste.append(
            {
                "schluessel": res.schluessel,
                "erfuellt": res.erfuellt,
                "punkte_beitrag": punkte_beitrag,
                "evidenz": res.evidenz,
                "typ": typ_val,
                "bezeichnung": getattr(eintrag, "bezeichnung", eintrag.schluessel),
            }
        )

        if eintrag.schluessel == "federfuehrung_eigene_stelle" and res.erfuellt:
            federfuehrung_erfuellt = True
        if eintrag.schluessel == "archivvermerk_behoerde" and res.erfuellt:
            archivvermerk_erfuellt = True

    finaler_score = rohscore
    if not federfuehrung_erfuellt:
        finaler_score = min(finaler_score, schwelle_a - 1)
    if archivvermerk_erfuellt:
        finaler_score = max(finaler_score, schwelle_a)

    if finaler_score >= schwelle_a:
        klassifikation = "A"
    elif finaler_score < schwelle_v:
        klassifikation = "V"
    else:
        klassifikation = "B"

    return {
        "rohscore": rohscore,
        "finaler_score": finaler_score,
        "klassifikation": klassifikation,
        "treffer": treffer_liste,
    }
