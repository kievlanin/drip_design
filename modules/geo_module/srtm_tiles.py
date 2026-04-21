"""
Повні тайли SRTM у форматі .hgt (CGIAR / AWS Skadi; опційно NASA Earthdata).
Earthdata: або власний HTTP (EARTHDATA_SRTM_TILE_BASE + Basic auth), або пакет earthaccess
(search_data SRTMGL1 → download → розпаковка .zip з LP DAAC).
"""
from __future__ import annotations

import base64
import gzip
import math
import os
import queue
import re
import struct
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from main_app.paths import SRTM_DIR, load_earthdata_credentials_from_project_file

# Публічне дзеркало void-filled SRTM (gzip .hgt.gz)
SKADI_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"

_hgt_cache: dict[str, "HgtTile"] = {}


def tile_source_for_schedule_mode(schedule_mode: str) -> str:
    """
    Джерело HTTP для завантаження тайлів .hgt (не плутати з API точкових висот).
    auto / skadi_local → Skadi; earthdata → власний URL (EARTHDATA_SRTM_TILE_BASE + auth)
    або earthaccess (SRTMGL1); open_elevation → не підтримує файли тайлів.
    """
    m = str(schedule_mode or "auto").strip().lower()
    if m == "earthdata":
        return "earthdata"
    if m == "open_elevation":
        return "open_elevation"
    return "skadi"


def resolve_tile_source_from_app(app) -> str:
    if app is None:
        return "skadi"
    try:
        if hasattr(app, "normalize_consumer_schedule"):
            app.normalize_consumer_schedule()
        cs = getattr(app, "consumer_schedule", None) or {}
        mode = str(cs.get("srtm_source_mode", "auto")).strip().lower()
    except Exception:
        mode = "auto"
    return tile_source_for_schedule_mode(mode)


def _earthdata_tile_url(lat_sw: int, lon_sw: int) -> Optional[str]:
    """
    База URL у тому ж вигляді, що й Skadi: {base}/{N50}/N50E029.hgt.gz
    Задайте EARTHDATA_SRTM_TILE_BASE (без завершального слеша).
    Опційно EARTHDATA_SRTM_TILE_SUFFIX (за замовчуванням .hgt.gz), напр. .hgt для нестиснутого.
    """
    base = (os.getenv("EARTHDATA_SRTM_TILE_BASE", "") or "").strip().rstrip("/")
    if not base:
        return None
    stem = tile_base_name(lat_sw, lon_sw)
    if stem.lower().endswith(".hgt"):
        stem = stem[:-4]
    sub = tile_s3_subdir(lat_sw)
    suffix = (os.getenv("EARTHDATA_SRTM_TILE_SUFFIX", ".hgt.gz") or ".hgt.gz").strip()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    return f"{base}/{sub}/{stem}{suffix}"


def _earthdata_basic_auth_header() -> Optional[str]:
    load_earthdata_credentials_from_project_file()
    user = (os.getenv("EARTHDATA_USER", "") or "").strip()
    password = (os.getenv("EARTHDATA_PASSWORD", "") or "").strip()
    if not user or not password:
        return None
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


_earthaccess_lock = threading.Lock()
_earthaccess_login_ok = False

# Діалог логіну (Tk): задається з DripCAD — schedule_on_main(fn) викликає fn у головному потоку.
_schedule_on_main: Optional[Callable[[Callable[[], None]], None]] = None
_tk_master = None


def configure_earthdata_tk_bridge(
    schedule_on_main: Optional[Callable[[Callable[[], None]], None]],
    tk_master=None,
) -> None:
    """Підключити UI для запиту Earthdata при невдалому earthaccess.login (напр. з фонового потоку)."""
    global _schedule_on_main, _tk_master
    _schedule_on_main = schedule_on_main
    _tk_master = tk_master


def _earthdata_credentials_toplevel(master, hint: str) -> Tuple[Optional[str], Optional[str]]:
    """Модальний діалог; викликати лише з головного потоку Tk."""
    import tkinter as tk
    from tkinter import ttk

    out_u: List[Optional[str]] = [None]
    out_p: List[Optional[str]] = [None]
    top = tk.Toplevel(master)
    top.title("NASA Earthdata")
    top.configure(bg="#2b2b2b")
    top.resizable(False, False)
    msg = (hint or "").strip() or "Введіть логін і пароль NASA Earthdata Login (URS)."
    tk.Label(
        top,
        text=msg,
        bg="#2b2b2b",
        fg="#e0e0e0",
        wraplength=420,
        justify=tk.LEFT,
        font=("Segoe UI", 9),
    ).pack(anchor=tk.W, padx=12, pady=(12, 6))
    tk.Label(top, text="Логін:", bg="#2b2b2b", fg="#bdbdbd", font=("Segoe UI", 9)).pack(
        anchor=tk.W, padx=12
    )
    e_user = ttk.Entry(top, width=44)
    e_user.pack(padx=12, pady=(0, 6))
    tk.Label(top, text="Пароль:", bg="#2b2b2b", fg="#bdbdbd", font=("Segoe UI", 9)).pack(
        anchor=tk.W, padx=12
    )
    e_pass = ttk.Entry(top, width=44, show="*")
    e_pass.pack(padx=12, pady=(0, 10))
    ex_u = (os.getenv("EARTHDATA_USERNAME") or os.getenv("EARTHDATA_USER") or "").strip()
    if ex_u:
        e_user.insert(0, ex_u)

    def _ok() -> None:
        u = e_user.get().strip()
        p = e_pass.get()
        if not u or not p:
            return
        out_u[0], out_p[0] = u, p
        top.destroy()

    def _cancel() -> None:
        out_u[0], out_p[0] = None, None
        top.destroy()

    bf = tk.Frame(top, bg="#2b2b2b")
    bf.pack(pady=(0, 12))
    ttk.Button(bf, text="OK", command=_ok).pack(side=tk.LEFT, padx=(12, 6))
    ttk.Button(bf, text="Скасувати", command=_cancel).pack(side=tk.LEFT)
    top.transient(master)
    top.grab_set()
    top.protocol("WM_DELETE_WINDOW", _cancel)
    e_user.focus_set()
    top.bind("<Return>", lambda _e: _ok())
    top.wait_window()
    return out_u[0], out_p[0]


def _prompt_earthdata_credentials_from_any_thread(hint: str) -> Tuple[Optional[str], Optional[str]]:
    if _schedule_on_main is None or _tk_master is None:
        return None, None
    q: "queue.Queue[Tuple[Optional[str], Optional[str]]]" = queue.Queue(maxsize=1)

    def run_on_main() -> None:
        try:
            u, p = _earthdata_credentials_toplevel(_tk_master, hint)
        except Exception:
            u, p = None, None
        try:
            q.put((u, p), block=False)
        except queue.Full:
            pass

    try:
        _schedule_on_main(run_on_main)
    except Exception:
        return None, None
    try:
        return q.get(timeout=600)
    except queue.Empty:
        return None, None


def _reset_earthaccess_login_state() -> None:
    """Для тестів або після явної зміни облікових даних Earthdata."""
    global _earthaccess_login_ok
    _earthaccess_login_ok = False


def _earthaccess_pick_login_strategy() -> str:
    """Без stdin з фонового потоку: у GUI — environment або netrc; інакше all."""
    load_earthdata_credentials_from_project_file()
    pw = (os.getenv("EARTHDATA_PASSWORD") or "").strip()
    u = (os.getenv("EARTHDATA_USER") or os.getenv("EARTHDATA_USERNAME") or "").strip()
    if pw and u:
        return "environment"
    if _schedule_on_main is not None and _tk_master is not None:
        return "netrc"
    return "all"


def _ensure_earthaccess_login(earthaccess_mod, strategy: str) -> Tuple[bool, str]:
    global _earthaccess_login_ok
    if _earthaccess_login_ok:
        return True, ""
    try:
        earthaccess_mod.login(strategy=strategy, persist=True)
        _earthaccess_login_ok = True
        return True, ""
    except Exception as e:
        return False, str(e)


def _hgt_bytes_from_earthaccess_paths(paths: List[Path], stem_core: str) -> bytes:
    """LP DAAC віддає .zip із файлом *.hgt усередині."""
    for p in paths:
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf == ".hgt":
            return p.read_bytes()
        if suf == ".zip":
            with zipfile.ZipFile(p, "r") as zf:
                members = [n for n in zf.namelist() if not n.endswith("/")]
                hgt_names = [
                    n
                    for n in members
                    if n.lower().split("/")[-1].endswith(".hgt")
                ]
                if not hgt_names:
                    continue
                leaf_pref = [
                    n
                    for n in hgt_names
                    if stem_core in n.replace("\\", "/").split("/")[-1].upper()
                ]
                pick = leaf_pref[0] if leaf_pref else hgt_names[0]
                return zf.read(pick)
    raise ValueError("немає .hgt у завантажених файлах earthaccess")


def _download_tile_payload_earthaccess(
    lat_sw: int,
    lon_sw: int,
    cache_dir: Path,
    name: str,
) -> Tuple[bool, str, Optional[bytes]]:
    """
    NASA LP DAAC SRTMGL1 v003 через earthaccess (без ручного EARTHDATA_SRTM_TILE_BASE).
    """
    try:
        import earthaccess
    except ImportError:
        return (
            False,
            "немає пакета earthaccess (pip install earthaccess) або задайте "
            "EARTHDATA_SRTM_TILE_BASE + EARTHDATA_USER / EARTHDATA_PASSWORD.",
            None,
        )

    stem_core = name[:-4] if name.lower().endswith(".hgt") else name

    err_login = ""
    while True:
        strat = _earthaccess_pick_login_strategy()
        with _earthaccess_lock:
            ok_login, err_login = _ensure_earthaccess_login(earthaccess, strat)
        if ok_login:
            break
        u, p = _prompt_earthdata_credentials_from_any_thread(
            f"Earthdata (earthaccess): не вдалося увійти.\n{err_login}"
        )
        if not u or not p:
            return (
                False,
                f"Earthdata (earthaccess): не вдалося увійти ({err_login}). Скасовано або не задано UI.",
                None,
            )
        os.environ["EARTHDATA_USER"] = u.strip()
        os.environ["EARTHDATA_USERNAME"] = u.strip()
        os.environ["EARTHDATA_PASSWORD"] = p
        _reset_earthaccess_login_state()

    with _earthaccess_lock:
        try:
            granules = earthaccess.search_data(
                short_name="SRTMGL1",
                version="003",
                granule_name=f"{stem_core}*",
                count=5,
            )
        except Exception as e:
            return False, f"CMR search_data: {e}", None
        if not granules:
            return (
                False,
                f"CMR: не знайдено гранулу SRTMGL1 для {stem_core}",
                None,
            )
        granule0 = granules[0]

    try:
        try:
            paths = earthaccess.download(
                granule0,
                local_path=str(cache_dir),
                threads=1,
                show_progress=False,
            )
        except TypeError:
            paths = earthaccess.download(
                granule0,
                local_path=str(cache_dir),
                threads=1,
            )
    except Exception as e:
        return False, f"earthaccess.download: {e}", None

    if not paths:
        return False, "earthaccess.download не повернув шляхів до файлів", None

    path_list = [Path(x) for x in paths]
    try:
        raw = _hgt_bytes_from_earthaccess_paths(path_list, stem_core.upper())
    except Exception as e:
        return False, str(e), None

    for p in path_list:
        try:
            if p.is_file() and p.suffix.lower() == ".zip":
                p.unlink(missing_ok=True)
        except OSError:
            pass

    return True, "", raw


def ensure_srtm_dir() -> Path:
    SRTM_DIR.mkdir(parents=True, exist_ok=True)
    return SRTM_DIR


def local_xy_to_lat_lon(x: float, y: float, ref_lon: float, ref_lat: float, R: float = 6378137.0) -> Tuple[float, float]:
    lat = ref_lat + math.degrees(-y / R)
    lon = ref_lon + math.degrees(x / (R * math.cos(math.radians(ref_lat))))
    return lat, lon


def lat_lon_to_local_xy(lat: float, lon: float, ref_lon: float, ref_lat: float, R: float = 6378137.0) -> Tuple[float, float]:
    """Обернене до local_xy_to_lat_lon (локальні метри відносно geo_ref)."""
    y = -math.radians(lat - ref_lat) * R
    x = math.radians(lon - ref_lon) * R * math.cos(math.radians(ref_lat))
    return x, y


_HGT_NAME_RE = re.compile(r"^([NS])(\d{2})([EW])(\d{3})$", re.IGNORECASE)


def parse_hgt_tile_sw_from_stem(stem: str) -> Optional[Tuple[int, int]]:
    """N49E024 → (49, 24) південно-західний кут тайлу в градусах."""
    s = stem.strip()
    if s.lower().endswith(".hgt"):
        s = s[:-4]
    m = _HGT_NAME_RE.match(s)
    if not m:
        return None
    lat = int(m.group(2)) * (1 if m.group(1).upper() == "N" else -1)
    lon = int(m.group(4)) * (1 if m.group(3).upper() == "E" else -1)
    return lat, lon


def hgt_path_tile_stem(path: Path) -> str:
    n = path.name
    if n.lower().endswith(".hgt.gz"):
        return n[:-7]
    if n.lower().endswith(".hgt"):
        return n[:-4]
    return path.stem


def local_rings_for_cached_srtm_tiles(
    geo_ref: Tuple[float, float], cache_dir: Optional[Path] = None
) -> List[List[Tuple[float, float]]]:
    """
    Замкнуті ламані (5 точок) у локальних метрах — межі 1°×1° тайлів,
    для яких у кеші є файл .hgt або .hgt.gz.
    """
    cache_dir = cache_dir or SRTM_DIR
    if not cache_dir.is_dir():
        return []
    ref_lon, ref_lat = geo_ref
    seen = set()
    rings: List[List[Tuple[float, float]]] = []
    paths = sorted(cache_dir.glob("*.hgt")) + sorted(cache_dir.glob("*.hgt.gz"))
    for path in paths:
        sw = parse_hgt_tile_sw_from_stem(hgt_path_tile_stem(path))
        if sw is None:
            continue
        lat0, lon0 = sw
        if (lat0, lon0) in seen:
            continue
        seen.add((lat0, lon0))
        lat1, lon1 = lat0 + 1, lon0 + 1
        c_sw = lat_lon_to_local_xy(lat0, lon0, ref_lon, ref_lat)
        c_se = lat_lon_to_local_xy(lat0, lon1, ref_lon, ref_lat)
        c_ne = lat_lon_to_local_xy(lat1, lon1, ref_lon, ref_lat)
        c_nw = lat_lon_to_local_xy(lat1, lon0, ref_lon, ref_lat)
        rings.append([c_sw, c_se, c_ne, c_nw, c_sw])
    return rings


def wgs84_bounds_from_local_ring(boundary_coords: Iterable[Tuple[float, float]], geo_ref: Tuple[float, float]) -> Tuple[float, float, float, float]:
    ref_lon, ref_lat = geo_ref
    lats: List[float] = []
    lons: List[float] = []
    for x, y in boundary_coords:
        lat, lon = local_xy_to_lat_lon(x, y, ref_lon, ref_lat)
        lats.append(lat)
        lons.append(lon)
    return min(lats), max(lats), min(lons), max(lons)


def wgs84_bounds_from_xy_bounds(minx: float, miny: float, maxx: float, maxy: float, geo_ref: Tuple[float, float]) -> Tuple[float, float, float, float]:
    corners = ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))
    lats: List[float] = []
    lons: List[float] = []
    for x, y in corners:
        lat, lon = local_xy_to_lat_lon(x, y, geo_ref[0], geo_ref[1])
        lats.append(lat)
        lons.append(lon)
    return min(lats), max(lats), min(lons), max(lons)


def download_tiles_for_xy_bounds(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    geo_ref: Tuple[float, float],
    *,
    tile_source: str = "skadi",
) -> List[Tuple[str, str]]:
    if not geo_ref:
        raise ValueError("Потрібна geo_ref.")
    lat0, lat1, lon0, lon1 = wgs84_bounds_from_xy_bounds(minx, miny, maxx, maxy, geo_ref)
    tiles = iter_tiles_covering_bbox(lat0, lat1, lon0, lon1)
    cache_dir = ensure_srtm_dir()
    results: List[Tuple[str, str]] = []
    for i, (la, lo) in enumerate(tiles):
        if i > 0:
            time.sleep(0.35)
        ok, msg = download_tile(la, lo, cache_dir, tile_source=tile_source)
        results.append((tile_base_name(la, lo), msg))
    return results


def iter_tiles_covering_bbox(lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> List[Tuple[int, int]]:
    t_lo, t_hi = min(lat_min, lat_max), max(lat_min, lat_max)
    u_lo, u_hi = min(lon_min, lon_max), max(lon_min, lon_max)
    tiles: List[Tuple[int, int]] = []
    la = int(math.floor(t_lo))
    while la <= int(math.floor(t_hi)):
        lo = int(math.floor(u_lo))
        while lo <= int(math.floor(u_hi)):
            tiles.append((la, lo))
            lo += 1
        la += 1
    return tiles


def tile_base_name(lat_sw: int, lon_sw: int) -> str:
    ns = "N" if lat_sw >= 0 else "S"
    ew = "E" if lon_sw >= 0 else "W"
    return f"{ns}{abs(lat_sw):02d}{ew}{abs(lon_sw):03d}.hgt"


def tile_s3_subdir(lat_sw: int) -> str:
    ns = "N" if lat_sw >= 0 else "S"
    return f"{ns}{abs(lat_sw):02d}"


def skadi_url(lat_sw: int, lon_sw: int) -> str:
    name = tile_base_name(lat_sw, lon_sw)
    sub = tile_s3_subdir(lat_sw)
    return f"{SKADI_BASE}/{sub}/{name}.gz"


def resolve_hgt_path(cache_dir: Path, lat_sw: int, lon_sw: int) -> Optional[Path]:
    name = tile_base_name(lat_sw, lon_sw)
    plain = cache_dir / name
    if plain.is_file():
        return plain
    gz = cache_dir / f"{name}.gz"
    if gz.is_file():
        return gz
    return None


def download_tile(
    lat_sw: int,
    lon_sw: int,
    cache_dir: Optional[Path] = None,
    *,
    tile_source: str = "skadi",
) -> Tuple[bool, str]:
    """
    Завантажує повний .hgt.gz (або .hgt) і зберігає розпакований .hgt у cache_dir.
    tile_source: skadi | earthdata | open_elevation
    Повертає (успіх, повідомлення).
    """
    cache_dir = cache_dir or ensure_srtm_dir()
    name = tile_base_name(lat_sw, lon_sw)
    out_path = cache_dir / name
    if out_path.is_file() and out_path.stat().st_size > 0:
        return True, f"вже є: {name}"

    src = str(tile_source or "skadi").strip().lower()
    if src == "open_elevation":
        return (
            False,
            f"{name}: Open-Elevation не надає файли тайлів .hgt; для тайлів оберіть «Skadi+локальні» "
            "або «NASA Earthdata» (earthaccess або власний EARTHDATA_SRTM_TILE_BASE).",
        )

    payload: Optional[bytes] = None
    tag = "Skadi"

    if src == "earthdata":
        tag = "Earthdata"
        url = _earthdata_tile_url(lat_sw, lon_sw)
        auth = _earthdata_basic_auth_header()
        if url and auth:
            headers = {
                "User-Agent": "DripCAD/1.0 (SRTM tiles; Earthdata)",
                "Authorization": auth,
            }
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return False, f"немає на сервері: {name} (404)"
                return False, f"{name}: HTTP {e.code} {e.reason}"
            except Exception as e:
                return False, f"{name}: {e}"
        else:
            ok_ea, msg_ea, raw_ea = _download_tile_payload_earthaccess(
                lat_sw, lon_sw, cache_dir, name
            )
            if not ok_ea or raw_ea is None:
                return False, f"{name}: {msg_ea}"
            payload = raw_ea
            tag = "Earthdata (earthaccess)"
    else:
        url = skadi_url(lat_sw, lon_sw)
        headers = {"User-Agent": "DripCAD/1.0 (SRTM tiles; contact: local)"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False, f"немає на сервері: {name} (404)"
            return False, f"{name}: HTTP {e.code} {e.reason}"
        except Exception as e:
            return False, f"{name}: {e}"

    if payload is None:
        return False, f"{name}: порожня відповідь"

    raw: bytes
    try:
        raw = gzip.decompress(payload)
    except Exception:
        raw = payload

    try:
        HgtTile(raw, lat_sw, lon_sw)
    except Exception as e:
        return False, f"{name}: некоректні дані тайла (.hgt): {e}"

    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    _hgt_cache.pop(str(out_path.resolve()), None)
    return True, f"завантажено ({tag}): {name}"


def download_tiles_for_boundary(
    boundary_coords: List[Tuple[float, float]],
    geo_ref: Tuple[float, float],
    *,
    tile_source: str = "skadi",
) -> List[Tuple[str, str]]:
    """Список (ім'я тайлу, статус) для усіх 1°×1°, що перетинають bbox контуру."""
    if not boundary_coords or not geo_ref:
        raise ValueError("Потрібен контур і geo_ref.")
    lat0, lat1, lon0, lon1 = wgs84_bounds_from_local_ring(boundary_coords, geo_ref)
    tiles = iter_tiles_covering_bbox(lat0, lat1, lon0, lon1)
    cache_dir = ensure_srtm_dir()
    results: List[Tuple[str, str]] = []
    for i, (la, lo) in enumerate(tiles):
        if i > 0:
            time.sleep(0.35)
        ok, msg = download_tile(la, lo, cache_dir, tile_source=tile_source)
        results.append((tile_base_name(la, lo), msg))
    return results


class HgtTile:
    __slots__ = ("raw", "n", "lat_sw", "lon_sw")

    def __init__(self, raw: bytes, lat_sw: int, lon_sw: int):
        self.raw = raw
        n2 = len(raw) // 2
        n = int(math.sqrt(n2))
        if n * n * 2 != len(raw):
            raise ValueError("некоректний розмір .hgt")
        self.n = n
        self.lat_sw = lat_sw
        self.lon_sw = lon_sw

    def _z(self, row: int, col: int) -> Optional[float]:
        row = max(0, min(row, self.n - 1))
        col = max(0, min(col, self.n - 1))
        i = (row * self.n + col) * 2
        v = struct.unpack_from(">h", self.raw, i)[0]
        if v == -32768 or v < -12000:
            return None
        return float(v)

    def elevation_at(self, lat: float, lon: float) -> Optional[float]:
        if not (self.lat_sw <= lat <= self.lat_sw + 1 and self.lon_sw <= lon <= self.lon_sw + 1):
            return None
        row_f = (self.lat_sw + 1.0 - lat) * (self.n - 1)
        col_f = (lon - self.lon_sw) * (self.n - 1)
        r0 = int(math.floor(row_f))
        c0 = int(math.floor(col_f))
        r1 = min(r0 + 1, self.n - 1)
        c1 = min(c0 + 1, self.n - 1)
        dr = row_f - r0
        dc = col_f - c0
        z00 = self._z(r0, c0)
        z01 = self._z(r0, c1)
        z10 = self._z(r1, c0)
        z11 = self._z(r1, c1)
        vals = [z for z in (z00, z01, z10, z11) if z is not None]
        if not vals:
            return None
        if z00 is None or z01 is None or z10 is None or z11 is None:
            return sum(vals) / len(vals)
        z0 = z00 * (1 - dc) + z01 * dc
        z1 = z10 * (1 - dc) + z11 * dc
        return z0 * (1 - dr) + z1 * dr


def _load_hgt_tile(cache_dir: Path, lat_sw: int, lon_sw: int) -> Optional[HgtTile]:
    path = resolve_hgt_path(cache_dir, lat_sw, lon_sw)
    if path is None:
        return None
    key = str(path.resolve())
    hit = _hgt_cache.get(key)
    if hit is not None:
        return hit
    if path.suffix.lower() == ".gz":
        raw = gzip.decompress(path.read_bytes())
    else:
        raw = path.read_bytes()
    tile = HgtTile(raw, lat_sw, lon_sw)
    _hgt_cache[key] = tile
    return tile


def elevation_from_local_srtm(lat: float, lon: float, cache_dir: Optional[Path] = None) -> Optional[float]:
    """Висота з локального _srtm_ або None, якщо тайла немає / void."""
    cache_dir = cache_dir or SRTM_DIR
    if not cache_dir.is_dir():
        return None
    lat_sw = int(math.floor(lat))
    lon_sw = int(math.floor(lon))
    tile = _load_hgt_tile(cache_dir, lat_sw, lon_sw)
    if tile is None:
        return None
    z = tile.elevation_at(lat, lon)
    if z is not None:
        return z
    return None


def clear_hgt_cache() -> None:
    _hgt_cache.clear()
