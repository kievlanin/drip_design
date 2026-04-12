#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Польовий калькулятор одного крила латераля (врізка → тупик).

Типовий випадок — турбулентна (некомпенсована) крапельниця: вилив ∝ √(H/H_ref);
через зворотний зв’язок «тиск ↔ витрата» підбір H на тупику під заданий H біля врізки
робиться методом shooting (бісекція або Ньютона–Рафсона), див. shooting_method_solver.md.

Компенсована крапельниця — опційно (чекбокс); тоді спершу швидкий афінний підбір, інакше той самий shooting.

Опція «дальнє поле на магістралі»: ближній і дальній блок поливають одночасно; між тапами
втрати Hazen–Williams від Q дальнього ряду (спрощена 1D-модель до появи CAD магістралі).
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from main_app.ui.silent_messagebox import silent_showerror, silent_showwarning

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import replace

from modules.hydraulic_module.lateral_field_compute import (
    LateralFieldInput,
    Mode,
    ShootSolver,
    compute_lateral_field,
)
from modules.hydraulic_module.manifold_block_coupling import (
    ManifoldNearFarLegInput,
    solve_near_far_shared_manifold_leg,
)
from main_app.ui.tooltips import attach_tooltip


class LateralFieldCalculator(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Калькулятор латераля (поле)")
        self.configure(bg="#1a1a1a")
        self.geometry("920x800")
        self.minsize(720, 640)

        # --- графік зверху ---
        graph_fr = tk.Frame(self, bg="#1a1a1a")
        graph_fr.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self.canvas = tk.Canvas(
            graph_fr,
            height=340,
            bg="#222222",
            highlightthickness=1,
            highlightbackground="#444",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._lbl_graph = tk.Label(
            graph_fr,
            text="Натисніть «Розрахувати»",
            bg="#1a1a1a",
            fg="#888888",
            font=("Segoe UI", 9),
        )
        self._lbl_graph.pack(pady=(2, 0))

        # --- параметри знизу ---
        par = tk.Frame(self, bg="#1a1a1a")
        par.pack(fill=tk.X, padx=10, pady=8)

        self.var_d_mm = tk.StringVar(value="13.6")
        self.var_length = tk.StringVar(value="120")
        self.var_slope = tk.StringVar(value="0")
        self.var_c_hw = tk.StringVar(value="140")
        self.var_e_step = tk.StringVar(value="0.3")
        self.var_e_flow = tk.StringVar(value="1.05")
        self.var_h_ref = tk.StringVar(value="10.0")
        self.var_h_tip = tk.StringVar(value="10.0")
        self.var_h_sub_target = tk.StringVar(value="12.0")
        self.var_comp = tk.BooleanVar(value=False)
        self.var_h_min = tk.StringVar(value="1.0")
        self.var_mode = tk.StringVar(value="tip")  # tip | shoot
        self.var_shoot_solver = tk.StringVar(value="bisection")  # bisection | newton (для shoot)
        self.var_manifold_couple = tk.BooleanVar(value=False)
        self.var_leg_m = tk.StringVar(value="200")
        self.var_d_man_mm = tk.StringVar(value="50")
        self.var_L_far = tk.StringVar(value="150")

        r = 0
        def row(label, widget, col=0):
            nonlocal r
            tk.Label(par, text=label, bg="#1a1a1a", fg="#cccccc", width=28, anchor=tk.W).grid(
                row=r, column=col * 2, sticky=tk.W, pady=2
            )
            widget.grid(row=r, column=col * 2 + 1, sticky=tk.W, pady=2, padx=6)
            r += 1

        row("Внутрішній діаметр (мм):", ttk.Entry(par, textvariable=self.var_d_mm, width=14))
        row("Довжина крила (м):", ttk.Entry(par, textvariable=self.var_length, width=14))
        row("Ухил вздовж ряду (% , + вниз до тупика):", ttk.Entry(par, textvariable=self.var_slope, width=14))
        row("C Hazen–Williams:", ttk.Entry(par, textvariable=self.var_c_hw, width=14))
        row("Крок емітерів (м):", ttk.Entry(par, textvariable=self.var_e_step, width=14))
        row("Номінальний вилив (л/год):", ttk.Entry(par, textvariable=self.var_e_flow, width=14))
        row("H опорна √-крапельниці (м):", ttk.Entry(par, textvariable=self.var_h_ref, width=14))

        mode_fr = tk.Frame(par, bg="#1a1a1a")
        tk.Radiobutton(
            mode_fr,
            text="Задано H на тупику",
            variable=self.var_mode,
            value="tip",
            bg="#1a1a1a",
            fg="white",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="white",
        ).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(
            mode_fr,
            text="Підбір H тупика під H врізки",
            variable=self.var_mode,
            value="shoot",
            bg="#1a1a1a",
            fg="white",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="white",
        ).pack(side=tk.LEFT)
        row("Режим:", mode_fr)

        shoot_sol_fr = tk.Frame(par, bg="#1a1a1a")
        tk.Label(
            shoot_sol_fr,
            text="Підбір кореня (некомпенс. / fallback):",
            bg="#1a1a1a",
            fg="#888888",
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Radiobutton(
            shoot_sol_fr,
            text="Бісекція",
            variable=self.var_shoot_solver,
            value="bisection",
            bg="#1a1a1a",
            fg="white",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="white",
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Radiobutton(
            shoot_sol_fr,
            text="Ньютон–Рафсон",
            variable=self.var_shoot_solver,
            value="newton",
            bg="#1a1a1a",
            fg="white",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="white",
        ).pack(side=tk.LEFT)
        row("Shooting:", shoot_sol_fr)

        row("H на тупику (м вод. ст.):", ttk.Entry(par, textvariable=self.var_h_tip, width=14))
        row("Цільовий H біля врізки (м, для підбору):", ttk.Entry(par, textvariable=self.var_h_sub_target, width=14))

        comp_fr = tk.Frame(par, bg="#1a1a1a")
        tk.Checkbutton(
            comp_fr,
            text="Компенсована (інакше турбулентна ∝√H)",
            variable=self.var_comp,
            bg="#1a1a1a",
            fg="#00ffcc",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="#00ffcc",
        ).pack(side=tk.LEFT)
        tk.Label(comp_fr, text="  H мін (м):", bg="#1a1a1a", fg="#888").pack(side=tk.LEFT)
        ttk.Entry(comp_fr, textvariable=self.var_h_min, width=8).pack(side=tk.LEFT, padx=4)
        row("Тип емітера:", comp_fr)

        tk.Checkbutton(
            par,
            text="Дальнє поле на тій самій магістралі (одночасний полив; H ближнього = «цільовий H врізки»)",
            variable=self.var_manifold_couple,
            bg="#1a1a1a",
            fg="#ffcc66",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="#ffcc66",
        ).grid(row=r, column=0, columnspan=2, sticky=tk.W, pady=(6, 2))
        r += 1
        row("Нога магістралі ближн.→дальн. (м):", ttk.Entry(par, textvariable=self.var_leg_m, width=14))
        row("Внутр. D магістралі (мм):", ttk.Entry(par, textvariable=self.var_d_man_mm, width=14))
        row("Довжина крила дальнього блоку (м):", ttk.Entry(par, textvariable=self.var_L_far, width=14))

        self._out_h = tk.StringVar(value="H біля врізки: —")
        self._out_q = tk.StringVar(value="Сумарна витрата: —")
        self._out_tip = tk.StringVar(value="H на тупику (факт): —")

        tk.Label(par, textvariable=self._out_h, bg="#1a1a1a", fg="#ffd700", font=("Consolas", 11, "bold")).grid(
            row=r, column=0, columnspan=2, sticky=tk.W, pady=(10, 2)
        )
        r += 1
        tk.Label(par, textvariable=self._out_q, bg="#1a1a1a", fg="#00ffcc", font=("Consolas", 11, "bold")).grid(
            row=r, column=0, columnspan=2, sticky=tk.W, pady=2
        )
        r += 1
        tk.Label(par, textvariable=self._out_tip, bg="#1a1a1a", fg="#aaaaaa", font=("Consolas", 10)).grid(
            row=r, column=0, columnspan=2, sticky=tk.W, pady=2
        )
        r += 1
        self._out_manifold = tk.StringVar(value="")
        tk.Label(
            par,
            textvariable=self._out_manifold,
            bg="#1a1a1a",
            fg="#ffcc66",
            font=("Consolas", 9),
            justify=tk.LEFT,
            wraplength=860,
        ).grid(row=r, column=0, columnspan=2, sticky=tk.W, pady=(4, 2))
        r += 1

        btn_fr = tk.Frame(self, bg="#1a1a1a")
        btn_fr.pack(fill=tk.X, pady=(0, 12))
        _btn_run = tk.Button(
            btn_fr,
            text="▶ Розрахувати",
            command=self._run,
            bg="#2e6d4e",
            fg="white",
            font=("Segoe UI", 12, "bold"),
            padx=24,
            pady=10,
            cursor="hand2",
        )
        _btn_run.pack()
        attach_tooltip(
            _btn_run,
            "Запустити польовий гідравлічний розрахунок крила латераля за введеними параметрами.",
        )

    def _parse(self):
        d_mm = float(self.var_d_mm.get().replace(",", "."))
        length = float(self.var_length.get().replace(",", "."))
        slope = float(self.var_slope.get().replace(",", "."))
        c_hw = float(self.var_c_hw.get().replace(",", "."))
        e_step = float(self.var_e_step.get().replace(",", "."))
        e_flow = float(self.var_e_flow.get().replace(",", "."))
        h_ref = float(self.var_h_ref.get().replace(",", "."))
        h_tip = float(self.var_h_tip.get().replace(",", "."))
        h_sub = float(self.var_h_sub_target.get().replace(",", "."))
        h_min = float(self.var_h_min.get().replace(",", "."))
        return (
            d_mm / 1000.0,
            length,
            slope,
            c_hw,
            e_step,
            e_flow,
            h_ref,
            h_tip,
            h_sub,
            max(0.05, h_min),
        )

    def _lateral_input(
        self,
        *,
        d_in: float,
        length_m: float,
        slope: float,
        c_hw: float,
        e_step: float,
        e_flow: float,
        h_ref: float,
        h_tip_in: float,
        h_sub: float,
        h_min: float,
        mode: Mode,
        shoot_solver: ShootSolver,
    ) -> LateralFieldInput:
        return LateralFieldInput(
            d_inner_m=d_in,
            length_m=length_m,
            slope_pct=slope,
            c_hw=c_hw,
            e_step_m=e_step,
            e_flow_lph=e_flow,
            h_ref_m=h_ref,
            h_tip_m=h_tip_in,
            h_sub_target_m=h_sub,
            compensated=bool(self.var_comp.get()),
            h_min_m=h_min,
            mode=mode,
            shoot_solver=shoot_solver,
        )

    def _run(self):
        try:
            d_in, L, slope, c_hw, e_step, e_flow, h_ref, h_tip_in, h_sub, h_min = self._parse()
        except ValueError:
            silent_showerror(self, "Помилка", "Перевірте числові поля.")
            return

        mode: Mode = "shoot" if self.var_mode.get().strip().lower() == "shoot" else "tip"
        shoot_solver: ShootSolver = (
            "newton" if self.var_shoot_solver.get().strip().lower() == "newton" else "bisection"
        )

        if bool(self.var_manifold_couple.get()):
            if mode != "shoot":
                silent_showwarning(
                    self,
                    "Увага",
                    "Сумісний полив з дальнім блоком рахується в режимі «Підбір H тупика під H врізки».",
                )
                return
            try:
                leg_m = float(self.var_leg_m.get().replace(",", "."))
                d_man_mm = float(self.var_d_man_mm.get().replace(",", "."))
                L_far = float(self.var_L_far.get().replace(",", "."))
            except ValueError:
                silent_showerror(self, "Помилка", "Перевірте поля магістралі (нога, D, довжина дальнього).")
                return
            d_man_m = d_man_mm / 1000.0
            near_inp = self._lateral_input(
                d_in=d_in,
                length_m=L,
                slope=slope,
                c_hw=c_hw,
                e_step=e_step,
                e_flow=e_flow,
                h_ref=h_ref,
                h_tip_in=h_tip_in,
                h_sub=h_sub,
                h_min=h_min,
                mode="shoot",
                shoot_solver=shoot_solver,
            )
            far_inp = replace(near_inp, length_m=L_far, h_tip_m=max(0.05, h_tip_in))
            try:
                mf = solve_near_far_shared_manifold_leg(
                    ManifoldNearFarLegInput(
                        h_at_near_tap_m=float(h_sub),
                        leg_length_m=leg_m,
                        d_manifold_inner_m=d_man_m,
                        near_lateral=near_inp,
                        far_lateral=far_inp,
                        c_manifold_hw=c_hw,
                    )
                )
            except ValueError as e:
                silent_showwarning(self, "Увага", str(e))
                return
            res = mf.near
            self._out_h.set(
                f"Ближній блок — H біля врізки: {res.h_at_connection_m:.3f} м вод. ст."
            )
            self._out_q.set(
                f"Ближній блок — витрата: {res.q_total_lph:.1f} л/год  ({res.q_total_m3h:.3f} м³/год)"
            )
            self._out_tip.set(f"Ближній — H на тупику: {res.h_tip_m:.3f} м вод. ст.")
            cv = "так" if mf.converged else "ні"
            self._out_manifold.set(
                f"Дальній блок: H біля врізки {mf.far.h_at_connection_m:.3f} м, "
                f"Q {mf.far.q_total_lph:.0f} л/год, H тупика {mf.far.h_tip_m:.3f} м. "
                f"Втрата ноги магістралі {mf.manifold_head_loss_m:.3f} м (іт. {mf.iterations}, збіжність {cv}). "
                f"Модель: між тапами тече лише витрата дальнього ряду."
            )
            self._lbl_graph.config(
                text="Профіль ближнього крила: H (золото), Q у трубі (бірюза), ΔZ (оливковий)"
            )
            self._draw_profile(res.profile, res.length_m)
            return

        self._out_manifold.set("")
        try:
            res = compute_lateral_field(
                self._lateral_input(
                    d_in=d_in,
                    length_m=L,
                    slope=slope,
                    c_hw=c_hw,
                    e_step=e_step,
                    e_flow=e_flow,
                    h_ref=h_ref,
                    h_tip_in=h_tip_in,
                    h_sub=h_sub,
                    h_min=h_min,
                    mode=mode,
                    shoot_solver=shoot_solver,
                )
            )
        except ValueError as e:
            silent_showwarning(self, "Увага", str(e))
            return

        self._out_h.set(f"H біля врізки: {res.h_at_connection_m:.3f} м вод. ст.")
        self._out_q.set(
            f"Сумарна витрата: {res.q_total_lph:.1f} л/год  ({res.q_total_m3h:.3f} м³/год)"
        )
        self._out_tip.set(f"H на тупику (задано/підібрано): {res.h_tip_m:.3f} м вод. ст.")
        self._lbl_graph.config(text="Профіль: H (золото), Q у трубі (бірюза), ΔZ (оливковий)")

        self._draw_profile(res.profile, res.length_m)

    def _draw_profile(self, prof: list, length_m: float):
        c = self.canvas
        c.update_idletasks()
        w = max(c.winfo_width(), 400)
        h = max(c.winfo_height(), 280)
        c.delete("all")

        pad_l, pad_r, pad_t, pad_b = 56, 24, 36, 44
        pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
        if not prof or length_m <= 0:
            c.create_text(w // 2, h // 2, text="Немає точок профілю", fill="#888")
            return

        xs = [float(p["x"]) for p in prof]
        hs = [float(p["h"]) for p in prof]
        qs = [float(p.get("q", 0)) for p in prof]
        els = [float(p.get("elev", 0)) for p in prof]

        x_max = max(xs + [length_m, 0.01])
        h_min_v, h_max_v = min(hs + els), max(hs + els)
        span_h = h_max_v - h_min_v
        pad_y = max(0.3, span_h * 0.08) if span_h > 1e-6 else 1.0
        y_lo, y_hi = h_min_v - pad_y, h_max_v + pad_y
        if y_hi <= y_lo:
            y_hi = y_lo + 5.0

        q_max = max(qs) if qs else 1.0
        q_scale = max(q_max * 1.08, 1.5)

        def sx(xv: float) -> float:
            return pad_l + (float(xv) / x_max) * pw

        def sy(yv: float) -> float:
            return pad_t + ph - ((float(yv) - y_lo) / (y_hi - y_lo)) * ph

        def sqy(qv: float) -> float:
            return pad_t + ph - (min(float(qv), q_scale) / q_scale) * ph

        c.create_line(pad_l, pad_t + ph, pad_l + pw, pad_t + ph, fill="#555", width=1)
        c.create_line(pad_l, pad_t, pad_l, pad_t + ph, fill="#555", width=1)

        pts_e = [(sx(x), sy(el)) for x, el in zip(xs, els)]
        pts_h = [(sx(x), sy(hv)) for x, hv in zip(xs, hs)]
        pts_q = [(sx(x), sqy(qv)) for x, qv in zip(xs, qs)]

        if len(pts_e) > 1:
            c.create_line(pts_e, fill="#8B8B4A", width=2, smooth=True)
        if len(pts_h) > 1:
            c.create_line(pts_h, fill="#FFD700", width=2, smooth=True)
        if len(pts_q) > 1:
            c.create_line(pts_q, fill="#00FFCC", width=2, smooth=True)

        c.create_text(pad_l, pad_t - 8, text="H, ΔZ — м  │  Q — л/год (масштаб по висоті)", fill="#aaa", anchor=tk.W, font=("Segoe UI", 9))
        c.create_text(pad_l + pw // 2, h - 18, text=f"Відстань від врізки, м  (0 … {length_m:.1f})", fill="#ccc", font=("Segoe UI", 9))


def main():
    app = LateralFieldCalculator()
    app.mainloop()


if __name__ == "__main__":
    main()
