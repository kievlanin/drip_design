"""
Спільна панель «Малювання» + «Магістраль» для лівої колонки (карта та режим «Без карти»).
Розрахунок HW за поливами — вкладка «Магістраль (HW)» на панелі керування праворуч.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from main_app.ui.tooltips import attach_tooltip_dark as _attach_dark_tooltip

SetToolFn = Callable[[Optional[str]], None]


def build_trunk_tools_tab(
    tab_trunk: tk.Misc,
    set_tool: SetToolFn,
    attach_tt=_attach_dark_tooltip,
    *,
    on_map_tab: bool = False,
    app=None,
) -> None:
    """Кнопки магістралі; set_tool — на карті прямий _set_tool, без карти — маршрутизатор на embedded host."""
    tk.Label(
        tab_trunk,
        text="Траса та модель магістралі",
        bg="#181818",
        fg="#8BC4FF",
        font=("Segoe UI", 9, "bold"),
    ).pack(fill=tk.X, padx=8, pady=(8, 4))
    _sub = (
        "Ламана на карті; дерево від насоса (пікет, розгалуження, споживач, Q(t))."
        if on_map_tab
        else "На «Без карти»: ЛКМ по полотну, ПКМ — зафіксувати вузол/лінію. Зона проєкту — лише на «Карті»; «Вибір»/«Інфо» працюють і на полотні."
    )
    tk.Label(
        tab_trunk,
        text=_sub,
        bg="#181818",
        fg="#909090",
        font=("Segoe UI", 8),
        wraplength=178,
        justify=tk.LEFT,
    ).pack(fill=tk.X, padx=8, pady=(0, 6))
    _row_info_tool = tk.Frame(tab_trunk, bg="#181818")
    _row_info_tool.pack(fill=tk.X, padx=8, pady=(0, 8))
    _btn_select = tk.Canvas(
        _row_info_tool,
        width=28,
        height=26,
        highlightthickness=0,
        bg="#2E3D32",
        bd=0,
        cursor="hand2",
    )
    _pad = 5
    _btn_select.create_line(
        _pad,
        26 - _pad,
        28 - _pad,
        _pad,
        fill="#C8E6C9",
        width=2,
        arrow=tk.LAST,
        arrowshape=(8, 10, 4),
    )
    _btn_select.pack(side=tk.LEFT)

    def _on_select_canvas_click(_event=None) -> None:
        set_tool("select")

    _btn_select.bind("<Button-1>", _on_select_canvas_click)
    attach_tt(
        _btn_select,
        "Вибір (стрілка): ЛКМ — об'єкт; Ctrl+ЛКМ — додати/зняти з групи; рамка — Ctrl додає до вже обраного; ПКМ — список обраного й скидання. На магістралі — жовтий шлях до насоса, біля розгалуження лайм до споживачів.",
        above=True,
    )
    _btn_map_pick_info = tk.Button(
        _row_info_tool,
        text="i",
        font=("Segoe UI", 11, "bold"),
        width=3,
        command=lambda: set_tool("map_pick_info"),
        bg="#37474F",
        fg="#ECEFF1",
        activebackground="#455A64",
        activeforeground="#FFFFFF",
        relief=tk.FLAT,
    )
    _btn_map_pick_info.pack(side=tk.LEFT, padx=(5, 0))
    attach_tt(
        _btn_map_pick_info,
        "Інфо (рука): те саме, що «Вибір», інший курсор на карті.",
        above=True,
    )

    def _on_trunk_hover_pipe_mode(_event=None) -> None:
        if app is None:
            return
        try:
            app.redraw()
        except Exception:
            pass
        if hasattr(app, "_schedule_embedded_map_overlay_refresh"):
            app._schedule_embedded_map_overlay_refresh()

    if app is not None and hasattr(app, "var_trunk_map_hover_pipes_mode"):
        _gp_btn = tk.Button(
            _row_info_tool,
            text="G",
            bg="#2E3D32",
            fg="#E8F5E9",
            activebackground="#3E4D42",
            activeforeground="#FFFFFF",
            font=("Segoe UI", 9, "bold"),
            width=3,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
        )

        def _sync_gp_caption() -> None:
            try:
                _gp_btn.config(text="P" if app.var_trunk_map_hover_pipes_mode.get() else "G")
            except tk.TclError:
                pass

        def _toggle_graph_pipes(_event=None) -> None:
            v = app.var_trunk_map_hover_pipes_mode
            v.set(not bool(v.get()))
            _sync_gp_caption()
            _on_trunk_hover_pipe_mode()

        _gp_btn.config(command=_toggle_graph_pipes)
        _sync_gp_caption()
        _gp_btn.pack(side=tk.LEFT, padx=(6, 0))
        attach_tt(
            _gp_btn,
            "Граф / труби (ЛКМ): G — топологія ребра (A→B, L); P — результат розрахунку: підсвітка окремої секції телескопа; "
            "у підказці — довжина L і знак Ø з числом (число — зовнішній діаметр, мм). Оновлює полотно й карту.",
            above=True,
        )
    _emb_btn_trunk = tk.Button(
        tab_trunk,
        text="🟩 Траса магістралі",
        command=lambda: set_tool("trunk_route"),
        bg="#242424",
        fg="#E8E8E8",
        relief=tk.FLAT,
    )
    _emb_btn_trunk.pack(fill=tk.X, padx=8, pady=3)
    attach_tt(
        _emb_btn_trunk,
        "Труба магістралі: ЛКМ+ПКМ на вузлі — кінець ребра; далі ЛКМ — початок і трасувальні точки "
        "(вільне поле на карті — автопікет bend); ПКМ — з’єднати останню точку з кінцем. "
        "Чернетка зберігається при перемиканні на насос/пікет/розгалуження/споживача. "
        "Топологія — «Зберегти граф магістралі». Без вузлів на карті — вільна траса в сабмейн блоку.",
    )
    _btn_trunk_pump = tk.Button(
        tab_trunk,
        text="◆ Насос (витік)",
        command=lambda: set_tool("trunk_pump"),
        bg="#5c1818",
        fg="#FFCDD2",
        relief=tk.FLAT,
    )
    _btn_trunk_pump.pack(fill=tk.X, padx=8, pady=3)
    attach_tt(
        _btn_trunk_pump,
        "Єдиний насос (source): кожен ЛКМ переміщує його, ПКМ — вийти з команди.",
    )
    _btn_trunk_picket = tk.Button(
        tab_trunk,
        text="● Пікет (на трасі)",
        command=lambda: set_tool("trunk_picket"),
        bg="#0d2840",
        fg="#90CAF9",
        relief=tk.FLAT,
    )
    _btn_trunk_picket.pack(fill=tk.X, padx=8, pady=3)
    attach_tt(
        _btn_trunk_picket,
        "Пікет (bend): ЛКМ — додати вузол (можна кілька підряд), ПКМ — вийти з команди.",
    )
    _btn_trunk_junction = tk.Button(
        tab_trunk,
        text="✹ Розгалуження",
        command=lambda: set_tool("trunk_junction"),
        bg="#0d2840",
        fg="#BBDEFB",
        relief=tk.FLAT,
    )
    _btn_trunk_junction.pack(fill=tk.X, padx=8, pady=3)
    attach_tt(
        _btn_trunk_junction,
        "Розгалуження (junction): ЛКМ — новий вузол, ПКМ — вийти з команди.",
    )
    _btn_trunk_consumer = tk.Button(
        tab_trunk,
        text="▲ Споживач (сток)",
        command=lambda: set_tool("trunk_consumer"),
        bg="#3d2e18",
        fg="#E8D4A8",
        relief=tk.FLAT,
    )
    _btn_trunk_consumer.pack(fill=tk.X, padx=8, pady=3)
    attach_tt(
        _btn_trunk_consumer,
        "Споживач (consumption): ЛКМ — новий вузол, ПКМ — вийти з команди.",
    )
    if app is not None and hasattr(app, "commit_trunk_graph_topology"):
        _btn_trunk_save = tk.Button(
            tab_trunk,
            text="💾 Зберегти граф магістралі",
            command=app.commit_trunk_graph_topology,
            bg="#1f3d2e",
            fg="#C8F5D8",
            relief=tk.FLAT,
        )
        _btn_trunk_save.pack(fill=tk.X, padx=8, pady=(8, 3))
        attach_tt(
            _btn_trunk_save,
            "Завершити редагування (вимкнути інструменти), перевірити топологію дерева та оновити trunk_tree з вузлів і відрізків.",
        )
def build_draw_modes_tab(tab_draw: tk.Misc, app, attach_tt=_attach_dark_tooltip) -> None:
    """Режими V/D/SM/… синхронізовані з app.mode та app.action."""
    if app is not None and hasattr(app, "mode") and hasattr(app, "action"):
        _map_mode_tips = {
            "VIEW": "Перегляд: навігація (полотно / карта).",
            "DRAW": "Контур блоку поля.",
            "SET_DIR": "Напрямок рядів у блоці.",
            "SUBMAIN": "Сабмейн: ЛКМ — клапан, далі ЛКМ — проміжні точки, ПКМ — кінець треки.",
            "DRAW_LAT": "Ручна лінія / латераль.",
            "PAN": "Панорама полотна.",
            "INFO": "Після розрахунку: графіки по латералі / сабмейну.",
            "CUT_LATS": "Різання латералів.",
            "RULER": "Лінійка вимірювання відстані.",
            "SUB_LABEL": "Підписи секцій сабмейну та телескопа магістралі: ЛКМ — взяти, рух миші, ЛКМ — відпустити на місці.",
            "LAT_TIP": "Оцінка тиску на тупиках латераля.",
        }
        _map_mode_buttons = {}
        _map_action_buttons = {}

        def _map_refresh_draw_toolbar_leds() -> None:
            cur_m = app.mode.get()
            for m, btn in _map_mode_buttons.items():
                active = m == cur_m
                btn.config(
                    bg="#1f6f2a" if active else "#5a1f1f",
                    fg="#DDFFDD" if active else "#FFD0D0",
                )
            cur_a = app.action.get()
            for a, btn in _map_action_buttons.items():
                active = a == cur_a and cur_m != "VIEW"
                btn.config(
                    bg="#1f6f2a" if active else "#5a1f1f",
                    fg="#DDFFDD" if active else "#FFD0D0",
                )

        def _map_set_mode(code: str) -> None:
            app.mode.set(code)
            if hasattr(app, "_clear_select_tool_if_blocking_draw_mode"):
                app._clear_select_tool_if_blocking_draw_mode(code)
            if hasattr(app, "reset_temp"):
                app.reset_temp()
            _map_refresh_draw_toolbar_leds()

        def _map_set_action(code: str) -> None:
            app.action.set(code)
            _map_refresh_draw_toolbar_leds()

        tk.Label(tab_draw, text="Дія", bg="#181818", fg="#FFD700", font=("Segoe UI", 8, "bold")).pack(
            fill=tk.X, padx=6, pady=(8, 2)
        )
        _mode_rows = (
            (("V", "VIEW"), ("D", "DRAW"), ("DR", "SET_DIR")),
            (("SM", "SUBMAIN"), ("LT", "DRAW_LAT"), ("P", "PAN")),
            (("I", "INFO"), ("R", "RULER"), ("C", "CUT_LATS")),
            (("LBL", "SUB_LABEL"), ("T", "LAT_TIP"), (None, None)),
        )
        for row_def in _mode_rows:
            rw = tk.Frame(tab_draw, bg="#181818")
            rw.pack(fill=tk.X, padx=4, pady=1)
            for txt, code in row_def:
                if not code:
                    tk.Frame(rw, bg="#181818", width=4).pack(side=tk.LEFT, expand=True)
                    continue
                btn_mode = tk.Button(
                    rw,
                    text=txt,
                    width=4,
                    command=lambda c=code: _map_set_mode(c),
                    bg="#5a1f1f",
                    fg="#FFD0D0",
                    relief=tk.FLAT,
                    padx=1,
                    pady=2,
                )
                btn_mode.pack(side=tk.LEFT, padx=2, pady=1)
                attach_tt(btn_mode, _map_mode_tips.get(code, code), above=True)
                _map_mode_buttons[code] = btn_mode

        tk.Label(tab_draw, text="Режим", bg="#181818", fg="#FFD700", font=("Segoe UI", 8, "bold")).pack(
            fill=tk.X, padx=6, pady=(10, 2)
        )
        ra = tk.Frame(tab_draw, bg="#181818")
        ra.pack(fill=tk.X, padx=6, pady=(0, 8))
        btn_add = tk.Button(
            ra,
            text="+ ADD",
            command=lambda: _map_set_action("ADD"),
            bg="#5a1f1f",
            fg="#FFD0D0",
            relief=tk.FLAT,
        )
        btn_add.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        attach_tt(btn_add, "Режим додавання вузлів.", above=True)
        _map_action_buttons["ADD"] = btn_add
        btn_del = tk.Button(
            ra,
            text="− DEL",
            command=lambda: _map_set_action("DEL"),
            bg="#5a1f1f",
            fg="#FFD0D0",
            relief=tk.FLAT,
        )
        btn_del.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        attach_tt(btn_del, "Режим видалення вузлів по кліку.", above=True)
        _map_action_buttons["DEL"] = btn_del

        rs = tk.Frame(tab_draw, bg="#181818")
        rs.pack(fill=tk.X, padx=6, pady=(0, 6))
        btn_lines = tk.Button(
            rs,
            text="Лінії",
            command=lambda: app.set_canvas_special_tool("scene_lines") if hasattr(app, "set_canvas_special_tool") else None,
            bg="#2b2b2b",
            fg="#E8E8E8",
            relief=tk.FLAT,
        )
        btn_lines.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        attach_tt(
            btn_lines,
            "Декоративні лінії (ескіз): ЛКМ — вершини, ПКМ — завершити. Працює на карті й у режимі «Без карти».",
            above=True,
        )
        btn_lines_cancel = tk.Button(
            rs,
            text="Скас.",
            command=lambda: app.set_canvas_special_tool(None) if hasattr(app, "set_canvas_special_tool") else None,
            bg="#3a2424",
            fg="#FFD0D0",
            relief=tk.FLAT,
        )
        btn_lines_cancel.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        attach_tt(btn_lines_cancel, "Вимкнути інструмент «Лінії».", above=True)

        tk.Label(
            tab_draw,
            text="Точна довжина L (м) — поле на правій панелі; Enter там.",
            bg="#181818",
            fg="#7a9aaa",
            font=("Segoe UI", 7),
            wraplength=178,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=8, pady=(0, 8))

        app.mode.trace_add("write", lambda *_: _map_refresh_draw_toolbar_leds())
        app.action.trace_add("write", lambda *_: _map_refresh_draw_toolbar_leds())
        _map_refresh_draw_toolbar_leds()
    else:
        tk.Label(
            tab_draw,
            text="Панель режимів доступна в головному вікні з відкритим проєктом.",
            bg="#181818",
            fg="#888888",
            font=("Segoe UI", 8),
            wraplength=178,
            justify=tk.LEFT,
        ).pack(fill=tk.BOTH, expand=True, padx=8, pady=12)


def build_off_canvas_draw_notebook(
    parent: tk.Misc,
    app,
    *,
    map_tool_router: SetToolFn,
    attach_tt=_attach_dark_tooltip,
) -> ttk.Notebook:
    """
    Ліва колонка «Без карти»: зверху інструментальна плейсхолдер-панель,
    знизу — вкладки «Малювання» / «Магістраль».
    """
    _nb_style = ttk.Style(parent.winfo_toplevel())
    try:
        _nb_style.theme_use("clam")
    except tk.TclError:
        pass
    _nb_style.configure("OffCanvasDraw.TNotebook", background="#181818", borderwidth=0)
    _nb_style.configure(
        "OffCanvasDraw.TNotebook.Tab",
        background="#333333",
        foreground="#e8e8e8",
        font=("Segoe UI", 8, "bold"),
        padding=[5, 2],
    )
    _nb_style.map("OffCanvasDraw.TNotebook.Tab", background=[("selected", "#0066FF")])

    paned = tk.PanedWindow(
        parent,
        orient=tk.VERTICAL,
        bd=0,
        sashwidth=6,
        bg="#2a2a2a",
        sashrelief=tk.FLAT,
        sashpad=1,
    )
    paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=4)

    instrumental = tk.Frame(paned, bg="#181818")
    draw_wrap = tk.Frame(paned, bg="#181818", highlightthickness=1, highlightbackground="#3d3d3d")
    paned.add(instrumental, minsize=88)
    paned.add(draw_wrap, minsize=168)

    def _init_off_canvas_sash() -> None:
        try:
            paned.update_idletasks()
            h = max(220, int(paned.winfo_height()))
            paned.sash_place(0, 0, min(h - 180, int(h * 0.32)))
        except tk.TclError:
            pass

    parent.winfo_toplevel().after(150, _init_off_canvas_sash)

    tk.Label(
        instrumental,
        text="Інструментальна панель",
        bg="#181818",
        fg="#A8E6FF",
        font=("Segoe UI", 9, "bold"),
        anchor="w",
    ).pack(fill=tk.X, padx=8, pady=(8, 2))
    tk.Label(
        instrumental,
        text="Плейсхолдер: сюди додаватимуться інші інструменти.",
        bg="#181818",
        fg="#666666",
        font=("Segoe UI", 8),
        wraplength=178,
        justify=tk.LEFT,
        anchor="nw",
    ).pack(fill=tk.X, padx=8, pady=(0, 8))

    tk.Label(
        draw_wrap,
        text="Панель малювання",
        bg="#181818",
        fg="#88CCFF",
        font=("Segoe UI", 8, "bold"),
        anchor="w",
    ).pack(fill=tk.X, padx=6, pady=(6, 2))

    draw_nb = ttk.Notebook(draw_wrap, style="OffCanvasDraw.TNotebook")
    draw_nb.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 4))

    tab_draw = tk.Frame(draw_nb, bg="#181818")
    tab_trunk = tk.Frame(draw_nb, bg="#181818")
    draw_nb.add(tab_draw, text="Малювання")
    draw_nb.add(tab_trunk, text="Магістраль")

    build_draw_modes_tab(tab_draw, app, attach_tt)
    build_trunk_tools_tab(tab_trunk, map_tool_router, attach_tt, app=app)
    return draw_nb
