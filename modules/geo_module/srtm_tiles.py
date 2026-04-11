"""
Повні тайли SRTM у форматі .hgt (CGIAR / AWS Skadi).
Завантаження цілих файлів; висота з локального кешу, якщо тайл є.
"""
from __future__ import annotations

import gzip
import math
import re
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from main_app.paths import SRTM_DIR

# Публічне дзеркало void-filled SRTM (gzip .hgt.gz)
SKADI_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"

_hgt_cache: dict[str, "HgtTile"] = {}


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


def download_tiles_for_xy_bounds(minx: float, miny: float, maxx: float, maxy: float, geo_ref: Tuple[float, float]) -> List[Tuple[str, str]]:
    if not geo_ref:
        raise ValueError("Потрібна geo_ref.")
    lat0, lat1, lon0, lon1 = wgs84_bounds_from_xy_bounds(minx, miny, maxx, maxy, geo_ref)
    tiles = iter_tiles_covering_bbox(lat0, lat1, lon0, lon1)
    cache_dir = ensure_srtm_dir()
    results: List[Tuple[str, str]] = []
    for i, (la, lo) in enumerate(tiles):
        if i > 0:
            time.sleep(0.35)
        ok, msg = download_tile(la, lo, cache_dir)
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


def download_tile(lat_sw: int, lon_sw: int, cache_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Завантажує повний .hgt.gz і зберігає розпакований .hgt у cache_dir.
    Повертає (успіх, повідомлення).
    """
    cache_dir = cache_dir or ensure_srtm_dir()
    name = tile_base_name(lat_sw, lon_sw)
    out_path = cache_dir / name
    if out_path.is_file() and out_path.stat().st_size > 0:
        return True, f"вже є: {name}"

    url = skadi_url(lat_sw, lon_sw)
    req = urllib.request.Request(url, headers={"User-Agent": "DripCAD/1.0 (SRTM tiles; contact: local)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw_gz = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, f"немає на сервері: {name} (404)"
        return False, f"{name}: HTTP {e.code} {e.reason}"
    except Exception as e:
        return False, f"{name}: {e}"

    try:
        raw = gzip.decompress(raw_gz)
    except Exception as e:
        return False, f"{name}: помилка gzip: {e}"

    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    _hgt_cache.pop(str(out_path.resolve()), None)
    return True, f"завантажено: {name}"


def download_tiles_for_boundary(boundary_coords: List[Tuple[float, float]], geo_ref: Tuple[float, float]) -> List[Tuple[str, str]]:
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
        ok, msg = download_tile(la, lo, cache_dir)
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
