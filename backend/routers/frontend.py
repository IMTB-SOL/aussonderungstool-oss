"""Frontend-Endpoints: Tool-SPA und Login-Seite mit BASE_PATH-Injektion."""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


def _html_mit_basepath(dateiname: str) -> HTMLResponse:
    """Liest eine Frontend-HTML-Datei und injiziert den BASE_PATH in <base href>."""
    pfad = FRONTEND_DIR / dateiname
    if not pfad.is_file():
        raise HTTPException(status_code=404, detail="Seite nicht gefunden.")
    html = pfad.read_text(encoding="utf-8")
    base_path = os.getenv("BASE_PATH", "").rstrip("/")
    if base_path:
        html = html.replace('<base href="/" />', f'<base href="{base_path}/" />')
    return HTMLResponse(content=html)


@router.get("/", include_in_schema=False)
async def _index():
    """Startseite — liefert direkt die Tool-SPA."""
    return _html_mit_basepath("app.html")


@router.get("/app.html", include_in_schema=False)
async def _app_html():
    """Tool-SPA mit injiziertem BASE_PATH."""
    return _html_mit_basepath("app.html")


@router.get("/login.html", include_in_schema=False)
async def _login_html():
    """Login-/Registrier-Seite mit injiziertem BASE_PATH."""
    return _html_mit_basepath("login.html")
