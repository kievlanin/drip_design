import tkinter as tk
from tkinter import ttk, scrolledtext

from shapely.geometry import Polygon

from main_app.ui.silent_messagebox import silent_askyesno, silent_showinfo, silent_showwarning
from main_app.ui.tooltips import attach_tooltip


def _block_contour_area_m2(app, bi: int):
    """Площа контуру блоку в м² (локальні координати XY)."""
    try:
        blocks = getattr(app, "field_blocks", None) or []
        if bi < 0 or bi >= len(blocks):
            return None
        ring = list(blocks[bi].get("ring") or [])
        if len(ring) < 3:
            return None
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        return float(abs(poly.area))
    except Exception:
        return None


class ControlPanel:
    def __init__(self, app):
        self.app = app
        self.is_expanded = True
        
        self.main_frame = tk.Frame(app.root, bg="#1e1e1e")
        self.main_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.strip_frame = tk.Frame(self.main_frame, bg="#0066FF", width=15, cursor="sb_h_double_arrow")
        self.strip_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.strip_frame.bind("<Button-1>", self.toggle_panel)
        
        self.lbl_toggle = tk.Label(self.strip_frame, text="▶", bg="#0066FF", fg="white", font=("Arial", 10, "bold"))
        self.lbl_toggle.pack(expand=True)
        self.lbl_toggle.bind("<Button-1>", self.toggle_panel)
        
        self.content_frame = tk.Frame(self.main_frame, bg="#1e1e1e", width=320)
        self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.header_frame = tk.Frame(self.content_frame, bg="#1e1e1e", cursor="fleur")
        self.header_frame.pack(fill=tk.X, pady=(10, 5), padx=10)
        self.lbl_title = tk.Label(self.header_frame, text="НАЗВА ПРОЕКТУ (Тягни мене):", bg="#1e1e1e", fg="#00FFCC", font=("Arial", 8, "bold"), cursor="fleur")
        self.lbl_title.pack(anchor=tk.W)
        tk.Entry(
            self.header_frame,
            textvariable=app.var_proj_name,
            bg="#222",
            fg="#FFD700",
            font=("Consolas", 12, "bold"),
            justify='center',
            insertbackground="white",
            insertwidth=2,
        ).pack(fill=tk.X)

        self.header_frame.bind("<ButtonPress-1>", self.start_move)
        self.header_frame.bind("<B1-Motion>", self.do_move)
        self.lbl_title.bind("<ButtonPress-1>", self.start_move)
        self.lbl_title.bind("<B1-Motion>", self.do_move)

        style = ttk.Style(app.root)
        style.theme_use('clam')
        style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#333", foreground="white", font=("Arial", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#0066FF")])

        self.notebook = ttk.Notebook(self.content_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.build_block_tab()
        self.build_geo_tab()
        self.build_hydro_tab()
        self.build_schedule_tab()
        self.build_results_tab()
        self.build_topo_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        self.stats_label = tk.Label(self.content_frame, text="", bg="#111", fg="#00FFCC", font=("Consolas", 9), justify=tk.LEFT, padx=5, pady=5)
        self.stats_label.pack(fill=tk.X, side=tk.BOTTOM)

        _len_fr = tk.Frame(self.content_frame, bg="#1e1e1e")
        tk.Label(
            _len_fr,
            text="L (м):",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6))
        self.len_entry = tk.Entry(
            _len_fr,
            bg="#222",
            fg="cyan",
            font=("Consolas", 10, "bold"),
            width=10,
            justify="center",
            insertbackground="white",
            insertwidth=2,
        )
        self.len_entry.pack(side=tk.LEFT)
        self.len_entry.bind("<Return>", self.app.add_by_length)
        self._attach_tooltip(
            self.len_entry,
            "Точна довжина лінії (м). Enter — додати відрізок. Режими малювання — ліва панель «Без карти» або вкладка «Карта».",
        )
        _len_fr.pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=(0, 4))

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.window.winfo_x() + deltax if hasattr(self, 'window') else self.main_frame.winfo_x() + deltax
        y = self.window.winfo_y() + deltay if hasattr(self, 'window') else self.main_frame.winfo_y() + deltay
        pass

    def toggle_panel(self, event=None):
        if self.is_expanded:
            self.content_frame.pack_forget() 
            self.lbl_toggle.config(text="◀")
            self.is_expanded = False
        else:
            self.content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True) 
            self.lbl_toggle.config(text="▶")
            self.is_expanded = True

    def show(self):
        if not self.is_expanded: self.toggle_panel()

    def hide(self):
        if self.is_expanded: self.toggle_panel()

    def _attach_tooltip(self, widget, text: str):
        attach_tooltip(widget, text)

    def sync_hydro_clear_block_selector(self):
        cb = getattr(self, "cb_hydro_clear_block", None)
        if cb is None:
            return
        n = len(self.app.field_blocks or [])
        vals = [str(i) for i in range(1, n + 1)]
        cb["values"] = vals
        if n <= 0:
            cb.set("")
            cb.config(state="disabled")
            return
        cur = (self.app.var_hydro_clear_block.get() or "").strip()
        if cur not in vals:
            cur = str(min(max(self.app._safe_active_block_idx() + 1, 1), n))
            self.app.var_hydro_clear_block.set(cur)
        cb.set(cur)
        cb.config(state="readonly")

    def sync_report_block_selector(self):
        cb = getattr(self, "cb_report_block", None)
        if cb is None:
            return
        n = len(self.app.field_blocks or [])
        vals = [str(i) for i in range(1, n + 1)]
        cb["values"] = vals
        if n <= 0:
            cb.set("")
            cb.config(state="disabled")
            return
        cur = (self.app.var_report_block.get() or "").strip() if hasattr(self.app, "var_report_block") else ""
        if cur not in vals:
            cur = str(min(max(self.app._safe_active_block_idx() + 1, 1), n))
            self.app.var_report_block.set(cur)
        cb.set(cur)
        cb.config(state="readonly")

    def _render_block_report_text(self):
        txt = getattr(self, "txt_block_report", None)
        if txt is None:
            return
        txt.config(state=tk.NORMAL)
        txt.delete("1.0", tk.END)

        n = len(self.app.field_blocks or [])
        if n <= 0:
            txt.insert(tk.END, "Немає блоків поля.")
            txt.config(state=tk.DISABLED)
            return
        try:
            bi = int((self.app.var_report_block.get() or "1").strip()) - 1
        except Exception:
            bi = 0
        bi = max(0, min(n - 1, bi))
        self.app.var_report_block.set(str(bi + 1))

        txt.insert(tk.END, f"=== Блок {bi + 1} ===\n\n")
        area_m2 = _block_contour_area_m2(self.app, bi)
        if area_m2 is not None:
            ha = area_m2 / 10000.0
            txt.insert(
                tk.END,
                f"Площа поля (контур блоку): {area_m2:.1f} м² ({ha:.3f} га)\n\n",
            )
        else:
            txt.insert(
                tk.END,
                "Площа: немає замкненого контуру блоку (≥3 точок).\n\n",
            )
        sections = [
            s for s in (self.app.calc_results.get("sections") or [])
            if int(s.get("block_idx", -1)) == bi
        ]
        if not sections:
            txt.insert(tk.END, "Гідравлічні секції відсутні. Виконайте розрахунок для цього блоку.\n")
        else:
            txt.insert(tk.END, "--- Сабмейн: секції ---\n")
            for s in sections:
                smi = int(s.get("sm_idx", -1))
                txt.insert(
                    tk.END,
                    f"SM {smi + 1}: {s.get('mat','?')} d{s.get('d','?')}/{s.get('pn','?')}  "
                    f"L={float(s.get('L', 0.0)):.1f} м\n",
                )

        valves = dict(self.app.calc_results.get("valves") or {})
        block_valves = []
        for sm in (self.app.field_blocks[bi].get("submain_lines") or []):
            if not sm:
                continue
            k = str((round(sm[0][0], 2), round(sm[0][1], 2)))
            if k in valves:
                block_valves.append((k, valves[k]))
        txt.insert(tk.END, "\n--- Вузли/крани ---\n")
        if not block_valves:
            txt.insert(tk.END, "Немає вузлів з розрахованими значеннями.\n")
        else:
            for _k, v in block_valves:
                try:
                    txt.insert(tk.END, f"H={float(v.get('H', 0.0)):.2f} м, Q={float(v.get('Q', 0.0)):.2f} м3/г\n")
                except Exception:
                    txt.insert(tk.END, "Вузол: дані недоступні\n")

        items = self.app.orchestrator.last_bom.get("items", []) if hasattr(self.app, "orchestrator") else []
        b_items = [i for i in items if int(i.get("block_idx", 0)) == bi]
        txt.insert(tk.END, "\n--- BOM (блок) ---\n")
        if not b_items:
            txt.insert(tk.END, "Немає записів BOM для цього блоку.\n")
        else:
            for item in b_items:
                txt.insert(
                    tk.END,
                    f"{item['material']} PN{item['pn']} d{item['diameter']}: "
                    f"{item['quantity']} x {item['unit_length_m']}м = {item['quantized_length_m']}м\n",
                )
        txt.config(state=tk.DISABLED)

    def build_results_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Результати")
        self.tab_results = tab

        row = tk.Frame(tab, bg="#1e1e1e")
        row.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(
            row,
            text="Блок:",
            bg="#1e1e1e",
            fg="#FFD700",
            font=("Arial", 9, "bold"),
        ).pack(side=tk.LEFT)
        self.app.var_report_block = tk.StringVar(value="1")
        self.cb_report_block = ttk.Combobox(
            row,
            textvariable=self.app.var_report_block,
            values=[],
            state="readonly",
            width=6,
            font=("Consolas", 10, "bold"),
            justify="center",
        )
        self.cb_report_block.pack(side=tk.LEFT, padx=(6, 8))
        self.cb_report_block.bind("<<ComboboxSelected>>", lambda _e: self._render_block_report_text())

        btn_refresh_report = tk.Button(
            row,
            text="Оновити",
            command=self._render_block_report_text,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 9, "bold"),
            width=10,
        )
        btn_refresh_report.pack(side=tk.LEFT)
        self._attach_tooltip(
            btn_refresh_report,
            "Оновити текст звіту для обраного блоку з поточних результатів розрахунку.",
        )

        sc = tk.Scrollbar(tab)
        sc.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 10))
        self.txt_block_report = tk.Text(
            tab,
            bg="#222",
            fg="#00FFCC",
            font=("Consolas", 10),
            wrap=tk.WORD,
            yscrollcommand=sc.set,
        )
        self.txt_block_report.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        sc.config(command=self.txt_block_report.yview)
        self.sync_report_block_selector()
        self._render_block_report_text()

    def build_draw_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Керування")

        snap_lf = tk.LabelFrame(
            tab,
            text="Врізка: сабмейн ↔ латераль (підкрутити тут)",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 9, "bold"),
        )
        snap_lf.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(
            snap_lf,
            text="Допуск (м), якщо полілінії не перетинаються:",
            bg="#1e1e1e",
            fg="white",
            font=("Arial", 8),
            anchor=tk.W,
        ).pack(fill=tk.X, padx=6, pady=(6, 0))
        tk.Entry(
            snap_lf,
            textvariable=self.app.var_submain_lateral_snap_m,
            width=10,
            bg="#222",
            fg="white",
            font=("Consolas", 10, "bold"),
            insertbackground="white",
            insertwidth=2,
        ).pack(anchor=tk.W, padx=6, pady=(4, 2))
        tk.Label(
            snap_lf,
            text="Після зміни — перемальовування карти; повторіть «Розрахунок» для гідравліки.",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 7),
            wraplength=280,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=6, pady=(0, 6))

        tk.Label(tab, text="РЕЖИМ ДІЇ", bg="#1e1e1e", fg="#FFD700", font=("Arial", 10, "bold")).pack(pady=(10, 2))
        action_frame = tk.Frame(tab, bg="#1e1e1e")
        action_frame.pack(fill=tk.X, padx=10)
        tk.Radiobutton(action_frame, text="✍ ДОДАТИ", variable=self.app.action, value="ADD", indicatoron=0, bg="#2e4d46", fg="white", selectcolor="#00FFCC", width=14).pack(side=tk.LEFT, padx=2)
        tk.Radiobutton(action_frame, text="❌ ВИДАЛИТИ", variable=self.app.action, value="DEL", indicatoron=0, bg="#662222", fg="white", selectcolor="#FF3366", width=14).pack(side=tk.RIGHT, padx=2)

        tk.Label(tab, text="ОБ'ЄКТ", bg="#1e1e1e", fg="gray", font=("Arial", 9, "bold")).pack(pady=(15, 5))
        modes = [
            ("1. Контур поля", "DRAW"),
            ("2. Напрямок Driplines", "SET_DIR"),
            ("3. Submain / Manifold", "SUBMAIN"),
            ("4. Ручна Dripline", "DRAW_LAT"),
            ("5. Панорама (СКМ)", "PAN"),
            ("6. 📊 ІНФО (Графіки)", "INFO"),
            ("7. 📏 Лінійка", "RULER"),
            ("8. ✂ Лінія зрізу латералей", "CUT_LATS"),
            ("9. Підписи секцій сабмейну", "SUB_LABEL"),
            ("10. Тиск на тупику (латераль)", "LAT_TIP"),
        ]
        for text, m in modes:
            tk.Radiobutton(tab, text=text, variable=self.app.mode, value=m, indicatoron=0, bg="#333", fg="white", selectcolor="#00FFCC", width=28, command=self.app.reset_temp).pack(pady=2)

        tk.Label(tab, text="Контури поля (кілька блоків)", bg="#1e1e1e", fg="#aaaaaa", font=("Arial", 8)).pack(pady=(10, 2))
        bf = tk.Frame(tab, bg="#1e1e1e")
        bf.pack(fill=tk.X, padx=10, pady=2)
        btn_finish_blocks = tk.Button(
            bf,
            text="✓ Завершити блоки → напрямок рядів",
            command=self.app.proceed_to_set_direction,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 9, "bold"),
            width=28,
        )
        btn_finish_blocks.pack(pady=2)
        self._attach_tooltip(
            btn_finish_blocks,
            "Перейти до етапу напрямку рядів: після цього ЛКМ всередині блоку й два кліки напрямку.",
        )
        btn_clear_blocks = tk.Button(
            bf,
            text="🗑 Очистити всі контури поля",
            command=self.app.clear_all_field_blocks,
            bg="#662222",
            fg="white",
            font=("Arial", 9),
            width=28,
        )
        btn_clear_blocks.pack(pady=2)
        self._attach_tooltip(btn_clear_blocks, "Видалити всі контури блоків поля з полотна.")
        tk.Label(
            tab,
            text="До 100 блоків. Після «Завершити блоки» — ЛКМ всередині блоку, потім два ЛКМ напрямку рядів (авто-латералі лише цього блоку). "
            "Сабмейн/ручна лінія: спочатку ЛКМ всередині блоку; ПКМ ручної — обрізка/подовження до сабмейну. Розрахунок: кожен сабмейн має бути з'єднаний з латераллю. "
            "Видалення: блок — ЛКМ у контурі (ВИДАЛИТИ); латераль — поруч або режим 8. ПКМ замикає контур. Delete — усі контури. "
            "Скасувати незавершену чернетку (сабмейн, ручна лінія, контур, напрямок, різ, лінійка): Escape або подвійне ПКМ на полотні. "
            "ІНФО: ЛКМ по латералі — графік ряду; по сабмейну — тиск, витрата та рельєф вздовж магістралі (після розрахунку). "
            "Режим 10: ЛКМ по латералі — задати H на тупиках і побачити H біля врізки та сумарну витрату (без повного перерахунку мережі). "
            "Допуск врізки сабмейн–латераль — у рамці зверху цієї вкладки.",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(pady=(0, 8))

        tk.Checkbutton(tab, text="ОРТО (Space)", variable=self.app.ortho_on, bg="#1e1e1e", fg="#FFD700", font=("Arial", 9, "bold")).pack(pady=10)
        
        tk.Label(tab, text="Точна довжина лінії (м):", bg="#1e1e1e", fg="cyan", font=("Arial", 8)).pack()
        self.len_entry = tk.Entry(
            tab,
            bg="#222",
            fg="cyan",
            font=("Consolas", 12, "bold"),
            width=12,
            justify='center',
            insertbackground="white",
            insertwidth=2,
        )
        self.len_entry.pack(pady=2)
        self.len_entry.bind("<Return>", self.app.add_by_length)

        btn_close_poly = tk.Button(
            tab,
            text="ЗАМКНУТИ КОНТУР",
            command=self.app.close_polygon,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 9, "bold"),
            width=26,
        )
        btn_close_poly.pack(pady=15)
        self._attach_tooltip(
            btn_close_poly,
            "Замкнути поточний контур блоку (еквівалент ПКМ після останньої вершини).",
        )

    def build_block_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Блок")

        sec_disp = tk.LabelFrame(
            tab,
            text="Відображення авто-латералей на полотні",
            bg="#1e1e1e",
            fg="#00FFCC",
            font=("Arial", 9, "bold"),
        )
        sec_disp.pack(fill=tk.X, padx=10, pady=(12, 8))

        cb_kw = dict(
            bg="#1e1e1e",
            fg="#00FFCC",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="#00FFCC",
            font=("Arial", 9, "bold"),
        )

        def _row_chk(parent, bool_var, label, str_var, attr_name):
            f = tk.Frame(parent, bg="#1e1e1e")
            f.pack(fill=tk.X, padx=6, pady=3)
            tk.Checkbutton(f, variable=bool_var, **cb_kw).pack(side=tk.LEFT)
            tk.Label(f, text=label, bg="#1e1e1e", fg="white", font=("Arial", 9), wraplength=200, justify=tk.LEFT).pack(
                side=tk.LEFT, padx=(4, 0), anchor=tk.W
            )
            ent = tk.Entry(
                f,
                textvariable=str_var,
                width=6,
                bg="#333",
                fg="white",
                justify="center",
                font=("Consolas", 10),
                insertbackground="white",
                insertwidth=2,
            )
            ent.pack(side=tk.RIGHT)
            setattr(self, attr_name, ent)

        _row_chk(
            sec_disp,
            self.app.var_lat_disp_use_step,
            "Кожну N-ту (1 = усі):",
            self.app.var_lat_disp_step,
            "ent_lat_disp_step",
        )
        tk.Label(
            sec_disp,
            text="Нумерація окрема на кожному відрізку сабмейну (кран — перша точка цього відрізка). Латераль прив’язується до найближчого відрізка.",
            bg="#1e1e1e",
            fg="#999999",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.LEFT,
        ).pack(padx=8, anchor=tk.W)
        tk.Label(
            sec_disp,
            text="Без гідравлічного розрахунку на полотні всі авто-латералі. Після розрахунку — фільтр нижче окремо на кожен відрізок.",
            bg="#1e1e1e",
            fg="#88AA88",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.LEFT,
        ).pack(padx=8, anchor=tk.W)
        tk.Label(
            sec_disp,
            text="«Кожну N-ту» впливає лише на видимість авто-латералей. "
            "Якщо ввімкнути «від крана» або «з кінця», режим «кожну N-ту» вимикається автоматично.",
            bg="#1e1e1e",
            fg="#777777",
            font=("Arial", 8),
            wraplength=270,
            justify=tk.LEFT,
        ).pack(padx=8, anchor=tk.W)

        _row_chk(
            sec_disp,
            self.app.var_lat_disp_use_start,
            "Номер лінії від крана (1 = перша):",
            self.app.var_lat_disp_n_start,
            "ent_lat_disp_n_start",
        )
        tk.Label(
            sec_disp,
            text="Одна латераль з цим номером на кожному відрізку сабмейну. "
            "Працює лише коли «кожну N-ту» вимкнено.",
            bg="#1e1e1e",
            fg="#777777",
            font=("Arial", 8),
            wraplength=270,
            justify=tk.LEFT,
        ).pack(padx=8, anchor=tk.W)

        _row_chk(
            sec_disp,
            self.app.var_lat_disp_use_end,
            "Номер лінії з кінця (1 = остання):",
            self.app.var_lat_disp_n_end,
            "ent_lat_disp_n_end",
        )
        tk.Label(
            sec_disp,
            text="Одна латераль з кінця відрізка. Напр. 5 і 5 на двох гілках → 4 лінії на блок (по 2 на гілку). "
            "Працює лише коли «кожну N-ту» вимкнено.",
            bg="#1e1e1e",
            fg="#777777",
            font=("Arial", 8),
            wraplength=270,
            justify=tk.LEFT,
        ).pack(padx=8, anchor=tk.W)

        tk.Checkbutton(
            sec_disp,
            text="Діаграма виливу (ізолінії по діапазонах тиску)",
            variable=self.app.var_show_emitter_flow,
            bg="#1e1e1e",
            fg="#88DDFF",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="#88DDFF",
            font=("Arial", 9),
        ).pack(padx=8, pady=(4, 2), anchor=tk.W)
        tk.Label(
            sec_disp,
            text="Діаграма виливу автономна від фільтрів видимості ліній («кожну N-ту», «від крана», «з кінця»). "
            "Показуються ізолінії виливу (6 рівнів, IDW, спрощені контури) для трьох зон: норма, недолив, перелив. "
            "Якщо увімкнено «Маски переливу/недоливу на мапі», ізолінії на полотні приховані, щоб лишилися лише контури зон. "
            "За замовчуванням вимкнено (важке перемальовування); увімкніть за потреби. Розрахунок не скидається — лише перемальовування.",
            bg="#1e1e1e",
            fg="#777777",
            font=("Arial", 8),
            wraplength=270,
            justify=tk.LEFT,
        ).pack(padx=8, pady=(0, 6), anchor=tk.W)

        tk.Label(
            sec_disp,
            text="Ручні латералі завжди на полотні (товщіша лінія).",
            bg="#1e1e1e",
            fg="#C4A35A",
            font=("Arial", 9, "bold"),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(padx=8, pady=(8, 10))

        wrap_bad = tk.Frame(tab, bg="#1e1e1e")
        wrap_bad.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 8))
        cb_bad_col = tk.Frame(wrap_bad, bg="#1e1e1e")
        cb_bad_col.pack(side=tk.LEFT, padx=(0, 8), anchor=tk.N, pady=(4, 0))
        _cb_kw = dict(
            bg="#1e1e1e",
            fg="#CCCCCC",
            selectcolor="#333333",
            activebackground="#1e1e1e",
            activeforeground="#CCCCCC",
            font=("Arial", 8),
            justify=tk.LEFT,
        )
        tk.Checkbutton(
            cb_bad_col,
            text="Маски переливу / недоливу\nна мапі блоку (контури)",
            variable=self.app.var_show_press_zone_outlines_on_map,
            command=self.app.redraw,
            wraplength=118,
            **_cb_kw,
        ).pack(anchor=tk.W)
        sec_bad_emit = tk.LabelFrame(
            wrap_bad,
            text="Крапельниці поза діапазоном тиску (активний блок)",
            bg="#1e1e1e",
            fg="#FFAA66",
            font=("Arial", 9, "bold"),
        )
        sec_bad_emit.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            sec_bad_emit,
            text="Перелік емітерів з H мін/макс з «Гідравліка»; той самий допуск ±0,02 м, що в розрахунку. "
            "Нумерація латераля — як у глобальному списку (лат. 1, 2, …). "
            "На карті: лише контури масок переливу (червоний) та недоливу (жовтий), обрізка активним блоком. "
            "Поки маски увімкнені, ізолінії «Діаграма виливу» на полотні не малюються (вони будуються з точок емітера і виглядають як сітка крапельниць).",
            bg="#1e1e1e",
            fg="#999999",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.LEFT,
        ).pack(padx=6, pady=(4, 0), anchor=tk.W)
        self.txt_block_bad_emitters = scrolledtext.ScrolledText(
            sec_bad_emit,
            height=10,
            wrap=tk.WORD,
            bg="#252525",
            fg="#E8E0D5",
            font=("Consolas", 8),
            insertbackground="white",
            state=tk.DISABLED,
        )
        self.txt_block_bad_emitters.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        sec_more = tk.LabelFrame(tab, text="Далі", bg="#1e1e1e", fg="#666666", font=("Arial", 9, "bold"))
        sec_more.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 12))
        tk.Label(
            sec_more,
            text="Подальші секції параметрів блоку можна додати тут.",
            bg="#1e1e1e",
            fg="#555555",
            font=("Arial", 9),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(pady=24)

    def build_geo_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Геометрія")

        self.create_input(tab, "Крок ліній (м):", self.app.var_lat_step)
        self.create_input(tab, "Крок емітерів (м):", self.app.var_emit_step)
        
        tk.Frame(tab, bg="#333", height=2).pack(fill=tk.X, padx=10, pady=10)
        tk.Label(tab, text="РОЗБИВКА НА БЛОКИ", bg="#1e1e1e", fg="#FFD700", font=("Arial", 9, "bold")).pack()
        self.create_input(tab, "Макс. довж. лінії (м) [0=вимк]:", self.app.var_max_lat_len)
        self.create_input(tab, "Групувати по (шт) [0=вимк]:", self.app.var_lat_block_count)
        
        btn_regen_grid = tk.Button(
            tab,
            text="ОНОВИТИ СІТКУ",
            command=self.app.regenerate_grid,
            bg="#0066FF",
            fg="white",
            font=("Arial", 9, "bold"),
            width=20,
        )
        btn_regen_grid.pack(pady=20)
        self._attach_tooltip(
            btn_regen_grid,
            "Перебудувати авто-латералі та емітери за кроками та лімітами з цієї вкладки.",
        )

    def build_hydro_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Гідравліка")

        flow_row = tk.Frame(tab, bg="#1e1e1e")
        flow_row.pack(fill=tk.X, padx=10, pady=4)
        self.lbl_q_flow = tk.Label(
            flow_row,
            text="Q ном (л/год), √H/10м:",
            bg="#1e1e1e",
            fg="#00FFCC",
            font=("Arial", 9, "bold"),
            width=22,
            anchor=tk.W,
        )
        self.lbl_q_flow.pack(side=tk.LEFT)
        tk.Entry(
            flow_row,
            textvariable=self.app.var_emit_flow,
            bg="#333",
            fg="white",
            width=10,
            justify="center",
            insertbackground="white",
            insertwidth=2,
            font=("Consolas", 10, "bold"),
        ).pack(side=tk.RIGHT)

        dr_db_row = tk.Frame(tab, bg="#1e1e1e")
        dr_db_row.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(
            dr_db_row,
            text="Модель/номінал з бази:",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8, "bold"),
        ).pack(side=tk.LEFT)
        self.app.cb_emit_model = ttk.Combobox(
            dr_db_row,
            textvariable=self.app.var_emit_model,
            width=16,
            font=("Consolas", 9, "bold"),
            state="readonly",
            values=self.app._dripper_model_names(),
        )
        self.app.cb_emit_model.pack(side=tk.LEFT, padx=(6, 4))
        self.app.cb_emit_nominal = ttk.Combobox(
            dr_db_row,
            textvariable=self.app.var_emit_nominal_flow,
            width=6,
            font=("Consolas", 9, "bold"),
            state="readonly",
            values=self.app._dripper_nominal_values(self.app.var_emit_model.get()),
        )
        self.app.cb_emit_nominal.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(
            dr_db_row,
            text="л/год",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8),
        ).pack(side=tk.LEFT)
        if not self.app.var_emit_model.get() and self.app._dripper_model_names():
            self.app.var_emit_model.set(self.app._dripper_model_names()[0])
        self.app._on_emit_model_change()

        kx_row = tk.Frame(tab, bg="#1e1e1e")
        kx_row.pack(fill=tk.X, padx=10, pady=(0, 2))
        tk.Label(
            kx_row,
            text="k / x  (x=0 → компенс., q≈Q при H≥H мін):",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8),
        ).pack(side=tk.LEFT)
        tk.Entry(
            kx_row,
            textvariable=self.app.var_emit_k_coeff,
            bg="#333",
            fg="#88DDFF",
            width=8,
            justify="center",
            insertbackground="white",
            insertwidth=2,
            font=("Consolas", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(8, 4))
        tk.Entry(
            kx_row,
            textvariable=self.app.var_emit_x_exp,
            bg="#333",
            fg="#88DDFF",
            width=6,
            justify="center",
            insertbackground="white",
            insertwidth=2,
            font=("Consolas", 9, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            kx_row,
            text="kd:",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8),
        ).pack(side=tk.LEFT, padx=(10, 4))
        tk.Entry(
            kx_row,
            textvariable=self.app.var_emit_kd_coeff,
            bg="#333",
            fg="#88DDFF",
            width=6,
            justify="center",
            insertbackground="white",
            insertwidth=2,
            font=("Consolas", 9, "bold"),
        ).pack(side=tk.LEFT)

        def _sync_q_flow_label(*_a):
            if self.app._emitter_compensated_effective():
                self.lbl_q_flow.config(text="Q ном (л/год), компенс. (x=0):")
            else:
                self.lbl_q_flow.config(text="Q ном (л/год), √H/10м або k·H^x:")

        self.create_input(tab, "H мін компенс. (м вод. ст.):", self.app.var_emit_h_min)
        self.create_input(
            tab,
            "H опорна для Q ном (м вод. ст., турбул.):",
            self.app.var_emit_h_ref,
        )
        lat_db_row = tk.Frame(tab, bg="#1e1e1e")
        lat_db_row.pack(fill=tk.X, padx=10, pady=(0, 4))
        tk.Label(
            lat_db_row,
            text="Латераль з бази:",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 8, "bold"),
        ).pack(side=tk.LEFT, anchor=tk.N, pady=(4, 0))
        lat_combo_col = tk.Frame(lat_db_row, bg="#1e1e1e")
        lat_combo_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.app.cb_lat_model = ttk.Combobox(
            lat_combo_col,
            textvariable=self.app.var_lateral_model,
            width=36,
            font=("Consolas", 9, "bold"),
            state="readonly",
            values=self.app._lateral_model_names(),
        )
        self.app.cb_lat_model.pack(fill=tk.X)
        if not self.app.var_lateral_model.get() and self.app._lateral_model_names():
            self.app.var_lateral_model.set(self.app._lateral_model_names()[0])

        def _wire_lat_combo_hscroll():
            try:
                ep = str(self.app.root.tk.call("ttk::combobox::Entry", str(self.app.cb_lat_model)))
                ent = self.app.root.nametowidget(ep)
            except tk.TclError:
                return
            hsb = getattr(self.app, "_lat_model_combo_hsb", None)
            if hsb is not None:
                try:
                    hsb.destroy()
                except tk.TclError:
                    pass
            sb = tk.Scrollbar(
                lat_combo_col,
                orient=tk.HORIZONTAL,
                troughcolor="#2a2a2a",
                bg="#444444",
                highlightthickness=0,
            )
            ent.configure(xscrollcommand=sb.set)
            sb.config(command=ent.xview)
            sb.pack(fill=tk.X, pady=(2, 0))
            self.app._lat_model_combo_hsb = sb

        self.app.root.after_idle(_wire_lat_combo_hscroll)

        self.create_input(
            tab,
            "Внутр. діаметр латералі (мм):",
            self.app.var_lat_inner_d_mm,
        )
        self.create_input(
            tab,
            "Мін. тиск на крапельниці (м, 0=без перевірки):",
            self.app.var_emit_h_press_min,
        )
        self.create_input(
            tab,
            "Макс. тиск на крапельниці (м, 0=без перевірки):",
            self.app.var_emit_h_press_max,
        )
        self.app.var_emit_x_exp.trace_add("write", lambda *_: _sync_q_flow_label())
        _sync_q_flow_label()

        lat_sol_fr = tk.LabelFrame(
            tab,
            text="Розрахунок латералів (прямий HW)",
            bg="#1e1e1e",
            fg="#00FFCC",
            font=("Arial", 9, "bold"),
        )
        lat_sol_fr.pack(fill=tk.X, padx=10, pady=(8, 4))
        for txt, val in (
            ("Порівняти бісекцію та Ньютона", "compare"),
            ("Лише бісекція (shooting)", "bisection"),
            ("Лише Ньютон–Рафсон", "newton"),
        ):
            tk.Radiobutton(
                lat_sol_fr,
                text=txt,
                variable=self.app.var_lateral_solver_mode,
                value=val,
                bg="#1e1e1e",
                fg="white",
                selectcolor="#333",
                activebackground="#1e1e1e",
                activeforeground="#00FFCC",
                font=("Arial", 8),
                anchor=tk.W,
            ).pack(fill=tk.X, padx=8, pady=1)
        tk.Label(
            lat_sol_fr,
            text=(
                "У звіті: ітерації та (у режимі порівняння) макс. розбіжності ΔH_tip, ΔQ.\n"
                "Перемикання режиму не змінює вже зроблений розрахунок на карті й у звіті — "
                "новий варіант піде лише в наступний «▶ РОЗРАХУНОК» (активний блок)."
            ),
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 7),
            wraplength=260,
            justify=tk.LEFT,
        ).pack(padx=8, pady=(0, 6))
        
        sec_frame = tk.Frame(tab, bg="#1e1e1e")
        sec_frame.pack(fill=tk.X, padx=10, pady=5)
        cb_sec = tk.Checkbutton(sec_frame, text="Фіксована к-сть секцій:", variable=self.app.var_fixed_sec, bg="#1e1e1e", fg="#00FFCC", selectcolor="#333", activebackground="#1e1e1e", activeforeground="#00FFCC", font=("Arial", 9, "bold"), command=self.toggle_sec_entry)
        cb_sec.pack(side=tk.LEFT)
        self.ent_num_sec = tk.Entry(sec_frame, textvariable=self.app.var_num_sec, bg="#333", fg="white", width=5, justify='center', font=("Consolas", 10, "bold"))
        self.ent_num_sec.pack(side=tk.RIGHT)
        tk.Label(
            tab,
            text="Без галочки: довжина магістралі ділиться автоматично (не більше 5 секцій, кожна ≥ 6 м).",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(padx=10, pady=(0, 4))

        self.create_input(tab, "V мін (м/с):", self.app.var_v_min)
        self.create_input(tab, "V макс (м/с):", self.app.var_v_max)
        self.create_input(tab, "H на крані макс (м вод. ст., 0=вимк):", self.app.var_valve_h_max_m)
        tk.Label(
            tab,
            text="Явне обмеження розрахункового напору на вузлі крана; 0 — не перевіряти. Перевищення — у звіті та на мапі.",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(padx=10, pady=(0, 4))
        tk.Checkbutton(
            tab,
            text="Підбирати d сабмейну автоматично під H макс на крані",
            variable=self.app.var_valve_h_max_optimize,
            bg="#1e1e1e",
            fg="#00FFCC",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="#00FFCC",
            font=("Arial", 9),
        ).pack(padx=10, pady=(0, 6))

        tk.Frame(tab, bg="#333", height=2).pack(fill=tk.X, padx=10, pady=10)
        tk.Label(tab, text="ТРУБИ ДЛЯ БЛОКУ (АКТИВНОГО)", bg="#1e1e1e", fg="#FFD700", font=("Arial", 9, "bold")).pack()
        if not getattr(self.app, "_hidden_pipe_combo_ready", False):
            _hld = tk.Frame(self.app.root)
            self.app.cb_mat = ttk.Combobox(
                _hld,
                textvariable=self.app.pipe_material,
                width=10,
                font=("Consolas", 10, "bold"),
            )
            self.app.cb_pn = ttk.Combobox(
                _hld,
                textvariable=self.app.pipe_pn,
                width=5,
                font=("Consolas", 10, "bold"),
            )
            self.app._hidden_pipe_combo_ready = True
        self.app.lbl_hydro_pipe = tk.Label(
            tab,
            text="",
            bg="#1e1e1e",
            fg="#FFD700",
            font=("Consolas", 10, "bold"),
            wraplength=280,
            justify=tk.CENTER,
        )
        self.app.lbl_hydro_pipe.pack(padx=10, pady=(4, 2))
        tk.Label(
            tab,
            text="Сабмейн: у розрахунку всі відмічені в таблиці труби (мат/PN/Ø); набір блоку — params у JSON.",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(padx=10, pady=(0, 4))
        self.app.lbl_hydro_submain_geom = tk.Label(
            tab,
            text="",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Consolas", 9, "bold"),
            wraplength=280,
            justify=tk.CENTER,
        )
        self.app.lbl_hydro_submain_geom.pack(padx=10, pady=(0, 2))
        self.app.lbl_hydro_submain_calc = tk.Label(
            tab,
            text="",
            bg="#1e1e1e",
            fg="#AAAAAA",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        )
        self.app.lbl_hydro_submain_calc.pack(padx=10, pady=(0, 6))
        btn_pipe_sel = tk.Button(
            tab,
            text="✅ Дозволені труби для блоку…",
            command=lambda: self.app.open_pipe_selector("block"),
            bg="#2e4d46",
            fg="white",
            font=("Arial", 9, "bold"),
            width=28,
        )
        btn_pipe_sel.pack(pady=(0, 8))
        self._attach_tooltip(
            btn_pipe_sel,
            "Відкрити діалог: які труби (матеріал/PN/Ø) дозволені для активного блоку в гідравліці.",
        )

        tk.Frame(tab, bg="#333", height=2).pack(fill=tk.X, padx=10, pady=10)
        tk.Label(tab, text="РОЗРАХУНОК ПО БЛОКАХ", bg="#1e1e1e", fg="#FFD700", font=("Arial", 9, "bold")).pack()
        clr_row = tk.Frame(tab, bg="#1e1e1e")
        clr_row.pack(fill=tk.X, padx=10, pady=6)
        btn_clear_hydro = tk.Button(
            clr_row,
            text="Скинути",
            command=self.app.clear_hydro_block,
            bg="#662222",
            fg="white",
            font=("Arial", 9, "bold"),
            width=10,
        )
        btn_clear_hydro.pack(side=tk.LEFT, padx=(0, 6))
        self.cb_hydro_clear_block = ttk.Combobox(
            clr_row,
            textvariable=self.app.var_hydro_clear_block,
            values=[],
            state="readonly",
            width=6,
            font=("Consolas", 10, "bold"),
            justify="center",
        )
        self.cb_hydro_clear_block.pack(side=tk.LEFT, padx=2)
        self._attach_tooltip(btn_clear_hydro, "Скинути гідравлічний розрахунок лише для вибраного номера блоку.")
        self.sync_hydro_clear_block_selector()

        tk.Label(
            tab,
            text="Підписи секцій: у «Керуванні» режим 9 — ЛКМ біля потрібної ділянки сабмейну. Однакові d/PN на одному сабмейні зливаються; L у підписі — сума.",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(padx=10, pady=(4, 6))

        btn_run_calc = tk.Button(
            tab,
            text="▶ РОЗРАХУНОК",
            command=self.app.run_calculation,
            bg="#FF3366",
            fg="white",
            font=("Arial", 11, "bold"),
            width=20,
            height=2,
        )
        btn_run_calc.pack(pady=12)
        self._attach_tooltip(
            btn_run_calc,
            "Запустити гідравлічний розрахунок мережі по всіх блоках з поточними параметрами.",
        )
        btn_stress = tk.Button(
            tab,
            text="⚙ Stress-тест (після основного розрахунку)",
            command=self.app.run_stress_calculation,
            bg="#444466",
            fg="#FFCC66",
            font=("Arial", 9, "bold"),
            width=28,
        )
        btn_stress.pack(pady=(0, 14))
        self._attach_tooltip(
            btn_stress,
            "Додатковий сценарій навантаження після основного розрахунку (перевірка запасу).",
        )

        self.app.sync_hydro_pipe_summary()

    def _on_notebook_tab_changed(self, event=None):
        try:
            if self.notebook.select() == str(getattr(self, "tab_schedule", "")):
                self._sync_schedule_editor()
                self._render_consumer_schedule_text()
        except tk.TclError:
            pass

    def _sync_schedule_editor(self):
        if not hasattr(self.app, "normalize_consumer_schedule"):
            return
        self.app.normalize_consumer_schedule()
        lb = getattr(self, "lb_sched_consumers", None)
        if lb is not None:
            lb.delete(0, tk.END)
            row_ids = []
            for i, node in enumerate(getattr(self.app, "trunk_map_nodes", []) or []):
                if str(node.get("kind", "")).lower() not in ("consumption", "valve"):
                    continue
                main, sub = self.app.trunk_consumer_caption_lines(node, i)
                line = f"{main}  ·  {sub}" if sub else main
                lb.insert(tk.END, line)
                row_ids.append(i)
            self._schedule_consumer_row_ids = row_ids
        self._sync_irrigation_overview_listbox()

    def _sync_irrigation_overview_listbox(self):
        lb = getattr(self, "lb_irrigation_slots", None)
        if lb is None:
            return
        self.app.normalize_consumer_schedule()
        slots = self.app.consumer_schedule.get("irrigation_slots") or [[] for _ in range(48)]
        lb.delete(0, tk.END)
        slot_for_row: list = []
        for i in range(48):
            ids = slots[i] if i < len(slots) else []
            if not ids:
                continue
            ncons = len(ids)
            qsum = ncons * 60
            lb.insert(
                tk.END,
                f"{i + 1:02d}: {', '.join(ids)}   │ ΣQ≈{qsum} м³/год · H≈40 м (тест)",
            )
            slot_for_row.append(i)
        self._irrigation_lb_slot_indices = slot_for_row
        try:
            cur = int(str(self.var_irrigation_slot.get()).strip())
        except ValueError:
            cur = 1
        cur = max(1, min(48, cur))
        lb.selection_clear(0, tk.END)
        if slot_for_row:
            try:
                row_idx = slot_for_row.index(cur - 1)
            except ValueError:
                row_idx = 0
            lb.selection_set(row_idx)
            lb.activate(row_idx)
            lb.see(row_idx)

    def _schedule_on_irrigation_combo(self, event=None):
        lb = getattr(self, "lb_irrigation_slots", None)
        if lb is None:
            return
        try:
            n = int(str(self.var_irrigation_slot.get()).strip())
        except ValueError:
            return
        i = max(0, min(47, n - 1))
        slot_for_row = getattr(self, "_irrigation_lb_slot_indices", []) or []
        lb.selection_clear(0, tk.END)
        if not slot_for_row:
            return
        try:
            row_idx = slot_for_row.index(i)
        except ValueError:
            return
        lb.selection_set(row_idx)
        lb.activate(row_idx)
        lb.see(row_idx)

    def _schedule_on_slot_list_select(self, event=None):
        lb = getattr(self, "lb_irrigation_slots", None)
        if lb is None:
            return
        sel = lb.curselection()
        if not sel:
            return
        row = int(sel[0])
        slot_for_row = getattr(self, "_irrigation_lb_slot_indices", []) or []
        if row < 0 or row >= len(slot_for_row):
            return
        self.var_irrigation_slot.set(str(int(slot_for_row[row]) + 1))

    def _schedule_clear_staging(self):
        self.app._rozklad_staging_ids = []
        self.app.redraw()
        try:
            self.app._schedule_embedded_map_overlay_refresh()
        except Exception:
            pass

    def _schedule_clear_current_irrigation_slot(self):
        try:
            n = int(str(self.var_irrigation_slot.get()).strip())
        except ValueError:
            silent_showwarning(self.app.root, "Розклад", "Оберіть номер поливу (1–48).")
            return
        if hasattr(self.app, "clear_irrigation_slot"):
            self.app.clear_irrigation_slot(n)

    def _schedule_clear_all_irrigation_slots(self):
        if not silent_askyesno(self.app.root, 
            "Розклад",
            "Очистити всі 48 слотів поливу? Список споживачів у кожному слоті буде видалено.",
        ):
            return
        if hasattr(self.app, "clear_all_irrigation_slots"):
            self.app.clear_all_irrigation_slots()

    def _schedule_run_trunk_hydro(self):
        if hasattr(self.app, "run_trunk_irrigation_schedule_hydro"):
            self.app.run_trunk_irrigation_schedule_hydro()

    def _schedule_apply_max_pump_head_from_entry(self) -> bool:
        """Зчитує поле «макс. напір насоса» у consumer_schedule. False — некоректне число."""
        self.app.normalize_consumer_schedule()
        try:
            v = float(str(self.var_schedule_max_pump_head_m.get()).replace(",", "."))
        except (TypeError, ValueError, tk.TclError):
            silent_showwarning(self.app.root, 
                "Розклад",
                "Напір насоса (задано): введіть додатне число (метри водяного стовпа).",
            )
            return False
        if v <= 0.0 or v > 400.0:
            silent_showwarning(self.app.root, 
                "Розклад",
                "Напір насоса поза діапазоном: дозволено від 1 до 400 м вод. ст.",
            )
            return False
        v = max(1.0, min(400.0, float(v)))
        if abs(v - round(v)) < 1e-6:
            disp = str(int(round(v)))
        else:
            disp = f"{v:.2f}".rstrip("0").rstrip(".")
        self.var_schedule_max_pump_head_m.set(disp)
        self.app.consumer_schedule["max_pump_head_m"] = float(v)
        return True

    def _sync_schedule_max_pump_head_ui(self) -> None:
        if not hasattr(self, "var_schedule_max_pump_head_m"):
            return
        self.app.normalize_consumer_schedule()
        mph = self.app.consumer_schedule.get("max_pump_head_m", 50.0)
        try:
            mph = float(mph)
        except (TypeError, ValueError):
            mph = 100.0
        mph = max(1.0, min(400.0, mph))
        if abs(mph - round(mph)) < 1e-6:
            self.var_schedule_max_pump_head_m.set(str(int(round(mph))))
        else:
            self.var_schedule_max_pump_head_m.set(f"{mph:.2f}".rstrip("0").rstrip("."))

    def _flush_schedule_max_pump_head_to_app(self) -> None:
        """Перед збереженням проєкту: перенести напір з поля в consumer_schedule (без діалогів)."""
        if not hasattr(self, "var_schedule_max_pump_head_m"):
            return
        self.app.normalize_consumer_schedule()
        try:
            v = float(str(self.var_schedule_max_pump_head_m.get()).replace(",", "."))
        except (TypeError, ValueError, tk.TclError):
            self._sync_schedule_max_pump_head_ui()
            return
        v = max(1.0, min(400.0, float(v)))
        self.app.consumer_schedule["max_pump_head_m"] = float(v)
        if abs(v - round(v)) < 1e-6:
            self.var_schedule_max_pump_head_m.set(str(int(round(v))))
        else:
            self.var_schedule_max_pump_head_m.set(f"{v:.2f}".rstrip("0").rstrip("."))

    def _schedule_apply_trunk_v_max_from_entry(self) -> bool:
        """Макс. швидкість у магістралі (м/с) для розкладу та автопідбору d. False — некоректне число."""
        self.app.normalize_consumer_schedule()
        try:
            v = float(str(self.var_schedule_trunk_v_max_mps.get()).replace(",", "."))
        except (TypeError, ValueError, tk.TclError):
            silent_showwarning(self.app.root, 
                "Розклад",
                "v max у магістралі: введіть додатне число (м/с), наприклад 2.0.",
            )
            return False
        if v < 0.2 or v > 8.0:
            silent_showwarning(self.app.root, 
                "Розклад",
                "v max поза діапазоном: дозволено від 0.2 до 8.0 м/с.",
            )
            return False
        v = max(0.2, min(8.0, float(v)))
        if abs(v - round(v)) < 1e-6:
            disp = str(int(round(v)))
        else:
            disp = f"{v:.2f}".rstrip("0").rstrip(".")
        self.var_schedule_trunk_v_max_mps.set(disp)
        self.app.consumer_schedule["trunk_schedule_v_max_mps"] = float(v)
        return True

    def _sync_schedule_trunk_v_max_ui(self) -> None:
        if not hasattr(self, "var_schedule_trunk_v_max_mps"):
            return
        self.app.normalize_consumer_schedule()
        vx = self.app.consumer_schedule.get("trunk_schedule_v_max_mps", 2.0)
        try:
            vx = float(vx)
        except (TypeError, ValueError):
            vx = 2.0
        vx = max(0.2, min(8.0, vx))
        if abs(vx - round(vx)) < 1e-6:
            self.var_schedule_trunk_v_max_mps.set(str(int(round(vx))))
        else:
            self.var_schedule_trunk_v_max_mps.set(f"{vx:.2f}".rstrip("0").rstrip("."))

    def _flush_schedule_trunk_v_max_to_app(self) -> None:
        """Перед збереженням: перенести v max з поля в consumer_schedule (без діалогів)."""
        if not hasattr(self, "var_schedule_trunk_v_max_mps"):
            return
        self.app.normalize_consumer_schedule()
        try:
            v = float(str(self.var_schedule_trunk_v_max_mps.get()).replace(",", "."))
        except (TypeError, ValueError, tk.TclError):
            self._sync_schedule_trunk_v_max_ui()
            return
        v = max(0.2, min(8.0, float(v)))
        self.app.consumer_schedule["trunk_schedule_v_max_mps"] = float(v)
        if abs(v - round(v)) < 1e-6:
            self.var_schedule_trunk_v_max_mps.set(str(int(round(v))))
        else:
            self.var_schedule_trunk_v_max_mps.set(f"{v:.2f}".rstrip("0").rstrip("."))

    def _schedule_on_consumer_select(self, event=None):
        lb = getattr(self, "lb_sched_consumers", None)
        if lb is None:
            return
        sel = lb.curselection()
        if not sel:
            return
        ix = int(sel[0])
        ids = getattr(self, "_schedule_consumer_row_ids", [])
        if ix >= len(ids):
            return
        idx = ids[ix]
        node = self.app.trunk_map_nodes[idx]
        self.var_schedule_caption.set(str(node.get("schedule_label", "")))

    def _schedule_apply_caption(self):
        lb = getattr(self, "lb_sched_consumers", None)
        if lb is None:
            return
        sel = lb.curselection()
        if not sel:
            silent_showinfo(self.app.root, "Розклад", "Оберіть споживача у списку.")
            return
        ix = int(sel[0])
        ids = getattr(self, "_schedule_consumer_row_ids", [])
        if ix >= len(ids):
            return
        idx = ids[ix]
        node = self.app.trunk_map_nodes[idx]
        nid = str(node.get("id", "")).strip()
        if not nid:
            return
        self.app.apply_trunk_consumer_schedule_label(nid, self.var_schedule_caption.get())
        self._sync_schedule_editor()
        self._render_consumer_schedule_text()

    def _schedule_autonumber_consumers(self):
        if hasattr(self.app, "autonumber_trunk_consumer_labels"):
            self.app.autonumber_trunk_consumer_labels()
        self._sync_schedule_editor()
        self._render_consumer_schedule_text()

    def _render_consumer_schedule_text(self):
        txt = getattr(self, "txt_consumer_schedule", None)
        if txt is None:
            return
        txt.config(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        app = self.app
        nodes = list(getattr(app, "trunk_map_nodes", []) or [])
        segs = list(getattr(app, "trunk_map_segments", []) or [])
        if not nodes:
            txt.insert(
                tk.END,
                "Вузлів магістралі немає. На полотні «Без карти» або на вкладці «Карта» задайте "
                "насос, трасу та вузли споживання (інструменти магістралі).\n",
            )
            txt.config(state=tk.DISABLED)
            return

        consumers = [
            (i, n)
            for i, n in enumerate(nodes)
            if str(n.get("kind", "")).lower() in ("consumption", "valve")
        ]
        topo = app._trunk_topology_oriented()
        if topo is None:
            from modules.hydraulic_module.trunk_map_graph import validate_trunk_map_graph

            txt.insert(tk.END, "Топологія магістралі не є коректним деревом від насоса — схему включень не зібрано.\n\n")
            errs = validate_trunk_map_graph(nodes, segs, complete_only=False)
            if errs:
                txt.insert(tk.END, "\n".join(errs[:14]))
                if len(errs) > 14:
                    txt.insert(tk.END, f"\n… ще {len(errs) - 14} повідомлень.")
            else:
                txt.insert(tk.END, "Перевірте: один витік, з’єднані сегменти, споживачі — лише листя.")
            txt.config(state=tk.DISABLED)
            return

        if not consumers:
            txt.insert(
                tk.END,
                "Вузлів «Споживач» (consumption) на магістралі ще немає.\n\n"
                "Після додавання споживачів тут з’явиться шлях від насоса до кожного з них "
                "(схема включень / розклад гілок).\n",
            )
            txt.config(state=tk.DISABLED)
            return

        if hasattr(app, "normalize_consumer_schedule"):
            app.normalize_consumer_schedule()
            slots = app.consumer_schedule.get("irrigation_slots") or [[] for _ in range(48)]
            txt.insert(tk.END, "— Розклад поливів (1…48), тест: 60 м³/год і 40 м на споживача —\n")
            any_slot = any(bool(slots[i]) for i in range(min(48, len(slots))))
            if not any_slot:
                txt.insert(
                    tk.END,
                    "Слоти порожні. Вкладка «Розклад»: оберіть № поливу, ЛКМ по споживачах на полотні "
                    "(VIEW/PAN), ПКМ — записати у слот.\n\n",
                )
            else:
                for si in range(min(48, len(slots))):
                    ids = slots[si] or []
                    if not ids:
                        continue
                    n = len(ids)
                    txt.insert(
                        tk.END,
                        f"Полив {si + 1:02d}: {', '.join(ids)}  "
                        f"(ΣQ≈{n * 60} м³/год, тест)\n",
                    )
                txt.insert(tk.END, "\n")

        txt.insert(tk.END, "— Шлях від насоса до кожного споживача —\n\n")
        for ci, (idx, node) in enumerate(consumers, start=1):
            path = app._trunk_path_indices_to_source(topo, idx)
            cap = app.trunk_consumer_display_caption(node, idx)
            q_note = ""
            try:
                qm = node.get("q_demand_m3s")
                if qm is not None:
                    qf = float(qm)
                    if abs(qf) > 1e-12:
                        q_note = f"  Q ≈ {qf * 3600.0:.2f} м³/год\n"
            except (TypeError, ValueError):
                pass
            txt.insert(tk.END, f"{ci}. {cap}\n{q_note}")
            if not path:
                txt.insert(tk.END, "   (немає шляху до насоса — перевірте топологію)\n\n")
                continue
            labels = []
            for j in reversed(path):
                if 0 <= j < len(nodes):
                    labels.append(app.trunk_consumer_display_caption(nodes[j], j))
            txt.insert(tk.END, "   " + " → ".join(labels) + "\n\n")
        txt.config(state=tk.DISABLED)

    def build_schedule_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Розклад")
        self.tab_schedule = tab

        tk.Label(
            tab,
            text="Схема включень споживачів",
            bg="#1e1e1e",
            fg="#FFD700",
            font=("Arial", 10, "bold"),
        ).pack(pady=(10, 4))
        tk.Label(
            tab,
            text="Тест: кожен споживач 60 м³/год при H≈40 м. Полотно/карта: режим VIEW або PAN, без інструментів "
            "магістралі. ЛКМ — у чернетку (повтор — зняти), ПКМ — записати у вибраний полив.",
            bg="#1e1e1e",
            fg="#aaaaaa",
            font=("Arial", 8),
            wraplength=300,
            justify=tk.LEFT,
        ).pack(padx=10, pady=(0, 6), anchor=tk.W)

        lf_ir = tk.LabelFrame(
            tab,
            text="Формування поливів",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 9, "bold"),
        )
        lf_ir.pack(fill=tk.BOTH, expand=False, padx=8, pady=(0, 6))
        ir_row = tk.Frame(lf_ir, bg="#1e1e1e")
        ir_row.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        left_ir = tk.Frame(ir_row, bg="#1e1e1e", width=72)
        left_ir.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        tk.Label(left_ir, text="№ поливу", bg="#1e1e1e", fg="#CCCCCC", font=("Arial", 8)).pack(anchor=tk.W)
        self.var_irrigation_slot = tk.StringVar(value="1")
        self.cb_irrigation = ttk.Combobox(
            left_ir,
            textvariable=self.var_irrigation_slot,
            values=[str(i) for i in range(1, 49)],
            state="readonly",
            width=5,
            font=("Consolas", 10, "bold"),
        )
        self.cb_irrigation.pack(anchor=tk.W, pady=(2, 4))
        self.cb_irrigation.bind("<<ComboboxSelected>>", self._schedule_on_irrigation_combo)
        tk.Button(
            left_ir,
            text="Скинути\nчернетку",
            command=self._schedule_clear_staging,
            bg="#444",
            fg="white",
            font=("Arial", 8),
        ).pack(anchor=tk.W, pady=(4, 0))
        btn_clr_one = tk.Button(
            left_ir,
            text="Очистити\nполив №",
            command=self._schedule_clear_current_irrigation_slot,
            bg="#5c4a2a",
            fg="white",
            font=("Arial", 8),
        )
        btn_clr_one.pack(anchor=tk.W, pady=(6, 0))
        self._attach_tooltip(
            btn_clr_one,
            "Очистити лише обраний у дроплісті номер поливу (список споживачів у цьому слоті).",
        )
        btn_clr_all = tk.Button(
            left_ir,
            text="Очистити\nвсі поливи",
            command=self._schedule_clear_all_irrigation_slots,
            bg="#662222",
            fg="white",
            font=("Arial", 8),
        )
        btn_clr_all.pack(anchor=tk.W, pady=(4, 0))
        self._attach_tooltip(
            btn_clr_all,
            "Видалити споживачів з усіх слотів 1…48 (підтвердження). Кольорові кільця на схемі зникнуть.",
        )
        btn_tr_hydro = tk.Button(
            left_ir,
            text="Розрахунок\nмагістралі",
            command=self._schedule_run_trunk_hydro,
            bg="#1565C0",
            fg="white",
            font=("Arial", 8, "bold"),
        )
        btn_tr_hydro.pack(anchor=tk.W, pady=(8, 0))
        self._attach_tooltip(
            btn_tr_hydro,
            "HW по поливах: 60 м³/год і H≥40 м у споживача; напір на насосі — той, що ви задаєте "
            "в полі нижче (реальна робоча точка, без автоматичного «завищення» до 100 м). "
            "Фарбування відрізків — домінантний полив; підпис біля насоса.",
        )
        fr_pump_h = tk.Frame(left_ir, bg="#1e1e1e")
        fr_pump_h.pack(anchor=tk.W, pady=(10, 0), fill=tk.X)
        tk.Label(
            fr_pump_h,
            text="Напір насоса\n(задано, м вод. ст.)",
            bg="#1e1e1e",
            fg="#CCCCCC",
            font=("Arial", 8),
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        self.var_schedule_max_pump_head_m = tk.StringVar(value="50")
        ent_max_pump = tk.Entry(
            fr_pump_h,
            textvariable=self.var_schedule_max_pump_head_m,
            width=8,
            bg="#333333",
            fg="#ECEFF1",
            font=("Consolas", 10, "bold"),
            insertbackground="white",
        )
        ent_max_pump.pack(anchor=tk.W, pady=(2, 0))
        self._attach_tooltip(
            ent_max_pump,
            "Робочий напір насоса (м вод. ст.), під який перевіряється магістраль. "
            "Менше значення — менший тиск у трубах (м’якші вимоги до класу PN). "
            "Якщо при цьому H у споживачів < 40 м — у звіті буде підказка мінімально потрібного H. "
            "Зберігається в проєкті.",
        )
        fr_vmax = tk.Frame(left_ir, bg="#1e1e1e")
        fr_vmax.pack(anchor=tk.W, pady=(8, 0), fill=tk.X)
        tk.Label(
            fr_vmax,
            text="v max у магістралі\n(м/с, для перевірки та автопідбору d)",
            bg="#1e1e1e",
            fg="#CCCCCC",
            font=("Arial", 8),
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        self.var_schedule_trunk_v_max_mps = tk.StringVar(value="2")
        ent_vmax = tk.Entry(
            fr_vmax,
            textvariable=self.var_schedule_trunk_v_max_mps,
            width=8,
            bg="#333333",
            fg="#ECEFF1",
            font=("Consolas", 10, "bold"),
            insertbackground="white",
        )
        ent_vmax.pack(anchor=tk.W, pady=(2, 0))
        self._attach_tooltip(
            ent_vmax,
            "Верхня межа швидкості потоку в трубах магістралі. Після першого розрахунку за поливами "
            "діаметри на ребрах дерева підбираються автоматично з дозволених труб (trunk → allowed_pipes) "
            "так, щоб при максимальній витраті по поливах було v ≤ цього значення. Зберігається в проєкті.",
        )
        self._attach_tooltip(
            self.cb_irrigation,
            "Номер поливу (1–48), у який запишуться споживачі з чернетки після ПКМ на полотні.",
        )

        right_ir = tk.Frame(ir_row, bg="#1e1e1e")
        right_ir.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            right_ir,
            text="Заповнені поливи (порожні слоти тут не показуються; номер — у дроплісті зліва)",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=210,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        ir_lb_fr = tk.Frame(right_ir, bg="#1e1e1e")
        ir_lb_fr.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        ir_sc = tk.Scrollbar(ir_lb_fr)
        ir_sc.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb_irrigation_slots = tk.Listbox(
            ir_lb_fr,
            height=12,
            bg="#222",
            fg="#ECEFF1",
            selectmode=tk.BROWSE,
            exportselection=0,
            font=("Consolas", 8),
            highlightthickness=0,
            yscrollcommand=ir_sc.set,
        )
        self.lb_irrigation_slots.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ir_sc.config(command=self.lb_irrigation_slots.yview)
        self.lb_irrigation_slots.bind("<<ListboxSelect>>", self._schedule_on_slot_list_select)

        lf_leg = tk.LabelFrame(
            tab,
            text="Легенда: колір лінії магістралі = домінантний полив",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 9, "bold"),
        )
        lf_leg.pack(fill=tk.X, padx=8, pady=(0, 6))
        tk.Label(
            lf_leg,
            text="Лише заповнені поливи: кожен номер — свій колір (як кільця біля споживачів). d і Q при наведенні "
            "на відрізок після «Розрахунок магістралі».",
            bg="#1e1e1e",
            fg="#999999",
            font=("Arial", 8),
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=6, pady=(4, 2))
        self.canvas_irrigation_legend = tk.Canvas(
            lf_leg,
            width=268,
            height=120,
            bg="#222222",
            highlightthickness=1,
            highlightbackground="#444444",
        )
        self.canvas_irrigation_legend.pack(padx=6, pady=(0, 4))
        self.lbl_trunk_pipe_legend = tk.Label(
            lf_leg,
            text="",
            bg="#1e1e1e",
            fg="#9DCBFA",
            font=("Consolas", 8),
            justify=tk.LEFT,
            wraplength=300,
        )
        self.lbl_trunk_pipe_legend.pack(anchor=tk.W, padx=6, pady=(0, 6))

        lf_cap = tk.LabelFrame(
            tab,
            text="Підпис на схемі",
            bg="#1e1e1e",
            fg="#88DDFF",
            font=("Arial", 9, "bold"),
        )
        lf_cap.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.lb_sched_consumers = tk.Listbox(
            lf_cap,
            height=4,
            bg="#222",
            fg="#ECEFF1",
            selectmode=tk.EXTENDED,
            exportselection=0,
            font=("Consolas", 9),
            highlightthickness=0,
        )
        self.lb_sched_consumers.pack(fill=tk.X, padx=6, pady=4)
        self.lb_sched_consumers.bind("<<ListboxSelect>>", self._schedule_on_consumer_select)
        self.var_schedule_caption = tk.StringVar(value="")
        tk.Entry(
            lf_cap,
            textvariable=self.var_schedule_caption,
            bg="#333",
            fg="white",
            font=("Consolas", 10),
            insertbackground="white",
        ).pack(fill=tk.X, padx=6, pady=(0, 4))
        row_cap = tk.Frame(lf_cap, bg="#1e1e1e")
        row_cap.pack(fill=tk.X, padx=6, pady=(0, 6))
        tk.Button(
            row_cap,
            text="Застосувати підпис",
            command=self._schedule_apply_caption,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 8, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 4))
        btn_autonum = tk.Button(
            row_cap,
            text="С1…Сn (порожні)",
            command=self._schedule_autonumber_consumers,
            bg="#3d4f5c",
            fg="white",
            font=("Arial", 8, "bold"),
        )
        btn_autonum.pack(side=tk.LEFT)
        self._attach_tooltip(
            btn_autonum,
            "Для споживачів без власного підпису задати С1, С2, … по порядку на магістралі.",
        )

        tk.Label(tab, text="Текст схеми (шляхи)", bg="#1e1e1e", fg="#666", font=("Arial", 8)).pack(
            anchor=tk.W, padx=10
        )
        row_rf = tk.Frame(tab, bg="#1e1e1e")
        row_rf.pack(fill=tk.X, padx=10, pady=(0, 4))
        btn_sched_txt = tk.Button(
            row_rf,
            text="Оновити текст",
            command=self._render_consumer_schedule_text,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 8, "bold"),
        )
        btn_sched_txt.pack(side=tk.LEFT)
        self._attach_tooltip(
            btn_sched_txt,
            "Оновити нижній звіт (групи та шляхи від насоса) без зміни списків вище.",
        )

        report_box = tk.Frame(tab, bg="#1e1e1e")
        report_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        sc = tk.Scrollbar(report_box)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_consumer_schedule = tk.Text(
            report_box,
            bg="#222",
            fg="#00FFCC",
            font=("Consolas", 9),
            wrap=tk.WORD,
            yscrollcommand=sc.set,
        )
        self.txt_consumer_schedule.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc.config(command=self.txt_consumer_schedule.yview)

        self._schedule_consumer_row_ids = []
        self._sync_schedule_editor()
        self._schedule_on_irrigation_combo()
        self._render_consumer_schedule_text()
        self._sync_irrigation_legend()
        self._sync_schedule_max_pump_head_ui()
        self._sync_schedule_trunk_v_max_ui()

    def _draw_irrigation_slot_legend(self) -> None:
        c = getattr(self, "canvas_irrigation_legend", None)
        if c is None:
            return
        c.delete("all")
        app = self.app
        if not hasattr(app, "irrigation_slot_color_hex"):
            return
        app.normalize_consumer_schedule()
        slots = app.consumer_schedule.get("irrigation_slots") or []
        used = [i for i in range(min(48, len(slots))) if slots[i]]
        if not used:
            c.create_text(
                134,
                55,
                text="Немає заповнених поливів",
                fill="#888888",
                font=("Segoe UI", 9),
            )
            try:
                c.config(height=96)
            except tk.TclError:
                pass
            return
        ncol = 8
        nrows = (len(used) + ncol - 1) // ncol
        try:
            c.config(height=min(220, max(72, 8 + nrows * 22)))
        except tk.TclError:
            pass
        for k, si in enumerate(used):
            row, col = divmod(k, ncol)
            x0 = col * 32 + 6
            y0 = row * 22 + 6
            colh = app.irrigation_slot_color_hex(si)
            c.create_rectangle(x0, y0, x0 + 24, y0 + 10, fill=colh, outline="#555555", width=1)
            c.create_text(
                x0 + 12,
                y0 + 21,
                text=str(si + 1),
                fill="#b0b0b0",
                font=("Consolas", 7),
            )

    def _sync_irrigation_legend(self) -> None:
        self._draw_irrigation_slot_legend()
        lbl = getattr(self, "lbl_trunk_pipe_legend", None)
        if lbl is None:
            return
        app = self.app
        h = getattr(app, "trunk_irrigation_hydro_cache", None)
        if not isinstance(h, dict):
            lbl.config(
                text="Труби на магістралі (позначення з каталогу: матеріал PN Ø): після «Розрахунок магістралі за поливами»."
            )
            return
        sh = h.get("segment_hover")
        if not isinstance(sh, dict) or not sh:
            lbl.config(text="")
            return
        labels: dict = {}
        for row in sh.values():
            if not isinstance(row, dict):
                continue
            try:
                d = float(row.get("d_inner_mm", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if hasattr(app, "trunk_pipe_label_for_inner_mm"):
                lab = app.trunk_pipe_label_for_inner_mm(d)
            else:
                lab = f"d={int(round(d))} мм"
            labels[lab] = labels.get(lab, 0) + 1
        if not labels:
            lbl.config(text="")
            return
        parts = [f"{lab} — {n} відр." for lab, n in sorted(labels.items(), key=lambda t: t[0])]
        text = "Труби на схемі: " + "; ".join(parts)
        lim = h.get("limits") if isinstance(h.get("limits"), dict) else {}
        try:
            vmax = float(lim.get("max_pipe_velocity_mps", 0.0) or 0.0)
            hp = float(lim.get("pump_operating_head_m", lim.get("max_pump_head_m", 0.0)) or 0.0)
        except (TypeError, ValueError):
            vmax, hp = 0.0, 0.0
        lim_bits = []
        if vmax > 1e-9:
            lim_bits.append(f"v ≤ {vmax:.2f} м/с у трубах")
        if hp > 1e-9:
            lim_bits.append(f"H насоса (задано) = {hp:.1f} м")
        if lim_bits:
            text += "\nПеревірки: " + "; ".join(lim_bits) + "."
        lbl.config(text=text)

    def build_topo_tab(self):
        tab = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(tab, text="Рельєф ⛰️")

        tk.Label(tab, text="ІНСТРУМЕНТИ РЕЛЬЄФУ", bg="#1e1e1e", fg="#FFD700", font=("Arial", 10, "bold")).pack(pady=10)

        tk.Radiobutton(tab, text="📍 Ставити точки висоти (ЛКМ)", variable=self.app.mode, value="TOPO", indicatoron=0, bg="#2e4d46", fg="white", selectcolor="#00FFCC", width=28, font=("Arial", 9, "bold"), command=self.app.reset_temp).pack(pady=5)

        btn_clear_topo = tk.Button(
            tab,
            text="🗑 Очистити рельєф",
            command=self.app.clear_topo,
            bg="#662222",
            fg="white",
            font=("Arial", 9),
            width=25,
        )
        btn_clear_topo.pack(pady=10)
        self._attach_tooltip(btn_clear_topo, "Прибрати з проєкту всі точки висоти та побудовані ізолінії.")

        self.lbl_z_cursor = tk.Label(tab, text="Z під курсором: -- м", bg="#1e1e1e", fg="#00FFCC", font=("Consolas", 10, "bold"))
        self.lbl_z_cursor.pack(pady=5)

        tk.Frame(tab, bg="#333", height=2).pack(fill=tk.X, padx=10, pady=10)
        
        tk.Label(tab, text="АВТОМАТИЧНІ ІЗОЛІНІЇ", bg="#1e1e1e", fg="#FFD700", font=("Arial", 9, "bold")).pack(pady=5)
        self.create_input(tab, "Крок ізоліній (м):", self.app.var_topo_step)
        self.create_input(tab, "Розмір сітки (м):", self.app.var_topo_grid)
        
        tk.Label(tab, text="Відображення на полотні", bg="#1e1e1e", fg="#aaaaaa", font=("Arial", 8)).pack(pady=(4, 0))
        btn_frame = tk.Frame(tab, bg="#1e1e1e")
        btn_frame.pack(pady=5)
        self.btn_build_contours = tk.Button(
            btn_frame,
            text="Побудувати ізолінії",
            command=self.app.build_contours,
            bg="#0066FF",
            fg="white",
            font=("Arial", 9, "bold"),
            width=26,
        )
        self.btn_build_contours.pack(side=tk.TOP, pady=2)
        self._attach_tooltip(
            self.btn_build_contours,
            "Ізолінії рельєфу: інтерполяція сітки методом IDW (як раніше). "
            "Обрізка — зона проєкту з карти (пріоритет), інакше KML SRTM, інакше контури блоків.",
        )
        self.btn_build_contours_kriging = tk.Button(
            btn_frame,
            text="Побудувати ізолінії (кріггінг)",
            command=self.app.build_contours_kriging,
            bg="#0066FF",
            fg="white",
            font=("Arial", 9, "bold"),
            width=26,
        )
        self.btn_build_contours_kriging.pack(side=tk.TOP, pady=2)
        self._attach_tooltip(
            self.btn_build_contours_kriging,
            "Та сама сітка та обрізка; Z на вузлах — звичайний кріггінг (PyKrige, варіограмма spherical/linear). "
            "На великих DEM (>500 точок) — локальне вікно найближчих точок. Потрібно: pip install pykrige scipy.",
        )
        tk.Checkbutton(btn_frame, text="Показувати ізолінії", variable=self.app.show_contours, command=self.app.redraw, bg="#1e1e1e", fg="white", selectcolor="#333", activebackground="#1e1e1e", activeforeground="white").pack(side=tk.TOP)
        tk.Checkbutton(btn_frame, text="Відображати точки висоти", variable=self.app.show_topo_points, command=self.app.redraw, bg="#1e1e1e", fg="white", selectcolor="#333", activebackground="#1e1e1e", activeforeground="white").pack(side=tk.TOP)
        _cb_topo_zone = tk.Checkbutton(
            btn_frame,
            text="Зона обчислення рельєфу (рамка)",
            variable=self.app.show_topo_computation_zone,
            command=self.app.redraw,
            bg="#1e1e1e",
            fg="white",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="white",
        )
        _cb_topo_zone.pack(side=tk.TOP)
        self._attach_tooltip(
            _cb_topo_zone,
            "Показує той самий полігон, що й обрізка ізоліній: спочатку рамка зони проєкту з карти, "
            "далі KML SRTM, далі об’єднання блоків поля.",
        )
        tk.Checkbutton(btn_frame, text="Межа / зона SRTM на кресленні", variable=self.app.show_srtm_boundary_overlay, command=self.app.redraw, bg="#1e1e1e", fg="white", selectcolor="#333", activebackground="#1e1e1e", activeforeground="white").pack(side=tk.TOP)

        tk.Frame(tab, bg="#333", height=2).pack(fill=tk.X, padx=10, pady=10)

        tk.Label(tab, text="АВТОМАТИЧНИЙ РЕЛЬЄФ (SRTM)", bg="#1e1e1e", fg="#FFD700", font=("Arial", 9, "bold")).pack(pady=5)
        
        btn_kml_srtm = tk.Button(
            tab,
            text="📂 Імпорт зони SRTM (KML)",
            command=lambda: __import__("main_app.io.file_io_impl", fromlist=["import_srtm_kml"]).import_srtm_kml(
                self.app
            ),
            bg="#444",
            fg="white",
            font=("Arial", 9),
            width=30,
        )
        btn_kml_srtm.pack(pady=2)
        self._attach_tooltip(
            btn_kml_srtm,
            "Імпортувати полігон зони з KML для подальшого завантаження SRTM у межах цієї зони.",
        )
        self.btn_srtm_dl = tk.Button(
            tab,
            text="⬇ Тайли SRTM у _srtm_ (за KML/полем)",
            command=self.app.download_srtm_tiles,
            bg="#3d3d5c",
            fg="white",
            font=("Arial", 9, "bold"),
            width=30,
        )
        self.btn_srtm_dl.pack(pady=2)
        self._attach_tooltip(
            self.btn_srtm_dl,
            "Завантажити відсутні тайли висот у каталог _srtm_ за поточною KML-зоною або полем.",
        )
        self.btn_prepare_zone = tk.Button(
            tab,
            text="⬇ Тайли + висоти (зона)",
            command=self.app.prepare_map_project_zone_pipeline,
            bg="#2a5538",
            fg="white",
            font=("Arial", 9, "bold"),
            width=30,
        )
        self.btn_prepare_zone.pack(pady=2)
        self._attach_tooltip(
            self.btn_prepare_zone,
            "На карті: зона проєкту → завантажити тайли за потреби й заповнити DEM у межах зони (крок 5–90 м).",
        )
        self.btn_srtm_local_bbox = tk.Button(
            tab,
            text="📥 Лише висоти з _srtm_ (рамка)",
            command=self.app.load_local_srtm_heights_bbox,
            bg="#35523a",
            fg="white",
            font=("Arial", 9, "bold"),
            width=30,
        )
        self.btn_srtm_local_bbox.pack(pady=2)
        self._attach_tooltip(
            self.btn_srtm_local_bbox,
            "Взяти висоти лише з уже збережених тайлів у _srtm_ для рамки на карті (без нового завантаження).",
        )
        tk.Label(
            tab,
            text="Спочатку на карті: «Зона проєкту (рамка)», потім «Тайли + висоти» або кнопка тут. "
            "Крок сітки висот — «Роздільна здатність» (5, 15, 30, 45, 90 м). «Лише висоти» — без завантаження тайлів.",
            bg="#1e1e1e",
            fg="#888888",
            font=("Arial", 8),
            wraplength=280,
            justify=tk.CENTER,
        ).pack(pady=(0, 4))
        self.lbl_srtm_model_status = tk.Label(
            tab,
            text="SRTM-модель: перевірка…",
            bg="#1e1e1e",
            fg="#AAAAAA",
            font=("Consolas", 9, "bold"),
            justify=tk.CENTER,
            wraplength=280,
        )
        self.lbl_srtm_model_status.pack(pady=(0, 6))
        tk.Checkbutton(
            tab,
            text="Межі кешу (_srtm_ / DEM)",
            variable=self.app.show_srtm_tile_footprints,
            command=self.app.redraw,
            bg="#1e1e1e",
            fg="white",
            selectcolor="#333",
            activebackground="#1e1e1e",
            activeforeground="white",
            wraplength=260,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, padx=10, pady=(0, 6), anchor="w")
        
        frame_srtm_res = tk.Frame(tab, bg="#1e1e1e")
        frame_srtm_res.pack(pady=5)
        tk.Label(frame_srtm_res, text="Роздільна здатність:", bg="#1e1e1e", fg="white").pack(side=tk.LEFT)
        self.app.var_srtm_res = tk.StringVar(value="30")
        cb_srtm = ttk.Combobox(
            frame_srtm_res,
            textvariable=self.app.var_srtm_res,
            values=["5", "15", "30", "45", "90"],
            state="readonly",
            width=5,
        )
        cb_srtm.pack(side=tk.LEFT, padx=5)
        tk.Label(frame_srtm_res, text="м", bg="#1e1e1e", fg="white").pack(side=tk.LEFT)
        
        self.btn_srtm = tk.Button(
            tab,
            text="🌐 Завантажити з супутника",
            command=self.app.fetch_srtm_data,
            bg="#2e4d46",
            fg="white",
            font=("Arial", 9, "bold"),
            width=30,
        )
        self.btn_srtm.pack(pady=5)
        self._attach_tooltip(
            self.btn_srtm,
            "Завантажити рельєф SRTM за видимою областю полотна «Без карти» (онлайн).",
        )

    def create_input(self, parent, label, var):
        f = tk.Frame(parent, bg="#1e1e1e")
        f.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(f, text=label, bg="#1e1e1e", fg="white", font=("Arial", 9)).pack(side=tk.LEFT)
        tk.Entry(
            f,
            textvariable=var,
            bg="#333",
            fg="white",
            width=8,
            justify='center',
            font=("Consolas", 10, "bold"),
            insertbackground="white",
            insertwidth=2,
        ).pack(side=tk.RIGHT)

    def toggle_sec_entry(self):
        if self.app.var_fixed_sec.get():
            self.ent_num_sec.config(state=tk.NORMAL)
        else:
            self.ent_num_sec.config(state=tk.DISABLED)