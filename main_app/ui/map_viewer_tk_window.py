from __future__ import annotations

import sys
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, ttk
import math
import threading

# Supports launching as standalone script from menu subprocess.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    from tkintermapview import TkinterMapView
    from tkintermapview.utility_functions import decimal_to_osm
except Exception as ex:  # pragma: no cover - runtime dependency check
    TkinterMapView = None
    decimal_to_osm = None  # type: ignore[misc, assignment]
    _IMPORT_ERR = ex
else:
    _IMPORT_ERR = None
from modules.geo_module import srtm_tiles
from modules.hydraulic_module.trunk_map_graph import ensure_trunk_node_ids
from main_app.paths import SRTM_DIR
from main_app.ui.map_left_draw_widgets import build_draw_modes_tab, build_trunk_tools_tab
from main_app.ui.silent_messagebox import (
    silent_askyesno,
    silent_showerror,
    silent_showinfo,
    silent_showwarning,
)
from main_app.ui.tooltips import attach_tooltip_dark as _attach_dark_tooltip

LIGHT_TILE_URL = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
SAT_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
TERRAIN_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
)
# Мозаїка Sentinel-2 без хмар (Copernicus / ESA) — тайли EOX WMTS; умови та атрибуція: https://s2maps.eu/
COPERNICUS_S2_CLOUDLESS_TILE_URL = (
    "https://c.tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg"
)
# Раніше було 13 — тайли EOX для цього шару віддаються принаймні до z=18 (z=19 — 404).
COPERNICUS_S2_CLOUDLESS_MAX_ZOOM = 18
# (підпис у UI, url, max_zoom)
MAP_BASEMAP_PRESETS = (
    ("Esri: супутник", SAT_TILE_URL, 19),
    ("Copernicus: Sentinel-2 cloudless", COPERNICUS_S2_CLOUDLESS_TILE_URL, COPERNICUS_S2_CLOUDLESS_MAX_ZOOM),
    ("Esri: рельєф", TERRAIN_TILE_URL, 19),
    ("OSM світла", LIGHT_TILE_URL, 19),
)
MAP_BG_DARK = "#0b0f14"
# Рамка «Зум рамкою» (виділення на canvas тайлів)
ZOOM_BOX_OUTLINE = "#FF1744"
ZOOM_BOX_WIDTH = 4
ZOOM_BOX_DASH = (5, 4)

# Спецінструменти карти: полілінії (ЛКМ — вершини, ПКМ — завершити) та одна точка (ЛКМ — позиція, ПКМ — зафіксувати).
_MAP_TOOLS_POLYLINE = frozenset({"capture_tiles", "block_contour", "trunk_route", "scene_lines"})
_MAP_TOOLS_TRUNK_POINT = frozenset(
    {"trunk_pump", "trunk_picket", "trunk_junction", "trunk_consumer"}
)
_MAP_TOOLS_PASSIVE = frozenset({"map_pick_info", "select"})
# Магістраль по вузлах: прив’язка ЛКМ у межах цього радіусу (м, локальні XY).
TRUNK_NODE_SNAP_M = 18.0
# Відображення магістралі та підказок «Інфо»
TRUNK_PATH_COLOR = "#8E24AA"
TRUNK_PICK_NODE_R_M = 26.0
TRUNK_PICK_LINE_R_M = 16.0
SUBMAIN_PICK_R_M = 14.0
VALVE_NODE_PICK_R_M = 22.0


def create_embedded_map_panel(parent: tk.Misc, app=None):
    """
    Build map UI inside an existing parent frame.
    Returns created outer frame.
    """
    if TkinterMapView is None:
        raise RuntimeError(
            "Не вдалося імпортувати tkintermapview. "
            "Встановіть: py -m pip install tkintermapview"
        )

    host = tk.Frame(parent, bg="#1e1e1e")

    def _map_silent_parent():
        if app is not None and getattr(app, "root", None) is not None:
            return app.root
        return host

    host.pack(fill="both", expand=True)
    host._map_bg_suspended = False
    host._scale_bar_after_id = None
    host._init_geo_after_id = None

    top_bar = tk.Frame(host, bg="#1e1e1e", height=34)
    top_bar.pack(side=tk.TOP, fill=tk.X)

    map_area = tk.Frame(host, bg="#1e1e1e")
    map_area.pack(fill="both", expand=True)
    left_toolbar = tk.Frame(map_area, bg="#181818", width=200)
    left_toolbar.pack(side=tk.LEFT, fill=tk.Y)
    left_toolbar.pack_propagate(False)

    map_widget = TkinterMapView(map_area, corner_radius=0)
    map_widget.pack(side=tk.LEFT, fill="both", expand=True)
    try:
        map_widget.configure(bg=MAP_BG_DARK)
        map_widget.canvas.configure(bg=MAP_BG_DARK, highlightthickness=0)
    except Exception:
        pass
    map_widget.set_tile_server(SAT_TILE_URL, max_zoom=19)
    map_widget.set_position(50.4501, 30.5234)
    map_widget.set_zoom(12)
    map_widget.set_marker(50.4501, 30.5234, text="Київ")

    def _latlon_to_canvas_xy(lat: float, lon: float):
        """Проєкція WGS84 на canvas карти (як CanvasPath.get_canvas_pos)."""
        if decimal_to_osm is None:
            return None
        try:
            z = round(float(map_widget.zoom))
            tx, ty = decimal_to_osm(float(lat), float(lon), z)
            ul_x, ul_y = map_widget.upper_left_tile_pos
            lr_x, lr_y = map_widget.lower_right_tile_pos
            wtw = float(lr_x - ul_x)
            wth = float(lr_y - ul_y)
            if abs(wtw) < 1e-12 or abs(wth) < 1e-12:
                return None
            cw = float(map_widget.width)
            ch = float(map_widget.height)
            x = (tx - ul_x) / wtw * cw
            y = (ty - ul_y) / wth * ch
            return x, y
        except Exception:
            return None

    # Лінійка не на canvas тайлів (тайли постійно перекривають) — окремий шар поверх map_area.
    scale_overlay = tk.Frame(map_area, bg=MAP_BG_DARK)
    scale_canvas = tk.Canvas(
        scale_overlay,
        bg="#101418",
        highlightthickness=0,
        bd=0,
        height=44,
        width=130,
    )
    scale_canvas.pack()
    scale_overlay.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-8)

    _orig_manage_z_order = map_widget.manage_z_order

    def _manage_z_order_overlay_paths():
        _orig_manage_z_order()
        c = map_widget.canvas
        try:
            if c.find_withtag("map_draft_rubber"):
                c.lift("map_draft_rubber")
        except Exception:
            pass
        try:
            if c.find_withtag("map_live_preview"):
                c.lift("map_live_preview")
        except Exception:
            pass
        try:
            if c.find_withtag("trunk_map_glyph"):
                c.lift("trunk_map_glyph")
        except Exception:
            pass

    map_widget.manage_z_order = _manage_z_order_overlay_paths

    def _draw_scale_bar_100m():
        if getattr(host, "_map_bg_suspended", False):
            return
        try:
            mc = map_widget.canvas
            w = int(mc.winfo_width())
            h = int(mc.winfo_height())
            if w < 40 or h < 40:
                return
            ax, ay = w - 20, h - 20
            lat0, lon0 = map_widget.convert_canvas_coords_to_decimal_coords(ax, ay)
            cos_lat = max(0.15, abs(math.cos(math.radians(lat0))))
            dlon = 100.0 / (111320.0 * cos_lat)
            lat_e, lon_e = lat0, lon0 + dlon
            p0 = _latlon_to_canvas_xy(lat0, lon0)
            p1 = _latlon_to_canvas_xy(lat_e, lon_e)
            if not p0 or not p1:
                return
            px_len = int(max(28, min(w // 3, abs(p1[0] - p0[0]))))
            pad_l, pad_r = 10, 10
            cw = px_len + pad_l + pad_r
            sc = scale_canvas
            sc.delete("all")
            sc.config(width=max(72, cw), height=44)
            y_line = 30
            x0, x1 = pad_l, pad_l + px_len
            sc.create_rectangle(2, 12, max(72, cw) - 2, 42, fill="#101418", outline="#F5F5F5", width=1)
            sc.create_line(x0, y_line, x1, y_line, fill="#FFD54A", width=4)
            sc.create_line(x0, y_line - 6, x0, y_line + 6, fill="#FFFFFF", width=2)
            sc.create_line(x1, y_line - 6, x1, y_line + 6, fill="#FFFFFF", width=2)
            sc.create_text(
                (x0 + x1) // 2,
                y_line - 10,
                text="100 м",
                fill="#FFFFFF",
                font=("Segoe UI", 9, "bold"),
            )
        except Exception:
            pass
        finally:
            if not getattr(host, "_map_bg_suspended", False):
                try:
                    host._scale_bar_after_id = host.after(600, _draw_scale_bar_100m)
                except tk.TclError:
                    host._scale_bar_after_id = None

    view_state = {
        "active_tool": None,
        "draft_points": [],
        "draft_path": None,
        "kml_paths": [],
        "kml_points": [],
        "last_bounds": None,
        "capture_points": [],
        "capture_path": None,
        "block_path": None,
        "trunk_path": None,
        "project_paths": [],
        "cached_tile_paths": [],
        "cached_tile_refresh_job": None,
        "pz_drag": None,
        "project_zone_map_path": None,
        "trunk_map_markers": [],
        "map_pick_regions": [],
        "trunk_segment_paths": [],
        "trunk_draft_indices": [],
    }

    hint = tk.StringVar(value="Інструмент: навігація")
    dl_status = tk.StringVar(value="SRTM: очікування")
    show_blocks_var = tk.BooleanVar(value=True)
    show_submains_var = tk.BooleanVar(value=True)
    show_laterals_var = tk.BooleanVar(value=True)
    show_cached_tiles_var = tk.BooleanVar(value=False)

    def _segment_dist_sq(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
        dx, dy = x1 - x0, y1 - y0
        ln = dx * dx + dy * dy
        if ln < 1e-18:
            return (px - x0) ** 2 + (py - y0) ** 2
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / ln))
        qx, qy = x0 + t * dx, y0 + t * dy
        return (px - qx) ** 2 + (py - qy) ** 2

    def _polyline_min_dist_m(px: float, py: float, pts: list) -> float:
        if len(pts) < 2:
            return 1e9
        best = 1e18
        for i in range(len(pts) - 1):
            x0, y0 = float(pts[i][0]), float(pts[i][1])
            x1, y1 = float(pts[i + 1][0]), float(pts[i + 1][1])
            best = min(best, _segment_dist_sq(px, py, x0, y0, x1, y1))
        return math.sqrt(best)

    def _point_in_ring(wx: float, wy: float, ring: list) -> bool:
        if len(ring) < 3:
            return False
        inside = False
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = float(ring[i][0]), float(ring[i][1])
            xj, yj = float(ring[j][0]), float(ring[j][1])
            if (yi > wy) != (yj > wy):
                x_int = (xj - xi) * (wy - yi) / (yj - yi + 1e-30) + xi
                if wx < x_int:
                    inside = not inside
            j = i
        return inside

    def _register_pick_disc(
        wx: float,
        wy: float,
        r_m: float,
        label: str,
        priority: int,
        *,
        trunk_geom: bool = False,
        trunk_node_pick: bool = False,
    ) -> None:
        row: dict = {"kind": "disc", "x": wx, "y": wy, "r": r_m, "label": label, "p": priority}
        if trunk_geom:
            row["trunk_geom"] = True
        if trunk_node_pick:
            row["trunk_node_pick"] = True
        view_state.setdefault("map_pick_regions", []).append(row)

    def _register_pick_polyline(
        pts: list, r_m: float, label: str, priority: int, *, trunk_geom: bool = False
    ) -> None:
        if len(pts) < 2:
            return
        flat = [(float(p[0]), float(p[1])) for p in pts]
        row: dict = {"kind": "polyline", "pts": flat, "r": r_m, "label": label, "p": priority}
        if trunk_geom:
            row["trunk_geom"] = True
        view_state.setdefault("map_pick_regions", []).append(row)

    def _register_pick_ring(ring: list, label: str, priority: int) -> None:
        if len(ring) < 3:
            return
        flat = [(float(p[0]), float(p[1])) for p in ring]
        view_state.setdefault("map_pick_regions", []).append(
            {"kind": "ring", "pts": flat, "label": label, "p": priority}
        )

    def _pick_map_object_at_world(wx: float, wy: float) -> str | None:
        hits: list[tuple[int, float, str, bool, bool]] = []
        for reg in view_state.get("map_pick_regions", []):
            kind = reg["kind"]
            pr = int(reg["p"])
            tg = bool(reg.get("trunk_geom"))
            tn = bool(reg.get("trunk_node_pick"))
            if kind == "disc":
                d = math.hypot(wx - reg["x"], wy - reg["y"])
                if d <= float(reg["r"]):
                    hits.append((pr, d, reg["label"], tg, tn))
            elif kind == "polyline":
                d = _polyline_min_dist_m(wx, wy, reg["pts"])
                if d <= float(reg["r"]):
                    hits.append((pr, d, reg["label"], tg, False))
            elif kind == "ring" and _point_in_ring(wx, wy, reg["pts"]):
                hits.append((pr, 0.0, reg["label"], False, False))
        if not hits:
            return None
        trunk_hits = [h for h in hits if h[3]]
        other_hits = [h for h in hits if not h[3]]
        if trunk_hits:
            node_hits = [h for h in trunk_hits if h[4]]
            seg_hits = [h for h in trunk_hits if not h[4]]
            best_n = min(node_hits, key=lambda h: h[1]) if node_hits else None
            best_s = min(seg_hits, key=lambda h: h[1]) if seg_hits else None
            if best_n is None:
                best_trunk = best_s
            elif best_s is None:
                best_trunk = best_n
            else:
                dn, ds = float(best_n[1]), float(best_s[1])
                amb_m = 2.0
                if dn <= amb_m and ds <= amb_m:
                    best_trunk = best_n
                else:
                    best_trunk = best_n if dn <= ds else best_s
            trunk_priority = bool(
                app is not None
                and hasattr(app, "_trunk_interaction_priority_active")
                and app._trunk_interaction_priority_active()
            )
            if trunk_priority and best_trunk is not None:
                return best_trunk[2]
            hits = ([best_trunk] if best_trunk is not None else []) + other_hits
        hits.sort(key=lambda t: (t[1], t[0]))
        return hits[0][2]

    def _draw_trunk_map_node_glyphs() -> None:
        """Фігури на canvas (не стандартний pin tkintermapview)."""
        if app is None:
            return
        c = map_widget.canvas
        nodes = list(getattr(app, "trunk_map_nodes", []) or [])
        for i, node in enumerate(nodes):
            try:
                wx = float(node["x"])
                wy = float(node["y"])
            except (TypeError, ValueError, KeyError):
                continue
            lat = None
            lon = None
            try:
                lat = float(node.get("lat"))
                lon = float(node.get("lon"))
            except (TypeError, ValueError):
                ll = _project_local_to_latlon(wx, wy)
                if ll is not None:
                    lat, lon = float(ll[0]), float(ll[1])
                    # Підлікуємо координати для наступних оновлень/збереження.
                    try:
                        node["lat"] = float(lat)
                        node["lon"] = float(lon)
                    except Exception:
                        pass
            if lat is None or lon is None:
                continue
            kind = str(node.get("kind", "")).lower()
            nid = str(node.get("id", "")).strip() or f"#{i}"
            if kind == "source":
                label = f"Насос (витік), {nid}"
                show_pipes = True
                if hasattr(app, "_trunk_map_hover_show_pipes_detail"):
                    show_pipes = app._trunk_map_hover_show_pipes_detail()
                if show_pipes and hasattr(app, "trunk_irrigation_hydro_pump_qp_hover_lines"):
                    qp = app.trunk_irrigation_hydro_pump_qp_hover_lines()
                    if qp:
                        label = f"{label}\n{qp[0]}\n{qp[1]}"
                elif not show_pipes:
                    label = f"{label}\nТопологія вузла (без Q/P з розрахунку)"
            elif kind == "bend":
                label = f"Пікет, {nid}"
            elif kind == "junction":
                label = f"Розгалуження (сумматор), {nid}"
            elif kind in ("consumption", "valve"):
                if hasattr(app, "trunk_consumer_display_caption"):
                    cap = app.trunk_consumer_display_caption(node, i)
                else:
                    cap = f"Споживач, {nid}"
                if hasattr(app, "_trunk_map_hover_show_pipes_detail") and app._trunk_map_hover_show_pipes_detail():
                    label = cap
                else:
                    if hasattr(app, "_trunk_consumption_is_terminal"):
                        role = "кінцевий" if app._trunk_consumption_is_terminal(i) else "проміжний"
                    else:
                        role = "—"
                    label = f"{cap}\nСпоживач ({role}) — топологія"
            else:
                label = f"Вузол магістралі, {nid}"
            _register_pick_disc(
                wx, wy, TRUNK_PICK_NODE_R_M, label, 0, trunk_geom=True, trunk_node_pick=True
            )
            p = _latlon_to_canvas_xy(lat, lon)
            if p is None:
                continue
            cx, cy = float(p[0]), float(p[1])
            if not (-80 < cx < float(map_widget.width) + 80 and -80 < cy < float(map_widget.height) + 80):
                continue
            g = 11.0
            if kind == "source":
                c.create_polygon(
                    cx,
                    cy - g,
                    cx + g,
                    cy,
                    cx,
                    cy + g,
                    cx - g,
                    cy,
                    fill="#D32F2F",
                    outline="#FFCDD2",
                    width=2,
                    tags="trunk_map_glyph",
                )
                if hasattr(app, "trunk_irrigation_hydro_pump_label_lines"):
                    plab = app.trunk_irrigation_hydro_pump_label_lines()
                    if plab:
                        c.create_text(
                            cx,
                            cy + g + 14,
                            text=plab[0],
                            anchor=tk.N,
                            fill="#FFE082",
                            font=("Segoe UI", 8, "bold"),
                            tags="trunk_map_glyph",
                        )
                        c.create_text(
                            cx,
                            cy + g + 28,
                            text=plab[1],
                            anchor=tk.N,
                            fill="#9E9E9E",
                            font=("Segoe UI", 7),
                            tags="trunk_map_glyph",
                        )
            elif kind == "bend":
                c.create_oval(
                    cx - g,
                    cy - g,
                    cx + g,
                    cy + g,
                    fill="#1E88E5",
                    outline="#BBDEFB",
                    width=2,
                    tags="trunk_map_glyph",
                )
            elif kind in ("consumption", "valve"):
                nid_map = str(node.get("id", "")).strip() or f"__{i}"
                stg = set(getattr(app, "_rozklad_staging_ids", []) or [])
                _fill = "#FFCA28" if nid_map in stg else "#C4933A"
                _outline = "#F57F17" if nid_map in stg else "#5D4037"
                _w = 3 if nid_map in stg else 2
                c.create_polygon(
                    cx,
                    cy - g * 1.05,
                    cx - g * 0.92,
                    cy + g * 0.58,
                    cx + g * 0.92,
                    cy + g * 0.58,
                    fill=_fill,
                    outline=_outline,
                    width=_w,
                    tags="trunk_map_glyph",
                )
                _nid_r = str(node.get("id", "")).strip()
                if _nid_r and hasattr(app, "_draw_consumer_irrigation_slot_rings"):
                    app._draw_consumer_irrigation_slot_rings(c, cx, cy, _nid_r, "trunk_map_glyph")
                is_terminal = True
                if hasattr(app, "_trunk_consumption_is_terminal"):
                    try:
                        is_terminal = bool(app._trunk_consumption_is_terminal(i))
                    except Exception:
                        is_terminal = True
                if is_terminal:
                    c.create_oval(
                        cx - 2.8,
                        cy - 2.8,
                        cx + 2.8,
                        cy + 2.8,
                        fill="#E8F5E9",
                        outline="#2E7D32",
                        width=1,
                        tags="trunk_map_glyph",
                    )
                else:
                    c.create_rectangle(
                        cx - 3.0,
                        cy - 3.0,
                        cx + 3.0,
                        cy + 3.0,
                        fill="#B2EBF2",
                        outline="#006064",
                        width=1,
                        tags="trunk_map_glyph",
                    )
            elif kind == "junction":
                Ro, Ri = g * 1.05, g * 0.42
                coords: list[float] = []
                for k in range(16):
                    ang = -0.5 * math.pi + k * (math.pi / 8)
                    R = Ro if k % 2 == 0 else Ri
                    coords.extend([cx + R * math.cos(ang), cy + R * math.sin(ang)])
                c.create_polygon(
                    *coords,
                    fill="#1565C0",
                    outline="#E3F2FD",
                    width=2,
                    tags="trunk_map_glyph",
                )
            else:
                c.create_oval(
                    cx - 4,
                    cy - 4,
                    cx + 4,
                    cy + 4,
                    fill="#757575",
                    outline="#FFFFFF",
                    width=1,
                    tags="trunk_map_glyph",
                )
            _nid_ins = str(node.get("id", "")).strip()
            if (
                _nid_ins
                and app is not None
                and _nid_ins == str(getattr(app, "_trunk_last_inserted_node_id", "")).strip()
            ):
                c.create_oval(
                    cx - g - 4,
                    cy - g - 4,
                    cx + g + 4,
                    cy + g + 4,
                    outline="#00E5FF",
                    width=3,
                    fill="",
                    tags="trunk_map_glyph",
                )
            if hasattr(app, "trunk_consumer_caption_lines"):
                cap_main, cap_sub = app.trunk_consumer_caption_lines(node, i)
            else:
                cap_main = (
                    app._trunk_map_node_caption(node, i)
                    if hasattr(app, "_trunk_map_node_caption")
                    else nid
                )
                cap_sub = None
            _ty = cy - g - 5 if kind != "junction" else cy - g * 1.35 - 4
            if cap_sub is not None:
                c.create_text(
                    cx + g + 5,
                    _ty,
                    text=cap_main,
                    anchor=tk.W,
                    fill="#FFF8E1",
                    font=("Segoe UI", 9, "bold"),
                    tags="trunk_map_glyph",
                )
                c.create_text(
                    cx + g + 5,
                    _ty + 12,
                    text=cap_sub,
                    anchor=tk.W,
                    fill="#B0BEC5",
                    font=("Segoe UI", 7),
                    tags="trunk_map_glyph",
                )
            else:
                c.create_text(
                    cx + g + 5,
                    _ty,
                    text=cap_main,
                    anchor=tk.W,
                    fill="#ECEFF1",
                    font=("Segoe UI", 8),
                    tags="trunk_map_glyph",
                )

        segs = list(getattr(app, "trunk_map_segments", []) or [])
        for si, seg in enumerate(segs):
            if not hasattr(app, "_trunk_segment_world_path"):
                break
            plw = app._trunk_segment_world_path(seg)
            if len(plw) < 2:
                continue
            mi = len(plw) // 2
            try:
                x0, y0 = float(plw[mi][0]), float(plw[mi][1])
            except (TypeError, ValueError, IndexError):
                continue
            ll = _project_local_to_latlon(x0, y0)
            if not ll:
                continue
            plat, plon = float(ll[0]), float(ll[1])
            pseg = _latlon_to_canvas_xy(plat, plon)
            if pseg is None:
                continue
            tcx, tcy = float(pseg[0]), float(pseg[1])
            if not (-80 < tcx < float(map_widget.width) + 80 and -80 < tcy < float(map_widget.height) + 80):
                continue
            if hasattr(app, "trunk_pipe_label_for_segment") and isinstance(seg, dict):
                cap = str(app.trunk_pipe_label_for_segment(seg))
            else:
                try:
                    dmm = float(seg.get("d_inner_mm", 90.0) or 90.0)
                except (TypeError, ValueError):
                    dmm = 90.0
                if hasattr(app, "trunk_pipe_label_for_inner_mm"):
                    cap = app.trunk_pipe_label_for_inner_mm(dmm)
                else:
                    cap = f"М{si + 1}"
            if len(cap) > 26:
                cap = cap[:23] + "…"
            c.create_text(
                tcx + 8,
                tcy - 8,
                text=cap,
                fill="#E1BEE7",
                font=("Segoe UI", 8, "bold"),
                tags="trunk_map_glyph",
            )

        drag_idx = getattr(app, "_trunk_node_drag_idx", None) if app is not None else None
        if (
            drag_idx is not None
            and hasattr(app, "_trunk_segment_world_path")
            and hasattr(app, "_polyline_length_m")
            and hasattr(app, "_polyline_point_at_dist")
        ):
            try:
                di_i = int(drag_idx)
            except (TypeError, ValueError):
                di_i = None
            if di_i is not None:
                for seg in list(getattr(app, "trunk_map_segments", []) or []):
                    if not isinstance(seg, dict):
                        continue
                    ni = seg.get("node_indices")
                    if not isinstance(ni, list):
                        continue
                    idxs: list[int] = []
                    for x in ni:
                        try:
                            idxs.append(int(x))
                        except (TypeError, ValueError):
                            idxs = []
                            break
                    if di_i not in idxs:
                        continue
                    plw = app._trunk_segment_world_path(seg)
                    if len(plw) < 2:
                        continue
                    try:
                        lm = float(app._polyline_length_m(plw))
                    except Exception:
                        continue
                    if lm <= 1e-6:
                        continue
                    try:
                        mx, my = app._polyline_point_at_dist(plw, lm * 0.5)
                    except ValueError:
                        continue
                    llm = _project_local_to_latlon(float(mx), float(my))
                    if not llm:
                        continue
                    pxy = _latlon_to_canvas_xy(float(llm[0]), float(llm[1]))
                    if pxy is None:
                        continue
                    tcx, tcy = float(pxy[0]), float(pxy[1])
                    if not (-80 < tcx < float(map_widget.width) + 80 and -80 < tcy < float(map_widget.height) + 80):
                        continue
                    c.create_text(
                        tcx,
                        tcy - 16,
                        text=f"{lm:.1f} м",
                        fill="#FFF59D",
                        font=("Segoe UI", 9, "bold"),
                        anchor=tk.S,
                        tags="trunk_map_glyph",
                    )

        probe = getattr(app, "_trunk_profile_probe_world", None) if app is not None else None
        if isinstance(probe, tuple) and len(probe) >= 2:
            try:
                llp = _project_local_to_latlon(float(probe[0]), float(probe[1]))
            except Exception:
                llp = None
            if llp:
                pxy = _latlon_to_canvas_xy(float(llp[0]), float(llp[1]))
                if pxy is not None:
                    px, py = float(pxy[0]), float(pxy[1])
                    c.create_oval(
                        px - 6,
                        py - 6,
                        px + 6,
                        py + 6,
                        fill="#00E5FF",
                        outline="#E0F7FA",
                        width=2,
                        tags="trunk_map_glyph",
                    )
                    c.create_line(px - 10, py, px + 10, py, fill="#00E5FF", width=2, tags="trunk_map_glyph")
                    c.create_line(px, py - 10, px, py + 10, fill="#00E5FF", width=2, tags="trunk_map_glyph")

        if hasattr(app, "_consumer_valve_snap_overlay_enabled") and app._consumer_valve_snap_overlay_enabled():
            try:
                r_m = float(app._consumer_valve_snap_radius_m())
            except Exception:
                r_m = 22.0
            try:
                for vx, vy in app.get_valves():
                    ll0 = _project_local_to_latlon(float(vx), float(vy))
                    ll1 = _project_local_to_latlon(float(vx) + r_m, float(vy))
                    if not ll0 or not ll1:
                        continue
                    p0 = _latlon_to_canvas_xy(float(ll0[0]), float(ll0[1]))
                    p1 = _latlon_to_canvas_xy(float(ll1[0]), float(ll1[1]))
                    if p0 is None or p1 is None:
                        continue
                    rad = max(3.0, math.hypot(float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])))
                    cx, cy = float(p0[0]), float(p0[1])
                    if not (-80 < cx < float(map_widget.width) + 80 and -80 < cy < float(map_widget.height) + 80):
                        continue
                    c.create_oval(
                        cx - rad,
                        cy - rad,
                        cx + rad,
                        cy + rad,
                        outline="#7CB342",
                        dash=(5, 4),
                        width=2,
                        fill="",
                        tags="trunk_map_glyph",
                    )
            except Exception:
                pass

    def _refresh_trunk_map_glyphs() -> None:
        """Перемалювати canvas-гліфи магістралі (вузли/підписи) після пану/масштабу."""
        try:
            map_widget.canvas.delete("trunk_map_glyph")
        except Exception:
            pass
        _draw_trunk_map_node_glyphs()

    def _safe_delete(path_obj):
        if not path_obj:
            return
        try:
            map_widget.delete(path_obj)
        except Exception:
            pass

    def _clear_project_overlay():
        for p in view_state.get("project_paths", []):
            _safe_delete(p)
        view_state["project_paths"] = []
        for m in view_state.get("trunk_map_markers", []):
            _safe_delete(m)
        view_state["trunk_map_markers"] = []
        try:
            map_widget.canvas.delete("trunk_map_glyph")
        except Exception:
            pass
        view_state["map_pick_regions"] = []
        for p in view_state.get("trunk_segment_paths", []):
            _safe_delete(p)
        view_state["trunk_segment_paths"] = []

    def _clear_cached_tiles_overlay():
        for p in view_state.get("cached_tile_paths", []):
            _safe_delete(p)
        view_state["cached_tile_paths"] = []

    def _show_cached_tiles_overlay():
        _clear_cached_tiles_overlay()
        if not show_cached_tiles_var.get():
            return
        try:
            c_lat = c_lon = None
            v_lat_min = v_lat_max = v_lon_min = v_lon_max = None
            try:
                c_lat, c_lon = map_widget.get_position()
                z = float(getattr(map_widget, "zoom", 12.0))
                c = map_widget.canvas
                cw = max(1, int(c.winfo_width()))
                ch = max(1, int(c.winfo_height()))
                lat_tl, lon_tl = map_widget.convert_canvas_coords_to_decimal_coords(0, 0)
                lat_br, lon_br = map_widget.convert_canvas_coords_to_decimal_coords(cw, ch)
                v_lat_min, v_lat_max = min(lat_tl, lat_br), max(lat_tl, lat_br)
                v_lon_min, v_lon_max = min(lon_tl, lon_br), max(lon_tl, lon_br)
            except Exception:
                z = 12.0
            cache_dir = srtm_tiles.ensure_srtm_dir()
            seen = set()
            for p in sorted(cache_dir.glob("*.hgt")) + sorted(cache_dir.glob("*.hgt.gz")):
                sw = srtm_tiles.parse_hgt_tile_sw_from_stem(srtm_tiles.hgt_path_tile_stem(p))
                if sw is None or sw in seen:
                    continue
                seen.add(sw)
                lat0, lon0 = sw
                lat1, lon1 = lat0 + 1, lon0 + 1
                ring = [
                    (lat0, lon0),
                    (lat0, lon1),
                    (lat1, lon1),
                    (lat1, lon0),
                    (lat0, lon0),
                ]
                is_in_view = True
                if None not in (v_lat_min, v_lat_max, v_lon_min, v_lon_max):
                    is_in_view = not (
                        lat1 < v_lat_min or lat0 > v_lat_max or lon1 < v_lon_min or lon0 > v_lon_max
                    )
                # Активні межі у viewport — помітніші; решта лишається фоновою сіткою.
                if is_in_view:
                    color = "#66ECFF"
                    width = 3 if z >= 10.0 else 2
                else:
                    color = "#2F6F7A"
                    width = 1
                view_state["cached_tile_paths"].append(
                    map_widget.set_path(ring, color=color, width=width)
                )
        except Exception:
            pass

    def _schedule_cached_tiles_overlay_refresh():
        if not show_cached_tiles_var.get():
            return
        old_job = view_state.get("cached_tile_refresh_job")
        if old_job:
            try:
                host.after_cancel(old_job)
            except Exception:
                pass
        view_state["cached_tile_refresh_job"] = host.after(180, _show_cached_tiles_overlay)

    def _clear_project_zone_map_overlay():
        _safe_delete(view_state.get("project_zone_map_path"))
        view_state["project_zone_map_path"] = None

    def _draw_project_zone_on_map():
        _clear_project_zone_map_overlay()
        if app is None or not getattr(app, "project_zone_bounds_local", None):
            return
        if not getattr(app, "geo_ref", None):
            return
        ring_ll = []
        ring_local = (
            app.project_zone_display_ring_local()
            if hasattr(app, "project_zone_display_ring_local")
            else None
        )
        if ring_local and len(ring_local) >= 3:
            for x, y in ring_local:
                ll = _project_local_to_latlon(float(x), float(y))
                if ll:
                    ring_ll.append(ll)
            if ring_ll:
                ring_ll.append(ring_ll[0])
        else:
            minx, miny, maxx, maxy = app.project_zone_bounds_local
            for x, y in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)):
                ll = _project_local_to_latlon(x, y)
                if ll:
                    ring_ll.append(ll)
        if len(ring_ll) >= 4:
            try:
                view_state["project_zone_map_path"] = map_widget.set_path(
                    ring_ll, color="#FF9800", width=3
                )
            except Exception:
                pass

    def _project_zone_latlon_bbox_from_app():
        if app is None or not getattr(app, "project_zone_bounds_local", None):
            return None
        if not getattr(app, "geo_ref", None):
            return None
        bb = app.project_zone_bounds_local
        ref_lon, ref_lat = app.geo_ref
        return srtm_tiles.wgs84_bounds_from_xy_bounds(
            float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]), (ref_lon, ref_lat)
        )

    def _project_local_to_latlon(x, y):
        if app is None or not getattr(app, "geo_ref", None):
            return None
        ref_lon, ref_lat = app.geo_ref
        lat, lon = srtm_tiles.local_xy_to_lat_lon(float(x), float(y), float(ref_lon), float(ref_lat))
        return (lat, lon)

    def _project_latlon_to_local(lat, lon):
        if app is None:
            return None
        if not getattr(app, "geo_ref", None):
            return None
        ref_lon, ref_lat = app.geo_ref
        x, y = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
        return (x, y)

    def _ensure_geo_ref_from_points(latlon_points):
        if app is None:
            return
        if getattr(app, "geo_ref", None):
            return
        if not latlon_points:
            return
        lat0, lon0 = latlon_points[0]
        app.geo_ref = (float(lon0), float(lat0))

    def _nearest_trunk_node_index(lat: float, lon: float):
        if app is None:
            return None, None
        nodes = list(getattr(app, "trunk_map_nodes", []) or [])
        if not nodes:
            return None, None
        xy_click = _project_latlon_to_local(lat, lon)
        if not xy_click:
            return None, None
        cx, cy = float(xy_click[0]), float(xy_click[1])
        best_i = None
        best_d = TRUNK_NODE_SNAP_M + 1.0
        for i, node in enumerate(nodes):
            try:
                nx = float(node.get("x"))
                ny = float(node.get("y"))
            except (TypeError, ValueError):
                continue
            d = math.hypot(cx - nx, cy - ny)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is None or best_d > TRUNK_NODE_SNAP_M:
            return None, best_d
        return best_i, best_d

    def _rebuild_trunk_route_draft_visual() -> None:
        nodes = list(getattr(app, "trunk_map_nodes", []) or []) if app else []
        idxs = (
            list(getattr(app, "_canvas_trunk_route_draft_indices", []) or [])
            if app is not None
            else []
        )
        view_state["draft_points"] = []
        for ii in idxs:
            if 0 <= ii < len(nodes):
                try:
                    la = float(nodes[ii]["lat"])
                    lo = float(nodes[ii]["lon"])
                    view_state["draft_points"].append((la, lo))
                except (KeyError, TypeError, ValueError):
                    pass
        if len(view_state["draft_points"]) >= 2:
            _draw_draft("#D1C4E9", 4, close_ring=False)
        else:
            _safe_delete(view_state.get("draft_path"))
            view_state["draft_path"] = None

    def _lat_line_color_map(li_use: int, manual: bool, is_calculated: bool) -> str:
        """Кольори статусу латераля як на полотні (без розбиття на крила)."""
        if app is None:
            return "#FFCC66" if manual else "#336699"
        if not is_calculated:
            return "#FFCC66" if manual else "#336699"
        base_ok = "#90EE90"
        aud = (app.calc_results.get("lateral_pressure_audit") or {}).get(f"lat_{li_use}")
        if not aud:
            return base_ok
        st = aud.get("status")
        if st == "overflow":
            return "#FF4444"
        if st == "underflow":
            return "#E8C547"
        if st == "both":
            return "#FF6600"
        return base_ok

    def _append_local_polyline(xy_seq, color: str, width: int, all_geo: list) -> None:
        lg = []
        for x, y in xy_seq:
            ll = _project_local_to_latlon(float(x), float(y))
            if ll:
                lg.append(ll)
        if len(lg) >= 2:
            view_state["project_paths"].append(
                map_widget.set_path(lg, color=color, width=width)
            )
            all_geo.extend(lg)

    def _append_lateral_geom(lat_obj, color: str, width: int, all_geo: list) -> None:
        try:
            coords = list(lat_obj.coords)
        except Exception:
            coords = list(lat_obj or [])
        _append_local_polyline(coords, color, width, all_geo)

    def _canvas_xy_from_world(wx: float, wy: float):
        if decimal_to_osm is None:
            return None
        ll = _project_local_to_latlon(wx, wy)
        if not ll:
            return None
        la, lo = float(ll[0]), float(ll[1])
        try:
            z = round(float(map_widget.zoom))
            tx, ty = decimal_to_osm(la, lo, z)
            ul_x, ul_y = map_widget.upper_left_tile_pos
            lr_x, lr_y = map_widget.lower_right_tile_pos
            cw = max(1, int(map_widget.canvas.winfo_width()))
            ch = max(1, int(map_widget.canvas.winfo_height()))
            span_x = float(lr_x - ul_x)
            span_y = float(lr_y - ul_y)
            if abs(span_x) < 1e-12 or abs(span_y) < 1e-12:
                return None
            rel_x = (float(tx) - float(ul_x)) / span_x
            rel_y = (float(ty) - float(ul_y)) / span_y
            return rel_x * cw, rel_y * ch
        except Exception:
            return None

    def _paint_map_draft_rubber(event) -> None:
        """Пунктир від останньої вершини контуру до курсора (інструменти карти до ПКМ)."""
        c = map_widget.canvas
        try:
            c.delete("map_draft_rubber")
        except Exception:
            pass
        tool = view_state.get("active_tool")
        if tool in _MAP_TOOLS_PASSIVE:
            return
        if tool not in _MAP_TOOLS_POLYLINE and tool not in _MAP_TOOLS_TRUNK_POINT:
            return
        if tool == "trunk_route" and app is not None and len(getattr(app, "trunk_map_nodes", []) or []) > 0:
            nodes = list(getattr(app, "trunk_map_nodes", []) or [])
            draft_i = list(getattr(app, "_canvas_trunk_route_draft_indices", []) or [])
            if not draft_i or not getattr(app, "geo_ref", None):
                return
            last = int(draft_i[-1])
            if not (0 <= last < len(nodes)):
                return
            try:
                la0, lo0 = float(nodes[last]["lat"]), float(nodes[last]["lon"])
            except (KeyError, TypeError, ValueError):
                return
            try:
                ex = int(getattr(event, "x", 0))
                ey = int(getattr(event, "y", 0))
                lat_e, lon_e = map_widget.convert_canvas_coords_to_decimal_coords(ex, ey)
            except Exception:
                return
            p0 = _latlon_to_canvas_xy(la0, lo0)
            p1 = _latlon_to_canvas_xy(float(lat_e), float(lon_e))
            if p0 is None or p1 is None:
                return
            c.create_line(
                p0[0],
                p0[1],
                p1[0],
                p1[1],
                fill=TRUNK_PATH_COLOR,
                dash=(6, 4),
                width=2,
                tags="map_draft_rubber",
            )
            try:
                if c.find_withtag("map_draft_rubber"):
                    c.lift("map_draft_rubber")
                if c.find_withtag("map_live_preview"):
                    c.lift("map_live_preview")
                if c.find_withtag("trunk_map_glyph"):
                    c.lift("trunk_map_glyph")
            except Exception:
                pass
            return
        pts = list(view_state.get("draft_points") or [])
        if not pts:
            return
        try:
            ex = int(getattr(event, "x", 0))
            ey = int(getattr(event, "y", 0))
            lat_e, lon_e = map_widget.convert_canvas_coords_to_decimal_coords(ex, ey)
        except Exception:
            return
        la0, lo0 = float(pts[-1][0]), float(pts[-1][1])
        p0 = _latlon_to_canvas_xy(la0, lo0)
        p1 = _latlon_to_canvas_xy(lat_e, lon_e)
        if p0 is None or p1 is None:
            return
        if tool == "capture_tiles":
            color = "#00D1FF"
        elif tool == "block_contour":
            color = "#FFD400"
        elif tool == "scene_lines":
            color = "#B8C0CC"
        elif tool == "trunk_pump":
            color = "#EF5350"
        elif tool == "trunk_picket":
            color = "#42A5F5"
        elif tool == "trunk_junction":
            color = "#1E88E5"
        elif tool == "trunk_consumer":
            color = "#C4933A"
        else:
            color = TRUNK_PATH_COLOR
        c.create_line(
            p0[0],
            p0[1],
            p1[0],
            p1[1],
            fill=color,
            dash=(6, 4),
            width=2,
            tags="map_draft_rubber",
        )
        try:
            if c.find_withtag("map_draft_rubber"):
                c.lift("map_draft_rubber")
            if c.find_withtag("map_live_preview"):
                c.lift("map_live_preview")
            if c.find_withtag("trunk_map_glyph"):
                c.lift("trunk_map_glyph")
        except Exception:
            pass

    def _paint_map_live_preview() -> None:
        """Резинові лінії режимів D / SM / LT / R на полотні карти."""
        try:
            c = map_widget.canvas
            c.delete("map_live_preview")
        except Exception:
            return
        if app is None or not getattr(app, "geo_ref", None):
            return
        ptr = getattr(app, "_last_map_pointer_world", None)
        if ptr is None:
            return
        cx, cy = float(ptr[0]), float(ptr[1])
        m = app.mode.get()
        try:
            c = map_widget.canvas
            if view_state.get("active_tool") in _MAP_TOOLS_PASSIVE and hasattr(
                app, "trunk_info_highlight_world_paths"
            ):
                lime_hp, yel_hp = app.trunk_info_highlight_world_paths(cx, cy)
                for pl in lime_hp:
                    scr_hp = []
                    for x, y in pl:
                        xy = _canvas_xy_from_world(float(x), float(y))
                        if xy:
                            scr_hp.extend(xy)
                    if len(scr_hp) >= 4:
                        c.create_line(
                            *scr_hp,
                            fill="#ADFF2F",
                            width=7,
                            tags="map_live_preview",
                        )
                for pl in yel_hp:
                    scr_hp2 = []
                    for x, y in pl:
                        xy = _canvas_xy_from_world(float(x), float(y))
                        if xy:
                            scr_hp2.extend(xy)
                    if len(scr_hp2) >= 4:
                        c.create_line(
                            *scr_hp2,
                            fill="#FFEB3B",
                            width=5,
                            tags="map_live_preview",
                        )
            if m == "RULER" and app.ruler_start:
                rx, ry = app.ruler_start
                p0 = _canvas_xy_from_world(rx, ry)
                p1 = _canvas_xy_from_world(cx, cy)
                if p0 and p1:
                    c.create_line(
                        p0[0], p0[1], p1[0], p1[1],
                        fill="#00FFFF", dash=(4, 4), width=2, tags="map_live_preview",
                    )
                    dist = math.hypot(cx - rx, cy - ry)
                    mid_x = (p0[0] + p1[0]) * 0.5
                    mid_y = (p0[1] + p1[1]) * 0.5
                    c.create_text(
                        mid_x + 8, mid_y - 8,
                        text=f"{dist:.1f} м",
                        fill="#00FFFF",
                        font=("Segoe UI", 9, "bold"),
                        tags="map_live_preview",
                    )
            if m == "DRAW" and app.points and not app.is_closed:
                pts_draw = list(app.points)
                if len(pts_draw) >= 2:
                    scr_done = []
                    for px, py in pts_draw:
                        xy = _canvas_xy_from_world(float(px), float(py))
                        if xy:
                            scr_done.extend(xy)
                    if len(scr_done) >= 4:
                        c.create_line(
                            *scr_done,
                            fill="#FFFFFF",
                            width=2,
                            tags="map_live_preview",
                        )
                lx, ly = pts_draw[-1]
                tx, ty = cx, cy
                if app.ortho_on.get():
                    if abs(tx - lx) > abs(ty - ly):
                        ty = ly
                    else:
                        tx = lx
                p0 = _canvas_xy_from_world(lx, ly)
                p1 = _canvas_xy_from_world(tx, ty)
                if p0 and p1:
                    c.create_line(
                        p0[0], p0[1], p1[0], p1[1],
                        fill="#FFFFFF",
                        dash=(4, 4),
                        width=2,
                        tags="map_live_preview",
                    )
            if m == "SUBMAIN" and app.active_submain:
                if app._submain_preview_world and len(app._submain_preview_world) >= 2:
                    chain = list(app.active_submain[:-1]) + list(app._submain_preview_world)
                elif app._current_live_end:
                    chain = list(app.active_submain) + [app._current_live_end]
                else:
                    chain = list(app.active_submain)
                scr = []
                for p in chain:
                    if p is None:
                        continue
                    xy = _canvas_xy_from_world(float(p[0]), float(p[1]))
                    if xy:
                        scr.extend(xy)
                if len(scr) >= 4:
                    c.create_line(*scr, fill="#FF5588", width=6, tags="map_live_preview")
            if m == "DRAW_LAT" and app.active_manual_lat:
                aml = list(app.active_manual_lat)
                if len(aml) >= 2:
                    scr_lat = []
                    for px, py in aml:
                        xy = _canvas_xy_from_world(float(px), float(py))
                        if xy:
                            scr_lat.extend(xy)
                    if len(scr_lat) >= 4:
                        c.create_line(
                            *scr_lat,
                            fill="#FFAA44",
                            width=2,
                            tags="map_live_preview",
                        )
                lx, ly = aml[-1]
                p0 = _canvas_xy_from_world(lx, ly)
                p1 = _canvas_xy_from_world(cx, cy)
                if p0 and p1:
                    c.create_line(
                        p0[0], p0[1], p1[0], p1[1],
                        fill="#FFAA44",
                        dash=(3, 3),
                        width=2,
                        tags="map_live_preview",
                    )
            if m == "CUT_LATS" and app._cut_line_start and app.action.get() == "ADD":
                sx, sy = app._cut_line_start
                p0 = _canvas_xy_from_world(sx, sy)
                p1 = _canvas_xy_from_world(cx, cy)
                if p0 and p1:
                    c.create_line(
                        p0[0], p0[1], p1[0], p1[1],
                        fill="#FF8800", dash=(4, 4), width=2, tags="map_live_preview",
                    )
            if hasattr(app, "paint_trunk_hydro_hover_on_map_canvas") and m not in (
                "RULER",
                "DEL",
                "INFO",
                "LAT_TIP",
            ):
                try:
                    app.paint_trunk_hydro_hover_on_map_canvas(c, cx, cy, _canvas_xy_from_world)
                except Exception:
                    pass
            try:
                if c.find_withtag("map_live_preview"):
                    c.lift("map_live_preview")
                if c.find_withtag("trunk_map_glyph"):
                    c.lift("trunk_map_glyph")
            except Exception:
                pass
        except Exception:
            pass

    def _show_project_overlay(fit_bounds: bool = True):
        if app is None:
            return
        if not getattr(app, "geo_ref", None):
            return
        _clear_project_overlay()
        all_geo = []
        try:
            blocks = list(getattr(app, "field_blocks", []) or [])
            calc_res = getattr(app, "calc_results", None) or {}
            is_calculated = bool(calc_res.get("sections"))
            all_sm_lines = (
                app._all_submain_lines()
                if hasattr(app, "_all_submain_lines")
                else [
                    sm
                    for b in blocks
                    for sm in list(b.get("submain_lines") or [])
                    if len(sm) >= 2
                ]
            )

            for bi, b in enumerate(blocks):
                ring = list(b.get("ring") or [])
                if show_blocks_var.get() and len(ring) >= 3:
                    rg = []
                    for x, y in ring:
                        ll = _project_local_to_latlon(x, y)
                        if ll:
                            rg.append(ll)
                    if len(rg) >= 3:
                        view_state["project_paths"].append(
                            map_widget.set_path(rg + [rg[0]], color="#00E5FF", width=3)
                        )
                        all_geo.extend(rg)
                        _register_pick_ring(ring, f"Блок поля {bi + 1}", 4)

            pz_ring_pick = (
                app.project_zone_display_ring_local()
                if hasattr(app, "project_zone_display_ring_local")
                else None
            )
            if pz_ring_pick and len(pz_ring_pick) >= 3:
                _register_pick_ring(
                    list(pz_ring_pick),
                    "Майданчик проєкту (місце роботи, не блок поля)",
                    50,
                )

            if show_submains_var.get():
                if is_calculated and hasattr(app, "_sections_for_canvas_draw"):
                    section_parts = app._sections_for_canvas_draw()
                    represented_sm = {
                        int(s.get("sm_idx", -1)) for s in section_parts
                    }
                    for sec in section_parts:
                        coords = sec.get("coords") or []
                        if len(coords) < 2:
                            continue
                        col = (
                            app._section_draw_color(sec)
                            if hasattr(app, "_section_draw_color")
                            else sec.get("color") or "#FF3366"
                        )
                        _append_local_polyline(coords, col, 5, all_geo)
                    for sm_i, sm in enumerate(all_sm_lines):
                        if sm_i in represented_sm or len(sm) < 2:
                            continue
                        _append_local_polyline(sm, "#FF3366", 4, all_geo)
                else:
                    for b in blocks:
                        for sm in list(b.get("submain_lines") or []):
                            if len(sm) < 2:
                                continue
                            _append_local_polyline(sm, "#FFA500", 3, all_geo)

            if show_laterals_var.get():
                lat_draw_idx = 0
                for b in blocks:
                    if not is_calculated:
                        for lat in b.get("auto_laterals") or []:
                            c = _lat_line_color_map(lat_draw_idx, False, False)
                            _append_lateral_geom(lat, c, 2, all_geo)
                            lat_draw_idx += 1
                    else:
                        for grp in app._per_submain_ordered_auto_laterals(b):
                            n_g = len(grp)
                            if n_g == 0:
                                continue
                            show_g = app._visible_auto_lateral_indices(n_g)
                            for i, lat in enumerate(grp):
                                if i not in show_g:
                                    lat_draw_idx += 1
                                    continue
                                gidx = (
                                    app._global_lat_flat_index(lat)
                                    if hasattr(app, "_global_lat_flat_index")
                                    else None
                                )
                                li_use = gidx if gidx is not None else lat_draw_idx
                                c = _lat_line_color_map(li_use, False, True)
                                _append_lateral_geom(lat, c, 2, all_geo)
                                lat_draw_idx += 1
                    for lat in b.get("manual_laterals") or []:
                        gidx = (
                            app._global_lat_flat_index(lat)
                            if hasattr(app, "_global_lat_flat_index")
                            else None
                        )
                        li_use = gidx if gidx is not None else lat_draw_idx
                        c = _lat_line_color_map(li_use, True, is_calculated)
                        _append_lateral_geom(lat, c, 2, all_geo)
                        lat_draw_idx += 1
            for seg in getattr(app, "scene_lines", []) or []:
                if len(seg) >= 2:
                    _append_local_polyline(seg, "#A8AEB8", 2, all_geo)
            if show_submains_var.get():
                for bi, b in enumerate(blocks):
                    for sm_i, sm in enumerate(list(b.get("submain_lines") or [])):
                        if len(sm) < 2:
                            continue
                        _register_pick_polyline(
                            sm,
                            SUBMAIN_PICK_R_M,
                            f"Сабмейн · блок {bi + 1} · лінія {sm_i + 1}",
                            3,
                        )
            if hasattr(app, "get_valves"):
                try:
                    for vx, vy in app.get_valves():
                        _register_pick_disc(
                            float(vx),
                            float(vy),
                            VALVE_NODE_PICK_R_M,
                            "Кран (початок відрізка сабмейну)",
                            1,
                        )
                except Exception:
                    pass
            nodes = list(getattr(app, "trunk_map_nodes", []) or [])
            for node in nodes:
                try:
                    all_geo.append((float(node.get("lat")), float(node.get("lon"))))
                except (TypeError, ValueError):
                    pass
            seg_draw = view_state.setdefault("trunk_segment_paths", [])
            for si, seg in enumerate(list(getattr(app, "trunk_map_segments", []) or [])):
                if hasattr(app, "_trunk_segment_world_path"):
                    pl = app._trunk_segment_world_path(seg)
                else:
                    pl = seg.get("path_local") or []
                if len(pl) < 2:
                    continue
                seg_d = seg if isinstance(seg, dict) else {}
                if hasattr(app, "_trunk_segment_telescope_path_chunks"):
                    chunks = app._trunk_segment_telescope_path_chunks(seg_d, pl)
                else:
                    chunks = [(pl, None)]
                vw = 0.0
                if hasattr(app, "trunk_display_velocity_warn_mps_effective"):
                    vw = float(app.trunk_display_velocity_warn_mps_effective())
                vm = None
                if hasattr(app, "trunk_segment_velocity_mps_from_hydro_cache"):
                    vm = app.trunk_segment_velocity_mps_from_hydro_cache(si)
                warn_vel = vw > 1e-9 and vm is not None and vm + 1e-9 >= vw
                for chunk_pl, sec in chunks:
                    lg = []
                    for xy in chunk_pl:
                        if isinstance(xy, (list, tuple)) and len(xy) >= 2:
                            ll = _project_local_to_latlon(float(xy[0]), float(xy[1]))
                            if ll:
                                lg.append(ll)
                    if len(lg) < 2:
                        continue
                    try:
                        col = TRUNK_PATH_COLOR
                        if hasattr(app, "_trunk_telescope_chunk_line_color"):
                            col = app._trunk_telescope_chunk_line_color(si, sec)
                        elif hasattr(app, "trunk_hydro_segment_line_color"):
                            hc = app.trunk_hydro_segment_line_color(si)
                            if hc:
                                col = hc
                        if warn_vel:
                            seg_draw.append(
                                map_widget.set_path(lg, color="#B71C1C", width=10)
                            )
                        seg_draw.append(
                            map_widget.set_path(lg, color=col, width=6)
                        )
                    except Exception:
                        pass
                    all_geo.extend(lg)
                label = f"Магістраль, відрізок {si + 1}"
                if hasattr(app, "trunk_map_pick_label_for_segment"):
                    try:
                        label = str(app.trunk_map_pick_label_for_segment(si))
                    except Exception:
                        pass
                elif hasattr(app, "trunk_segment_display_caption"):
                    try:
                        label = str(app.trunk_segment_display_caption(si))
                    except Exception:
                        pass
                _register_pick_polyline(
                    pl,
                    TRUNK_PICK_LINE_R_M,
                    label,
                    2,
                    trunk_geom=True,
                )
            _draw_trunk_map_node_glyphs()
        except Exception:
            return
        if fit_bounds and all_geo:
            lats = [p[0] for p in all_geo]
            lons = [p[1] for p in all_geo]
            _fit_bounds(min(lats), min(lons), max(lats), max(lons))
        _show_cached_tiles_overlay()
        _draw_project_zone_on_map()
        _paint_map_live_preview()

    _MAP_TRUNK_CHAIN = frozenset(
        {"trunk_route", "trunk_pump", "trunk_picket", "trunk_junction", "trunk_consumer"}
    )

    def _set_tool(name):
        """None — навігація (ЛКМ+drag на тайлах tkintermapview); «Вибір»/«Інфо» — окремі active_tool."""
        pd = view_state.get("pz_drag")
        if pd and pd.get("rect") is not None:
            try:
                map_widget.canvas.delete(pd["rect"])
            except Exception:
                pass
        view_state["pz_drag"] = None
        try:
            map_widget.canvas.delete("map_draft_rubber")
        except Exception:
            pass
        prev_active = view_state.get("active_tool")
        draft_snapshot = list(view_state.get("trunk_draft_indices") or [])
        app_draft_len = (
            len(getattr(app, "_canvas_trunk_route_draft_indices", []) or [])
            if app is not None
            else 0
        )
        view_state["active_tool"] = name
        view_state["draft_points"] = []
        preserve_trunk_draft = name is not None and (
            (prev_active in _MAP_TRUNK_CHAIN and name in _MAP_TRUNK_CHAIN)
            or (name == "trunk_route" and (len(draft_snapshot) > 0 or app_draft_len > 0))
        )
        if not preserve_trunk_draft:
            view_state["trunk_draft_indices"] = []
            if app is not None:
                app._canvas_trunk_route_draft_indices = []
                app._trunk_route_endpoint_pending_idx = None
                app._trunk_route_edge_end_idx = None
        _safe_delete(view_state.get("draft_path"))
        view_state["draft_path"] = None
        if name == "capture_tiles":
            hint.set("Контур захвату тайлів (ЛКМ вершини, ПКМ завершити)")
        elif name == "block_contour":
            hint.set("Контур блоку (ЛКМ вершини, ПКМ завершити)")
        elif name == "trunk_route":
            if app is not None and len(getattr(app, "trunk_map_nodes", []) or []) > 0:
                hint.set(
                    f"Труба магістралі: ЛКМ+ПКМ на вузлі — кінець ребра; далі ЛКМ — початок і трасувальні точки "
                    f"(вільне поле — пікет); ПКМ — з’єднати з кінцем. Прив’язка ~{int(TRUNK_NODE_SNAP_M)} м. "
                    f"Топологія — «Зберегти граф магістралі»."
                )
            else:
                hint.set("Траса в блок (без вузлів на карті): ЛКМ вершини, ПКМ завершити → сабмейн активного блоку")
        elif name == "trunk_pump":
            hint.set("Насос: лише один — кожен ЛКМ переміщує, ПКМ — вийти з команди")
        elif name == "map_pick_info":
            hint.set(
                "Інфо: ЛКМ — назва; біля магістралі — жовтий шлях до насоса, "
                "біля розгалуження ще лайм — гілки до споживачів"
            )
        elif name == "select":
            hint.set(
                "Вибір (стрілка): ЛКМ — об'єкт; магістраль — ті самі підсвічені шляхи, що в «Інфо»"
            )
        elif name == "trunk_picket":
            hint.set("Пікет: ЛКМ — ставити по одному, ПКМ — вийти з команди")
        elif name == "trunk_junction":
            hint.set("Розгалуження: ЛКМ — новий вузол, ПКМ — вийти")
        elif name == "trunk_consumer":
            hint.set("Споживач: ЛКМ — новий вузол, ПКМ — вийти")
        elif name == "scene_lines":
            hint.set("Лінії (ситуація): ЛКМ — вершини, ПКМ — завершити; без розрахунку")
        elif name == "project_zone_rect":
            hint.set("Зона проєкту: ЛКМ потягніть прямокутник на карті")
        else:
            hint.set("Інструмент: навігація")
        try:
            map_widget.canvas.config(
                cursor="hand2"
                if name == "map_pick_info"
                else ("arrow" if name == "select" else "")
            )
        except Exception:
            pass
        if (
            name == "trunk_route"
            and app is not None
            and len(getattr(app, "trunk_map_nodes", []) or []) > 0
            and app is not None
            and len(getattr(app, "_canvas_trunk_route_draft_indices", []) or []) > 0
        ):
            _rebuild_trunk_route_draft_visual()

    def _draw_draft(color, width, close_ring=False):
        _safe_delete(view_state.get("draft_path"))
        pts = list(view_state["draft_points"])
        if len(pts) < 2:
            return
        draw_pts = pts + [pts[0]] if close_ring and len(pts) >= 3 else pts
        try:
            view_state["draft_path"] = map_widget.set_path(draw_pts, color=color, width=width)
        except Exception:
            view_state["draft_path"] = None

    def _finish_shape(event=None):
        tool = view_state.get("active_tool")
        pts = list(view_state.get("draft_points") or [])
        keep_tool_active = False
        if tool == "capture_tiles" and len(pts) >= 3:
            view_state["capture_points"] = list(pts)
            _safe_delete(view_state.get("capture_path"))
            view_state["capture_path"] = map_widget.set_path(pts + [pts[0]], color="#00D1FF", width=4)
            if app is not None:
                _ensure_geo_ref_from_points(pts)
                local = []
                for lat, lon in pts:
                    xy = _project_latlon_to_local(lat, lon)
                    if xy:
                        local.append(xy)
                if len(local) >= 3 and hasattr(app, "topo"):
                    app.topo.srtm_boundary_pts_local = list(local)
                    if hasattr(app, "sync_srtm_model_status"):
                        app.sync_srtm_model_status()
                    if hasattr(app, "redraw"):
                        app.redraw()
        elif tool == "block_contour" and len(pts) >= 3:
            _safe_delete(view_state.get("block_path"))
            view_state["block_path"] = map_widget.set_path(pts + [pts[0]], color="#FFD400", width=4)
            if app is not None and hasattr(app, "_new_field_block"):
                _ensure_geo_ref_from_points(pts)
                ring_local = []
                for lat, lon in pts:
                    xy = _project_latlon_to_local(lat, lon)
                    if xy:
                        ring_local.append(xy)
                if len(ring_local) >= 3:
                    max_b = int(getattr(app, "MAX_FIELD_BLOCKS", 100) or 100)
                    if len(app.field_blocks) >= max_b:
                        silent_showwarning(_map_silent_parent(), 
                            "Карта",
                            f"Досягнуто максимум блоків поля ({max_b}).",
                        )
                    else:
                        app.field_blocks.append(app._new_field_block(ring_local))
                        if hasattr(app, "_refresh_active_block_combo"):
                            app._refresh_active_block_combo()
                        if hasattr(app, "zoom_to_fit"):
                            app.zoom_to_fit()
                        if hasattr(app, "reset_calc"):
                            app.reset_calc()
                        if hasattr(app, "redraw"):
                            app.redraw()
                elif len(pts) >= 3:
                    silent_showwarning(_map_silent_parent(), 
                        "Карта",
                        "Не вдалося перевести вершини контуру в локальні координати. "
                        "Перевірте геоприв'язку проєкту або намалюйте контур ще раз.",
                    )
        elif tool == "trunk_route":
            nodes = list(getattr(app, "trunk_map_nodes", []) or []) if app else []
            if app is not None and len(nodes) > 0:
                wx: float | None = None
                wy: float | None = None
                if event is not None and getattr(app, "geo_ref", None):
                    try:
                        lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(
                            int(event.x), int(event.y)
                        )
                        ref_lon, ref_lat = app.geo_ref
                        wx, wy = srtm_tiles.lat_lon_to_local_xy(
                            float(lat), float(lon), float(ref_lon), float(ref_lat)
                        )
                    except Exception:
                        pass
                if wx is None or wy is None:
                    silent_showwarning(
                        _map_silent_parent(),
                        "Магістраль",
                        "Для ПКМ потрібна геоприв’язка проєкту та коректний клік на карті.",
                    )
                    keep_tool_active = True
                else:
                    exit_tool = app.handle_trunk_route_right_click_world(float(wx), float(wy))
                    keep_tool_active = not exit_tool
                    view_state["trunk_draft_indices"] = []
                    view_state["draft_points"] = []
                    _safe_delete(view_state.get("draft_path"))
                    _safe_delete(view_state.get("trunk_path"))
                    try:
                        map_widget.canvas.delete("map_draft_rubber")
                    except Exception:
                        pass
                    if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                        app._schedule_embedded_map_overlay_refresh()
                    else:
                        _show_project_overlay(False)
                    _rebuild_trunk_route_draft_visual()
            elif len(pts) >= 2:
                _safe_delete(view_state.get("trunk_path"))
                view_state["trunk_path"] = map_widget.set_path(pts, color=TRUNK_PATH_COLOR, width=4)
                if app is not None and hasattr(app, "_safe_active_block_idx"):
                    _ensure_geo_ref_from_points(pts)
                    line_local = []
                    for lat, lon in pts:
                        xy = _project_latlon_to_local(lat, lon)
                        if xy:
                            line_local.append(xy)
                    if len(line_local) >= 2:
                        bi = app._safe_active_block_idx()
                        if bi is not None and 0 <= bi < len(app.field_blocks):
                            app.field_blocks[bi].setdefault("submain_lines", []).append(list(line_local))
                            if hasattr(app, "reset_calc"):
                                app.reset_calc()
                            if hasattr(app, "redraw"):
                                app.redraw()
        elif tool == "scene_lines" and len(pts) >= 2:
            if app is not None:
                _ensure_geo_ref_from_points(pts)
                line_local = []
                for lat, lon in pts:
                    xy = _project_latlon_to_local(lat, lon)
                    if xy:
                        line_local.append(xy)
                if len(line_local) >= 2:
                    app.scene_lines.append(list(line_local))
                    if hasattr(app, "redraw"):
                        app.redraw()
                    _show_project_overlay(False)
        if not keep_tool_active:
            _set_tool(None)

    def _download_tiles():
        zone_ll = _project_zone_latlon_bbox_from_app()
        if zone_ll is not None:
            lat_lo, lat_hi, lon_lo, lon_hi = zone_ll
            src_label = "рамка зони проєкту"
            tiles = srtm_tiles.iter_tiles_covering_bbox(lat_lo, lat_hi, lon_lo, lon_hi)
        else:
            pts_capture = list(view_state.get("capture_points") or [])
            pts_kml = list(view_state.get("kml_points") or [])
            if len(pts_capture) >= 3:
                pts = pts_capture
                src_label = "контур захвату"
            elif len(pts_kml) >= 3:
                pts = pts_kml
                src_label = "відкритий KML"
            else:
                silent_showwarning(_map_silent_parent(), 
                    "SRTM",
                    "Немає зони для завантаження.\n"
                    "Задайте «Зону проєкту (рамка)» на карті або намалюйте контур захвату / KML.",
                )
                return
            lats = [p[0] for p in pts]
            lons = [p[1] for p in pts]
            tiles = srtm_tiles.iter_tiles_covering_bbox(min(lats), max(lats), min(lons), max(lons))
        if not tiles:
            return
        cache_dir = srtm_tiles.ensure_srtm_dir()
        missing_tiles = []
        existing_n = 0
        for la, lo in tiles:
            if srtm_tiles.resolve_hgt_path(cache_dir, la, lo) is not None:
                existing_n += 1
            else:
                missing_tiles.append((la, lo))
        if not missing_tiles:
            dl_status.set(f"SRTM: уже в кеші {existing_n}/{len(tiles)}")
            _show_cached_tiles_overlay()
            silent_showinfo(_map_silent_parent(), 
                "SRTM",
                f"Усі тайли вже завантажені.\n"
                f"Знайдено в кеші: {existing_n}/{len(tiles)}.",
            )
            return
        tile_src = srtm_tiles.resolve_tile_source_from_app(app)
        if tile_src == "open_elevation":
            silent_showwarning(
                _map_silent_parent(),
                "SRTM",
                "Open-Elevation не надає файли тайлів .hgt.\n"
                "У верхній панелі оберіть «Skadi+локальні» або «NASA Earthdata» (earthaccess або EARTHDATA_SRTM_TILE_BASE).",
            )
            return
        src_tile_label = {"skadi": "Skadi (AWS)", "earthdata": "NASA Earthdata"}.get(tile_src, tile_src)
        if not silent_askyesno(
            _map_silent_parent(),
            "SRTM",
            f"Джерело меж: {src_label}\n"
            f"Джерело тайлів: {src_tile_label}\n"
            f"Всього в межах: {len(tiles)}\n"
            f"Уже в кеші: {existing_n}\n"
            f"До завантаження: {len(missing_tiles)}\n\n"
            f"Завантажити відсутні тайли у:\n{SRTM_DIR} ?",
        ):
            return

        dl_status.set(f"SRTM: 0/{len(missing_tiles)}")

        def _task():
            srtm_tiles.ensure_srtm_dir()
            ok_n = 0
            for i, (la, lo) in enumerate(missing_tiles, start=1):
                ok, _msg = srtm_tiles.download_tile(la, lo, tile_source=tile_src)
                if ok:
                    ok_n += 1
                host.after(0, lambda i=i, ok_n=ok_n: dl_status.set(f"SRTM: {i}/{len(missing_tiles)}, успішно {ok_n}"))
            host.after(0, lambda: _finish(ok_n, len(missing_tiles), existing_n, len(tiles)))

        def _finish(ok_n, total_downloaded, existed_n, total_all):
            dl_status.set(
                f"SRTM: завершено, нових {ok_n}/{total_downloaded}, "
                f"в кеші {existed_n + ok_n}/{total_all}"
            )
            _show_cached_tiles_overlay()

        threading.Thread(target=_task, daemon=True).start()

    zoom_box_state = {"on": False, "x0": 0, "y0": 0, "rect": None}

    def _fit_bounds(lat_min, lon_min, lat_max, lon_max):
        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min
        if lon_min > lon_max:
            lon_min, lon_max = lon_max, lon_min
        view_state["last_bounds"] = (lat_min, lon_min, lat_max, lon_max)
        c_lat = (lat_min + lat_max) * 0.5
        c_lon = (lon_min + lon_max) * 0.5
        lat_span = max(1e-6, abs(lat_max - lat_min))
        lon_span = max(1e-6, abs(lon_max - lon_min))
        deg_span = max(lat_span, lon_span)
        z = int(round(math.log2(360.0 / deg_span)))
        z = max(2, min(19, z - 1))

        def _apply_once():
            try:
                if hasattr(map_widget, "fit_bounding_box"):
                    map_widget.fit_bounding_box((lat_max, lon_min), (lat_min, lon_max))
            except Exception:
                pass
            try:
                map_widget.set_position(c_lat, c_lon)
                map_widget.set_zoom(z)
            except Exception:
                pass

        _apply_once()
        host.after(120, _apply_once)
        host.after(320, _apply_once)

    def _load_kml():
        path = filedialog.askopenfilename(
            title="Відкрити KML",
            filetypes=[("KML files", "*.kml"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
            blocks = re.findall(r"<coordinates>(.*?)</coordinates>", txt, flags=re.DOTALL | re.IGNORECASE)
            coords = []
            segments = []
            for b in blocks:
                seg = []
                for token in b.strip().split():
                    parts = token.split(",")
                    if len(parts) < 2:
                        continue
                    try:
                        lon = float(parts[0]); lat = float(parts[1])
                    except ValueError:
                        continue
                    pt = (lat, lon)
                    coords.append(pt)
                    seg.append(pt)
                if len(seg) >= 2:
                    segments.append(seg)
            if not coords:
                raise ValueError("У KML не знайдено координати.")
            view_state["kml_points"] = list(coords)
            for p in view_state.get("kml_paths", []):
                _safe_delete(p)
            view_state["kml_paths"] = []
            for seg in segments:
                try:
                    p = map_widget.set_path(seg, color="#FF3B30", width=4)
                    view_state["kml_paths"].append(p)
                except Exception:
                    pass
            lats = [p[0] for p in coords]
            lons = [p[1] for p in coords]
            _fit_bounds(min(lats), min(lons), max(lats), max(lons))
        except Exception as ex:
            silent_showerror(_map_silent_parent(), "KML", f"Не вдалося прочитати KML:\n{ex}")

    def _zoom_box_on():
        zoom_box_state["on"] = True
        try:
            host.config(cursor="crosshair")
        except Exception:
            pass

    def _zoom_box_off():
        zoom_box_state["on"] = False
        try:
            host.config(cursor="")
        except Exception:
            pass
        if zoom_box_state["rect"] is not None:
            try:
                map_widget.canvas.delete(zoom_box_state["rect"])
            except Exception:
                pass
            zoom_box_state["rect"] = None

    def _map_tool_or_draw_blocks_pan() -> bool:
        """True — не рухати карту (інструменти карти або режим малювання з панелі D/SM/…)."""
        if zoom_box_state["on"]:
            return True
        if view_state.get("pz_drag"):
            return True
        if view_state.get("active_tool") in _MAP_TOOLS_POLYLINE | _MAP_TOOLS_TRUNK_POINT:
            return True
        if view_state.get("active_tool") == "project_zone_rect":
            return True
        if app is None:
            return False
        m = app.mode.get()
        # Режим PAN: перетягування тайлів (tkintermapview mouse_move), пасивні інструменти не блокують.
        if m == "PAN":
            return False
        if view_state.get("active_tool") in _MAP_TOOLS_PASSIVE:
            return True
        return m not in ("VIEW", "PAN")

    def _b1_press_chain(event):
        if zoom_box_state["on"]:
            zoom_box_state["x0"] = int(event.x)
            zoom_box_state["y0"] = int(event.y)
            if zoom_box_state["rect"] is not None:
                try:
                    map_widget.canvas.delete(zoom_box_state["rect"])
                except Exception:
                    pass
            zoom_box_state["rect"] = map_widget.canvas.create_rectangle(
                event.x,
                event.y,
                event.x,
                event.y,
                outline=ZOOM_BOX_OUTLINE,
                width=ZOOM_BOX_WIDTH,
                dash=ZOOM_BOX_DASH,
            )
            return "break"
        if view_state.get("active_tool") == "project_zone_rect":
            view_state["pz_drag"] = {
                "x0": int(event.x),
                "y0": int(event.y),
                "rect": map_widget.canvas.create_rectangle(
                    event.x,
                    event.y,
                    event.x,
                    event.y,
                    outline="#FF55AA",
                    width=2,
                    dash=(5, 3),
                ),
            }
            return "break"
        tool = view_state.get("active_tool")
        # У PAN пасивні інструменти не забирають ЛКМ — інакше зум (колесо) працює, а drag по тайлах ні.
        if tool in _MAP_TOOLS_PASSIVE and not (app is not None and app.mode.get() == "PAN"):
            if app is None or not getattr(app, "geo_ref", None):
                silent_showinfo(_map_silent_parent(), 
                    "Інфо",
                    "На карті для підбору об'єктів потрібна геоприв’язка (geo_ref).\n"
                    "Відкрийте вкладку «Без карти» — там працюють інструменти «Вибір» та «Інфо» в локальних координатах.",
                )
                return "break"
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(
                    float(lat), float(lon), float(ref_lon), float(ref_lat)
                )
            except Exception:
                return "break"
            if (
                tool == "select"
                and app is not None
                and app.mode.get() in ("VIEW", "RULER")
                and hasattr(app, "_nearest_trunk_node_index_world")
            ):
                try:
                    ni, _dist = app._nearest_trunk_node_index_world(float(wx), float(wy))
                except Exception:
                    ni = None
                if ni is not None:
                    if getattr(app, "mode", None) is not None and app.mode.get() == "RULER":
                        if hasattr(app, "_exit_ruler_for_trunk_interaction"):
                            app._exit_ruler_for_trunk_interaction()
                    app._trunk_node_drag_idx = int(ni)
                    app._trunk_node_drag_moved = False
                    if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                        app._schedule_embedded_map_overlay_refresh()
                    _paint_map_live_preview()
                    return "break"
            if not view_state.get("map_pick_regions"):
                _show_project_overlay(False)
            if tool == "select" and hasattr(app, "_collect_world_pick_hits"):
                ctrl = bool(int(getattr(event, "state", 0)) & 0x0004)
                hits = app._collect_world_pick_hits(float(wx), float(wy))
                if hits:
                    _pri, _d, cat, payload, label = hits[0]
                    prev = list(getattr(app, "_canvas_selection_keys", []) or [])
                    key = (cat, payload)
                    if ctrl:
                        idx = next((i for i, e in enumerate(prev) if (e[0], e[1]) == key), None)
                        if idx is not None:
                            prev.pop(idx)
                        else:
                            prev.append((cat, payload, label))
                        app._canvas_selection_keys = prev
                    else:
                        app._canvas_selection_keys = [(cat, payload, label)]
                else:
                    if not ctrl:
                        app._canvas_selection_keys = []
                app.redraw()
                if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                    app._schedule_embedded_map_overlay_refresh()
                else:
                    _show_project_overlay(False)
            else:
                label = _pick_map_object_at_world(wx, wy)
                title = "Вибір" if tool == "select" else "Інфо"
                if label:
                    silent_showinfo(_map_silent_parent(), title, label)
                else:
                    silent_showinfo(_map_silent_parent(), 
                        title,
                        "Об'єкт не знайдено. Увімкніть шари «Блоки» / «Сабмейни», оновіть накладення або наблизьте карту.",
                    )
            return "break"
        if tool == "trunk_route" and app is not None and len(getattr(app, "trunk_map_nodes", []) or []) > 0:
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                if not getattr(app, "geo_ref", None):
                    silent_showinfo(
                        _map_silent_parent(),
                        "Магістраль",
                        "Потрібна геоприв’язка проєкту (geo_ref), щоб трасувати магістраль на карті.",
                    )
                    return "break"
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(
                    float(lat), float(lon), float(ref_lon), float(ref_lat)
                )
            except Exception:
                return "break"
            app._canvas_trunk_route_left_click(float(wx), float(wy))
            _rebuild_trunk_route_draft_visual()
            _paint_map_draft_rubber(event)
            return "break"
        if tool in _MAP_TOOLS_TRUNK_POINT:
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                view_state["draft_points"] = [(float(lat), float(lon))]
                if app is not None:
                    _ensure_geo_ref_from_points([(lat, lon)])
                    ref_lon, ref_lat = app.geo_ref
                    wx, wy = srtm_tiles.lat_lon_to_local_xy(
                        float(lat), float(lon), float(ref_lon), float(ref_lat)
                    )
                    if hasattr(app, "place_trunk_point_tool_world_xy"):
                        app.place_trunk_point_tool_world_xy(tool, wx, wy)
                    if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                        app._schedule_embedded_map_overlay_refresh()
                    else:
                        _show_project_overlay(False)
                _paint_map_draft_rubber(event)
            except Exception:
                pass
            return "break"
        if tool in _MAP_TOOLS_POLYLINE:
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                view_state["draft_points"].append((lat, lon))
                if tool == "capture_tiles":
                    _draw_draft("#00D1FF", 3, close_ring=True)
                elif tool == "block_contour":
                    _draw_draft("#FFD400", 3, close_ring=True)
                elif tool == "scene_lines":
                    _draw_draft("#B8C0CC", 2, close_ring=False)
                else:
                    _draw_draft(TRUNK_PATH_COLOR, 3, close_ring=False)
                _paint_map_draft_rubber(event)
            except Exception:
                pass
            return "break"
        if app is not None and _map_tool_or_draw_blocks_pan():
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                # Якщо ще немає geo_ref — беремо перший клік як початок локальної СК (як після «Контур блоку» / KML).
                if not getattr(app, "geo_ref", None):
                    app.geo_ref = (float(lon), float(lat))
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
                app.feed_map_pointer_world(wx, wy)
                app._handle_left_click_world(wx, wy)
                if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                    app._schedule_embedded_map_overlay_refresh()
                _paint_map_live_preview()
            except Exception:
                pass
            return "break"
        if (
            app is not None
            and getattr(app, "geo_ref", None)
            and app.mode.get() == "VIEW"
            and tool is None
            and getattr(app, "_canvas_special_tool", None) is None
        ):
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
                app.feed_map_pointer_world(wx, wy)
                app._handle_left_click_world(float(wx), float(wy), scr_x=int(event.x), scr_y=int(event.y))
                if getattr(app, "_trunk_node_drag_idx", None) is not None:
                    if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                        app._schedule_embedded_map_overlay_refresh()
                    _paint_map_live_preview()
                    return "break"
            except Exception:
                pass
        return map_widget.mouse_click(event)

    def _map_canvas_motion(event):
        if zoom_box_state["on"] or view_state.get("pz_drag"):
            return
        tool = view_state.get("active_tool")
        if tool in _MAP_TOOLS_POLYLINE or tool in _MAP_TOOLS_TRUNK_POINT:
            _paint_map_draft_rubber(event)
        _schedule_cached_tiles_overlay_refresh()
        if app is not None and getattr(app, "geo_ref", None):
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(
                    float(lat), float(lon), float(ref_lon), float(ref_lat)
                )
                if view_state.get("active_tool") in _MAP_TOOLS_PASSIVE:
                    app.feed_map_pointer_world(wx, wy, redraw_canvas=False)
                    _paint_map_live_preview()
                    return
            except Exception:
                pass
        if app is None or not getattr(app, "geo_ref", None):
            return
        try:
            m_nav = app.mode.get()
            tool_nav = view_state.get("active_tool")
            if (
                m_nav in ("VIEW", "PAN")
                and isinstance(getattr(app, "trunk_irrigation_hydro_cache", None), dict)
                and tool_nav not in _MAP_TOOLS_POLYLINE
                and tool_nav not in _MAP_TOOLS_TRUNK_POINT
                and tool_nav != "project_zone_rect"
            ):
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(
                    float(lat), float(lon), float(ref_lon), float(ref_lat)
                )
                app.feed_map_pointer_world(wx, wy, redraw_canvas=False)
                _paint_map_live_preview()
        except Exception:
            pass
        if not _map_tool_or_draw_blocks_pan():
            return
        m = app.mode.get()
        if m not in ("SUBMAIN", "RULER", "DRAW", "DRAW_LAT", "CUT_LATS", "DEL"):
            return
        try:
            lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
            ref_lon, ref_lat = app.geo_ref
            wx, wy = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
            app.feed_map_pointer_world(wx, wy, redraw_canvas=False)
            _paint_map_live_preview()
        except Exception:
            pass

    def _drag(event):
        pd = view_state.get("pz_drag")
        if pd and pd.get("rect") is not None:
            map_widget.canvas.coords(pd["rect"], pd["x0"], pd["y0"], event.x, event.y)
            return "break"
        if not zoom_box_state["on"] or zoom_box_state["rect"] is None:
            return None
        map_widget.canvas.coords(
            zoom_box_state["rect"],
            zoom_box_state["x0"],
            zoom_box_state["y0"],
            event.x,
            event.y,
        )
        return "break"

    def _release(event):
        if app is not None and getattr(app, "_trunk_node_drag_idx", None) is not None:
            app._finalize_trunk_node_drag()
            return "break"
        pd = view_state.get("pz_drag")
        if pd:
            view_state["pz_drag"] = None
            try:
                map_widget.canvas.delete(pd["rect"])
            except Exception:
                pass
            x0, y0 = pd["x0"], pd["y0"]
            x1, y1 = int(event.x), int(event.y)
            if abs(x1 - x0) >= 8 and abs(y1 - y0) >= 8 and app is not None:
                try:
                    lat_list = []
                    lon_list = []
                    for cx, cy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                        la, lo = map_widget.convert_canvas_coords_to_decimal_coords(cx, cy)
                        lat_list.append(la)
                        lon_list.append(lo)
                    if hasattr(app, "set_project_zone_wgs84_bbox"):
                        app.set_project_zone_wgs84_bbox(
                            min(lat_list), max(lat_list), min(lon_list), max(lon_list)
                        )
                    _draw_project_zone_on_map()
                    if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                        app._schedule_embedded_map_overlay_refresh()
                except Exception:
                    pass
            _set_tool(None)
            return "break"
        if not zoom_box_state["on"]:
            return None
        x0, y0 = zoom_box_state["x0"], zoom_box_state["y0"]
        x1, y1 = int(event.x), int(event.y)
        if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
            _zoom_box_off()
            return "break"
        try:
            lat1, lon1 = map_widget.convert_canvas_coords_to_decimal_coords(x0, y0)
            lat2, lon2 = map_widget.convert_canvas_coords_to_decimal_coords(x1, y1)
            _fit_bounds(min(lat1, lat2), min(lon1, lon2), max(lat1, lat2), max(lon1, lon2))
        except Exception:
            pass
        _zoom_box_off()
        return "break"

    _orig_mouse_move = map_widget.mouse_move
    _orig_mouse_right_click = map_widget.mouse_right_click
    _trunk_glyph_refresh_job = {"id": None}

    def _schedule_trunk_glyph_refresh(delay_ms: int = 16) -> None:
        """Throttled refresh, щоб гліфи не відставали при drag-пані."""
        if app is None:
            return
        if not getattr(app, "geo_ref", None):
            return
        try:
            old = _trunk_glyph_refresh_job.get("id")
            if old is not None:
                map_widget.canvas.after_cancel(old)
        except Exception:
            pass

        def _run():
            _trunk_glyph_refresh_job["id"] = None
            _refresh_trunk_map_glyphs()

        try:
            _trunk_glyph_refresh_job["id"] = map_widget.canvas.after(max(1, int(delay_ms)), _run)
        except Exception:
            _refresh_trunk_map_glyphs()

    def _b1_motion_chain(event):
        if view_state.get("pz_drag"):
            return _drag(event)
        if zoom_box_state["on"]:
            return _drag(event)
        if (
            app is not None
            and getattr(app, "_trunk_node_drag_idx", None) is not None
            and app.mode.get() in ("VIEW", "RULER")
            and getattr(app, "geo_ref", None)
        ):
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                ref_lon, ref_lat = app.geo_ref
                wx, wy = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
                app._trunk_node_drag_apply_world(int(app._trunk_node_drag_idx), float(wx), float(wy))
                app._trunk_node_drag_moved = True
                _schedule_trunk_glyph_refresh(0)
            except Exception:
                pass
            return "break"
        if (
            view_state.get("active_tool") == "trunk_route"
            and app is not None
            and len(getattr(app, "trunk_map_nodes", []) or []) > 0
            and list(getattr(app, "_canvas_trunk_route_draft_indices", []) or [])
        ):
            _paint_map_draft_rubber(event)
        if _map_tool_or_draw_blocks_pan():
            return
        out = _orig_mouse_move(event)
        _schedule_trunk_glyph_refresh()
        return out

    def _canvas_right_button(event):
        if zoom_box_state["on"]:
            _zoom_box_off()
            return "break"
        if view_state.get("pz_drag"):
            pd = view_state["pz_drag"]
            view_state["pz_drag"] = None
            try:
                map_widget.canvas.delete(pd["rect"])
            except Exception:
                pass
            return "break"
        if view_state.get("active_tool") in _MAP_TOOLS_PASSIVE:
            if (
                app is not None
                and getattr(app, "geo_ref", None)
                and app.mode.get() in ("VIEW", "PAN")
                and hasattr(app, "_trunk_interaction_priority_active")
                and app._trunk_interaction_priority_active()
                and hasattr(app, "_open_trunk_graph_context_menu")
            ):
                try:
                    lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                    ref_lon, ref_lat = app.geo_ref
                    wx, wy = srtm_tiles.lat_lon_to_local_xy(
                        float(lat), float(lon), float(ref_lon), float(ref_lat)
                    )
                    anchor = (int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0)))
                    if app._open_trunk_graph_context_menu(float(wx), float(wy), menu_anchor=anchor):
                        return "break"
                except Exception:
                    pass
            _set_tool(None)
            return "break"
        if view_state.get("active_tool") in _MAP_TOOLS_TRUNK_POINT:
            _set_tool(None)
            return "break"
        if view_state.get("active_tool") in _MAP_TOOLS_POLYLINE:
            _finish_shape(event)
            return "break"
        if app is not None and getattr(app, "geo_ref", None):
            m = app.mode.get()
            if m in ("VIEW", "PAN"):
                if hasattr(app, "_irrigation_schedule_canvas_pick_active") and app._irrigation_schedule_canvas_pick_active():
                    try:
                        lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                        ref_lon, ref_lat = app.geo_ref
                        wx, wy = srtm_tiles.lat_lon_to_local_xy(
                            float(lat), float(lon), float(ref_lon), float(ref_lat)
                        )
                        app._handle_right_click_world(float(wx), float(wy))
                        if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                            app._schedule_embedded_map_overlay_refresh()
                        _paint_map_live_preview()
                    except Exception:
                        pass
                    return "break"
                if (
                    hasattr(app, "_trunk_interaction_priority_active")
                    and app._trunk_interaction_priority_active()
                    and hasattr(app, "_open_trunk_graph_context_menu")
                ):
                    try:
                        lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                        ref_lon, ref_lat = app.geo_ref
                        wx, wy = srtm_tiles.lat_lon_to_local_xy(
                            float(lat), float(lon), float(ref_lon), float(ref_lat)
                        )
                        anchor = (int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0)))
                        if app._open_trunk_graph_context_menu(float(wx), float(wy), menu_anchor=anchor):
                            if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                                app._schedule_embedded_map_overlay_refresh()
                            _paint_map_live_preview()
                            return "break"
                    except Exception:
                        pass
            if m in ("DRAW", "SUBMAIN", "DRAW_LAT", "RULER", "CUT_LATS", "TOPO"):
                try:
                    lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                    ref_lon, ref_lat = app.geo_ref
                    wx, wy = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
                    app._handle_right_click_world(wx, wy)
                    if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
                        app._schedule_embedded_map_overlay_refresh()
                    _paint_map_live_preview()
                except Exception:
                    pass
                return "break"
        return _orig_mouse_right_click(event)

    try:
        map_widget.canvas.unbind("<B1-Motion>")
    except Exception:
        pass
    map_widget.canvas.bind("<B1-Motion>", _b1_motion_chain)
    try:
        map_widget.canvas.unbind("<Button-1>")
    except Exception:
        pass
    map_widget.canvas.bind("<Button-1>", _b1_press_chain)
    try:
        if sys.platform == "darwin":
            map_widget.canvas.unbind("<Button-2>")
            map_widget.canvas.bind("<Button-2>", _canvas_right_button)
        else:
            map_widget.canvas.unbind("<Button-3>")
            map_widget.canvas.bind("<Button-3>", _canvas_right_button)
    except Exception:
        pass

    map_widget.canvas.bind("<ButtonRelease-1>", _release, add="+")

    def _map_double_b1(event):
        if app is None or not getattr(app, "geo_ref", None):
            return
        tool = view_state.get("active_tool")
        if tool in _MAP_TOOLS_POLYLINE or tool in _MAP_TOOLS_TRUNK_POINT or tool == "project_zone_rect":
            return
        try:
            lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
            ref_lon, ref_lat = app.geo_ref
            wx, wy = srtm_tiles.lat_lon_to_local_xy(float(lat), float(lon), float(ref_lon), float(ref_lat))
            if hasattr(app, "handle_trunk_segment_double_click_world"):
                app.handle_trunk_segment_double_click_world(wx, wy)
        except Exception:
            pass

    map_widget.canvas.bind("<Double-Button-1>", _map_double_b1, add="+")
    map_widget.canvas.bind("<Motion>", _map_canvas_motion, add="+")
    map_widget.canvas.bind(
        "<MouseWheel>",
        lambda _e: (_schedule_cached_tiles_overlay_refresh(), _schedule_trunk_glyph_refresh()),
        add="+",
    )
    map_widget.canvas.bind(
        "<Button-4>",
        lambda _e: (_schedule_cached_tiles_overlay_refresh(), _schedule_trunk_glyph_refresh()),
        add="+",
    )
    map_widget.canvas.bind(
        "<Button-5>",
        lambda _e: (_schedule_cached_tiles_overlay_refresh(), _schedule_trunk_glyph_refresh()),
        add="+",
    )

    _basemap_choice_labels = [p[0] for p in MAP_BASEMAP_PRESETS]
    basemap_var = tk.StringVar(value=_basemap_choice_labels[0])

    def _apply_embedded_basemap(_evt=None) -> None:
        lab = basemap_var.get()
        try:
            _emb_lbl_osm_attr.pack_forget()
        except Exception:
            pass
        for name, url, mz in MAP_BASEMAP_PRESETS:
            if name == lab:
                try:
                    map_widget.set_tile_server(url, max_zoom=int(mz))
                except Exception:
                    pass
                try:
                    _schedule_cached_tiles_overlay_refresh()
                except Exception:
                    pass
                if name.startswith("OSM"):
                    try:
                        _emb_lbl_osm_attr.config(
                            text="© OpenStreetMap contributors",
                            font=("Segoe UI", 7),
                        )
                        _emb_lbl_osm_attr.pack(side=tk.LEFT, padx=(0, 6), pady=4)
                    except Exception:
                        pass
                return

    tk.Label(
        top_bar,
        text="Географічна основа:",
        bg="#1e1e1e",
        fg="#d0d0d0",
        font=("Segoe UI", 9, "bold"),
    ).pack(side=tk.LEFT, padx=(8, 4), pady=4)
    _emb_lbl_osm_attr = tk.Label(
        top_bar,
        text="© OpenStreetMap contributors",
        bg="#1e1e1e",
        fg="#757575",
        font=("Segoe UI", 7),
    )
    _emb_cb_basemap = ttk.Combobox(
        top_bar,
        textvariable=basemap_var,
        values=_basemap_choice_labels,
        state="readonly",
        width=34,
        font=("Segoe UI", 9),
    )
    _emb_cb_basemap.pack(side=tk.LEFT, padx=(0, 10), pady=2)
    _emb_cb_basemap.bind("<<ComboboxSelected>>", _apply_embedded_basemap)
    _attach_dark_tooltip(
        _emb_cb_basemap,
        "Підложка карти: супутник, рельєф Esri, світла OSM, Copernicus Sentinel-2 cloudless. "
        "Тло віджета між тайлами — темне (MAP_BG_DARK). "
        "Copernicus / Sentinel-2 cloudless — глобальна мозаїка Sentinel-2 (програма Copernicus), без хмар; сервіс EOX. "
        "Ліцензія та атрибуція: https://s2maps.eu/ . "
        f"У додатку дозволено зум до {COPERNICUS_S2_CLOUDLESS_MAX_ZOOM} (перевірено HEAD по тайлах EOX; вище — 404).",
    )
    _emb_btn_kml = tk.Button(
        top_bar,
        text="📂 Відкрити .kml",
        command=_load_kml,
        bg="#2a2a2a",
        fg="#e8e8e8",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _emb_btn_kml.pack(side=tk.LEFT, padx=4, pady=4)
    _attach_dark_tooltip(_emb_btn_kml, "Відкрити KML і показати геометрію на карті.")
    _emb_btn_zoom_box = tk.Button(
        top_bar,
        text="🔲 Зум рамкою",
        command=_zoom_box_on,
        bg="#2a2a2a",
        fg="#e8e8e8",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _emb_btn_zoom_box.pack(side=tk.LEFT, padx=4, pady=4)
    _attach_dark_tooltip(_emb_btn_zoom_box, "Обрати прямокутник на карті — масштаб підігнати під нього.")
    _emb_btn_proj = tk.Button(
        top_bar,
        text="🔄 Показати проєкт",
        command=lambda: _show_project_overlay(True),
        bg="#2a2a2a",
        fg="#e8e8e8",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _emb_btn_proj.pack(side=tk.LEFT, padx=4, pady=4)
    _attach_dark_tooltip(
        _emb_btn_proj,
        "Оновити накладення контуру проєкту (блоки/сабмейни/латералі залежно від прапорців нижче).",
    )

    _nb_style = ttk.Style(left_toolbar.winfo_toplevel())
    try:
        _nb_style.theme_use("clam")
    except tk.TclError:
        pass
    _nb_style.configure("MapLeft.TNotebook", background="#181818", borderwidth=0)
    _nb_style.configure(
        "MapLeft.TNotebook.Tab",
        background="#333333",
        foreground="#e8e8e8",
        font=("Segoe UI", 8, "bold"),
        padding=[5, 2],
    )
    _nb_style.map("MapLeft.TNotebook.Tab", background=[("selected", "#0066FF")])

    # Зверху — інструментальна панель (зона/тайли/шари); знизу — панель малювання (Малювання / Магістраль).
    left_paned = tk.PanedWindow(
        left_toolbar,
        orient=tk.VERTICAL,
        bd=0,
        sashwidth=6,
        bg="#2a2a2a",
        sashrelief=tk.FLAT,
        sashpad=1,
    )
    left_paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=4)

    instrumental_top = tk.Frame(left_paned, bg="#181818")
    draw_panel = tk.Frame(left_paned, bg="#181818", highlightthickness=1, highlightbackground="#3d3d3d")
    left_paned.add(instrumental_top, minsize=140)
    left_paned.add(draw_panel, minsize=160)

    def _init_map_left_sash() -> None:
        try:
            left_paned.update_idletasks()
            h = max(240, int(left_paned.winfo_height()))
            # Межа між інструментальною зоною (зверху) і панеллю малювання: ~58% висоти лівої колонки.
            left_paned.sash_place(0, 0, min(h - 150, int(h * 0.58)))
        except Exception:
            pass

    host.after(150, _init_map_left_sash)

    tk.Label(
        draw_panel,
        text="Панель малювання",
        bg="#181818",
        fg="#88CCFF",
        font=("Segoe UI", 8, "bold"),
        anchor="w",
    ).pack(fill=tk.X, padx=6, pady=(6, 2))
    draw_nb = ttk.Notebook(draw_panel, style="MapLeft.TNotebook")
    draw_nb.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 4))

    tab_draw = tk.Frame(draw_nb, bg="#181818")
    tab_trunk = tk.Frame(draw_nb, bg="#181818")
    draw_nb.add(tab_draw, text="Малювання")
    draw_nb.add(tab_trunk, text="Магістраль")
    if app is not None and hasattr(app, "_set_trunk_panel_active_map"):
        def _sync_map_trunk_tab_state(_event=None) -> None:
            try:
                tab_id = str(draw_nb.select())
                tab_txt = str(draw_nb.tab(tab_id, "text")).strip().lower()
                app._set_trunk_panel_active_map(tab_txt == "магістраль")
            except Exception:
                app._set_trunk_panel_active_map(False)
        draw_nb.bind("<<NotebookTabChanged>>", _sync_map_trunk_tab_state, add="+")
        _sync_map_trunk_tab_state()

    # --- Інструментальна панель: зона / тайли / шари та overlay ---
    tk.Label(
        instrumental_top,
        text="Інструментальна панель",
        bg="#181818",
        fg="#A8E6FF",
        font=("Segoe UI", 9, "bold"),
    ).pack(fill=tk.X, padx=8, pady=(8, 2))
    tk.Label(
        instrumental_top,
        text="Шари проєкту — у цьому блоці; зона/тайли/висоти керуються на вкладці «Рельєф» праворуч.",
        bg="#181818",
        fg="#666666",
        font=("Segoe UI", 8),
        wraplength=178,
        justify=tk.LEFT,
        anchor="nw",
    ).pack(fill=tk.X, padx=8, pady=(0, 6))
    tk.Label(instrumental_top, text="Шари проєкту", bg="#181818", fg="#8BC4FF", font=("Segoe UI", 8, "bold")).pack(
        fill=tk.X, padx=8, pady=(4, 4)
    )
    if app is not None:
        app._map_prepare_zone_button = None
    tk.Label(instrumental_top, text="Шари overlay", bg="#181818", fg="#8BC4FF", font=("Segoe UI", 9, "bold")).pack(
        fill=tk.X, padx=10, pady=(10, 4)
    )
    tk.Checkbutton(
        instrumental_top,
        text="Блоки",
        variable=show_blocks_var,
        command=lambda: _show_project_overlay(False),
        bg="#181818",
        fg="white",
        selectcolor="#303030",
        activebackground="#181818",
        activeforeground="white",
    ).pack(fill=tk.X, padx=10)
    tk.Checkbutton(
        instrumental_top,
        text="Сабмейни",
        variable=show_submains_var,
        command=lambda: _show_project_overlay(False),
        bg="#181818",
        fg="white",
        selectcolor="#303030",
        activebackground="#181818",
        activeforeground="white",
    ).pack(fill=tk.X, padx=10)
    tk.Checkbutton(
        instrumental_top,
        text="Латералі",
        variable=show_laterals_var,
        command=lambda: _show_project_overlay(False),
        bg="#181818",
        fg="white",
        selectcolor="#303030",
        activebackground="#181818",
        activeforeground="white",
    ).pack(fill=tk.X, padx=10)
    _fr_cache_chk = tk.Frame(instrumental_top, bg="#181818")
    _fr_cache_chk.pack(fill=tk.X, padx=4, pady=0)
    tk.Checkbutton(
        _fr_cache_chk,
        text="Межі кешу",
        variable=show_cached_tiles_var,
        command=_show_cached_tiles_overlay,
        bg="#181818",
        fg="white",
        selectcolor="#303030",
        activebackground="#181818",
        activeforeground="white",
        wraplength=118,
        justify=tk.LEFT,
        anchor="w",
    ).pack(fill=tk.X, anchor="w")
    tk.Label(instrumental_top, textvariable=hint, justify=tk.LEFT, wraplength=170, bg="#181818", fg="#B0B0B0", anchor="nw").pack(
        fill=tk.X, padx=10, pady=(10, 6)
    )
    tk.Label(instrumental_top, textvariable=dl_status, justify=tk.LEFT, wraplength=170, bg="#181818", fg="#8FCF9B", anchor="nw").pack(
        fill=tk.X, padx=10, pady=(0, 8)
    )

    build_trunk_tools_tab(tab_trunk, _set_tool, _attach_dark_tooltip, on_map_tab=True, app=app)

    def _zoom_extents_project():
        _show_project_overlay(True)

    def _focus_trunk_node_by_id(node_id: str, zoom_min: int = 15) -> bool:
        if app is None:
            return False
        nid = str(node_id or "").strip()
        if not nid:
            return False
        for node in list(getattr(app, "trunk_map_nodes", []) or []):
            if str(node.get("id", "")).strip() != nid:
                continue
            try:
                lat = float(node.get("lat"))
                lon = float(node.get("lon"))
            except (TypeError, ValueError):
                return False
            try:
                map_widget.set_position(lat, lon)
                z_now = int(map_widget.get_zoom())
            except Exception:
                return False
            try:
                if z_now < int(zoom_min):
                    map_widget.set_zoom(int(zoom_min))
            except Exception:
                pass
            try:
                _show_project_overlay(False)
            except Exception:
                pass
            return True
        return False

    build_draw_modes_tab(tab_draw, app, _attach_dark_tooltip)

    try:
        scale_overlay.lift()
    except Exception:
        pass
    _draw_scale_bar_100m()
    host._refresh_project_overlay = _show_project_overlay
    host._set_map_tool = _set_tool
    try:
        _set_tool(None)
    except Exception:
        view_state["active_tool"] = None
    host._map_hint_var = hint
    host._zoom_box_on = _zoom_box_on
    host._zoom_extents_project = _zoom_extents_project
    host._focus_trunk_node_by_id = _focus_trunk_node_by_id

    def _init_geo_ref_from_map_center() -> None:
        """Відкрита карта задає геоприв’язку: початок локальної СК — центр поточного виду."""
        if app is None:
            return
        try:
            if not getattr(app, "geo_ref", None):
                lat_c, lon_c = map_widget.get_position()
                app.geo_ref = (float(lon_c), float(lat_c))
            _show_project_overlay(True)
            _show_cached_tiles_overlay()
            _draw_project_zone_on_map()
        except Exception:
            pass

    def _run_init_geo_wrap() -> None:
        host._init_geo_after_id = None
        _init_geo_ref_from_map_center()

    host._init_geo_after_id = host.after(200, _run_init_geo_wrap)

    def _suspend_background_jobs() -> None:
        host._map_bg_suspended = True
        jid = getattr(host, "_scale_bar_after_id", None)
        if jid is not None:
            try:
                host.after_cancel(jid)
            except Exception:
                pass
            host._scale_bar_after_id = None
        jid2 = getattr(host, "_init_geo_after_id", None)
        if jid2 is not None:
            try:
                host.after_cancel(jid2)
            except Exception:
                pass
            host._init_geo_after_id = None
        ctj = view_state.get("cached_tile_refresh_job")
        if ctj is not None:
            try:
                host.after_cancel(ctj)
            except Exception:
                pass
            view_state["cached_tile_refresh_job"] = None
        tg = _trunk_glyph_refresh_job.get("id")
        if tg is not None:
            try:
                map_widget.canvas.after_cancel(tg)
            except Exception:
                pass
            _trunk_glyph_refresh_job["id"] = None

    def _resume_background_jobs() -> None:
        if not getattr(host, "_map_bg_suspended", False):
            return
        host._map_bg_suspended = False
        _draw_scale_bar_100m()

    host._suspend_background_jobs = _suspend_background_jobs
    host._resume_background_jobs = _resume_background_jobs

    return host


def main() -> None:
    if TkinterMapView is None:
        root = tk.Tk()
        root.withdraw()
        silent_showerror(root, 
            "Мапа",
            "Не вдалося імпортувати tkintermapview.\n"
            "Встановіть: py -m pip install tkintermapview\n\n"
            f"Деталі: {_IMPORT_ERR}",
        )
        root.destroy()
        return

    root = tk.Tk()
    root.title("DripCAD - Мапа (навігація)")
    root.geometry("1200x800")
    root.minsize(900, 600)
    root.configure(bg="#1e1e1e")
    app = None  # автономне вікно карти без екземпляра DripCAD

    def _standalone_save_trunk_graph() -> None:
        silent_showinfo(root, 
            "Магістраль",
            "Зберегти граф магістралі можна у головному вікні DripCAD: зліва вкладка «Магістраль» або меню "
            "«Інструменти» → «Зберегти граф магістралі».",
        )

    top_bar = tk.Frame(root, bg="#1e1e1e", height=34)
    top_bar.pack(side=tk.TOP, fill=tk.X)

    map_area = tk.Frame(root, bg="#1e1e1e")
    map_area.pack(fill="both", expand=True)

    left_toolbar = tk.Frame(map_area, bg="#181818", width=170)
    left_toolbar.pack(side=tk.LEFT, fill=tk.Y)
    left_toolbar.pack_propagate(False)

    map_widget = TkinterMapView(map_area, corner_radius=0)
    map_widget.pack(side=tk.LEFT, fill="both", expand=True)
    # Reduce bright white flash while tiles are reloaded.
    try:
        map_widget.configure(bg=MAP_BG_DARK)
    except Exception:
        pass
    try:
        map_widget.canvas.configure(bg=MAP_BG_DARK, highlightthickness=0)
    except Exception:
        pass

    # Start in satellite mode (Google Earth-like).
    map_widget.set_tile_server(SAT_TILE_URL, max_zoom=19)
    map_widget.set_zoom(12)
    view_state = {
        "last_bounds": None,
        "kml_paths": [],
        "active_tool": None,  # None | capture_tiles | block_contour | trunk_route
        "draft_points": [],
        "draft_path": None,
        "capture_path": None,
        "capture_points": [],
        "block_path": None,
        "trunk_path": None,
    }

    tool_hint_var = tk.StringVar(value="Інструмент: навігація")
    download_status_var = tk.StringVar(value="SRTM: очікування")

    _standalone_osm_lbl = tk.Label(
        top_bar,
        text="© OpenStreetMap contributors",
        bg="#1e1e1e",
        fg="#757575",
        font=("Segoe UI", 7),
    )

    def _standalone_hide_osm_label() -> None:
        try:
            _standalone_osm_lbl.pack_forget()
        except Exception:
            pass

    def _set_light() -> None:
        try:
            _standalone_osm_lbl.config(text="© OpenStreetMap contributors", font=("Segoe UI", 7))
            _standalone_osm_lbl.pack(side=tk.LEFT, padx=(6, 2), pady=4)
        except Exception:
            pass
        map_widget.set_tile_server(LIGHT_TILE_URL, max_zoom=19)

    def _set_satellite() -> None:
        _standalone_hide_osm_label()
        map_widget.set_tile_server(SAT_TILE_URL, max_zoom=19)

    def _set_copernicus() -> None:
        _standalone_hide_osm_label()
        map_widget.set_tile_server(
            COPERNICUS_S2_CLOUDLESS_TILE_URL, max_zoom=COPERNICUS_S2_CLOUDLESS_MAX_ZOOM
        )

    def _set_terrain() -> None:
        _standalone_hide_osm_label()
        map_widget.set_tile_server(TERRAIN_TILE_URL, max_zoom=19)

    def _safe_delete_path(path_obj) -> None:
        if not path_obj:
            return
        try:
            if hasattr(map_widget, "delete"):
                map_widget.delete(path_obj)
        except Exception:
            pass

    def _set_tool(name: str | None) -> None:
        view_state["active_tool"] = name
        view_state["draft_points"] = []
        _safe_delete_path(view_state.get("draft_path"))
        view_state["draft_path"] = None
        if name == "capture_tiles":
            tool_hint_var.set("Інструмент: контур захвату тайлів (ЛКМ вершини, ПКМ завершити)")
        elif name == "block_contour":
            tool_hint_var.set("Інструмент: контур блоку (ЛКМ вершини, ПКМ завершити)")
        elif name == "trunk_route":
            tool_hint_var.set("Інструмент: траса магістралі (ЛКМ вершини, ПКМ завершити)")
        else:
            tool_hint_var.set("Інструмент: навігація")
        try:
            root.config(cursor="crosshair" if name else "")
        except Exception:
            pass

    def _draw_draft_path(color: str, width: int, close_ring: bool = False) -> None:
        _safe_delete_path(view_state.get("draft_path"))
        pts = list(view_state.get("draft_points") or [])
        if len(pts) < 2:
            view_state["draft_path"] = None
            return
        draw_pts = pts + [pts[0]] if close_ring and len(pts) >= 3 else pts
        try:
            view_state["draft_path"] = map_widget.set_path(draw_pts, color=color, width=width)
        except Exception:
            view_state["draft_path"] = None

    def _finalize_tool_shape() -> None:
        tool = view_state.get("active_tool")
        pts = list(view_state.get("draft_points") or [])
        if tool == "capture_tiles":
            if len(pts) < 3:
                _set_tool(None)
                return
            view_state["capture_points"] = list(pts)
            _safe_delete_path(view_state.get("capture_path"))
            try:
                view_state["capture_path"] = map_widget.set_path(pts + [pts[0]], color="#00D1FF", width=4)
            except Exception:
                pass
            lats = [p[0] for p in pts]
            lons = [p[1] for p in pts]
            _fit_bounds(min(lats), min(lons), max(lats), max(lons))
        elif tool == "block_contour":
            if len(pts) < 3:
                _set_tool(None)
                return
            _safe_delete_path(view_state.get("block_path"))
            try:
                view_state["block_path"] = map_widget.set_path(pts + [pts[0]], color="#FFD400", width=4)
            except Exception:
                pass
        elif tool == "trunk_route":
            if len(pts) < 2:
                _set_tool(None)
                return
            _safe_delete_path(view_state.get("trunk_path"))
            try:
                view_state["trunk_path"] = map_widget.set_path(pts, color=TRUNK_PATH_COLOR, width=4)
            except Exception:
                pass
        _set_tool(None)

    def _download_tiles_for_capture() -> None:
        pts = list(view_state.get("capture_points") or [])
        if len(pts) < 3:
            silent_showwarning(root, 
                "SRTM",
                "Спершу намалюйте контур захвату тайлів (мінімум 3 вершини) і завершіть ПКМ.",
            )
            return
        tile_src = srtm_tiles.resolve_tile_source_from_app(app)
        if tile_src == "open_elevation":
            silent_showwarning(
                root,
                "SRTM",
                "Open-Elevation не надає файли тайлів .hgt.\n"
                "У головному вікні оберіть «Skadi+локальні» або «NASA Earthdata» (earthaccess / LP DAAC або власний URL).",
            )
            return
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        lat_min, lat_max = min(lats), max(lats)
        lon_min, lon_max = min(lons), max(lons)
        tiles = srtm_tiles.iter_tiles_covering_bbox(lat_min, lat_max, lon_min, lon_max)
        if not tiles:
            silent_showinfo(root, "SRTM", "Немає тайлів для завантаження у вибраному контурі.")
            return
        src_tile_label = {"skadi": "Skadi (AWS)", "earthdata": "NASA Earthdata"}.get(tile_src, tile_src)
        if not silent_askyesno(
            root,
            "SRTM",
            f"Джерело тайлів: {src_tile_label}\n"
            f"Буде завантажено до {len(tiles)} тайлів у:\n{SRTM_DIR}\n\nПродовжити?",
        ):
            return

        download_status_var.set(f"SRTM: підготовка ({len(tiles)} тайлів)…")
        _btn_dl_tiles.config(state=tk.DISABLED, text="⏳ Завантаження…")

        def _task() -> None:
            srtm_tiles.ensure_srtm_dir()
            results = []
            ok_n = 0
            for i, (la, lo) in enumerate(tiles, start=1):
                ok, msg = srtm_tiles.download_tile(la, lo, tile_source=tile_src)
                if ok:
                    ok_n += 1
                results.append((srtm_tiles.tile_base_name(la, lo), msg))
                root.after(
                    0,
                    lambda i=i, total=len(tiles), ok_n=ok_n: download_status_var.set(
                        f"SRTM: {i}/{total}, успішно {ok_n}"
                    ),
                )
            root.after(0, lambda: _on_download_done(results, ok_n, len(tiles)))

        def _on_download_done(results, ok_n, total_n) -> None:
            _btn_dl_tiles.config(state=tk.NORMAL, text="⬇ Завантажити тайли SRTM")
            download_status_var.set(f"SRTM: завершено {ok_n}/{total_n}")
            lines = "\n".join(f"{n}: {m}" for n, m in results[:30])
            if len(results) > 30:
                lines += f"\n… ще {len(results) - 30} рядків"
            silent_showinfo(root, 
                "SRTM",
                f"Папка: {SRTM_DIR}\nУспішно: {ok_n}/{total_n}\n\n{lines}",
            )

        threading.Thread(target=_task, daemon=True).start()

    def _fit_bounds(lat_min: float, lon_min: float, lat_max: float, lon_max: float) -> None:
        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min
        if lon_min > lon_max:
            lon_min, lon_max = lon_max, lon_min
        view_state["last_bounds"] = (lat_min, lon_min, lat_max, lon_max)
        c_lat = (lat_min + lat_max) / 2.0
        c_lon = (lon_min + lon_max) / 2.0
        # Fallback zoom-extents approximation.
        lat_span = max(1e-6, abs(lat_max - lat_min))
        lon_span = max(1e-6, abs(lon_max - lon_min))
        deg_span = max(lat_span, lon_span)
        z = int(round(math.log2(360.0 / deg_span)))
        z = max(2, min(19, z - 1))
        # Apply multiple times (some widget builds ignore first update during tile refresh).
        def _apply_once() -> None:
            try:
                if hasattr(map_widget, "fit_bounding_box"):
                    # tkintermapview expects (top_left_lat, top_left_lon), (bottom_right_lat, bottom_right_lon)
                    map_widget.fit_bounding_box(
                        (lat_max, lon_min),
                        (lat_min, lon_max),
                    )
                map_widget.set_position(c_lat, c_lon)
                map_widget.set_zoom(z)
            except Exception:
                pass

        _apply_once()
        root.after(120, _apply_once)
        root.after(320, _apply_once)

    def _zoom_extents() -> None:
        b = view_state.get("last_bounds")
        if not b:
            return
        _fit_bounds(b[0], b[1], b[2], b[3])

    def _load_kml() -> None:
        path = filedialog.askopenfilename(
            title="Відкрити KML",
            filetypes=[("KML files", "*.kml"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
            blocks = re.findall(
                r"<coordinates>(.*?)</coordinates>",
                txt,
                flags=re.DOTALL | re.IGNORECASE,
            )
            coords = []
            path_segments = []
            for b in blocks:
                seg = []
                for token in b.strip().split():
                    parts = token.split(",")
                    if len(parts) < 2:
                        continue
                    try:
                        lon = float(parts[0])
                        lat = float(parts[1])
                    except ValueError:
                        continue
                    pt = (lat, lon)
                    coords.append(pt)
                    seg.append(pt)
                if len(seg) >= 2:
                    path_segments.append(seg)
            if not coords:
                raise ValueError("У KML не знайдено координати.")
            # Clear previously rendered KML overlays.
            try:
                if hasattr(map_widget, "delete_all_path"):
                    map_widget.delete_all_path()
                    view_state["capture_path"] = None
                    view_state["capture_points"] = []
                    view_state["block_path"] = None
                    view_state["trunk_path"] = None
                else:
                    for p in view_state.get("kml_paths", []):
                        try:
                            map_widget.delete(p)
                        except Exception:
                            pass
                    _safe_delete_path(view_state.get("capture_path"))
                    _safe_delete_path(view_state.get("block_path"))
                    _safe_delete_path(view_state.get("trunk_path"))
                    view_state["capture_path"] = None
                    view_state["capture_points"] = []
                    view_state["block_path"] = None
                    view_state["trunk_path"] = None
            except Exception:
                pass
            view_state["kml_paths"] = []

            # Draw KML with high-contrast style.
            for seg in path_segments:
                try:
                    p = map_widget.set_path(
                        seg,
                        color="#FF3B30",
                        width=4,
                    )
                    view_state["kml_paths"].append(p)
                except Exception:
                    pass

            lats = [p[0] for p in coords]
            lons = [p[1] for p in coords]
            _fit_bounds(min(lats), min(lons), max(lats), max(lons))
            silent_showinfo(root, 
                "KML",
                f"Завантажено координат: {len(coords)}\n"
                f"Сегментів: {len(path_segments)}",
            )
        except Exception as ex:
            silent_showerror(root, "KML", f"Не вдалося прочитати KML:\n{ex}")

    zoom_box_state = {"on": False, "x0": 0, "y0": 0, "rect": None}

    def _zoom_box_on() -> None:
        zoom_box_state["on"] = True
        try:
            root.config(cursor="crosshair")
        except Exception:
            pass

    def _zoom_box_off() -> None:
        zoom_box_state["on"] = False
        try:
            root.config(cursor="")
        except Exception:
            pass
        if zoom_box_state["rect"] is not None:
            try:
                map_widget.canvas.delete(zoom_box_state["rect"])
            except Exception:
                pass
            zoom_box_state["rect"] = None

    def _canvas_press(event) -> None:
        tool = view_state.get("active_tool")
        if tool in ("capture_tiles", "block_contour", "trunk_route"):
            if tool == "trunk_route" and app is not None and len(getattr(app, "trunk_map_nodes", []) or []) > 0:
                return "break"
            try:
                lat, lon = map_widget.convert_canvas_coords_to_decimal_coords(int(event.x), int(event.y))
                view_state["draft_points"].append((lat, lon))
                if tool == "capture_tiles":
                    _draw_draft_path("#00D1FF", 3, close_ring=True)
                elif tool == "block_contour":
                    _draw_draft_path("#FFD400", 3, close_ring=True)
                else:
                    _draw_draft_path(TRUNK_PATH_COLOR, 3, close_ring=False)
            except Exception:
                pass
            return "break"
        if not zoom_box_state["on"]:
            return None
        zoom_box_state["x0"] = int(event.x)
        zoom_box_state["y0"] = int(event.y)
        if zoom_box_state["rect"] is not None:
            try:
                map_widget.canvas.delete(zoom_box_state["rect"])
            except Exception:
                pass
        zoom_box_state["rect"] = map_widget.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline=ZOOM_BOX_OUTLINE,
            width=ZOOM_BOX_WIDTH,
            dash=ZOOM_BOX_DASH,
        )
        return "break"

    def _canvas_drag(event) -> None:
        if view_state.get("active_tool") in ("capture_tiles", "block_contour", "trunk_route"):
            return "break"
        if not zoom_box_state["on"] or zoom_box_state["rect"] is None:
            return None
        map_widget.canvas.coords(
            zoom_box_state["rect"],
            zoom_box_state["x0"],
            zoom_box_state["y0"],
            event.x,
            event.y,
        )
        return "break"

    def _canvas_release(event) -> None:
        if view_state.get("active_tool") in ("capture_tiles", "block_contour", "trunk_route"):
            return "break"
        if not zoom_box_state["on"]:
            return None
        x0, y0 = zoom_box_state["x0"], zoom_box_state["y0"]
        x1, y1 = int(event.x), int(event.y)
        if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
            _zoom_box_off()
            return "break"
        try:
            # Center on rectangle midpoint.
            mx = int((x0 + x1) / 2)
            my = int((y0 + y1) / 2)
            lat_c, lon_c = map_widget.convert_canvas_coords_to_decimal_coords(mx, my)
            map_widget.set_position(lat_c, lon_c)
        except Exception:
            pass
        try:
            # Pixel-based zoom factor: reliable even when bbox conversion is unstable.
            cw = max(1, int(map_widget.canvas.winfo_width()))
            ch = max(1, int(map_widget.canvas.winfo_height()))
            bw = max(1, abs(x1 - x0))
            bh = max(1, abs(y1 - y0))
            scale = min(float(cw) / float(bw), float(ch) / float(bh))
            delta = int(max(1, math.floor(math.log2(max(1.01, scale)))))
            z0 = int(getattr(map_widget, "zoom", 12))
            z1 = max(2, min(19, z0 + delta))
            map_widget.set_zoom(z1)
        except Exception:
            pass
        _zoom_box_off()
        return "break"

    def _canvas_right_click(_event):
        tool = view_state.get("active_tool")
        if tool in ("capture_tiles", "block_contour"):
            _finalize_tool_shape()
            return "break"
        if tool == "trunk_route":
            if app is not None and len(getattr(app, "trunk_map_nodes", []) or []) > 0:
                return None
            _finalize_tool_shape()
            return "break"
        return None

    try:
        map_widget.canvas.bind("<ButtonPress-1>", _canvas_press, add="+")
        map_widget.canvas.bind("<B1-Motion>", _canvas_drag, add="+")
        map_widget.canvas.bind("<ButtonRelease-1>", _canvas_release, add="+")
        map_widget.canvas.bind("<ButtonPress-3>", _canvas_right_click, add="+")
    except Exception:
        pass

    tk.Label(
        left_toolbar,
        text="Спецінструменти",
        bg="#181818",
        fg="#A8E6FF",
        font=("Segoe UI", 10, "bold"),
        anchor="w",
    ).pack(fill=tk.X, padx=10, pady=(10, 8))
    _main_btn_cap = tk.Button(
        left_toolbar,
        text="🧭 Захват тайлів",
        command=lambda: _set_tool("capture_tiles"),
        bg="#242424",
        fg="#E8E8E8",
        relief=tk.FLAT,
        padx=8,
        pady=6,
    )
    _main_btn_cap.pack(fill=tk.X, padx=8, pady=3)
    _attach_dark_tooltip(_main_btn_cap, "Виділити прямокутник — завантажити SRTM-тайли лише для цієї області.")
    _main_btn_blk = tk.Button(
        left_toolbar,
        text="🟨 Контур блоку",
        command=lambda: _set_tool("block_contour"),
        bg="#242424",
        fg="#E8E8E8",
        relief=tk.FLAT,
        padx=8,
        pady=6,
    )
    _main_btn_blk.pack(fill=tk.X, padx=8, pady=3)
    _attach_dark_tooltip(_main_btn_blk, "Намалювати контур поля на карті (автономний переглядач).")
    _main_btn_trunk = tk.Button(
        left_toolbar,
        text="🟩 Траса магістралі",
        command=lambda: _set_tool("trunk_route"),
        bg="#242424",
        fg="#E8E8E8",
        relief=tk.FLAT,
        padx=8,
        pady=6,
    )
    _main_btn_trunk.pack(fill=tk.X, padx=8, pady=3)
    _attach_dark_tooltip(
        _main_btn_trunk,
        "Труба: ЛКМ+ПКМ на вузлі — кінець ребра; ЛКМ — початок і трасувальні точки (вільне поле — пікет); "
        "ПКМ — з’єднати з кінцем. Чернетка зберігається при перемиканні на вузли магістралі. "
        "Топологія — «Зберегти граф магістралі».",
    )
    _btn_trunk_save = tk.Button(
        left_toolbar,
        text="💾 Зберегти граф магістралі",
        command=_standalone_save_trunk_graph,
        bg="#1f3d2e",
        fg="#C8F5D8",
        relief=tk.FLAT,
        padx=8,
        pady=6,
    )
    _btn_trunk_save.pack(fill=tk.X, padx=8, pady=3)
    _attach_dark_tooltip(
        _btn_trunk_save,
        "Завершити редагування (інструменти вимикаються), перевірити дерево магістралі та оновити trunk_tree з вузлів і відрізків карти.",
    )
    _main_btn_cancel = tk.Button(
        left_toolbar,
        text="❌ Скасувати інструмент",
        command=lambda: _set_tool(None),
        bg="#2d1f1f",
        fg="#FFD1D1",
        relief=tk.FLAT,
        padx=8,
        pady=6,
    )
    _main_btn_cancel.pack(fill=tk.X, padx=8, pady=(10, 3))
    _attach_dark_tooltip(_main_btn_cancel, "Вимкнути активний інструмент малювання на карті.")
    _btn_dl_tiles = tk.Button(
        left_toolbar,
        text="⬇ Завантажити тайли SRTM",
        command=_download_tiles_for_capture,
        bg="#1f3b2a",
        fg="#D7FFE6",
        relief=tk.FLAT,
        padx=8,
        pady=7,
    )
    _btn_dl_tiles.pack(fill=tk.X, padx=8, pady=(8, 3))
    _attach_dark_tooltip(_btn_dl_tiles, "Завантажити тайли висот для області, виділеної інструментом «Захват тайлів».")
    tk.Label(
        left_toolbar,
        textvariable=tool_hint_var,
        justify=tk.LEFT,
        wraplength=150,
        bg="#181818",
        fg="#B0B0B0",
        anchor="nw",
    ).pack(fill=tk.X, padx=10, pady=(10, 6))
    tk.Label(
        left_toolbar,
        textvariable=download_status_var,
        justify=tk.LEFT,
        wraplength=150,
        bg="#181818",
        fg="#8FCF9B",
        anchor="nw",
    ).pack(fill=tk.X, padx=10, pady=(0, 8))

    _main_top_sat = tk.Button(
        top_bar,
        text="🛰 Супутник (GE-style)",
        command=_set_satellite,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_sat.pack(side=tk.LEFT, padx=(8, 6), pady=4)
    _attach_dark_tooltip(_main_top_sat, "Підложка супутникових знімків (як у Google Earth).")
    _main_top_cop = tk.Button(
        top_bar,
        text="🛰 Copernicus S2",
        command=_set_copernicus,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_cop.pack(side=tk.LEFT, padx=2, pady=4)
    _attach_dark_tooltip(
        _main_top_cop,
        "Мозаїка Sentinel-2 без хмар (програма Copernicus, дані ESA). Тайли EOX; атрибуція та умови: https://s2maps.eu/ . "
        f"Максимальний зум у карті — {COPERNICUS_S2_CLOUDLESS_MAX_ZOOM} (z+1 з EOX — 404).",
    )
    _main_top_terrain = tk.Button(
        top_bar,
        text="🏔 Рельєф",
        command=_set_terrain,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_terrain.pack(side=tk.LEFT, padx=2, pady=4)
    _attach_dark_tooltip(_main_top_terrain, "Топографічна підложка з рельєфом.")
    _main_top_kml = tk.Button(
        top_bar,
        text="📂 Відкрити .kml",
        command=_load_kml,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_kml.pack(side=tk.LEFT, padx=(10, 6), pady=4)
    _attach_dark_tooltip(_main_top_kml, "Відкрити KML і показати на карті.")
    _main_top_zoom_box = tk.Button(
        top_bar,
        text="🔲 Зум рамкою",
        command=_zoom_box_on,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_zoom_box.pack(side=tk.LEFT, padx=2, pady=4)
    _attach_dark_tooltip(_main_top_zoom_box, "Масштабувати карту під виділений прямокутник.")
    _main_top_ext = tk.Button(
        top_bar,
        text="🔍 Zoom Extents",
        command=_zoom_extents,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_ext.pack(side=tk.LEFT, padx=2, pady=4)
    _attach_dark_tooltip(_main_top_ext, "Показати всю завантажену геометрію (маркери/KML) у вікні карти.")
    _main_top_light = tk.Button(
        top_bar,
        text="☀️ Світла мапа",
        command=_set_light,
        bg="#2a2a2a",
        fg="#e8e8e8",
        activebackground="#353535",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=4,
    )
    _main_top_light.pack(side=tk.LEFT, padx=2, pady=4)
    _attach_dark_tooltip(_main_top_light, "Світла векторна підложка OpenStreetMap.")

    # Kyiv initial center.
    kyiv_lat, kyiv_lon = 50.4501, 30.5234
    map_widget.set_position(kyiv_lat, kyiv_lon)
    map_widget.set_zoom(12)
    map_widget.set_marker(kyiv_lat, kyiv_lon, text="Київ")

    root.mainloop()


if __name__ == "__main__":
    main()

