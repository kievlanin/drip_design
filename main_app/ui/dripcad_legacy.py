import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, simpledialog
import copy
import hashlib
import json
import math
import os
import fnmatch
import subprocess
import sys
import threading
import time
from typing import List, Optional, Set, Tuple
from shapely.geometry import (
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Polygon,
    LineString,
    Point,
    box as shapely_box,
)
from shapely.ops import nearest_points, substring, unary_union

# Імпорт модулів нової структури
from modules.hydraulic_module.hydraulics_core import (
    HydraulicEngine,
    allowed_pipe_candidates_sorted,
    normalize_allowed_pipes_map,
)
from modules.hydraulic_module.trunk_tree_compute import (
    TrunkTreeEdge,
    TrunkTreeNode,
    TrunkTreeSpec,
    compute_trunk_tree_steady,
)
from modules.hydraulic_module import lateral_solver as lat_sol
from main_app.paths import PIPES_DB_PATH, PROJECT_ROOT, DRIPPERS_DB_PATH, LATERALS_DB_PATH
from main_app.io import file_io_impl as file_io
from main_app.ui.control_panel_impl import ControlPanel
from main_app.ui.tooltips import attach_tooltip
from modules.geo_module.topography_core import (
    TopoEngine,
    _idw_z,
    _BUCKET_CELL_M,
    _build_point_buckets,
    _z_at_grid_node,
)
from modules.hydraulic_module.trunk_map_graph import (
    build_oriented_edges,
    ensure_trunk_node_ids,
    expand_trunk_segments_to_pair_edges,
    is_trunk_root_kind,
    validate_trunk_map_graph,
)

# Інструменти магістралі на головному полотні («Без карти») — ті самі імена, що на карті.
_CANVAS_TRUNK_POINT_TOOLS = frozenset(
    {"trunk_pump", "trunk_valve", "trunk_picket", "trunk_junction", "trunk_consumer"}
)
# Підбір об'єкта: «Інфо» (рука) і «Вибір» (стрілка) — одна логіка, різний курсор.
_CANVAS_PASSIVE_PICK_TOOLS = frozenset({"map_pick_info", "select"})
_TRUNK_NODE_SNAP_CANVAS_M = 40.0
_TRUNK_CANVAS_PATH_COLOR = "#8E24AA"
# Інфо: шлях до насоса / шлях до споживачів від розгалуження
_TRUNK_INFO_COLOR_PUMP_PATH = "#FFEB3B"
_TRUNK_INFO_COLOR_TO_CONSUMERS = "#ADFF2F"
# Підбір об'єктів (як на карті): пріоритет менше — вище в стеку.
_PICK_TRUNK_NODE_R_M = 26.0
_PICK_TRUNK_LINE_R_M = 16.0
_PICK_SUBMAIN_R_M = 14.0
_PICK_FIELD_VALVE_R_M = 22.0
_PICK_LAT_SCENE_R_M = 12.0


def _canvas_trunk_rubber_color(tool: str) -> str:
    return {
        "trunk_pump": "#EF5350",
        "trunk_valve": "#E57373",
        "trunk_picket": "#42A5F5",
        "trunk_junction": "#1E88E5",
        "trunk_consumer": "#C4933A",
    }.get(tool, _TRUNK_CANVAS_PATH_COLOR)


class DripCAD:
    def __init__(self, root):
        self.root = root
        self.root.title("Drip Designer Pro v10.7 - Spacious Table Rows")
        self.root.geometry("1000x800")
        self.root.configure(bg="#1e1e1e")
        
        try: self.root.state('zoomed') 
        except: self.root.attributes('-zoomed', True) 

        self.engine = HydraulicEngine()
        self.pipe_db = self.engine.pipes_db
        self.topo = TopoEngine() 
        
        self.allowed_pipes = {}
        for mat, pns in self.pipe_db.items():
            self.allowed_pipes[mat] = {}
            for pn, ods in pns.items():
                self.allowed_pipes[mat][pn] = list(ods.keys())

        self.MAX_FIELD_BLOCKS = 100
        self.points, self.dir_points = [], []
        # Кожен блок: контур, свій напрямок рядів, свої сабмейни та латералі
        self.field_blocks = []
        self._dir_target_block_idx = None
        self._active_submain_block_idx = None
        self._active_draw_block_idx = None
        self._cut_line_start = None
        self.active_submain, self.active_manual_lat = [], []
        self.is_closed = False
        self.zoom, self.offset_x, self.offset_y = 0.7, 425, 375
        self._snap_point, self._current_live_end = None, None
        self._last_mouse_world, self._pan_start = (0, 0), None
        self._full_redraw_idle_id = None
        self.calc_results = {"sections": [], "valves": {}, "emitters": {}, "submain_profiles": {}}
        self._submain_topo_in_headloss = True
        self._submain_preview_world = None
        self._submain_end_snapped = False
        self._moving_section_label_key = None
        self._moving_section_label_sub_idx = None
        self._moving_section_label_sm_idx = None
        self._moving_section_label_preview = None
        self._emit_isolines_cache = {"sig": None, "contours_by_cls": {}}
        self._pressure_zone_geom_cache = {}
        self._zoom_box_start = None
        self._zoom_box_end = None
        self.ruler_start = None
        self._last_map_pointer_world = None
        self.geo_ref = None
        # Декоративні полілінії (карта / ситуація), не беруть участі в гідравліці; зберігаються в JSON як scene_lines.
        self.scene_lines = []
        # Вузли магістралі на карті (WGS84 + локальні XY); kind: source | bend | junction | consumption — зберігаються в JSON.
        self.trunk_map_nodes = []
        # Відрізки магістралі: кожен запис — ребро (два вузли) + path_local (полілінія) у м.
        self.trunk_map_segments = []
        self._trunk_route_last_node_idx = None
        # Спецінструменти магістралі / ліній на полотні «Без карти» (не плутати з mode=DRAW…).
        self._canvas_special_tool = None
        self._canvas_trunk_draft_world = None
        self._canvas_polyline_draft = []
        self._canvas_trunk_route_draft_indices = []
        # Вибір (стрілка): збережені об'єкти (category, payload, label); рамка ЛКМ.
        self._canvas_selection_keys: List[Tuple[str, object, str]] = []
        self._select_marquee_active = False
        self._select_marquee_dragged = False
        self._select_marquee_start_screen: Optional[Tuple[int, int]] = None
        self._select_marquee_curr_screen: Optional[Tuple[int, int]] = None
        self._select_marquee_start_world: Optional[Tuple[float, float]] = None
        self._select_marquee_curr_world: Optional[Tuple[float, float]] = None
        # Рамка зони майбутнього проєкту (локальні м, XY) — задається на карті; пріоритет для тайлів/DEM/ізоліній.
        self.project_zone_bounds_local = None
        self.is_georeferenced = False
        self.last_report = None
        self.trunk_tree_data = self._default_trunk_tree_payload()
        self.trunk_tree_results = {}

        self.snap_enabled = True 
        self.snap_disabled_next_click = False

        self.var_proj_name = tk.StringVar(value="Project_01")
        self.mode = tk.StringVar(value="VIEW")
        self.action = tk.StringVar(value="ADD") 
        self.ortho_on = tk.BooleanVar(value=True)
        
        self.var_lat_step = tk.StringVar(value="0.9")
        self.var_emit_step = tk.StringVar(value="0.3")
        self.var_emit_flow = tk.StringVar(value="1.05")
        self.var_emit_model = tk.StringVar(value="")
        self.var_emit_nominal_flow = tk.StringVar(value="")
        self.var_emit_k_coeff = tk.StringVar(value="")
        self.var_emit_x_exp = tk.StringVar(value="")
        self.var_emit_kd_coeff = tk.StringVar(value="1.0")
        self.var_emit_h_min = tk.StringVar(value="1.0")
        self.var_emit_h_ref = tk.StringVar(value="10.0")
        self.var_lat_inner_d_mm = tk.StringVar(value="13.6")
        self.var_lateral_model = tk.StringVar(value="")
        self.var_emit_h_press_min = tk.StringVar(value="0")
        self.var_emit_h_press_max = tk.StringVar(value="0")
        self.var_max_lat_len = tk.StringVar(value="0")
        self.var_lat_block_count = tk.StringVar(value="0")
        
        self.var_fixed_sec = tk.BooleanVar(value=True)
        self.var_num_sec = tk.StringVar(value="3")
        self.var_hydro_clear_block = tk.StringVar(value="1")
        self.var_v_min = tk.StringVar(value="0.5")
        self.var_v_max = tk.StringVar(value="1.5")
        self.var_submain_lateral_snap_m = tk.StringVar(value="2.0")
        self.var_valve_h_max_m = tk.StringVar(value="0")
        self.var_valve_h_max_optimize = tk.BooleanVar(value=True)
        # Увімкніть за потреби: IDW + ізолінії навантажують CPU; типово вимкнено для плавнішого UI.
        self.var_show_emitter_flow = tk.BooleanVar(value=False)
        self.var_show_press_zone_outlines_on_map = tk.BooleanVar(value=False)
        self.var_emit_iso_method = tk.StringVar(value="idw")
        # Латералі: compare | bisection | newton (див. lateral_solver_stats у звіті)
        self.var_lateral_solver_mode = tk.StringVar(value="bisection")
        
        self.var_topo_step = tk.StringVar(value="1.0")
        self.var_topo_grid = tk.StringVar(value="5.0")
        self.show_contours = tk.BooleanVar(value=True)
        self.show_topo_points = tk.BooleanVar(value=True)
        self.show_topo_computation_zone = tk.BooleanVar(value=True)
        self.show_srtm_boundary_overlay = tk.BooleanVar(value=True)
        self.show_srtm_tile_footprints = tk.BooleanVar(value=False)
        self.cached_contours = []
        
        self.pipe_material = tk.StringVar()
        self.pipe_pn = tk.StringVar()

        self.export_lat_step_kml = tk.IntVar(value=10)

        # Відображення авто-латералей (вкладка «Блок»); ручні завжди на полотні
        self.var_lat_disp_step = tk.StringVar(value="1")
        self.var_lat_disp_n_start = tk.StringVar(value="")
        self.var_lat_disp_n_end = tk.StringVar(value="")
        self.var_lat_disp_use_step = tk.BooleanVar(value=True)
        self.var_lat_disp_use_start = tk.BooleanVar(value=False)
        self.var_lat_disp_use_end = tk.BooleanVar(value=False)

        self.var_lat_step.trace_add("write", lambda *a: [self.reset_calc(), self.regenerate_grid()])
        self.var_emit_step.trace_add("write", lambda *a: [self.reset_calc(), self.redraw()])
        self.var_emit_flow.trace_add("write", lambda *a: [self.reset_calc(), self.redraw()])
        self.var_emit_k_coeff.trace_add("write", lambda *a: self.reset_calc())
        self.var_emit_x_exp.trace_add("write", lambda *a: [self.reset_calc(), self.redraw()])
        self.var_emit_kd_coeff.trace_add("write", lambda *a: self.reset_calc())
        self.var_emit_h_min.trace_add("write", lambda *a: [self.reset_calc(), self.redraw()])
        self.var_emit_h_ref.trace_add("write", lambda *a: [self.reset_calc(), self.redraw()])
        self.var_lat_inner_d_mm.trace_add("write", lambda *a: self.reset_calc())
        self.var_emit_h_press_min.trace_add("write", lambda *a: self.reset_calc())
        self.var_emit_h_press_max.trace_add("write", lambda *a: self.reset_calc())
        self.var_max_lat_len.trace_add("write", lambda *a: [self.reset_calc(), self.regenerate_grid()])
        self.var_lat_block_count.trace_add("write", lambda *a: [self.reset_calc(), self.regenerate_grid()])
        self.var_num_sec.trace_add(
            "write", lambda *a: self._invalidate_hydro_ui_active_block_or_all()
        )
        self.var_fixed_sec.trace_add(
            "write", lambda *a: self._invalidate_hydro_ui_active_block_or_all()
        )
        self.var_v_min.trace_add("write", lambda *a: self.reset_calc())
        self.var_v_max.trace_add("write", lambda *a: self.reset_calc())
        self.var_submain_lateral_snap_m.trace_add("write", lambda *a: self.redraw())
        self.var_valve_h_max_m.trace_add(
            "write", lambda *a: self._invalidate_hydro_ui_active_block_or_all()
        )
        self.var_valve_h_max_optimize.trace_add(
            "write", lambda *a: self._invalidate_hydro_ui_active_block_or_all()
        )
        self.var_show_emitter_flow.trace_add("write", lambda *a: self.redraw())
        self.var_show_press_zone_outlines_on_map.trace_add("write", lambda *a: self.redraw())
        self.var_emit_iso_method.trace_add(
            "write",
            lambda *a: (
                setattr(
                    self,
                    "_emit_isolines_cache",
                    {"sig": None, "contours_by_cls": {}},
                ),
                self.redraw(),
            ),
        )
        # Режим бісекція/Ньютон застосовується лише під час «Розрахунок»; перемикання не чіпає calc_results.
        self.pipe_material.trace_add("write", lambda *a: self.update_pn_dropdown(skip_reset=True))
        self.pipe_pn.trace_add("write", lambda *a: self.sync_hydro_pipe_summary())
        self.var_emit_model.trace_add("write", self._on_emit_model_change)
        self.var_emit_nominal_flow.trace_add("write", self._on_emit_nominal_change)
        self.var_lateral_model.trace_add("write", self._on_lateral_model_change)

        self.drippers_db = []
        self._load_drippers_db()
        self.laterals_db = []
        self._load_laterals_db()

        self.setup_menu()
        self.control_panel = ControlPanel(self)
        self.var_active_block_idx = tk.IntVar(value=0)
        self.left_pane = tk.Frame(self.root, bg="#121212")
        self.left_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.top_bar = tk.Frame(self.left_pane, bg="#1a1a1e", height=32)
        self.top_bar.pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            self.top_bar,
            text="Активний блок:",
            bg="#1a1a1e",
            fg="#aaaaaa",
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(8, 4), pady=4)
        self.cb_active_block = ttk.Combobox(
            self.top_bar,
            width=16,
            state="readonly",
            values=[],
        )
        self.cb_active_block.pack(side=tk.LEFT, pady=4)
        self.cb_active_block.bind("<<ComboboxSelected>>", self._on_active_block_combo)
        self._btn_submain_editor = ttk.Button(
            self.top_bar,
            text="Редактор сабмейну…",
            command=self.open_submain_segment_editor,
            width=24,
        )
        self._btn_submain_editor.pack(side=tk.LEFT, padx=(12, 6), pady=4)
        attach_tooltip(
            self._btn_submain_editor,
            "Редагувати довжини труб по секціях активного сабмейну (підганка під потрібну сумарну довжину).",
        )
        self.lbl_view_mode = tk.Label(
            self.top_bar,
            text="Режим: Без карти",
            bg="#1a1a1e",
            fg="#88ddff",
            font=("Arial", 9, "bold"),
        )
        self.lbl_view_mode.pack(side=tk.LEFT, padx=(8, 4), pady=4)
        self.lbl_map_mode_hint = tk.Label(
            self.top_bar,
            text="Без карти: локальне креслення",
            bg="#1a1a1e",
            fg="#9a9a9a",
            font=("Arial", 8),
        )
        self.lbl_map_mode_hint.pack(side=tk.LEFT, padx=(2, 6), pady=4)
        self.view_notebook = ttk.Notebook(self.left_pane)
        self.view_notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.draw_panel = tk.Frame(self.view_notebook, bg="#121212")
        self.map_panel = tk.Frame(self.view_notebook, bg="#0f1218")
        self.view_notebook.add(self.draw_panel, text="Без карти")
        self.view_notebook.add(self.map_panel, text="Карта")
        self._draw_left_sidebar = tk.Frame(self.draw_panel, bg="#181818", width=200)
        self._draw_left_sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self._draw_left_sidebar.pack_propagate(False)
        self._canvas_host = tk.Frame(self.draw_panel, bg="#121212")
        self._canvas_host.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self._canvas_host, bg="#121212", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        from main_app.ui.map_left_draw_widgets import build_off_canvas_draw_notebook

        def _route_map_tool(n):
            self.route_embedded_map_tool(n)

        build_off_canvas_draw_notebook(self._draw_left_sidebar, self, map_tool_router=_route_map_tool)
        self._embedded_map_ready = False
        self.view_notebook.bind("<<NotebookTabChanged>>", self._on_view_panel_changed)
        self._refresh_active_block_combo()

        def _on_lat_disp_change(*_args):
            self._sync_lat_disp_widgets()

        for _vd in (
            self.var_lat_disp_step,
            self.var_lat_disp_n_start,
            self.var_lat_disp_n_end,
            self.var_lat_disp_use_step,
            self.var_lat_disp_use_start,
            self.var_lat_disp_use_end,
        ):
            _vd.trace_add("write", _on_lat_disp_change)
        
        avail = list(self.pipe_db.keys())
        if hasattr(self, "cb_mat"):
            self.cb_mat.config(values=avail)
        if avail:
            self.pipe_material.set(avail[0])
        self.update_pn_dropdown(skip_reset=True)
        self.sync_hydro_pipe_summary()
        self.sync_srtm_model_status()
        self.bind_events()
        self._sync_lat_disp_widgets()

    def setup_menu(self):
        self.menubar = tk.Menu(self.root)
        filemenu = tk.Menu(self.menubar, tearoff=0)
        filemenu.add_command(label="Новий проект", command=self.clear_all)
        filemenu.add_command(label="Відкрити проект (JSON)...", command=lambda: file_io.load_project(self))
        filemenu.add_separator()
        filemenu.add_command(label="📥 Імпорт контуру (KML)...", command=lambda: file_io.import_kml(self))
        filemenu.add_command(label="📤 Експорт сітки висот 10х10м (KML)...", command=lambda: file_io.export_elevation_grid_kml(self))
        filemenu.add_separator()
        filemenu.add_command(label="📤 Експорт розрахунку (KML для Earth)...", command=lambda: file_io.export_kml(self))
        filemenu.add_command(label="📤 Експорт ізоліній (DXF для AutoCAD)...", command=lambda: file_io.export_dxf(self))
        filemenu.add_command(label="📤 Експорт звіту (PDF)...", command=lambda: file_io.export_pdf(self))
        filemenu.add_separator()
        filemenu.add_command(label="💾 Зберегти проект...", command=lambda: file_io.save_project(self))
        filemenu.add_command(
            label="💾 Зберегти проект як (JSON)...",
            command=lambda: file_io.save_project_as(self),
        )
        filemenu.add_command(
            label="💾 Зберегти проект як геоприв'язаний...",
            command=lambda: file_io.save_project_georeferenced(self),
        )
        self.menubar.add_cascade(label="Файл", menu=filemenu)
        
        settingsmenu = tk.Menu(self.menubar, tearoff=0)
        settingsmenu.add_command(label="⚙️ Параметри експорту...", command=self.open_export_settings)
        settingsmenu.add_separator()
        settingsmenu.add_command(label="✅ Вибір труб для проекту...", command=self.open_pipe_selector)
        settingsmenu.add_command(label="🗄 Глобальна база труб (Редактор)...", command=self.open_pipe_editor)
        settingsmenu.add_command(label="💧 База крапельниць (Редактор)...", command=self.open_drippers_editor)
        settingsmenu.add_command(label="🧵 База латералей (Редактор)...", command=self.open_laterals_editor)
        self.menubar.add_cascade(label="Налаштування", menu=settingsmenu)

        toolsmenu = tk.Menu(self.menubar, tearoff=0)
        toolsmenu.add_command(
            label="Калькулятор латераля (поле)…",
            command=self.open_lateral_field_calculator,
        )
        toolsmenu.add_command(
            label="Калькулятор телескопа сабмейну…",
            command=self.open_submain_telescope_calculator,
        )
        toolsmenu.add_command(
            label="Редактор сегментів сабмейну…",
            command=self.open_submain_segment_editor,
        )
        toolsmenu.add_command(
            label="Магістраль-дерево…",
            command=self.open_trunk_tree_editor,
        )
        self.menubar.add_cascade(label="Інструменти", menu=toolsmenu)

        viewmenu = tk.Menu(self.menubar, tearoff=0)
        viewmenu.add_command(label="🎛 Розгорнути/Згорнути панель", command=lambda: self.control_panel.toggle_panel())
        viewmenu.add_command(label="🔍 Центрувати камеру (Zoom Extents)", command=lambda: [self.zoom_to_fit(), self.redraw()])
        viewmenu.add_command(label="🔲 Зум рамкою", command=self.enable_zoom_box_mode)
        self.menubar.add_cascade(label="Вікно", menu=viewmenu)

        self.root.config(menu=self.menubar)

    def _ensure_embedded_map_panel(self):
        if self._embedded_map_ready:
            return True
        try:
            from main_app.ui.map_viewer_tk_window import create_embedded_map_panel
            self._embedded_map_host = create_embedded_map_panel(self.map_panel, app=self)
            self._embedded_map_ready = True
            return True
        except Exception as ex:
            messagebox.showerror("Мапа", f"Не вдалося ініціалізувати панель карти:\n{ex}")
            return False

    def route_embedded_map_tool(self, name):
        """Увімкнути інструмент: на «Без карти» — на головному полотні; на «Карті» — на віджеті карти."""
        try:
            tab_idx = int(self.view_notebook.index("current"))
        except Exception:
            tab_idx = 0
        _MAP_ONLY_TOOLS = frozenset({"project_zone_rect", "capture_tiles", "block_contour"})

        def _clear_canvas_tool_state() -> None:
            self._canvas_special_tool = None
            self._canvas_trunk_draft_world = None
            self._canvas_polyline_draft = []
            self._canvas_trunk_route_draft_indices = []
            self._canvas_selection_keys = []
            self._select_marquee_active = False
            self._select_marquee_dragged = False
            self._select_marquee_start_screen = None
            self._select_marquee_curr_screen = None
            self._select_marquee_start_world = None
            self._select_marquee_curr_world = None

        def _clear_embedded_map_tool_only() -> None:
            if not getattr(self, "_embedded_map_ready", False):
                return
            host = getattr(self, "_embedded_map_host", None)
            fn = getattr(host, "_set_map_tool", None) if host is not None else None
            if callable(fn):
                try:
                    fn(None)
                except Exception:
                    pass

        if tab_idx == 0:
            if name is None:
                _clear_canvas_tool_state()
                self.redraw()
                self._refresh_canvas_cursor_for_special_tool()
                return True
            if name in _MAP_ONLY_TOOLS:
                messagebox.showinfo(
                    "Карта",
                    "Цей інструмент працює лише на вкладці «Карта».",
                )
                return False
            _clear_embedded_map_tool_only()
            if name != "select":
                self._select_marquee_active = False
                self._select_marquee_dragged = False
                self._select_marquee_start_screen = None
                self._select_marquee_curr_screen = None
                self._select_marquee_start_world = None
                self._select_marquee_curr_world = None
            if name not in ("select", "map_pick_info"):
                self._canvas_selection_keys = []
            self._canvas_special_tool = name
            self._canvas_trunk_draft_world = None
            self._canvas_polyline_draft = []
            self._canvas_trunk_route_draft_indices = []
            self.redraw()
            self._refresh_canvas_cursor_for_special_tool()
            return True

        _clear_canvas_tool_state()
        if not self._ensure_embedded_map_panel():
            messagebox.showerror(
                "Карта",
                "Не вдалося відкрити панель карти (перевірте tkintermapview).",
            )
            return False
        host = getattr(self, "_embedded_map_host", None)
        fn = getattr(host, "_set_map_tool", None) if host is not None else None
        if callable(fn):
            fn(name)
            self._refresh_canvas_cursor_for_special_tool()
            return True
        messagebox.showerror("Карта", "Панель карти не готова.")
        return False

    def sync_trunk_segment_paths_from_nodes(self) -> None:
        """Оновлює path_local відрізків: для ребра з двома вузлами зберігає проміжні точки полілінії, лише кінці прив’язує до вузлів."""
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        for seg in list(getattr(self, "trunk_map_segments", []) or []):
            if not isinstance(seg, dict):
                continue
            ni = seg.get("node_indices")
            if not isinstance(ni, list) or len(ni) < 2:
                continue
            if len(ni) == 2:
                try:
                    ia, ib = int(ni[0]), int(ni[1])
                except (TypeError, ValueError):
                    continue
                if not (0 <= ia < len(nodes) and 0 <= ib < len(nodes)):
                    continue
                try:
                    ax, ay = float(nodes[ia]["x"]), float(nodes[ia]["y"])
                    bx, by = float(nodes[ib]["x"]), float(nodes[ib]["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                existing = seg.get("path_local")
                fixed: list = []
                if isinstance(existing, list) and len(existing) > 2:
                    for p in existing:
                        if isinstance(p, (list, tuple)) and len(p) >= 2:
                            try:
                                fixed.append((float(p[0]), float(p[1])))
                            except (TypeError, ValueError):
                                fixed = []
                                break
                        else:
                            fixed = []
                            break
                    if len(fixed) > 2:
                        fixed[0] = (ax, ay)
                        fixed[-1] = (bx, by)
                        seg["path_local"] = fixed
                        continue
                seg["path_local"] = [(ax, ay), (bx, by)]
                continue
            path: list = []
            ok = True
            for ii in ni:
                try:
                    idx = int(ii)
                except (TypeError, ValueError):
                    ok = False
                    break
                if not (0 <= idx < len(nodes)):
                    ok = False
                    break
                try:
                    path.append((float(nodes[idx]["x"]), float(nodes[idx]["y"])))
                except (KeyError, TypeError, ValueError):
                    ok = False
                    break
            if ok and len(path) >= 2:
                seg["path_local"] = path

    def normalize_trunk_segments_to_graph_edges(self) -> None:
        """Один запис сегмента = одне ребро (труба); ланцюги розбиваються на пари вузлів."""
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        segs = list(getattr(self, "trunk_map_segments", []) or [])
        if not segs:
            return
        self.trunk_map_segments = expand_trunk_segments_to_pair_edges(segs, nodes)
        self.sync_trunk_segment_paths_from_nodes()

    def _trunk_segment_world_path(self, seg) -> list:
        """Ламана труби в локальних м: для ребра з path_local — полілінія; інакше ланцюг вузлів або path_local."""
        if not isinstance(seg, dict):
            return []
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        ni = seg.get("node_indices")

        def _parse_pl(raw) -> list:
            out: list = []
            if not isinstance(raw, list):
                return out
            for p in raw:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    try:
                        out.append((float(p[0]), float(p[1])))
                    except (TypeError, ValueError):
                        return []
                else:
                    return []
            return out

        if isinstance(ni, list) and len(ni) == 2:
            pl = _parse_pl(seg.get("path_local") or [])
            if len(pl) >= 2:
                return pl
            path: list = []
            for ii in ni:
                try:
                    idx = int(ii)
                except (TypeError, ValueError):
                    path = []
                    break
                if 0 <= idx < len(nodes):
                    try:
                        path.append((float(nodes[idx]["x"]), float(nodes[idx]["y"])))
                    except (KeyError, TypeError, ValueError):
                        path = []
                        break
                else:
                    path = []
                    break
            return path if len(path) >= 2 else []

        path = []
        if isinstance(ni, list) and len(ni) > 2:
            for ii in ni:
                try:
                    idx = int(ii)
                except (TypeError, ValueError):
                    path = []
                    break
                if 0 <= idx < len(nodes):
                    try:
                        path.append((float(nodes[idx]["x"]), float(nodes[idx]["y"])))
                    except (KeyError, TypeError, ValueError):
                        path = []
                        break
                else:
                    path = []
                    break
        if len(path) >= 2:
            return path
        pl2 = _parse_pl(seg.get("path_local") or [])
        return pl2 if len(pl2) >= 2 else []

    def _distance_point_to_polyline_m(self, wx: float, wy: float, pts: list) -> float:
        if len(pts) < 2:
            return 1e18
        try:
            return LineString([(float(a), float(b)) for a, b in pts]).distance(Point(wx, wy))
        except Exception:
            return 1e18

    def _collect_world_pick_hits(self, wx: float, wy: float) -> List[Tuple[int, float, str, object, str]]:
        """
        Упорядковані попадання (пріоритет ↑, відстань ↑).
        Кортеж: (priority, dist, category, payload, label).
        category: trunk_node | trunk_seg | field_valve | submain | block | lateral | scene
        payload: int (індекс вузла / відрізка) або None.

        Серед вузла магістралі (пр. 0) і відрізка (пр. 2) лишається один запис — з меншою
        відстанню до курсора, щоб підпис «Інфо» збігався з підсвіткою. Інші пріоритети без змін.
        """
        hits: List[Tuple[int, float, str, object, str]] = []
        p_mouse = Point(wx, wy)
        tol_node = self._pick_tolerance_m(_PICK_TRUNK_NODE_R_M, 24.0)
        tol_valve = self._pick_tolerance_m(_PICK_FIELD_VALVE_R_M, 22.0)
        tol_trunk_ln = self._pick_tolerance_m(_PICK_TRUNK_LINE_R_M, 18.0)
        tol_sm = self._pick_tolerance_m(_PICK_SUBMAIN_R_M, 18.0)
        tol_scene = self._pick_tolerance_m(_PICK_LAT_SCENE_R_M, 16.0)

        for i, node in enumerate(getattr(self, "trunk_map_nodes", []) or []):
            try:
                nx = float(node["x"])
                ny = float(node["y"])
            except (KeyError, TypeError, ValueError):
                continue
            kind = str(node.get("kind", "")).lower()
            nid = str(node.get("id", "")).strip() or f"T{i}"
            if kind == "source":
                lab = f"Насос (витік), {nid}"
            elif kind == "valve":
                lab = f"Кран (відведення / сток), {nid}"
            elif kind == "bend":
                lab = f"Пікет, {nid}"
            elif kind == "junction":
                lab = f"Розгалуження (сумматор), {nid}"
            elif kind == "consumption":
                lab = f"Споживач (сток), {nid}"
            else:
                lab = f"Вузол магістралі, {nid}"
            d = math.hypot(wx - nx, wy - ny)
            if d <= tol_node:
                hits.append((0, d, "trunk_node", i, lab))

        try:
            for vx, vy in self.get_valves():
                d = math.hypot(wx - float(vx), wy - float(vy))
                if d <= tol_valve:
                    hits.append((1, d, "field_valve", None, "Кран (початок відрізка сабмейну)"))
        except Exception:
            pass

        for si, seg in enumerate(getattr(self, "trunk_map_segments", []) or []):
            pl = self._trunk_segment_world_path(seg)
            if len(pl) < 2:
                continue
            d = self._distance_point_to_polyline_m(wx, wy, pl)
            if d <= tol_trunk_ln:
                hits.append((2, d, "trunk_seg", si, f"Магістраль, відрізок {si + 1}"))

        for bi, b in enumerate(getattr(self, "field_blocks", []) or []):
            for sm_i, sm in enumerate(list(b.get("submain_lines") or [])):
                if len(sm) < 2:
                    continue
                flat = [(float(p[0]), float(p[1])) for p in sm if isinstance(p, (list, tuple)) and len(p) >= 2]
                if len(flat) < 2:
                    continue
                d = self._distance_point_to_polyline_m(wx, wy, flat)
                if d <= tol_sm:
                    hits.append((3, d, "submain", (bi, sm_i), f"Сабмейн · блок {bi + 1} · лінія {sm_i + 1}"))

        tol_block = max(15.0 / max(self.zoom, 0.01), 0.5)
        for bi, b in enumerate(getattr(self, "field_blocks", []) or []):
            ring = list(b.get("ring") or [])
            if len(ring) < 3:
                continue
            try:
                poly = Polygon(ring)
                if poly.is_empty:
                    continue
                if poly.contains(p_mouse):
                    hits.append((4, 0.0, "block", bi, f"Блок поля {bi + 1}"))
                else:
                    bd = poly.boundary.distance(p_mouse)
                    if bd <= tol_block:
                        hits.append((4, bd, "block", bi, f"Контур блоку поля {bi + 1}"))
            except Exception:
                continue

        lat_thresh = 15.0 / max(self.zoom, 0.01)
        for bi, b in enumerate(getattr(self, "field_blocks", []) or []):
            for lat in b.get("auto_laterals") or []:
                try:
                    d = lat.distance(p_mouse)
                    if d < lat_thresh:
                        hits.append((5, d, "lateral", bi, f"Латераль (авто) · блок {bi + 1}"))
                except Exception:
                    pass
            for lat in b.get("manual_laterals") or []:
                try:
                    d = lat.distance(p_mouse)
                    if d < lat_thresh:
                        hits.append((5, d, "lateral", bi, f"Латераль (ручний) · блок {bi + 1}"))
                except Exception:
                    pass

        for si, seg in enumerate(getattr(self, "scene_lines", []) or []):
            if len(seg) < 2:
                continue
            flat = [(float(p[0]), float(p[1])) for p in seg if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(flat) < 2:
                continue
            d = self._distance_point_to_polyline_m(wx, wy, flat)
            if d <= tol_scene:
                hits.append((6, d, "scene", si, f"Лінія ситуації (ескіз) #{si + 1}"))

        trunk_hits = [h for h in hits if h[2] in ("trunk_node", "trunk_seg")]
        other_hits = [h for h in hits if h[2] not in ("trunk_node", "trunk_seg")]
        if trunk_hits:
            best_trunk = min(trunk_hits, key=lambda h: h[1])
            hits = [best_trunk] + other_hits
        hits.sort(key=lambda t: (t[0], t[1]))
        return hits

    def _trunk_topology_oriented(self):
        """Орієнтоване дерево від насоса або None при помилці топології."""
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        segs = list(getattr(self, "trunk_map_segments", []) or [])
        if not nodes or not segs:
            return None
        directed, errs = build_oriented_edges(nodes, segs)
        if directed is None or errs:
            return None
        n = len(nodes)
        parent = [-1] * n
        children: List[List[int]] = [[] for _ in range(n)]
        for u, v in directed:
            parent[v] = u
            children[u].append(v)
        src = None
        for i in range(n):
            if is_trunk_root_kind(str(nodes[i].get("kind", ""))):
                src = i
                break
        if src is None:
            return None
        return {"parent": parent, "children": children, "source": src, "nodes": nodes}

    def _trunk_edge_undirected_to_segment_index(self) -> dict:
        out: dict = {}
        for si, seg in enumerate(getattr(self, "trunk_map_segments", []) or []):
            if not isinstance(seg, dict):
                continue
            ni = seg.get("node_indices")
            if not isinstance(ni, list) or len(ni) != 2:
                continue
            try:
                a, b = int(ni[0]), int(ni[1])
            except (TypeError, ValueError):
                continue
            out[(min(a, b), max(a, b))] = si
        return out

    @staticmethod
    def _trunk_path_to_undirected_edge_keys(path_idx: List[int]) -> List[Tuple[int, int]]:
        keys: List[Tuple[int, int]] = []
        for i in range(len(path_idx) - 1):
            a, b = path_idx[i], path_idx[i + 1]
            keys.append((min(a, b), max(a, b)))
        return keys

    def _trunk_path_indices_to_source(self, topo: dict, idx: int) -> Optional[List[int]]:
        src = topo["source"]
        parent = topo["parent"]
        nlim = len(parent) + 3
        out: List[int] = []
        cur = idx
        for _ in range(nlim):
            out.append(cur)
            if cur == src:
                return out
            p = parent[cur]
            if p < 0 or p == cur:
                return None
            cur = p
        return None

    def _trunk_path_indices_j_to_descendant(self, topo: dict, j: int, d: int) -> Optional[List[int]]:
        parent = topo["parent"]
        nlim = len(parent) + 3
        up: List[int] = []
        cur = d
        for _ in range(nlim):
            up.append(cur)
            if cur == j:
                up.reverse()
                return up
            cur = parent[cur]
            if cur < 0:
                return None
        return None

    def _trunk_consumption_descendants(self, topo: dict, j: int) -> List[int]:
        children = topo["children"]
        nodes = topo["nodes"]
        stack = list(children[j])
        seen = set()
        out: List[int] = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            k = str(nodes[cur].get("kind", "")).lower()
            if k == "consumption":
                out.append(cur)
            for ch in children[cur]:
                stack.append(ch)
        return out

    def _trunk_info_resolve_focus_node(self, wx: float, wy: float, cat: str, payload: object) -> Optional[int]:
        if cat == "trunk_node":
            if isinstance(payload, int):
                return int(payload)
            return None
        if cat != "trunk_seg":
            return None
        if not isinstance(payload, int):
            return None
        si = int(payload)
        segs = list(getattr(self, "trunk_map_segments", []) or [])
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        if si < 0 or si >= len(segs):
            return None
        seg = segs[si]
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) != 2:
            return None
        try:
            a, b = int(ni[0]), int(ni[1])
        except (TypeError, ValueError):
            return None
        if not (0 <= a < len(nodes) and 0 <= b < len(nodes)):
            return None
        try:
            ax, ay = float(nodes[a]["x"]), float(nodes[a]["y"])
            bx, by = float(nodes[b]["x"]), float(nodes[b]["y"])
        except (KeyError, TypeError, ValueError):
            return None
        da = math.hypot(wx - ax, wy - ay)
        db = math.hypot(wx - bx, wy - by)
        return a if da <= db else b

    def _trunk_highlight_segment_index_sets_for_focus(
        self, topo: dict, focus: int
    ) -> Tuple[Set[int], Set[int]]:
        """Множини індексів сегментів магістралі для підсвітки (лайм / жовтий) від вузла focus."""
        lime_si: Set[int] = set()
        yellow_si: Set[int] = set()
        nodes = topo["nodes"]
        if focus < 0 or focus >= len(nodes):
            return lime_si, yellow_si
        knd = str(nodes[focus].get("kind", "")).lower()
        edge_map = self._trunk_edge_undirected_to_segment_index()

        def add_path_keys(keys: List[Tuple[int, int]], dest: Set[int]) -> None:
            for ek in keys:
                si = edge_map.get(ek)
                if si is not None:
                    dest.add(si)

        if knd == "source":
            return lime_si, yellow_si

        path_up = self._trunk_path_indices_to_source(topo, focus)
        if path_up:
            add_path_keys(self._trunk_path_to_undirected_edge_keys(path_up), yellow_si)

        if knd == "junction":
            for d in self._trunk_consumption_descendants(topo, focus):
                p_down = self._trunk_path_indices_j_to_descendant(topo, focus, d)
                if p_down and len(p_down) >= 2:
                    add_path_keys(self._trunk_path_to_undirected_edge_keys(p_down), lime_si)

        return lime_si, yellow_si

    def trunk_info_highlight_world_paths(
        self, wx: float, wy: float
    ) -> Tuple[List[List[Tuple[float, float]]], List[List[Tuple[float, float]]]]:
        """
        Для інструмента «Інфо», коли перше попадання — вузол/відрізок магістралі:
        (лайм — гілки до споживачів, жовтий — шлях до насоса). Локальні координати м.
        """
        hits = self._collect_world_pick_hits(wx, wy)
        if not hits:
            return [], []
        _pri, _d, cat, payload, _lab = hits[0]
        if cat not in ("trunk_node", "trunk_seg"):
            return [], []
        topo = self._trunk_topology_oriented()
        if topo is None:
            return [], []
        focus = self._trunk_info_resolve_focus_node(wx, wy, cat, payload)
        if focus is None:
            return [], []
        lime_si, yellow_si = self._trunk_highlight_segment_index_sets_for_focus(topo, focus)

        lime_paths: List[List[Tuple[float, float]]] = []
        yellow_paths: List[List[Tuple[float, float]]] = []
        segs = list(getattr(self, "trunk_map_segments", []) or [])
        for si in sorted(lime_si):
            if 0 <= si < len(segs):
                pl = self._trunk_segment_world_path(segs[si])
                if len(pl) >= 2:
                    lime_paths.append([(float(x), float(y)) for x, y in pl])
        for si in sorted(yellow_si):
            if 0 <= si < len(segs):
                pl = self._trunk_segment_world_path(segs[si])
                if len(pl) >= 2:
                    yellow_paths.append([(float(x), float(y)) for x, y in pl])
        return lime_paths, yellow_paths

    def pick_world_object_at_canvas(self, wx: float, wy: float) -> Optional[str]:
        """
        Підпис об'єкта під курсором на полотні «Без карти» (локальні м).
        Пріоритети узгоджені з підбором на карті. Пороги залежать від zoom (пікселі на екрані).
        """
        hits = self._collect_world_pick_hits(wx, wy)
        if not hits:
            return None
        return str(hits[0][4])

    @staticmethod
    def _world_rect_normalize(
        x0: float, y0: float, x1: float, y1: float
    ) -> Tuple[float, float, float, float]:
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _pick_hits_in_world_rect(
        self, x0: float, y0: float, x1: float, y1: float, *, crossing: bool
    ) -> List[Tuple[str, object, str]]:
        """Підбір об'єктів у прямокутнику: crossing — перетин; інакше лише повністю всередині (рамка)."""
        minx, miny, maxx, maxy = self._world_rect_normalize(x0, y0, x1, y1)
        if maxx - minx < 1e-6 or maxy - miny < 1e-6:
            return []
        R = shapely_box(minx, miny, maxx, maxy)
        out: List[Tuple[str, object, str]] = []
        seen: set = set()

        def add(cat: str, payload: object, label: str) -> None:
            key = (cat, payload)
            try:
                hash(key)
            except TypeError:
                key = (cat, repr(payload))
            if key in seen:
                return
            seen.add(key)
            out.append((cat, payload, label))

        def line_ok(pts: list) -> bool:
            if len(pts) < 2:
                return False
            try:
                ls = LineString([(float(a), float(b)) for a, b in pts])
            except Exception:
                return False
            if crossing:
                return bool(R.intersects(ls))
            return bool(ls.within(R))

        def point_ok(px: float, py: float) -> bool:
            pt = Point(float(px), float(py))
            if crossing:
                return bool(R.intersects(pt))
            return bool(R.contains(pt))

        for i, node in enumerate(getattr(self, "trunk_map_nodes", []) or []):
            try:
                nx = float(node["x"])
                ny = float(node["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if not point_ok(nx, ny):
                continue
            kind = str(node.get("kind", "")).lower()
            nid = str(node.get("id", "")).strip() or f"T{i}"
            if kind == "source":
                lab = f"Насос (витік), {nid}"
            elif kind == "valve":
                lab = f"Кран (відведення / сток), {nid}"
            elif kind == "bend":
                lab = f"Пікет, {nid}"
            elif kind == "junction":
                lab = f"Розгалуження (сумматор), {nid}"
            elif kind == "consumption":
                lab = f"Споживач (сток), {nid}"
            else:
                lab = f"Вузол магістралі, {nid}"
            add("trunk_node", i, lab)

        try:
            for vx, vy in self.get_valves():
                if point_ok(float(vx), float(vy)):
                    add("field_valve", (float(vx), float(vy)), "Кран (початок відрізка сабмейну)")
        except Exception:
            pass

        for si, seg in enumerate(getattr(self, "trunk_map_segments", []) or []):
            pl = self._trunk_segment_world_path(seg)
            if line_ok(pl):
                add("trunk_seg", si, f"Магістраль, відрізок {si + 1}")

        for bi, b in enumerate(getattr(self, "field_blocks", []) or []):
            for sm_i, sm in enumerate(list(b.get("submain_lines") or [])):
                if len(sm) < 2:
                    continue
                flat = [(float(p[0]), float(p[1])) for p in sm if isinstance(p, (list, tuple)) and len(p) >= 2]
                if line_ok(flat):
                    add(
                        "submain",
                        (bi, sm_i),
                        f"Сабмейн · блок {bi + 1} · лінія {sm_i + 1}",
                    )

        for bi, b in enumerate(getattr(self, "field_blocks", []) or []):
            ring = list(b.get("ring") or [])
            if len(ring) < 3:
                continue
            try:
                poly = Polygon(ring)
                if poly.is_empty:
                    continue
                if crossing:
                    ok = R.intersects(poly)
                else:
                    ok = R.contains(poly)
                if ok:
                    add("block", bi, f"Блок поля {bi + 1}")
            except Exception:
                continue

        for bi, b in enumerate(getattr(self, "field_blocks", []) or []):
            for li, lat in enumerate(b.get("auto_laterals") or []):
                try:
                    if crossing:
                        ok = lat.intersects(R)
                    else:
                        ok = lat.within(R)
                    if ok:
                        add("lateral", ("auto", bi, li), f"Латераль (авто) · блок {bi + 1}")
                except Exception:
                    pass
            for li, lat in enumerate(b.get("manual_laterals") or []):
                try:
                    if crossing:
                        ok = lat.intersects(R)
                    else:
                        ok = lat.within(R)
                    if ok:
                        add("lateral", ("manual", bi, li), f"Латераль (ручний) · блок {bi + 1}")
                except Exception:
                    pass

        for si, seg in enumerate(getattr(self, "scene_lines", []) or []):
            if len(seg) < 2:
                continue
            flat = [(float(p[0]), float(p[1])) for p in seg if isinstance(p, (list, tuple)) and len(p) >= 2]
            if line_ok(flat):
                add("scene", si, f"Лінія ситуації (ескіз) #{si + 1}")

        return out

    def _draw_canvas_selection_layer(self) -> None:
        """Постійна підсвітка вибраних об'єктів і рамка вибору (рамка / кросрамка)."""
        try:
            if not self.canvas.winfo_exists():
                return
        except tk.TclError:
            return
        tag = "selection_layer"
        if (
            getattr(self, "_select_marquee_active", False)
            and self._select_marquee_dragged
            and self._select_marquee_start_screen
            and self._select_marquee_curr_screen
        ):
            x0, y0 = self._select_marquee_start_screen
            x1, y1 = self._select_marquee_curr_screen
            xa, xb = min(x0, x1), max(x0, x1)
            ya, yb = min(y0, y1), max(y0, y1)
            crossing = x1 < x0
            col = "#FFAB40" if crossing else "#69F0AE"
            dash = (4, 4) if crossing else ()
            self.canvas.create_rectangle(
                xa, ya, xb, yb, outline=col, width=2, dash=dash, tags=tag
            )
            self.canvas.create_text(
                xa + 4,
                ya + 4,
                text="Кросрамка" if crossing else "Рамка",
                anchor=tk.NW,
                fill=col,
                font=("Segoe UI", 8, "bold"),
                tags=tag,
            )

        keys = list(getattr(self, "_canvas_selection_keys", []) or [])
        if not keys:
            return

        topo = self._trunk_topology_oriented()
        lime_all: Set[int] = set()
        yellow_all: Set[int] = set()
        seg_outline: Set[int] = set()
        for cat, payload, _lab in keys:
            if cat == "trunk_node" and isinstance(payload, int) and topo is not None:
                le, ye = self._trunk_highlight_segment_index_sets_for_focus(topo, int(payload))
                lime_all |= le
                yellow_all |= ye
            elif cat == "trunk_seg" and isinstance(payload, int):
                seg_outline.add(int(payload))

        segs = list(getattr(self, "trunk_map_segments", []) or [])
        for si in sorted(lime_all):
            if 0 <= si < len(segs):
                pl = self._trunk_segment_world_path(segs[si])
                if len(pl) >= 2:
                    scr = []
                    for xy in pl:
                        scr.extend(self.to_screen(float(xy[0]), float(xy[1])))
                    if len(scr) >= 4:
                        self.canvas.create_line(
                            scr,
                            fill=_TRUNK_INFO_COLOR_TO_CONSUMERS,
                            width=8,
                            tags=tag,
                        )
        for si in sorted(yellow_all):
            if 0 <= si < len(segs):
                pl = self._trunk_segment_world_path(segs[si])
                if len(pl) >= 2:
                    scr = []
                    for xy in pl:
                        scr.extend(self.to_screen(float(xy[0]), float(xy[1])))
                    if len(scr) >= 4:
                        self.canvas.create_line(
                            scr,
                            fill=_TRUNK_INFO_COLOR_PUMP_PATH,
                            width=6,
                            tags=tag,
                        )
        for si in sorted(seg_outline):
            if si in lime_all or si in yellow_all:
                continue
            if 0 <= si < len(segs):
                pl = self._trunk_segment_world_path(segs[si])
                if len(pl) >= 2:
                    scr = []
                    for xy in pl:
                        scr.extend(self.to_screen(float(xy[0]), float(xy[1])))
                    if len(scr) >= 4:
                        self.canvas.create_line(
                            scr,
                            fill="#00E5FF",
                            width=7,
                            tags=tag,
                        )

        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        for cat, payload, _lab in keys:
            if cat != "trunk_node" or not isinstance(payload, int):
                continue
            ni = int(payload)
            if not (0 <= ni < len(nodes)):
                continue
            try:
                cx, cy = self.to_screen(float(nodes[ni]["x"]), float(nodes[ni]["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            self.canvas.create_oval(
                cx - 14,
                cy - 14,
                cx + 14,
                cy + 14,
                outline="#00E5FF",
                width=3,
                tags=tag,
            )

        for cat, payload, _lab in keys:
            if cat == "submain" and isinstance(payload, tuple) and len(payload) == 2:
                bi, sm_i = int(payload[0]), int(payload[1])
                blocks = getattr(self, "field_blocks", []) or []
                if 0 <= bi < len(blocks):
                    sm = list(blocks[bi].get("submain_lines") or [])
                    if 0 <= sm_i < len(sm) and len(sm[sm_i]) >= 2:
                        scr = []
                        for p in sm[sm_i]:
                            if isinstance(p, (list, tuple)) and len(p) >= 2:
                                scr.extend(self.to_screen(float(p[0]), float(p[1])))
                        if len(scr) >= 4:
                            self.canvas.create_line(
                                scr, fill="#FFD54F", width=6, tags=tag
                            )
            elif cat == "block" and isinstance(payload, int):
                bi = int(payload)
                blocks = getattr(self, "field_blocks", []) or []
                if 0 <= bi < len(blocks):
                    ring = list(blocks[bi].get("ring") or [])
                    if len(ring) > 1:
                        scr = [self.to_screen(float(p[0]), float(p[1])) for p in ring]
                        if len(scr) >= 4:
                            self.canvas.create_line(
                                scr + [scr[0]],
                                fill="#FFD54F",
                                width=4,
                                dash=(6, 4),
                                tags=tag,
                            )
            elif cat == "scene" and isinstance(payload, int):
                si = int(payload)
                sl = list(getattr(self, "scene_lines", []) or [])
                if 0 <= si < len(sl) and len(sl[si]) >= 2:
                    scr = []
                    for p in sl[si]:
                        if isinstance(p, (list, tuple)) and len(p) >= 2:
                            scr.extend(self.to_screen(float(p[0]), float(p[1])))
                    if len(scr) >= 4:
                        self.canvas.create_line(
                            scr, fill="#B388FF", width=5, dash=(4, 3), tags=tag
                        )
            elif cat == "lateral" and isinstance(payload, tuple) and len(payload) == 3:
                kind, bi, li = payload[0], int(payload[1]), int(payload[2])
                blocks = getattr(self, "field_blocks", []) or []
                if 0 <= bi < len(blocks):
                    b = blocks[bi]
                    lst = b.get("auto_laterals" if kind == "auto" else "manual_laterals") or []
                    if 0 <= li < len(lst):
                        lat = lst[li]
                        try:
                            coords = list(lat.coords)
                            scr = []
                            for c in coords:
                                scr.extend(self.to_screen(float(c[0]), float(c[1])))
                            if len(scr) >= 4:
                                self.canvas.create_line(
                                    scr, fill="#81D4FA", width=5, tags=tag
                                )
                        except Exception:
                            pass
            elif cat == "field_valve" and isinstance(payload, tuple) and len(payload) == 2:
                vx, vy = float(payload[0]), float(payload[1])
                cx, cy = self.to_screen(vx, vy)
                self.canvas.create_oval(
                    cx - 10, cy - 10, cx + 10, cy + 10,
                    outline="#FFD54F", width=3, tags=tag,
                )

    def _refresh_canvas_cursor_for_special_tool(self) -> None:
        try:
            ct = getattr(self, "_canvas_special_tool", None)
            if ct == "map_pick_info":
                self.canvas.config(cursor="hand2")
            elif ct == "select":
                self.canvas.config(cursor="arrow")
            else:
                self.canvas.config(cursor="")
        except tk.TclError:
            pass

    def _on_view_panel_changed(self, _event=None):
        idx = int(self.view_notebook.index("current"))
        is_map = idx == 1
        if is_map:
            self._canvas_special_tool = None
            self._canvas_trunk_draft_world = None
            self._canvas_polyline_draft = []
            self._canvas_trunk_route_draft_indices = []
            if not self._ensure_embedded_map_panel():
                self.view_notebook.select(0)
                return
            self.lbl_view_mode.config(text="Режим: Карта")
            self.lbl_map_mode_hint.config(text="Карта: взаємодія з реальною місцевістю", fg="#88dd88")
            self._refresh_canvas_cursor_for_special_tool()
        else:
            if getattr(self, "_embedded_map_ready", False):
                host = getattr(self, "_embedded_map_host", None)
                fn = getattr(host, "_set_map_tool", None) if host is not None else None
                if callable(fn):
                    try:
                        fn(None)
                    except Exception:
                        pass
            self.lbl_view_mode.config(text="Режим: Без карти")
            self.lbl_map_mode_hint.config(text="Без карти: локальне креслення", fg="#9a9a9a")
            self.redraw()
            self._refresh_canvas_cursor_for_special_tool()

    def refresh_map_after_project_load(self):
        """Перемалювати overlay та позиціонувати проєкт на вкладці Карта після load_project."""
        if not getattr(self, "_embedded_map_ready", False):
            return
        host = getattr(self, "_embedded_map_host", None)
        cb = getattr(host, "_refresh_project_overlay", None) if host is not None else None
        if callable(cb):
            try:
                cb(True)
            except TypeError:
                try:
                    cb()
                except Exception:
                    pass
            except Exception:
                pass

    def _schedule_embedded_map_overlay_refresh(self):
        """Оновити overlay карти без зуму (дебаунс), лише на вкладці «Карта»."""
        if not getattr(self, "_embedded_map_ready", False):
            return
        try:
            idx = int(self.view_notebook.index("current"))
        except Exception:
            return
        if idx != 1:
            return
        host = getattr(self, "_embedded_map_host", None)
        cb = getattr(host, "_refresh_project_overlay", None) if host is not None else None
        if not callable(cb):
            return
        pending = getattr(self, "_map_overlay_refresh_after_id", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
        self._map_overlay_refresh_after_id = self.root.after(
            200, self._run_embedded_map_overlay_refresh
        )

    def _run_embedded_map_overlay_refresh(self):
        self._map_overlay_refresh_after_id = None
        host = getattr(self, "_embedded_map_host", None)
        cb = getattr(host, "_refresh_project_overlay", None) if host is not None else None
        if not callable(cb):
            return
        try:
            cb(False)
        except TypeError:
            try:
                cb()
            except Exception:
                pass
        except Exception:
            pass

    def _allowed_pipes_for_block_index(self, bi: int) -> dict:
        """Дозволені труби для гідравліки: окремий набір блоку або глобальний проєкт."""
        if bi is None or bi < 0 or bi >= len(self.field_blocks):
            return self.allowed_pipes
        b = self.field_blocks[bi]
        bp = b.get("params") or {}
        ap = bp.get("allowed_pipes")
        if not isinstance(ap, dict) or not ap:
            ap = b.get("allowed_pipes")
        if isinstance(ap, dict) and ap:
            norm = normalize_allowed_pipes_map(ap)
            return norm if norm else self.allowed_pipes
        return self.allowed_pipes

    def _derive_hydro_mat_pn_from_allowed(self, eff_allowed: dict) -> tuple:
        """
        Мат/PN для підказок UI: збігається з порядком у гідравлічному ядрі —
        перший елемент відсортованого робочого набору (перетин allowed ∩ pipes_db).
        """
        cands = allowed_pipe_candidates_sorted(eff_allowed or {}, self.pipe_db)
        if not cands:
            return str(self.pipe_material.get() or "PVC"), str(self.pipe_pn.get() or "6")
        c0 = cands[0]
        return str(c0["mat"]), str(c0["pn"])

    def _geom_submain_length_for_block(self, bi: int) -> float:
        tot = 0.0
        if bi < 0 or bi >= len(self.field_blocks):
            return 0.0
        for sm in self.field_blocks[bi].get("submain_lines") or []:
            for i in range(len(sm) - 1):
                x0, y0 = sm[i][0], sm[i][1]
                x1, y1 = sm[i + 1][0], sm[i + 1][1]
                tot += math.hypot(x1 - x0, y1 - y0)
        return tot

    def _calc_submain_sections_length_for_block(self, bi: int):
        secs = [
            s
            for s in (self.calc_results.get("sections") or [])
            if int(s.get("block_idx", -1)) == bi
        ]
        if not secs:
            return None
        return sum(float(s.get("L", 0) or 0) for s in secs)

    def _build_allowed_pipes_blocks_list(self):
        out = []
        for bi in range(len(self.field_blocks)):
            b = self.field_blocks[bi]
            bp = b.get("params") or {}
            ap = bp.get("allowed_pipes")
            if not isinstance(ap, dict) or not ap:
                ap = b.get("allowed_pipes")
            if isinstance(ap, dict) and ap:
                norm = normalize_allowed_pipes_map(ap)
                out.append(norm if norm else None)
            else:
                out.append(None)
        return out

    def sync_hydro_pipe_summary(self):
        lbl = getattr(self, "lbl_hydro_pipe", None)
        if lbl is None:
            return
        try:
            bi = self._safe_active_block_idx()
            if bi is not None:
                eff = self._allowed_pipes_for_block_index(bi)
                n_c = len(allowed_pipe_candidates_sorted(eff, self.pipe_db))
                mat, pn = self._derive_hydro_mat_pn_from_allowed(eff)
                bp = self.field_blocks[bi].get("params") or {}
                has_own = isinstance(bp.get("allowed_pipes"), dict) and bool(bp["allowed_pipes"])
                src = "окремий набір блоку (params.allowed_pipes)" if has_own else "як у проєкті (глобально)"
                lbl.config(
                    text=f"Блок {bi + 1}: робочий набір {n_c} труб (усі ✅ у розрахунку); приклад: {mat} PN {pn} — {src}"
                )
            else:
                eff = self.allowed_pipes
                n_c = len(allowed_pipe_candidates_sorted(eff, self.pipe_db))
                mat, pn = self._derive_hydro_mat_pn_from_allowed(eff)
                lbl.config(
                    text=f"Глобально: {n_c} труб у наборі; приклад: {mat} PN {pn}"
                )
        except tk.TclError:
            pass

    def sync_srtm_model_status(self):
        """Оновити індикатор наявності локальної SRTM-моделі в зоні проєкту."""
        lbl = getattr(getattr(self, "control_panel", None), "lbl_srtm_model_status", None)
        if lbl is None:
            return
        try:
            if not getattr(self, "geo_ref", None):
                lbl.config(text="SRTM-модель: невідомо (немає геоприв'язки)", fg="#AAAAAA")
                return

            zone_ring = None
            if getattr(self.topo, "srtm_boundary_pts_local", None) and len(self.topo.srtm_boundary_pts_local) >= 3:
                zone_ring = list(self.topo.srtm_boundary_pts_local)
            else:
                u = self.field_union_polygon()
                if u is not None and not u.is_empty:
                    minx, miny, maxx, maxy = u.bounds
                    zone_ring = [(minx, miny), (minx, maxy), (maxx, maxy), (maxx, miny)]

            if not zone_ring:
                lbl.config(text="SRTM-модель: немає зони проєкту", fg="#AAAAAA")
                return

            from modules.geo_module import srtm_tiles

            ref_lon, ref_lat = self.geo_ref
            lat_min, lat_max, lon_min, lon_max = srtm_tiles.wgs84_bounds_from_local_ring(
                zone_ring, (ref_lon, ref_lat)
            )
            tile_keys = srtm_tiles.iter_tiles_covering_bbox(lat_min, lat_max, lon_min, lon_max)
            if not tile_keys:
                lbl.config(text="SRTM-модель: немає тайлів у межах зони", fg="#AAAAAA")
                return

            cache_dir = srtm_tiles.ensure_srtm_dir()
            have = sum(
                1
                for la, lo in tile_keys
                if srtm_tiles.resolve_hgt_path(cache_dir, la, lo) is not None
            )
            total = len(tile_keys)
            if have == total:
                lbl.config(text=f"SRTM-модель: доступна ({have}/{total} тайлів)", fg="#55DD88")
            elif have > 0:
                lbl.config(text=f"SRTM-модель: частково ({have}/{total} тайлів)", fg="#FFCC66")
            else:
                lbl.config(text=f"SRTM-модель: відсутня ({have}/{total} тайлів)", fg="#FF7777")
        except tk.TclError:
            pass
        except Exception:
            lbl.config(text="SRTM-модель: стан недоступний", fg="#AAAAAA")
        lg = getattr(self, "lbl_hydro_submain_geom", None)
        lc = getattr(self, "lbl_hydro_submain_calc", None)
        try:
            bi2 = self._safe_active_block_idx()
            if lg is not None:
                if bi2 is not None:
                    Lg = self._geom_submain_length_for_block(bi2)
                    lg.config(text=f"Сабмейн блоку {bi2 + 1}: геометрія ΣL = {Lg:.2f} м")
                else:
                    lg.config(text="Сабмейн: оберіть активний блок")
            if lc is not None:
                if bi2 is not None:
                    Lc = self._calc_submain_sections_length_for_block(bi2)
                    if Lc is not None and Lc > 1e-6:
                        lc.config(
                            text=f"У розрахунку (сума L усіх секцій блоку на карті): {Lc:.2f} м"
                        )
                    else:
                        lc.config(
                            text="У розрахунку: немає секцій для цього блоку — виконайте «Розрахунок»"
                        )
                else:
                    lc.config(text="")
        except tk.TclError:
            pass

    def _load_drippers_db(self):
        self.drippers_db = []
        try:
            if DRIPPERS_DB_PATH.exists():
                with open(DRIPPERS_DB_PATH, "r", encoding="utf-8") as f:
                    db = json.load(f)
                rows = db.get("models", []) if isinstance(db, dict) else []
                if isinstance(rows, list):
                    changed = False
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        row_mfr = str(row.get("manufacturer", "")).strip() or "Netafim"
                        if str(row.get("manufacturer", "")).strip() != row_mfr:
                            row["manufacturer"] = row_mfr
                            changed = True
                        tech = row.get("drippers_technical_data", [])
                        if not isinstance(tech, list):
                            continue
                        for it in tech:
                            if not isinstance(it, dict):
                                continue
                            it_mfr = str(it.get("manufacturer", "")).strip() or row_mfr
                            if str(it.get("manufacturer", "")).strip() != it_mfr:
                                it["manufacturer"] = it_mfr
                                changed = True
                    self.drippers_db = rows
                    if changed:
                        payload = db if isinstance(db, dict) else {}
                        payload["schema_version"] = int(payload.get("schema_version", 2) or 2)
                        payload["description"] = str(
                            payload.get("description", "База технічних даних крапельниць за моделями")
                        )
                        payload["models"] = rows
                        with open(DRIPPERS_DB_PATH, "w", encoding="utf-8") as f:
                            json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception:
            self.drippers_db = []

    def _load_laterals_db(self):
        self.laterals_db = []
        try:
            if not LATERALS_DB_PATH.exists():
                return
            with open(LATERALS_DB_PATH, "r", encoding="utf-8") as f:
                db = json.load(f)
            if isinstance(db, dict):
                rows = db.get("items", [])
            else:
                rows = db
            self.laterals_db = rows if isinstance(rows, list) else []
        except Exception:
            self.laterals_db = []

    def _lateral_model_names(self):
        names = []
        for grp in self.laterals_db:
            if not isinstance(grp, dict):
                continue
            for it in grp.get("technical_data", []) or []:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("model", "")).strip()
                if nm:
                    names.append(nm)
        return sorted(set(names))

    def _lateral_record_by_model(self, model_name: str):
        target = str(model_name).strip()
        if not target:
            return None
        for grp in self.laterals_db:
            if not isinstance(grp, dict):
                continue
            od = grp.get("outside_diameter_mm")
            for it in grp.get("technical_data", []) or []:
                if not isinstance(it, dict):
                    continue
                if str(it.get("model", "")).strip() == target:
                    rec = dict(it)
                    rec["outside_diameter_mm"] = od
                    return rec
        return None

    def _on_lateral_model_change(self, *_args):
        rec = self._lateral_record_by_model(self.var_lateral_model.get())
        if not rec:
            return
        try:
            self.var_lat_inner_d_mm.set(str(float(rec.get("inside_diameter_mm"))))
        except (TypeError, ValueError):
            pass

    def _dripper_model_names(self):
        out = []
        for row in self.drippers_db:
            nm = str(row.get("model_name", "") or row.get("series", "")).strip()
            if nm:
                out.append(nm)
        return sorted(set(out))

    def _dripper_nominal_values(self, model_name: str):
        for row in self.drippers_db:
            nm = str(row.get("model_name", "") or row.get("series", "")).strip()
            if nm != str(model_name).strip():
                continue
            vals = []
            for it in row.get("drippers_technical_data", []) or []:
                try:
                    vals.append(float(it.get("nominal_flow_l_h")))
                except (TypeError, ValueError):
                    continue
            vals = sorted(set(vals))
            return [f"{v:.2f}" for v in vals]
        return []

    def _dripper_record(self, model_name: str, nominal_lph: str):
        try:
            qn = float(str(nominal_lph).replace(",", "."))
        except (TypeError, ValueError):
            return None
        for row in self.drippers_db:
            nm = str(row.get("model_name", "") or row.get("series", "")).strip()
            if nm != str(model_name).strip():
                continue
            for it in row.get("drippers_technical_data", []) or []:
                try:
                    if abs(float(it.get("nominal_flow_l_h")) - qn) < 1e-6:
                        return it
                except (TypeError, ValueError):
                    continue
        return None

    def _on_emit_model_change(self, *_args):
        cb_nom = getattr(self, "cb_emit_nominal", None)
        vals = self._dripper_nominal_values(self.var_emit_model.get())
        if cb_nom is not None:
            try:
                cb_nom.config(values=vals)
            except tk.TclError:
                pass
        if vals and self.var_emit_nominal_flow.get() not in vals:
            self.var_emit_nominal_flow.set(vals[0])
        elif not vals:
            self.var_emit_nominal_flow.set("")
            self.var_emit_k_coeff.set("")
            self.var_emit_x_exp.set("")
            self.var_emit_kd_coeff.set("1.0")
            self.reset_calc()

    def _on_emit_nominal_change(self, *_args):
        rec = self._dripper_record(self.var_emit_model.get(), self.var_emit_nominal_flow.get())
        if not rec:
            self.var_emit_k_coeff.set("")
            self.var_emit_x_exp.set("")
            self.var_emit_kd_coeff.set("1.0")
            return
        try:
            qn = float(rec.get("nominal_flow_l_h"))
            self.var_emit_flow.set(f"{qn:.2f}")
        except (TypeError, ValueError):
            pass
        try:
            self.var_emit_k_coeff.set(str(float(rec.get("constant_k"))))
        except (TypeError, ValueError):
            self.var_emit_k_coeff.set("")
        try:
            self.var_emit_x_exp.set(str(float(rec.get("exponent_x"))))
        except (TypeError, ValueError):
            self.var_emit_x_exp.set("")
        try:
            kd = float(rec.get("kd", 1.0))
            if kd <= 1e-12:
                kd = 1.0
            self.var_emit_kd_coeff.set(str(kd))
        except (TypeError, ValueError):
            self.var_emit_kd_coeff.set("1.0")
        self.reset_calc()

    def update_pn_dropdown(self, *args, skip_reset=False):
        # Зміна списку mat/PN у UI не скидає гідравліку автоматично:
        # це лише вибір для редагування/підказок.
        mat = self.pipe_material.get()
        if mat in self.pipe_db:
            pns = sorted(
                list(self.pipe_db[mat].keys()),
                key=lambda x: float(x) if str(x).replace(".", "").isdigit() else 0,
            )
            if hasattr(self, "cb_pn"):
                self.cb_pn.config(values=pns)
            if pns and self.pipe_pn.get() not in pns:
                self.pipe_pn.set(pns[0])
        self.sync_hydro_pipe_summary()

    @staticmethod
    def _normalize_pipe_od_key(db_ods: dict, d_val) -> Optional[str]:
        """Знайти ключ у pipes_db[mat][pn] для номінального Ø секції (рядок або float)."""
        if not db_ods or d_val is None:
            return None
        s = str(d_val).strip()
        if s in db_ods:
            return s
        try:
            d_f = float(str(d_val).replace(",", "."))
        except (TypeError, ValueError):
            d_f = None
        if d_f is not None:
            for k in db_ods:
                try:
                    if abs(float(str(k).replace(",", ".")) - d_f) < 1e-4:
                        return str(k)
                except (TypeError, ValueError):
                    continue
        return None

    def _pipe_color_from_db(self, mat, pn, d_val) -> Optional[str]:
        if not mat or pn is None or d_val is None:
            return None
        try:
            by_pn = self.pipe_db.get(mat, {}).get(str(pn), {})
            od_k = self._normalize_pipe_od_key(by_pn, d_val)
            if not od_k:
                return None
            pd = by_pn.get(od_k)
            if isinstance(pd, dict):
                c = pd.get("color")
                if c and isinstance(c, str) and c.strip():
                    return c.strip()
        except Exception:
            pass
        return None

    def _section_draw_color(self, sec: dict) -> str:
        c = self._pipe_color_from_db(sec.get("mat"), sec.get("pn"), sec.get("d"))
        if c:
            return c
        return sec.get("color") or "#FF3366"

    def bind_events(self):
        self.canvas.bind("<Button-1>", self.handle_left_click)
        self.canvas.bind("<ButtonRelease-1>", self.handle_left_release)
        self.canvas.bind("<Button-3>", self.handle_right_click)
        self.canvas.bind("<Double-Button-3>", self._on_double_right_cancel_draft)
        self.canvas.bind("<Motion>", self.handle_motion)
        self.canvas.bind("<B1-Motion>", self._canvas_b1_motion)
        self.canvas.bind("<ButtonPress-2>", self.start_pan)
        self.canvas.bind("<B2-Motion>", self.handle_pan)
        self.canvas.bind("<ButtonRelease-2>", self.end_pan)
        self.canvas.bind("<MouseWheel>", self.handle_zoom)
        self.canvas.bind("<Double-Button-2>", lambda e: [self.zoom_to_fit(), self.redraw()])
        self.root.bind("<space>", lambda e: self.ortho_on.set(not self.ortho_on.get()))
        self.root.bind("<Control-z>", self.undo_action)
        self.root.bind("<Home>", lambda e: [self.zoom_to_fit(), self.redraw()])
        
        self.root.bind("<Control_L>", self.disable_snap_once)
        self.root.bind("<Control_R>", self.disable_snap_once)
        self.root.bind("<Key>", self.on_key_press)
        self.root.bind("<Escape>", self.handle_escape_cancel_draft)
        self.canvas.bind("<Delete>", self.on_field_delete_key)
        self.canvas.bind("<BackSpace>", self.on_field_delete_key)

    def on_field_delete_key(self, event=None):
        foc = self.root.focus_get()
        if foc is not None and foc.winfo_class() == "Entry":
            return
        if foc != self.canvas:
            return
        self.clear_all_field_blocks()
        return "break"

    def _drawing_draft_active(self) -> bool:
        m = self.mode.get()
        if self.active_submain or self._active_submain_block_idx is not None:
            return True
        if self.active_manual_lat or self._active_draw_block_idx is not None:
            return True
        if self.points and not self.is_closed:
            return True
        if self._dir_target_block_idx is not None or self.dir_points:
            return True
        if self._cut_line_start:
            return True
        if m == "RULER" and self.ruler_start:
            return True
        if self._canvas_draft_active():
            return True
        return False

    def _canvas_draft_active(self) -> bool:
        ct = getattr(self, "_canvas_special_tool", None)
        if ct in _CANVAS_TRUNK_POINT_TOOLS:
            return True
        if not ct:
            return False
        if self._canvas_trunk_draft_world is not None:
            return True
        if len(getattr(self, "_canvas_polyline_draft", []) or []) > 0:
            return True
        if len(getattr(self, "_canvas_trunk_route_draft_indices", []) or []) > 0:
            return True
        return False

    def cancel_active_draft(self, event=None) -> bool:
        """
        Скинути незавершену чернетку (сабмейн, ручна лінія, контур, напрямок, лінія різу, лінійка).
        Подвійне ПКМ на полотні або Escape (коли фокус не в полі вводу).
        """
        if self.action.get() == "DEL":
            return False
        if self._canvas_draft_active():
            self._canvas_trunk_draft_world = None
            self._canvas_polyline_draft = []
            self._canvas_trunk_route_draft_indices = []
            if getattr(self, "_canvas_special_tool", None) in _CANVAS_TRUNK_POINT_TOOLS:
                self._canvas_special_tool = None
                self._refresh_canvas_cursor_for_special_tool()
            self.redraw()
            return True
        if not self._drawing_draft_active():
            return False
        m = self.mode.get()
        self.active_submain = []
        self.active_manual_lat = []
        self.points = []
        self.is_closed = False
        self.dir_points = []
        self._dir_target_block_idx = None
        self._active_submain_block_idx = None
        self._active_draw_block_idx = None
        self._cut_line_start = None
        self.ruler_start = None
        self._current_live_end = None
        self._submain_preview_world = None
        self._submain_end_snapped = False
        if m in ("SET_DIR", "SUBMAIN", "DRAW_LAT", "CUT_LATS"):
            self.mode.set("DRAW")
        self.redraw()
        return True

    def _on_double_right_cancel_draft(self, event=None):
        self.cancel_active_draft()
        return "break"

    def handle_escape_cancel_draft(self, event=None):
        foc = self.root.focus_get()
        if foc is not None:
            wc = foc.winfo_class()
            if wc in ("Entry", "TEntry", "Text"):
                return
        if self.cancel_active_draft():
            return "break"

    def field_union_polygon(self):
        polys = []
        for ring in (b["ring"] for b in getattr(self, "field_blocks", []) or []):
            if len(ring) >= 3:
                g = Polygon(ring)
                if not g.is_valid:
                    g = g.buffer(0)
                if not g.is_empty:
                    polys.append(g)
        if self.is_closed and len(self.points) >= 3:
            g = Polygon(self.points)
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                polys.append(g)
        if not polys:
            return None
        if len(polys) == 1:
            return polys[0]
        try:
            return unary_union(polys)
        except Exception:
            return polys[0]

    def contour_clip_geometry(self):
        """
        Полігон обрізки для ізоліній рельєфу та (разом із fetch) для завантаження DEM.
        Пріоритет: 1) прямокутник зони проєкту з карти; 2) KML зони SRTM; 3) об’єднання блоків поля;
        4) опукла оболонка за точками висоти.
        """
        if self.project_zone_bounds_local is not None:
            minx, miny, maxx, maxy = self.project_zone_bounds_local
            g = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
            return g.buffer(0) if not g.is_valid else g
        # Ізолінії та рамка «зона рельєфу» — спочатку KML зони SRTM (як для тайлів), потім поле поливу
        if self.topo.srtm_boundary_pts_local and len(self.topo.srtm_boundary_pts_local) >= 3:
            g = Polygon(self.topo.srtm_boundary_pts_local)
            return g.buffer(0) if not g.is_valid else g
        u = self.field_union_polygon()
        if u is not None and not u.is_empty:
            return u
        pts = self.topo.elevation_points
        if len(pts) >= 3:
            mp = MultiPoint([(p[0], p[1]) for p in pts])
            h = mp.convex_hull
            if h.geom_type == "Polygon" and h.area > 1e-9:
                return h
        return None

    def field_download_bounds_xy(self):
        if self.project_zone_bounds_local is not None:
            return tuple(self.project_zone_bounds_local)
        u = self.field_union_polygon()
        if u is not None and not u.is_empty:
            return tuple(u.bounds)
        if self.topo.srtm_boundary_pts_local and len(self.topo.srtm_boundary_pts_local) >= 3:
            g = Polygon(self.topo.srtm_boundary_pts_local)
            if not g.is_valid:
                g = g.buffer(0)
            return tuple(g.bounds)
        return None

    def set_project_zone_wgs84_bbox(self, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> None:
        """Задати зону проєкту з карти (географічний прямокутник → локальний AABB + контур для оверлею)."""
        from modules.geo_module import srtm_tiles

        t_lo, t_hi = min(lat_min, lat_max), max(lat_min, lat_max)
        u_lo, u_hi = min(lon_min, lon_max), max(lon_min, lon_max)
        if t_hi - t_lo < 1e-8 or u_hi - u_lo < 1e-8:
            return
        if self.geo_ref is None:
            self.geo_ref = (float((u_lo + u_hi) * 0.5), float((t_lo + t_hi) * 0.5))
        ref_lon, ref_lat = self.geo_ref
        ring_ll = (
            (t_lo, u_lo),
            (t_lo, u_hi),
            (t_hi, u_hi),
            (t_hi, u_lo),
        )
        ring_local = []
        for la, lo in ring_ll:
            ring_local.append(srtm_tiles.lat_lon_to_local_xy(float(la), float(lo), ref_lon, ref_lat))
        xs = [p[0] for p in ring_local]
        ys = [p[1] for p in ring_local]
        self.project_zone_bounds_local = (min(xs), min(ys), max(xs), max(ys))
        self.topo.srtm_boundary_pts_local = list(ring_local)
        self.cached_contours = []
        if hasattr(self, "sync_srtm_model_status"):
            self.sync_srtm_model_status()
        self.redraw()
        if hasattr(self, "_schedule_embedded_map_overlay_refresh"):
            self._schedule_embedded_map_overlay_refresh()

    def _snapshot_block_params(self):
        """Параметри сітки та крапельниць на момент створення/закриття блоку (зберігаються в JSON)."""
        p = {
            "lat": self.var_lat_step.get(),
            "emit": self.var_emit_step.get(),
            "flow": self.var_emit_flow.get(),
            "emit_model": self.var_emit_model.get(),
            "emit_nominal_flow": self.var_emit_nominal_flow.get(),
            "emit_k_coeff": self.var_emit_k_coeff.get(),
            "emit_x_exp": self.var_emit_x_exp.get(),
            "emit_kd_coeff": self.var_emit_kd_coeff.get(),
            "lateral_inner_d_mm": self.var_lat_inner_d_mm.get(),
            "lateral_model": self.var_lateral_model.get(),
            "max_len": self.var_max_lat_len.get(),
            "blocks": self.var_lat_block_count.get(),
        }
        p["emitter_compensated"] = bool(self._emitter_compensated_effective())
        if hasattr(self, "var_emit_h_min"):
            p["emitter_h_min_m"] = self.var_emit_h_min.get()
        if hasattr(self, "var_emit_h_ref"):
            p["emitter_h_ref_m"] = self.var_emit_h_ref.get()
        if hasattr(self, "var_emit_h_press_min"):
            p["emitter_h_press_min_m"] = self.var_emit_h_press_min.get()
        if hasattr(self, "var_emit_h_press_max"):
            p["emitter_h_press_max_m"] = self.var_emit_h_press_max.get()
        return p

    def _new_field_block(self, ring):
        return {
            "ring": list(ring),
            "edge_angle": None,
            "submain_lines": [],
            "auto_laterals": [],
            "manual_laterals": [],
            "params": self._snapshot_block_params(),
            "submain_segment_plan": {},
        }

    def _block_poly(self, block):
        if len(block["ring"]) < 3:
            return Polygon()
        g = Polygon(block["ring"])
        if not g.is_valid:
            g = g.buffer(0)
        return g

    def _lateral_grid_clip_polygon(self, block_poly: Polygon) -> Polygon:
        """Обрізання ліній сітки: уся зона проєкту з карти, інакше — контур блоку."""
        if self.project_zone_bounds_local is not None:
            minx, miny, maxx, maxy = self.project_zone_bounds_local
            g = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
            if not g.is_valid:
                g = g.buffer(0)
            return g
        return block_poly

    def _all_auto_laterals(self):
        return [lat for b in self.field_blocks for lat in b["auto_laterals"]]

    def _all_manual_laterals(self):
        return [lat for b in self.field_blocks for lat in b["manual_laterals"]]

    def _all_submain_lines(self):
        return [sm for b in self.field_blocks for sm in b["submain_lines"]]

    def _all_submain_lines_with_block_indices(self):
        """Паралельно до списку сабмейнів — індекс блоку (для гідравліки та скидання)."""
        lines, block_idx = [], []
        for bi, b in enumerate(self.field_blocks):
            for sm in b.get("submain_lines") or []:
                if len(sm) > 1:
                    lines.append(sm)
                    block_idx.append(bi)
        return lines, block_idx

    def _all_submain_section_lengths_by_sm(self):
        """
        План довжин секцій по кожному сабмейну у глобальному порядку гідравліки.
        Елемент списку: [L1, L2, ...] або [] (коли план відсутній/некоректний).
        """
        out = []
        for bi, b in enumerate(self.field_blocks):
            plan = (b.get("submain_segment_plan") or {})
            by_line = plan.get("by_line") if isinstance(plan, dict) else None
            sm_lines = b.get("submain_lines") or []
            for sm_local_idx, sm in enumerate(sm_lines):
                if len(sm) <= 1:
                    continue
                seg_lens = []
                try:
                    if isinstance(by_line, list) and sm_local_idx < len(by_line):
                        entry = by_line[sm_local_idx]
                        segs = (entry or {}).get("segments") if isinstance(entry, dict) else None
                        if isinstance(segs, list):
                            for s in segs:
                                if not isinstance(s, dict):
                                    continue
                                k = float(s.get("k_mult", 1) or 1)
                                n = float(s.get("n_sticks", 1) or 1)
                                u = float(s.get("unit_m", 0) or 0)
                                L = k * n * u
                                if L > 1e-9:
                                    seg_lens.append(float(L))
                except Exception:
                    seg_lens = []
                # Не підставляти «фантомні» плани (напр. unit_m=100 з каталогу бухт).
                try:
                    L_geom = float(LineString(sm).length)
                except Exception:
                    L_geom = 0.0
                if seg_lens and L_geom > 1e-6:
                    s_plan = sum(seg_lens)
                    if s_plan > L_geom * 1.12 + 2.0:
                        seg_lens = []
                out.append(seg_lens)
        return out

    def _iter_global_submain_meta(self):
        """Глобальний індекс сабмейну, блок, локальний індекс у блоці, координати (як у гідравліці)."""
        sm_i = 0
        for bi, b in enumerate(self.field_blocks):
            for li, sm in enumerate(b.get("submain_lines") or []):
                if len(sm) > 1:
                    yield sm_i, bi, li, sm
                    sm_i += 1

    def _plan_raw_lengths_block_local(self, bi: int, local_sm_idx: int) -> list:
        """Довжини логічних секцій з редактора (k·n·Lтруби) для гілки блоку."""
        if bi < 0 or bi >= len(self.field_blocks):
            return []
        b = self.field_blocks[bi]
        plan = b.get("submain_segment_plan") or {}
        by_line = plan.get("by_line") if isinstance(plan, dict) else None
        if not isinstance(by_line, list) or local_sm_idx >= len(by_line):
            return []
        entry = by_line[local_sm_idx]
        if not isinstance(entry, dict):
            return []
        segs = entry.get("segments")
        if not isinstance(segs, list):
            return []
        out = []
        for s in segs:
            if not isinstance(s, dict):
                continue
            k = float(s.get("k_mult", 1) or 1)
            n = float(s.get("n_sticks", 1) or 1)
            u = float(s.get("unit_m", 0) or 0)
            L = k * n * u
            if L > 1e-9:
                out.append(float(L))
        return out

    def _any_submain_segment_plan(self) -> bool:
        for b in self.field_blocks:
            plan = b.get("submain_segment_plan") or {}
            by_line = plan.get("by_line") if isinstance(plan, dict) else None
            if not isinstance(by_line, list):
                continue
            for entry in by_line:
                if isinstance(entry, dict) and entry.get("segments"):
                    return True
        return False

    def _sections_for_canvas_draw_plan_labels(self, secs_all: list) -> list:
        """
        Підписи L на карті — довжини з плану редактора (логічні секції).
        Геометрія ліній зливається з дрібних гідросекцій у межах кожної логічної ділянки.
        """
        flat = []
        for sm_g, bi, li, sm_coords in self._iter_global_submain_meta():
            plan_lens = self._plan_raw_lengths_block_local(bi, li)
            try:
                L_geom_chk = float(LineString(sm_coords).length)
            except Exception:
                L_geom_chk = 0.0
            if (
                plan_lens
                and L_geom_chk > 1e-6
                and sum(plan_lens) > L_geom_chk * 1.12 + 2.0
            ):
                plan_lens = []
            sm_secs = [
                s
                for s in secs_all
                if int(s.get("sm_idx", -1)) == sm_g and int(s.get("block_idx", -1)) == bi
            ]
            if not plan_lens or not sm_secs:
                for sec in self._merged_sections_display(sm_secs):
                    flat.extend(self._expand_section_draw_parts(sec))
                continue
            try:
                sm_line = LineString(sm_coords)
                L_geom = float(sm_line.length)
            except Exception:
                for sec in self._merged_sections_display(sm_secs):
                    flat.extend(self._expand_section_draw_parts(sec))
                continue
            s_plan = sum(plan_lens)
            if s_plan < 1e-9 or L_geom < 1e-9:
                for sec in self._merged_sections_display(sm_secs):
                    flat.extend(self._expand_section_draw_parts(sec))
                continue
            scale = L_geom / s_plan
            bounds = [0.0]
            acc = 0.0
            for Lp in plan_lens:
                acc += float(Lp) * scale
                bounds.append(acc)
            bounds[-1] = L_geom

            def chainage_mid(sec):
                co = sec.get("coords") or []
                if len(co) < 2:
                    return 0.0
                try:
                    p = LineString(co).interpolate(0.5, normalized=True)
                    return float(sm_line.project(p))
                except Exception:
                    return 0.0

            sm_secs.sort(key=lambda s: int(s.get("section_index", 10**9)))
            n_plan = len(plan_lens)
            merged_logical = []
            for i in range(n_plan):
                lo, hi = bounds[i], bounds[i + 1]
                bucket = []
                for s in sm_secs:
                    cm = chainage_mid(s)
                    if i < n_plan - 1:
                        if lo - 1e-3 <= cm < hi - 1e-3:
                            bucket.append(s)
                    else:
                        if lo - 1e-3 <= cm <= hi + 1e-3:
                            bucket.append(s)
                if not bucket:
                    continue
                bucket.sort(key=chainage_mid)
                merged_coords = []
                tol = 1e-3
                for s in bucket:
                    co = list(s.get("coords") or [])
                    if len(co) < 2:
                        continue
                    if not merged_coords:
                        merged_coords = co
                    else:
                        if (
                            abs(merged_coords[-1][0] - co[0][0]) < tol
                            and abs(merged_coords[-1][1] - co[0][1]) < tol
                        ):
                            merged_coords = merged_coords[:-1] + co
                        else:
                            merged_coords = merged_coords + co[1:]
                if len(merged_coords) < 2:
                    continue
                mid_target = 0.5 * (lo + hi)
                rep = min(bucket, key=lambda s: abs(chainage_mid(s) - mid_target))
                L_show = float(plan_lens[i])
                lk = sm_g * 1000 + i
                merged_logical.append(
                    {
                        "mat": rep.get("mat"),
                        "pn": rep.get("pn"),
                        "d": rep.get("d"),
                        "color": rep.get("color", "#FF3366"),
                        "coords": merged_coords,
                        "L": L_show,
                        "block_idx": bi,
                        "sm_idx": sm_g,
                        "label_key": int(lk),
                    }
                )
            if not merged_logical:
                for sec in self._merged_sections_display(sm_secs):
                    flat.extend(self._expand_section_draw_parts(sec))
                continue
            for sec in merged_logical:
                flat.extend(self._expand_section_draw_parts(sec))
        return flat

    def _flatten_all_lats(self):
        out = []
        for b in self.field_blocks:
            out.extend(b["auto_laterals"])
            out.extend(b["manual_laterals"])
        return out

    def _hydraulic_submain_lines(self):
        """Той самий порядок магістралей, що й у гідравлічному DTO (лише полілінії з ≥2 точок)."""
        lines, _ = self._all_submain_lines_with_block_indices()
        return lines

    def _submain_lateral_snap_m(self) -> float:
        try:
            v = float(str(self.var_submain_lateral_snap_m.get()).replace(",", "."))
        except (TypeError, ValueError, tk.TclError, AttributeError):
            v = float(lat_sol.SUBMAIN_LATERAL_SNAP_M)
        return max(0.05, min(50.0, v))

    def _emitter_compensated_effective(self) -> bool:
        """Режим компенсатора: x = 0 у полі степеня (k·H^x); інакше — турбулентна/степенева модель."""
        raw = (self.var_emit_x_exp.get() or "").strip()
        if not raw:
            return False
        try:
            x = float(raw.replace(",", "."))
        except (TypeError, ValueError):
            return False
        return abs(x) < 1e-12

    def _lateral_connection_sm_index_and_chainage(self, lat: LineString):
        """Індекс сабмейну та s (м) узгоджено з розрахунком (як у lat_geom)."""
        sm_lines = self._hydraulic_submain_lines()
        vs_geom = [c for c in sm_lines if len(c) > 1]
        if not vs_geom:
            return None, 0.0
        sm_multi_geom = MultiLineString(vs_geom)
        conn_dist = 0.0
        inter = lat.intersection(sm_multi_geom)
        if not inter.is_empty:
            if inter.geom_type == "Point":
                conn_dist = lat.project(inter)
            elif inter.geom_type == "LineString":
                conn_dist = lat.project(inter.interpolate(0.5, normalized=True))
            elif hasattr(inter, "geoms") and len(inter.geoms) > 0:
                g0 = inter.geoms[0]
                if g0.geom_type == "Point":
                    conn_dist = lat.project(g0)
                elif g0.geom_type == "LineString":
                    conn_dist = lat.project(g0.interpolate(0.5, normalized=True))
        else:
            pt_lat, pt_sm = nearest_points(lat, sm_multi_geom)
            if pt_lat.distance(pt_sm) < self._submain_lateral_snap_m():
                conn_dist = lat.project(pt_lat)
        pt_conn_geom = lat.interpolate(conn_dist)
        cx, cy = float(pt_conn_geom.x), float(pt_conn_geom.y)
        sm_i, s_along = lat_sol.nearest_submain_chainage_any(cx, cy, sm_lines)
        return int(sm_i), float(s_along)

    def _lateral_block_indices(self):
        out = []
        for bi, b in enumerate(self.field_blocks):
            for _ in b.get("auto_laterals") or []:
                out.append(bi)
            for _ in b.get("manual_laterals") or []:
                out.append(bi)
        return out

    def _per_lateral_emit_steps_flows(self):
        try:
            es0 = float(self.var_emit_step.get().replace(",", "."))
        except Exception:
            es0 = 0.3
        try:
            ef0 = float(self.var_emit_flow.get().replace(",", "."))
        except Exception:
            ef0 = 1.05
        out_s, out_f = [], []
        n_blocks = len(self.field_blocks)
        for bi in self._lateral_block_indices():
            blk = self.field_blocks[bi] if 0 <= bi < n_blocks else {}
            p = blk.get("params") or {}
            try:
                out_s.append(
                    float(str(p.get("emit", self.var_emit_step.get())).replace(",", "."))
                )
            except (ValueError, TypeError):
                out_s.append(es0)
            try:
                out_f.append(
                    float(str(p.get("flow", self.var_emit_flow.get())).replace(",", "."))
                )
            except (ValueError, TypeError):
                out_f.append(ef0)
        return out_s, out_f

    def _submain_segment_lines(self, block):
        """Окремі відрізки сабмейну в блоці; початок відрізка = перша точка полілінії (кран для цього шматка)."""
        return [LineString(list(sm)) for sm in block.get("submain_lines") or [] if len(sm) > 1]

    def _per_submain_ordered_auto_laterals(self, block):
        """
        Авто-латералі розбиті по відрізках сабмейну: кожен відрізок — своя нумерація 0..n-1
        (впорядкування за проекцією на цей відрізок). Латераль потрапляє до найближчого відрізка.
        Без відрізків — одна група (порядок як у списку).
        """
        lats = list(block.get("auto_laterals") or [])
        segs = self._submain_segment_lines(block)
        if not lats:
            return []
        if not segs:
            return [lats]

        buckets = [[] for _ in segs]
        for lat in lats:
            best_si = None
            best_d = float("inf")
            for si, sm in enumerate(segs):
                try:
                    p_lat, p_sm = nearest_points(lat, sm)
                    d = float(p_lat.distance(p_sm))
                except Exception:
                    continue
                if best_si is None or d < best_d - 1e-6 or (abs(d - best_d) <= 1e-6 and si < best_si):
                    best_d, best_si = d, si
            if best_si is None:
                best_si = 0
            buckets[best_si].append(lat)

        out = []
        for si, grp in enumerate(buckets):
            if not grp:
                out.append([])
                continue
            sm = segs[si]

            def pr_key(lat):
                try:
                    _pl, p_sm = nearest_points(lat, sm)
                    return float(sm.project(p_sm))
                except Exception:
                    return 0.0

            out.append(sorted(grp, key=pr_key))
        return out

    def _sync_lat_disp_widgets(self, *args):
        """Увімкнути/вимкнути поля за чекбоксами та перемалювати полотно."""
        if getattr(self, "_lat_disp_sync_lock", False):
            return
        self._lat_disp_sync_lock = True
        try:
            use_step = bool(self.var_lat_disp_use_step.get())
            use_start = bool(self.var_lat_disp_use_start.get())
            use_end = bool(self.var_lat_disp_use_end.get())

            # Вмикання "від/з кінця" автоматично вимикає режим "кожну N-ту".
            if use_step and (use_start or use_end):
                self.var_lat_disp_use_step.set(False)
                use_step = False

            # Якщо увімкнули "кожну N-ту", два номерні режими відключаються.
            if use_step:
                if use_start:
                    self.var_lat_disp_use_start.set(False)
                    use_start = False
                if use_end:
                    self.var_lat_disp_use_end.set(False)
                    use_end = False

            step_state = tk.NORMAL if use_step else tk.DISABLED
            start_state = tk.NORMAL if ((not use_step) and use_start) else tk.DISABLED
            end_state = tk.NORMAL if ((not use_step) and use_end) else tk.DISABLED

            cp = getattr(self, "control_panel", None)
            if cp is not None:
                if hasattr(cp, "ent_lat_disp_step"):
                    cp.ent_lat_disp_step.config(state=step_state)
                if hasattr(cp, "ent_lat_disp_n_start"):
                    cp.ent_lat_disp_n_start.config(state=start_state)
                if hasattr(cp, "ent_lat_disp_n_end"):
                    cp.ent_lat_disp_n_end.config(state=end_state)
        finally:
            self._lat_disp_sync_lock = False
        if hasattr(self, "canvas") and self.canvas.winfo_exists():
            self.redraw()

    @staticmethod
    def _parse_nonneg_int_field(raw) -> int:
        s = (raw or "").strip()
        if not s:
            return 0
        try:
            return max(0, int(float(s.replace(",", "."))))
        except (ValueError, TypeError):
            return 0

    def _visible_auto_lateral_indices(self, n):
        """
        Окремо для кожного відрізка сабмейну (n латералей, від крана: 1…n):
        — крок «кожну N-ту» (за сіткою індексів), якщо увімкнено;
        — «від крана»: одна лінія з порядковим номером k (1 = перша біля крана);
        — «з кінця»: одна лінія з номером m від кінця (1 = остання на відрізку).
        Якщо обидва номери задані — на відрізку до двох ліній (або одна, якщо збігся індекс).
        Підсумок перетинається з кроком.
        """
        if n <= 0:
            return set()
        use_step = bool(
            getattr(self, "var_lat_disp_use_step", None) and self.var_lat_disp_use_step.get()
        )
        if use_step:
            try:
                step = int(float(self.var_lat_disp_step.get().replace(",", ".").strip()))
            except (ValueError, TypeError, AttributeError):
                step = 1
            step = max(1, step)
            return {i for i in range(0, n, step) if 0 <= i < n}
        else:
            # Вимкнений «кожну N-ту»: не показувати автолатералі за замовчуванням.
            step_set = set()

        use_s = getattr(self, "var_lat_disp_use_start", None) and self.var_lat_disp_use_start.get()
        use_e = getattr(self, "var_lat_disp_use_end", None) and self.var_lat_disp_use_end.get()
        k_start = self._parse_nonneg_int_field(self.var_lat_disp_n_start.get()) if use_s else 0
        k_end = self._parse_nonneg_int_field(self.var_lat_disp_n_end.get()) if use_e else 0

        if k_start <= 0 and k_end <= 0:
            return {i for i in step_set if 0 <= i < n}

        band = set()
        if k_start >= 1:
            i0 = k_start - 1
            if 0 <= i0 < n:
                band.add(i0)
        if k_end >= 1:
            j0 = n - k_end
            if 0 <= j0 < n:
                band.add(j0)
        return {i for i in band if 0 <= i < n}

    def _emitter_dots_skip_manual_due_to_step(self) -> bool:
        """Якщо «кожну N-ту» з N>1 — крапельниці лише на тих самих авто-латералях, що й лінії (ручні без точок)."""
        if not (
            getattr(self, "var_lat_disp_use_step", None)
            and self.var_lat_disp_use_step.get()
        ):
            return False
        try:
            step = int(
                float(self.var_lat_disp_step.get().replace(",", ".").strip())
            )
        except (ValueError, TypeError, AttributeError):
            step = 1
        return step > 1

    def _auto_lat_index_in_block(self, auto_list: list, lat) -> Optional[int]:
        """Індекс латераля у списку auto_list (не покладаємось лише на id — після серіалізації/копій id розходиться)."""
        for j, L in enumerate(auto_list):
            if L is lat:
                return j
        for j, L in enumerate(auto_list):
            try:
                if L.equals(lat):
                    return j
            except Exception:
                continue
        return None

    def _flatten_indices_with_visible_emitter_dots(self):
        """
        Індекси латералів у порядку _flatten_all_lats(), для яких дозволені крапельниці на мапі.
        Фільтр збігається з видимістю авто-латералей після розрахунку.
        При «кожну N-ту», N>1, ручні латералі без крапельниць.
        """
        out = set()
        skip_man_emit = self._emitter_dots_skip_manual_due_to_step()
        li = 0
        for b in self.field_blocks:
            auto_list = list(b.get("auto_laterals") or [])
            visible_j = set()
            for grp in self._per_submain_ordered_auto_laterals(b):
                show_g = self._visible_auto_lateral_indices(len(grp))
                for i, lat in enumerate(grp):
                    if i not in show_g:
                        continue
                    j = self._auto_lat_index_in_block(auto_list, lat)
                    if j is not None:
                        visible_j.add(j)
            for j in range(len(auto_list)):
                if j in visible_j:
                    out.add(li)
                li += 1
            for _lat in b.get("manual_laterals") or []:
                if not skip_man_emit:
                    out.add(li)
                li += 1
        return out

    def _flatten_indices_every_n_auto_only(self):
        """
        Індекси лише авто-латералей у порядку _flatten_all_lats(), що потрапляють у фільтр
        «відображати кожну N-ту» (без смуг від крана/з кінця і без ручних латералей).
        """
        out = set()
        li = 0
        for b in self.field_blocks:
            auto_list = list(b.get("auto_laterals") or [])
            visible_j = set()
            for grp in self._per_submain_ordered_auto_laterals(b):
                n = len(grp)
                if n <= 0:
                    continue
                if getattr(self, "var_lat_disp_use_step", None) and self.var_lat_disp_use_step.get():
                    try:
                        step = int(float(self.var_lat_disp_step.get().replace(",", ".").strip()))
                    except (ValueError, TypeError, AttributeError):
                        step = 1
                    step = max(1, step)
                    show_g = set(range(0, n, step))
                else:
                    show_g = set()
                for i, lat in enumerate(grp):
                    if i not in show_g:
                        continue
                    j = self._auto_lat_index_in_block(auto_list, lat)
                    if j is not None:
                        visible_j.add(j)
            for j in range(len(auto_list)):
                if j in visible_j:
                    out.add(li)
                li += 1
            li += len(b.get("manual_laterals") or [])
        return out

    def _global_lat_flat_index(self, lat):
        """Індекс латераля в _flatten_all_lats() — для аудиту тиску (lat_N), незалежно від порядку малювання."""
        if lat is None or lat.is_empty:
            return None
        for i, L in enumerate(self._flatten_all_lats()):
            if L is lat:
                return i
            try:
                if L.equals(lat):
                    return i
            except Exception:
                continue
        return None

    @staticmethod
    def _emit_iso_color(t: float) -> str:
        t = max(0.0, min(1.0, float(t)))
        r = int(30 + (240 - 30) * t)
        g = int(180 + (60 - 180) * t)
        b = int(255 + (40 - 255) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _merge_sorted_distances(self, dists, eps_m=0.02):
        dists = sorted({float(d) for d in dists if d == d})
        if len(dists) < 2:
            return dists
        out = [dists[0]]
        for d in dists[1:]:
            if d - out[-1] > eps_m:
                out.append(d)
        return out

    def _intersection_along_distances(self, lat: LineString, geom, dists: set):
        if geom is None or geom.is_empty or lat.is_empty:
            return
        try:
            inter = lat.intersection(geom)
        except Exception:
            return
        if inter.is_empty:
            return
        gt = inter.geom_type
        if gt == "Point":
            dists.add(float(lat.project(inter)))
        elif gt == "MultiPoint":
            for p in inter.geoms:
                dists.add(float(lat.project(p)))
        elif gt == "LineString":
            for c in inter.coords:
                dists.add(float(lat.project(Point(c))))
        elif gt == "MultiLineString":
            for g in inter.geoms:
                for c in g.coords:
                    dists.add(float(lat.project(Point(c))))
        elif gt == "GeometryCollection":
            for g in inter.geoms:
                self._intersection_along_distances(lat, g, dists)
        elif hasattr(inter, "geoms"):
            for g in inter.geoms:
                self._intersection_along_distances(lat, g, dists)

    def _split_lateral_at_block_submains(self, lat: LineString, block) -> list:
        """Відрізки латераля між перетинами з відрізками сабмейну цього блоку (окремі лінії на полотні)."""
        if lat.is_empty or lat.length < 1e-9:
            return [lat]
        segs = self._submain_segment_lines(block)
        if not segs:
            return [lat]
        dists = {0.0, float(lat.length)}
        for sm in segs:
            self._intersection_along_distances(lat, sm, dists)
        dists = self._merge_sorted_distances(dists)
        out = []
        min_len = 0.02
        for i in range(len(dists) - 1):
            a, b = dists[i], dists[i + 1]
            if b - a < min_len:
                continue
            try:
                piece = substring(lat, a, b)
            except Exception:
                continue
            if piece.is_empty or piece.length < 1e-6:
                continue
            out.append(piece)
        return out if out else [lat]

    def _colored_spans_for_lateral_wings(self, lat: LineString, block, conn_dist: float):
        """Підвідрізки вздовж латераля: межі сабмейнів + точка врізки (крило 1 до врізки / крило 2 після)."""
        if lat.is_empty or lat.length < 1e-9:
            return []
        L = float(lat.length)
        conn_dist = max(0.0, min(L, float(conn_dist)))
        dists = {0.0, conn_dist, L}
        for sm in self._submain_segment_lines(block):
            self._intersection_along_distances(lat, sm, dists)
        dists = self._merge_sorted_distances(dists)
        out = []
        min_len = 0.02
        for i in range(len(dists) - 1):
            a, b = dists[i], dists[i + 1]
            if b - a < min_len:
                continue
            mid = 0.5 * (a + b)
            wing = 1 if mid < conn_dist - 1e-9 else 2
            try:
                piece = substring(lat, a, b)
            except Exception:
                continue
            if piece.is_empty or piece.length < 1e-6:
                continue
            out.append((piece, wing))
        return out

    @staticmethod
    def _audit_wing_line_color(st: str, base_ok: str) -> str:
        if st in (None, "", "no_emitters"):
            return base_ok
        if st == "overflow":
            return "#FF4444"
        if st == "underflow":
            return "#E8C547"
        if st == "both":
            return "#FF6600"
        return base_ok

    def _safe_active_block_idx(self):
        """Індекс активного блоку з панелі; None якщо блоків немає."""
        n = len(self.field_blocks)
        if n <= 0:
            return None
        try:
            i = int(self.var_active_block_idx.get())
        except (tk.TclError, ValueError, TypeError):
            i = 0
        return max(0, min(n - 1, i))

    def _bad_pressure_emitter_details_active_block(self):
        """
        Крапельниці активного блоку з H поза діапазоном (як у гідравліці, ±0,02 м).
        Повертає словник: items[{wx, wy, color, line}], had_block_emits, bi, h_lo, h_hi, band_on, has_calc, tol.
        """
        tol = 0.02
        out = {
            "items": [],
            "had_block_emits": False,
            "bi": 0,
            "h_lo": 0.0,
            "h_hi": 0.0,
            "band_on": False,
            "has_calc": False,
            "tol": tol,
        }
        if not self.field_blocks:
            return out
        bi = self._safe_active_block_idx()
        out["bi"] = bi
        try:
            h_lo = float(self.var_emit_h_press_min.get().replace(",", "."))
        except Exception:
            h_lo = 0.0
        try:
            h_hi = float(self.var_emit_h_press_max.get().replace(",", "."))
        except Exception:
            h_hi = 0.0
        out["h_lo"], out["h_hi"] = h_lo, h_hi
        band_on = (h_lo > 1e-9) or (h_hi > 1e-9)
        out["band_on"] = band_on
        has_calc = bool(self.calc_results.get("sections") or self.calc_results.get("emitters"))
        out["has_calc"] = has_calc
        if not band_on or not has_calc:
            return out

        em_db = self.calc_results.get("emitters") or {}
        lpa = self.calc_results.get("lateral_pressure_audit") or {}
        lat_list = self._flatten_all_lats()
        lateral_bi = self._lateral_block_indices()
        sm_for_conn = self._hydraulic_submain_lines()

        def _lat_sort_key(kv):
            k = str(kv[0])
            if not k.startswith("lat_"):
                return 10**9
            try:
                return int(k.split("_", 1)[1])
            except (ValueError, IndexError):
                return 10**9

        for key, pay in sorted(em_db.items(), key=_lat_sort_key):
            if not str(key).startswith("lat_"):
                continue
            try:
                li = int(str(key).split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            if li < 0 or li >= len(lat_list):
                continue
            if li < len(lateral_bi):
                row_bi = int(lateral_bi[li])
            else:
                row_bi = int((lpa.get(f"lat_{li}") or {}).get("block_idx", -1))
            if row_bi != int(bi):
                continue
            lat = lat_list[li]
            if lat.is_empty or lat.length < 1e-6:
                continue
            try:
                conn = lat_sol.connection_distance_along_lateral(
                    lat, sm_for_conn, snap_m=self._submain_lateral_snap_m()
                )
            except Exception:
                conn = 0.0
            conn = max(0.0, min(float(lat.length), float(conn)))
            for row in (pay.get("L1") or []) + (pay.get("L2") or []):
                if float(row.get("q_emit", 0)) > 1e-4:
                    out["had_block_emits"] = True
                    break

            def _wing_scan(rows, wing_label: str, sign_xa: float):
                for row in rows or []:
                    qe = float(row.get("q_emit", 0))
                    if qe <= 1e-4:
                        continue
                    h_em = float(row.get("h", 0))
                    xa = float(row.get("x", 0))
                    ov = h_hi > 1e-9 and h_em > h_hi + tol
                    un = h_lo > 1e-9 and h_em < h_lo - tol
                    if not ov and not un:
                        continue
                    if ov and un:
                        color = "#FF6600"
                        issue = "перелив і недолив"
                    elif ov:
                        color = "#FF4444"
                        issue = f"перелив (H макс. {h_hi:.2f} м)"
                    else:
                        color = "#E8C547"
                        issue = f"недолив (H мін. {h_lo:.2f} м)"
                    along = conn + sign_xa * xa
                    along = max(0.0, min(float(lat.length), float(along)))
                    try:
                        pt = lat.interpolate(along)
                    except Exception:
                        continue
                    line = (
                        f"Лат. {li + 1} · {wing_label} · від врізки x={xa:.2f} м · "
                        f"H={h_em:.2f} м · Q={qe:.3f} л/г → {issue}"
                    )
                    out["items"].append(
                        {
                            "wx": float(pt.x),
                            "wy": float(pt.y),
                            "color": color,
                            "line": line,
                            "overflow": bool(ov),
                            "underflow": bool(un),
                        }
                    )

            _wing_scan(pay.get("L1"), "L1", -1.0)
            _wing_scan(pay.get("L2"), "L2", 1.0)

        return out

    def _emitter_q_samples_for_block_index(self, bi: int) -> list:
        """Точки (x, y, q_emit л/г) для IDW поля виливу в межах блоку — ізолінія Q ном."""
        em_db = self.calc_results.get("emitters") or {}
        lpa = self.calc_results.get("lateral_pressure_audit") or {}
        lat_list = self._flatten_all_lats()
        lateral_bi = self._lateral_block_indices()
        sm_for_conn = self._hydraulic_submain_lines()
        out = []

        def _lat_sort_key(kv):
            k = str(kv[0])
            if not k.startswith("lat_"):
                return 10**9
            try:
                return int(k.split("_", 1)[1])
            except (ValueError, IndexError):
                return 10**9

        for key, pay in sorted(em_db.items(), key=_lat_sort_key):
            if not str(key).startswith("lat_"):
                continue
            try:
                li = int(str(key).split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            if li < 0 or li >= len(lat_list):
                continue
            if li < len(lateral_bi):
                row_bi = int(lateral_bi[li])
            else:
                row_bi = int((lpa.get(f"lat_{li}") or {}).get("block_idx", -1))
            if row_bi != int(bi):
                continue
            lat = lat_list[li]
            if lat.is_empty or lat.length < 1e-6:
                continue
            try:
                conn = lat_sol.connection_distance_along_lateral(
                    lat, sm_for_conn, snap_m=self._submain_lateral_snap_m()
                )
            except Exception:
                conn = 0.0
            conn = max(0.0, min(float(lat.length), float(conn)))
            for rows, sign_xa in ((pay.get("L1"), -1.0), (pay.get("L2"), 1.0)):
                for row in rows or []:
                    qe = float(row.get("q_emit", 0))
                    if qe <= 1e-4:
                        continue
                    xa = float(row.get("x", 0))
                    along = conn + sign_xa * xa
                    along = max(0.0, min(float(lat.length), float(along)))
                    try:
                        pt = lat.interpolate(along)
                        out.append((float(pt.x), float(pt.y), float(qe)))
                    except Exception:
                        pass
        if len(out) > 2800:
            step = max(1, len(out) // 2800)
            out = out[::step]
        return out

    def _union_overflow_with_q_nom_contour(
        self, clipped, bi: int, block_poly, es: float
    ):
        """
        Зона переливу з’єднується з смугою вздовж ізолінії Q = Q ном (номінальний вилив),
        щоб внутрішня межа маски виходила з контуру номінального виливу.
        """
        if clipped is None or clipped.is_empty:
            return clipped
        try:
            q_nom = float(self.var_emit_flow.get().replace(",", "."))
        except Exception:
            return clipped
        if q_nom <= 1e-6:
            return clipped
        pts_q = self._emitter_q_samples_for_block_index(bi)
        if len(pts_q) < 8:
            return clipped
        lo_q = min(p[2] for p in pts_q)
        hi_q = max(p[2] for p in pts_q)
        if not (lo_q - 1e-4 <= q_nom <= hi_q + 1e-4):
            return clipped
        nq = len(pts_q)
        if nq < 900:
            grid_m = 8.0
        elif nq < 1600:
            grid_m = 10.0
        elif nq < 2400:
            grid_m = 13.0
        else:
            grid_m = 16.0
        ck = (
            int(bi),
            id(self.calc_results),
            round(float(q_nom), 4),
            round(float(grid_m), 2),
            round(lo_q, 4),
            round(hi_q, 4),
            int(nq),
        )
        cache_lines = getattr(self, "_q_nom_contour_line_cache", None)
        if not isinstance(cache_lines, dict):
            cache_lines = {}
        gn = cache_lines.get(ck)
        if gn is None or gn.is_empty:
            tpe = TopoEngine()
            tpe.power = 2.0
            try:
                contours = tpe.generate_contours(
                    boundary=block_poly,
                    step_z=1.0,
                    grid_size=grid_m,
                    elevation_points=pts_q,
                    fixed_z_levels=[q_nom],
                ) or []
            except Exception:
                return clipped
            if not contours:
                return clipped
            gn = contours[0].get("geom")
            if gn is not None and not gn.is_empty:
                cache_lines[ck] = gn
                if len(cache_lines) > 14:
                    cache_lines.clear()
                self._q_nom_contour_line_cache = cache_lines
        if gn is None or gn.is_empty:
            return clipped
        band = max(0.1, min(0.5, 0.26 * es))
        try:
            strip = gn.buffer(band, quad_segs=4)
            merged = unary_union([clipped, strip])
            if block_poly is not None and not block_poly.is_empty:
                merged = merged.intersection(block_poly)
            if merged.is_empty:
                return clipped
            return merged
        except Exception:
            return clipped

    def refresh_block_out_of_range_emitters_panel(self):
        """Вкладка «Блок»: перелік крапельниць активного блоку з H поза діапазоном (як у гідравліці)."""
        cp = getattr(self, "control_panel", None)
        txtw = getattr(cp, "txt_block_bad_emitters", None) if cp is not None else None
        if txtw is None:
            return
        try:
            txtw.config(state=tk.NORMAL)
            txtw.delete("1.0", tk.END)
        except tk.TclError:
            return
        if not self.field_blocks:
            txtw.insert(tk.END, "Немає блоків поля.")
            txtw.config(state=tk.DISABLED)
            return
        b = self._bad_pressure_emitter_details_active_block()
        bi = b["bi"]
        if not b["band_on"]:
            txtw.insert(
                tk.END,
                "Задайте H мін. / H макс. на панелі «Гідравліка» (робочий діапазон тиску на крапельниці), "
                "щоб перевіряти відхилення.",
            )
            txtw.config(state=tk.DISABLED)
            return
        if not b["has_calc"]:
            txtw.insert(tk.END, "Ще немає гідравлічного розрахунку — список з’явиться після «Розрахунок».")
            txtw.config(state=tk.DISABLED)
            return
        hdr = (
            f"Активний блок {bi + 1}. Допуск ±{b['tol']} м (як у звіті розрахунку).\n"
            f"Діапазон: [{b['h_lo']:.2f} … {b['h_hi']:.2f}] м.\n\n"
        )
        if not b["items"]:
            blk = self.field_blocks[bi]
            n_lat_b = len(blk.get("auto_laterals") or []) + len(blk.get("manual_laterals") or [])
            if n_lat_b > 0 and not b["had_block_emits"]:
                txtw.insert(
                    tk.END,
                    hdr
                    + "Немає даних крапельниць для цього блоку в поточному розрахунку "
                    "(виконайте «Розрахунок» для активного блоку або повне поле).",
                )
            else:
                txtw.insert(tk.END, hdr + "Усі крапельниці цього блоку в межах діапазону.")
        else:
            txtw.insert(tk.END, hdr + "\n".join(it["line"] for it in b["items"]))
        try:
            txtw.see("1.0")
        except tk.TclError:
            pass
        txtw.config(state=tk.DISABLED)

    @staticmethod
    def _decimate_closed_ring_xy(
        ring_coords: list,
        min_step: float,
        max_vertices: int = 280,
    ) -> list:
        """Менше вершин на контурі маски — швидший Tk Canvas при pan/zoom."""
        if len(ring_coords) < 4:
            return ring_coords
        closed = ring_coords[0] == ring_coords[-1]
        pts = list(ring_coords[:-1] if closed else ring_coords)
        if len(pts) < 3:
            return ring_coords
        step = float(max(min_step, 1e-6))
        out = None
        for _ in range(14):
            out = [pts[0]]
            for i in range(1, len(pts)):
                x, y = pts[i]
                px, py = out[-1]
                if math.hypot(x - px, y - py) >= step:
                    out.append((x, y))
            nv = len(out) + (1 if closed else 0)
            if nv <= max_vertices and len(out) >= 3:
                break
            step *= 1.38
        if out is None or len(out) < 3:
            return ring_coords
        if closed:
            if out[0] != out[-1]:
                out = out + [out[0]]
        return out

    def _pressure_zone_geom_cache_key(self, bun: dict) -> Optional[tuple]:
        items = bun.get("items") or []
        if not items or not self.field_blocks:
            return None
        bi = int(bun.get("bi", 0))
        if bi < 0 or bi >= len(self.field_blocks):
            return None
        h = hashlib.sha256()
        for it in sorted(
            items,
            key=lambda x: (float(x["wx"]), float(x["wy"])),
        ):
            h.update(
                f"{float(it['wx']):.5f},{float(it['wy']):.5f}|".encode("utf-8")
            )
        try:
            es = float(self.var_emit_step.get().replace(",", "."))
        except Exception:
            es = 0.3
        es = round(max(0.12, min(2.0, es)), 4)
        ring = self.field_blocks[bi].get("ring") or []
        hr = hashlib.sha256(
            "".join(f"{float(p[0]):.4f}:{float(p[1]):.4f};" for p in ring).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        try:
            qn = float(self.var_emit_flow.get().replace(",", "."))
        except Exception:
            qn = 0.0
        qn = round(max(0.0, qn), 4)
        return (id(self.calc_results), bi, h.hexdigest()[:40], es, hr, qn)

    def _lighten_pressure_zone_geometry(self, clipped, es: float):
        """Спрощення + прорідження вершин для швидкого малювання."""
        if clipped is None or clipped.is_empty:
            return clipped
        tol = max(0.28, min(2.2, 0.95 * es))
        try:
            g = clipped.simplify(tol, preserve_topology=True)
            if g.is_empty:
                g = clipped
        except Exception:
            g = clipped
        min_decimate = max(0.12, min(0.85, 0.35 * es))

        def polys_of(gm):
            t = gm.geom_type
            if t == "Polygon":
                return [gm]
            if t == "MultiPolygon":
                return list(gm.geoms)
            if t == "GeometryCollection":
                acc = []
                for sub in gm.geoms:
                    acc.extend(polys_of(sub))
                return acc
            return []

        rebuilt = []
        for pl in polys_of(g):
            if pl.geom_type != "Polygon" or pl.is_empty:
                continue
            dec = self._decimate_closed_ring_xy(
                list(pl.exterior.coords),
                min_decimate,
                max_vertices=260,
            )
            if len(dec) < 4:
                continue
            try:
                rebuilt.append(Polygon(dec))
            except Exception:
                continue
        if not rebuilt:
            return g
        if len(rebuilt) == 1:
            out = rebuilt[0]
        else:
            out = MultiPolygon(rebuilt)
        try:
            out = out.buffer(0)
        except Exception:
            pass
        return out if not out.is_empty else g

    def _bad_emitter_pressure_zone_clipped(self, bun: dict):
        """
        Силует «хмари» поганих крапельниць: одна зовнішня межа без дірок усередині.
        Диски навколо точок зливаються; додатковий buffer з’єднує сусідні ряди в один контур,
        без внутрішніх обручів (які виглядали б як окремі емітери). Обрізка — полігоном блоку.
        Геометрія кешується між redraw (pan/zoom); контур спрощується для Canvas.
        """
        items = bun.get("items") or []
        if not items or not self.field_blocks:
            return None
        bi = int(bun.get("bi", 0))
        if bi < 0 or bi >= len(self.field_blocks):
            return None
        ckey = self._pressure_zone_geom_cache_key(bun)
        if ckey is not None:
            cache = getattr(self, "_pressure_zone_geom_cache", None) or {}
            hit = cache.get(ckey)
            if hit is not None and not hit.is_empty:
                return hit
        pts = [(float(it["wx"]), float(it["wy"])) for it in items]
        block_poly = self._block_poly(self.field_blocks[bi])
        if block_poly.is_empty:
            return None
        if not block_poly.is_valid:
            try:
                block_poly = block_poly.buffer(0)
            except Exception:
                return None
        try:
            es = float(self.var_emit_step.get().replace(",", "."))
        except Exception:
            es = 0.3
        es = max(0.12, min(2.0, es))
        r = max(0.22, min(0.7, 0.62 * es))
        merge_m = max(0.42, min(2.8, 2.05 * es))
        # Менше сегментів на дузі — менше вершин після union/buffer.
        qseg = 4
        try:
            if len(pts) == 1:
                base = Point(pts[0]).buffer(r, quad_segs=qseg)
            else:
                base = MultiPoint(pts).buffer(r, quad_segs=qseg)
            if base.is_empty:
                return None
            if base.geom_type == "GeometryCollection":
                polys = [
                    g
                    for g in base.geoms
                    if getattr(g, "geom_type", "") == "Polygon"
                ]
                if not polys:
                    return None
                base = unary_union(polys)
            try:
                base = base.buffer(0)
            except Exception:
                pass
            base = base.buffer(merge_m, quad_segs=qseg)
            if base.is_empty:
                return None
            clipped = base.intersection(block_poly)
        except Exception:
            return None
        if clipped is None or clipped.is_empty:
            return None
        overflow_only = bool(
            items and all(bool(it.get("overflow")) for it in items)
        )
        if overflow_only:
            clipped = self._union_overflow_with_q_nom_contour(
                clipped, bi, block_poly, es
            )
        if clipped is None or clipped.is_empty:
            return None
        clipped = self._lighten_pressure_zone_geometry(clipped, es)
        if clipped is None or clipped.is_empty:
            return None
        if ckey is not None:
            cache = getattr(self, "_pressure_zone_geom_cache", None)
            if cache is None or not isinstance(cache, dict):
                cache = {}
            if len(cache) > 16:
                cache.clear()
            cache[ckey] = clipped
            self._pressure_zone_geom_cache = cache
        return clipped

    def _draw_emitter_pressure_zone_on_canvas(
        self,
        geom,
        outline: str = "#FFBB66",
        width: int = 2,
        canvas_tag: str = "bad_emit_pressure_zone",
        dash_pattern: Optional[Tuple[int, ...]] = None,
        outline_exterior_only: bool = True,
    ):
        """Контур Polygon / MultiPolygon / LineString після обрізки блоком.
        outline_exterior_only: не малювати внутрішні кільця (дірки) — лише зовнішній силует «хмари».
        dash_pattern: None — суцільна лінія контуру."""

        def collect_polygons(g):
            if g is None or g.is_empty:
                return []
            t = g.geom_type
            if t == "Polygon":
                return [g]
            if t == "MultiPolygon":
                return list(g.geoms)
            if t == "GeometryCollection":
                acc = []
                for sub in g.geoms:
                    acc.extend(collect_polygons(sub))
                return acc
            return []

        def collect_lines(g):
            if g is None or g.is_empty:
                return []
            t = g.geom_type
            if t == "LineString":
                return [g]
            if t == "MultiLineString":
                return list(g.geoms)
            if t == "GeometryCollection":
                acc = []
                for sub in g.geoms:
                    acc.extend(collect_lines(sub))
                return acc
            return []

        poly_kw = {"tags": canvas_tag}
        if dash_pattern is not None:
            poly_kw["dash"] = dash_pattern
        line_kw = {"fill": outline, "width": width, "tags": canvas_tag}
        if dash_pattern is not None:
            line_kw["dash"] = dash_pattern
        canvas_bg = "#121212"
        try:
            canvas_bg = self.canvas.cget("bg") or canvas_bg
        except tk.TclError:
            pass

        for pl in collect_polygons(geom):
            ext = list(pl.exterior.coords)
            flat = []
            for xy in ext:
                flat.extend(self.to_screen(float(xy[0]), float(xy[1])))
            if len(flat) >= 6:
                try:
                    self.canvas.create_polygon(
                        flat,
                        fill="",
                        outline=outline,
                        width=width,
                        **poly_kw,
                    )
                except tk.TclError:
                    self.canvas.create_polygon(
                        flat,
                        fill=canvas_bg,
                        outline=outline,
                        width=width,
                        **poly_kw,
                    )
            if not outline_exterior_only:
                for intr in pl.interiors:
                    ic = list(intr.coords)
                    f2 = []
                    for xy in ic:
                        f2.extend(self.to_screen(float(xy[0]), float(xy[1])))
                    if len(f2) >= 4:
                        self.canvas.create_line(
                            *f2,
                            fill=outline,
                            width=max(1, width - 1),
                            dash=(3, 4),
                            tags=canvas_tag,
                        )

        for ln in collect_lines(geom):
            f3 = []
            for xy in ln.coords:
                f3.extend(self.to_screen(float(xy[0]), float(xy[1])))
            if len(f3) >= 4:
                self.canvas.create_line(*f3, **line_kw)

    def _refresh_active_block_combo(self):
        n = len(self.field_blocks)
        vals = [f"Блок {j + 1}" for j in range(n)]
        self.cb_active_block["values"] = vals
        if n == 0:
            self.cb_active_block.set("")
            self.cb_active_block.config(state="disabled")
            if hasattr(self, "control_panel"):
                self.control_panel.sync_hydro_clear_block_selector()
                self.control_panel.sync_report_block_selector()
            self.refresh_block_out_of_range_emitters_panel()
            return
        self.cb_active_block.config(state="readonly")
        i = self._safe_active_block_idx()
        self.var_active_block_idx.set(i)
        self.cb_active_block.set(vals[i])
        self.var_hydro_clear_block.set(str(i + 1))
        self.sync_hydro_pipe_summary()
        if hasattr(self, "control_panel"):
            self.control_panel.sync_hydro_clear_block_selector()
            self.control_panel.sync_report_block_selector()
        self.refresh_block_out_of_range_emitters_panel()

    def _on_active_block_combo(self, _event=None):
        vals = list(self.cb_active_block.cget("values") or [])
        cur = self.cb_active_block.get()
        try:
            idx = vals.index(cur)
        except ValueError:
            idx = 0
        self.var_active_block_idx.set(idx)
        # Перемикання блоку завершує незавершене перетягування підпису.
        self._moving_section_label_key = None
        self._moving_section_label_sub_idx = None
        self._moving_section_label_sm_idx = None
        self._moving_section_label_preview = None
        self.sync_hydro_pipe_summary()
        self.redraw()

    def open_submain_segment_editor(self):
        from main_app.ui.submain_segment_editor import open_submain_segment_editor as _open_sm_editor

        _open_sm_editor(self)

    @staticmethod
    def _default_trunk_tree_payload():
        return {
            "source_id": "SRC",
            "source_head_m": 30.0,
            "nodes": [
                {"id": "SRC", "kind": "source", "q_demand_m3s": 0.0},
                {"id": "C1", "kind": "consumption", "q_demand_m3s": 0.002},
            ],
            "edges": [
                {
                    "parent_id": "SRC",
                    "child_id": "C1",
                    "length_m": 100.0,
                    "d_inner_mm": 90.0,
                    "c_hw": 140.0,
                    "dz_m": 0.0,
                }
            ],
        }

    def _normalize_trunk_tree_payload(self, payload):
        if not isinstance(payload, dict):
            return self._default_trunk_tree_payload()
        src_id = str(payload.get("source_id", "SRC")).strip() or "SRC"
        try:
            src_head = float(payload.get("source_head_m", 30.0))
        except Exception:
            src_head = 30.0
        nodes_in = payload.get("nodes")
        if not isinstance(nodes_in, list):
            nodes_in = []
        nodes = []
        for row in nodes_in:
            if not isinstance(row, dict):
                continue
            nid = str(row.get("id", "")).strip()
            kind = str(row.get("kind", "")).strip().lower()
            if not nid:
                continue
            try:
                qd = float(row.get("q_demand_m3s", 0.0))
            except Exception:
                qd = 0.0
            nodes.append({"id": nid, "kind": kind, "q_demand_m3s": qd})
        edges_in = payload.get("edges")
        if not isinstance(edges_in, list):
            edges_in = []
        edges = []
        for row in edges_in:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("parent_id", "")).strip()
            cid = str(row.get("child_id", "")).strip()
            if not pid or not cid:
                continue
            try:
                lm = float(row.get("length_m", 0.0))
            except Exception:
                lm = 0.0
            try:
                dmm = float(row.get("d_inner_mm", 0.0))
            except Exception:
                dmm = 0.0
            try:
                chw = float(row.get("c_hw", 140.0))
            except Exception:
                chw = 140.0
            try:
                dz = float(row.get("dz_m", 0.0))
            except Exception:
                dz = 0.0
            edges.append(
                {
                    "parent_id": pid,
                    "child_id": cid,
                    "length_m": lm,
                    "d_inner_mm": dmm,
                    "c_hw": chw,
                    "dz_m": dz,
                }
            )
        if not nodes:
            return self._default_trunk_tree_payload()
        return {
            "source_id": src_id,
            "source_head_m": src_head,
            "nodes": nodes,
            "edges": edges,
        }

    def run_trunk_tree_calculation(self, show_ok_dialog=True):
        payload = self._normalize_trunk_tree_payload(self.trunk_tree_data)
        self.trunk_tree_data = payload
        spec = TrunkTreeSpec(
            nodes=tuple(
                TrunkTreeNode(
                    id=n["id"],
                    kind=n["kind"],
                    q_demand_m3s=float(n.get("q_demand_m3s", 0.0)),
                )
                for n in payload.get("nodes", [])
            ),
            edges=tuple(
                TrunkTreeEdge(
                    parent_id=e["parent_id"],
                    child_id=e["child_id"],
                    length_m=float(e.get("length_m", 0.0)),
                    d_inner_mm=float(e.get("d_inner_mm", 0.0)),
                    c_hw=float(e.get("c_hw", 140.0)),
                    dz_m=float(e.get("dz_m", 0.0)),
                )
                for e in payload.get("edges", [])
            ),
            source_id=str(payload.get("source_id", "SRC")),
            source_head_m=float(payload.get("source_head_m", 30.0)),
        )
        result = compute_trunk_tree_steady(spec)
        self.trunk_tree_results = {
            "issues": list(result.issues),
            "total_q_m3s": float(result.total_q_m3s),
            "node_head_m": dict(result.node_head_m),
            "edges": [
                {
                    "parent_id": e.parent_id,
                    "child_id": e.child_id,
                    "q_m3s": float(e.q_m3s),
                    "length_m": float(e.length_m),
                    "d_inner_mm": float(e.d_inner_mm),
                    "c_hw": float(e.c_hw),
                    "head_loss_m": float(e.head_loss_m),
                    "dz_m": float(e.dz_m),
                    "h_upstream_m": float(e.h_upstream_m),
                    "h_downstream_m": float(e.h_downstream_m),
                    "velocity_m_s": float(e.velocity_m_s),
                }
                for e in result.edges
            ],
        }
        if show_ok_dialog:
            if result.issues:
                messagebox.showwarning(
                    "Магістраль-дерево",
                    "Є помилки валідації:\n- " + "\n- ".join(result.issues),
                )
            else:
                messagebox.showinfo(
                    "Магістраль-дерево",
                    f"Розрахунок виконано.\nСумарна витрата: {result.total_q_m3s:.6f} м³/с\n"
                    f"Ребер: {len(result.edges)}; вузлів: {len(result.node_head_m)}",
                )
        return result

    def open_trunk_tree_editor(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Магістраль-дерево (JSON)")
        dlg.geometry("960x700")
        dlg.configure(bg="#1e1e1e")
        dlg.transient(self.root)

        top = tk.Frame(dlg, bg="#1e1e1e")
        top.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(
            top,
            text="Опис дерева магістралі у JSON (source/nodes/edges).",
            bg="#1e1e1e",
            fg="#00FFCC",
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        txt = tk.Text(
            dlg,
            bg="#161616",
            fg="#d8d8d8",
            insertbackground="#d8d8d8",
            font=("Consolas", 10),
            wrap="none",
        )
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        sx = tk.Scrollbar(txt, orient=tk.HORIZONTAL, command=txt.xview)
        sy = tk.Scrollbar(txt, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        sx.pack(side=tk.BOTTOM, fill=tk.X)
        txt.insert(
            "1.0",
            json.dumps(self._normalize_trunk_tree_payload(self.trunk_tree_data), ensure_ascii=False, indent=2),
        )

        out = tk.Text(
            dlg,
            height=10,
            bg="#101010",
            fg="#9ad1ff",
            font=("Consolas", 9),
            wrap="word",
            state=tk.DISABLED,
        )
        out.pack(fill=tk.X, padx=10, pady=(0, 10))

        def _print_output(lines):
            out.config(state=tk.NORMAL)
            out.delete("1.0", tk.END)
            out.insert("1.0", "\n".join(lines))
            out.config(state=tk.DISABLED)

        def _parse_and_apply(show_messages=False):
            raw = txt.get("1.0", tk.END).strip()
            if not raw:
                raise ValueError("Порожній JSON.")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as ex:
                raise ValueError(f"JSON: {ex}") from ex
            self.trunk_tree_data = self._normalize_trunk_tree_payload(payload)
            if show_messages:
                self.run_trunk_tree_calculation(show_ok_dialog=True)

        def _run():
            try:
                _parse_and_apply(show_messages=False)
                res = self.run_trunk_tree_calculation(show_ok_dialog=False)
                if res.issues:
                    _print_output(
                        ["ПОМІЛКИ ВАЛІДАЦІЇ:"] + [f"- {x}" for x in res.issues]
                    )
                else:
                    lines = [
                        f"OK | total_q_m3s={res.total_q_m3s:.6f}",
                        f"nodes={len(res.node_head_m)} edges={len(res.edges)}",
                        "",
                        "Перші вузли (H):",
                    ]
                    for nid, h in list(res.node_head_m.items())[:12]:
                        lines.append(f"- {nid}: {h:.3f} м")
                    _print_output(lines)
            except Exception as ex:
                _print_output([f"Помилка: {ex}"])

        def _apply_close():
            try:
                _parse_and_apply(show_messages=False)
                self.run_trunk_tree_calculation(show_ok_dialog=False)
                dlg.destroy()
            except Exception as ex:
                messagebox.showerror("Магістраль-дерево", str(ex), parent=dlg)

        def _load_template():
            txt.delete("1.0", tk.END)
            txt.insert(
                "1.0",
                json.dumps(self._default_trunk_tree_payload(), ensure_ascii=False, indent=2),
            )
            _print_output(["Завантажено шаблон дерева."])

        btns = tk.Frame(dlg, bg="#1e1e1e")
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))
        _b_tpl = tk.Button(btns, text="Шаблон", command=_load_template, width=12)
        _b_tpl.pack(side=tk.LEFT)
        attach_tooltip(_b_tpl, "Підставити приклад JSON дерева магістралі в поле редагування.")
        _b_run = tk.Button(btns, text="Порахувати", command=_run, width=14)
        _b_run.pack(side=tk.LEFT, padx=6)
        attach_tooltip(_b_run, "Розрахувати тиски/витрати за поточним JSON дерева без закриття вікна.")
        _b_apply = tk.Button(btns, text="Застосувати і закрити", command=_apply_close, width=22)
        _b_apply.pack(side=tk.LEFT, padx=6)
        attach_tooltip(_b_apply, "Застосувати дерево до проєкту, запустити розрахунок магістралі й закрити діалог.")
        _b_close = tk.Button(btns, text="Закрити", command=dlg.destroy, width=12)
        _b_close.pack(side=tk.RIGHT)
        attach_tooltip(_b_close, "Закрити вікно без застосування змін.")

    def _find_block_containing(self, wx, wy, boundary_tol=None):
        """Індекс блоку, що містить точку (включно з межею в межах допуску)."""
        p = Point(wx, wy)
        boundary_tol = boundary_tol or (15.0 / max(self.zoom, 0.01))
        for i, b in enumerate(self.field_blocks):
            poly = self._block_poly(b)
            if poly.is_empty:
                continue
            if poly.contains(p):
                return i
            if poly.boundary.distance(p) <= boundary_tol:
                return i
        return None

    def _find_block_interior(self, wx, wy):
        """Лише внутрішність полігона (для видалення всього блоку)."""
        p = Point(wx, wy)
        for i, b in enumerate(self.field_blocks):
            poly = self._block_poly(b)
            if not poly.is_empty and poly.contains(p):
                return i
        return None

    def _erase_laterals_intersecting_line(self, cutter: LineString):
        """Видалити всі авто/ручні латералі, що перетинають відрізок."""
        self.reset_calc()
        if cutter.is_empty or cutter.length < 1e-9:
            return
        for b in self.field_blocks:
            for key in ("auto_laterals", "manual_laterals"):
                b[key] = [lat for lat in b[key] if not lat.intersects(cutter)]

    def _intersection_params_along_line(self, ls: LineString, other) -> list:
        """Відстані вздовж ls від старту до перетину з іншою геометрією."""
        inter = ls.intersection(other)
        if inter.is_empty:
            return []
        out = []

        def add_pt(pt: Point):
            t = ls.project(pt)
            if 0.02 < t < ls.length - 0.02:
                out.append(t)

        if inter.geom_type == "Point":
            add_pt(inter)
        elif inter.geom_type == "MultiPoint":
            for p in inter.geoms:
                add_pt(p)
        elif inter.geom_type == "LineString":
            for c in inter.coords:
                add_pt(Point(c))
        elif hasattr(inter, "geoms"):
            for g in inter.geoms:
                if g.geom_type == "Point":
                    add_pt(g)
                elif g.geom_type == "LineString":
                    for c in g.coords:
                        add_pt(Point(c))
        return sorted(set(out))

    def _finalize_manual_lat_against_submains(self, coords: list, block_idx: int) -> LineString:
        """
        Підрізати латераль на першому перетині з сабмейном блоку або подовжити від останньої
        точки вздовж напрямку до перетину з сабмейном.
        """
        if len(coords) < 2:
            return LineString(coords)
        ls = LineString(coords)
        if block_idx is None or block_idx >= len(self.field_blocks):
            return ls
        sm_geoms = [LineString(s) for s in self.field_blocks[block_idx]["submain_lines"] if len(s) > 1]
        if not sm_geoms:
            return ls

        cut_ts = []
        for sm in sm_geoms:
            cut_ts.extend(self._intersection_params_along_line(ls, sm))
        cut_ts = sorted(set(cut_ts))

        if cut_ts:
            t_hit = min(cut_ts)
            if t_hit < ls.length - 0.05:
                return substring(ls, 0, max(t_hit, 0.02))
            return ls

        ax, ay = ls.coords[-2]
        bx, by = ls.coords[-1]
        udx, udy = bx - ax, by - ay
        un = math.hypot(udx, udy) or 1.0
        udx, udy = udx / un, udy / un
        far = (bx + udx * 2_000_000.0, by + udy * 2_000_000.0)
        ray = LineString([(bx, by), far])
        best = None
        best_d = float("inf")
        for sm in sm_geoms:
            hit = ray.intersection(sm)
            if hit.is_empty:
                continue
            cand = []
            if hit.geom_type == "Point":
                cand.append((hit.x, hit.y))
            elif hit.geom_type == "MultiPoint":
                cand.extend((p.x, p.y) for p in hit.geoms)
            elif hit.geom_type == "LineString":
                cand.extend(hit.coords)
            elif hasattr(hit, "geoms"):
                for g in hit.geoms:
                    if g.geom_type == "Point":
                        cand.append((g.x, g.y))
                    elif g.geom_type == "LineString":
                        cand.extend(g.coords)
            for q in cand:
                dd = math.hypot(q[0] - bx, q[1] - by)
                if dd > 1e-4 and dd < best_d:
                    best_d = dd
                    best = q
        if best is not None:
            return LineString(list(ls.coords) + [best])
        return ls

    def _submain_has_connected_lateral(self, sm_coords: list) -> bool:
        if len(sm_coords) < 2:
            return False
        sm = LineString(sm_coords)
        tol = self._submain_lateral_snap_m()
        for lat in self._flatten_all_lats():
            if lat.intersects(sm) or lat.distance(sm) <= tol:
                return True
        return False

    def _all_submains_have_connected_laterals(self) -> bool:
        for sm in self._all_submain_lines():
            if len(sm) > 1 and not self._submain_has_connected_lateral(sm):
                return False
        return True

    def clear_all_field_blocks(self):
        self.reset_calc()
        self.field_blocks = []
        self._dir_target_block_idx = None
        self.points = []
        self.is_closed = False
        if self.mode.get() in ("SET_DIR", "SUBMAIN", "DRAW_LAT", "CUT_LATS", "SUB_LABEL"):
            self.mode.set("DRAW")
            self.reset_temp()
        self.var_active_block_idx.set(0)
        self._refresh_active_block_combo()
        self.redraw()

    def proceed_to_set_direction(self):
        if len(self.points) > 0:
            messagebox.showwarning(
                "Увага",
                "Замкніть поточний контур правою кнопкою миші або очистіть чернетку.",
            )
            return
        if not self.field_blocks:
            messagebox.showwarning("Увага", "Немає замкнених блоків поля: намалюйте контур і замкніть ПКМ.")
            return
        self._dir_target_block_idx = None
        self.dir_points = []
        self.mode.set("SET_DIR")
        self.reset_temp()
        self.redraw()

    def on_key_press(self, event):
        char = event.char.lower() if event.char else ""
        foc = self.root.focus_get()
        in_entry = foc and foc.winfo_class() == 'Entry'
        
        if char in ['s', 'і', 'ы', 's']:
            if not in_entry:
                self.toggle_snap()
            return
            
        if event.char.isdigit() or event.char in ".,":
            if not in_entry:
                ent = getattr(self.control_panel, "len_entry", None)
                if ent is not None:
                    ent.focus_set()

    def toggle_snap(self, event=None):
        self.snap_enabled = not self.snap_enabled
        self._snap_point = None
        self.redraw()

    def disable_snap_once(self, event):
        if not self.snap_disabled_next_click:
            self.snap_disabled_next_click = True
            self._snap_point = None
            self.redraw()

    def undo_action(self, event=None):
        if self.action.get() == "DEL":
            return
        m = self.mode.get()
        if m == "DRAW":
            if self.points:
                self.points.pop()
            elif self.field_blocks:
                bi = len(self.field_blocks) - 1
                self._strip_hydro_for_block_keep_others(bi)
                self.field_blocks.pop()
                self._refresh_active_block_combo()
        elif m == "SUBMAIN":
            for bi in range(len(self.field_blocks) - 1, -1, -1):
                b = self.field_blocks[bi]
                if b["submain_lines"]:
                    self._strip_hydro_for_block_keep_others(bi)
                    b["submain_lines"].pop()
                    break
        elif m == "DRAW_LAT":
            for bi in range(len(self.field_blocks) - 1, -1, -1):
                b = self.field_blocks[bi]
                if b["manual_laterals"]:
                    self._strip_hydro_for_block_keep_others(bi)
                    b["manual_laterals"].pop()
                    break
        self.redraw()

    def add_by_length(self, e):
        if self.action.get() == "DEL": return
        try:
            val = float(self.control_panel.len_entry.get().replace(',', '.'))
            if self.points and not self.is_closed and self.mode.get() == "DRAW":
                lx, ly = self.points[-1]
                mx, my = self._last_mouse_world
                ang = math.atan2(my-ly, mx-lx)
                if self.ortho_on.get():
                    if abs(mx-lx) > abs(my-ly): new_p = (lx + (val if mx>lx else -val), ly)
                    else: new_p = (lx, ly + (val if my>ly else -val))
                else: 
                    new_p = (lx + val*math.cos(ang), ly + val*math.sin(ang))
                self.points.append(new_p)
                self.control_panel.len_entry.delete(0, tk.END)
                self.redraw()
        except: pass

    def _invalidate_hydro_ui_active_block_or_all(self):
        """
        Зміна параметрів секцій сабмейну / клапана на панелі: гідравліка скидається лише
        для активного блоку; інші блоки зберігають розрахунок. Якщо блоків немає — повне скидання.
        """
        bi = self._safe_active_block_idx()
        if bi is not None:
            self._strip_hydro_for_block_keep_others(bi)
        else:
            self.reset_calc()
        self.redraw()

    def reset_calc(self):
        self._pressure_zone_geom_cache = {}
        cr = self.calc_results
        if (cr.get("sections") or cr.get("valves") or cr.get("emitters")):
            self.calc_results = {"sections": [], "valves": {}, "emitters": {}, "submain_profiles": {}}
            self.redraw()

    def _lat_index_range_for_block(self, bi: int):
        start = 0
        for j in range(bi):
            b = self.field_blocks[j]
            start += len(b.get("auto_laterals") or []) + len(b.get("manual_laterals") or [])
        b = self.field_blocks[bi]
        n = len(b.get("auto_laterals") or []) + len(b.get("manual_laterals") or [])
        return start, start + n

    def _submain_global_indices_for_block(self, bi: int):
        _, sm_blocks = self._all_submain_lines_with_block_indices()
        return [i for i, bidx in enumerate(sm_blocks) if bidx == bi]

    def _active_block_submains_have_connected_laterals(self) -> bool:
        abi = self._safe_active_block_idx()
        if abi is None or not self.field_blocks:
            return False
        blk = self.field_blocks[abi]
        for sm in blk.get("submain_lines") or []:
            if len(sm) > 1 and not self._submain_has_connected_lateral(sm):
                return False
        return True

    def _strip_hydro_for_block_keep_others(self, bi: int) -> None:
        if not self.field_blocks or bi < 0 or bi >= len(self.field_blocks):
            return
        orig_sm = self._submain_global_indices_for_block(bi)
        lo, hi = self._lat_index_range_for_block(bi)
        cr = self.calc_results
        cr["sections"] = [
            s for s in (cr.get("sections") or []) if int(s.get("block_idx", -1)) != bi
        ]
        valves = dict(cr.get("valves") or {})
        for sm in self.field_blocks[bi].get("submain_lines") or []:
            if sm:
                k = str((round(sm[0][0], 2), round(sm[0][1], 2)))
                valves.pop(k, None)
        cr["valves"] = valves
        em = dict(cr.get("emitters") or {})
        for j in range(lo, hi):
            em.pop(f"lat_{j}", None)
        cr["emitters"] = em
        sp = dict(cr.get("submain_profiles") or {})
        for sm_i in orig_sm:
            sp.pop(str(sm_i), None)
        cr["submain_profiles"] = sp
        smz = dict(cr.get("submain_math_zones") or {})
        for sm_i in orig_sm:
            smz.pop(str(sm_i), None)
        cr["submain_math_zones"] = smz
        lpa = dict(cr.get("lateral_pressure_audit") or {})
        for j in range(lo, hi):
            lpa.pop(f"lat_{j}", None)
        cr["lateral_pressure_audit"] = lpa
        bae = dict(cr.get("block_avg_emit_lph") or {})
        bae.pop(str(bi), None)
        cr["block_avg_emit_lph"] = bae
        cr["section_label_pos"] = {}

    def _remap_partial_hydro_results(self, partial: dict, orig_sm_indices: list, lat_lo: int) -> dict:
        out = copy.deepcopy(partial)
        sm_map = {j: orig_sm_indices[j] for j in range(len(orig_sm_indices))}
        for s in out.get("sections") or []:
            old = int(s.get("sm_idx", -1))
            if old in sm_map:
                s["sm_idx"] = sm_map[old]
        pr = {}
        for k, v in (out.get("submain_profiles") or {}).items():
            try:
                ji = int(k)
            except (TypeError, ValueError):
                continue
            if ji in sm_map:
                pr[str(sm_map[ji])] = v
        out["submain_profiles"] = pr
        mz = {}
        for k, v in (out.get("submain_math_zones") or {}).items():
            try:
                ji = int(k)
            except (TypeError, ValueError):
                continue
            if ji in sm_map:
                mz[str(sm_map[ji])] = v
        out["submain_math_zones"] = mz
        em = {}
        for k, v in (out.get("emitters") or {}).items():
            if k.startswith("lat_"):
                try:
                    j = int(k.split("_", 1)[1])
                except (IndexError, ValueError):
                    em[k] = v
                    continue
                em[f"lat_{lat_lo + j}"] = v
            else:
                em[k] = v
        out["emitters"] = em
        la = {}
        for k, v in (out.get("lateral_pressure_audit") or {}).items():
            if k.startswith("lat_"):
                try:
                    j = int(k.split("_", 1)[1])
                except (IndexError, ValueError):
                    la[k] = v
                    continue
                nk = f"lat_{lat_lo + j}"
                la[nk] = copy.deepcopy(v) if isinstance(v, dict) else v
            else:
                la[k] = v
        out["lateral_pressure_audit"] = la
        return out

    def _merge_hydro_slice_into_state(self, remapped: dict) -> None:
        cr = self.calc_results
        cr["sections"] = list(cr.get("sections") or []) + list(remapped.get("sections") or [])
        for key in ("emitters", "valves", "submain_profiles", "submain_math_zones", "lateral_pressure_audit"):
            base = dict(cr.get(key) or {})
            base.update(remapped.get(key) or {})
            cr[key] = base
        bae = dict(cr.get("block_avg_emit_lph") or {})
        bae.update(remapped.get("block_avg_emit_lph") or {})
        cr["block_avg_emit_lph"] = bae
        for key in ("valve_h_max_m_spec", "valve_pressure_within_spec", "lateral_solver_stats"):
            if key in remapped:
                cr[key] = remapped[key]

    def clear_hydro_block(self):
        if not self.field_blocks:
            messagebox.showwarning("Увага", "Немає блоків поля.")
            return
        try:
            num = int(float(self.var_hydro_clear_block.get().replace(",", ".").strip()))
        except (ValueError, TypeError):
            messagebox.showwarning("Увага", "Вкажіть номер блоку числом.")
            return
        bi = num - 1
        if bi < 0 or bi >= len(self.field_blocks):
            messagebox.showwarning("Увага", f"Номер блоку від 1 до {len(self.field_blocks)}.")
            return
        secs = list(self.calc_results.get("sections") or [])
        if not secs:
            messagebox.showinfo("Інфо", "Немає збереженого гідравлічного розрахунку.")
            return
        self.calc_results["sections"] = [s for s in secs if int(s.get("block_idx", -1)) != bi]
        valves = dict(self.calc_results.get("valves") or {})
        for sm in self.field_blocks[bi].get("submain_lines") or []:
            if sm:
                k = str((round(sm[0][0], 2), round(sm[0][1], 2)))
                valves.pop(k, None)
        self.calc_results["valves"] = valves
        lo, hi = self._lat_index_range_for_block(bi)
        em = dict(self.calc_results.get("emitters") or {})
        for j in range(lo, hi):
            em.pop(f"lat_{j}", None)
        self.calc_results["emitters"] = em
        self.calc_results["section_label_pos"] = {}
        self.calc_results["submain_profiles"] = {}
        self.redraw()

    def _merged_sections_display(self, sections: list) -> list:
        """Суміжні ділянки з однаковим mat/pn/d на тому ж сабмейні — одна лінія, L додається."""
        if not sections:
            return []
        out = []
        cur = None
        tol = 1e-3

        def spec(s):
            return (s.get("mat"), str(s.get("pn")), s.get("d"), s.get("sm_idx", -1))

        for sec in sections:
            coords = list(sec.get("coords") or [])
            if len(coords) < 2:
                continue
            k = spec(sec)
            if cur is None:
                cur = {
                    "mat": sec["mat"],
                    "pn": sec["pn"],
                    "d": sec["d"],
                    "color": sec.get("color", "#FF3366"),
                    "coords": list(coords),
                    "L": float(sec.get("L", 0)),
                    "block_idx": sec.get("block_idx", 0),
                    "sm_idx": sec.get("sm_idx", -1),
                    "label_key": int(sec.get("section_index", 0)),
                }
                continue
            touch = (
                abs(cur["coords"][-1][0] - coords[0][0]) < tol
                and abs(cur["coords"][-1][1] - coords[0][1]) < tol
            )
            if spec(cur) == k and touch:
                cur["coords"] = cur["coords"][:-1] + list(coords)
                cur["L"] += float(sec.get("L", 0))
            else:
                out.append(cur)
                cur = {
                    "mat": sec["mat"],
                    "pn": sec["pn"],
                    "d": sec["d"],
                    "color": sec.get("color", "#FF3366"),
                    "coords": list(coords),
                    "L": float(sec.get("L", 0)),
                    "block_idx": sec.get("block_idx", 0),
                    "sm_idx": sec.get("sm_idx", -1),
                    "label_key": int(sec.get("section_index", 0)),
                }
        if cur:
            out.append(cur)
        return out

    @staticmethod
    def _turn_angle_deg_at_vertex(p0, p1, p2) -> float:
        """Кут між напрямком p0→p1 і p1→p2 (0° — прямо, 90° — поворот)."""
        ax, ay = p1[0] - p0[0], p1[1] - p0[1]
        bx, by = p2[0] - p1[0], p2[1] - p1[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la < 1e-9 or lb < 1e-9:
            return 0.0
        ax, ay = ax / la, ay / la
        bx, by = bx / lb, by / lb
        dot = max(-1.0, min(1.0, ax * bx + ay * by))
        return math.degrees(math.acos(dot))

    @staticmethod
    def _polyline_length_xy(coords) -> float:
        s = 0.0
        for i in range(len(coords) - 1):
            s += math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
        return s

    def _split_polyline_at_bends(self, coords: list, min_turn_deg: float = 22.0) -> list:
        """Розбити полілінію в місцях різких поворотів (та сама труба, різні прямі ділянки)."""
        if len(coords) < 3:
            return [list(coords)]
        runs = []
        start = 0
        for i in range(1, len(coords) - 1):
            ang = self._turn_angle_deg_at_vertex(coords[i - 1], coords[i], coords[i + 1])
            if ang >= min_turn_deg:
                runs.append(coords[start : i + 1])
                start = i
        runs.append(coords[start:])
        return [list(r) for r in runs if len(r) >= 2]

    def _expand_section_draw_parts(self, sec: dict) -> list:
        """
        Одна злита гідравлічна секція (один d) → кілька відрізків для креслення та підписів
        (довжина L з розрахунку розподіляється пропорційно довжині геометрії кожного шматка).
        """
        coords = list(sec.get("coords") or [])
        if len(coords) < 2:
            return []
        hyd_L = float(sec.get("L", 0))
        lk = int(sec.get("label_key", 0))
        base = {k: sec[k] for k in ("mat", "pn", "d", "color", "block_idx", "sm_idx") if k in sec}

        parts = self._split_polyline_at_bends(coords)
        if len(parts) <= 1:
            return [{**base, "coords": coords, "L": hyd_L, "label_key": lk, "sub_idx": 0}]

        geos = [self._polyline_length_xy(pc) for pc in parts]
        gtot = sum(geos)
        if gtot < 1e-6:
            return [{**base, "coords": coords, "L": hyd_L, "label_key": lk, "sub_idx": 0}]
        out = []
        for si, pc in enumerate(parts):
            L_part = hyd_L * (geos[si] / gtot)
            out.append({**base, "coords": pc, "L": L_part, "label_key": lk, "sub_idx": si})
        return out

    def _sections_for_canvas_draw(self) -> list:
        secs_all = self.calc_results.get("sections") or []
        if not secs_all:
            return []
        if self._any_submain_segment_plan():
            return self._sections_for_canvas_draw_plan_labels(secs_all)
        flat = []
        for sec in self._merged_sections_display(secs_all):
            flat.extend(self._expand_section_draw_parts(sec))
        return flat

    def _section_label_storage_key(self, label_key: int, sub_idx: int, sm_idx: int) -> str:
        return f"{int(sm_idx)}:{int(label_key)}_{int(sub_idx)}"

    def _section_label_lookup_pos(self, label_pts: dict, label_key: int, sub_idx: int, sm_idx: int):
        k_new = self._section_label_storage_key(label_key, sub_idx, sm_idx)
        if k_new in label_pts:
            return label_pts[k_new]
        k_sub = f"{int(label_key)}_{int(sub_idx)}"
        if k_sub in label_pts:
            return label_pts[k_sub]
        if int(sub_idx) == 0:
            t = label_pts.get(str(int(label_key)))
            if t is not None:
                return t
        return None

    def _restore_section_label_positions(self, old_label_pts: dict) -> None:
        """
        Після перерахунку мережі зберігає вручну пересунуті підписи там, де секції
        (sm_idx + section_index + sub_idx) все ще існують.
        """
        if not old_label_pts:
            self.calc_results["section_label_pos"] = {}
            return
        kept = {}
        for sec in self._sections_for_canvas_draw():
            lk = int(sec.get("label_key", 0))
            si = int(sec.get("sub_idx", 0))
            sm = int(sec.get("sm_idx", -1))
            key_new = self._section_label_storage_key(lk, si, sm)
            if key_new in old_label_pts:
                kept[key_new] = old_label_pts[key_new]
                continue
            key_old = f"{lk}_{si}"
            if key_old in old_label_pts:
                kept[key_new] = old_label_pts[key_old]
                continue
            if si == 0:
                key_legacy = str(lk)
                if key_legacy in old_label_pts:
                    kept[key_new] = old_label_pts[key_legacy]
        self.calc_results["section_label_pos"] = kept

    def _try_place_section_label(self, wx, wy):
        parts = self._sections_for_canvas_draw()
        if not parts:
            return
        p = Point(wx, wy)
        pick_m = 10.0
        best_d, best_lk, best_si = None, None, None
        for pr in parts:
            try:
                d = LineString(pr["coords"]).distance(p)
            except Exception:
                continue
            if best_d is None or d < best_d:
                best_d = d
                best_lk = pr["label_key"]
                best_si = int(pr.get("sub_idx", 0))
        if best_d is None or best_d > pick_m:
            return
        best_sm = -1
        for pr in parts:
            if int(pr.get("label_key", -1)) == int(best_lk) and int(pr.get("sub_idx", -1)) == int(best_si):
                best_sm = int(pr.get("sm_idx", -1))
                break
        key = self._section_label_storage_key(int(best_lk), int(best_si), int(best_sm))
        self.calc_results.setdefault("section_label_pos", {})[key] = (float(wx), float(wy))

    def _label_anchor_world(self, sec: dict, label_pts: dict):
        coords = sec.get("coords") or []
        if len(coords) < 2:
            return None
        lk = int(sec.get("label_key", 0))
        si = int(sec.get("sub_idx", 0))
        sm_idx = int(sec.get("sm_idx", -1))
        lp = self._section_label_lookup_pos(label_pts, lk, si, sm_idx)
        if lp:
            return (float(lp[0]), float(lp[1]))
        try:
            geom = LineString(coords)
            midpt = geom.interpolate(0.5, normalized=True)
            return (float(midpt.x), float(midpt.y))
        except Exception:
            return None

    def _pick_section_label_for_move(self, wx: float, wy: float):
        parts_all = self._sections_for_canvas_draw()
        abi = self._safe_active_block_idx()
        if abi is None:
            parts = parts_all
        else:
            parts = [s for s in parts_all if int(s.get("block_idx", -1)) == int(abi)] or parts_all
        if not parts:
            return None
        label_pts = self.calc_results.get("section_label_pos") or {}
        best_d = None
        best = None
        max_pick_anchor_m = max(16.0 / max(self.zoom, 1e-9), 0.35)
        max_pick_line_m = max(14.0 / max(self.zoom, 1e-9), 0.35)
        for sec in parts:
            anchor = self._label_anchor_world(sec, label_pts)
            if anchor is None:
                continue
            d = math.hypot(anchor[0] - wx, anchor[1] - wy)
            if best_d is None or d < best_d:
                best_d = d
                best = (
                    int(sec.get("label_key", 0)),
                    int(sec.get("sub_idx", 0)),
                    int(sec.get("sm_idx", -1)),
                )
        if best_d is None:
            return None
        if best_d > max_pick_anchor_m:
            # fallback: клік по самій ділянці сабмейну, якщо підпис далеко/на авто-позиції
            p = Point(wx, wy)
            best_line_d = None
            best_line = None
            for sec in parts:
                try:
                    d_line = LineString(sec.get("coords") or []).distance(p)
                except Exception:
                    continue
                if best_line_d is None or d_line < best_line_d:
                    best_line_d = d_line
                    best_line = (
                        int(sec.get("label_key", 0)),
                        int(sec.get("sub_idx", 0)),
                        int(sec.get("sm_idx", -1)),
                    )
            if best_line_d is None or best_line_d > max_pick_line_m:
                return None
            return best_line
        return best

    def to_world(self, x, y): return (x - self.offset_x) / self.zoom, (y - self.offset_y) / self.zoom
    def to_screen(self, x, y): return x * self.zoom + self.offset_x, y * self.zoom + self.offset_y

    def get_snap(self, wx, wy):
        if not self.snap_enabled or self.action.get() == "DEL" or self.mode.get() == "TOPO" or self.snap_disabled_next_click: 
            return None
        for vx, vy in self.get_valves():
            if math.hypot(wx - vx, wy - vy) * self.zoom < 25: return (vx, vy)
        closest_pt, min_dist = None, 15 / self.zoom
        targets = []
        for b in self.field_blocks:
            targets.extend(b["ring"])
        targets.extend(self.points)
        for sm in self._all_submain_lines():
            targets.extend(sm)
        for lat in self._flatten_all_lats():
            if lat.coords:
                targets.extend([lat.coords[0], lat.coords[-1]])
        for tx, ty in targets:
            d = math.hypot(wx - tx, wy - ty)
            if d < min_dist: min_dist = d; closest_pt = (tx, ty)
        return closest_pt

    def start_pan(self, event):
        self._pan_start = (event.x, event.y)

    def handle_pan(self, event):
        if self._pan_start:
            self.offset_x += event.x - self._pan_start[0]
            self.offset_y += event.y - self._pan_start[1]
            self._pan_start = (event.x, event.y)
            # Без важких шарів (ізолінії рельєфу / діаграма виливу / маски) — плавна панорама.
            self.redraw(skip_heavy_canvas_layers=True)

    def end_pan(self, event=None):
        self._cancel_debounced_full_redraw()
        if self._pan_start is not None:
            self._pan_start = None
            self.redraw(skip_heavy_canvas_layers=False)

    def _cancel_debounced_full_redraw(self):
        tid = getattr(self, "_full_redraw_idle_id", None)
        if tid is not None:
            try:
                self.root.after_cancel(tid)
            except Exception:
                pass
            self._full_redraw_idle_id = None

    def _schedule_debounced_full_redraw(self, delay_ms: int = 90) -> None:
        self._cancel_debounced_full_redraw()

        def _go():
            self._full_redraw_idle_id = None
            if hasattr(self, "canvas") and self.canvas.winfo_exists():
                self.redraw(skip_heavy_canvas_layers=False)

        self._full_redraw_idle_id = self.root.after(delay_ms, _go)

    def handle_zoom(self, e):
        f = 1.1 if e.delta > 0 else 0.9
        mx, my = self.to_world(e.x, e.y)
        self.zoom *= f
        self.offset_x = e.x - mx * self.zoom
        self.offset_y = e.y - my * self.zoom
        self.redraw(skip_heavy_canvas_layers=True)
        self._schedule_debounced_full_redraw(90)

    def handle_erase(self, wx, wy):
        p = Point(wx, wy)
        m = self.mode.get()
        thresh = 15 / self.zoom
        if m == "DRAW":
            bi_del = self._find_block_interior(wx, wy)
            if bi_del is not None:
                nblk = len(self.field_blocks)
                if nblk <= 1 or bi_del == nblk - 1:
                    self._strip_hydro_for_block_keep_others(bi_del)
                else:
                    self.reset_calc()
                self.field_blocks.pop(bi_del)
                self._refresh_active_block_combo()
                self.redraw()
                return
            if self.points:
                closest = min(self.points, key=lambda pt: math.hypot(pt[0]-wx, pt[1]-wy))
                if math.hypot(closest[0]-wx, closest[1]-wy) < thresh:
                    self.points.remove(closest)
                    if len(self.points) < 3:
                        self.is_closed = False
                    self.redraw()
                    return
            for ri in range(len(self.field_blocks) - 1, -1, -1):
                ring = self.field_blocks[ri]["ring"]
                closest = min(ring, key=lambda pt: math.hypot(pt[0]-wx, pt[1]-wy))
                if math.hypot(closest[0]-wx, closest[1]-wy) < thresh:
                    new_ring = [pt for pt in ring if math.hypot(pt[0]-closest[0], pt[1]-closest[1]) > 1e-9]
                    if len(new_ring) < 3:
                        nblk = len(self.field_blocks)
                        if nblk <= 1 or ri == nblk - 1:
                            self._strip_hydro_for_block_keep_others(ri)
                        else:
                            self.reset_calc()
                        self.field_blocks.pop(ri)
                    else:
                        self.field_blocks[ri]["ring"] = new_ring
                        self._strip_hydro_for_block_keep_others(ri)
                    self._refresh_active_block_combo()
                    break
            self.redraw()
            return
        if m == "SUBMAIN":
            for bi, b in enumerate(self.field_blocks):
                for sm in list(b["submain_lines"]):
                    if LineString(sm).distance(p) < 15 / self.zoom:
                        b["submain_lines"].remove(sm)
                        self._strip_hydro_for_block_keep_others(bi)
                        self.redraw()
                        return
        elif m in ("DRAW_LAT", "SET_DIR", "CUT_LATS"):
            for b in self.field_blocks:
                for key in ("auto_laterals", "manual_laterals"):
                    for lat in list(b[key]):
                        if lat.distance(p) < 15 / self.zoom:
                            b[key].remove(lat)
                            self.reset_calc()
                            self.redraw()
                            return
        self.redraw()

    def show_graph(self, lat_id):
        emitters_db = self.calc_results.get("emitters", {})
        if not emitters_db or lat_id not in emitters_db:
            messagebox.showinfo("Інфо", "Спочатку виконайте розрахунок!")
            return
            
        data = emitters_db[lat_id]
        l1_data = data.get("L1", [])
        l2_data = data.get("L2", [])
        if not l1_data and not l2_data: return
        
        if not hasattr(self, 'graph_window') or not self.graph_window.winfo_exists():
            self.graph_window = tk.Toplevel(self.root)
            self.graph_window.geometry("850x640")
            self.graph_window.configure(bg="#1e1e1e")
            self.graph_canvas = tk.Canvas(self.graph_window, width=800, height=500, bg="#222", highlightthickness=0)
            self.graph_canvas.pack(padx=20, pady=20)
            self.graph_info_label = tk.Label(
                self.graph_window,
                bg="#1e1e1e",
                fg="white",
                font=("Arial", 11, "bold"),
                justify=tk.LEFT,
                wraplength=780,
            )
            self.graph_info_label.pack(fill=tk.X, padx=16)
            
        top = self.graph_window
        lat_idx0 = None
        if isinstance(lat_id, str) and lat_id.startswith("lat_"):
            try:
                lat_idx0 = int(lat_id.split("_", 1)[1])
            except ValueError:
                lat_idx0 = None
        lat_human_n = (lat_idx0 + 1) if lat_idx0 is not None else None
        top.title(
            f"Латераль №{lat_human_n}: гідравлічний профіль ({lat_id})"
            if lat_human_n is not None
            else f"Гідравлічний профіль {lat_id}"
        )
        top.lift(self.root)
        try:
            top.focus_force()
        except tk.TclError:
            pass
        top.attributes("-topmost", True)
        top.after(150, lambda w=top: w.attributes("-topmost", False))
        canvas = self.graph_canvas
        canvas.delete("all")
        
        max_x1 = max([d["x"] for d in l1_data] + [0])
        max_x2 = max([d["x"] for d in l2_data] + [0])
        total_width = max_x1 + max_x2
        if total_width == 0: return
        
        plot_w = 700
        plot_h = 400
        pad_left = 50
        pad_top = 50
        
        zero_x = pad_left + (max_x1 / total_width) * plot_w if total_width > 0 else pad_left

        all_h = [float(d["h"]) for d in l1_data + l2_data]
        # q — витрата в трубі до тупика (л/год), для кривої на одному масштабі
        all_q = [float(d.get("q", 0)) for d in l1_data + l2_data]
        all_elev = [float(d.get("elev", 0.0)) for d in l1_data + l2_data]

        try:
            hpmin = float(self.var_emit_h_press_min.get().replace(",", "."))
            hpmax = float(self.var_emit_h_press_max.get().replace(",", "."))
        except ValueError:
            hpmin, hpmax = 0.0, 0.0
        band_h_active = (hpmin > 1e-9) or (hpmax > 1e-9)

        y_samples = all_h + all_elev
        if band_h_active:
            if hpmin > 1e-9:
                y_samples.append(hpmin)
            if hpmax > 1e-9:
                y_samples.append(hpmax)
        y_min = min(y_samples) if y_samples else 0.0
        y_max = max(y_samples) if y_samples else 12.0
        y_span = y_max - y_min
        pad_y = max(0.5, y_span * 0.08) if y_span > 1e-6 else 1.0
        y_lo = y_min - pad_y
        y_hi = y_max + pad_y
        if y_hi <= y_lo:
            y_hi = y_lo + 12.0

        def y_screen(yv: float) -> float:
            return pad_top + plot_h - ((yv - y_lo) / (y_hi - y_lo)) * plot_h

        axis_bottom = pad_top + plot_h
        canvas.create_line(pad_left, axis_bottom, pad_left + plot_w, axis_bottom, fill="white", width=2)
        canvas.create_line(zero_x, pad_top, zero_x, axis_bottom, fill="gray", width=2, dash=(4, 4))

        if band_h_active and hpmin > 1e-9 and hpmax > 1e-9 and hpmax > hpmin + 1e-9:
            y_a = y_screen(hpmax)
            y_b = y_screen(hpmin)
            canvas.create_rectangle(
                pad_left,
                min(y_a, y_b),
                pad_left + plot_w,
                max(y_a, y_b),
                outline="",
                fill="#1a3d2a",
                stipple="gray25",
            )
        if band_h_active and hpmin > 1e-9:
            yl = y_screen(hpmin)
            canvas.create_line(
                pad_left, yl, pad_left + plot_w, yl, dash=(6, 5), fill="#66FF88", width=2
            )
            canvas.create_text(
                pad_left + plot_w - 4,
                yl - 2,
                text=f"Hmin={hpmin:.2f}",
                fill="#66FF88",
                anchor=tk.E,
                font=("Arial", 8, "bold"),
            )
        if band_h_active and hpmax > 1e-9:
            yl2 = y_screen(hpmax)
            canvas.create_line(
                pad_left, yl2, pad_left + plot_w, yl2, dash=(6, 5), fill="#FF8866", width=2
            )
            canvas.create_text(
                pad_left + plot_w - 4,
                yl2 - 2,
                text=f"Hmax={hpmax:.2f}",
                fill="#FF8866",
                anchor=tk.E,
                font=("Arial", 8, "bold"),
            )

        qmax = max(all_q) if all_q else 0.0
        max_q_val = max(qmax * 1.08 if qmax > 1e-9 else 0.0, 1.5)

        pts_e_1, pts_h_1, pts_q_1 = [], [], []
        for d in l1_data:
            sx = zero_x - (d["x"] / total_width) * plot_w
            el = float(d.get("elev", 0.0))
            pts_e_1.append((sx, y_screen(el)))
            pts_h_1.append((sx, y_screen(float(d["h"]))))
            qv = float(d.get("q", 0))
            pts_q_1.append((sx, pad_top + plot_h - (qv / max_q_val) * plot_h))
            
        pts_e_2, pts_h_2, pts_q_2 = [], [], []
        for d in l2_data:
            sx = zero_x + (d["x"] / total_width) * plot_w
            el = float(d.get("elev", 0.0))
            pts_e_2.append((sx, y_screen(el)))
            pts_h_2.append((sx, y_screen(float(d["h"]))))
            qv = float(d.get("q", 0))
            pts_q_2.append((sx, pad_top + plot_h - (qv / max_q_val) * plot_h))
            
        if len(pts_e_1) > 1:
            canvas.create_line(pts_e_1, fill="#8B6914", width=2, smooth=True)
        if len(pts_e_2) > 1:
            canvas.create_line(pts_e_2, fill="#8B6914", width=2, smooth=True)
        if len(pts_h_1) > 1:
            canvas.create_line(pts_h_1, fill="#FFD700", width=3, smooth=True)
            canvas.create_line(pts_q_1, fill="#00FFCC", width=2, smooth=True)
        if len(pts_h_2) > 1:
            canvas.create_line(pts_h_2, fill="#FFD700", width=3, smooth=True)
            canvas.create_line(pts_q_2, fill="#00FFCC", width=2, smooth=True)

        emit_strip_h = 36
        y_es = pad_top + plot_h - emit_strip_h
        canvas.create_line(pad_left, y_es, pad_left + plot_w, y_es, fill="#444444", width=1)
        canvas.create_text(
            pad_left + 2,
            y_es - 10,
            text="Вилив кр. (л/г)",
            fill="#FFAA66",
            anchor=tk.SW,
            font=("Arial", 7, "bold"),
        )
        try:
            eof = float(self.var_emit_flow.get().replace(",", "."))
            h_ref_e = float(self.var_emit_h_ref.get().replace(",", "."))
            h_min_work = float(self.var_emit_h_min.get().replace(",", "."))
        except ValueError:
            eof, h_ref_e, h_min_work = 1.0, 10.0, 1.0
        comp = bool(self._emitter_compensated_effective())
        hm_work = max(0.05, h_min_work)
        q_lo_b = 0.0
        q_hi_b = 0.0
        if band_h_active and hpmin > 1e-9:
            q_lo_b = lat_sol.emitter_flow_lph(
                max(hpmin, 1e-6),
                eof,
                max(0.05, h_ref_e),
                compensated=comp,
                h_min_work_m=hm_work,
            )
        if band_h_active and hpmax > 1e-9:
            q_hi_b = lat_sol.emitter_flow_lph(
                max(hpmax, 1e-6),
                eof,
                max(0.05, h_ref_e),
                compensated=comp,
                h_min_work_m=hm_work,
            )
        q_em_vals = [
            float(d.get("q_emit", 0))
            for d in l1_data + l2_data
            if float(d.get("q_emit", 0)) > 1e-4
        ]
        q_em_max = (
            max(q_em_vals + [q_lo_b, q_hi_b, 0.08]) * 1.1
            if (q_em_vals or band_h_active)
            else 0.0
        )

        def y_qstrip(qv: float) -> float:
            if q_em_max < 1e-9:
                return y_es + emit_strip_h * 0.5
            t = max(0.0, min(1.0, qv / q_em_max))
            return y_es + emit_strip_h - t * (emit_strip_h - 3)

        if band_h_active and q_em_max > 1e-9:
            canvas.create_line(
                pad_left,
                y_qstrip(q_lo_b),
                pad_left + plot_w,
                y_qstrip(q_lo_b),
                dash=(4, 3),
                fill="#AA6633",
            )
            canvas.create_line(
                pad_left,
                y_qstrip(q_hi_b),
                pad_left + plot_w,
                y_qstrip(q_hi_b),
                dash=(4, 3),
                fill="#AA6633",
            )

        pts_em = []
        for d in l1_data:
            qe = float(d.get("q_emit", 0))
            if qe > 1e-4:
                sx = zero_x - (d["x"] / total_width) * plot_w
                pts_em.append((sx, y_qstrip(qe)))
        for d in l2_data:
            qe = float(d.get("q_emit", 0))
            if qe > 1e-4:
                sx = zero_x + (d["x"] / total_width) * plot_w
                pts_em.append((sx, y_qstrip(qe)))
        if len(pts_em) > 1:
            canvas.create_line(pts_em, fill="#FFAA44", width=2, smooth=True)
        elif len(pts_em) == 1:
            sx, sy = pts_em[0]
            canvas.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill="#FFAA44", outline="")

        canvas.create_text(zero_x, pad_top + plot_h + 20, text="0 м\n(Сабмейн)", fill="white", anchor=tk.N, justify=tk.CENTER)
        if max_x1 > 0: canvas.create_text(pad_left, pad_top + plot_h + 20, text=f"-{max_x1:.1f} м", fill="white", anchor=tk.N)
        if max_x2 > 0: canvas.create_text(pad_left + plot_w, pad_top + plot_h + 20, text=f"+{max_x2:.1f} м", fill="white", anchor=tk.N)
        
        submain_h = float(l1_data[0]["h"]) if l1_data else (float(l2_data[0]["h"]) if l2_data else 10.0)
        sub_q_parts = []
        if l1_data:
            sub_q_parts.append(float(l1_data[0].get("q", 0)))
        if l2_data:
            sub_q_parts.append(float(l2_data[0].get("q", 0)))
        submain_q = sum(sub_q_parts) if sub_q_parts else 0.0
        submain_sy = y_screen(submain_h)
        
        canvas.create_oval(zero_x-4, submain_sy-4, zero_x+4, submain_sy+4, fill="red")
        txt_submain = f"Сабмейн\nH: {submain_h:.1f} м\nQ труби: {submain_q:.0f} л/г"
        canvas.create_text(zero_x + 10, submain_sy - 10, text=txt_submain, fill="white", anchor=tk.W, font=("Arial", 9, "bold"), justify=tk.LEFT)
        
        def _last_emit_q_emit(wing_rows: list) -> float:
            qe = 0.0
            for d in wing_rows:
                if float(d.get("q_emit", 0)) > 1e-4:
                    qe = float(d.get("q_emit", 0))
            return qe

        if l1_data:
            end_x1 = pts_h_1[-1][0]
            end_y1 = pts_h_1[-1][1]
            end_d1 = l1_data[-1]
            end_h1 = float(end_d1["h"])
            end_qp1 = float(end_d1.get("q", 0))
            end_em1 = _last_emit_q_emit(l1_data)
            canvas.create_oval(end_x1-4, end_y1-4, end_x1+4, end_y1+4, fill="red")
            if end_em1 > 1e-4:
                txt_l1 = (
                    f"Тупик\nH: {end_h1:.1f} м\nQ труби: {end_qp1:.0f}\n"
                    f"Вилив ост. крап.: {end_em1:.2f} л/г"
                )
            else:
                txt_l1 = f"Тупик\nH: {end_h1:.1f} м\nQ труби: {end_qp1:.0f} л/г"
            canvas.create_text(end_x1, end_y1 - 10, text=txt_l1, fill="white", anchor=tk.S, font=("Arial", 9, "bold"), justify=tk.CENTER)

        if l2_data:
            end_x2 = pts_h_2[-1][0]
            end_y2 = pts_h_2[-1][1]
            end_d2 = l2_data[-1]
            end_h2 = float(end_d2["h"])
            end_qp2 = float(end_d2.get("q", 0))
            end_em2 = _last_emit_q_emit(l2_data)
            canvas.create_oval(end_x2-4, end_y2-4, end_x2+4, end_y2+4, fill="red")
            if end_em2 > 1e-4:
                txt_l2 = (
                    f"Тупик\nH: {end_h2:.1f} м\nQ труби: {end_qp2:.0f}\n"
                    f"Вилив ост. крап.: {end_em2:.2f} л/г"
                )
            else:
                txt_l2 = f"Тупик\nH: {end_h2:.1f} м\nQ труби: {end_qp2:.0f} л/г"
            canvas.create_text(end_x2, end_y2 - 10, text=txt_l2, fill="white", anchor=tk.S, font=("Arial", 9, "bold"), justify=tk.CENTER)

        canvas.create_text(pad_left, pad_top - 30, text="--- Тиск (H, м)", fill="#FFD700", anchor=tk.W, font=("Arial", 11, "bold"))
        canvas.create_text(pad_left + 150, pad_top - 30, text="--- Поверхня ΔZ (м)", fill="#C4A35A", anchor=tk.W, font=("Arial", 11, "bold"))
        canvas.create_text(
            pad_left + 330,
            pad_top - 30,
            text="--- Q у трубі (л/г)",
            fill="#00FFCC",
            anchor=tk.W,
            font=("Arial", 11, "bold"),
        )

        if lat_human_n is not None:
            canvas.create_text(
                pad_left + plot_w - 6,
                pad_top + 6,
                text=f"Латераль №{lat_human_n}",
                fill="#FFFFFF",
                anchor=tk.NE,
                font=("Arial", 10, "bold"),
            )
        leg_x = pad_left + plot_w - 120
        leg_y = pad_top + 24 if lat_human_n is not None else pad_top + 6
        legend_rows = [
            ("#90EE90", "Норма"),
            ("#E8C547", "Недолив"),
            ("#FF4444", "Перелив"),
        ]
        row_h = 15
        for i, (col, label) in enumerate(legend_rows):
            yy = leg_y + i * row_h
            canvas.create_rectangle(leg_x, yy, leg_x + 10, yy + 10, fill=col, outline="#555555")
            canvas.create_text(leg_x + 14, yy + 5, text=label, fill="#DDDDDD", anchor=tk.W, font=("Arial", 8))
        
        emit_rates = [
            float(d.get("q_emit", 0))
            for d in l1_data + l2_data
            if float(d.get("q_emit", 0)) > 1e-4
        ]
        if len(emit_rates) >= 2:
            q_min, q_max = min(emit_rates), max(emit_rates)
            q_avg = sum(emit_rates) / len(emit_rates)
            eu = (q_min / q_avg) * 100 if q_avg > 0 else 0.0
            qmin_over_qmax_pct = (100.0 * q_min / q_max) if q_max > 1e-9 else 0.0
            spread_pct = (100.0 * (q_max - q_min) / q_max) if q_max > 1e-9 else 0.0
        else:
            eu = None
            qmin_over_qmax_pct = None
            spread_pct = None

        info = ""
        if lat_idx0 is not None:
            aud_st = (self.calc_results.get("lateral_pressure_audit") or {}).get(f"lat_{lat_idx0}")
            if aud_st:
                st = aud_st.get("status", "")
                st_ua = {
                    "ok": "Норма",
                    "underflow": "Недолив",
                    "overflow": "Перелив",
                    "both": "Недолив і перелив",
                    "no_emitters": "Немає крапельниць",
                }.get(st, str(st))
                info = f"Тиск на крапельницях — статус: {st_ua}\n"
        if eu is not None and qmin_over_qmax_pct is not None and spread_pct is not None:
            info += (
                f"Рівномірність виливу (EU): {eu:.1f}%\n"
                f"Qmin/Qmax·100%: {qmin_over_qmax_pct:.1f}%\n"
                f"(Qmax−Qmin)/Qmax·100%: {spread_pct:.1f}%"
            )
        else:
            info += (
                "Рівномірність виливу: н/д (у профілі <2 емітерів із q_emit).\n"
                "Показники Qmin/Qmax не рахуються без реальних точок виливу."
            )
        if band_h_active and hpmin > 1e-9 and hpmax > 1e-9:
            qa, qb = sorted((q_lo_b, q_hi_b))
            info += f"\nОрієнтир виливу при Hmin/Hmax (крива q(H) з панелі): {qa:.3f}…{qb:.3f} л/г"
        elif band_h_active and hpmin > 1e-9:
            info += f"\nОрієнтир виливу при Hmin (модель): {q_lo_b:.3f} л/г"
        elif band_h_active and hpmax > 1e-9:
            info += f"\nОрієнтир виливу при Hmax (модель): {q_hi_b:.3f} л/г"
        if comp:
            info += (
                "\n\nПро «перелив» і компенсатор (x=0 у полі степеня): при H ≥ H мін у вузлі вилив у розрахунку ≈ Q ном (слабо залежить від H). "
                "Колір емітера на мапі та статус тут — від **порівняння фактичного H** у вузлі з полями "
                "«мін./макс. тиск на крапельниці» (вкладка «Гідравліка»), тобто чи потрапляє тиск у ваш робочий коридор."
            )
        else:
            info += (
                "\n\nМодель турбулентної крапельниці: вилив залежить від H (крива «Вилив кр.»). "
                "Смуги Hmin/Hmax на графіку — ваш коридор; «перелив» означає H вище максимуму (тощо)."
            )
        aud = (self.calc_results.get("lateral_pressure_audit") or {}).get(lat_id)
        if aud and aud.get("h_sub_target_m") is not None:
            info += (
                f"\nРекоменд. H біля врізки: {float(aud['h_sub_target_m']):.2f} м "
                f"(зараз {float(aud.get('h_sub_actual_m', 0)):.2f})"
            )
        self.graph_info_label.config(text=info)

    def show_submain_graph(self, sm_idx: int):
        """Графік H(s), Q(s) та рельєфу ΔZ(s) вздовж сабмейну після розрахунку."""
        prof = (self.calc_results.get("submain_profiles") or {}).get(str(int(sm_idx)), [])
        if not prof:
            messagebox.showinfo("Інфо", "Немає профілю сабмейну — спочатку виконайте гідравлічний розрахунок.")
            return

        if not hasattr(self, "graph_window") or not self.graph_window.winfo_exists():
            self.graph_window = tk.Toplevel(self.root)
            self.graph_window.geometry("900x620")
            self.graph_window.configure(bg="#1e1e1e")
            self.graph_canvas = tk.Canvas(self.graph_window, width=840, height=520, bg="#222", highlightthickness=0)
            self.graph_canvas.pack(padx=20, pady=16)
            self.graph_info_label = tk.Label(
                self.graph_window,
                bg="#1e1e1e",
                fg="white",
                font=("Arial", 11),
                justify=tk.LEFT,
                wraplength=780,
            )
            self.graph_info_label.pack(fill=tk.X, padx=16)

        top = self.graph_window
        top.title(f"Сабмейн {int(sm_idx) + 1}: H, Q, рельєф (№ латераля — врізка)")
        top.lift(self.root)
        try:
            top.focus_force()
        except tk.TclError:
            pass
        top.attributes("-topmost", True)
        top.after(150, lambda w=top: w.attributes("-topmost", False))

        canvas = self.graph_canvas
        canvas.delete("all")

        s_max = max(float(p["s"]) for p in prof) or 1.0
        z_ref = float(prof[0]["z"])
        all_h = [float(p["h"]) for p in prof]
        all_q = [float(p["q_m3h"]) for p in prof]
        z_rel = [float(p["z"]) - z_ref for p in prof]

        plot_w = 720
        plot_h = 420
        pad_left = 56
        pad_top = 44

        def x_screen(s: float) -> float:
            return pad_left + (float(s) / s_max) * plot_w

        y_samples = all_h + z_rel
        y_min = min(y_samples) if y_samples else 0.0
        y_max = max(y_samples) if y_samples else 12.0
        y_span = y_max - y_min
        pad_y = max(0.5, y_span * 0.08) if y_span > 1e-6 else 1.0
        y_lo = y_min - pad_y
        y_hi = y_max + pad_y
        if y_hi <= y_lo:
            y_hi = y_lo + 12.0

        def y_screen(yv: float) -> float:
            return pad_top + plot_h - ((yv - y_lo) / (y_hi - y_lo)) * plot_h

        max_q_val = max(max(all_q) * 1.1 if all_q else 1.0, 1.0)

        pts_h = [(x_screen(float(p["s"])), y_screen(float(p["h"]))) for p in prof]
        pts_z = [(x_screen(float(p["s"])), y_screen(zr)) for p, zr in zip(prof, z_rel)]
        pts_q = [
            (x_screen(float(p["s"])), pad_top + plot_h - (float(p["q_m3h"]) / max_q_val) * plot_h)
            for p in prof
        ]

        canvas.create_line(pad_left, pad_top + plot_h, pad_left + plot_w, pad_top + plot_h, fill="white", width=2)
        canvas.create_line(pad_left, pad_top, pad_left, pad_top + plot_h, fill="white", width=2)

        if len(pts_z) > 1:
            canvas.create_line(pts_z, fill="#C4A35A", width=2, smooth=True)
        if len(pts_h) > 1:
            canvas.create_line(pts_h, fill="#FFD700", width=3, smooth=True)
        if len(pts_q) > 1:
            canvas.create_line(pts_q, fill="#00FFCC", width=2, smooth=True)

        sm_i_int = int(sm_idx)
        for li, lat in enumerate(self._flatten_all_lats()):
            conn_sm, s_along = self._lateral_connection_sm_index_and_chainage(lat)
            if conn_sm is None or int(conn_sm) != sm_i_int:
                continue
            xv = x_screen(s_along)
            canvas.create_line(
                xv, pad_top + 2, xv, pad_top + plot_h - 2, fill="#666666", dash=(4, 5), width=1
            )
            aud_sm = (self.calc_results.get("lateral_pressure_audit") or {}).get(f"lat_{li}")
            st_sm = (aud_sm or {}).get("status", "ok")
            lc_sm = {"overflow": "#FF4444", "underflow": "#E8C547", "both": "#FF6600"}.get(
                st_sm, "#90EE90"
            )
            canvas.create_text(xv, pad_top + 12, text=f"№{li + 1}", fill=lc_sm, anchor=tk.N, font=("Arial", 9, "bold"))

        leg_x = pad_left + plot_w - 118
        leg_y = pad_top + 6
        sm_legend = [
            ("#90EE90", "Норма"),
            ("#E8C547", "Недолив"),
            ("#FF4444", "Перелив"),
        ]
        for i, (col, label) in enumerate(sm_legend):
            yy = leg_y + i * 15
            canvas.create_rectangle(leg_x, yy, leg_x + 10, yy + 10, fill=col, outline="#555555")
            canvas.create_text(leg_x + 14, yy + 5, text=label, fill="#DDDDDD", anchor=tk.W, font=("Arial", 8))

        canvas.create_text(pad_left, pad_top - 28, text="— H, м (на початку відрізка)", fill="#FFD700", anchor=tk.W, font=("Arial", 10, "bold"))
        canvas.create_text(pad_left + 260, pad_top - 28, text="— ΔZ відносно початку, м", fill="#C4A35A", anchor=tk.W, font=("Arial", 10, "bold"))
        canvas.create_text(pad_left + 520, pad_top - 28, text="— Q, м³/г (масштаб по висоті)", fill="#00FFCC", anchor=tk.W, font=("Arial", 10, "bold"))

        canvas.create_text(
            pad_left + plot_w / 2,
            pad_top + plot_h + 28,
            text=f"Відстань по сабмейну (0 … {s_max:.1f} м)",
            fill="white",
            font=("Arial", 10, "bold"),
        )

        h_end = all_h[-1] if all_h else 0.0
        info = (
            f"Z у початку сабмейну: {z_ref:.2f} м | H наприкінці: {h_end:.2f} м | "
            f"Q на вході: {all_q[0] if all_q else 0:.1f} м³/г\n"
            "Колір підпису № — статус тиску на крапельницях (як на мапі; помаранчевий — обидва порушення)."
        )
        if bool(self._emitter_compensated_effective()):
            info += (
                "\n\nЯкщо x=0 (режим компенсатора): у робочій зоні H вилив у розрахунку ≈ Q ном, "
                "а червоний/жовтий тут — це вихід **напору H** за межі «мін./макс. тиск на крапельниці» (вкладка «Гідравліка»), "
                "а не суперечність «вилив не залежить від тиску»."
            )
        else:
            info += (
                "\n\nТурбулентна модель: вилив залежить від H; колір — за порівнянням H з вашим коридором на «Гідравліці»."
            )
        self.graph_info_label.config(text=info)

    def _open_lateral_tip_probe_dialog(self, lat_idx: int, lat: LineString):
        sm_lines = self._all_submain_lines()
        if not sm_lines:
            messagebox.showwarning(
                "Увага",
                "Намалюйте сабмейн — потрібен для визначення точки врізки латераля.",
            )
            return
        conn_dist = lat_sol.connection_distance_along_lateral(
            lat, sm_lines, snap_m=self._submain_lateral_snap_m()
        )
        top = tk.Toplevel(self.root)
        top.title(f"Тиск на тупику — латераль {lat_idx}")
        top.configure(bg="#2d2d2d")
        top.geometry("440x380")

        ref_h = None
        ed = self.calc_results.get("emitters", {}).get(f"lat_{lat_idx}", {})
        if ed:
            ref_h = ed.get("H_submain_conn_m")

        L2 = max(0.0, float(lat.length) - conn_dist)
        tk.Label(
            top,
            text=f"Врізка: {conn_dist:.2f} м від початку полілінії | L₁={conn_dist:.2f} м, L₂={L2:.2f} м",
            bg="#2d2d2d",
            fg="#cccccc",
            font=("Arial", 9),
            wraplength=420,
        ).pack(pady=(10, 4))

        row = tk.Frame(top, bg="#2d2d2d")
        row.pack(pady=8)
        tk.Label(row, text="H на тупиках (м вод. ст.):", bg="#2d2d2d", fg="white").pack(
            side=tk.LEFT, padx=8
        )
        var_h = tk.StringVar(value="10.0")
        tk.Entry(row, textvariable=var_h, width=12, bg="#222", fg="white", insertbackground="white").pack(
            side=tk.LEFT
        )

        out = tk.Label(
            top,
            text="Натисніть «Порахувати». Використовуються крок емітерів, Q та тип крапельниці з панелі параметрів.",
            bg="#2d2d2d",
            fg="#FFD700",
            font=("Consolas", 9),
            justify=tk.LEFT,
            wraplength=420,
        )
        out.pack(pady=14, anchor=tk.W, padx=14)

        def run_probe():
            try:
                h_tip = float(var_h.get().replace(",", "."))
            except ValueError:
                messagebox.showerror("Помилка", "Некоректний тиск на тупику.")
                return
            try:
                e_step = float(self.var_emit_step.get().replace(",", "."))
                e_flow = float(self.var_emit_flow.get().replace(",", "."))
                h_min_e = float(self.var_emit_h_min.get().replace(",", "."))
                h_ref_e = float(self.var_emit_h_ref.get().replace(",", "."))
            except ValueError:
                messagebox.showerror(
                    "Помилка",
                    "Перевірте крок емітерів, номінальний Q, H опорна та H мін на вкладці параметрів.",
                )
                return
            emitter_opts = {
                "compensated": bool(self._emitter_compensated_effective()),
                "h_min_m": h_min_e,
            }
            try:
                r = lat_sol.probe_lateral_dripline(
                    lat,
                    conn_dist,
                    h_tip,
                    e_step,
                    e_flow,
                    lambda x, y: self.topo.get_z(x, y),
                    emitter_opts=emitter_opts,
                    h_ref_m=max(0.05, h_ref_e),
                )
            except Exception as ex:
                messagebox.showerror("Помилка", str(ex))
                return
            h1 = r["H_at_connection_wing1_m"]
            h2 = r["H_at_connection_wing2_m"]
            q_lph = r["Q_total_lph"]
            q1_lph = r["Q_wing1_m3s"] * 1000.0 * 3600.0
            q2_lph = r["Q_wing2_m3s"] * 1000.0 * 3600.0
            lines = [
                f"H біля врізки (крило до початку лінії): {h1:.3f} м",
                f"H біля врізки (крило до кінця лінії): {h2:.3f} м",
                f"Сумарна витрата латераля: {q_lph:.2f} л/год",
                f"З них крило 1: {q1_lph:.2f} | крило 2: {q2_lph:.2f} л/год",
            ]
            if ref_h is not None:
                lines.append("")
                lines.append(f"Після повного розрахунку мережі: H на врізці ≈ {ref_h} м (порівняння)")
            lines.append("")
            lines.append(
                "Два значення H біля врізки — окремі зворотні розрахунки по крилах;"
                " розбіжність через довжину та рельєф."
            )
            out.config(text="\n".join(lines))

        _b_probe = tk.Button(
            top,
            text="Порахувати",
            command=run_probe,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 10, "bold"),
        )
        _b_probe.pack(pady=10)
        attach_tooltip(
            _b_probe,
            "Оновити зворотні оцінки тиску біля врізки за поточними даними режиму «Тиск на тупику».",
        )
        top.transient(self.root)

    def _world_m_from_screen_px(self, px: float) -> float:
        """Скільки метрів у світі відповідає заданій товщині на екрані (залежить від zoom)."""
        return float(px) / max(self.zoom, 0.01)

    def _trunk_snap_radius_m(self) -> float:
        """Радіус прив’язки до вузла магістралі: не менше фіксованого м і ~28 px на екрані."""
        return max(_TRUNK_NODE_SNAP_CANVAS_M, self._world_m_from_screen_px(28.0))

    def _pick_tolerance_m(self, min_m: float, px: float = 22.0) -> float:
        """Поріг попадання в об’єкт при підборі: мінімум у метрах або ~px пікселів на полотні."""
        return max(min_m, self._world_m_from_screen_px(px))

    def _nearest_trunk_node_index_world(self, wx: float, wy: float):
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        if not nodes:
            return None, None
        r_snap = self._trunk_snap_radius_m()
        best_i = None
        best_d = r_snap + 1.0
        for i, node in enumerate(nodes):
            try:
                nx = float(node.get("x"))
                ny = float(node.get("y"))
            except (TypeError, ValueError):
                continue
            d = math.hypot(wx - nx, wy - ny)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is None or best_d > r_snap:
            return None, best_d
        return best_i, best_d

    def _trunk_route_preview_snap(self, wx: float, wy: float) -> Tuple[Optional[int], bool]:
        """(індекс вузла в радіусі snap або None, чи ЛКМ зараз додасть вузол без відмови)."""
        ni, _dist = self._nearest_trunk_node_index_world(wx, wy)
        if ni is None:
            return None, False
        nodes = list(self.trunk_map_nodes)
        draft_i = list(getattr(self, "_canvas_trunk_route_draft_indices", []) or [])
        if not draft_i:
            last_end = getattr(self, "_trunk_route_last_node_idx", None)
            if last_end is None:
                src_ix = self._canvas_first_trunk_source_index()
                ok = src_ix is not None and ni == src_ix
                return ni, ok
            k = str(nodes[ni].get("kind", "")).lower()
            ok = ni == last_end or k in ("junction", "valve")
            return ni, ok
        if ni == draft_i[-1]:
            return ni, False
        return ni, True

    def _canvas_first_trunk_source_index(self):
        for i, node in enumerate(self.trunk_map_nodes):
            if is_trunk_root_kind(str(node.get("kind", ""))):
                return i
        return None

    def place_trunk_point_tool_world_xy(self, tool: str, wx: float, wy: float) -> None:
        """
        Поставити вузол магістралі за інструментом (локальні м, XY).
        trunk_pump — лише один насос: повторний ЛКМ переміщує його; інші інструменти — новий вузол на кожен ЛКМ.
        """
        if tool not in _CANVAS_TRUNK_POINT_TOOLS:
            return
        from modules.geo_module import srtm_tiles

        kind_engine = {
            "trunk_pump": "source",
            "trunk_valve": "valve",
            "trunk_picket": "bend",
            "trunk_junction": "junction",
            "trunk_consumer": "consumption",
        }.get(tool, "junction")
        gr = getattr(self, "geo_ref", None)
        if gr and len(gr) >= 2:
            ref_lon, ref_lat = float(gr[0]), float(gr[1])
        else:
            ref_lon, ref_lat = 30.5234, 50.4501
            self.geo_ref = (ref_lon, ref_lat)
        lat, lon = srtm_tiles.local_xy_to_lat_lon(float(wx), float(wy), ref_lon, ref_lat)

        if tool == "trunk_pump":
            src_i = self._canvas_first_trunk_source_index()
            if src_i is not None:
                node = self.trunk_map_nodes[src_i]
                node["kind"] = "source"
                node["x"] = float(wx)
                node["y"] = float(wy)
                node["lat"] = float(lat)
                node["lon"] = float(lon)
            else:
                self.trunk_map_nodes.append(
                    {
                        "kind": "source",
                        "lat": float(lat),
                        "lon": float(lon),
                        "x": float(wx),
                        "y": float(wy),
                    }
                )
        else:
            self.trunk_map_nodes.append(
                {
                    "kind": kind_engine,
                    "lat": float(lat),
                    "lon": float(lon),
                    "x": float(wx),
                    "y": float(wy),
                }
            )
        ensure_trunk_node_ids(self.trunk_map_nodes)
        self.sync_trunk_segment_paths_from_nodes()
        try:
            self._schedule_embedded_map_overlay_refresh()
        except Exception:
            pass
        self.redraw()

    def _canvas_trunk_point_place_at_world(self, wx: float, wy: float) -> None:
        tool = getattr(self, "_canvas_special_tool", None)
        if tool not in _CANVAS_TRUNK_POINT_TOOLS:
            return
        self.place_trunk_point_tool_world_xy(tool, float(wx), float(wy))
        self._canvas_trunk_draft_world = (float(wx), float(wy))

    def _canvas_trunk_point_exit_tool(self) -> None:
        self._canvas_trunk_draft_world = None
        self._canvas_special_tool = None
        self._refresh_canvas_cursor_for_special_tool()
        try:
            self._schedule_embedded_map_overlay_refresh()
        except Exception:
            pass
        self.redraw()

    def _canvas_trunk_route_left_click(self, wx: float, wy: float) -> None:
        nodes = list(self.trunk_map_nodes)
        ni, _dist = self._nearest_trunk_node_index_world(wx, wy)
        if ni is None:
            r_m = int(max(1, round(self._trunk_snap_radius_m())))
            messagebox.showinfo(
                "Магістраль",
                f"Немає вузла в радіусі ~{r_m} м (залежить від масштабу). "
                "Наведіть курсор на насос, кран, пікет, розгалуження або споживача — з’явиться підсвітка.",
            )
            return
        draft_i = list(getattr(self, "_canvas_trunk_route_draft_indices", []) or [])
        if not draft_i:
            last_end = getattr(self, "_trunk_route_last_node_idx", None)
            if last_end is None:
                src_ix = self._canvas_first_trunk_source_index()
                if src_ix is None:
                    messagebox.showwarning(
                        "Магістраль",
                        "Спочатку задайте на полотні вузол витоку — «Насос».",
                    )
                    return
                if ni != src_ix:
                    messagebox.showwarning(
                        "Магістраль",
                        "Перший клік першого відрізка має бути на насосі (витік).",
                    )
                    return
            else:
                k = str(nodes[ni].get("kind", "")).lower()
                start_ok = ni == last_end or k in ("junction", "valve")
                if not start_ok:
                    messagebox.showwarning(
                        "Магістраль",
                        "Початок нового відрізка: кінець попереднього вузла або вузол «Розгалуження» / «Кран» (нова гілка).",
                    )
                    return
        else:
            if ni == draft_i[-1]:
                messagebox.showinfo(
                    "Магістраль",
                    "Оберіть наступний вузол по трасі (не дублюйте попередній).",
                )
                return
        draft_i.append(ni)
        self._canvas_trunk_route_draft_indices = draft_i
        self.redraw()

    def _finish_canvas_trunk_route_segment(self) -> None:
        idxs = list(getattr(self, "_canvas_trunk_route_draft_indices", []) or [])
        nodes = list(self.trunk_map_nodes)
        if len(idxs) >= 2:
            path_local = []
            ok = True
            for ii in idxs:
                if not (0 <= ii < len(nodes)):
                    messagebox.showerror("Магістраль", "Некоректні індекси вузлів.")
                    ok = False
                    break
                try:
                    path_local.append((float(nodes[ii]["x"]), float(nodes[ii]["y"])))
                except (KeyError, TypeError, ValueError):
                    messagebox.showerror("Магістраль", "Не вдалося прочитати координати вузла.")
                    ok = False
                    break
            if ok:
                segs = self.trunk_map_segments
                proposed = {"node_indices": list(idxs), "path_local": path_local}
                trial = list(segs) + [proposed]
                ensure_trunk_node_ids(nodes)
                graph_errs = validate_trunk_map_graph(nodes, trial, complete_only=False)
                if graph_errs:
                    msg = "\n".join(graph_errs[:10])
                    if len(graph_errs) > 10:
                        msg += f"\n… ще {len(graph_errs) - 10}."
                    messagebox.showwarning("Граф магістралі", msg)
                    self._canvas_trunk_route_draft_indices = []
                    self.redraw()
                    return
                segs.append(proposed)
                self._trunk_route_last_node_idx = idxs[-1]
                self._canvas_trunk_route_draft_indices = []
                self.normalize_trunk_segments_to_graph_edges()
                try:
                    self._schedule_embedded_map_overlay_refresh()
                except Exception:
                    pass
                self.redraw()
                return
        elif len(idxs) == 1:
            messagebox.showinfo(
                "Магістраль",
                "Для відрізка потрібні щонайменше два вузли. Додайте ще один ЛКМ або почніть спочатку.",
            )
        self._canvas_trunk_route_draft_indices = []
        self.redraw()

    def _draw_trunk_map_on_canvas(self) -> None:
        for seg in getattr(self, "trunk_map_segments", []) or []:
            pl = self._trunk_segment_world_path(seg)
            if len(pl) < 2:
                continue
            scr = []
            for xy in pl:
                scr.extend(self.to_screen(float(xy[0]), float(xy[1])))
            if len(scr) >= 4:
                self.canvas.create_line(
                    scr,
                    fill=_TRUNK_CANVAS_PATH_COLOR,
                    width=5,
                    tags="trunk_map_canvas",
                )
        nodes = list(getattr(self, "trunk_map_nodes", []) or [])
        g = 11.0
        for node in nodes:
            try:
                cx, cy = self.to_screen(float(node["x"]), float(node["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            kind = str(node.get("kind", "")).lower()
            if kind == "source":
                self.canvas.create_polygon(
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
                    tags="trunk_map_canvas",
                )
            elif kind == "valve":
                self.canvas.create_polygon(
                    cx,
                    cy - g * 1.05,
                    cx - g * 0.92,
                    cy + g * 0.58,
                    cx + g * 0.92,
                    cy + g * 0.58,
                    fill="#C4933A",
                    outline="#1565C0",
                    width=2,
                    tags="trunk_map_canvas",
                )
                self.canvas.create_oval(
                    cx - g * 0.35,
                    cy - g * 0.15,
                    cx + g * 0.35,
                    cy + g * 0.55,
                    fill="#1565C0",
                    outline="#E3F2FD",
                    width=1,
                    tags="trunk_map_canvas",
                )
            elif kind == "bend":
                self.canvas.create_oval(
                    cx - g,
                    cy - g,
                    cx + g,
                    cy + g,
                    fill="#1E88E5",
                    outline="#BBDEFB",
                    width=2,
                    tags="trunk_map_canvas",
                )
            elif kind == "consumption":
                self.canvas.create_polygon(
                    cx,
                    cy - g * 1.05,
                    cx - g * 0.92,
                    cy + g * 0.58,
                    cx + g * 0.92,
                    cy + g * 0.58,
                    fill="#C4933A",
                    outline="#5D4037",
                    width=2,
                    tags="trunk_map_canvas",
                )
            elif kind == "junction":
                Ro, Ri = g * 1.05, g * 0.42
                coords = []
                for k in range(16):
                    ang = -0.5 * math.pi + k * (math.pi / 8)
                    R = Ro if k % 2 == 0 else Ri
                    coords.extend([cx + R * math.cos(ang), cy + R * math.sin(ang)])
                self.canvas.create_polygon(
                    *coords,
                    fill="#1565C0",
                    outline="#E3F2FD",
                    width=2,
                    tags="trunk_map_canvas",
                )
            else:
                self.canvas.create_oval(
                    cx - 4,
                    cy - 4,
                    cx + 4,
                    cy + 4,
                    fill="#757575",
                    outline="#FFFFFF",
                    width=1,
                    tags="trunk_map_canvas",
                )

    def _draw_canvas_polyline_and_route_drafts(self) -> None:
        ct = getattr(self, "_canvas_special_tool", None)
        if ct == "trunk_route" and len(getattr(self, "trunk_map_nodes", []) or []) > 0:
            idxs = getattr(self, "_canvas_trunk_route_draft_indices", []) or []
            nodes = self.trunk_map_nodes
            scr = []
            for ii in idxs:
                if 0 <= ii < len(nodes):
                    try:
                        scr.extend(
                            self.to_screen(float(nodes[ii]["x"]), float(nodes[ii]["y"]))
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
            if len(scr) >= 4:
                self.canvas.create_line(
                    scr,
                    fill="#D1C4E9",
                    width=4,
                    tags="trunk_canvas_draft",
                )
            return
        if ct not in ("scene_lines", "trunk_route"):
            return
        pts = list(getattr(self, "_canvas_polyline_draft", []) or [])
        if len(pts) >= 2:
            scr = [self.to_screen(float(a), float(b)) for a, b in pts]
            col = "#B8C0CC" if ct == "scene_lines" else _TRUNK_CANVAS_PATH_COLOR
            self.canvas.create_line(
                scr,
                fill=col,
                width=3,
                dash=(4, 4),
                tags="trunk_canvas_draft",
            )
        for p in pts:
            sx, sy = self.to_screen(float(p[0]), float(p[1]))
            self.canvas.create_oval(
                sx - 4,
                sy - 4,
                sx + 4,
                sy + 4,
                outline="#CCCCCC",
                tags="trunk_canvas_draft",
            )

    def handle_left_click(self, event):
        self.canvas.focus_set()
        wx, wy = self.to_world(event.x, event.y)
        self._handle_left_click_world(wx, wy, scr_x=event.x, scr_y=event.y)

    def handle_left_release(self, event):
        wx, wy = self.to_world(event.x, event.y)
        ct = getattr(self, "_canvas_special_tool", None)
        if ct != "select" or not getattr(self, "_select_marquee_active", False):
            return
        self._select_marquee_active = False
        sx0, sy0 = self._select_marquee_start_screen or (event.x, event.y)
        crossing = event.x < sx0
        w0 = self._select_marquee_start_world or (float(wx), float(wy))
        minx, miny, maxx, maxy = self._world_rect_normalize(w0[0], w0[1], wx, wy)
        dragged = bool(
            getattr(self, "_select_marquee_dragged", False)
            or abs(event.x - sx0) > 4
            or abs(event.y - sy0) > 4
        )
        self._select_marquee_dragged = False
        self._select_marquee_start_screen = None
        self._select_marquee_curr_screen = None
        self._select_marquee_start_world = None
        self._select_marquee_curr_world = None
        if dragged and (maxx - minx) > 1e-3 and (maxy - miny) > 1e-3:
            hits = self._pick_hits_in_world_rect(
                w0[0], w0[1], wx, wy, crossing=crossing
            )
            self._canvas_selection_keys = list(hits)
            if hits:
                lines = [h[2] for h in hits[:18]]
                msg = "\n".join(lines)
                if len(hits) > 18:
                    msg += f"\n… ще {len(hits) - 18}."
                messagebox.showinfo("Вибір", f"Обрано: {len(hits)}\n\n{msg}")
            else:
                messagebox.showinfo("Вибір", "У прямокутнику немає об'єктів.")
        else:
            hits = self._collect_world_pick_hits(wx, wy)
            if hits:
                pri, _d, cat, payload, label = hits[0]
                self._canvas_selection_keys = [(cat, payload, label)]
                messagebox.showinfo("Вибір", label)
            else:
                self._canvas_selection_keys = []
                messagebox.showinfo(
                    "Вибір",
                    "Об'єкт не знайдено. Клацніть ближче до вузла магістралі, труби, блоку чи лінії мережі.",
                )
        self.redraw()

    def _canvas_b1_motion(self, event):
        if self.mode.get() == "PAN":
            self.handle_pan(event)
            return
        if (
            getattr(self, "_canvas_special_tool", None) == "select"
            and getattr(self, "_select_marquee_active", False)
        ):
            wx, wy = self.to_world(event.x, event.y)
            self._select_marquee_curr_world = (wx, wy)
            self._select_marquee_curr_screen = (event.x, event.y)
            sx0, sy0 = self._select_marquee_start_screen or (event.x, event.y)
            if abs(event.x - sx0) > 3 or abs(event.y - sy0) > 3:
                self._select_marquee_dragged = True
            self.redraw()
            return

    def _handle_left_click_world(
        self, wx: float, wy: float, *, scr_x: Optional[int] = None, scr_y: Optional[int] = None
    ) -> None:
        """ЛКМ у світових координатах (полотно або вкладка «Карта» після geo)."""
        if self.action.get() == "DEL":
            self.handle_erase(wx, wy)
            return

        ct = getattr(self, "_canvas_special_tool", None)
        if ct == "select":
            self._select_marquee_active = True
            self._select_marquee_dragged = False
            self._select_marquee_start_world = (float(wx), float(wy))
            self._select_marquee_curr_world = (float(wx), float(wy))
            sx = int(scr_x) if scr_x is not None else 0
            sy = int(scr_y) if scr_y is not None else 0
            self._select_marquee_start_screen = (sx, sy)
            self._select_marquee_curr_screen = (sx, sy)
            return
        if ct == "map_pick_info":
            label = self.pick_world_object_at_canvas(wx, wy)
            if label:
                messagebox.showinfo("Інфо", label)
            else:
                messagebox.showinfo(
                    "Інфо",
                    "Об'єкт не знайдено. Клацніть ближче до вузла магістралі, труби, блоку чи лінії мережі.",
                )
            self.redraw()
            return
        if ct in _CANVAS_TRUNK_POINT_TOOLS:
            self._canvas_trunk_point_place_at_world(float(wx), float(wy))
            return
        if ct == "scene_lines":
            self._canvas_polyline_draft.append((float(wx), float(wy)))
            self.redraw()
            return
        if ct == "trunk_route":
            nodes = getattr(self, "trunk_map_nodes", []) or []
            if len(nodes) == 0:
                self._canvas_polyline_draft.append((float(wx), float(wy)))
                self.redraw()
                return
            self._canvas_trunk_route_left_click(float(wx), float(wy))
            return

        m = self.mode.get()
        if m in ("PAN", "VIEW"):
            return
        if m == "ZOOM_BOX":
            if self._zoom_box_start is None:
                self._zoom_box_start = (float(wx), float(wy))
                self._zoom_box_end = (float(wx), float(wy))
            else:
                self._zoom_box_end = (float(wx), float(wy))
                x1, y1 = self._zoom_box_start
                x2, y2 = self._zoom_box_end
                w = abs(x2 - x1)
                h = abs(y2 - y1)
                if w > 1e-6 and h > 1e-6:
                    self.canvas.update_idletasks()
                    cw = max(1, int(self.canvas.winfo_width()))
                    ch = max(1, int(self.canvas.winfo_height()))
                    margin = 0.08
                    use_w = max(1.0, cw * (1 - 2 * margin))
                    use_h = max(1.0, ch * (1 - 2 * margin))
                    zf = min(use_w / w, use_h / h)
                    cx = (x1 + x2) / 2.0
                    cy = (y1 + y2) / 2.0
                    self.zoom = max(0.02, min(200.0, float(zf)))
                    self.offset_x = cw / 2.0 - cx * self.zoom
                    self.offset_y = ch / 2.0 - cy * self.zoom
                self._zoom_box_start = None
                self._zoom_box_end = None
                self.mode.set("VIEW")
            self.redraw()
            return
        if m == "SUB_LABEL" and self.calc_results.get("sections"):
            # 1-й ЛКМ: вибрати підпис секції; 2-й ЛКМ: зафіксувати нову позицію.
            if self._moving_section_label_key is None:
                picked = self._pick_section_label_for_move(wx, wy)
                if picked is not None:
                    (
                        self._moving_section_label_key,
                        self._moving_section_label_sub_idx,
                        self._moving_section_label_sm_idx,
                    ) = picked
                    self._moving_section_label_preview = (float(wx), float(wy))
            else:
                key = self._section_label_storage_key(
                    int(self._moving_section_label_key),
                    int(self._moving_section_label_sub_idx),
                    int(self._moving_section_label_sm_idx)
                )
                self.calc_results.setdefault("section_label_pos", {})[key] = (
                    float(wx),
                    float(wy),
                )
                self._moving_section_label_key = None
                self._moving_section_label_sub_idx = None
                self._moving_section_label_sm_idx = None
                self._moving_section_label_preview = None
            self.redraw()
            return

        if m == "TOPO":
            z_str = simpledialog.askstring("Висота", "Введіть висоту Z (м) для цієї точки:", parent=self.root)
            if z_str is not None:
                try:
                    z_val = float(z_str.replace(',', '.'))
                    self.topo.add_point(wx, wy, z_val)
                    self.redraw()
                except ValueError:
                    messagebox.showerror("Помилка", "Будь ласка, введіть число.")
            return

        if m == "RULER":
            if self.ruler_start and self.ortho_on.get():
                if abs(wx-self.ruler_start[0]) > abs(wy-self.ruler_start[1]): wy = self.ruler_start[1]
                else: wx = self.ruler_start[0]
            if self.ruler_start:
                self.control_panel.stats_label.config(text=f"📏 Останній вимір: {math.hypot(wx - self.ruler_start[0], wy - self.ruler_start[1]):.2f} м")
            self.ruler_start = (wx, wy); self.redraw(); return
            
        if m == "INFO":
            p_mouse = Point(wx, wy)
            thresh = 15 / self.zoom
            best = None
            for idx, sm in enumerate(self._hydraulic_submain_lines()):
                d = LineString(sm).distance(p_mouse)
                if d < thresh and (best is None or d < best[0]):
                    best = (d, "sm", idx)
            for idx, lat in enumerate(self._flatten_all_lats()):
                d = lat.distance(p_mouse)
                if d < thresh and (best is None or d < best[0]):
                    best = (d, "lat", idx)
            if best is not None:
                if best[1] == "sm":
                    self.show_submain_graph(best[2])
                else:
                    self.show_graph(f"lat_{best[2]}")
            return

        if m == "LAT_TIP":
            p_mouse = Point(wx, wy)
            thresh = 15 / self.zoom
            best = None
            lats = self._flatten_all_lats()
            for idx, lat in enumerate(lats):
                d = lat.distance(p_mouse)
                if d < thresh and (best is None or d < best[0]):
                    best = (d, idx)
            if best is not None:
                self._open_lateral_tip_probe_dialog(best[1], lats[best[1]])
            else:
                messagebox.showinfo("Інфо", "Клікніть ближче до латераля.")
            return

        if m == "CUT_LATS":
            if self._snap_point:
                wx, wy = self._snap_point
            if self._cut_line_start is None:
                self._cut_line_start = (wx, wy)
            else:
                self._erase_laterals_intersecting_line(LineString([self._cut_line_start, (wx, wy)]))
                self._cut_line_start = None
            if self.snap_disabled_next_click:
                self.snap_disabled_next_click = False
                self._snap_point = self.get_snap(wx, wy)
            self.redraw()
            return

        if not (
            (m == "SET_DIR" and self._dir_target_block_idx is None)
            or m == "SUB_LABEL"
            or (m == "DRAW")
            or (m == "SUBMAIN")
        ):
            self.reset_calc()
        if self._snap_point:
            wx, wy = self._snap_point

        if m == "DRAW" and not self.is_closed:
            if self.points and self.ortho_on.get():
                if abs(wx - self.points[-1][0]) > abs(wy - self.points[-1][1]):
                    wy = self.points[-1][1]
                else:
                    wx = self.points[-1][0]
            self.points.append((wx, wy))
        elif m == "SET_DIR":
            if self._dir_target_block_idx is None:
                bi = self._find_block_interior(wx, wy)
                if bi is None:
                    messagebox.showwarning(
                        "Увага",
                        "Спочатку клікніть ЛКМ всередині блоку, для якого задаєте напрямок рядів.",
                    )
                    return
                self._dir_target_block_idx = bi
                self.dir_points = []
                self.redraw()
                return
            self.dir_points.append((wx, wy))
            if len(self.dir_points) == 2:
                idx = self._dir_target_block_idx
                if idx is not None and idx < len(self.field_blocks):
                    self.field_blocks[idx]["edge_angle"] = math.atan2(
                        self.dir_points[1][1] - self.dir_points[0][1],
                        self.dir_points[1][0] - self.dir_points[0][0],
                    )
                    self._regenerate_block_grid(idx)
                self._dir_target_block_idx = None
                self.dir_points = []
        elif m == "SUBMAIN":
            if not self.active_submain:
                bi_click = self._find_block_containing(wx, wy)
                abi = self._safe_active_block_idx()
                bi = bi_click
                if bi is None and abi is not None and 0 <= abi < len(self.field_blocks):
                    poly = self._block_poly(self.field_blocks[abi])
                    p = Point(wx, wy)
                    tol = 15.0 / max(self.zoom, 0.01)
                    if not poly.is_empty and (
                        poly.contains(p) or poly.boundary.distance(p) <= tol
                    ):
                        bi = abi
                if bi is None:
                    messagebox.showwarning(
                        "Увага",
                        "Клікніть всередині блоку поля (або всередині обраного в панелі «Активний блок»), щоб прив'язати сабмейн.",
                    )
                    return
                self._active_submain_block_idx = bi
                self.active_submain.append((wx, wy))
            elif self._current_live_end:
                self.active_submain.append(self._current_live_end)
        elif m == "DRAW_LAT":
            if not self.active_manual_lat:
                bi = self._find_block_containing(wx, wy)
                if bi is None:
                    messagebox.showwarning("Увага", "Спочатку клікніть всередині блоку, куди входить ручна dripline.")
                    return
                self._active_draw_block_idx = bi
            if self.active_manual_lat and self.ortho_on.get():
                if abs(wx - self.active_manual_lat[-1][0]) > abs(wy - self.active_manual_lat[-1][1]):
                    wy = self.active_manual_lat[-1][1]
                else:
                    wx = self.active_manual_lat[-1][0]
            self.active_manual_lat.append((wx, wy))
            
        if self.snap_disabled_next_click:
            self.snap_disabled_next_click = False
            self._snap_point = self.get_snap(wx, wy)

        self.redraw()

    def feed_map_pointer_world(self, wx: float, wy: float, *, redraw_canvas: bool = True) -> None:
        """Рух курсора на карті: snap і живий кінець сабмейну (як handle_motion)."""
        self._last_map_pointer_world = (float(wx), float(wy))
        self._snap_point = self.get_snap(wx, wy)
        if self.mode.get() == "SUBMAIN" and self.active_submain and self.action.get() == "ADD":
            self._current_live_end = self.calculate_live_submain(wx, wy)
        if redraw_canvas:
            self.redraw()

    def handle_right_click(self, event):
        self.canvas.focus_set()
        if self.action.get() == "DEL":
            return
        wx, wy = self.to_world(event.x, event.y)
        self._handle_right_click_world(wx, wy)

    def _handle_right_click_world(self, wx: float, wy: float) -> None:
        if self.action.get() == "DEL":
            return
        ct = getattr(self, "_canvas_special_tool", None)
        if ct == "select":
            if getattr(self, "_canvas_selection_keys", None):
                self._canvas_selection_keys = []
                self.redraw()
                return
            self._canvas_special_tool = None
            self._refresh_canvas_cursor_for_special_tool()
            self.redraw()
            return
        if ct == "map_pick_info":
            self._canvas_special_tool = None
            self._refresh_canvas_cursor_for_special_tool()
            self.redraw()
            return
        if ct in _CANVAS_TRUNK_POINT_TOOLS:
            self._canvas_trunk_point_exit_tool()
            return
        if ct == "scene_lines":
            pts = list(getattr(self, "_canvas_polyline_draft", []) or [])
            if len(pts) >= 2:
                self.scene_lines.append([(float(p[0]), float(p[1])) for p in pts])
            elif len(pts) == 1:
                messagebox.showinfo("Лінії", "Потрібно щонайменше дві вершини (ЛКМ).")
            self._canvas_polyline_draft = []
            self._canvas_special_tool = None
            self.redraw()
            return
        if ct == "trunk_route":
            nodes = getattr(self, "trunk_map_nodes", []) or []
            if len(nodes) == 0:
                pts = list(getattr(self, "_canvas_polyline_draft", []) or [])
                if len(pts) >= 2:
                    line_local = [(float(p[0]), float(p[1])) for p in pts]
                    bi = self._safe_active_block_idx()
                    if bi is not None and 0 <= bi < len(self.field_blocks):
                        self.field_blocks[bi].setdefault("submain_lines", []).append(list(line_local))
                        self.reset_calc()
                    else:
                        messagebox.showwarning(
                            "Магістраль",
                            "Оберіть активний блок поля або намалюйте блок перед трасою в сабмейн.",
                        )
                elif len(pts) == 1:
                    messagebox.showinfo("Магістраль", "Потрібно щонайменше дві вершини (ЛКМ).")
                self._canvas_polyline_draft = []
                self._canvas_special_tool = None
                self.redraw()
                return
            self._finish_canvas_trunk_route_segment()
            return

        m = self.mode.get()

        if m == "TOPO":
            if self.topo.elevation_points:
                closest = min(self.topo.elevation_points, key=lambda pt: math.hypot(pt[0]-wx, pt[1]-wy))
                if math.hypot(closest[0]-wx, closest[1]-wy) * self.zoom < 15:
                    self.topo.elevation_points.remove(closest)
                    self.reset_calc()
                    self.redraw()
            return
            
        if m == "DRAW" and not self.is_closed and len(self.points) > 2:
            self.close_polygon(); return

        if m == "RULER":
            self.ruler_start = None
            self.redraw()
            return
        if m == "CUT_LATS":
            self._cut_line_start = None
            self.redraw()
            return
        if m == "SUBMAIN" and self.active_submain and self._current_live_end:
            self.active_submain.append(self._current_live_end)
            bi = self._active_submain_block_idx
            if bi is not None and bi < len(self.field_blocks):
                self._strip_hydro_for_block_keep_others(bi)
                self.field_blocks[bi]["submain_lines"].append(list(self.active_submain))
            self.active_submain = []
            self._active_submain_block_idx = None
            self._current_live_end = None
            self._submain_preview_world = None
            self._submain_end_snapped = False
            self.redraw()
        elif m == "DRAW_LAT" and len(self.active_manual_lat) > 1:
            self.reset_calc()
            bi = self._active_draw_block_idx
            if bi is not None and bi < len(self.field_blocks):
                lat_geom = self._finalize_manual_lat_against_submains(self.active_manual_lat, bi)
                if lat_geom is not None and not lat_geom.is_empty and len(lat_geom.coords) >= 2:
                    self.field_blocks[bi]["manual_laterals"].append(lat_geom)
            self.active_manual_lat = []
            self._active_draw_block_idx = None
            self.redraw()

    def calculate_live_submain(self, twx, twy):
        """
        Притягування кінця сабмейну до перетинів із латералями та до кінців латералів уздовж
        напрямку курсора; ланцюг точок уздовж променя — для попереднього креслення всіх «вузлів».
        """
        self._submain_preview_world = None
        self._submain_end_snapped = False
        if not self.active_submain:
            return None
        s_pt = self.active_submain[-1]
        sx, sy = s_pt

        if self.ortho_on.get():
            if abs(twx - sx) > abs(twy - sy):
                twy = sy
            else:
                twx = sx

        def _preview_free(ex, ey):
            self._submain_preview_world = [s_pt, (float(ex), float(ey))]
            self._submain_end_snapped = False
            return (float(ex), float(ey))

        if not self.snap_enabled or self.snap_disabled_next_click:
            return _preview_free(twx, twy)

        rdx, rdy = twx - sx, twy - sy
        rlen = math.hypot(rdx, rdy)
        if rlen < 1e-9:
            return _preview_free(twx, twy)
        dx, dy = rdx / rlen, rdy / rlen

        tol = max(15.0 / self.zoom, 0.5)
        huge = 1.0e7
        far = (sx + dx * huge, sy + dy * huge)
        long_ray = LineString([s_pt, far])

        bi = self._active_submain_block_idx
        lats = (
            self.field_blocks[bi]["auto_laterals"] + self.field_blocks[bi]["manual_laterals"]
            if bi is not None and bi < len(self.field_blocks)
            else self._flatten_all_lats()
        )

        def along_t(px, py):
            return (px - sx) * dx + (py - sy) * dy

        candidates = []

        def add_pt(px, py):
            t = along_t(px, py)
            if t > 1e-6:
                candidates.append((t, (float(px), float(py))))

        for lat in lats:
            if lat.is_empty or len(lat.coords) < 2:
                continue
            try:
                inter = long_ray.intersection(lat)
            except Exception:
                continue
            if inter.is_empty:
                pass
            elif inter.geom_type == "Point":
                add_pt(inter.x, inter.y)
            elif inter.geom_type == "LineString":
                for c in inter.coords:
                    add_pt(c[0], c[1])
            elif inter.geom_type == "MultiPoint":
                for p in inter.geoms:
                    add_pt(p.x, p.y)
            elif inter.geom_type == "MultiLineString":
                for g in inter.geoms:
                    for c in g.coords:
                        add_pt(c[0], c[1])
            elif hasattr(inter, "geoms"):
                for g in inter.geoms:
                    if g.geom_type == "Point":
                        add_pt(g.x, g.y)
                    elif g.geom_type == "LineString":
                        for c in g.coords:
                            add_pt(c[0], c[1])

            for end in (lat.coords[0], lat.coords[-1]):
                ex, ey = float(end[0]), float(end[1])
                t = (ex - sx) * dx + (ey - sy) * dy
                if t <= 1e-6:
                    continue
                fx, fy = sx + t * dx, sy + t * dy
                perp = math.hypot(ex - fx, ey - fy)
                if perp <= tol:
                    add_pt(fx, fy)

        if not candidates:
            return _preview_free(twx, twy)
        candidates.sort(key=lambda x: x[0])
        dedup = []
        for _t, pt in candidates:
            if not dedup or math.hypot(pt[0] - dedup[-1][0], pt[1] - dedup[-1][1]) > 1e-4:
                dedup.append(pt)
        end_xy = dedup[-1]
        self._submain_preview_world = [s_pt] + dedup
        self._submain_end_snapped = True
        return end_xy

    def handle_motion(self, event):
        wx, wy = self.to_world(event.x, event.y)
        self._last_mouse_world = (wx, wy)
        self._snap_point = self.get_snap(wx, wy)
        m = self.mode.get()
        if m == "SUB_LABEL" and self._moving_section_label_key is not None:
            self._moving_section_label_preview = (float(wx), float(wy))
        if m == "ZOOM_BOX" and self._zoom_box_start is not None:
            self._zoom_box_end = (float(wx), float(wy))
        zoom_rubber = m == "ZOOM_BOX" and self._zoom_box_start is not None

        if m == "SUBMAIN" and self.active_submain and self.action.get() == "ADD":
            self._current_live_end = self.calculate_live_submain(wx, wy)
            
        z_val = self.topo.get_z(wx, wy) if self.topo.elevation_points else 0.0
        if hasattr(self.control_panel, 'lbl_z_cursor'):
            self.control_panel.lbl_z_cursor.config(text=f"Z під курсором: {z_val:.2f} м (pts: {len(self.topo.elevation_points)})")
        
        self.redraw(skip_heavy_canvas_layers=zoom_rubber)
        self.canvas.delete("preview")
        
        if m == "RULER" and self.ruler_start:
            tx, ty = wx, wy
            if self.ortho_on.get():
                if abs(tx-self.ruler_start[0]) > abs(ty-self.ruler_start[1]): ty = self.ruler_start[1]
                else: tx = self.ruler_start[0]
            stx, sty = self.to_screen(tx, ty)
            self.canvas.create_line(self.to_screen(*self.ruler_start), stx, sty, fill="#00FFFF", dash=(4,4), width=2, tags="preview")
            self.canvas.create_text(stx + 15, sty - 15, text=f"{math.hypot(tx-self.ruler_start[0], ty-self.ruler_start[1]):.2f} м", fill="#00FFFF", font=("Arial", 10, "bold"), anchor=tk.SW, tags="preview")
            return
        
        if m == "INFO":
            p_mouse = Point(wx, wy)
            for lat in self._flatten_all_lats():
                if lat.distance(p_mouse) < 15 / self.zoom:
                    self.canvas.create_line(
                        [self.to_screen(*c) for c in lat.coords], fill="#FFD700", width=4, tags="preview"
                    )
                    break
            return

        if m == "LAT_TIP":
            p_mouse = Point(wx, wy)
            for lat in self._flatten_all_lats():
                if lat.distance(p_mouse) < 15 / self.zoom:
                    self.canvas.create_line(
                        [self.to_screen(*c) for c in lat.coords], fill="#FFD700", width=4, tags="preview"
                    )
                    break
            return

        if m == "CUT_LATS" and self._cut_line_start and self.action.get() == "ADD":
            self.canvas.create_line(
                self.to_screen(*self._cut_line_start),
                event.x,
                event.y,
                fill="#FF8800",
                dash=(4, 4),
                width=2,
                tags="preview",
            )

        if self.action.get() != "DEL":
            ct_pv = getattr(self, "_canvas_special_tool", None)
            if ct_pv in _CANVAS_TRUNK_POINT_TOOLS and self._canvas_trunk_draft_world is not None:
                st = self._canvas_trunk_draft_world
                self.canvas.create_line(
                    self.to_screen(st[0], st[1]),
                    event.x,
                    event.y,
                    fill=_canvas_trunk_rubber_color(ct_pv),
                    dash=(6, 4),
                    width=2,
                    tags="preview",
                )
            elif ct_pv == "trunk_route":
                nodes = getattr(self, "trunk_map_nodes", []) or []
                if len(nodes) > 0:
                    draft_i = getattr(self, "_canvas_trunk_route_draft_indices", []) or []
                    if draft_i:
                        last = draft_i[-1]
                        if 0 <= last < len(nodes):
                            try:
                                nx, ny = float(nodes[last]["x"]), float(nodes[last]["y"])
                                self.canvas.create_line(
                                    self.to_screen(nx, ny),
                                    event.x,
                                    event.y,
                                    fill="#D1C4E9",
                                    dash=(6, 4),
                                    width=2,
                                    tags="preview",
                                )
                            except (KeyError, TypeError, ValueError):
                                pass
                    ni_vis, snap_ok = self._trunk_route_preview_snap(wx, wy)
                    if ni_vis is not None and 0 <= ni_vis < len(nodes):
                        try:
                            sx, sy = self.to_screen(
                                float(nodes[ni_vis]["x"]), float(nodes[ni_vis]["y"])
                            )
                            rad = 22
                            col = "#58F5C2" if snap_ok else "#FFAB40"
                            self.canvas.create_oval(
                                sx - rad,
                                sy - rad,
                                sx + rad,
                                sy + rad,
                                outline=col,
                                width=3,
                                tags="preview",
                            )
                            if snap_ok:
                                hint = "Прив'язка: ЛКМ додасть вузол"
                            elif draft_i and ni_vis == draft_i[-1]:
                                hint = "Оберіть інший вузол по трасі"
                            else:
                                hint = "Потрібен насос / кінець гілки / кран чи розгалуження"
                            self.canvas.create_text(
                                sx + rad + 8,
                                sy,
                                text=hint,
                                fill=col,
                                font=("Segoe UI", 8, "bold"),
                                anchor=tk.W,
                                tags="preview",
                            )
                        except (KeyError, TypeError, ValueError):
                            pass
                else:
                    pts = getattr(self, "_canvas_polyline_draft", []) or []
                    if pts:
                        lx, ly = float(pts[-1][0]), float(pts[-1][1])
                        self.canvas.create_line(
                            self.to_screen(lx, ly),
                            event.x,
                            event.y,
                            fill=_TRUNK_CANVAS_PATH_COLOR,
                            dash=(6, 4),
                            width=2,
                            tags="preview",
                        )
            elif ct_pv == "scene_lines":
                pts = getattr(self, "_canvas_polyline_draft", []) or []
                if pts:
                    lx, ly = float(pts[-1][0]), float(pts[-1][1])
                    self.canvas.create_line(
                        self.to_screen(lx, ly),
                        event.x,
                        event.y,
                        fill="#B8C0CC",
                        dash=(6, 4),
                        width=2,
                        tags="preview",
                    )
            elif ct_pv in _CANVAS_PASSIVE_PICK_TOOLS:
                skip_footer = (
                    ct_pv == "select"
                    and getattr(self, "_select_marquee_active", False)
                    and getattr(self, "_select_marquee_dragged", False)
                )
                lbl = (
                    None
                    if skip_footer
                    else self.pick_world_object_at_canvas(wx, wy)
                )
                lime_pls, yel_pls = self.trunk_info_highlight_world_paths(wx, wy)
                for pl in lime_pls:
                    scr_ln: list = []
                    for xy in pl:
                        scr_ln.extend(self.to_screen(float(xy[0]), float(xy[1])))
                    if len(scr_ln) >= 4:
                        self.canvas.create_line(
                            scr_ln,
                            fill=_TRUNK_INFO_COLOR_TO_CONSUMERS,
                            width=7,
                            tags="preview",
                        )
                for pl in yel_pls:
                    scr_y: list = []
                    for xy in pl:
                        scr_y.extend(self.to_screen(float(xy[0]), float(xy[1])))
                    if len(scr_y) >= 4:
                        self.canvas.create_line(
                            scr_y,
                            fill=_TRUNK_INFO_COLOR_PUMP_PATH,
                            width=5,
                            tags="preview",
                        )
                try:
                    cw = max(120, int(self.canvas.winfo_width()))
                    ch = max(40, int(self.canvas.winfo_height()))
                except tk.TclError:
                    cw, ch = 400, 300
                pad = 6
                if lbl:
                    self.canvas.create_rectangle(
                        pad,
                        ch - 46,
                        cw - pad,
                        ch - pad,
                        fill="#0f141c",
                        outline="#3a5a78",
                        width=1,
                        tags="preview",
                    )
                    self.canvas.create_text(
                        pad + 8,
                        ch - 26,
                        text=lbl[:110],
                        anchor=tk.W,
                        fill="#9DCBFA",
                        font=("Segoe UI", 9, "bold"),
                        tags="preview",
                    )
                else:
                    self.canvas.create_rectangle(
                        pad,
                        ch - 34,
                        min(cw - pad, pad + 340),
                        ch - pad,
                        fill="#141414",
                        outline="#333333",
                        width=1,
                        tags="preview",
                    )
                    self.canvas.create_text(
                        pad + 8,
                        ch - 22,
                        text="Під курсором нічого — наблизьте або клацніть ближче до лінії/вузла",
                        anchor=tk.W,
                        fill="#777777",
                        font=("Segoe UI", 8),
                        tags="preview",
                    )

        if self.action.get() == "DEL":
            p_mouse = Point(wx, wy)
            if m == "DRAW":
                bi = self._find_block_interior(wx, wy)
                if bi is not None:
                    ring = self.field_blocks[bi]["ring"]
                    if len(ring) > 1:
                        scr = [self.to_screen(*p) for p in ring]
                        self.canvas.create_line(scr + [scr[0]], fill="red", width=4, tags="preview")
                else:
                    cand = list(self.points)
                    for b in self.field_blocks:
                        cand.extend(b["ring"])
                    if cand:
                        closest = min(cand, key=lambda pt: math.hypot(pt[0] - wx, pt[1] - wy))
                        if math.hypot(closest[0] - wx, closest[1] - wy) * self.zoom < 15:
                            cx, cy = self.to_screen(*closest)
                            self.canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline="red", width=2, tags="preview")
            elif m == "SUBMAIN":
                for sm in self._all_submain_lines():
                    if LineString(sm).distance(p_mouse) < 15 / self.zoom:
                        for i in range(len(sm) - 1):
                            self.canvas.create_line(
                                self.to_screen(*sm[i]), self.to_screen(*sm[i + 1]), fill="red", width=10, tags="preview"
                            )
            elif m in ("DRAW_LAT", "SET_DIR", "CUT_LATS"):
                for lat in self._flatten_all_lats():
                    if lat.distance(p_mouse) < 15 / self.zoom:
                        self.canvas.create_line(
                            [self.to_screen(*c) for c in lat.coords], fill="red", width=3, tags="preview"
                        )
                        break
            elif m == "TOPO" and self.topo.elevation_points:
                closest = min(self.topo.elevation_points, key=lambda pt: math.hypot(pt[0]-wx, pt[1]-wy))
                if math.hypot(closest[0]-wx, closest[1]-wy) * self.zoom < 15:
                    cx, cy = self.to_screen(closest[0], closest[1])
                    self.canvas.create_oval(cx-8, cy-8, cx+8, cy+8, outline="red", width=2, tags="preview")
            return 

        if m == "DRAW" and self.points and not self.is_closed:
            lx, ly = self.to_screen(*self.points[-1])
            tx, ty = event.x, event.y
            if self.ortho_on.get():
                if abs(tx-lx) > abs(ty-ly): ty = ly
                else: tx = lx
            self.canvas.create_line(lx, ly, tx, ty, fill="white", dash=(4,4), tags="preview")
        elif m == "SUBMAIN" and self.active_submain:
            if self._submain_preview_world and len(self._submain_preview_world) >= 2:
                chain = list(self.active_submain[:-1]) + list(self._submain_preview_world)
            elif self._current_live_end:
                chain = list(self.active_submain) + [self._current_live_end]
            else:
                chain = list(self.active_submain)
            scr = [self.to_screen(*p) for p in chain if p is not None]
            if len(scr) >= 2:
                self.canvas.create_line(scr, fill="#FF3366", width=8, tags="preview")
            if self._current_live_end and getattr(self, "_submain_end_snapped", False):
                cx, cy = self.to_screen(*self._current_live_end)
                self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, outline="#00FFCC", width=2, tags="preview")
                self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline="#FFFFFF", width=1, tags="preview")
        elif m == "DRAW_LAT" and self.active_manual_lat:
            self.canvas.create_line(self.to_screen(*self.active_manual_lat[-1]), event.x, event.y, fill="orange", dash=(2,2), tags="preview")
        elif m == "ZOOM_BOX" and self._zoom_box_start is not None and self._zoom_box_end is not None:
            sx1, sy1 = self.to_screen(*self._zoom_box_start)
            sx2, sy2 = self.to_screen(*self._zoom_box_end)
            self.canvas.create_rectangle(
                sx1,
                sy1,
                sx2,
                sy2,
                outline="#66CCFF",
                width=2,
                dash=(4, 3),
                tags="preview",
            )

    def _block_params_float(self, block, key, fallback_var, default):
        p = block.get("params") or {}
        raw = p.get(key)
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            try:
                return float(fallback_var.get().replace(",", "."))
            except Exception:
                return default
        try:
            return float(str(raw).replace(",", "."))
        except Exception:
            try:
                return float(fallback_var.get().replace(",", "."))
            except Exception:
                return default

    def _block_params_int(self, block, key, fallback_var, default):
        p = block.get("params") or {}
        raw = p.get(key)
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            try:
                return int(fallback_var.get())
            except Exception:
                return default
        try:
            return int(float(str(raw).replace(",", ".")))
        except Exception:
            try:
                return int(fallback_var.get())
            except Exception:
                return default

    def _regenerate_block_grid(self, block_index: int, redraw: bool = True) -> bool:
        if block_index < 0 or block_index >= len(self.field_blocks):
            return False
        block = self.field_blocks[block_index]
        try:
            step = self._block_params_float(block, "lat", self.var_lat_step, 0.9)
            max_len = self._block_params_float(block, "max_len", self.var_max_lat_len, 0.0)
            block_count = self._block_params_int(block, "blocks", self.var_lat_block_count, 0)
            if step < 0.1:
                return False
        except Exception:
            return False
        block["auto_laterals"] = []
        ea = block.get("edge_angle")
        if ea is None:
            if redraw:
                self.redraw()
            return True
        poly = self._block_poly(block)
        if poly.is_empty:
            if redraw:
                self.redraw()
            return True
        clip = self._lateral_grid_clip_polygon(poly)
        if clip.is_empty:
            if redraw:
                self.redraw()
            return True
        bx0, by0, bx1, by1 = clip.bounds
        reach = max(max(bx1 - bx0, by1 - by0) * 2.0, 10000.0)
        ref = block["ring"][0]
        dx, dy = math.cos(ea), math.sin(ea)
        nx, ny = -math.sin(ea), math.cos(ea)
        raw_lines = []
        for i in range(-1500, 1500):
            if block_count > 0 and i % (block_count + 1) == block_count:
                continue
            ox, oy = ref[0] + nx * (i * step), ref[1] + ny * (i * step)
            ray = LineString([(ox - reach * dx, oy - reach * dy), (ox + reach * dx, oy + reach * dy)])
            inter = ray.intersection(clip)
            if not inter.is_empty:
                if inter.geom_type == "LineString":
                    raw_lines.append(inter)
                elif hasattr(inter, "geoms"):
                    for g in inter.geoms:
                        if g.geom_type == "LineString":
                            raw_lines.append(g)
        for geom in raw_lines:
            if max_len > 0 and geom.length > max_len:
                num_parts = math.ceil(geom.length / max_len)
                part_len = geom.length / num_parts
                for p in range(num_parts):
                    start_dist = p * part_len
                    end_dist = (p + 1) * part_len - 0.5
                    if end_dist > start_dist:
                        block["auto_laterals"].append(substring(geom, start_dist, end_dist))
            else:
                block["auto_laterals"].append(geom)
        if redraw:
            self.redraw()
        return True

    def regenerate_grid(self):
        try:
            step = float(self.var_lat_step.get().replace(",", "."))
            if step < 0.1:
                return
        except Exception:
            return
        for i in range(len(self.field_blocks)):
            self._regenerate_block_grid(i, redraw=False)
        self.redraw()

    def get_valves(self):
        unique_valves = set()
        for sm in self._all_submain_lines() + ([self.active_submain] if len(self.active_submain) > 1 else []):
            if sm:
                unique_valves.add((sm[0][0], sm[0][1]))
        return unique_valves
        
    def clear_topo(self):
        self.topo.clear()
        self.cached_contours = []
        self.redraw()

    def fetch_srtm_data(self):
        geom = self.contour_clip_geometry()
        if geom is None or geom.is_empty:
            messagebox.showwarning(
                "Увага",
                "Потрібна зона проєкту (рамка на карті), контур поля (KML) або KML зони SRTM.",
            )
            return
        if getattr(self, "geo_ref", None) is None:
            messagebox.showwarning("Увага", "Проект не має гео-прив'язки (імпортуйте KML з Google Earth)!")
            return
            
        try:
            res = float(self.var_srtm_res.get().replace(',', '.'))
        except:
            res = 30.0
            
        def _task():
            try:
                boundary = geom
                count = self.topo.fetch_srtm_grid(boundary, self.geo_ref, res)
                self.root.after(0, _on_success, count)
            except Exception as e:
                self.root.after(0, _on_error, str(e))
                
        def _on_success(count):
            self.cached_contours = []
            self.zoom_to_fit()
            self.redraw()
            if hasattr(self.control_panel, 'btn_srtm'): 
                self.control_panel.btn_srtm.config(state=tk.NORMAL, text="🌐 Завантажити з супутника")
            messagebox.showinfo(
                "Успіх",
                f"Побудовано {count} точок висоти (_srtm_ за наявності тайлів, інакше API Open-Meteo).",
            )
            
        def _on_error(err):
            if hasattr(self.control_panel, 'btn_srtm'): 
                self.control_panel.btn_srtm.config(state=tk.NORMAL, text="🌐 Завантажити з супутника")
            messagebox.showerror("Помилка", f"Не вдалося завантажити SRTM:\n{err}")

        if hasattr(self.control_panel, 'btn_srtm'): 
            self.control_panel.btn_srtm.config(state=tk.DISABLED, text="⏳ Очікування API...")

        threading.Thread(target=_task, daemon=True).start()

    def zoom_to_fit(self):
        self.canvas.update_idletasks()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            cw, ch = 800, 600 # fallback
            
        points_to_check = []
        for b in self.field_blocks:
            points_to_check.extend(b["ring"])
        if self.points:
            points_to_check.extend(self.points)
        for sm in self._all_submain_lines():
            points_to_check.extend(sm)
        for lat in self._flatten_all_lats():
            points_to_check.extend(list(lat.coords))
        if self.topo.elevation_points:
            points_to_check.extend([(p[0], p[1]) for p in self.topo.elevation_points])
        if self.topo.srtm_boundary_pts_local:
            points_to_check.extend(self.topo.srtm_boundary_pts_local)
            
        if not points_to_check:
            self.zoom, self.offset_x, self.offset_y = 0.7, cw/2, ch/2
            return
            
        min_x = min(p[0] for p in points_to_check)
        max_x = max(p[0] for p in points_to_check)
        min_y = min(p[1] for p in points_to_check)
        max_y = max(p[1] for p in points_to_check)
        
        width = max_x - min_x
        height = max_y - min_y
        
        if width == 0 and height == 0:
            self.zoom = 1.0
            self.offset_x = cw/2 - min_x
            self.offset_y = ch/2 - min_y
            return
            
        margin = 0.05
        use_w = cw * (1 - 2*margin)
        use_h = ch * (1 - 2*margin)
        
        zoom_x = use_w / width if width > 0 else float('inf')
        zoom_y = use_h / height if height > 0 else float('inf')
        self.zoom = min(zoom_x, zoom_y)
        
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        
        self.offset_x = cw/2 - center_x * self.zoom
        self.offset_y = ch/2 - center_y * self.zoom

    def _draw_workspace_background(self) -> None:
        """Фонова сітка 100×100 м у локальних координатах; підказка, коли ще немає блоків."""
        _GRID_STEP_M = 100.0
        _MAX_GRID_LINES = 420  # при дуже сильному віддаленні не малюємо — інакче гальмує Tk

        try:
            cw = max(1, int(self.canvas.winfo_width()))
            ch = max(1, int(self.canvas.winfo_height()))
        except tk.TclError:
            return
        w0 = self.to_world(0, 0)
        w1 = self.to_world(cw, ch)
        wmin_x = min(w0[0], w1[0])
        wmax_x = max(w0[0], w1[0])
        wmin_y = min(w0[1], w1[1])
        wmax_y = max(w0[1], w1[1])
        span_x = wmax_x - wmin_x
        span_y = wmax_y - wmin_y

        step = _GRID_STEP_M
        grid_fill = "#1e252c"
        if span_x / step <= _MAX_GRID_LINES and span_y / step <= _MAX_GRID_LINES:
            gx0 = math.floor(wmin_x / step) * step
            gy0 = math.floor(wmin_y / step) * step
            x = gx0
            while x <= wmax_x + step * 0.05:
                xa, ya = self.to_screen(x, wmin_y)
                xb, yb = self.to_screen(x, wmax_y)
                self.canvas.create_line(xa, ya, xb, yb, fill=grid_fill, width=1, tags="workspace_bg")
                x += step
            y = gy0
            while y <= wmax_y + step * 0.05:
                xa, ya = self.to_screen(wmin_x, y)
                xb, yb = self.to_screen(wmax_x, y)
                self.canvas.create_line(xa, ya, xb, yb, fill=grid_fill, width=1, tags="workspace_bg")
                y += step

        ox, oy = self.to_screen(0.0, 0.0)
        if -40 <= ox <= cw + 40 and -40 <= oy <= ch + 40:
            self.canvas.create_line(ox - 10, oy, ox + 10, oy, fill="#3d4752", width=1, tags="workspace_bg")
            self.canvas.create_line(ox, oy - 10, ox, oy + 10, fill="#3d4752", width=1, tags="workspace_bg")
            self.canvas.create_text(
                ox + 12,
                oy - 8,
                text="0,0",
                fill="#4a5560",
                font=("Consolas", 8),
                anchor=tk.NW,
                tags="workspace_bg",
            )

        if self.field_blocks or self.points:
            return
        m = self.mode.get()
        if m == "DRAW":
            msg = "ЛКМ — вершини блоку поля · ПКМ — замкнути контур"
        else:
            msg = (
                "Порожній проєкт · пласка модель (без точок рельєфу).\n"
                "Натисніть D або «Малювання» → «Контур блоку», потім клацайте ЛКМ по полотні."
            )
        self.canvas.create_text(
            cw // 2,
            ch // 2,
            text=msg,
            fill="#4a5663",
            font=("Segoe UI", 11),
            justify="center",
            tags="workspace_bg",
        )

    def enable_zoom_box_mode(self):
        self.mode.set("ZOOM_BOX")
        self.action.set("ADD")
        self._zoom_box_start = None
        self._zoom_box_end = None
        self.redraw()

    def redraw(self, skip_heavy_canvas_layers: bool = False):
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return
        if not skip_heavy_canvas_layers:
            self._cancel_debounced_full_redraw()
        self.canvas.delete("all")
        self._draw_workspace_background()

        if not self.snap_enabled:
            self.canvas.create_text(20, 20, text="🚫 ПРИВ'ЯЗКА ВИМКНЕНА (Натисніть 'S' або 'І' щоб увімкнути)", fill="#FF3366", font=("Arial", 12, "bold"), anchor=tk.NW)
        elif self.snap_disabled_next_click:
            self.canvas.create_text(20, 20, text="🚫 ПРИВ'ЯЗКА ВИМКНЕНА ДЛЯ НАСТУПНОГО КЛІКУ (ЛКМ)", fill="#FF3366", font=("Arial", 12, "bold"), anchor=tk.NW)

        dyn_font_size = max(8, min(14, int(10 * self.zoom)))
        dyn_font = ("Arial", dyn_font_size, "bold")
        
        area_ha = 0
        try:
            u_area = self.field_union_polygon()
            if u_area is not None and not u_area.is_empty:
                area_ha = u_area.area / 10000
        except Exception:
            pass

        for bi, b in enumerate(self.field_blocks):
            ring = b["ring"]
            if len(ring) > 1:
                scr = [self.to_screen(*p) for p in ring]
                self.canvas.create_line(scr + [scr[0]], fill="#00FFCC", width=4)
                cx = sum(p[0] for p in ring) / len(ring)
                cy = sum(p[1] for p in ring) / len(ring)
                sx, sy = self.to_screen(cx, cy)
                self.canvas.create_text(sx, sy, text=str(bi + 1), fill="#00FFCC", font=("Arial", 9, "bold"))
                bavg = (self.calc_results.get("block_avg_emit_lph") or {}).get(str(bi))
                if bavg is not None and bool(self.calc_results.get("sections")):
                    self.canvas.create_text(
                        sx,
                        sy + 14,
                        text=f"ØQ {float(bavg):.2f} л/г",
                        fill="#AAEEDD",
                        font=("Arial", 8, "bold"),
                    )
        if self.points:
            scr = [self.to_screen(*p) for p in self.points]
            if len(scr) > 1:
                self.canvas.create_line(scr + ([scr[0]] if self.is_closed else []), fill="#00FFCC", width=4)
            if not self.is_closed and self.mode.get() == "DRAW":
                for sx, sy in scr:
                    self.canvas.create_oval(sx-3, sy-3, sx+3, sy+3, fill="#00FFCC")

        if self.show_srtm_boundary_overlay.get() and getattr(self.topo, 'srtm_boundary_pts_local', None):
            scr_srtm = [self.to_screen(*p) for p in self.topo.srtm_boundary_pts_local]
            if len(scr_srtm)>1: 
                self.canvas.create_line(scr_srtm+([scr_srtm[0]]), fill="#FF33FF", dash=(4,4), width=2)

        if self.show_srtm_tile_footprints.get() and getattr(self, "geo_ref", None):
            try:
                from modules.geo_module import srtm_tiles

                ref_lon, ref_lat = self.geo_ref
                for ring in srtm_tiles.local_rings_for_cached_srtm_tiles((ref_lon, ref_lat)):
                    scr = [self.to_screen(*p) for p in ring]
                    if len(scr) > 2:
                        self.canvas.create_line(
                            scr,
                            fill="#55AADD",
                            dash=(8, 5),
                            width=1,
                            tags="srtm_tile_grid",
                        )
            except Exception:
                pass
                
        if self.show_topo_points.get() and self.topo.elevation_points:
            for pt in self.topo.elevation_points:
                sx, sy = self.to_screen(pt[0], pt[1])
                self.canvas.create_oval(sx-2, sy-2, sx+2, sy+2, fill="#FF6600", outline="")
        
        _cg = self.contour_clip_geometry() if (self.show_topo_computation_zone.get() and (self.mode.get() == "TOPO" or self.show_contours.get())) else None
        if _cg is not None:
            try:
                grid_size = float(self.var_topo_grid.get().replace(',', '.'))
                minx, miny, maxx, maxy = _cg.bounds
                sx1, sy1 = self.to_screen(minx - grid_size, miny - grid_size)
                sx2, sy2 = self.to_screen(maxx + grid_size, maxy + grid_size)
                self.canvas.create_rectangle(sx1, sy1, sx2, sy2, outline="#888888", dash=(4,4), width=2, tags="topo_bounds")
                self.canvas.create_text(sx1, sy1-10, text="Зона обчислення рельєфу", fill="#888888", font=("Arial", 9, "bold"), anchor=tk.SW, tags="topo_bounds")
            except Exception as e: 
                self.canvas.create_text(100, 100, text=f"TOPO Bounds Error: {e}", fill="red", font=("Arial", 12, "bold"))
            
        if (
            self.show_contours.get()
            and self.cached_contours
            and not skip_heavy_canvas_layers
        ):
            for contour in self.cached_contours:
                z_val = contour["z"]
                geom = contour["geom"]
                
                is_major = abs(z_val - round(z_val)) < 0.001
                color = "#9C661F" if is_major else "#8B4513"
                width = 2 if is_major else 1
                dash = None if is_major else (2, 2)
                
                geoms_to_draw = getattr(geom, 'geoms', [geom])
                for g in geoms_to_draw:
                    if g.geom_type == 'LineString' and len(g.coords) > 1:
                        pts = [coord for pt in g.coords for coord in self.to_screen(*pt)]
                        self.canvas.create_line(
                            *pts,
                            fill=color,
                            width=width,
                            dash=dash,
                            smooth=False,
                        )
                        # Підписи на ізолініях рельєфу (і для major, і для minor).
                        if len(g.coords) >= 3:
                            mid_idx = len(g.coords) // 2
                            mx, my = self.to_screen(*g.coords[mid_idx])
                            self.canvas.create_text(
                                mx,
                                my,
                                text=f"{z_val:.1f}",
                                fill="#66FF00" if is_major else "#52E020",
                                font=("Arial", 10 if is_major else 9, "bold"),
                            )

        is_calculated = bool(self.calc_results.get("sections"))
        total_drip = sum(lat.length for lat in self._flatten_all_lats())
        sm_for_conn = self._hydraulic_submain_lines() if is_calculated else []

        def _lat_line_color(li: int, calculated: bool, base_ok: str, base_pre: str) -> str:
            if not calculated:
                return base_pre
            aud = (self.calc_results.get("lateral_pressure_audit") or {}).get(f"lat_{li}")
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

        def _draw_lateral_with_audit(
            lat, block, li_use: int, line_width: int, manual: bool, block_bi: int
        ):
            pre = "#FFCC66" if manual else "#336699"
            okc = "#90EE90"
            if not is_calculated:
                fill = pre if manual else "#336699"
                for piece in self._split_lateral_at_block_submains(lat, block):
                    self.canvas.create_line(
                        [self.to_screen(*c) for c in piece.coords],
                        fill=fill,
                        width=line_width,
                    )
                return
            abi_map = self._safe_active_block_idx()
            mask_outlines_on = bool(self.var_show_press_zone_outlines_on_map.get())
            if (
                mask_outlines_on
                and abi_map is not None
                and int(block_bi) == int(abi_map)
            ):
                for piece in self._split_lateral_at_block_submains(lat, block):
                    self.canvas.create_line(
                        [self.to_screen(*c) for c in piece.coords],
                        fill=okc,
                        width=line_width,
                    )
                return
            aud = (self.calc_results.get("lateral_pressure_audit") or {}).get(f"lat_{li_use}")
            has_wings = aud is not None and (
                aud.get("status_l1") is not None or aud.get("status_l2") is not None
            )
            if has_wings:
                try:
                    conn = lat_sol.connection_distance_along_lateral(
                        lat, sm_for_conn, snap_m=self._submain_lateral_snap_m()
                    )
                except Exception:
                    conn = 0.0
                conn = max(0.0, min(float(lat.length), float(conn)))
                for piece, wing in self._colored_spans_for_lateral_wings(lat, block, conn):
                    stw = aud.get("status_l1") if wing == 1 else aud.get("status_l2")
                    lc = self._audit_wing_line_color(stw, okc)
                    self.canvas.create_line(
                        [self.to_screen(*c) for c in piece.coords],
                        fill=lc,
                        width=line_width,
                    )
            else:
                lc = _lat_line_color(li_use, True, okc, okc)
                for piece in self._split_lateral_at_block_submains(lat, block):
                    self.canvas.create_line(
                        [self.to_screen(*c) for c in piece.coords],
                        fill=lc,
                        width=line_width,
                    )

        draw_laterals = True
        lat_draw_idx = 0
        for bi, b in enumerate(self.field_blocks):
            if not is_calculated:
                for lat in b.get("auto_laterals") or []:
                    if draw_laterals:
                        _draw_lateral_with_audit(lat, b, lat_draw_idx, 2, False, bi)
                    lat_draw_idx += 1
            else:
                for grp in self._per_submain_ordered_auto_laterals(b):
                    n_g = len(grp)
                    if n_g == 0:
                        continue
                    show_g = self._visible_auto_lateral_indices(n_g)
                    for i, lat in enumerate(grp):
                        if i not in show_g:
                            lat_draw_idx += 1
                            continue
                        if draw_laterals:
                            gidx = self._global_lat_flat_index(lat)
                            li_use = gidx if gidx is not None else lat_draw_idx
                            _draw_lateral_with_audit(lat, b, li_use, 2, False, bi)
                        lat_draw_idx += 1
            for lat in b.get("manual_laterals") or []:
                if draw_laterals:
                    gidx = self._global_lat_flat_index(lat)
                    li_use = gidx if gidx is not None else lat_draw_idx
                    _draw_lateral_with_audit(lat, b, li_use, 3, True, bi)
                lat_draw_idx += 1

        # Ізолінії виливу будуються з точок емітера — виглядають як «кожна крапельниця».
        # Якщо на мапі потрібні лише контури зон переливу/недоливу — не накладаємо ізолінії.
        if (
            is_calculated
            and self.var_show_emitter_flow.get()
            and not bool(self.var_show_press_zone_outlines_on_map.get())
            and not skip_heavy_canvas_layers
        ):
            em_db = self.calc_results.get("emitters") or {}
            if em_db:
                lat_list = self._flatten_all_lats()
                lpa = self.calc_results.get("lateral_pressure_audit") or {}
                try:
                    h_lo_lim = float(
                        self.var_emit_h_press_min.get().replace(",", ".")
                    )
                except Exception:
                    h_lo_lim = 0.0
                try:
                    h_hi_lim = float(
                        self.var_emit_h_press_max.get().replace(",", ".")
                    )
                except Exception:
                    h_hi_lim = 0.0
                band_on = (h_lo_lim > 1e-9) or (h_hi_lim > 1e-9)
                _h_band_tol = 0.02
                sm_for_conn = self._hydraulic_submain_lines()
                sample_all = []

                def _append_wing_rows(lat, pay, wing_rows, conn, sign_xa: float):
                    """sign_xa: -1 для L1 (along = conn - xa), +1 для L2 (along = conn + xa)."""
                    for row in wing_rows or []:
                        qe = float(row.get("q_emit", 0))
                        if qe <= 1e-4:
                            continue
                        xa = float(row.get("x", 0))
                        along = conn + sign_xa * xa
                        along = max(0.0, min(float(lat.length), float(along)))
                        h_em = float(row.get("h", 0))
                        if not band_on:
                            continue
                        try:
                            pt = lat.interpolate(along)
                            sample_all.append((float(pt.x), float(pt.y), float(qe), float(h_em)))
                        except Exception:
                            pass

                def _densify_lateral_wing(lat, wing_rows, conn, sign_xa: float, n_between: int = 4):
                    """Додаткові точки між емітерами на крилі — щоб q/h поле змінювалось уздовж латераля і ізолінії його перетинали."""
                    pts_along = []
                    for row in wing_rows or []:
                        qe = float(row.get("q_emit", 0))
                        if qe <= 1e-4:
                            continue
                        xa = float(row.get("x", 0))
                        along = conn + sign_xa * xa
                        along = max(0.0, min(float(lat.length), float(along)))
                        h_em = float(row.get("h", 0))
                        try:
                            pt = lat.interpolate(along)
                            pts_along.append((along, float(pt.x), float(pt.y), qe, h_em))
                        except Exception:
                            pass
                    pts_along.sort(key=lambda t: t[0])
                    for i in range(len(pts_along) - 1):
                        a1, x1, y1, q1, h1 = pts_along[i]
                        a2, x2, y2, q2, h2 = pts_along[i + 1]
                        span = abs(a2 - a1)
                        if span < 1e-6:
                            continue
                        n_sub = min(8, max(2, int(span / 2.4)))
                        for k in range(1, n_sub):
                            t = k / n_sub
                            al = a1 + t * (a2 - a1)
                            try:
                                pt = lat.interpolate(al)
                            except Exception:
                                continue
                            qe = q1 + t * (q2 - q1)
                            h_em = h1 + t * (h2 - h1)
                            sample_all.append((float(pt.x), float(pt.y), float(qe), float(h_em)))

                bad_wing_status = frozenset({"overflow", "underflow", "both"})

                def _lateral_needs_densify(li: int) -> bool:
                    aud = lpa.get(f"lat_{li}")
                    if not aud:
                        return False
                    if aud.get("status") in bad_wing_status:
                        return True
                    return (
                        aud.get("status_l1") in bad_wing_status
                        or aud.get("status_l2") in bad_wing_status
                    )

                for key, pay in em_db.items():
                    if not str(key).startswith("lat_"):
                        continue
                    try:
                        li = int(str(key).split("_", 1)[1])
                    except (ValueError, IndexError):
                        continue
                    if li < 0 or li >= len(lat_list):
                        continue
                    lat = lat_list[li]
                    if lat.is_empty or lat.length < 1e-6:
                        continue
                    try:
                        conn = lat_sol.connection_distance_along_lateral(
                            lat, sm_for_conn, snap_m=self._submain_lateral_snap_m()
                        )
                    except Exception:
                        conn = 0.0
                    conn = max(0.0, min(float(lat.length), float(conn)))
                    _append_wing_rows(lat, pay, pay.get("L1"), conn, -1.0)
                    _append_wing_rows(lat, pay, pay.get("L2"), conn, 1.0)
                    if band_on and _lateral_needs_densify(li):
                        _densify_lateral_wing(lat, pay.get("L1"), conn, -1.0)
                        _densify_lateral_wing(lat, pay.get("L2"), conn, 1.0)

                def _classify_emit(hm: float) -> str:
                    ok_lo = (True if h_lo_lim <= 1e-9 else hm >= h_lo_lim - _h_band_tol)
                    ok_hi = (True if h_hi_lim <= 1e-9 else hm <= h_hi_lim + _h_band_tol)
                    if ok_lo and ok_hi:
                        return "inband"
                    if not ok_hi:
                        return "overflow"
                    return "underflow"

                def _downsample(arr, max_pts=2500):
                    n = len(arr)
                    if n <= max_pts:
                        return arr
                    step = max(1, n // max_pts)
                    return arr[::step]

                # Єдине поле q_emit: так ізолінії не "борються" між незалежними полями.
                pts_h_classify = [
                    (float(x), float(y), float(h)) for x, y, _q, h in sample_all
                ]
                if len(pts_h_classify) > 5000:
                    _st = max(1, len(pts_h_classify) // 5000)
                    pts_h_classify = pts_h_classify[::_st]

                pts_q = [(x, y, q) for x, y, q, _h in sample_all]
                pts_q = _downsample(pts_q, max_pts=2200)

                if pts_q:
                    lo_q = min(p[2] for p in pts_q)
                    hi_q = max(p[2] for p in pts_q)
                else:
                    lo_q = 0.0
                    hi_q = 0.0

                sig = (
                    len(pts_q),
                    round(sum((p[0] for p in pts_q), 3), 3),
                    round(sum((p[1] for p in pts_q), 3), 3),
                    round(sum((p[2] for p in pts_q), 3), 3),
                    round(lo_q, 4),
                    round(hi_q, 4),
                    round(h_lo_lim, 4),
                    round(h_hi_lim, 4),
                    "idw",
                )
                _emit_boundary = self.field_union_polygon()
                if _emit_boundary is None or _emit_boundary.is_empty:
                    _abi_fb = self._safe_active_block_idx()
                    if _abi_fb is not None and 0 <= _abi_fb < len(self.field_blocks):
                        _emit_boundary = self._block_poly(self.field_blocks[_abi_fb])
                _emit_bsig = (
                    tuple(round(x, 1) for x in _emit_boundary.bounds)
                    if _emit_boundary is not None and not _emit_boundary.is_empty
                    else (0, 0, 0, 0)
                )
                # Версія кешу: зміна рівнів / сітки / спрощення інвалідує старий кеш.
                _emit_flow_n_levels = 6
                sig = sig + (
                    _emit_bsig,
                    len(sample_all),
                    _emit_flow_n_levels,
                    "v4",
                )

                cache = getattr(self, "_emit_isolines_cache", None) or {}
                contours = []
                if cache.get("sig") == sig:
                    contours = list(cache.get("contours") or [])
                else:
                    boundary = _emit_boundary
                    if boundary is not None and not boundary.is_empty and len(pts_q) >= 8 and hi_q > lo_q + 1e-9:
                        q_step = max(
                            (hi_q - lo_q) / float(_emit_flow_n_levels), 1e-6
                        )
                        nq = len(pts_q)
                        # Грубіша сітка за горизонталями рельєфу — менше сегментів ізоліній виливу.
                        if nq < 900:
                            grid_m = 11.0
                        elif nq < 1600:
                            grid_m = 14.0
                        elif nq < 2400:
                            grid_m = 17.0
                        else:
                            grid_m = 21.0
                        tpe = TopoEngine()
                        # Фіксований метод: IDW.
                        tpe.power = 2.0
                        try:
                            contours = tpe.generate_contours(
                                boundary=boundary,
                                step_z=q_step,
                                grid_size=grid_m,
                                elevation_points=pts_q,
                            ) or []
                        except Exception:
                            contours = []
                    self._emit_isolines_cache = {
                        "sig": sig,
                        "contours": contours,
                    }

                def _palette(cls_name: str, t: float) -> str:
                    t = max(0.0, min(1.0, float(t)))
                    if cls_name == "inband":
                        c0, c1 = (198, 239, 255), (119, 214, 255)   # light blue
                    elif cls_name == "overflow":
                        c0, c1 = (255, 170, 90), (255, 72, 0)       # hot orange
                    else:
                        c0, c1 = (241, 208, 110), (178, 132, 38)    # yellow/ochre
                    r = int(c0[0] + (c1[0] - c0[0]) * t)
                    g = int(c0[1] + (c1[1] - c0[1]) * t)
                    b = int(c0[2] + (c1[2] - c0[2]) * t)
                    return f"#{r:02x}{g:02x}{b:02x}"

                _emit_idw_power = 2.0
                _h_buckets = None
                if pts_h_classify:
                    _h_buckets = _build_point_buckets(
                        pts_h_classify, float(_BUCKET_CELL_M)
                    )

                def _h_at_xy(mx: float, my: float) -> float:
                    if not pts_h_classify:
                        return 0.0
                    if _h_buckets is None:
                        return _idw_z(mx, my, pts_h_classify, _emit_idw_power)
                    return _z_at_grid_node(
                        mx,
                        my,
                        _h_buckets,
                        float(_BUCKET_CELL_M),
                        pts_h_classify,
                        _emit_idw_power,
                    )

                def _smooth_coords(coords, passes=0):
                    out = [(float(x), float(y)) for x, y in coords]
                    for _ in range(max(0, int(passes))):
                        if len(out) < 3:
                            break
                        nxt = [out[0]]
                        for i in range(len(out) - 1):
                            x1, y1 = out[i]
                            x2, y2 = out[i + 1]
                            qx, qy = (0.75 * x1 + 0.25 * x2), (0.75 * y1 + 0.25 * y2)
                            rx, ry = (0.25 * x1 + 0.75 * x2), (0.25 * y1 + 0.75 * y2)
                            nxt.append((qx, qy))
                            nxt.append((rx, ry))
                        nxt.append(out[-1])
                        out = nxt
                    return out

                _emit_line_tol = 0.55
                try:
                    if _emit_boundary is not None and not _emit_boundary.is_empty:
                        ex0, ey0, ex1, ey1 = _emit_boundary.bounds
                        _emit_line_tol = max(
                            0.4,
                            min(3.2, 0.00022 * math.hypot(ex1 - ex0, ey1 - ey0)),
                        )
                except Exception:
                    pass

                n_in = n_ov = n_un = 0
                for c in contours:
                    z = float(c.get("z", 0.0))
                    if z < lo_q - 1e-9 or z > hi_q + 1e-9:
                        continue
                    t = 0.0 if hi_q <= lo_q + 1e-9 else (z - lo_q) / (hi_q - lo_q)
                    g = c.get("geom")
                    if g is None or g.is_empty:
                        continue
                    geoms = getattr(g, "geoms", [g])
                    for gg_raw in geoms:
                        if gg_raw.geom_type != "LineString" or len(gg_raw.coords) <= 1:
                            continue
                        try:
                            gsimp = gg_raw.simplify(
                                _emit_line_tol, preserve_topology=True
                            )
                        except Exception:
                            gsimp = gg_raw
                        if gsimp is None or gsimp.is_empty:
                            continue
                        if gsimp.geom_type == "LineString":
                            line_chunks = [gsimp]
                        elif gsimp.geom_type == "MultiLineString":
                            line_chunks = [
                                s
                                for s in gsimp.geoms
                                if s.geom_type == "LineString"
                                and len(s.coords) >= 2
                            ]
                        else:
                            line_chunks = [gg_raw]
                        for gg in line_chunks:
                            draw_coords = list(gg.coords)
                            if len(draw_coords) >= 3:
                                draw_coords = _smooth_coords(draw_coords, passes=0)
                            g_draw = LineString(draw_coords)
                            mid = g_draw.interpolate(0.5, normalized=True)
                            if pts_h_classify:
                                h_mid = _h_at_xy(float(mid.x), float(mid.y))
                                cls_name = _classify_emit(h_mid)
                            else:
                                cls_name = "inband"
                            if cls_name == "inband":
                                n_in += 1
                            elif cls_name == "overflow":
                                n_ov += 1
                            else:
                                n_un += 1
                            col = _palette(cls_name, t)
                            self.canvas.create_line(
                                [self.to_screen(*pt) for pt in g_draw.coords],
                                fill=col,
                                width=1,
                                dash=(2, 2),
                            )
                try:
                    _ch = max(100, int(self.canvas.winfo_height()))
                except tk.TclError:
                    _ch = 600
                if band_on:
                    _leg = (
                        "Ізолінії виливу (6 рівнів, IDW, спрощені лінії; усі блоки; колір за тиском як у латералів): "
                        "норма — світло-блакитні, перелив — гаряча помаранчева гама, "
                        "недолив — жовто-охряна гама"
                    )
                    _leg += f" (контурів: норм={n_in}, перелив={n_ov}, недолив={n_un})"
                else:
                    _leg = "Ізолінії виливу: діапазон тиску не задано — не показуються"
                self.canvas.create_text(12, _ch - 6, text=_leg, fill="#AAAAAA", font=("Arial", 7), anchor=tk.SW)
            
        if is_calculated:
            label_pts = self.calc_results.get("section_label_pos") or {}
            section_parts = self._sections_for_canvas_draw()
            represented_sm = {int(s.get("sm_idx", -1)) for s in section_parts}
            for sec in section_parts:
                coords = sec["coords"]
                self.canvas.create_line(
                    [self.to_screen(x, y) for x, y in coords],
                    fill=self._section_draw_color(sec),
                    width=10,
                )
                if len(coords) < 2:
                    continue
                geom = LineString(coords)
                lk = int(sec["label_key"])
                si = int(sec.get("sub_idx", 0))
                if (
                    self._moving_section_label_key is not None
                    and lk == int(self._moving_section_label_key)
                    and si == int(self._moving_section_label_sub_idx)
                    and int(sec.get("sm_idx", -1)) == int(self._moving_section_label_sm_idx)
                    and self._moving_section_label_preview is not None
                ):
                    midpt = Point(
                        self._moving_section_label_preview[0],
                        self._moving_section_label_preview[1],
                    )
                else:
                    lp = self._section_label_lookup_pos(
                        label_pts,
                        lk,
                        si,
                        int(sec.get("sm_idx", -1)),
                    )
                    if lp:
                        midpt = Point(lp[0], lp[1])
                    else:
                        midpt = geom.interpolate(0.5, normalized=True)
                lc = len(coords)
                mi = min(max(0, lc // 2 - 1), lc - 2)
                dx = coords[mi + 1][0] - coords[mi][0]
                dy = coords[mi + 1][1] - coords[mi][1]
                if abs(dx) + abs(dy) < 1e-9:
                    dx = coords[-1][0] - coords[0][0]
                    dy = coords[-1][1] - coords[0][1]
                angle_rad = math.atan2(dy, dx)
                tk_angle = -math.degrees(angle_rad)
                if tk_angle < -90 or tk_angle > 90:
                    tk_angle += 180
                    angle_rad += math.pi
                off_x = 10 * math.cos(angle_rad + math.pi/2)
                off_y = -10 * math.sin(angle_rad + math.pi/2)
                sx, sy = self.to_screen(midpt.x, midpt.y)
                txt = f"{sec['mat']} d{sec['d']}/{sec['pn']} L={sec['L']:.1f}m"
                is_selected = (
                    self._moving_section_label_key is not None
                    and lk == int(self._moving_section_label_key)
                    and si == int(self._moving_section_label_sub_idx)
                    and int(sec.get("sm_idx", -1)) == int(self._moving_section_label_sm_idx)
                )
                if is_selected:
                    fill_main = "#FFFF00"
                    fill_shadow = "#000000"
                else:
                    fill_main = "#000000"
                    fill_shadow = "#FFFFFF"
                self.canvas.create_text(
                    sx + off_x,
                    sy + off_y,
                    text=txt,
                    fill=fill_main,
                    font=dyn_font,
                    angle=tk_angle,
                    anchor=tk.S,
                )
                self.canvas.create_text(
                    sx + off_x - 1,
                    sy + off_y - 1,
                    text=txt,
                    fill=fill_shadow,
                    font=dyn_font,
                    angle=tk_angle,
                    anchor=tk.S,
                )
            # Якщо після часткового редагування блоку для деяких сабмейнів ще немає секцій
            # у calc_results, все одно показуємо їх геометрію на полотні.
            for sm_i, sm in enumerate(self._all_submain_lines()):
                if sm_i in represented_sm:
                    continue
                if len(sm) < 2:
                    continue
                self.canvas.create_line(
                    [self.to_screen(*p) for p in sm],
                    fill="#FF3366",
                    width=8,
                )

        else:
            for sm in self._all_submain_lines() + ([self.active_submain] if len(self.active_submain) > 1 else []):
                for i in range(len(sm) - 1):
                    self.canvas.create_line(self.to_screen(*sm[i]), self.to_screen(*sm[i + 1]), fill="#FF3366", width=8)

        for vx, vy in self.get_valves():
            sx, sy = self.to_screen(vx, vy)
            node_r = max(3, min(8, int(5 * self.zoom)))
            self.canvas.create_oval(sx-node_r, sy-node_r, sx+node_r, sy+node_r, fill="#0066FF", outline="white", width=2)
            v_key = str((round(vx, 2), round(vy, 2)))
            if self.calc_results.get("valves") and v_key in self.calc_results["valves"]:
                v_res = self.calc_results["valves"][v_key]
                txt = f"H: {v_res['H']:.1f} м\nQ: {v_res['Q']:.1f} м³/г"
                spec = v_res.get("valve_h_max_m_spec")
                if spec is not None:
                    try:
                        txt += f"\nH макс (задано): {float(spec):.1f} м"
                    except (TypeError, ValueError):
                        pass
                if v_res.get("exceeds_valve_h_max"):
                    txt += "\n⚠ понад норму"
                fg = "#FF8888" if v_res.get("exceeds_valve_h_max") else "#FFD700"
                offset_y = int(15 * (self.zoom / 5.0))
                self.canvas.create_text(sx+1, sy+offset_y+1, text=txt, fill="#000000", font=dyn_font, anchor=tk.N, justify=tk.CENTER)
                self.canvas.create_text(sx, sy+offset_y, text=txt, fill=fg, font=dyn_font, anchor=tk.N, justify=tk.CENTER)

        if self._snap_point and self.action.get() == "ADD" and self.snap_enabled and not self.snap_disabled_next_click:
            sx, sy = self.to_screen(*self._snap_point)
            self.canvas.create_rectangle(sx-6, sy-6, sx+6, sy+6, outline="yellow", width=2)

        if self.show_topo_points.get():
            for px, py, pz in self.topo.elevation_points:
                sx, sy = self.to_screen(px, py)
                self.canvas.create_oval(sx-4, sy-4, sx+4, sy+4, fill="#FFD700", outline="black", width=1)
                self.canvas.create_text(sx+6, sy-6, text=f"{pz:.1f}m", fill="#FFD700", font=("Consolas", 9, "bold"), anchor=tk.SW)

        if self.show_srtm_boundary_overlay.get() and self.topo.srtm_boundary_pts_local:
            scr = [self.to_screen(*p) for p in self.topo.srtm_boundary_pts_local]
            if len(scr) > 1:
                self.canvas.create_polygon(scr + [scr[0]], fill="", outline="#888844", dash=(4,6), width=2)
                self.canvas.create_text(scr[0][0], scr[0][1]-15, text="Межа SRTM", fill="#888844", font=("Arial", 9, "bold"), anchor=tk.W)

        if bool(self.var_show_press_zone_outlines_on_map.get()) and not skip_heavy_canvas_layers:
            try:
                bun = self._bad_pressure_emitter_details_active_block()
                if bun["band_on"] and bun["has_calc"]:
                    items = bun.get("items") or []
                    ov_items = [it for it in items if it.get("overflow")]
                    un_items = [it for it in items if it.get("underflow")]
                    lw = max(3, min(6, int(max(self.zoom, 1.0) + 1)))
                    if ov_items:
                        zone_ov = self._bad_emitter_pressure_zone_clipped(
                            {**bun, "items": ov_items}
                        )
                        if zone_ov is not None and not zone_ov.is_empty:
                            self._draw_emitter_pressure_zone_on_canvas(
                                zone_ov,
                                outline="#FF5533",
                                width=lw,
                                canvas_tag="bad_emit_zone_overflow",
                            )
                    if un_items:
                        zone_un = self._bad_emitter_pressure_zone_clipped(
                            {**bun, "items": un_items}
                        )
                        if zone_un is not None and not zone_un.is_empty:
                            self._draw_emitter_pressure_zone_on_canvas(
                                zone_un,
                                outline="#E8C547",
                                width=lw,
                                canvas_tag="bad_emit_zone_underflow",
                            )
            except Exception:
                pass

        for seg in getattr(self, "scene_lines", []) or []:
            if len(seg) >= 2:
                scr_sl = [self.to_screen(float(p[0]), float(p[1])) for p in seg]
                if len(scr_sl) >= 2:
                    self.canvas.create_line(
                        scr_sl,
                        fill="#9AA0AA",
                        dash=(5, 4),
                        width=2,
                        tags="scene_lines",
                    )

        self._draw_trunk_map_on_canvas()
        self._draw_canvas_selection_layer()
        self._draw_canvas_polyline_and_route_drafts()

        try:
            e_step = max(0.01, float(self.var_emit_step.get().replace(',','.')))
            e_flow = float(self.var_emit_flow.get().replace(',','.'))
            q = (total_drip/e_step*e_flow)/1000
            q_lbl = "Q(comp)" if self._emitter_compensated_effective() else "Q(nom)"
            base_stats = f"Площа: {area_ha:.2f} га\nВузлів: {len(self.get_valves())} шт\n{q_lbl}: {q:.2f} м³/год"
            curr_stats = self.control_panel.stats_label.cget("text")
            if "📏" in curr_stats:
                last_line = curr_stats.split("\n")[-1]
                self.control_panel.stats_label.config(text=f"{base_stats}\n{last_line}")
            else:
                self.control_panel.stats_label.config(text=base_stats)
        except: pass

        try:
            self.refresh_block_out_of_range_emitters_panel()
        except Exception:
            pass

        try:
            self._schedule_embedded_map_overlay_refresh()
        except Exception:
            pass

    def run_calculation(self):
        if not self._all_submain_lines():
            messagebox.showwarning("Увага", "Намалюйте хоча б один сабмейн!")
            return
        if not self._all_submains_have_connected_laterals():
            dmax = self._submain_lateral_snap_m()
            messagebox.showwarning(
                "Увага",
                f"Кожен сабмейн має перетинати латераль або бути поруч з нею (≤{dmax:.2f} м — "
                "див. «Керування»). Замкніть ручну dripline ПКМ біля сабмейну або збільшіть допуск.",
            )
            return
        old_label_pts = dict(self.calc_results.get("section_label_pos") or {})
        self.reset_calc()
        try:
            sm_lines, sm_blocks = self._all_submain_lines_with_block_indices()
            e_steps, e_flows = self._per_lateral_emit_steps_flows()
            ref_bi = int(sm_blocks[0]) if sm_blocks else 0
            _m, _p = self._derive_hydro_mat_pn_from_allowed(self._allowed_pipes_for_block_index(ref_bi))
            data = {
                "e_step": float(self.var_emit_step.get().replace(',', '.')),
                "e_flow": float(self.var_emit_flow.get().replace(',', '.')),
                "e_steps": e_steps,
                "e_flows": e_flows,
                "v_max": float(self.var_v_max.get().replace(',', '.')),
                "v_min": float(self.var_v_min.get().replace(',', '.')),
                "num_sec": int(self.var_num_sec.get()),
                "fixed_sec": self.var_fixed_sec.get(),
                "mat_str": _m,
                "pn_str": _p,
                "all_lats": self._flatten_all_lats(),
                "submain_lines": sm_lines,
                "submain_block_idx": sm_blocks,
                "submain_section_lengths_by_sm": self._all_submain_section_lengths_by_sm(),
                "allowed_pipes": self.allowed_pipes,
                "allowed_pipes_blocks": self._build_allowed_pipes_blocks_list(),
                "pipes_db": self.pipe_db,
                "topo": self.topo,
                "lateral_solver_mode": self.var_lateral_solver_mode.get().strip().lower(),
                "emitter_compensated": self._emitter_compensated_effective(),
                "emitter_h_min_m": float(self.var_emit_h_min.get().replace(",", ".")),
                "emitter_h_ref_m": float(self.var_emit_h_ref.get().replace(",", ".")),
                "lateral_inner_d_mm": float(
                    (self.var_lat_inner_d_mm.get().strip() or "13.6").replace(",", ".")
                ),
                "emitter_h_press_min_m": float(
                    self.var_emit_h_press_min.get().replace(",", ".")
                ),
                "emitter_h_press_max_m": float(
                    self.var_emit_h_press_max.get().replace(",", ".")
                ),
                "emitter_k_coeff": float(
                    (self.var_emit_k_coeff.get().strip() or "0").replace(",", ".")
                ),
                "emitter_x_exp": float(
                    (self.var_emit_x_exp.get().strip() or "0").replace(",", ".")
                ),
                "emitter_kd_coeff": float(
                    (self.var_emit_kd_coeff.get().strip() or "1").replace(",", ".")
                ),
                "lateral_block_idx": self._lateral_block_indices(),
                "submain_topo_in_headloss": bool(getattr(self, "_submain_topo_in_headloss", True)),
                "valve_h_max_m": float(
                    (self.var_valve_h_max_m.get().strip() or "0").replace(",", ".")
                ),
                "valve_h_max_optimize": bool(self.var_valve_h_max_optimize.get()),
                "submain_lateral_snap_m": self._submain_lateral_snap_m(),
            }
            report, self.calc_results = self.engine.calculate_network(data)
            self._restore_section_label_positions(old_label_pts)
            self.last_report = report
            self.redraw()
            self.sync_hydro_pipe_summary()
            if hasattr(self, "control_panel"):
                self.control_panel.sync_report_block_selector()
                self.control_panel._render_block_report_text()
        except Exception as e: messagebox.showerror("Помилка", f"Некоректні дані: {e}")

    def run_stress_calculation(self):
        messagebox.showinfo(
            "Stress-тест",
            "Повна кнопка доступна у збірці DripCADUI (main_app.main).",
        )

    def close_polygon(self):
        if len(self.points) < 3:
            return
        if len(self.field_blocks) >= self.MAX_FIELD_BLOCKS:
            messagebox.showwarning("Увага", f"Максимум {self.MAX_FIELD_BLOCKS} блоків поля.")
            return
        self.field_blocks.append(self._new_field_block(self.points))
        self.points = []
        self.is_closed = False
        self.mode.set("DRAW")
        self._refresh_active_block_combo()
        self.redraw()
            
    def reset_temp(self):
        self.active_submain = []
        self.active_manual_lat = []
        self.dir_points = []
        self.ruler_start = None
        self._cut_line_start = None
        self._active_submain_block_idx = None
        self._active_draw_block_idx = None
        self._dir_target_block_idx = None
        self._current_live_end = None
        self._moving_section_label_key = None
        self._moving_section_label_sub_idx = None
        self._moving_section_label_sm_idx = None
        self._moving_section_label_preview = None
        self.redraw()

    def clear_all(self):
        self.reset_calc()
        self.field_blocks = []
        self._dir_target_block_idx = None
        self.points = []
        self.is_closed, self.ruler_start, self.geo_ref = False, None, None
        self.project_zone_bounds_local = None
        self.scene_lines = []
        self.trunk_map_nodes = []
        self.trunk_map_segments = []
        self._trunk_route_last_node_idx = None
        self._canvas_special_tool = None
        self._canvas_trunk_draft_world = None
        self._canvas_polyline_draft = []
        self._canvas_trunk_route_draft_indices = []
        self.is_georeferenced = False
        self.mode.set("VIEW")
        self.zoom, self.offset_x, self.offset_y = 0.7, 425, 375
        self.topo.clear()
        self.topo.clear_srtm_boundary()
        self.cached_contours = []
        
        try:
            with open(PIPES_DB_PATH, "r", encoding="utf-8") as f:
                self.pipe_db = json.load(f)
        except: pass
        
        self.allowed_pipes = {}
        for mat, pns in self.pipe_db.items():
            self.allowed_pipes[mat] = {}
            for pn, ods in pns.items():
                self.allowed_pipes[mat][pn] = list(ods.keys())

        avail = list(self.pipe_db.keys())
        if hasattr(self, "cb_mat"):
            self.cb_mat.config(values=avail)
        if avail:
            self.pipe_material.set(avail[0])
        self.update_pn_dropdown(skip_reset=True)
        self.sync_hydro_pipe_summary()
        self.sync_srtm_model_status()
        self.var_proj_name.set("Project_01")
        self.trunk_tree_data = self._default_trunk_tree_payload()
        self.trunk_tree_results = {}
        self.var_active_block_idx.set(0)
        self._refresh_active_block_combo()
        self.redraw()

    def open_lateral_field_calculator(self):
        script = PROJECT_ROOT / "lateral_field_calculator.py"
        if not script.is_file():
            messagebox.showerror("Помилка", f"Не знайдено файл:\n{script}")
            return
        try:
            subprocess.Popen([sys.executable, str(script)], cwd=str(PROJECT_ROOT))
        except OSError as e:
            messagebox.showerror("Помилка", str(e))

    def open_submain_telescope_calculator(self):
        script = PROJECT_ROOT / "submain_telescope_calculator.py"
        if not script.is_file():
            messagebox.showerror("Помилка", f"Не знайдено файл:\n{script}")
            return
        try:
            subprocess.Popen([sys.executable, str(script)], cwd=str(PROJECT_ROOT))
        except OSError as e:
            messagebox.showerror("Помилка", str(e))

    def open_export_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Параметри експорту")
        dlg.geometry("340x160")
        dlg.configure(bg="#1e1e1e")
        tk.Label(dlg, text="Експортувати кожну N-ту латераль (KML / Google Earth):", bg="#1e1e1e", fg="#00FFCC").pack(pady=(10, 2))
        tk.Entry(dlg, textvariable=self.export_lat_step_kml, justify="center", width=10, font=("Consolas", 11)).pack()
        tk.Label(
            dlg,
            text="DXF: лише ізолінії (Файл → Експорт ізоліній).\nГідравліка до експорту не потрібна.",
            bg="#1e1e1e",
            fg="#888888",
            justify=tk.CENTER,
        ).pack(pady=14)
        _b_ok_exp = tk.Button(dlg, text="OK", command=dlg.destroy, bg="#0066FF", fg="white")
        _b_ok_exp.pack()
        attach_tooltip(_b_ok_exp, "Закрити діалог; крок експорту KML зберігається в проєкті.")

    def open_pipe_selector(self, scope="project"):
        pipe_allow_ref = self.allowed_pipes
        editor_target_block_bi = None
        dlg_title = f"Вибір дозволених труб для проекту: {self.var_proj_name.get()}"
        scope_body = "Відмітьте труби, які можна використовувати в цьому проєкті (глобально, allowed_pipes у JSON)."
        if scope == "block":
            bi = self._safe_active_block_idx()
            if bi is None:
                messagebox.showwarning("Увага", "Немає блоків поля.")
                return
            editor_target_block_bi = bi
            blk = self.field_blocks[bi]
            p = blk.setdefault("params", {})
            if "allowed_pipes" not in p:
                p["allowed_pipes"] = copy.deepcopy(self.allowed_pipes)
            pipe_allow_ref = p["allowed_pipes"]
            dlg_title = f"Дозволені труби для блоку {bi + 1} — {self.var_proj_name.get()}"
            scope_body = (
                "Набір зберігається в JSON у field_blocks → params → allowed_pipes для цього блоку. "
                "Гідравліка для сабмейнів блоку використовує саме його (інакше — глобальний проєкт)."
            )

        dlg = tk.Toplevel(self.root)
        dlg.title(dlg_title)
        dlg.geometry("640x620")
        dlg.minsize(480, 420)
        dlg.configure(bg="#1e1e1e")
        try:
            dlg.transient(self.root)
        except tk.TclError:
            pass
        ttk.Sizegrip(dlg).place(relx=1.0, rely=1.0, anchor=tk.SE)

        def _done_selector():
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            self.sync_hydro_pipe_summary()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _done_selector)

        def _raise_pipe_dialog():
            try:
                dlg.lift()
                dlg.focus_force()
                dlg.grab_set()
            except tk.TclError:
                pass

        dlg.after_idle(_raise_pipe_dialog)

        sel_top = tk.Frame(dlg, bg="#1e1e1e")
        sel_top.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(
            sel_top,
            text="Розрахунок сабмейну використовує лише рядки з ✅: усі відмічені матеріали, PN і Ø "
            "(перетин із каталогом pipes_db). Підбір d — найменший дозволений внутрішній d при заданому Vmax.",
            bg="#1e1e1e",
            fg="#AAAAAA",
            font=("Arial", 8),
            wraplength=600,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        self.sync_hydro_pipe_summary()

        tk.Label(
            dlg,
            text="ЛКМ по заголовку стовпця (крім «Вик.») — меню фільтра. "
            "Одне ЛКМ у клітинці «Вик.» рядка — перемкнути ✅/❌. "
            "Подвійне ЛКМ по заголовку «Вик.» — інвертувати всі рядки або лише виділені (Ctrl/Shift+ЛКМ).",
            bg="#1e1e1e",
            fg="#AAAAAA",
            font=("Arial", 8),
            wraplength=600,
            justify=tk.LEFT,
        ).pack(padx=10, pady=(4, 4))
        tk.Label(
            dlg,
            text=scope_body,
            bg="#1e1e1e",
            fg="white",
            wraplength=600,
            justify=tk.LEFT,
        ).pack(padx=10, pady=(0, 8))
        var_only_driplines = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dlg,
            text="Лише категорія «Крапельні лінії»",
            variable=var_only_driplines,
            bg="#1e1e1e",
            fg="#88DDFF",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="#88DDFF",
            command=lambda: refresh_selector(),
        ).pack(pady=(0, 6))

        style = ttk.Style(dlg)
        style.theme_use("clam")
        style.configure("Treeview", background="#333", foreground="white", fieldbackground="#333", rowheight=30)
        style.configure("Treeview.Heading", background="#222", foreground="#00FFCC")

        tree_frame = tk.Frame(dlg, bg="#1e1e1e")
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("use", "mat", "pn", "od", "len")
        tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            height=14,
            selectmode="extended",
        )

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        tree.heading("use", text="Вик.")
        tree.heading("mat", text="Матеріал")
        tree.heading("pn", text="PN")
        tree.heading("od", text="Діаметр Ø")
        tree.heading("len", text="Довжина(м)")

        tree.column("use", width=46, anchor="center")
        tree.column("mat", width=118, anchor="center")
        tree.column("pn", width=58, anchor="center")
        tree.column("od", width=96, anchor="center")
        tree.column("len", width=96, anchor="center")

        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        all_items = []
        filters = {"mat": None, "pn": None, "od": None, "len": None}
        col_to_key = {"#2": "mat", "#3": "pn", "#4": "od", "#5": "len"}

        def rebuild_catalog():
            all_items.clear()
            for mat, pns in self.pipe_db.items():
                for pn, ods in pns.items():
                    for od, data in ods.items():
                        l_val = data.get("length", "") if isinstance(data, dict) else ""
                        all_items.append(
                            {"mat": mat, "pn": str(pn), "od": str(od), "len": str(l_val)}
                        )

        def row_passes_filters(it):
            for k, fv in filters.items():
                if fv is None:
                    continue
                if str(it[k]) != str(fv):
                    return False
            return True

        def refresh_selector():
            for item in tree.get_children():
                tree.delete(item)
            for it in all_items:
                if var_only_driplines.get() and str(it.get("mat", "")).strip() != "Крапельні лінії":
                    continue
                if not row_passes_filters(it):
                    continue
                is_allowed = it["od"] in pipe_allow_ref.get(it["mat"], {}).get(it["pn"], [])
                tree.insert(
                    "",
                    tk.END,
                    values=("✅" if is_allowed else "❌", it["mat"], it["pn"], it["od"], it["len"]),
                )

        def invert_for_items(items_list):
            for it in items_list:
                mat, pn, od = it["mat"], it["pn"], it["od"]
                if mat not in pipe_allow_ref:
                    pipe_allow_ref[mat] = {}
                if pn not in pipe_allow_ref[mat]:
                    pipe_allow_ref[mat][pn] = []
                cur = pipe_allow_ref[mat][pn]
                if od in cur:
                    cur.remove(od)
                else:
                    cur.append(od)
            refresh_selector()
            self.redraw()
            self.sync_hydro_pipe_summary()
            try:
                dlg.after(0, _raise_pipe_dialog)
            except tk.TclError:
                pass

        def on_heading_double_vyk(event):
            if str(tree.identify_region(event.x, event.y)) != "heading":
                return
            if tree.identify_column(event.x) != "#1":
                return
            sel = tree.selection()
            if sel:
                rows = []
                for iid in sel:
                    v = tree.item(iid, "values")
                    rows.append({"mat": v[1], "pn": str(v[2]), "od": str(v[3])})
                invert_for_items(rows)
            else:
                invert_for_items(list(all_items))

        def on_heading_release_filter(event):
            if str(tree.identify_region(event.x, event.y)) != "heading":
                return
            col = tree.identify_column(event.x)
            if col == "#1":
                return
            key = col_to_key.get(col)
            if not key:
                return
            uniq = sorted({str(it[key]) for it in all_items}, key=lambda x: (len(x), x))

            def clear_filter():
                filters[key] = None
                refresh_selector()

            menu = tk.Menu(dlg, tearoff=0)
            menu.add_command(label="Усі (скинути фільтр)", command=clear_filter)
            for u in uniq[:160]:

                def pick_val(k=key, val=str(u)):
                    filters[k] = val
                    refresh_selector()

                menu.add_command(label=str(u), command=pick_val)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except tk.TclError:
                    pass

        def toggle_use_cell_at(event):
            if str(tree.identify_region(event.x, event.y)) != "cell":
                return
            if tree.identify_column(event.x) != "#1":
                return
            item_id = tree.identify_row(event.y)
            if not item_id:
                return
            vals = tree.item(item_id, "values")
            if len(vals) < 4:
                return
            mat, pn, od = vals[1], vals[2], vals[3]
            invert_for_items([{"mat": mat, "pn": str(pn), "od": str(od)}])

        def on_double_left(event):
            r = str(tree.identify_region(event.x, event.y))
            col = tree.identify_column(event.x)
            if r == "heading" and col == "#1":
                on_heading_double_vyk(event)

        def on_tree_button_release(event):
            region = str(tree.identify_region(event.x, event.y))
            col = tree.identify_column(event.x)
            if region == "cell" and col == "#1":
                toggle_use_cell_at(event)
                return
            on_heading_release_filter(event)

        rebuild_catalog()
        refresh_selector()

        tree.bind("<ButtonRelease-1>", on_tree_button_release)
        tree.bind("<Double-1>", on_double_left)

        btn_frame = tk.Frame(dlg, bg="#1e1e1e")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        _b_done_sel = tk.Button(btn_frame, text="Готово", command=_done_selector, bg="#0066FF", fg="white", width=20)
        _b_done_sel.pack()
        attach_tooltip(_b_done_sel, "Зберегти вибір дозволених труб і закрити діалог.")

    def open_pipe_editor(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Глобальна база труб (pipes_db.json)")
        dlg.geometry("800x600")
        dlg.minsize(640, 480)
        dlg.configure(bg="#1e1e1e")
        ttk.Sizegrip(dlg).place(relx=1.0, rely=1.0, anchor=tk.SE)
        
        style = ttk.Style(dlg)
        style.theme_use("clam")
        style.configure("Treeview", background="#333", foreground="white", fieldbackground="#333", rowheight=35)
        style.configure("Treeview.Heading", background="#222", foreground="#00FFCC")

        btn_frame = tk.Frame(dlg, bg="#1e1e1e")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        
        form_frame = tk.Frame(dlg, bg="#1e1e1e")
        form_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)
        
        filter_frame = tk.Frame(dlg, bg="#1e1e1e")
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(6, 0))
        var_pipe_mask = tk.StringVar(value="")
        tk.Label(filter_frame, text="Маска (усі поля):", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=(0, 6))
        ent_pipe_mask = tk.Entry(
            filter_frame,
            textvariable=var_pipe_mask,
            width=18,
            bg="#222",
            fg="white",
            insertbackground="white",
            insertwidth=2,
            font=("Consolas", 9, "bold"),
        )
        ent_pipe_mask.pack(side=tk.LEFT, padx=(0, 8))
        _b_clr_pipe_mask = tk.Button(
            filter_frame,
            text="Очистити маску",
            command=lambda: (var_pipe_mask.set(""), refresh_tree()),
            bg="#2c2c2c",
            fg="white",
        )
        _b_clr_pipe_mask.pack(side=tk.LEFT)
        attach_tooltip(_b_clr_pipe_mask, "Скинути текстову маску фільтрації рядків у таблиці труб.")

        tree_frame = tk.Frame(dlg, bg="#1e1e1e")
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("mat", "pn", "od", "id", "len", "price", "color")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        
        for c, t in zip(columns, ["Матеріал", "PN", "Зовн. Ø", "Внутр. Ø", "Довжина(м)", "Ціна", "Колір"]): tree.heading(c, text=t)
        for c, w in zip(columns, [100, 60, 80, 80, 100, 90, 80]): tree.column(c, width=w, anchor="center")
        
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def refresh_tree():
            for item in tree.get_children():
                tree.delete(item)
            m = (var_pipe_mask.get() or "").strip()
            if m and not any(ch in m for ch in ("*", "?", "[")):
                m = f"*{m}*"
            for mat, pns in self.pipe_db.items():
                for pn, ods in pns.items():
                    for od, data in ods.items():
                        id_v, len_v, price_v, col_v = (
                            data.get("id", ""),
                            data.get("length", ""),
                            data.get("price", 0.0),
                            data.get("color", "#FFFFFF"),
                        ) if isinstance(data, dict) else (data, 6.0, 0.0, "#FFFFFF")
                        vals = (mat, pn, od, id_v, len_v, price_v, col_v)
                        if m:
                            blob = " | ".join(str(x) for x in vals).lower()
                            if not fnmatch.fnmatch(blob, m.lower()):
                                continue
                        tree.insert("", tk.END, values=vals)

        ent_pipe_mask.bind("<KeyRelease>", lambda _e: refresh_tree())
        refresh_tree()

        vars_dict = {c: tk.StringVar() for c in columns}
        vars_dict['color'].set("#FFFFFF")
        
        for i, (c, t) in enumerate(zip(columns, ["Матеріал:", "PN:", "Зовн. Ø:", "Внутр. Ø:", "Довжина:", "Ціна:", "Колір:"])):
            tk.Label(
                form_frame,
                text=t,
                bg="#1e1e1e",
                fg="gray" if c not in ["len", "price", "color"] else ("#FFD700" if c == "len" else ("#88CC88" if c == "price" else "cyan")),
            ).grid(row=0, column=i, sticky=tk.W)
            if c == 'color':
                cf = tk.Frame(form_frame, bg="#1e1e1e")
                cf.grid(row=1, column=i, padx=2, sticky=tk.W)
                tk.Entry(
                    cf,
                    textvariable=vars_dict[c],
                    width=9,
                    bg="#222",
                    fg="white",
                    insertbackground="white",
                    insertwidth=2,
                ).pack(
                    side=tk.LEFT
                )
                sw_lbl = tk.Label(
                    cf,
                    text="   ",
                    width=3,
                    bg="#555555",
                    relief=tk.SUNKEN,
                    borderwidth=1,
                )
                sw_lbl.pack(side=tk.LEFT, padx=3)

                def sync_pipe_editor_color_swatch(*_args):
                    raw = (vars_dict["color"].get() or "").strip()
                    trial = raw if (len(raw) >= 4 and raw.startswith("#")) else "#555555"
                    try:
                        sw_lbl.config(bg=trial)
                    except tk.TclError:
                        sw_lbl.config(bg="#555555")

                def pick_row_color():
                    cur = vars_dict["color"].get() or "#FFFFFF"
                    res = colorchooser.askcolor(color=cur, parent=dlg, title="Колір труби")
                    if res and res[1]:
                        vars_dict["color"].set(res[1])

                _b_pick_col = tk.Button(
                    cf,
                    text="🎨",
                    command=pick_row_color,
                    width=2,
                    bg="#333333",
                    fg="white",
                )
                _b_pick_col.pack(side=tk.LEFT, padx=2)
                attach_tooltip(_b_pick_col, "Відкрити вибір кольору для відображення труби на схемі.")
                vars_dict["color"].trace_add("write", sync_pipe_editor_color_swatch)
            else:
                tk.Entry(
                    form_frame,
                    textvariable=vars_dict[c],
                    width=14 if c == "mat" else (9 if c in ("len", "price") else 8),
                    bg="#222",
                    fg=("#FFD700" if c == "len" else ("#88CC88" if c == "price" else "white")),
                    insertbackground="white",
                    insertwidth=2,
                    font=("Consolas", 10, "bold") if c in ("len", "price") else None,
                ).grid(row=1, column=i, padx=2, sticky=tk.W)

        def on_select(e):
            sel = tree.selection()
            if not sel:
                return
            for c, val in zip(columns, tree.item(sel[0])["values"]):
                vars_dict[c].set(str(val))
            sync_pipe_editor_color_swatch()

        tree.bind("<<TreeviewSelect>>", on_select)
        sync_pipe_editor_color_swatch()

        def add_upd():
            mat, pn, od = vars_dict['mat'].get().strip(), str(vars_dict['pn'].get()).strip(), str(vars_dict['od'].get()).strip()
            try:
                id_v = float(vars_dict["id"].get().replace(",", "."))
                len_v = float(vars_dict["len"].get().replace(",", "."))
                price_v = float((vars_dict["price"].get() or "0").replace(",", "."))
            except:
                return messagebox.showerror("Помилка", "Діаметр, довжина і ціна повинні бути числами!")
            if not mat or not pn or not od: return
            col = (vars_dict["color"].get() or "").strip() or "#FFFFFF"
            if mat not in self.pipe_db: self.pipe_db[mat] = {}
            if pn not in self.pipe_db[mat]: self.pipe_db[mat][pn] = {}
            self.pipe_db[mat][pn][od] = {"id": id_v, "length": len_v, "price": max(0.0, price_v), "color": col}
            if mat not in self.allowed_pipes: self.allowed_pipes[mat] = {}
            if pn not in self.allowed_pipes[mat]: self.allowed_pipes[mat][pn] = []
            if od not in self.allowed_pipes[mat][pn]: self.allowed_pipes[mat][pn].append(od)
            refresh_tree()

        def del_item():
            mat, pn, od = vars_dict['mat'].get().strip(), str(vars_dict['pn'].get()).strip(), str(vars_dict['od'].get()).strip()
            if mat in self.pipe_db and pn in self.pipe_db[mat] and od in self.pipe_db[mat][pn]:
                del self.pipe_db[mat][pn][od]
                if not self.pipe_db[mat][pn]: del self.pipe_db[mat][pn]
                if not self.pipe_db[mat]: del self.pipe_db[mat]
            refresh_tree()

        def save_db():
            try:
                mat = vars_dict["mat"].get().strip()
                pn = str(vars_dict["pn"].get()).strip()
                od = str(vars_dict["od"].get()).strip()
                if mat and pn and od:
                    try:
                        id_v = float(vars_dict["id"].get().replace(",", "."))
                        len_v = float(vars_dict["len"].get().replace(",", "."))
                        price_v = float((vars_dict["price"].get() or "0").replace(",", "."))
                    except ValueError:
                        pass
                    else:
                        col = (vars_dict["color"].get() or "").strip() or "#FFFFFF"
                        if mat not in self.pipe_db:
                            self.pipe_db[mat] = {}
                        if pn not in self.pipe_db[mat]:
                            self.pipe_db[mat][pn] = {}
                        self.pipe_db[mat][pn][od] = {
                            "id": id_v,
                            "length": len_v,
                            "price": max(0.0, price_v),
                            "color": col,
                        }
                        if mat not in self.allowed_pipes:
                            self.allowed_pipes[mat] = {}
                        if pn not in self.allowed_pipes[mat]:
                            self.allowed_pipes[mat][pn] = []
                        if od not in self.allowed_pipes[mat][pn]:
                            self.allowed_pipes[mat][pn].append(od)

                with open(PIPES_DB_PATH, "w", encoding="utf-8") as f: json.dump(self.pipe_db, f, indent=4)
                
                proj_dir = file_io.ensure_project_dir(self)
                proj_db_path = os.path.join(proj_dir, "pipes_db.json")
                with open(proj_db_path, "w", encoding="utf-8") as f: json.dump(self.pipe_db, f, indent=4)
                
                avail = list(self.pipe_db.keys())
                if hasattr(self, 'cb_mat'): self.cb_mat.config(values=avail)
                if self.pipe_material.get() not in avail and avail: self.pipe_material.set(avail[0])
                self.update_pn_dropdown(skip_reset=True)
                self.sync_hydro_pipe_summary()
                dlg.destroy()
                messagebox.showinfo("Збережено", "Базу успішно оновлено!")
                self.redraw()
            except Exception as e: messagebox.showerror("Помилка", f"Не вдалося зберегти: {e}")

        _b_au = tk.Button(btn_frame, text="Додати / Оновити", command=add_upd, bg="#0066FF", fg="white")
        _b_au.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_b_au, "Додати новий запис труби або оновити вибраний за полями форми.")
        _b_delp = tk.Button(btn_frame, text="Видалити", command=del_item, bg="#662222", fg="white")
        _b_delp.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_b_delp, "Видалити вибраний у таблиці рядок з пам'яті (ще не в файл).")
        _b_svp = tk.Button(btn_frame, text="💾 Зберегти в файл", command=save_db, bg="#2e4d46", fg="white")
        _b_svp.pack(side=tk.RIGHT, padx=5)
        attach_tooltip(_b_svp, "Записати глобальну базу труб pipes_db.json на диск.")
        dlg.bind("<Control-s>", lambda _e: save_db())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: add_upd())

    def open_drippers_editor(self):
        # Always reload from disk to avoid stale in-memory cache.
        self._load_drippers_db()
        dlg = tk.Toplevel(self.root)
        dlg.title("База крапельниць (drippers_db.json)")
        dlg.geometry("1050x620")
        dlg.configure(bg="#1e1e1e")
        dlg.transient(self.root)

        style = ttk.Style(dlg)
        style.theme_use("clam")
        style.configure("Treeview", background="#333", foreground="white", fieldbackground="#333", rowheight=30)
        style.configure("Treeview.Heading", background="#222", foreground="#00FFCC")

        btn_frame = tk.Frame(dlg, bg="#1e1e1e")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        form_frame = tk.Frame(dlg, bg="#1e1e1e")
        form_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)

        filter_frame = tk.Frame(dlg, bg="#1e1e1e")
        filter_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(6, 0))

        tree_frame = tk.Frame(dlg, bg="#1e1e1e")
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("model", "manufacturer", "qnom", "k", "x", "kd", "passages", "area", "filter", "cit")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=12)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        headers = [
            "Модель",
            "Виробник",
            "Q ном, л/год",
            "k",
            "x",
            "kd",
            "Канали, мм",
            "Площа фільтр., мм²",
            "Реком. фільтрація",
            "Джерело",
        ]
        widths = [120, 100, 90, 80, 70, 60, 160, 120, 190, 90]
        for c, t, w in zip(columns, headers, widths):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="center")

        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _db_to_rows():
            rows = []
            src_models = self.drippers_db
            if isinstance(src_models, dict):
                src_models = src_models.get("models", [])
            if not isinstance(src_models, list):
                src_models = []
            for model in src_models:
                if not isinstance(model, dict):
                    continue
                mname = str(model.get("model_name", "") or model.get("series", "")).strip()
                mfr_default = str(model.get("manufacturer", "")).strip() or "Netafim"
                cite = str(model.get("_citations", "")).strip()
                for it in model.get("drippers_technical_data", []) or []:
                    rows.append(
                        {
                            "model": mname,
                            "manufacturer": str(it.get("manufacturer", mfr_default)).strip() or "Netafim",
                            "qnom": str(it.get("nominal_flow_l_h", "")),
                            "k": str(it.get("constant_k", "")),
                            "x": str(it.get("exponent_x", "")),
                            "kd": str(it.get("kd", 1.0)),
                            "passages": str(it.get("water_passages_dimensions_mm", "")),
                            "area": str(it.get("filtration_area_mm2", "")),
                            "filter": str(it.get("recommended_filtration", "")),
                            "cit": cite,
                        }
                    )
            rows.sort(
                key=lambda r: (
                    r["model"],
                    float(r["qnom"]) if r["qnom"].replace(".", "", 1).isdigit() else 0.0,
                )
            )
            return rows

        rows = _db_to_rows()
        filters = {"model": None, "qnom": None, "manufacturer": None}
        var_filter_x0 = tk.BooleanVar(value=False)
        var_mask = tk.StringVar(value="")

        tk.Label(filter_frame, text="Фільтр модель:", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=(0, 6))
        cb_filter_model = ttk.Combobox(filter_frame, state="readonly", width=16)
        cb_filter_model.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(filter_frame, text="Фільтр Q ном:", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=(0, 6))
        cb_filter_q = ttk.Combobox(filter_frame, state="readonly", width=10)
        cb_filter_q.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(filter_frame, text="Фільтр виробник:", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=(0, 6))
        cb_filter_mfr = ttk.Combobox(filter_frame, state="readonly", width=12)
        cb_filter_mfr.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(filter_frame, text="Маска (усі поля):", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=(0, 6))
        ent_mask = tk.Entry(
            filter_frame,
            textvariable=var_mask,
            width=14,
            bg="#222",
            fg="white",
            insertbackground="white",
            insertwidth=2,
            font=("Consolas", 9, "bold"),
        )
        ent_mask.pack(side=tk.LEFT, padx=(0, 10))
        btn_clear_filters = tk.Button(
            filter_frame,
            text="Скинути фільтри",
            command=lambda: None,
            bg="#2c2c2c",
            fg="white",
        )
        btn_clear_filters.pack(side=tk.LEFT)
        attach_tooltip(
            btn_clear_filters,
            "Скинути всі фільтри списку крапельниць (модель, Q, виробник, маска, x=0).",
        )
        tk.Checkbutton(
            filter_frame,
            text="x = 0",
            variable=var_filter_x0,
            bg="#1e1e1e",
            fg="#88DDFF",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="#88DDFF",
            command=lambda: refresh_tree(),
        ).pack(side=tk.LEFT, padx=(12, 0))

        def _passes_filters(r):
            if filters["model"] is not None and str(r["model"]) != str(filters["model"]):
                return False
            if filters["qnom"] is not None and str(r["qnom"]) != str(filters["qnom"]):
                return False
            if filters["manufacturer"] is not None and str(r["manufacturer"]) != str(filters["manufacturer"]):
                return False
            if var_filter_x0.get():
                try:
                    if abs(float(str(r.get("x", "")).replace(",", "."))) > 1e-12:
                        return False
                except (TypeError, ValueError):
                    return False
            m = (var_mask.get() or "").strip()
            if m:
                if not any(ch in m for ch in ("*", "?", "[")):
                    m = f"*{m}*"
                row_blob = " | ".join(
                    [
                        str(r.get("model", "")),
                        str(r.get("manufacturer", "")),
                        str(r.get("qnom", "")),
                        str(r.get("k", "")),
                        str(r.get("x", "")),
                        str(r.get("kd", "")),
                        str(r.get("passages", "")),
                        str(r.get("area", "")),
                        str(r.get("filter", "")),
                        str(r.get("cit", "")),
                    ]
                ).lower()
                if not fnmatch.fnmatch(row_blob, m.lower()):
                    return False
            return True

        def refresh_filter_values():
            models = sorted({str(r["model"]) for r in rows if str(r["model"]).strip()})
            qnoms = sorted(
                {str(r["qnom"]) for r in rows if str(r["qnom"]).strip()},
                key=lambda x: float(x) if x.replace(".", "", 1).isdigit() else 0.0,
            )
            mfrs = sorted({str(r["manufacturer"]) for r in rows if str(r["manufacturer"]).strip()})
            cb_filter_model["values"] = ["Усі"] + models
            cb_filter_q["values"] = ["Усі"] + qnoms
            cb_filter_mfr["values"] = ["Усі"] + mfrs
            if filters["model"] is None:
                cb_filter_model.set("Усі")
            elif filters["model"] not in models:
                filters["model"] = None
                cb_filter_model.set("Усі")
            else:
                cb_filter_model.set(str(filters["model"]))
            if filters["qnom"] is None:
                cb_filter_q.set("Усі")
            elif filters["qnom"] not in qnoms:
                filters["qnom"] = None
                cb_filter_q.set("Усі")
            else:
                cb_filter_q.set(str(filters["qnom"]))
            if filters["manufacturer"] is None:
                cb_filter_mfr.set("Усі")
            elif filters["manufacturer"] not in mfrs:
                filters["manufacturer"] = None
                cb_filter_mfr.set("Усі")
            else:
                cb_filter_mfr.set(str(filters["manufacturer"]))

        def refresh_tree():
            for iid in tree.get_children():
                tree.delete(iid)
            for r in rows:
                if not _passes_filters(r):
                    continue
                tree.insert(
                    "",
                    tk.END,
                    values=(
                        r["model"],
                        r["manufacturer"],
                        r["qnom"],
                        r["k"],
                        r["x"],
                        r["kd"],
                        r["passages"],
                        r["area"],
                        r["filter"],
                        r["cit"],
                    ),
                )

        def on_filter_model(_e=None):
            v = (cb_filter_model.get() or "").strip()
            filters["model"] = None if (not v or v == "Усі") else v
            refresh_tree()

        def on_filter_q(_e=None):
            v = (cb_filter_q.get() or "").strip()
            filters["qnom"] = None if (not v or v == "Усі") else v
            refresh_tree()

        def on_filter_mfr(_e=None):
            v = (cb_filter_mfr.get() or "").strip()
            filters["manufacturer"] = None if (not v or v == "Усі") else v
            refresh_tree()

        def clear_filters():
            filters["model"] = None
            filters["qnom"] = None
            filters["manufacturer"] = None
            var_filter_x0.set(False)
            var_mask.set("")
            cb_filter_model.set("Усі")
            cb_filter_q.set("Усі")
            cb_filter_mfr.set("Усі")
            refresh_tree()

        cb_filter_model.bind("<<ComboboxSelected>>", on_filter_model)
        cb_filter_q.bind("<<ComboboxSelected>>", on_filter_q)
        cb_filter_mfr.bind("<<ComboboxSelected>>", on_filter_mfr)
        ent_mask.bind("<KeyRelease>", lambda _e: refresh_tree())
        btn_clear_filters.configure(command=clear_filters)

        refresh_filter_values()
        refresh_tree()

        status_var = tk.StringVar(value="")
        tk.Label(
            form_frame,
            textvariable=status_var,
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8),
            anchor=tk.W,
            justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=len(columns), sticky=tk.W, pady=(4, 0))

        vars_dict = {c: tk.StringVar() for c in columns}
        selected_row_sig = {"value": None}
        labels = ["Модель:", "Виробник:", "Q ном:", "k:", "x:", "kd:", "Канали, мм:", "Площа, мм²:", "Фільтрація:", "Джерело:"]
        for i, (c, t) in enumerate(zip(columns, labels)):
            tk.Label(
                form_frame,
                text=t,
                bg="#1e1e1e",
                fg="#00FFCC" if c in ("qnom", "k", "x", "kd") else "gray",
            ).grid(row=0, column=i, sticky=tk.W)
            tk.Entry(
                form_frame,
                textvariable=vars_dict[c],
                width=10 if c in ("model", "manufacturer", "qnom", "k", "x", "kd") else (18 if c in ("passages", "filter") else 10),
                bg="#222",
                fg="#00FFCC" if c in ("qnom", "k", "x", "kd") else "white",
                insertbackground="#FFFFFF",
                insertwidth=2,
                font=("Consolas", 10, "bold") if c in ("qnom", "k", "x", "kd") else None,
            ).grid(row=1, column=i, padx=2, sticky=tk.W)

        def _row_signature(r):
            return tuple(str(r.get(c, "")) for c in columns)

        def _find_row_idx_by_signature(sig):
            if not sig:
                return None
            for i, r in enumerate(rows):
                if _row_signature(r) == sig:
                    return i
            return None

        def _persist_drippers_db(*, close_dialog=False, show_popup=False):
            rebuild_db_from_rows()
            payload = {
                "schema_version": 2,
                "description": "База технічних даних крапельниць за моделями",
                "models": self.drippers_db,
            }
            with open(DRIPPERS_DB_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            if hasattr(self, "cb_emit_model"):
                self.cb_emit_model.config(values=self._dripper_model_names())
            self._on_emit_model_change()
            if close_dialog:
                dlg.destroy()
            if show_popup:
                messagebox.showinfo("Збережено", f"Базу крапельниць оновлено:\n{DRIPPERS_DB_PATH}")

        def on_select(_e):
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], "values")
            for c, v in zip(columns, vals):
                vars_dict[c].set(str(v))
            selected_row_sig["value"] = tuple(str(v) for v in vals)

        tree.bind("<<TreeviewSelect>>", on_select)

        def rebuild_db_from_rows():
            grouped = {}
            cites = {}
            for r in rows:
                m = r["model"].strip()
                if not m:
                    continue
                grouped.setdefault(m, [])
                cites[m] = r["cit"].strip()
                grouped[m].append(
                    {
                        "manufacturer": (r.get("manufacturer") or "Netafim").strip() or "Netafim",
                        "nominal_flow_l_h": float(r["qnom"]),
                        "constant_k": float(r["k"]),
                        "exponent_x": float(r["x"]),
                        "kd": float((r.get("kd") or "1").replace(",", ".")),
                        "water_passages_dimensions_mm": r["passages"].strip(),
                        "filtration_area_mm2": int(float(r["area"])) if r["area"].strip() else 0,
                        "recommended_filtration": r["filter"].strip(),
                    }
                )
            out_models = []
            for m in sorted(grouped.keys()):
                dr = sorted(grouped[m], key=lambda x: float(x.get("nominal_flow_l_h", 0)))
                mfr_series = "Netafim"
                if dr:
                    mfr_series = str(dr[0].get("manufacturer", "Netafim")).strip() or "Netafim"
                out_models.append(
                    {
                        "series": m,
                        "manufacturer": mfr_series,
                        "drippers_technical_data": dr,
                        "_citations": cites.get(m, ""),
                    }
                )
            self.drippers_db = out_models

        def _read_form_record():
            try:
                rec = {
                    "model": vars_dict["model"].get().strip(),
                    "manufacturer": vars_dict["manufacturer"].get().strip() or "Netafim",
                    "qnom": str(float(vars_dict["qnom"].get().replace(",", "."))),
                    "k": str(float(vars_dict["k"].get().replace(",", "."))),
                    "x": str(float(vars_dict["x"].get().replace(",", "."))),
                    "kd": str(float((vars_dict["kd"].get().strip() or "1").replace(",", "."))),
                    "passages": vars_dict["passages"].get().strip(),
                    "area": str(int(float((vars_dict["area"].get() or "0").replace(",", ".")))),
                    "filter": vars_dict["filter"].get().strip(),
                    "cit": vars_dict["cit"].get().strip(),
                }
            except Exception:
                messagebox.showerror("Помилка", "Поля Q, k, x, kd, площа мають бути числами.")
                return None
            if not rec["model"]:
                messagebox.showerror("Помилка", "Вкажіть модель.")
                return None
            return rec

        def add_item():
            rec = _read_form_record()
            if not rec:
                return
            rows.append(rec)
            refresh_filter_values()
            refresh_tree()
            selected_row_sig["value"] = _row_signature(rec)
            _persist_drippers_db(close_dialog=False, show_popup=False)
            status_var.set(f"Додано: {rec['model']} @ {rec['qnom']} л/год.")

        def update_item():
            rec = _read_form_record()
            if not rec:
                return
            replaced = False
            idx_sel = _find_row_idx_by_signature(selected_row_sig["value"])
            if idx_sel is not None:
                rows[idx_sel] = rec
                replaced = True
            else:
                for i, r in enumerate(rows):
                    try:
                        same = (
                            r["model"] == rec["model"]
                            and r.get("manufacturer", "Netafim") == rec["manufacturer"]
                            and abs(float(r["qnom"]) - float(rec["qnom"])) < 1e-9
                        )
                    except Exception:
                        same = False
                    if same:
                        rows[i] = rec
                        replaced = True
                        break
            if not replaced:
                messagebox.showwarning("Увага", "Оберіть рядок у таблиці для оновлення.")
                return
            refresh_filter_values()
            refresh_tree()
            selected_row_sig["value"] = _row_signature(rec)
            _persist_drippers_db(close_dialog=False, show_popup=False)
            status_var.set(f"Збережено: {rec['model']} @ {rec['qnom']} л/год.")

        def delete_selected():
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], "values")
            if not vals or len(vals) < 2:
                return
            sig = tuple(str(v) for v in vals)
            idx_sel = _find_row_idx_by_signature(sig)
            if idx_sel is not None:
                del rows[idx_sel]
                selected_row_sig["value"] = None
                refresh_filter_values()
                refresh_tree()
                _persist_drippers_db(close_dialog=False, show_popup=False)
                status_var.set("Запис видалено і базу збережено.")
                return
            m = str(vals[0])
            q = float(str(vals[2]).replace(",", "."))
            kept = []
            for r in rows:
                try:
                    same = r["model"] == m and abs(float(r["qnom"]) - q) < 1e-9
                except Exception:
                    same = False
                if not same:
                    kept.append(r)
            rows[:] = kept
            refresh_filter_values()
            refresh_tree()
            _persist_drippers_db(close_dialog=False, show_popup=False)
            status_var.set("Запис видалено і базу збережено.")

        def save_db():
            try:
                _persist_drippers_db(close_dialog=False, show_popup=False)
                status_var.set("Базу записано у файл.")
                messagebox.showinfo("Збережено", f"Базу крапельниць оновлено:\n{DRIPPERS_DB_PATH}")
            except Exception as e:
                messagebox.showerror("Помилка", f"Не вдалося зберегти базу крапельниць:\n{e}")

        _bd = tk.Button(btn_frame, text="Додати", command=add_item, bg="#0066FF", fg="white")
        _bd.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_bd, "Додати новий рядок крапельниці з полів форми.")
        _bu = tk.Button(btn_frame, text="Оновити", command=update_item, bg="#2e4d46", fg="white")
        _bu.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_bu, "Оновити вибраний рядок значеннями з форми.")
        _bx = tk.Button(btn_frame, text="Видалити", command=delete_selected, bg="#662222", fg="white")
        _bx.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_bx, "Видалити вибраний рядок і зберегти базу у файл.")
        _bs = tk.Button(btn_frame, text="💾 Зберегти в файл", command=save_db, bg="#2e4d46", fg="white")
        _bs.pack(side=tk.RIGHT, padx=5)
        attach_tooltip(_bs, "Явно зберегти базу крапельниць drippers_db.json.")
        dlg.bind("<Control-s>", lambda _e: save_db())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: update_item())

    def open_laterals_editor(self):
        self._load_laterals_db()
        dlg = tk.Toplevel(self.root)
        dlg.title("База латералей (laterals_db.json)")
        dlg.geometry("1100x620")
        dlg.configure(bg="#1e1e1e")

        style = ttk.Style(dlg)
        style.theme_use("clam")
        style.configure("Treeview", background="#333", foreground="white", fieldbackground="#333", rowheight=28)
        style.configure("Treeview.Heading", background="#222", foreground="#00FFCC")

        btn_frame = tk.Frame(dlg, bg="#1e1e1e")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        form_frame = tk.Frame(dlg, bg="#1e1e1e")
        form_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)
        filter_lat_frame = tk.Frame(dlg, bg="#1e1e1e")
        filter_lat_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(6, 0))
        var_lat_mask = tk.StringVar(value="")
        tk.Label(filter_lat_frame, text="Маска (усі поля):", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=(0, 6))
        ent_lat_mask = tk.Entry(
            filter_lat_frame,
            textvariable=var_lat_mask,
            width=22,
            bg="#222",
            fg="white",
            insertbackground="#FFFFFF",
            insertwidth=2,
            font=("Consolas", 9, "bold"),
        )
        ent_lat_mask.pack(side=tk.LEFT, padx=(0, 8))
        _b_lat_mask = tk.Button(
            filter_lat_frame,
            text="Очистити маску",
            command=lambda: (var_lat_mask.set(""), _rebuild_tree()),
            bg="#2c2c2c",
            fg="white",
        )
        _b_lat_mask.pack(side=tk.LEFT)
        attach_tooltip(_b_lat_mask, "Скинути маску фільтрації рядків у таблиці латералів.")

        tree_frame = tk.Frame(dlg, bg="#1e1e1e")
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("od", "model", "id", "wall", "work", "flush")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=14, selectmode="browse")
        for c, t, w in (
            ("od", "Зовн. Ø, мм", 90),
            ("model", "Модель", 360),
            ("id", "Внутр. Ø, мм", 90),
            ("wall", "Товщина, мм", 90),
            ("work", "Роб. тиск, бар", 130),
            ("flush", "Макс. промивка, бар", 140),
        ):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="center")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        rows = []
        for grp in self.laterals_db:
            if not isinstance(grp, dict):
                continue
            od = grp.get("outside_diameter_mm")
            for it in grp.get("technical_data", []) or []:
                if not isinstance(it, dict):
                    continue
                rows.append(
                    {
                        "od": str(od),
                        "model": str(it.get("model", "")),
                        "id": str(it.get("inside_diameter_mm", "")),
                        "wall": str(it.get("wall_thickness_mm", "")),
                        "work": str(it.get("working_pressure_bar", "")),
                        "flush": str(it.get("maximum_flushing_pressure_bar", "")),
                    }
                )

        selected = {"idx": None}

        def _passes_lat_mask(r):
            m = (var_lat_mask.get() or "").strip()
            if not m:
                return True
            if not any(ch in m for ch in ("*", "?", "[")):
                m = f"*{m}*"
            blob = " | ".join(
                [
                    str(r.get("od", "")),
                    str(r.get("model", "")),
                    str(r.get("id", "")),
                    str(r.get("wall", "")),
                    str(r.get("work", "")),
                    str(r.get("flush", "")),
                ]
            ).lower()
            return fnmatch.fnmatch(blob, m.lower())

        def _rebuild_tree():
            for iid in tree.get_children():
                tree.delete(iid)
            prev_sel = selected.get("idx")
            for j, r in enumerate(rows):
                if not _passes_lat_mask(r):
                    continue
                tree.insert("", tk.END, iid=str(j), values=(r["od"], r["model"], r["id"], r["wall"], r["work"], r["flush"]))
            if prev_sel is not None and 0 <= prev_sel < len(rows) and _passes_lat_mask(rows[prev_sel]):
                try:
                    tree.selection_set(str(prev_sel))
                    tree.see(str(prev_sel))
                except tk.TclError:
                    selected["idx"] = None
            else:
                selected["idx"] = None

        ent_lat_mask.bind("<KeyRelease>", lambda _e: _rebuild_tree())
        _rebuild_tree()
        vars_dict = {c: tk.StringVar() for c in columns}
        for i, (c, label) in enumerate(
            (("od", "Зовн. Ø"), ("model", "Модель"), ("id", "Внутр. Ø"), ("wall", "Товщина"), ("work", "Роб. тиск"), ("flush", "Макс. промивка"))
        ):
            tk.Label(form_frame, text=label + ":", bg="#1e1e1e", fg="gray").grid(row=0, column=i, sticky=tk.W)
            tk.Entry(
                form_frame,
                textvariable=vars_dict[c],
                width=36 if c == "model" else 10,
                bg="#222",
                fg="white",
                insertbackground="#FFFFFF",
                insertwidth=2,
            ).grid(row=1, column=i, padx=3, sticky=tk.W)

        def _on_select(_e):
            sel = tree.selection()
            if not sel:
                selected["idx"] = None
                return
            vals = tree.item(sel[0], "values")
            try:
                selected["idx"] = int(sel[0])
            except (TypeError, ValueError):
                selected["idx"] = None
                return
            for c, v in zip(columns, vals):
                vars_dict[c].set(str(v))

        tree.bind("<<TreeviewSelect>>", _on_select)

        def _save_file():
            groups = {}
            for r in rows:
                od_key = str(r["od"]).strip()
                if not od_key:
                    continue
                groups.setdefault(od_key, [])
                groups[od_key].append(
                    {
                        "model": str(r["model"]).strip(),
                        "inside_diameter_mm": float(str(r["id"]).replace(",", ".")),
                        "wall_thickness_mm": float(str(r["wall"]).replace(",", ".")),
                        "working_pressure_bar": str(r["work"]).strip(),
                        "maximum_flushing_pressure_bar": (
                            None if str(r["flush"]).strip().lower() in ("", "none", "null") else str(r["flush"]).strip()
                        ),
                    }
                )
            out = []
            for od in sorted(groups.keys(), key=lambda x: float(x)):
                out.append({"outside_diameter_mm": float(od), "technical_data": groups[od]})
            payload = {
                "schema_version": 1,
                "manufacturer": "Netafim",
                "items": out,
            }
            with open(LATERALS_DB_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            self.laterals_db = out
            cb_lat = getattr(self, "cb_lat_model", None)
            if cb_lat is not None:
                try:
                    cb_lat.config(values=self._lateral_model_names())
                except tk.TclError:
                    pass

        def _read_form():
            try:
                rec = {
                    "od": str(float(str(vars_dict["od"].get()).replace(",", "."))),
                    "model": vars_dict["model"].get().strip(),
                    "id": str(float(str(vars_dict["id"].get()).replace(",", "."))),
                    "wall": str(float(str(vars_dict["wall"].get()).replace(",", "."))),
                    "work": vars_dict["work"].get().strip(),
                    "flush": vars_dict["flush"].get().strip(),
                }
            except Exception:
                messagebox.showerror("Помилка", "Поля Ø/ID/товщина мають бути числами.")
                return None
            if not rec["model"]:
                messagebox.showerror("Помилка", "Вкажіть модель.")
                return None
            return rec

        def _add():
            rec = _read_form()
            if not rec:
                return
            rows.append(rec)
            _rebuild_tree()
            _save_file()

        def _update():
            rec = _read_form()
            if not rec:
                return
            idx = selected["idx"]
            if idx is None or idx < 0 or idx >= len(rows):
                messagebox.showwarning("Увага", "Оберіть рядок для оновлення.")
                return
            rows[idx] = rec
            _rebuild_tree()
            _save_file()

        def _delete():
            idx = selected["idx"]
            if idx is None or idx < 0 or idx >= len(rows):
                messagebox.showwarning("Увага", "Оберіть рядок для видалення.")
                return
            del rows[idx]
            selected["idx"] = None
            _rebuild_tree()
            _save_file()

        _bl_u = tk.Button(btn_frame, text="Оновити", command=_update, bg="#2e4d46", fg="white")
        _bl_u.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_bl_u, "Записати зміни у вибраний рядок і зберегти laterals_db.json.")
        _bl_a = tk.Button(btn_frame, text="Додати", command=_add, bg="#0066FF", fg="white")
        _bl_a.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_bl_a, "Додати новий рядок з форми та зберегти у файл.")
        _bl_d = tk.Button(btn_frame, text="Видалити", command=_delete, bg="#662222", fg="white")
        _bl_d.pack(side=tk.LEFT, padx=5)
        attach_tooltip(_bl_d, "Видалити вибраний рядок і зберегти у файл.")
        _bl_sf = tk.Button(btn_frame, text="💾 Зберегти в файл", command=_save_file, bg="#2e4d46", fg="white")
        _bl_sf.pack(side=tk.RIGHT, padx=5)
        attach_tooltip(_bl_sf, "Записати всю таблицю латералей у laterals_db.json.")
        dlg.bind("<Control-s>", lambda _e: _save_file())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: _update())

if __name__ == "__main__":
    root = tk.Tk()
    app = DripCAD(root)
    root.mainloop()
