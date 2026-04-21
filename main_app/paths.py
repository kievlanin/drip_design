"""Single source of truth for repository-root-relative paths."""
import os
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_EARTHDATA_TXT_LOADED = False


def _parse_earthdata_netrc_blocks(text: str) -> List[Tuple[str, str, str]]:
    """machine / login / password → список (machine, user, password)."""
    machine = ""
    login_v = ""
    pass_v = ""
    blocks: List[Tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(" ")
        key_l = key.lower()
        val = rest.lstrip()
        if key_l == "machine":
            if machine and login_v and pass_v:
                blocks.append((machine, login_v, pass_v))
            machine = val.strip()
            login_v = ""
            pass_v = ""
        elif key_l == "login":
            login_v = val.strip()
        elif key_l == "password":
            pass_v = val
            if machine and login_v and pass_v:
                blocks.append((machine, login_v, pass_v))
                machine = ""
                login_v = ""
                pass_v = ""
    if machine and login_v and pass_v:
        blocks.append((machine, login_v, pass_v))
    return blocks


def _pick_earthdata_block(blocks: List[Tuple[str, str, str]]) -> Optional[Tuple[str, str]]:
    if not blocks:
        return None
    for m, u, p in blocks:
        ml = m.lower()
        if "urs.earthdata.nasa.gov" in ml or ml.endswith("earthdata.nasa.gov"):
            return (u, p)
    for m, u, p in blocks:
        if "earthdata" in m.lower():
            return (u, p)
    m, u, p = blocks[-1]
    return (u, p)


def load_earthdata_credentials_from_project_file() -> None:
    """
    Якщо в середовищі ще немає пари EARTHDATA_USER + EARTHDATA_PASSWORD,
    читає з кореня проєкту EarthData.txt (або earthdata.txt) у стилі .netrc:
    machine … / login … / password …
    Значення з env мають пріоритет (функція ідемпотентна).
    """
    global _EARTHDATA_TXT_LOADED
    if (os.getenv("EARTHDATA_USER") or "").strip() and (
        os.getenv("EARTHDATA_PASSWORD") or ""
    ).strip():
        _EARTHDATA_TXT_LOADED = True
        return
    if _EARTHDATA_TXT_LOADED:
        return
    for fname in ("EarthData.txt", "earthdata.txt", "Earthdata.txt"):
        path = PROJECT_ROOT / fname
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            _EARTHDATA_TXT_LOADED = True
            return
        blocks = _parse_earthdata_netrc_blocks(text)
        picked = _pick_earthdata_block(blocks)
        if picked:
            u, p = picked
            if u and p:
                os.environ.setdefault("EARTHDATA_USER", u)
                os.environ.setdefault("EARTHDATA_PASSWORD", p)
        _EARTHDATA_TXT_LOADED = True
        return
    # Файлу ще немає — не кешуємо «пропуск», щоб пізніше доданий EarthData.txt підхопився.

PIPES_DB_PATH = PROJECT_ROOT / "pipes_db.json"
DRIPPERS_DB_PATH = PROJECT_ROOT / "drippers_db.json"
DRIPPERLINES_DB_PATH = PROJECT_ROOT / "dripperlines_db.json"
LATERALS_DB_PATH = PROJECT_ROOT / "laterals_db.json"
DESIGNS_DIR = PROJECT_ROOT / "designs"
# Повні тайли SRTM (.hgt), без різання — за межами імпортованого KML / поля
SRTM_DIR = PROJECT_ROOT / "_srtm_"
# Кеш відповідей Overpass для модуля osm_cad_context (резерв / тести; у UI карти векторний шар вимкнено)
OSM_CAD_CACHE_DIR = PROJECT_ROOT / "_osm_cad_cache_"
