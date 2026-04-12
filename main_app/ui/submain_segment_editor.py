# -*- coding: utf-8 -*-
"""Діалог плану сегментів сабмейну: кратність × довжина труби × кількість палок на секцію."""

import copy
import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from main_app.ui.silent_messagebox import silent_showerror, silent_showwarning
from main_app.ui.tooltips import attach_tooltip

# Ширина полів вводу в символах (узгоджено з рядком «Необхідна довжина…», компактно).
_ENTRY_NUM_W = 10


def _edge_lengths_m(coords):
    out = []
    for i in range(len(coords) - 1):
        x0, y0 = coords[i][:2]
        x1, y1 = coords[i + 1][:2]
        out.append(math.hypot(x1 - x0, y1 - y0))
    return out


# У pipes_db поле length часто = 100 м як «бухта/умовна відпуск», а не довжина однієї палки
# для підбору сегментів сабмейну — такі значення не можна підставляти як L₀.
_MAX_STICK_HINT_M = 30.0


def default_unit_m_from_app(app, block_bi: int) -> float:
    src = (
        app._allowed_pipes_for_block_index(block_bi)
        if hasattr(app, "_allowed_pipes_for_block_index")
        else app.allowed_pipes
    )
    if hasattr(app, "_derive_hydro_mat_pn_from_allowed"):
        mat, pn = app._derive_hydro_mat_pn_from_allowed(src)
    else:
        mat = app.pipe_material.get()
        pn = str(app.pipe_pn.get())
    db = app.pipe_db.get(mat, {}).get(pn, {})
    allowed = src.get(mat, {}).get(pn, [])

    def collect_stick_lengths(pipe_map: dict) -> list:
        out = []
        for _od, pd in (pipe_map or {}).items():
            if not isinstance(pd, dict) or pd.get("length") is None:
                continue
            try:
                L = float(pd["length"])
            except (TypeError, ValueError):
                continue
            if 0.05 < L <= _MAX_STICK_HINT_M:
                out.append(L)
        return out

    cand = collect_stick_lengths({od: db.get(od) for od in (allowed or list(db.keys())) if db.get(od)})
    if cand:
        return float(min(cand))
    cand = collect_stick_lengths(db)
    if cand:
        return float(min(cand))
    return 6.0


def _plan_segments_look_like_phantom_coil(segments: list, L_geoms: list) -> bool:
    """Збережений план з unit_m≈100 (бухта з каталогу) або сума моделі >> геометрії секції."""
    if not segments or len(segments) != len(L_geoms):
        return False
    for s, Lg in zip(segments, L_geoms):
        if not isinstance(s, dict):
            continue
        try:
            um = float(s.get("unit_m", 0) or 0)
            n = float(s.get("n_sticks", 1) or 1)
            k = float(s.get("k_mult", 1) or 1)
            model = k * n * um
            Lgeom = float(Lg)
        except (TypeError, ValueError):
            continue
        if um >= 45.0:
            return True
        if Lgeom > 1e-6 and model > Lgeom * 1.25 + 1.0:
            return True
    return False


def _format_unit_m_display(x: float) -> str:
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _model_len_m(seg: dict) -> float:
    return float(seg.get("k_mult", 1)) * float(seg.get("unit_m", 6)) * float(seg.get("n_sticks", 1))


def _default_seg_for_edge(L_geom: float, unit_m: float) -> dict:
    k = 1
    um = max(unit_m, 1e-6)
    n = max(1, int(L_geom / um))
    while k * um * n > L_geom + 1e-4 and n > 1:
        n -= 1
    while k * um * n > L_geom + 1e-4 and k > 1:
        k -= 1
    return {"k_mult": k, "n_sticks": max(1, n), "unit_m": float(unit_m)}


def ensure_segment_plan(block: dict, line_idx: int, L_geoms: list, unit_m: float) -> list:
    plan = block.setdefault("submain_segment_plan", {})
    if not isinstance(plan, dict):
        plan = {}
        block["submain_segment_plan"] = plan
    by_line = plan.setdefault("by_line", [])
    while len(by_line) <= line_idx:
        by_line.append({"segments": []})

    entry = by_line[line_idx]
    if not isinstance(entry, dict):
        entry = {"segments": []}
        by_line[line_idx] = entry

    segs = entry.get("segments")
    need_rebuild = (
        not isinstance(segs, list)
        or len(segs) != len(L_geoms)
        or any(not isinstance(s, dict) for s in segs)
    )
    if need_rebuild:
        segs = [_default_seg_for_edge(Lg, unit_m) for Lg in L_geoms]
        entry["segments"] = segs
    else:
        for s in segs:
            if s.get("unit_m") is None:
                s["unit_m"] = float(unit_m)
    return entry["segments"]


def _sections_for_sm(app, block_idx: int, sm_idx: int) -> list:
    rows = [
        s
        for s in (app.calc_results.get("sections") or [])
        if int(s.get("block_idx", -1)) == int(block_idx) and int(s.get("sm_idx", -1)) == int(sm_idx)
    ]
    rows.sort(
        key=lambda s: (
            int(s.get("section_index", 10**9)),
            float(((s.get("coords") or [[0.0, 0.0]])[0][0] if (s.get("coords") or []) else 0.0)),
        )
    )
    return rows


def diameter_label_for_section(app, block_idx: int, sm_idx: int, sec_idx: int, n_edges: int) -> str:
    secs = _sections_for_sm(app, block_idx, sm_idx)
    if len(secs) == n_edges and 0 <= sec_idx < len(secs):
        d = secs[sec_idx].get("d")
        if d is not None:
            return f"{float(d):.2f} мм"
    if secs:
        d = secs[0].get("d")
        if d is not None:
            return f"{float(d):.2f} мм"
    return "—"


def open_submain_segment_editor(app):
    bi = app._safe_active_block_idx()
    if bi is None:
        silent_showwarning(app.root, "Увага", "Немає блоків поля.")
        return
    block = app.field_blocks[bi]
    lines = block.get("submain_lines") or []
    valid_ix = [i for i, sm in enumerate(lines) if sm and len(sm) >= 2]
    if not valid_ix:
        silent_showwarning(app.root, 
            "Увага",
            "У активному блоці немає магістралі з двома точками. Намалюйте сабмейн у режимі SUBMAIN.",
        )
        return
    SubmainSegmentEditorDialog(app, bi, block, valid_ix)


class SubmainSegmentEditorDialog:
    EPS = 1e-3

    def __init__(self, app, block_idx: int, block: dict, line_indices: list):
        self.app = app
        self.block_idx = block_idx
        self.block = block
        self.line_indices = list(line_indices)
        self._plan_backup = copy.deepcopy(block.get("submain_segment_plan"))

        self.win = tk.Toplevel(app.root)
        self.win.title("Редактор сегментів сабмейну")
        self.win.configure(bg="#1e1e1e")
        self.win.geometry("860x620")
        self.win.minsize(820, 560)
        self.win.transient(app.root)

        _df = tkfont.nametofont("TkDefaultFont", self.win)
        self._entry_font = (_df.actual("family"), int(_df.actual("size")))

        self.var_line = tk.StringVar()
        self.var_section = tk.StringVar()
        self.var_qty = tk.StringVar(value="1")
        self.var_pipe_len_m = tk.StringVar(value="6")
        self.var_selected_geom_len = tk.StringVar(value="0.00")
        self.var_selected_model_len = tk.StringVar(value="0.00")
        self.var_req_len = tk.StringVar(value="0.000")
        self.var_cur_sum = tk.StringVar(value="0.000")
        self.var_delta = tk.StringVar(value="0.000")

        self._line_combo_ix = 0
        self.L_geoms: list = []
        self.segments: list = []
        self._sum_model_open = 0.0
        self._initialized_lines = set()

        top = tk.Frame(self.win, bg="#1e1e1e")
        top.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(
            top,
            text=f"Блок {block_idx + 1} · секції нумеруються від крана (перша точка магістралі).",
            bg="#1e1e1e",
            fg="#cccccc",
            font=("Arial", 9),
            wraplength=720,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        Lg_geom = app._geom_submain_length_for_block(block_idx)
        Lc = app._calc_submain_sections_length_for_block(block_idx)
        sub_txt = f"Геометрія сабмейнів блоку: {Lg_geom:.2f} м"
        if Lc is not None and Lc > 1e-6:
            sub_txt += f"  ·  У розрахунку (ΣL секцій на карті): {Lc:.2f} м"
        else:
            sub_txt += "  ·  Розрахунок ще не виконували (ΣL з гідравліки з’явиться після «Розрахунок»)"
        tk.Label(
            top,
            text=sub_txt,
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 9),
            wraplength=720,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

        row1 = tk.Frame(self.win, bg="#1e1e1e")
        row1.pack(fill=tk.X, padx=10, pady=4)
        tk.Label(row1, text="Гілка:", bg="#1e1e1e", fg="white").pack(side=tk.LEFT)
        line_labels = [f"Гілка {i + 1} (блок)" for i in self.line_indices]
        self.cb_line = ttk.Combobox(
            row1,
            textvariable=self.var_line,
            values=line_labels,
            state="readonly",
            width=36,
        )
        self.cb_line.pack(side=tk.LEFT, padx=8)
        self.cb_line.bind("<<ComboboxSelected>>", self._on_line_change)

        row2 = tk.Frame(self.win, bg="#1e1e1e")
        row2.pack(fill=tk.X, padx=10, pady=4)
        tk.Label(row2, text="Секція (від крана):", bg="#1e1e1e", fg="white").pack(side=tk.LEFT)
        self.cb_section = ttk.Combobox(row2, textvariable=self.var_section, state="readonly", width=28)
        self.cb_section.pack(side=tk.LEFT, padx=8)
        self.cb_section.bind("<<ComboboxSelected>>", self._on_section_change)

        tk.Label(row2, text="Діаметр:", bg="#1e1e1e", fg="#aaaaaa").pack(side=tk.LEFT, padx=(16, 4))
        self.lbl_d = tk.Label(row2, text="—", bg="#1e1e1e", fg="#00FFCC", width=18, anchor=tk.W)
        self.lbl_d.pack(side=tk.LEFT)

        self.unit_default = default_unit_m_from_app(app, block_idx)
        self.var_catalog_unit_hint = tk.StringVar(
            value=f"підказка з каталогу: {self.unit_default:.3f} м"
        )
        row_u = tk.Frame(self.win, bg="#1e1e1e")
        row_u.pack(fill=tk.X, padx=10, pady=4)
        self.entry_unit_m = tk.Entry(
            row_u,
            textvariable=self.var_pipe_len_m,
            width=_ENTRY_NUM_W,
            bg="#2a2a2a",
            fg="white",
            font=self._entry_font,
            insertbackground="white",
            insertwidth=2,
        )
        self.entry_unit_m.pack(side=tk.LEFT, padx=(0, 8))
        self.entry_unit_m.bind("<Return>", self._on_unit_m_entry_commit)
        self.entry_unit_m.bind("<FocusOut>", self._on_unit_m_entry_commit)
        attach_tooltip(self.entry_unit_m, "Довжина 1 труби, м")

        self.entry_qty = tk.Entry(
            row_u,
            textvariable=self.var_qty,
            width=_ENTRY_NUM_W,
            bg="#2a2a2a",
            fg="white",
            font=self._entry_font,
            insertbackground="white",
            insertwidth=2,
        )
        self.entry_qty.pack(side=tk.LEFT, padx=(0, 8))
        self.entry_qty.bind("<Return>", self._on_entry_commit)
        self.entry_qty.bind("<FocusOut>", self._on_entry_commit)
        attach_tooltip(self.entry_qty, "Кількість труб у вибраній секції")

        self.entry_new_len = tk.Entry(
            row_u,
            textvariable=self.var_selected_model_len,
            width=_ENTRY_NUM_W,
            state="readonly",
            readonlybackground="#2a2a2a",
            fg="#88DDFF",
            font=self._entry_font,
        )
        self.entry_new_len.pack(side=tk.LEFT, padx=(0, 8))
        attach_tooltip(self.entry_new_len, "Нова довжина секції = довжина труби × кількість труб")

        tk.Label(
            row_u,
            textvariable=self.var_catalog_unit_hint,
            bg="#1e1e1e",
            fg="#666666",
            font=("Arial", 8),
        ).pack(side=tk.LEFT, padx=(2, 0))

        row_sec_info = tk.Frame(self.win, bg="#1e1e1e")
        row_sec_info.pack(fill=tk.X, padx=10, pady=(2, 4))
        self.entry_selected_geom = tk.Entry(
            row_sec_info,
            textvariable=self.var_selected_geom_len,
            width=_ENTRY_NUM_W,
            state="readonly",
            readonlybackground="#2a2a2a",
            fg="#cccccc",
            font=self._entry_font,
        )
        self.entry_selected_geom.pack(side=tk.LEFT)
        attach_tooltip(self.entry_selected_geom, "Початкова довжина вибраної секції, м")

        calc_fr = tk.Frame(self.win, bg="#1e1e1e")
        calc_fr.pack(fill=tk.X, padx=10, pady=(6, 4))
        tk.Label(
            calc_fr,
            text="Калькулятор:",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.var_calc_a = tk.StringVar(value="")
        self.var_calc_b = tk.StringVar(value="")
        self.var_calc_res = tk.StringVar(value="")
        self.var_calc_op = tk.StringVar(value="×")
        ce = dict(
            width=_ENTRY_NUM_W,
            bg="#2a2a2a",
            fg="white",
            font=self._entry_font,
            insertbackground="white",
            insertwidth=2,
        )
        tk.Entry(calc_fr, textvariable=self.var_calc_a, **ce).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            calc_fr,
            textvariable=self.var_calc_op,
            values=["×", "+", "-", "/"],
            width=4,
            state="readonly",
            font=self._entry_font,
        ).pack(side=tk.LEFT, padx=2)
        tk.Entry(calc_fr, textvariable=self.var_calc_b, **ce).pack(side=tk.LEFT, padx=(4, 4))
        _btn_calc_eq = tk.Button(
            calc_fr, text="=", command=self._calc_simple_exec, width=3, bg="#444444", fg="white"
        )
        _btn_calc_eq.pack(side=tk.LEFT, padx=2)
        attach_tooltip(_btn_calc_eq, "Обчислити: перше число, операція, друге число — результат у полі праворуч.")
        tk.Entry(
            calc_fr,
            textvariable=self.var_calc_res,
            width=_ENTRY_NUM_W,
            state="readonly",
            readonlybackground="#1a3328",
            fg="#88FFAA",
            font=self._entry_font,
        ).pack(side=tk.LEFT, padx=(6, 4))
        _btn_calc_to_L = tk.Button(
            calc_fr,
            text="→ L",
            command=self._calc_simple_apply_to_pipe_len,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 8, "bold"),
        )
        _btn_calc_to_L.pack(side=tk.LEFT, padx=2)
        attach_tooltip(_btn_calc_to_L, "Підставити результат калькулятора в поле «довжина 1 труби».")

        self.canvas = tk.Canvas(self.win, width=740, height=160, bg="#121212", highlightthickness=0)
        self.canvas.pack(padx=10, pady=8)

        stats = tk.Frame(self.win, bg="#252525", relief=tk.GROOVE, bd=1)
        stats.pack(fill=tk.X, padx=10, pady=8)
        self.lbl_L_geom = tk.Label(stats, text="", bg="#252525", fg="#cccccc", anchor=tk.W, justify=tk.LEFT)
        self.lbl_L_geom.pack(fill=tk.X, padx=8, pady=2)
        self.lbl_L_model = tk.Label(stats, text="", bg="#252525", fg="#cccccc", anchor=tk.W)
        self.lbl_L_model.pack(fill=tk.X, padx=8, pady=2)
        self.lbl_remain = tk.Label(stats, text="", bg="#252525", fg="#88FF88", anchor=tk.W)
        self.lbl_remain.pack(fill=tk.X, padx=8, pady=2)
        self.lbl_delta_edit = tk.Label(stats, text="", bg="#252525", fg="#E8C547", anchor=tk.W)
        self.lbl_delta_edit.pack(fill=tk.X, padx=8, pady=2)
        self.lbl_unit = tk.Label(
            stats,
            text="",
            bg="#252525",
            fg="#888888",
            anchor=tk.W,
            font=("Arial", 8),
        )
        self.lbl_unit.pack(fill=tk.X, padx=8, pady=2)
        stats_fields = tk.Frame(self.win, bg="#1e1e1e")
        stats_fields.pack(fill=tk.X, padx=10, pady=(0, 8))
        tk.Label(stats_fields, text="Необхідна довжина сабмейну:", bg="#1e1e1e", fg="#aaaaaa").pack(side=tk.LEFT)
        tk.Entry(
            stats_fields,
            textvariable=self.var_req_len,
            width=_ENTRY_NUM_W,
            state="readonly",
            readonlybackground="#2a2a2a",
            fg="#cccccc",
            font=self._entry_font,
        ).pack(side=tk.LEFT, padx=6)
        tk.Label(stats_fields, text="Поточна сума секцій:", bg="#1e1e1e", fg="#aaaaaa").pack(side=tk.LEFT, padx=(12, 0))
        tk.Entry(
            stats_fields,
            textvariable=self.var_cur_sum,
            width=_ENTRY_NUM_W,
            state="readonly",
            readonlybackground="#2a2a2a",
            fg="#88DDFF",
            font=self._entry_font,
        ).pack(side=tk.LEFT, padx=6)
        tk.Label(stats_fields, text="Різниця:", bg="#1e1e1e", fg="#aaaaaa").pack(side=tk.LEFT, padx=(12, 0))
        tk.Entry(
            stats_fields,
            textvariable=self.var_delta,
            width=_ENTRY_NUM_W,
            state="readonly",
            readonlybackground="#2a2a2a",
            fg="#E8C547",
            font=self._entry_font,
        ).pack(side=tk.LEFT, padx=6)

        btn_row = tk.Frame(self.win, bg="#1e1e1e")
        btn_row.pack(fill=tk.X, padx=10, pady=10)
        _btn_apply = tk.Button(
            btn_row,
            text="Застосувати і закрити",
            command=self._apply_close,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 9, "bold"),
            width=22,
        )
        _btn_apply.pack(side=tk.LEFT, padx=4)
        attach_tooltip(_btn_apply, "Зберегти план сегментів сабмейну в блоці й закрити редактор.")
        _btn_cancel = tk.Button(btn_row, text="Скасувати", command=self._cancel, bg="#444", fg="white", width=12)
        _btn_cancel.pack(side=tk.LEFT, padx=4)
        attach_tooltip(_btn_cancel, "Скинути зміни до відкриття діалогу й закрити без збереження.")
        # Додаткова "ручка" для ресайзу — завжди в куті вікна.
        self._sizegrip = ttk.Sizegrip(self.win)
        self._sizegrip.place(relx=1.0, rely=1.0, anchor=tk.SE)

        self.cb_line.current(0)
        self._on_line_change()
        self.win.grab_set()

    def _calc_simple_exec(self):
        try:
            a = float(str(self.var_calc_a.get()).replace(",", ".").strip() or "0")
            b = float(str(self.var_calc_b.get()).replace(",", ".").strip() or "0")
        except ValueError:
            self.var_calc_res.set("?")
            return
        op = str(self.var_calc_op.get()).strip()
        if op in ("×", "*", "x", "X"):
            r = a * b
        elif op == "+":
            r = a + b
        elif op == "-":
            r = a - b
        elif op in ("/", "÷"):
            if abs(b) < 1e-18:
                self.var_calc_res.set("—")
                return
            r = a / b
        else:
            r = a * b
        if math.isnan(r) or math.isinf(r):
            self.var_calc_res.set("—")
        else:
            self.var_calc_res.set(_format_unit_m_display(r))

    def _calc_simple_apply_to_pipe_len(self):
        self._calc_simple_exec()
        raw = (self.var_calc_res.get() or "").strip()
        if not raw or raw in ("?", "—"):
            return
        self.var_pipe_len_m.set(raw)
        self._on_entry_commit()

    def _cancel(self):
        if self._plan_backup is not None:
            self.block["submain_segment_plan"] = copy.deepcopy(self._plan_backup)
        else:
            self.block.pop("submain_segment_plan", None)
        self.win.destroy()

    def _current_sm_idx(self) -> int:
        return self.line_indices[self._line_combo_ix]

    def _geom_lengths_for_sm(self, sm_idx: int) -> list:
        sec_rows = _sections_for_sm(self.app, self.block_idx, sm_idx)
        if sec_rows:
            return [float(s.get("L", 0.0) or 0.0) for s in sec_rows]
        coords = self.block["submain_lines"][sm_idx]
        L_raw = _edge_lengths_m(coords)
        if len(L_raw) == 1 and L_raw[0] > 0:
            l = float(L_raw[0]) / 3.0
            return [l, l, l]
        return L_raw

    def _current_unit_m(self) -> float:
        try:
            raw = str(self.var_pipe_len_m.get()).replace(",", ".").strip()
            if not raw:
                return max(1e-6, float(self.unit_default))
            v = float(raw)
            return max(0.01, min(10000.0, v))
        except (ValueError, TypeError, tk.TclError):
            return max(1e-6, float(self.unit_default))

    def _on_unit_m_entry_commit(self, _evt=None):
        if not self.segments:
            return
        self._on_entry_commit()

    def _on_line_change(self, _evt=None):
        try:
            self._line_combo_ix = int(self.cb_line.current())
        except (tk.TclError, ValueError, TypeError):
            self._line_combo_ix = 0
        sm_idx = self._current_sm_idx()
        coords = self.block["submain_lines"][sm_idx]
        self.L_geoms = self._geom_lengths_for_sm(sm_idx)
        self.unit_default = default_unit_m_from_app(self.app, self.block_idx)
        self.var_catalog_unit_hint.set(f"підказка з каталогу: {self.unit_default:.3f} м")
        u_seed = float(self.unit_default)

        # Чи є вже явно збережений план для цієї гілки.
        plan = self.block.get("submain_segment_plan") or {}
        by_line = plan.get("by_line") if isinstance(plan, dict) else []
        has_explicit_plan = (
            isinstance(by_line, list)
            and sm_idx < len(by_line)
            and isinstance(by_line[sm_idx], dict)
            and isinstance(by_line[sm_idx].get("segments"), list)
            and len(by_line[sm_idx].get("segments") or []) == len(self.L_geoms)
        )

        self.segments = ensure_segment_plan(self.block, sm_idx, self.L_geoms, u_seed)
        # Відновити план, якщо в JSON потрапили «бухтові» 100 м з каталогу замість палок.
        if has_explicit_plan and _plan_segments_look_like_phantom_coil(self.segments, self.L_geoms):
            for i, Lg in enumerate(self.L_geoms):
                self.segments[i]["k_mult"] = 1
                self.segments[i]["n_sticks"] = 1
                self.segments[i]["unit_m"] = float(Lg)
        # Початковий стан: кожна секція = 1 труба на всю її довжину.
        elif (not has_explicit_plan) and (sm_idx not in self._initialized_lines):
            for i, Lg in enumerate(self.L_geoms):
                self.segments[i]["k_mult"] = 1
                self.segments[i]["n_sticks"] = 1
                self.segments[i]["unit_m"] = float(Lg)
            self._initialized_lines.add(sm_idx)

        if self.segments:
            u_seed = float(self.segments[0].get("unit_m", u_seed))
        self.var_pipe_len_m.set(_format_unit_m_display(u_seed))
        self._sum_model_open = sum(_model_len_m(s) for s in self.segments)

        sec_vals = [f"Секція {j + 1} · L_geom={self.L_geoms[j]:.2f} м" for j in range(len(self.L_geoms))]
        self.cb_section["values"] = sec_vals
        self.cb_section.current(0)
        self._on_section_change()
        self._redraw_schematic()
        self._refresh_stats()

    def _section_idx(self) -> int:
        try:
            return int(self.cb_section.current())
        except (tk.TclError, ValueError, TypeError):
            return 0

    def _on_section_change(self, _evt=None):
        si = self._section_idx()
        si = max(0, min(si, len(self.segments) - 1)) if self.segments else 0
        if not self.segments:
            return
        seg = self.segments[si]
        n = int(seg.get("n_sticks", 1))
        um = float(seg.get("unit_m", self.unit_default))
        self.var_qty.set(str(max(1, n)))
        self.var_pipe_len_m.set(_format_unit_m_display(um))
        self.var_selected_geom_len.set(f"{float(self.L_geoms[si]):.2f}")
        self.var_selected_model_len.set(f"{_model_len_m(seg):.2f}")
        sm_i = self._current_sm_idx()
        self.lbl_d.config(
            text=diameter_label_for_section(self.app, self.block_idx, sm_i, si, len(self.L_geoms))
        )
        self._redraw_schematic()

    def _on_entry_commit(self, _evt=None):
        si = self._section_idx()
        try:
            n = int(float(self.var_qty.get().replace(",", ".")))
        except (ValueError, TypeError):
            n = 1
        n = max(1, min(500, n))
        u = self._current_unit_m()
        if self.segments and 0 <= si < len(self.segments):
            self.segments[si]["k_mult"] = 1
            self.segments[si]["n_sticks"] = n
            self.segments[si]["unit_m"] = float(u)
            self.var_qty.set(str(n))
            self.var_pipe_len_m.set(_format_unit_m_display(u))
            self.var_selected_model_len.set(f"{_model_len_m(self.segments[si]):.2f}")
        self._redraw_schematic()
        self._refresh_stats()

    def _redraw_schematic(self):
        self.canvas.delete("all")
        W, H = int(float(self.canvas.cget("width"))), int(float(self.canvas.cget("height")))
        pad = 36
        if not self.L_geoms:
            self.canvas.create_text(W // 2, H // 2, text="Немає секцій", fill="#666")
            return
        total = sum(self.L_geoms) or 1.0
        sel = self._section_idx()
        y_mid = H // 2
        x0 = float(pad)
        usable = W - 2 * pad
        for i, Lg in enumerate(self.L_geoms):
            w = (Lg / total) * usable
            x1 = x0 + w
            color = "#00FFCC" if i == sel else "#555555"
            self.canvas.create_line(x0, y_mid, x1, y_mid, fill=color, width=5, capstyle="round")
            seg = self.segments[i] if i < len(self.segments) else {}
            n = int(seg.get("n_sticks", 1))
            um = float(seg.get("unit_m", self.unit_default))
            Lm = _model_len_m(seg) if seg else 0.0
            cx = (x0 + x1) * 0.5
            self.canvas.create_text(
                cx,
                y_mid - 38,
                text=f"{i + 1}",
                fill="#FFD700" if i == sel else "#888888",
                font=("Arial", 15, "bold"),
            )
            self.canvas.create_text(
                cx,
                y_mid - 22,
                text=f"n={n}  L={um:.2f} м",
                fill="#cccccc",
                font=("Arial", 12),
            )
            self.canvas.create_text(
                cx,
                y_mid + 22,
                text=f"L_geom {Lg:.2f} м",
                fill="#888888",
                font=("Arial", 12),
            )
            self.canvas.create_text(
                cx,
                y_mid + 38,
                text=f"модель {Lm:.2f} м ({n}×{um:.2g})",
                fill="#66AAFF" if Lm <= Lg + self.EPS else "#FF6666",
                font=("Arial", 12),
            )
            x0 = x1

    def _refresh_stats(self):
        if not self.L_geoms:
            return
        branch_no = int(self._current_sm_idx()) + 1
        s_geom = sum(self.L_geoms)
        s_mod = sum(_model_len_m(s) for s in self.segments)
        rem = s_geom - s_mod
        d_ed = s_mod - self._sum_model_open
        self.lbl_L_geom.config(text=f"Гілка {branch_no}: сума геометричних довжин секцій: {s_geom:.3f} м")
        self.lbl_L_model.config(text=f"Гілка {branch_no}: сума довжин за планом (n·Lтруби): {s_mod:.3f} м")
        ok = abs(s_geom - s_mod) <= self.EPS
        self.lbl_remain.config(
            text=f"Гілка {branch_no}: різниця (необхідна - поточна): {rem:.3f} м",
            fg="#88FF88" if ok else "#FF6666",
        )
        self.lbl_delta_edit.config(
            text=f"Зміна суми плану відносно відкриття вікна: {d_ed:+.3f} м (для підбору довжин)"
        )
        um = self._current_unit_m() if self.segments else self.unit_default
        src = (
            self.app._allowed_pipes_for_block_index(self.block_idx)
            if hasattr(self.app, "_allowed_pipes_for_block_index")
            else self.app.allowed_pipes
        )
        if hasattr(self.app, "_derive_hydro_mat_pn_from_allowed"):
            _dm, _dp = self.app._derive_hydro_mat_pn_from_allowed(src)
        else:
            _dm, _dp = self.app.pipe_material.get(), str(self.app.pipe_pn.get())
        self.lbl_unit.config(text=f"Поточна довжина труби у формулі: {um:.3f} м · {_dm} PN{_dp}")
        self.var_req_len.set(f"{s_geom:.3f}")
        self.var_cur_sum.set(f"{s_mod:.3f}")
        self.var_delta.set(f"{rem:.3f}")

    def _apply_close(self):
        # Контроль різниці виконується для активної (редагованої) гілки.
        sm_idx = self._current_sm_idx()
        s_geom = sum(self._geom_lengths_for_sm(sm_idx))
        s_mod = sum(_model_len_m(s) for s in self.segments if isinstance(s, dict))
        if abs(s_mod - s_geom) > self.EPS:
            silent_showwarning(self.app.root,
                "Увага",
                "Контроль виконується по редагованій гілці.\n"
                f"Гілка {sm_idx + 1}: потрібно {s_geom:.3f} м, задано {s_mod:.3f} м, різниця {s_geom - s_mod:.3f} м.",
            )
            return
        self.app.redraw()
        self.win.destroy()
        # Після зміни плану секцій одразу оновлюємо гідравлічний розрахунок.
        try:
            self.app.run_calculation()
        except Exception as ex:
            try:
                silent_showerror(self.app.root, "Помилка", f"Не вдалося оновити розрахунок:\n{ex}")
            except Exception:
                pass
