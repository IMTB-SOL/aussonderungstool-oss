"""Hilfsfunktionen zum Entpacken und Packen von ZIP-Dateien."""

import logging
import zipfile
from pathlib import Path

from utils.logging_setup import pseudonymisiere_dateiname

logger = logging.getLogger(__name__)

# Zip-Bomb-Schutz: Grenzen, die für JEDES entpackte ZIP gelten — auch für
# innere ZIPs aus DOMEA-Lieferverzeichnis-Bundles.
MAX_ZIP_EINTRAEGE = 10_000
MAX_ZIP_UNKOMPRIMIERT = 5 * 1024 * 1024 * 1024  # 5 GB
# Größter eingebetteter Container, der für die Dateisuche in den RAM geladen wird.
MAX_CONTAINER_IM_SPEICHER = 512 * 1024 * 1024  # 512 MB

# Endungen, unter denen DMS-Systeme XDOMEA-Nachrichten ablegen. nscale eGOV
# benennt seine Container '.xdomea', obwohl es ZIPs mit XML darin sind.
XDOMEA_XML_ENDUNGEN = {".xml", ".xdomea"}
# Dateien, die als eingebetteter Paket-Container in Frage kommen.
CONTAINER_ENDUNGEN = {".zip", ".xdomea"}


def _ist_symlink_eintrag(info: zipfile.ZipInfo) -> bool:
    """True, wenn der ZIP-Eintrag einen Unix-Symlink darstellt."""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def entpacke_zip(zip_pfad: Path, ziel_verzeichnis: Path) -> None:
    """
    Entpackt eine ZIP-Datei sicher in das Zielverzeichnis.

    Schützt gegen Zip-Slip (Path-Traversal), Symlink-Einträge und Zip-Bomben.
    Jeder Eintrag wird vor dem Entpacken geprüft; schlägt eine Prüfung fehl,
    wird nichts entpackt.

    Raises:
        ValueError: Bei ungültigem ZIP, Path-Traversal, Symlinks oder
            überschrittenen Zip-Bomb-Grenzen.
        RuntimeError: Bei sonstigen Entpackfehlern.
    """
    try:
        if not zipfile.is_zipfile(zip_pfad):
            raise ValueError(f"Keine gültige ZIP-Datei: {zip_pfad}")

        ziel = Path(ziel_verzeichnis).resolve()
        with zipfile.ZipFile(zip_pfad, "r") as zf:
            eintraege = zf.infolist()

            if len(eintraege) > MAX_ZIP_EINTRAEGE:
                raise ValueError(
                    f"ZIP enthält zu viele Einträge: {len(eintraege)} "
                    f"(Maximum: {MAX_ZIP_EINTRAEGE})."
                )

            gesamt_unkomprimiert = sum(info.file_size for info in eintraege)
            if gesamt_unkomprimiert > MAX_ZIP_UNKOMPRIMIERT:
                raise ValueError(
                    f"Unkomprimierte Gesamtgröße zu groß: "
                    f"{gesamt_unkomprimiert / (1024**3):.1f} GB."
                )

            for info in eintraege:
                if _ist_symlink_eintrag(info):
                    raise ValueError(f"Symlink im ZIP nicht erlaubt: '{info.filename}'.")
                zielpfad = (ziel / info.filename).resolve()
                if zielpfad != ziel and not zielpfad.is_relative_to(ziel):
                    raise ValueError(f"Path-Traversal erkannt im ZIP-Eintrag: '{info.filename}'.")

            zf.extractall(ziel_verzeichnis)
        logger.info(
            "ZIP entpackt: %s → %s", pseudonymisiere_dateiname(str(zip_pfad)), ziel_verzeichnis
        )
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"Fehler beim Entpacken von {zip_pfad}: {e}") from e


def packe_zip(quell_verzeichnis: Path, ziel_zip: Path, komprimieren: bool = True) -> None:
    """
    Packt den Inhalt eines Verzeichnisses in eine neue ZIP-Datei.

    Verzeichnisse mit dem Präfix '_lv_inner_' werden als temporäre
    Lieferverzeichnis-Auspack-Ordner erkannt und NICHT mitgepackt — sonst
    würde jeder Agent die Bundle-Inhalte doppeln.

    Args:
        quell_verzeichnis: Verzeichnis mit den Dateien.
        ziel_zip: Pfad zur Ziel-ZIP-Datei.
        komprimieren: True für ZIP_DEFLATED (langsam, klein), False für ZIP_STORED (schnell, groß).

    Raises:
        RuntimeError: Bei Packfehlern.
    """
    try:
        methode = zipfile.ZIP_DEFLATED if komprimieren else zipfile.ZIP_STORED
        with zipfile.ZipFile(ziel_zip, "w", compression=methode) as zf:
            for datei in quell_verzeichnis.rglob("*"):
                if not datei.is_file():
                    continue
                # Temp-Verzeichnisse aus LV-Bundle-Auspackung ausschließen
                if any(
                    p.startswith("_lv_inner_") for p in datei.relative_to(quell_verzeichnis).parts
                ):
                    continue
                zf.write(datei, datei.relative_to(quell_verzeichnis))
        logger.info(
            "ZIP erstellt: %s (komprimiert=%s)",
            pseudonymisiere_dateiname(str(ziel_zip)),
            komprimieren,
        )
    except Exception as e:
        raise RuntimeError(f"Fehler beim Packen von {ziel_zip}: {e}") from e


def _ist_xdomea_container(pfad: Path) -> bool:
    """True, wenn die Datei ein Archiv ist, das eine XDOMEA-Nachricht enthält.

    Bewusst inhalts- statt endungsbasiert: nscale eGOV liefert seine inneren
    Pakete mit der Endung '.xdomea', andere DMS als '.zip'. Umgekehrt darf ein
    ZIP, das als Nutzdokument im Paket liegt, NICHT ausgepackt werden — das
    Kriterium ist deshalb eine XDOMEA-XML im Archiv, nicht die Endung.
    """
    try:
        if not zipfile.is_zipfile(pfad):
            return False
        with zipfile.ZipFile(pfad, "r") as zf:
            for eintrag in zf.namelist()[:MAX_ZIP_EINTRAEGE]:
                if Path(eintrag).suffix.lower() not in XDOMEA_XML_ENDUNGEN:
                    continue
                with zf.open(eintrag) as f:
                    kopf = f.read(8192).decode("utf-8", errors="ignore").lower()
                if "xdomea" in kopf or "aussonderunglisten" in kopf:
                    return True
    except Exception as e:
        logger.debug(
            "Container-Prüfung fehlgeschlagen für %s: %s", pseudonymisiere_dateiname(pfad.name), e
        )
    return False


def entpacke_container_archive(arbeitsverzeichnis: Path, max_runden: int = 3) -> int:
    """Entpackt alle eingebetteten XDOMEA-Container eines Pakets.

    Deckt zwei Praxisfälle ab, in denen die eigentlichen Nachrichten nicht
    direkt im hochgeladenen ZIP liegen:

      - DOMEA-Lieferverzeichnis-Bundles (Bundesarchiv-Schema), deren Akten-
        bzw. Vorgangs-Container referenziert sind — bei nscale eGOV mit der
        Endung '.xdomea'
      - Sammel-ZIPs ohne Lieferverzeichnis, die mehrere Pakete bündeln
        (z.B. elKA "Beispielaussonderungen")

    Die Container werden in Unterordner `_lv_inner_<stem>/` entpackt, damit
    nachfolgende Agenten ihre rglob-basierte Logik unverändert anwenden können.
    `packe_zip` schließt diese Ordner beim Neupacken aus.

    Args:
        arbeitsverzeichnis: Entpacktes Paketverzeichnis.
        max_runden: Wie tief verschachtelte Container verfolgt werden.

    Returns:
        Anzahl entpackter Container (0 wenn keine gefunden).
    """
    gesamt = 0

    for _ in range(max_runden):
        kandidaten = [
            p
            for p in arbeitsverzeichnis.rglob("*")
            if p.is_file()
            and p.suffix.lower() in CONTAINER_ENDUNGEN
            and not any(teil.startswith("_lv_inner_") for teil in p.parts)
        ]

        in_dieser_runde = 0
        for archiv in kandidaten:
            ziel = arbeitsverzeichnis / f"_lv_inner_{archiv.stem}"
            # Bereits entpackt (z.B. durch einen vorherigen Agenten) — sonst
            # würden Dateien doppelt gezählt.
            if ziel.exists() and any(ziel.rglob("*")):
                continue
            if not _ist_xdomea_container(archiv):
                continue
            ziel.mkdir(parents=True, exist_ok=True)
            try:
                entpacke_zip(archiv, ziel)
                in_dieser_runde += 1
            except Exception as e:
                logger.warning(
                    "Container %s konnte nicht entpackt werden: %s",
                    pseudonymisiere_dateiname(archiv.name),
                    e,
                )

        if in_dieser_runde == 0:
            break
        gesamt += in_dieser_runde

    # Auch ohne neue Auspackung die bereits vorhandenen Container melden,
    # damit wiederholte Aufrufe denselben Wert liefern.
    if gesamt == 0:
        gesamt = len(
            [p for p in arbeitsverzeichnis.glob("_lv_inner_*") if p.is_dir() and any(p.rglob("*"))]
        )
    else:
        logger.info("%d eingebettete XDOMEA-Container entpackt.", gesamt)

    return gesamt


# Alter Name — die Funktion deckt inzwischen mehr als Lieferverzeichnis-Bundles ab.
entpacke_lv_bundle_falls_vorhanden = entpacke_container_archive


def finde_datei_in_paket(
    zip_pfad: Path,
    basisname: str,
    max_groesse: int = 25 * 1024 * 1024,
    max_tiefe: int = 2,
) -> bytes | None:
    """Sucht eine Datei im Paket — auch in eingebetteten Containern.

    Bei nscale eGOV und elKA liegen die Schriftstücke nicht direkt im
    hochgeladenen ZIP, sondern in einem eingebetteten Container. Ohne diese
    Suche zeigt die Strukturansicht Dokumente an, die sich nicht öffnen lassen.

    Verglichen wird ausschließlich der Basisname gegen die tatsächlichen
    Archiveinträge — aus der Eingabe wird nie ein Pfad konstruiert.

    Args:
        zip_pfad: Das Paket-ZIP.
        basisname: Gesuchter Dateiname (nur der Basisname wird ausgewertet).
        max_groesse: Obergrenze für die ausgelieferte Datei.
        max_tiefe: Wie tief in verschachtelte Container gestiegen wird.

    Returns:
        Dateiinhalt oder None, wenn nichts gefunden wurde.

    Raises:
        ValueError: Wenn die gefundene Datei größer als `max_groesse` ist.
        zipfile.BadZipFile: Wenn das äußere Archiv beschädigt ist.
    """
    gesucht = Path(basisname or "").name
    if not gesucht:
        return None
    return _suche_in_archiv(zip_pfad, gesucht, max_groesse, max_tiefe)


def _suche_in_archiv(
    archiv: Path | zipfile.ZipFile, gesucht: str, max_groesse: int, tiefe: int
) -> bytes | None:
    """Durchsucht ein Archiv rekursiv nach `gesucht` (Basisnamen-Vergleich)."""
    schliessen = not isinstance(archiv, zipfile.ZipFile)
    zf = zipfile.ZipFile(archiv, "r") if schliessen else archiv
    try:
        eintraege = [i for i in zf.infolist() if not i.is_dir()]

        for info in eintraege:
            if Path(info.filename).name != gesucht:
                continue
            if info.file_size > max_groesse:
                raise ValueError(f"Datei '{gesucht}' ist zu groß für die Auslieferung.")
            return zf.read(info)

        if tiefe <= 0:
            return None

        # Nicht gefunden — in eingebettete Container absteigen.
        for info in eintraege:
            if Path(info.filename).suffix.lower() not in CONTAINER_ENDUNGEN:
                continue
            # Eingebettete Container werden zum Durchsuchen in den RAM gelesen —
            # deshalb deutlich enger begrenzt als das Entpacken auf Platte.
            if info.file_size > MAX_CONTAINER_IM_SPEICHER:
                continue
            try:
                with zf.open(info) as roh:
                    import io

                    puffer = io.BytesIO(roh.read())
                if not zipfile.is_zipfile(puffer):
                    continue
                with zipfile.ZipFile(puffer, "r") as inner:
                    treffer = _suche_in_archiv(inner, gesucht, max_groesse, tiefe - 1)
                if treffer is not None:
                    return treffer
            except ValueError:
                raise
            except Exception as e:
                logger.debug(
                    "Container %s nicht durchsuchbar: %s",
                    pseudonymisiere_dateiname(info.filename),
                    e,
                )
        return None
    finally:
        if schliessen:
            zf.close()
