"""Bewertungsagent-v2-Router: Vorgangsliste, Archivar-Entscheidung, Tiefenprüfung."""

from typing import Literal

from config import DATA_DIR
from fastapi import APIRouter, Depends, HTTPException, Request
from models import (
    IndizKatalog,
    VorgangBewertung,
    get_db,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload
from utils.job_dateien import finde_datei
from utils.zeit import jetzt_utc

router = APIRouter()


class EntscheidungBody(BaseModel):
    klassifikation_final: Literal["A", "B", "V"]
    begruendung: str = ""


@router.get("/api/bewertung/vorgaenge/{job_id}")
async def liste_vorgaenge(job_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """Listet alle bewerteten Vorgänge eines Jobs samt Indizien-Treffern.

    Eager-loading via `selectinload`: statt 1 + 2·N Queries (Vorgänge +
    pro Vorgang je eine für Treffer und Dokumente) laufen nur drei
    Queries — eine pro Tabelle.
    """
    vorgaenge = (
        db.query(VorgangBewertung)
        .options(
            selectinload(VorgangBewertung.treffer),
            selectinload(VorgangBewertung.dokumente),
        )
        .filter_by(job_id=job_id)
        .all()
    )
    # Katalog einmalig laden für Bezeichnung + Typ pro Treffer.
    katalog_map = {
        i.schluessel: (i.bezeichnung, i.typ.value if i.typ else None)
        for i in db.query(IndizKatalog).all()
    }
    return [
        {
            "id": v.id,
            "vorgang_id": v.vorgang_id,
            "aktenzeichen": v.aktenzeichen,
            "betreff": v.betreff,
            "rohscore": v.rohscore,
            "finaler_score": v.finaler_score,
            "klassifikation_vorschlag": v.klassifikation_vorschlag,
            "klassifikation_final": v.klassifikation_final,
            "llm_begruendung": v.llm_begruendung,
            "schlagworte": v.schlagworte,
            "override_begruendung": v.override_begruendung,
            "tiefenpruefung_gemacht": v.tiefenpruefung_gemacht,
            "treffer": [
                {
                    "schluessel": t.indiz_schluessel,
                    "bezeichnung": katalog_map.get(t.indiz_schluessel, (t.indiz_schluessel, None))[
                        0
                    ],
                    "typ": katalog_map.get(t.indiz_schluessel, (None, None))[1],
                    "erfuellt": t.erfuellt,
                    "punkte_beitrag": t.punkte_beitrag,
                    "evidenz": t.evidenz,
                }
                for t in v.treffer
            ],
            "dokumente": [
                {
                    "dateiname": d.dateiname,
                    "bewertung": d.bewertung.value
                    if hasattr(d.bewertung, "value")
                    else d.bewertung,
                    "begruendung": d.begruendung,
                }
                for d in v.dokumente
            ],
        }
        for v in vorgaenge
    ]


@router.post("/api/bewertung/vorgang/{vorgang_bewertung_id}/entscheidung")
async def speichere_entscheidung(
    vorgang_bewertung_id: int,
    body: EntscheidungBody,
    request: Request,
    db: Session = Depends(get_db),
):
    """Speichert die finale Entscheidung des Archivars; bei Override ist Begründung Pflicht."""
    vb = db.query(VorgangBewertung).get(vorgang_bewertung_id)
    if vb is None:
        raise HTTPException(status_code=404, detail="Vorgang nicht gefunden")

    ist_override = body.klassifikation_final != vb.klassifikation_vorschlag
    if ist_override and not body.begruendung.strip():
        raise HTTPException(
            status_code=400,
            detail="Bei abweichender Klassifikation ist eine Begründung Pflicht.",
        )

    vb.klassifikation_final = body.klassifikation_final
    vb.override_begruendung = body.begruendung.strip() or None
    vb.entschieden_am = jetzt_utc()
    user = getattr(request.state, "user", None)
    vb.entschieden_von = (
        user.get("name") if isinstance(user, dict) and user.get("name") else "system"
    )
    db.commit()
    return {"status": "ok", "klassifikation_final": vb.klassifikation_final}


@router.post("/api/bewertung/vorgang/{vorgang_bewertung_id}/tiefenpruefung")
async def starte_tiefenpruefung(
    vorgang_bewertung_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Stößt die Tiefenprüfung (Dokument-Ebene) für einen Vorgang an.

    Nach Abschluss des Bewertungsagenten ist `arbeit_bewertung/` aufgeräumt
    und nur das neu gepackte ZIP liegt im Job-Verzeichnis. Wir entpacken
    es bei Bedarf in `arbeit_tiefenpruefung/`.
    """
    from agents.agent_bewertung import tiefenpruefung_vorgang
    from utils.xdomea_parser import finde_xdomea_xmls, lese_alle_vorgaenge, lese_bewertungseinheiten
    from utils.zip_handler import entpacke_zip

    vb = db.query(VorgangBewertung).get(vorgang_bewertung_id)
    if vb is None:
        raise HTTPException(status_code=404, detail="Vorgang nicht gefunden")

    job_pfad = DATA_DIR / "jobs" / vb.job_id
    if not job_pfad.exists():
        raise HTTPException(status_code=404, detail="Job-Verzeichnis nicht gefunden")

    # Arbeitsverzeichnis für Tiefenprüfung; ZIP bei Bedarf entpacken
    arbeit = job_pfad / "arbeit_tiefenpruefung"
    if not arbeit.exists() or not any(arbeit.rglob("*.xml")):
        zips = sorted(job_pfad.glob("*_bewertung_ok.zip"))
        if not zips:
            zips = sorted(job_pfad.glob("*.zip"))
        if not zips:
            raise HTTPException(status_code=404, detail="Kein ZIP im Job-Verzeichnis gefunden.")
        arbeit.mkdir(parents=True, exist_ok=True)
        entpacke_zip(zips[-1], arbeit)

    # Bewertungseinheiten identisch zur Bewertung aufbauen (gleiche Ebene +
    # Renditions-Dedup + eindeutige vorgang_id), damit das Matching auch bei
    # leeren/doppelten Aktenzeichen den richtigen Vorgang trifft.
    from utils.job_config import lese_bewertungsebene

    ebene = lese_bewertungsebene(job_pfad)
    einheiten = lese_bewertungseinheiten(finde_xdomea_xmls(arbeit), ebene)
    vorgang_dict = next(
        (v for v in einheiten if vb.vorgang_id and v.get("vorgang_id") == vb.vorgang_id),
        None,
    )
    if vorgang_dict is None:
        # Fallback: Match über Aktenzeichen (ältere Bewertungen ohne vorgang_id-Treffer)
        vorgang_dict = next(
            (v for v in einheiten if vb.aktenzeichen and v.get("aktenzeichen") == vb.aktenzeichen),
            None,
        )
    if vorgang_dict is None:
        raise HTTPException(
            status_code=404,
            detail="Vorgang im Arbeitsverzeichnis nicht gefunden",
        )

    # Lieferverzeichnis-Sonderfall: vorgang_dict trägt Verweis auf inneres ZIP,
    # aber keine Dokumentliste. Inneres ZIP entpacken und Vorgang von dort laden.
    quelle_zip = vorgang_dict.get("quelle_zip")
    arbeitsverzeichnis = arbeit
    if quelle_zip and not vorgang_dict.get("dokumente"):
        inneres_zip = finde_datei(arbeit, quelle_zip)
        if inneres_zip is None:
            raise HTTPException(
                status_code=404,
                detail=f"Inneres Vorgangs-ZIP '{quelle_zip}' nicht im Akten-Bundle gefunden.",
            )
        sub_arbeit = arbeit / f"vorgang_{vb.id}"
        if not sub_arbeit.exists() or not any(sub_arbeit.rglob("*.xml")):
            sub_arbeit.mkdir(parents=True, exist_ok=True)
            entpacke_zip(inneres_zip, sub_arbeit)
        # Erste Bewertungseinheit aus dem inneren XDOMEA übernehmen
        innere_xmls = [x for x in sub_arbeit.rglob("*.xml") if not x.name.startswith("protokoll_")]
        inneres_vorgang = None
        for xml in innere_xmls:
            kandidaten = lese_alle_vorgaenge(xml)
            if kandidaten:
                inneres_vorgang = kandidaten[0]
                break
        if inneres_vorgang is None:
            raise HTTPException(
                status_code=404,
                detail=f"Im inneren ZIP '{quelle_zip}' wurde kein Vorgang/Akte gefunden.",
            )
        # Metadaten des Lieferverzeichnis-Eintrags behalten (Aktenzeichen,
        # Betreff bleiben wie zuvor angezeigt), nur Dokumente übernehmen.
        vorgang_dict = {**vorgang_dict, "dokumente": inneres_vorgang.get("dokumente", [])}
        arbeitsverzeichnis = sub_arbeit

    ergebnis = await tiefenpruefung_vorgang(vb.job_id, vb.id, vorgang_dict, arbeitsverzeichnis)
    return ergebnis
