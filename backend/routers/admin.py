"""Admin-Router: Schwellwerte, Indizienkatalog, Behördenprofile, System-Prompts, LLM-Provider."""

import logging
import os
from typing import Literal

from dependencies import require_admin
from fastapi import APIRouter, Depends, HTTPException, Request
from models import (
    BehoerdenProfil,
    BewertungSchwellwerte,
    IndizKatalog,
    LLMEinstellung,
    SystemPrompt,
    User,
    get_db,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter()
logger = logging.getLogger(__name__)


class SchwellwerteBody(BaseModel):
    schwelle_a: int
    schwelle_v: int


class IndizUpdateBody(BaseModel):
    punkte: int | None = None
    aktiv: bool | None = None
    bezeichnung: str | None = None


class BehoerdenBody(BaseModel):
    name: str
    kuerzel: str | None = None
    hierarchie_stufe: Literal["ministerium", "oberbehoerde", "mittelbehoerde", "unterbehoerde"]
    besondere_oe: list[str] = []
    aktenzeichen_praefix: str | None = None


class PromptBody(BaseModel):
    inhalt: str


class LLMProviderBody(BaseModel):
    aktiver_provider: Literal["ollama", "scaleway"]
    scaleway_modell: str | None = None


def _scaleway_freigeschaltet() -> bool:
    """Prüft, ob Scaleway als Provider freigeschaltet ist (Egress + AVV vorausgesetzt)."""
    return os.getenv("SCALEWAY_ENABLED", "false").strip().lower() == "true"


@router.get("/api/admin/schwellwerte")
async def get_schwellwerte(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    sw = db.query(BewertungSchwellwerte).first()
    if sw is None:
        raise HTTPException(status_code=404, detail="Keine Schwellwerte gesetzt")
    return {
        "schwelle_a": sw.schwelle_a,
        "schwelle_v": sw.schwelle_v,
        "aktualisiert_am": sw.aktualisiert_am,
        "aktualisiert_von": sw.aktualisiert_von,
    }


@router.put("/api/admin/schwellwerte")
async def put_schwellwerte(
    body: SchwellwerteBody,
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    if body.schwelle_a <= body.schwelle_v:
        raise HTTPException(status_code=400, detail="schwelle_a muss > schwelle_v sein.")
    sw = db.query(BewertungSchwellwerte).first()
    if sw is None:
        sw = BewertungSchwellwerte()
        db.add(sw)
    sw.schwelle_a = body.schwelle_a
    sw.schwelle_v = body.schwelle_v
    user = getattr(request.state, "user", None)
    sw.aktualisiert_von = user.get("name") if isinstance(user, dict) else "admin"
    db.commit()
    return {"status": "ok"}


@router.get("/api/admin/indizien")
async def get_indizien(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    return [
        {
            "id": i.id,
            "schluessel": i.schluessel,
            "bezeichnung": i.bezeichnung,
            "punkte": i.punkte,
            "typ": i.typ.value if hasattr(i.typ, "value") else i.typ,
            "beschreibung": i.beschreibung,
            "aktiv": i.aktiv,
        }
        for i in db.query(IndizKatalog).order_by(IndizKatalog.id).all()
    ]


@router.put("/api/admin/indizien/{indiz_id}")
async def put_indiz(
    indiz_id: int,
    body: IndizUpdateBody,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    i = db.query(IndizKatalog).get(indiz_id)
    if i is None:
        raise HTTPException(status_code=404, detail="Indiz nicht gefunden")
    if body.punkte is not None:
        i.punkte = body.punkte
    if body.aktiv is not None:
        i.aktiv = body.aktiv
    if body.bezeichnung is not None:
        i.bezeichnung = body.bezeichnung
    db.commit()
    return {"status": "ok"}


@router.get("/api/admin/behoerden")
async def get_behoerden(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    return [
        {
            "id": b.id,
            "name": b.name,
            "kuerzel": b.kuerzel,
            "hierarchie_stufe": b.hierarchie_stufe.value
            if hasattr(b.hierarchie_stufe, "value")
            else b.hierarchie_stufe,
            "besondere_oe": b.besondere_oe,
            "aktenzeichen_praefix": b.aktenzeichen_praefix,
        }
        for b in db.query(BehoerdenProfil).order_by(BehoerdenProfil.name).all()
    ]


@router.post("/api/admin/behoerden")
async def post_behoerde(
    body: BehoerdenBody, db: Session = Depends(get_db), _admin: dict = Depends(require_admin)
):
    b = BehoerdenProfil(
        name=body.name,
        kuerzel=body.kuerzel,
        hierarchie_stufe=body.hierarchie_stufe,
        besondere_oe=body.besondere_oe,
        aktenzeichen_praefix=body.aktenzeichen_praefix,
    )
    db.add(b)
    db.commit()
    return {"id": b.id}


@router.put("/api/admin/behoerden/{bid}")
async def put_behoerde(
    bid: int,
    body: BehoerdenBody,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    b = db.query(BehoerdenProfil).get(bid)
    if b is None:
        raise HTTPException(status_code=404, detail="Behörde nicht gefunden")
    b.name = body.name
    b.kuerzel = body.kuerzel
    b.hierarchie_stufe = body.hierarchie_stufe
    b.besondere_oe = body.besondere_oe
    b.aktenzeichen_praefix = body.aktenzeichen_praefix
    db.commit()
    return {"status": "ok"}


@router.delete("/api/admin/behoerden/{bid}")
async def delete_behoerde(
    bid: int, db: Session = Depends(get_db), _admin: dict = Depends(require_admin)
):
    b = db.query(BehoerdenProfil).get(bid)
    if b is None:
        raise HTTPException(status_code=404, detail="Behörde nicht gefunden")
    db.delete(b)
    db.commit()
    return {"status": "ok"}


@router.get("/api/admin/prompts")
async def get_prompts(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    """Liefert die editierbaren System-Prompts; ohne DB-Eintrag den Default."""
    from utils.openai_client import SYSTEM_PROMPT_REGISTRY

    ergebnis = []
    for schluessel, meta in SYSTEM_PROMPT_REGISTRY.items():
        eintrag = db.query(SystemPrompt).filter_by(schluessel=schluessel).first()
        ergebnis.append(
            {
                "schluessel": schluessel,
                "anzeige_name": meta["anzeige_name"],
                "inhalt": eintrag.inhalt if eintrag else meta["default"],
                "aktualisiert_am": eintrag.aktualisiert_am if eintrag else None,
                "aktualisiert_von": eintrag.aktualisiert_von if eintrag else None,
            }
        )
    return ergebnis


@router.put("/api/admin/prompts/{schluessel}")
async def put_prompt(
    schluessel: str,
    body: PromptBody,
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    """Aktualisiert einen System-Prompt; legt den Eintrag bei Bedarf an."""
    from utils.openai_client import SYSTEM_PROMPT_REGISTRY

    if schluessel not in SYSTEM_PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail="Unbekannter Prompt-Schlüssel.")
    if not body.inhalt.strip():
        raise HTTPException(status_code=400, detail="Der Prompt-Inhalt darf nicht leer sein.")

    eintrag = db.query(SystemPrompt).filter_by(schluessel=schluessel).first()
    if eintrag is None:
        eintrag = SystemPrompt(schluessel=schluessel, inhalt=body.inhalt)
        db.add(eintrag)
    else:
        eintrag.inhalt = body.inhalt
    user = getattr(request.state, "user", None)
    eintrag.aktualisiert_von = (
        user.get("name") if isinstance(user, dict) and user.get("name") else "admin"
    )
    db.commit()
    return {"status": "ok"}


@router.get("/api/admin/llm-provider")
async def get_llm_provider(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    """Liefert den aktiven LLM-Provider, das Scaleway-Modell und die Verfügbarkeit."""
    eintrag = db.query(LLMEinstellung).first()
    aktiv = eintrag.aktiver_provider if eintrag else "ollama"
    scaleway_modell = eintrag.scaleway_modell if eintrag else None
    scaleway_frei = _scaleway_freigeschaltet()
    return {
        "aktiver_provider": aktiv,
        "scaleway_modell": scaleway_modell,
        "scaleway_verfuegbar": scaleway_frei,
        "provider": [
            {"id": "ollama", "name": "Ollama (lokal/intern)", "verfuegbar": True},
            {"id": "scaleway", "name": "Scaleway Generative APIs", "verfuegbar": scaleway_frei},
        ],
    }


@router.get("/api/admin/scaleway-modelle")
async def get_scaleway_modelle(_admin: dict = Depends(require_admin)):
    """Liefert die bei Scaleway verfügbaren Modelle (Live-Abruf von /v1/models)."""
    if not _scaleway_freigeschaltet():
        raise HTTPException(
            status_code=400,
            detail="Scaleway ist nicht freigeschaltet (SCALEWAY_ENABLED).",
        )
    from utils.openai_client import liste_scaleway_modelle

    try:
        modelle = await liste_scaleway_modelle()
    except Exception as e:
        logger.warning("Scaleway-Modellliste nicht abrufbar: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Modellliste konnte nicht von Scaleway abgerufen werden. "
            "Bitte Netzwerk-Egress, Base-URL und API-Key prüfen.",
        ) from e
    return {"modelle": modelle}


@router.put("/api/admin/llm-provider")
async def put_llm_provider(
    body: LLMProviderBody,
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    """Setzt den aktiven LLM-Provider und optional das Scaleway-Modell."""
    if body.aktiver_provider == "scaleway" and not _scaleway_freigeschaltet():
        raise HTTPException(
            status_code=400,
            detail="Scaleway ist nicht freigeschaltet (SCALEWAY_ENABLED). "
            "Erst nach Egress-Freigabe und datenschutzrechtlicher Prüfung aktivierbar.",
        )
    eintrag = db.query(LLMEinstellung).first()
    if eintrag is None:
        eintrag = LLMEinstellung()
        db.add(eintrag)
    eintrag.aktiver_provider = body.aktiver_provider
    # Modell nur ändern, wenn das Feld im Request enthalten war. Leerer
    # String → NULL (kein Modell gesetzt → Env-Default greift).
    if "scaleway_modell" in body.model_fields_set:
        eintrag.scaleway_modell = body.scaleway_modell or None
    user = getattr(request.state, "user", None)
    eintrag.aktualisiert_von = (
        user.get("name") if isinstance(user, dict) and user.get("name") else "admin"
    )
    db.commit()
    logger.info("LLM-Provider umgestellt auf: %s", eintrag.aktiver_provider)
    return {
        "status": "ok",
        "aktiver_provider": eintrag.aktiver_provider,
        "scaleway_modell": eintrag.scaleway_modell,
    }


class RolleBody(BaseModel):
    is_admin: bool


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "approved": u.approved,
        "is_admin": u.is_admin,
        "erstellt_am": u.erstellt_am,
    }


@router.get("/api/admin/users")
async def get_users(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    """Listet alle Nutzer (für die Admin-Freischaltung)."""
    users = db.query(User).order_by(User.erstellt_am.desc()).all()
    return {"users": [_user_dict(u) for u in users]}


@router.put("/api/admin/users/{user_id}/freischalten")
async def freischalten(
    user_id: int, db: Session = Depends(get_db), _admin: dict = Depends(require_admin)
):
    """Schaltet einen Nutzer frei (approved=true)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    user.approved = True
    db.commit()
    logger.info("Nutzer freigeschaltet: user_id=%s", user.id)
    return {"status": "ok"}


@router.put("/api/admin/users/{user_id}/rolle")
async def rolle_setzen(
    user_id: int,
    body: RolleBody,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    """Setzt/entfernt die Admin-Rolle. Der letzte Admin darf nicht degradiert werden."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    if not body.is_admin and user.is_admin:
        andere_admins = db.query(User).filter(User.is_admin.is_(True), User.id != user_id).count()
        if andere_admins == 0:
            raise HTTPException(
                status_code=400, detail="Der letzte Admin kann nicht degradiert werden."
            )
    user.is_admin = body.is_admin
    if body.is_admin:
        user.approved = True  # Admins sind immer freigeschaltet
    db.commit()
    return {"status": "ok"}


@router.delete("/api/admin/users/{user_id}")
async def user_loeschen(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    """Löscht einen Nutzer. Selbstlöschung und Löschen des letzten Admins verhindert."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    if _admin.get("id") == user_id:
        raise HTTPException(
            status_code=400, detail="Der eigene Account kann nicht gelöscht werden."
        )
    if user.is_admin:
        andere_admins = db.query(User).filter(User.is_admin.is_(True), User.id != user_id).count()
        if andere_admins == 0:
            raise HTTPException(
                status_code=400, detail="Der letzte Admin kann nicht gelöscht werden."
            )
    db.delete(user)
    db.commit()
    logger.info("Nutzer gelöscht: user_id=%s", user_id)
    return {"status": "ok"}
