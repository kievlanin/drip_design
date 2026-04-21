import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import linemerge

# Розмір клітини для просторового індексу точок рельєфу (менше сусідів у IDW → швидше сітка).
_BUCKET_CELL_M = 25.0
# Сусідні комірки відра: 5×5 (dx,dy ∈ [-2..2]) — ближче до повного IDW, ніж 3×3.
_BUCKET_NEIGHBOR_HALF = 2
_MIN_NEIGHBORS_FOR_LOCAL_IDW = 6

# Великі поля (напр. 1,3×6,5 км при кроці 5 м): без обмеження — мільйони операцій marching squares.
_CONTOUR_MAX_GRID_CELLS = 280_000
_CONTOUR_MAX_Z_LEVELS = 320

# Згладжування сітки Z перед marching squares: придушує «зубці» / різкі згини ізоліній від шуму
# у вихідних точках або локальної інтерполяції (IDW/кріггінг). 0 — вимкнути.
# Кожен «прохід» = сепарабельний [1,2,1]/4 по рядках і по стовпцях (аналог легкого Gaussian).
_CONTOUR_GRID_Z_SMOOTH_PASSES = 2


def _smooth_contour_grid_z_binomial(
    grid: dict,
    rows: int,
    cols: int,
    passes: int,
) -> None:
    """
    In-place згладжування Z у grid[(r,c)] = (gx, gy, z); gx, gy не змінюються.
    Потребує numpy (у проєкті вже використовується для кріггінгу сітки).
    """
    if passes <= 0 or rows < 1 or cols < 1:
        return
    import numpy as np

    Z = np.empty((rows, cols), dtype=np.float64)
    for r in range(rows):
        for c in range(cols):
            Z[r, c] = float(grid[(r, c)][2])
    out = Z
    for _ in range(int(passes)):
        p = np.pad(out, ((0, 0), (1, 1)), mode="edge")
        out = (p[:, :-2] + 2.0 * p[:, 1:-1] + p[:, 2:]) * 0.25
        p = np.pad(out, ((1, 1), (0, 0)), mode="edge")
        out = (p[:-2, :] + 2.0 * p[1:-1, :] + p[2:, :]) * 0.25
    for r in range(rows):
        for c in range(cols):
            gx, gy, _ = grid[(r, c)]
            grid[(r, c)] = (gx, gy, float(out[r, c]))


def _idw_z(x: float, y: float, points: Sequence[Tuple[float, float, float]], power: float) -> float:
    if not points:
        return 0.0
    for px, py, pz in points:
        if math.hypot(x - px, y - py) < 0.01:
            return float(pz)
    num = 0.0
    den = 0.0
    for px, py, pz in points:
        dist = math.hypot(x - px, y - py)
        w = 1.0 / (dist**power)
        num += w * pz
        den += w
    return (num / den) if den > 0 else 0.0


def _build_point_buckets(
    points: Sequence[Tuple[float, float, float]], cell_m: float
) -> dict:
    buckets: dict = {}
    if cell_m <= 0:
        cell_m = _BUCKET_CELL_M
    for x, y, z in points:
        bx = int(math.floor(x / cell_m))
        by = int(math.floor(y / cell_m))
        buckets.setdefault((bx, by), []).append((float(x), float(y), float(z)))
    return buckets


def _coarser_contour_step_multiple(
    user_step: float,
    z_lo: float,
    z_hi: float,
    max_levels: int,
) -> float:
    """
    Найменший крок виду mult * user_step (mult >= 1), щоб кількість ізоліній
    (індекси k від floor(z_lo/s) до ceil(z_hi/s)) не перевищувала max_levels.
    """
    us = max(float(user_step), 1e-9)
    if max_levels <= 1:
        return us
    mult = 1
    while mult < 50000:
        s = mult * us
        k_lo = int(math.floor(z_lo / s - 1e-12))
        k_hi = int(math.ceil(z_hi / s + 1e-12))
        if k_hi - k_lo + 1 <= max_levels:
            return float(s)
        mult += 1
    return mult * us


def _contour_k_range(z_lo: float, z_hi: float, step: float) -> Tuple[int, int]:
    if step <= 0:
        return 0, 0
    k_lo = int(math.floor(z_lo / step - 1e-12))
    k_hi = int(math.ceil(z_hi / step + 1e-12))
    return k_lo, k_hi


def _z_at_grid_node(
    gx: float,
    gy: float,
    buckets: dict,
    bucket_cell_m: float,
    all_points: Sequence[Tuple[float, float, float]],
    power: float,
) -> float:
    if not all_points:
        return 0.0
    bx = int(math.floor(gx / bucket_cell_m))
    by = int(math.floor(gy / bucket_cell_m))
    cand: List[Tuple[float, float, float]] = []
    h = _BUCKET_NEIGHBOR_HALF
    for dx in range(-h, h + 1):
        for dy in range(-h, h + 1):
            cand.extend(buckets.get((bx + dx, by + dy), []))
    if len(cand) < _MIN_NEIGHBORS_FOR_LOCAL_IDW:
        return _idw_z(gx, gy, all_points, power)
    return _idw_z(gx, gy, cand, power)


def _roughen_ok_variogram(ok, z_obs) -> None:
    """
    Після авто-підбору зменшує кореляційний range і трохи піднімає nugget,
    щоб поле Z було менш «рідинним» і горизонталі ближчі до IDW (без справжнього сплайну).
    """
    import numpy as np

    vm = getattr(ok, "variogram_model", "")
    vp = getattr(ok, "variogram_model_parameters", None)
    if vp is None:
        return
    vp = list(vp)
    if vm == "linear" and len(vp) >= 2:
        slope, nug = float(vp[0]), float(vp[1])
        dz = float(np.std(np.asarray(z_obs, dtype=np.float64))) if z_obs.size else 0.0
        ok.variogram_model_parameters = [
            slope * 1.2,
            nug + max(1e-8, 0.07 * dz),
        ]
        return
    if len(vp) < 3:
        return
    if vm not in ("spherical", "exponential", "gaussian"):
        return
    psill, rng, nug = float(vp[0]), float(vp[1]), float(vp[2])
    rng_n = max(rng * 0.55, 1e-5)
    nug_n = nug + max(0.1 * psill, 1e-8)
    ok.variogram_model_parameters = [psill, rng_n, nug_n]


def _ordinary_kriging_to_grid(
    pts_u,
    vals_u,
    xi,
    notes: List[str],
    progress_cb: Optional[Callable[[str, int, int], None]],
):
    """
    Сітка Z методом звичайного кріггінгу (PyKrige).
    Для великих DEM (>500 точок) — локальне вікно найближчих спостережень.
    """
    import warnings

    import numpy as np
    from pykrige.ok import OrdinaryKriging

    x = np.asarray(pts_u[:, 0], dtype=np.float64).ravel()
    y = np.asarray(pts_u[:, 1], dtype=np.float64).ravel()
    z = np.asarray(vals_u, dtype=np.float64).ravel()
    n = int(x.size)
    if n < 3:
        notes.append("Кріггінг: замало точок — IDW.")
        return None

    n_closest = None
    if n > 500:
        # Вужче вікно → менше глобального згладжування (криві не «як сплайн»).
        if n > 4500:
            n_closest = min(56, n - 1)
        elif n > 2000:
            n_closest = min(72, n - 1)
        else:
            n_closest = min(96, n - 1)
        notes.append(
            f"Кріггінг: локальне вікно {n_closest} найближчих точок ({n} точок ДЕМ); "
            "варіограмма послаблена (коротший range) для меншого згладжування."
        )
    else:
        notes.append(
            "Кріггінг: усі точки ДЕМ; після підбору варіограми — послаблення range/nugget "
            "для менш «сплайнових» горизонталей."
        )

    nlags = int(min(12, max(6, n // 80)))

    def _fit(model: str, use_weight: bool) -> OrdinaryKriging:
        return OrdinaryKriging(
            x,
            y,
            z,
            variogram_model=model,
            variogram_parameters=None,
            nlags=nlags,
            weight=use_weight,
            verbose=False,
            enable_plotting=False,
            enable_statistics=False,
            pseudo_inv=True,
            exact_values=True,
        )

    OK: Optional[OrdinaryKriging] = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            OK = _fit("spherical", True)
        except Exception:
            try:
                OK = _fit("exponential", True)
            except Exception as ex2:
                notes.append(
                    f"Кріггінг (spherical/exponential): {type(ex2).__name__}; спроба linear."
                )
                try:
                    OK = _fit("linear", False)
                except Exception as ex3:
                    notes.append(
                        f"Кріггінг: {type(ex3).__name__}; сітка Z — IDW."
                    )
                    return None

    assert OK is not None
    _roughen_ok_variogram(OK, z)

    xi = np.asarray(xi, dtype=np.float64)
    npt = int(xi.shape[0])
    chunk = 6000
    n_chunks = max(1, (npt + chunk - 1) // chunk)
    parts = []
    for ci, start in enumerate(range(0, npt, chunk)):
        end = min(start + chunk, npt)
        sx = xi[start:end, 0]
        sy = xi[start:end, 1]
        assert OK is not None
        try:
            zk, _ = OK.execute(
                "points",
                sx,
                sy,
                backend="C",
                n_closest_points=n_closest,
            )
        except Exception:
            zk, _ = OK.execute(
                "points",
                sx,
                sy,
                backend="loop",
                n_closest_points=n_closest,
            )
        parts.append(np.asarray(zk, dtype=np.float64).ravel())
        if progress_cb:
            progress_cb("grid", ci, n_chunks)

    return np.concatenate(parts)


class TopoEngine:
    def __init__(self):
        self.elevation_points = []
        self.srtm_boundary_pts_local = []
        self.power = 2.0
        self.last_contour_adaptation_note: Optional[str] = None
        self.last_srtm_provider_info: Dict[str, object] = {}

    def add_point(self, x, y, z):
        self.elevation_points.append((x, y, z))

    def clear(self):
        self.elevation_points = []
        
    def clear_srtm_boundary(self):
        self.srtm_boundary_pts_local = []

    def get_z(self, x, y):
        if not self.elevation_points:
            return 0.0
        return _idw_z(float(x), float(y), self.elevation_points, self.power)

    def generate_contours(
        self,
        boundary,
        step_z=1.0,
        grid_size=5.0,
        elevation_points: Optional[List] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        fixed_z_levels: Optional[List[float]] = None,
        interp_method: str = "idw",
    ):
        self.last_contour_adaptation_note = None
        pts_src = elevation_points if elevation_points is not None else self.elevation_points
        pts: List[Tuple[float, float, float]] = [
            (float(x), float(y), float(z)) for x, y, z in (pts_src or [])
        ]
        notes: List[str] = []
        res = _generate_contours_core(
            boundary,
            float(step_z),
            float(grid_size),
            pts,
            float(self.power),
            progress_cb,
            adaptation_notes=notes,
            fixed_z_levels=fixed_z_levels,
            interp_method=str(interp_method or "idw").strip().lower(),
        )
        if notes:
            self.last_contour_adaptation_note = "\n".join(notes)
        return res

    def _fetch_open_elevation_batch(
        self, batch: List[Tuple[float, float]]
    ) -> List[float]:
        url = "https://api.open-elevation.com/api/v1/lookup"
        payload = {
            "locations": [{"latitude": float(lat), "longitude": float(lon)} for lat, lon in batch]
        }
        raw = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=raw,
            headers={
                "User-Agent": "DripCAD/1.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        rows = data.get("results")
        if not isinstance(rows, list) or len(rows) != len(batch):
            raise ValueError("Open-Elevation повернув неповні дані.")
        out: List[float] = []
        for row in rows:
            if not isinstance(row, dict) or "elevation" not in row:
                raise ValueError("Open-Elevation: некоректний формат відповіді.")
            out.append(float(row["elevation"]))
        return out

    def _fetch_earthdata_batch(
        self, batch: List[Tuple[float, float]]
    ) -> List[float]:
        from main_app.paths import load_earthdata_credentials_from_project_file

        load_earthdata_credentials_from_project_file()
        # Earthdata endpoint винесено в env, бо в проєктах часто різні проксі/шлюзи.
        base = (os.getenv("EARTHDATA_ELEVATION_API_URL", "") or "").strip()
        if not base:
            raise ValueError(
                "NASA Earthdata не налаштовано (немає EARTHDATA_ELEVATION_API_URL)."
            )
        user = (os.getenv("EARTHDATA_USER", "") or "").strip()
        password = (os.getenv("EARTHDATA_PASSWORD", "") or "").strip()
        if not user or not password:
            raise ValueError(
                "NASA Earthdata не налаштовано (немає EARTHDATA_USER/EARTHDATA_PASSWORD)."
            )
        lats = ",".join(f"{lat:.6f}" for lat, _ in batch)
        lons = ",".join(f"{lon:.6f}" for _, lon in batch)
        url = f"{base}?latitude={lats}&longitude={lons}"
        auth = (f"{user}:{password}").encode("utf-8")
        import base64

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "DripCAD/1.0",
                "Accept": "application/json",
                "Authorization": f"Basic {base64.b64encode(auth).decode('ascii')}",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        rows = data.get("elevation")
        if not isinstance(rows, list) or len(rows) != len(batch):
            raise ValueError("NASA Earthdata повернув неповні дані.")
        return [float(v) for v in rows]

    def _provider_chain_for_mode(self, mode: str) -> List[str]:
        mm = str(mode or "auto").strip().lower()
        mapping = {
            "auto": ["open_elevation", "earthdata"],
            "skadi_local": ["open_elevation", "earthdata"],
            "open_elevation": ["open_elevation", "earthdata"],
            "earthdata": ["earthdata", "open_elevation"],
        }
        return list(mapping.get(mm, mapping["auto"]))

    def fetch_srtm_grid(self, boundary_coords, geo_ref, resolution=30.0, source_mode="auto"):
        from shapely.geometry import Point

        from main_app.paths import SRTM_DIR
        from modules.geo_module import srtm_tiles

        # Межу завжди беремо з аргументу (зона проєкту з карти / KML / поле з UI).
        # Раніше KML перекривав передану зону — горизонталі та DEM різались не тою рамкою.

        if not geo_ref:
            raise ValueError("Відсутні координати контуру або гео-прив'язка.")

        from shapely.geometry import MultiPolygon, Polygon as ShpPolygon

        if isinstance(boundary_coords, (ShpPolygon, MultiPolygon)):
            poly = boundary_coords
            if not poly.is_valid:
                poly = poly.buffer(0)
        elif isinstance(boundary_coords, (list, tuple)) and len(boundary_coords) >= 3:
            poly = ShpPolygon(boundary_coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
        else:
            raise ValueError("Відсутні координати контуру або гео-прив'язка.")

        minx, miny, maxx, maxy = poly.bounds
        ref_lon, ref_lat = geo_ref
        R = 6378137.0

        grid_points = []
        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                if poly.covers(Point(x, y)):
                    grid_points.append((x, y))
                y += resolution
            x += resolution

        if not grid_points:
            raise ValueError("Не знайдено жодної точки у контурі поля з обраним кроком сітки.")

        srtm_tiles.ensure_srtm_dir()

        geo_points = []
        for x, y in grid_points:
            lat = ref_lat + math.degrees(-y / R)
            lon = ref_lon + math.degrees(x / (R * math.cos(math.radians(ref_lat))))
            geo_points.append((lat, lon))

        elevations: List[Optional[float]] = []
        for lat, lon in geo_points:
            z = srtm_tiles.elevation_from_local_srtm(lat, lon, SRTM_DIR)
            elevations.append(z)

        missing_idx = [i for i, z in enumerate(elevations) if z is None]
        active_provider = "skadi_local"
        fallback_chain_used: List[str] = []
        provider_errors: Dict[str, str] = {}
        if missing_idx:
            if len(missing_idx) > 1500:
                raise ValueError(
                    f"Без локальних тайлів у _srtm_ потрібно >1500 запитів до API ({len(missing_idx)}).\n"
                    "Завантажте тайли (кнопка на вкладці «Рельєф») або збільште крок сітки (напр. 90 м)."
                )
            provider_chain = self._provider_chain_for_mode(source_mode)
            provider_fetch = {
                "open_elevation": self._fetch_open_elevation_batch,
                "earthdata": self._fetch_earthdata_batch,
            }
            batch_size = 100
            for provider in provider_chain:
                still_missing = [i for i, z in enumerate(elevations) if z is None]
                if not still_missing:
                    break
                fetcher = provider_fetch.get(provider)
                if fetcher is None:
                    continue
                try:
                    for b_start in range(0, len(still_missing), batch_size):
                        if b_start > 0:
                            time.sleep(1.2)
                        chunk_ix = still_missing[b_start : b_start + batch_size]
                        batch = [geo_points[i] for i in chunk_ix]
                        api_z = fetcher(batch)
                        for j, idx in enumerate(chunk_ix):
                            elevations[idx] = float(api_z[j])
                    fallback_chain_used.append(provider)
                    active_provider = provider
                except urllib.error.HTTPError as e:
                    err_msg = e.read().decode("utf-8", errors="ignore")
                    provider_errors[provider] = f"HTTP {e.code}: {err_msg or e.reason}"
                    continue
                except Exception as ex:
                    provider_errors[provider] = str(ex)
                    continue

        self.clear()
        for i, (x, y) in enumerate(grid_points):
            z = elevations[i]
            if z is None:
                provider_bits = ", ".join(
                    f"{k}: {v}" for k, v in provider_errors.items()
                ) or "немає доступних онлайн-джерел"
                raise ValueError(
                    "Не вдалося отримати висоту для частини точок "
                    "(void SRTM і недоступні онлайн-резерви).\n"
                    f"Деталі: {provider_bits}"
                )
            self.add_point(x, y, float(z))
        self.last_srtm_provider_info = {
            "source_mode": str(source_mode or "auto"),
            "active_provider": active_provider,
            "fallback_chain_used": fallback_chain_used,
            "provider_errors": provider_errors,
            "missing_points_resolved": len(missing_idx),
        }
        return {
            "count": len(grid_points),
            "active_provider": active_provider,
            "fallback_chain_used": fallback_chain_used,
            "provider_errors": provider_errors,
            "missing_points_resolved": len(missing_idx),
            "elevation_points": list(self.elevation_points),
        }


def _generate_contours_core(
    boundary,
    step_z: float,
    grid_size: float,
    elevation_points: List[Tuple[float, float, float]],
    power: float,
    progress_cb: Optional[Callable[[str, int, int], None]],
    adaptation_notes: Optional[List[str]] = None,
    fixed_z_levels: Optional[List[float]] = None,
    interp_method: str = "idw",
):
    from shapely.geometry import MultiPolygon, Polygon as ShpPolygon

    interp_method = str(interp_method or "idw").strip().lower()

    if not elevation_points or boundary is None:
        return []

    if isinstance(boundary, (ShpPolygon, MultiPolygon)):
        poly = boundary
        if not poly.is_valid:
            poly = poly.buffer(0)
    elif isinstance(boundary, (list, tuple)) and len(boundary) >= 3:
        poly = ShpPolygon(boundary)
        if not poly.is_valid:
            poly = poly.buffer(0)
    else:
        return []

    notes = adaptation_notes if adaptation_notes is not None else []

    if interp_method in ("natural_neighbor", "nn", "sibson"):
        notes.append(
            "Метод natural neighbor прибрано; застосовано кріггінг (PyKrige)."
        )
        interp_method = "kriging"

    minx, miny, maxx, maxy = poly.bounds

    user_grid = max(float(grid_size), 0.5)
    gs = user_grid
    minx -= gs
    miny -= gs
    maxx += gs
    maxy += gs
    width = maxx - minx
    height = maxy - miny

    cols = int(math.ceil(width / gs)) + 1
    rows = int(math.ceil(height / gs)) + 1
    cell_count = cols * rows

    if cell_count > _CONTOUR_MAX_GRID_CELLS:
        scale = math.sqrt(cell_count / float(_CONTOUR_MAX_GRID_CELLS))
        gs = user_grid * scale
        cols = int(math.ceil(width / gs)) + 1
        rows = int(math.ceil(height / gs)) + 1
        cell_count = cols * rows
        notes.append(
            f"Крок сітки: {user_grid:g} → {gs:.1f} м ({cols}×{rows} ≈ {cell_count:,} клітин; ліміт {_CONTOUR_MAX_GRID_CELLS:,})."
        )

    grid_size_eff = gs

    bucket_cell = max(_BUCKET_CELL_M, grid_size_eff * 4.0)
    buckets = _build_point_buckets(elevation_points, bucket_cell)

    grid = {}
    min_z = float("inf")
    max_z = float("-inf")

    xi_rows: List[Tuple[float, float]] = []
    rc_order: List[Tuple[int, int]] = []
    for r in range(rows):
        if progress_cb and r % 8 == 0:
            progress_cb("grid", r, rows)
        for c in range(cols):
            gx = minx + c * grid_size_eff
            gy = miny + r * grid_size_eff
            xi_rows.append((gx, gy))
            rc_order.append((r, c))
    if progress_cb and rows > 0:
        progress_cb("grid", rows - 1, rows)

    kriging_used = False
    if interp_method in ("kriging", "ordinary_kriging", "ok"):
        try:
            import numpy as np
        except ImportError as _e:
            raise ImportError(
                "Кріггінг потребує numpy.\n"
                "У середовищі Python виконайте:\n  pip install numpy scipy pykrige"
            ) from _e
        try:
            import pykrige  # noqa: F401
        except ImportError as _e:
            raise ImportError(
                "Кріггінг ізоліній потребує PyKrige (і scipy).\n"
                "У середовищі Python виконайте:\n  pip install pykrige scipy"
            ) from _e
        pts_xy = np.array(
            [[p[0], p[1]] for p in elevation_points], dtype=np.float64
        )
        vals_z = np.array([p[2] for p in elevation_points], dtype=np.float64)
        if len(pts_xy) >= 3:
            xy_r = np.round(pts_xy, decimals=4)
            _, uniq_ix = np.unique(xy_r, axis=0, return_index=True)
            uniq_ix = np.sort(uniq_ix)
            pts_u = pts_xy[uniq_ix]
            vals_u = vals_z[uniq_ix]
            if len(pts_u) >= 3:
                xi = np.array(xi_rows, dtype=np.float64)
                zi = _ordinary_kriging_to_grid(
                    pts_u, vals_u, xi, notes, progress_cb
                )
                if zi is not None:
                    kriging_used = True
                    for k, (r, c) in enumerate(rc_order):
                        gx, gy = float(xi[k, 0]), float(xi[k, 1])
                        z = float(zi[k])
                        if not math.isfinite(z):
                            z = _z_at_grid_node(
                                gx,
                                gy,
                                buckets,
                                bucket_cell,
                                elevation_points,
                                power,
                            )
                        grid[(r, c)] = (gx, gy, z)
                        if z < min_z:
                            min_z = z
                        if z > max_z:
                            max_z = z
            else:
                notes.append("Кріггінг: замало унікальних точок XY — IDW.")
        else:
            return []

    if not kriging_used:
        for k, (r, c) in enumerate(rc_order):
            gx, gy = xi_rows[k]
            z = _z_at_grid_node(
                gx, gy, buckets, bucket_cell, elevation_points, power
            )
            grid[(r, c)] = (gx, gy, z)
            if z < min_z:
                min_z = z
            if z > max_z:
                max_z = z

    if _CONTOUR_GRID_Z_SMOOTH_PASSES > 0:
        try:
            _smooth_contour_grid_z_binomial(
                grid, rows, cols, _CONTOUR_GRID_Z_SMOOTH_PASSES
            )
            notes.append(
                "Ізолінії: згладжено сітку висот (фільтр придушення артефактів / різких згинів)."
            )
            min_z = float("inf")
            max_z = float("-inf")
            for r in range(rows):
                for c in range(cols):
                    z = float(grid[(r, c)][2])
                    if z < min_z:
                        min_z = z
                    if z > max_z:
                        max_z = z
        except ImportError:
            notes.append(
                "Згладжування сітки Z для ізоліній пропущено (немає numpy)."
            )

    if min_z == float("inf") or max_z == float("-inf"):
        return []

    if fixed_z_levels is not None:
        levels_set = []
        for z in fixed_z_levels:
            zf = round(float(z), 6)
            if zf < min_z - 1e-5 or zf > max_z + 1e-5:
                continue
            levels_set.append(zf)
        levels = sorted(set(levels_set))
        if not levels:
            return []
        n_levels = len(levels)
    else:
        user_step_z = max(float(step_z), 1e-6)
        user_step_z = float(round(user_step_z, 9))

        sz = user_step_z
        k_lo, k_hi = _contour_k_range(min_z, max_z, sz)
        n_levels = max(1, k_hi - k_lo + 1)

        if n_levels > _CONTOUR_MAX_Z_LEVELS:
            sz = _coarser_contour_step_multiple(
                user_step_z, min_z, max_z, _CONTOUR_MAX_Z_LEVELS
            )
            if sz > user_step_z + 1e-9:
                notes.append(
                    f"Крок висоти ізоліній: {user_step_z:g} → {sz:g} м "
                    f"(кратно {user_step_z:g} м; макс. {_CONTOUR_MAX_Z_LEVELS} рівнів)."
                )
            k_lo, k_hi = _contour_k_range(min_z, max_z, sz)
            n_levels = max(1, k_hi - k_lo + 1)

        step_z = sz
        levels = [round(k * step_z, 6) for k in range(k_lo, k_hi + 1)]

    contours = []
    for li, z_level in enumerate(levels):
        if progress_cb:
            progress_cb("levels", li, n_levels)
        lines_for_level = []
        for r in range(rows - 1):
            for c in range(cols - 1):
                pA = grid[(r, c)]
                pB = grid[(r, c + 1)]
                pC = grid[(r + 1, c + 1)]
                pD = grid[(r + 1, c)]

                vA = 1 if pA[2] >= z_level else 0
                vB = 1 if pB[2] >= z_level else 0
                vC = 1 if pC[2] >= z_level else 0
                vD = 1 if pD[2] >= z_level else 0

                state = (vA << 3) | (vB << 2) | (vC << 1) | vD
                if state == 0 or state == 15:
                    continue

                def interp(pf, pt):
                    z0, z1 = pf[2], pt[2]
                    if z1 == z0:
                        return (pf[0], pf[1])
                    t = (z_level - z0) / (z1 - z0)
                    return (
                        pf[0] + t * (pt[0] - pf[0]),
                        pf[1] + t * (pt[1] - pf[1]),
                    )

                edges_cross = {
                    1: [(2, 3)],
                    2: [(1, 2)],
                    3: [(1, 3)],
                    4: [(0, 1)],
                    5: [(0, 3), (1, 2)],
                    6: [(0, 2)],
                    7: [(0, 3)],
                    8: [(0, 3)],
                    9: [(0, 2)],
                    10: [(0, 1), (2, 3)],
                    11: [(0, 1)],
                    12: [(1, 3)],
                    13: [(1, 2)],
                    14: [(2, 3)],
                }

                if state in edges_cross:
                    geom_pts = {
                        0: interp(pA, pB),
                        1: interp(pB, pC),
                        2: interp(pC, pD),
                        3: interp(pD, pA),
                    }
                    for e1, e2 in edges_cross[state]:
                        pt1 = geom_pts[e1]
                        pt2 = geom_pts[e2]
                        if pt1 != pt2:
                            lines_for_level.append(LineString([pt1, pt2]))

        if lines_for_level:
            multi_line = MultiLineString(lines_for_level)
            merged = linemerge(multi_line)
            try:
                clipped = merged.intersection(poly)
                if not clipped.is_empty:
                    contours.append({"z": z_level, "geom": clipped})
            except Exception:
                pass

    return contours