"""
Векторний шар OpenStreetMap для карти (Overpass → canvas tkintermapview).

Режими: «cad» (яскраві лінії на чорному) та «night» (нічна палітра на OSM_NIGHT_BG).
Дані — bbox через Overpass API з локальним JSON-кешем.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

# Версія запиту — змініть, щоб інвалідувати старий кеш після зміни QL.
OVERPASS_QUERY_VERSION = 1
OVERPASS_INTERPRETER = "https://overpass-api.de/api/interpreter"
USER_AGENT = "DripCAD/1.0 (irrigation CAD; contact: local)"

# Темний CAD: суцільно чорне тло + яскраві лінії / контури (без кольорового тла).
OSM_CAD_BG_FILL = "#000000"
OSM_CAD_LINE_DEFAULT = "#FFFFFF"
OSM_CAD_LINE_MOTORWAY = "#40C4FF"
OSM_CAD_LINE_TRUNK = "#80D8FF"
OSM_CAD_LINE_PRIMARY = "#B3E5FC"
OSM_CAD_BUILDING_FILL = "#FFFFFF"
OSM_CAD_BUILDING_OUTLINE = "#B0BEC5"

# Нічний векторний режим (чисте темне тло + OSM-геометрія)
OSM_NIGHT_BG = "#0b0f14"
OSM_NIGHT_LINE_MOTORWAY = "#4FC3F7"
OSM_NIGHT_LINE_TRUNK = "#80D8FF"
OSM_NIGHT_LINE_PRIMARY = "#B0BEC5"
OSM_NIGHT_LINE_SECONDARY = "#90A4AE"
OSM_NIGHT_LINE_DEFAULT = "#78909C"
OSM_NIGHT_BUILDING_FILL = "#ECEFF1"
OSM_NIGHT_BUILDING_OUTLINE = "#546E7A"
# Макс. примітивів на кадр (захист від фрізу canvas)
OSM_NIGHT_MAX_DRAWABLES = 11000


@dataclass(frozen=True)
class OsmCadDrawable:
    kind: str  # "line" | "poly"
    latlon: Tuple[Tuple[float, float], ...]
    highway: Optional[str] = None
    is_building: bool = False


def _bbox_area_deg(s: float, w: float, n: float, e: float) -> float:
    return max(0.0, float(n - s)) * max(0.0, float(e - w))


def build_overpass_ql(south: float, west: float, north: float, east: float, zoom: float) -> str:
    """Побудувати Overpass QL за видимою областю та рівнем масштабу."""
    z = int(round(float(zoom)))
    s, w, n, e = float(south), float(west), float(north), float(east)
    area = _bbox_area_deg(s, w, n, e)
    # Великі видимі області — лише магістральна мережа, щоб не перевантажувати Overpass.
    if area > 0.06 or z <= 11:
        roads = (
            f'way["highway"~"motorway|trunk|primary|secondary"]'
            f"({s},{w},{n},{e});"
        )
        buildings = ""
    elif area > 0.015 or z <= 13:
        roads = (
            f'way["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential"]'
            f"({s},{w},{n},{e});"
        )
        buildings = ""
    else:
        roads = f'way["highway"]({s},{w},{n},{e});'
        buildings = f'way["building"]({s},{w},{n},{e});' if z >= 15 and area <= 0.008 else ""

    parts = [roads]
    if buildings:
        parts.append(buildings)
    body = "\n  ".join(parts)
    return (
        f"[out:json][timeout:55];\n(\n  {body}\n);\nout geom;\n"
    )


def _cache_key(query: str) -> str:
    h = hashlib.sha256()
    h.update(query.encode("utf-8"))
    h.update(b"\n" + str(OVERPASS_QUERY_VERSION).encode("ascii"))
    return h.hexdigest()[:24]


def load_or_fetch_overpass(
    south: float,
    west: float,
    north: float,
    east: float,
    zoom: float,
    cache_dir: Path,
    timeout_s: float = 55.0,
) -> dict[str, Any]:
    """Повернути сирий JSON Overpass (dict). Кешує у cache_dir."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    q = build_overpass_ql(south, west, north, east, zoom)
    key = _cache_key(q)
    path = cache_dir / f"osm_{key}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = urllib.parse.urlencode({"data": q}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_INTERPRETER,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    try:
        path.write_text(raw, encoding="utf-8")
    except OSError:
        pass
    return payload


def overpass_json_to_drawables(
    payload: dict[str, Any], zoom: float, *, simplify_scale: float = 1.0
) -> List[OsmCadDrawable]:
    """Перетворити відповідь Overpass out geom на прості drawable."""
    z = int(round(float(zoom)))
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return []
    tol = max(1e-6, 0.000004 * max(1, 17 - z) * float(simplify_scale))
    out: List[OsmCadDrawable] = []
    try:
        from shapely.geometry import LineString, Polygon
    except Exception:
        LineString = None  # type: ignore[misc, assignment]
        Polygon = None  # type: ignore[misc, assignment]

    for el in elements:
        if not isinstance(el, dict) or el.get("type") != "way":
            continue
        geom = el.get("geometry")
        if not isinstance(geom, list) or len(geom) < 2:
            continue
        pts: List[Tuple[float, float]] = []
        for node in geom:
            if not isinstance(node, dict):
                continue
            try:
                pts.append((float(node["lat"]), float(node["lon"])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(pts) < 2:
            continue
        tags = el.get("tags") if isinstance(el.get("tags"), dict) else {}
        hw = tags.get("highway")
        is_b = "building" in tags
        if LineString is not None and tol > 0 and len(pts) >= 2:
            try:
                ll = LineString([(p[1], p[0]) for p in pts])  # x=lon y=lat
                ll2 = ll.simplify(tol, preserve_topology=True)
                pts = [(float(y), float(x)) for x, y in ll2.coords]
            except Exception:
                pass
        if is_b and len(pts) >= 3:
            closed = pts[0] == pts[-1] or (
                abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9
            )
            if closed and Polygon is not None:
                try:
                    ring = [(p[1], p[0]) for p in pts]
                    poly = Polygon(ring)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if not poly.is_empty:
                        ext = list(poly.exterior.coords)
                        pts = [(float(lat), float(lon)) for lon, lat in ext]
                except Exception:
                    pass
            out.append(OsmCadDrawable("poly", tuple(pts), highway=None, is_building=True))
        elif hw:
            out.append(OsmCadDrawable("line", tuple(pts), highway=str(hw), is_building=False))
    return out


class OsmCadMapOverlay:
    """
    Яскравий векторний шар OSM поверх тайлів карти.

    Раніше використовувався суцільний прямокутник cad_bg над тайлами — через це тайли
    рухались «під» ним при пані, і було видно лише маркер. Тепер тло — темні тайли + чорний
    canvas у проміжках; вектор малюється поверх тайлів (state=disabled, щоб не блокувати ЛКМ).
    """

    def __init__(
        self,
        map_widget: Any,
        tk_host: Any,
        decimal_to_osm: Any,
        cache_dir: Path,
    ) -> None:
        self.map_widget = map_widget
        self.tk_host = tk_host
        self.decimal_to_osm = decimal_to_osm
        self.cache_dir = cache_dir
        self.active = False
        self._after_job: Any = None
        self._fetch_thread: Optional[threading.Thread] = None
        self._prev_canvas_bg: Optional[str] = None
        self._prev_map_widget_bg: Optional[str] = None
        self._last_lift_paths_mono: float = 0.0
        self._render_style: str = "cad"

    def latlon_to_canvas_xy(self, lat: float, lon: float) -> Optional[Tuple[float, float]]:
        if self.decimal_to_osm is None:
            return None
        try:
            z = round(float(self.map_widget.zoom))
            tx, ty = self.decimal_to_osm(float(lat), float(lon), z)
            ul_x, ul_y = self.map_widget.upper_left_tile_pos
            lr_x, lr_y = self.map_widget.lower_right_tile_pos
            wtw = float(lr_x - ul_x)
            wth = float(lr_y - ul_y)
            if abs(wtw) < 1e-12 or abs(wth) < 1e-12:
                return None
            cw = float(self.map_widget.width)
            ch = float(self.map_widget.height)
            x = (tx - ul_x) / wtw * cw
            y = (ty - ul_y) / wth * ch
            return x, y
        except Exception:
            return None

    def activate(self, style: str = "cad") -> None:
        self.active = True
        self._render_style = "night" if style == "night" else "cad"
        self._last_lift_paths_mono = 0.0
        try:
            self.map_widget.canvas.delete("cad_bg")
        except Exception:
            pass
        bg = OSM_NIGHT_BG if self._render_style == "night" else OSM_CAD_BG_FILL
        try:
            self._prev_canvas_bg = self.map_widget.canvas.cget("bg")
            self.map_widget.canvas.config(bg=bg)
        except Exception:
            self._prev_canvas_bg = None
        try:
            self._prev_map_widget_bg = self.map_widget.cget("bg")
            self.map_widget.configure(bg=bg)
        except Exception:
            self._prev_map_widget_bg = None
        self._lift_map_paths_and_markers_above_osm()
        self.schedule_redraw(500 if self._render_style == "cad" else 1400)

    def deactivate(self) -> None:
        self.active = False
        self._cancel_scheduled()
        c = self.map_widget.canvas
        try:
            c.delete("cad_bg")
            c.delete("osm_cad")
        except Exception:
            pass
        try:
            if self._prev_canvas_bg is not None:
                c.config(bg=self._prev_canvas_bg)
        except Exception:
            pass
        self._prev_canvas_bg = None
        try:
            if self._prev_map_widget_bg is not None:
                self.map_widget.configure(bg=self._prev_map_widget_bg)
        except Exception:
            pass
        self._prev_map_widget_bg = None

    def _cancel_scheduled(self) -> None:
        if self._after_job is not None:
            try:
                self.tk_host.after_cancel(self._after_job)
            except Exception:
                pass
            self._after_job = None

    def schedule_redraw(self, delay_ms: int = 300) -> None:
        if not self.active:
            return
        self._cancel_scheduled()
        # Мінімальна пауза перед Overpass — злиття частих викликів (зум/пан).
        base = 1400 if self._render_style == "night" else 450
        d = max(base, int(delay_ms))

        def _run() -> None:
            self._after_job = None
            self._start_fetch_if_idle()

        try:
            self._after_job = self.tk_host.after(max(1, d), _run)
        except Exception:
            _run()

    def _start_fetch_if_idle(self) -> None:
        if not self.active:
            return
        if self._fetch_thread is not None and self._fetch_thread.is_alive():
            return
        try:
            c = self.map_widget.canvas
            cw = max(1, int(c.winfo_width()))
            ch = max(1, int(c.winfo_height()))
            lat_tl, lon_tl = self.map_widget.convert_canvas_coords_to_decimal_coords(0, 0)
            lat_br, lon_br = self.map_widget.convert_canvas_coords_to_decimal_coords(cw, ch)
            south = min(lat_tl, lat_br)
            north = max(lat_tl, lat_br)
            west = min(lon_tl, lon_br)
            east = max(lon_tl, lon_br)
            z = float(getattr(self.map_widget, "zoom", 12.0))
        except Exception:
            return

        def _worker() -> None:
            err: Optional[BaseException] = None
            drawables: List[OsmCadDrawable] = []
            try:
                payload = load_or_fetch_overpass(south, west, north, east, z, self.cache_dir)
                sim = 2.2 if self._render_style == "night" else 1.0
                drawables = overpass_json_to_drawables(payload, z, simplify_scale=sim)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as ex:
                err = ex
            except Exception as ex:  # pragma: no cover
                err = ex

            def _apply() -> None:
                self._fetch_thread = None
                if not self.active:
                    return
                if err is not None:
                    # Тихо лишаємо чорну підкладку; повтор при наступному pan/zoom.
                    return
                self._render_drawables(drawables)

            try:
                self.tk_host.after(0, _apply)
            except Exception:
                pass

        t = threading.Thread(target=_worker, name="osm_cad_overpass", daemon=True)
        self._fetch_thread = t
        t.start()

    def _render_drawables(self, drawables: Sequence[OsmCadDrawable]) -> None:
        c = self.map_widget.canvas
        try:
            c.delete("osm_cad")
        except Exception:
            return
        night = self._render_style == "night"
        if night and len(drawables) > OSM_NIGHT_MAX_DRAWABLES:
            drawables = list(drawables)[:OSM_NIGHT_MAX_DRAWABLES]
        hw_width = {
            "motorway": 4,
            "trunk": 4,
            "primary": 3,
            "secondary": 2,
            "tertiary": 2,
        }
        if night:
            hw_color = {
                "motorway": OSM_NIGHT_LINE_MOTORWAY,
                "trunk": OSM_NIGHT_LINE_TRUNK,
                "primary": OSM_NIGHT_LINE_PRIMARY,
                "secondary": OSM_NIGHT_LINE_SECONDARY,
                "tertiary": OSM_NIGHT_LINE_DEFAULT,
                "residential": OSM_NIGHT_LINE_DEFAULT,
                "service": OSM_NIGHT_LINE_DEFAULT,
                "unclassified": OSM_NIGHT_LINE_DEFAULT,
            }
            line_default = OSM_NIGHT_LINE_DEFAULT
            b_fill, b_outline = OSM_NIGHT_BUILDING_FILL, OSM_NIGHT_BUILDING_OUTLINE
        else:
            hw_color = {
                "motorway": OSM_CAD_LINE_MOTORWAY,
                "trunk": OSM_CAD_LINE_TRUNK,
                "primary": OSM_CAD_LINE_PRIMARY,
                "secondary": OSM_CAD_LINE_PRIMARY,
                "tertiary": OSM_CAD_LINE_DEFAULT,
                "residential": OSM_CAD_LINE_DEFAULT,
                "service": OSM_CAD_LINE_DEFAULT,
                "unclassified": OSM_CAD_LINE_DEFAULT,
            }
            line_default = OSM_CAD_LINE_DEFAULT
            b_fill, b_outline = OSM_CAD_BUILDING_FILL, OSM_CAD_BUILDING_OUTLINE

        lines = [d for d in drawables if d.kind == "line"]
        polys = [d for d in drawables if d.kind == "poly"]
        ordered = lines + polys

        for d in ordered:
            flat: List[float] = []
            for lat, lon in d.latlon:
                xy = self.latlon_to_canvas_xy(lat, lon)
                if xy is None:
                    flat = []
                    break
                flat.extend(xy)
            if len(flat) < 4:
                continue
            try:
                if d.kind == "poly" and len(flat) >= 6:
                    c.create_polygon(
                        *flat,
                        fill=b_fill,
                        outline=b_outline,
                        width=2,
                        tags=("osm_cad",),
                        state=tk.DISABLED,
                    )
                else:
                    w = hw_width.get(d.highway or "", 2)
                    col = hw_color.get(d.highway or "", line_default)
                    c.create_line(
                        *flat,
                        fill=col,
                        width=w,
                        capstyle="round",
                        joinstyle="round",
                        tags=("osm_cad",),
                        state=tk.DISABLED,
                    )
            except Exception:
                continue
        self._lift_osm_above_tiles()
        self._lift_map_paths_and_markers_above_osm()

    def _lift_osm_above_tiles(self) -> None:
        c = self.map_widget.canvas
        try:
            if c.find_withtag("tile") and c.find_withtag("osm_cad"):
                c.tag_raise("osm_cad", "tile")
        except Exception:
            pass

    def _lift_map_paths_and_markers_above_osm(self) -> None:
        """Лінії та маркери tkintermapview (tags path/marker) — поверх векторного OSM."""
        c = self.map_widget.canvas
        try:
            for iid in c.find_withtag("path"):
                c.lift(iid)
            for iid in c.find_withtag("marker"):
                c.lift(iid)
            for iid in c.find_withtag("marker_image"):
                c.lift(iid)
        except Exception:
            pass

    def after_manage_z_order(self) -> None:
        """Викликати після типового manage_z_order віджета: підняти чернетки/прев’ю над OSM."""
        if not self.active:
            return
        c = self.map_widget.canvas
        try:
            self._lift_osm_above_tiles()
            now = time.monotonic()
            if now - self._last_lift_paths_mono >= 0.05:
                self._last_lift_paths_mono = now
                self._lift_map_paths_and_markers_above_osm()
            for tag in ("map_draft_rubber", "map_live_preview", "trunk_map_glyph"):
                if c.find_withtag(tag):
                    c.lift(tag)
        except Exception:
            pass

    def cancel_pending_redraw(self) -> None:
        """Скасувати відкладене оновлення (наприклад при згортанні панелі карти)."""
        self._cancel_scheduled()
