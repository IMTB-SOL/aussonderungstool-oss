"""Legal-Router: /impressum und /datenschutz als eigenständige HTML-Seiten.

Die Seiten sind ohne Login erreichbar (siehe routers.auth._AUTH_AUSNAHMEN),
damit Pflichtangaben (§ 5 DDG, Art. 13 DSGVO) jederzeit abrufbar sind.

Die Inhalte gehören dem jeweiligen Betreiber der Installation und sind
deshalb nicht im Quellcode hinterlegt. Der Betreiber stellt zwei
HTML-Fragmente bereit und verweist per Umgebungsvariable darauf:

    IMPRESSUM_DATEI=/pfad/zu/impressum.html
    DATENSCHUTZ_DATEI=/pfad/zu/datenschutz.html

Die Dateien werden unverändert in die Seitenhülle eingebettet (vertrauens-
würdiger Betreiber-Inhalt, kein Nutzer-Input). Fehlt eine Datei, erscheint
ein neutraler Hinweis.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Gemeinsame Hülle — einheitliches Look-and-Feel ohne externe CSS-Abhängigkeit
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --primary: #E4523B;
  --text: #3C4058;
  --muted: #82849F;
  --border: #E0E0E0;
  --bg: #FDFCF9;
  --bg-card: #FFFFFF;
}
* { box-sizing: border-box; }
html { font-size: 16px; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, -apple-system, Arial, sans-serif;
  line-height: 1.6;
}
.legal-wrapper {
  max-width: 760px;
  margin: 0 auto;
  padding: 48px 24px 96px;
}
.legal-header {
  border-bottom: 1px solid var(--border);
  padding-bottom: 16px;
  margin-bottom: 32px;
}
.legal-header h1 {
  margin: 0 0 4px;
  font-size: 28px;
  font-weight: 600;
  color: var(--text);
}
.legal-meta {
  color: var(--muted);
  font-size: 14px;
}
.legal-body h2 {
  font-size: 18px;
  font-weight: 600;
  margin-top: 32px;
  margin-bottom: 8px;
  color: var(--text);
}
.legal-body p,
.legal-body ul {
  margin: 8px 0;
}
.legal-body ul {
  padding-left: 20px;
}
.legal-body a {
  color: var(--primary);
  text-decoration: underline;
}
.legal-footer {
  border-top: 1px solid var(--border);
  margin-top: 48px;
  padding-top: 16px;
  color: var(--muted);
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.legal-footer a {
  color: var(--muted);
}
"""


def _huelle(titel: str, koerper_html: str, untertitel: str = "") -> str:
    """Wickelt den Seiten-Inhalt in HTML5 mit Sicherheits-Headern-tauglichem CSS."""
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titel} — Aussonderungstool</title>
<meta name="robots" content="noindex,nofollow">
<style>{_CSS}</style>
</head>
<body>
<main class="legal-wrapper">
  <header class="legal-header">
    <h1>{titel}</h1>
    {f'<div class="legal-meta">{untertitel}</div>' if untertitel else ""}
  </header>
  <section class="legal-body">
{koerper_html}
  </section>
  <footer class="legal-footer">
    <nav>
      <a href="/impressum">Impressum</a> · <a href="/datenschutz">Datenschutz</a>
    </nav>
  </footer>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Inhalte — vom Betreiber bereitgestellt
# ---------------------------------------------------------------------------

_MAX_FRAGMENT_BYTES = 512 * 1024

_PLATZHALTER = """
    <p>
      Für diese Installation wurde noch kein Inhalt hinterlegt. Der Betreiber
      stellt die Angaben über die Umgebungsvariable <code>{variable}</code>
      bereit (Pfad zu einer HTML-Datei).
    </p>
"""


def _lade_fragment(variable: str) -> str:
    """Liest das vom Betreiber hinterlegte HTML-Fragment oder liefert den Platzhalter."""
    pfad = os.getenv(variable, "").strip()
    if pfad:
        try:
            datei = Path(pfad)
            if datei.stat().st_size > _MAX_FRAGMENT_BYTES:
                raise ValueError("Datei zu groß")
            return datei.read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeDecodeError) as e:
            logger.error("%s konnte nicht gelesen werden: %s", variable, type(e).__name__)
    return _PLATZHALTER.format(variable=variable)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/impressum", response_class=HTMLResponse, include_in_schema=False)
async def impressum() -> HTMLResponse:
    """Impressum des Betreibers — ohne Login erreichbar."""
    return HTMLResponse(_huelle("Impressum", _lade_fragment("IMPRESSUM_DATEI")))


@router.get("/datenschutz", response_class=HTMLResponse, include_in_schema=False)
async def datenschutz() -> HTMLResponse:
    """Datenschutzerklärung des Betreibers — ohne Login erreichbar."""
    return HTMLResponse(_huelle("Datenschutzerklärung", _lade_fragment("DATENSCHUTZ_DATEI")))
