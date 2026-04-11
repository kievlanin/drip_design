"""
Спільна панель «Малювання» + «Магістраль» для лівої колонки (карта та режим «Без карти»).
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
        "Ламана на карті; дерево від насоса (кран — відведення/сток, розгалуження, Q(t))."
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
        "Вибір (стрілка): ЛКМ по об'єкту — підпис; на магістралі — жовтий шлях до насоса, біля розгалуження лайм до споживачів.",
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
    tk.Label(
        _row_info_tool,
        text=" Вибір / Інфо — ЛКМ по об'єкту",
        bg="#181818",
        fg="#9E9E9E",
        font=("Segoe UI", 8),
    ).pack(side=tk.LEFT, padx=(6, 0))
    attach_tt(
        _btn_map_pick_info,
        "Інфо (рука): те саме, що «Вибір», інший курсор на карті.",
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
        "На карті: ЛКМ по вузлах, ПКМ — кінець відрізка. Без вузлів — вільна траса в сабмейн активного блоку.",
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
    _btn_trunk_valve = tk.Button(
        tab_trunk,
        text="⎔ Кран (відведення)",
        command=lambda: set_tool("trunk_valve"),
        bg="#4a3820",
        fg="#E8D4A8",
        relief=tk.FLAT,
    )
    _btn_trunk_valve.pack(fill=tk.X, padx=8, pady=3)
    attach_tt(
        _btn_trunk_valve,
        "Кран (valve): ЛКМ — новий вузол на кожен клік, ПКМ — вийти з команди.",
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


def build_draw_modes_tab(tab_draw: tk.Misc, app, attach_tt=_attach_dark_tooltip) -> None:
    """Режими V/D/SM/… синхронізовані з app.mode та app.action."""
    if app is not None and hasattr(app, "mode") and hasattr(app, "action"):
        _map_mode_tips = {
            "VIEW": "Перегляд: навігація (полотно / карта).",
            "DRAW": "Контур блоку поля.",
            "SET_DIR": "Напрямок рядів у блоці.",
            "SUBMAIN": "Режим сабмейну.",
            "DRAW_LAT": "Ручна лінія / латераль.",
            "PAN": "Панорама полотна.",
            "INFO": "Після розрахунку: графіки по латералі / сабмейну.",
            "CUT_LATS": "Різання латералів.",
            "RULER": "Лінійка вимірювання відстані.",
            "SUB_LABEL": "Підписи секцій сабмейну.",
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
            if hasattr(app, "reset_temp"):
                app.reset_temp()
            _map_refresh_draw_toolbar_leds()

        def _map_set_action(code: str) -> None:
            app.action.set(code)
            _map_refresh_draw_toolbar_leds()

        tk.Label(tab_draw, text="Режими", bg="#181818", fg="#FFD700", font=("Segoe UI", 8, "bold")).pack(
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

        tk.Label(tab_draw, text="Дія", bg="#181818", fg="#FFD700", font=("Segoe UI", 8, "bold")).pack(
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
    знизу — лише вкладки «Панель малювання» (як на карті).
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
    build_trunk_tools_tab(tab_trunk, map_tool_router, attach_tt)
    return draw_nb
