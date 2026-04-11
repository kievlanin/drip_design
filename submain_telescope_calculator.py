#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Підбір телескопа сабмейну: напір біля насоса → напір у кінці, відомі L і Q по ділянках.

Запуск з кореня проєкту: python submain_telescope_calculator.py

Рядки сегментів: «довжина_м витрата_м3_год» (приклад: 200 40). Витрату по ділянках
беруть з розрахунку латералів (накопичена Q зменшується до кінця — вводьте від насоса до дальнього).
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, scrolledtext

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.hydraulic_module.submain_telescope_opt import (
    TelescopeSegment,
    optimize_submain_telescope,
)
from main_app.ui.tooltips import attach_tooltip


DEFAULT_SEGMENTS = """200 40
150 25
100 10"""


class SubmainTelescopeCalculator(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Сабмейн: телескоп під бюджет напору")
        self.configure(bg="#1a1a1a")
        self.geometry("780x640")
        self.minsize(640, 520)

        par = tk.Frame(self, bg="#1a1a1a")
        par.pack(fill=tk.X, padx=10, pady=8)

        self.var_h_in = tk.StringVar(value="30")
        self.var_h_end = tk.StringVar(value="20")
        self.var_vmax = tk.StringVar(value="2.5")
        self.var_c_hw = tk.StringVar(value="140")
        self.var_mat = tk.StringVar(value="PVC")

        r = 0

        def row(lbl, w):
            nonlocal r
            tk.Label(par, text=lbl, bg="#1a1a1a", fg="#ccc", width=34, anchor=tk.W).grid(
                row=r, column=0, sticky=tk.W, pady=2
            )
            w.grid(row=r, column=1, sticky=tk.W, pady=2, padx=6)
            r += 1

        row("Напір біля насоса / початку сабмейну (м):", ttk.Entry(par, textvariable=self.var_h_in, width=12))
        row("Мін. напір у кінці / дальнього поля (м):", ttk.Entry(par, textvariable=self.var_h_end, width=12))
        row("C Hazen–Williams:", ttk.Entry(par, textvariable=self.var_c_hw, width=12))
        row("Макс. швидкість у сабмейні (м/с):", ttk.Entry(par, textvariable=self.var_vmax, width=12))
        row("Матеріал:", ttk.Combobox(par, textvariable=self.var_mat, values=("PVC", "PE", "Layflat"), width=10, state="readonly"))

        tk.Label(
            par,
            text="Сегменти від насоса → дальнє поле (кожен ряд: довжина_м  витрата_м³/год):",
            bg="#1a1a1a",
            fg="#aaa",
        ).grid(row=r, column=0, columnspan=2, sticky=tk.W, pady=(10, 4))
        r += 1

        self.txt_segs = scrolledtext.ScrolledText(
            par,
            width=52,
            height=8,
            bg="#2a2a2a",
            fg="#e0e0e0",
            insertbackground="white",
            font=("Consolas", 10),
        )
        self.txt_segs.grid(row=r, column=0, columnspan=2, sticky=tk.EW, pady=4)
        par.columnconfigure(1, weight=1)
        self.txt_segs.insert(tk.END, DEFAULT_SEGMENTS)
        r += 1

        tk.Label(
            par,
            text="Рельєф (опційно): у рядку через пробіл третє число — ΔZ по сегменту в метрах\n"
            "(+ якщо вгору вздовж потоку, з’їдає напір так само як тертя).",
            bg="#1a1a1a",
            fg="#666",
            font=("Segoe UI", 8),
            justify=tk.LEFT,
        ).grid(row=r, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))
        r += 1

        self.out = scrolledtext.ScrolledText(
            self,
            height=14,
            bg="#1e1e1e",
            fg="#9cdcfe",
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.out.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        _btn_telescope = tk.Button(
            self,
            text="Підібрати телескоп",
            command=self._run,
            bg="#2e4d6d",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            padx=20,
            pady=8,
        )
        _btn_telescope.pack(pady=(0, 10))
        attach_tooltip(
            _btn_telescope,
            "Підібрати діаметри сегментів сабмейну (телескоп) під заданий бюджет втрат напору.",
        )

    def _parse_segments(self) -> list[TelescopeSegment]:
        lines = self.txt_segs.get("1.0", tk.END).strip().splitlines()
        segs: list[TelescopeSegment] = []
        for li, line in enumerate(lines, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", ".").split()
            if len(parts) < 2:
                raise ValueError(f"Рядок {li}: потрібні довжина (м) і витрата (м³/год).")
            L = float(parts[0])
            q_m3h = float(parts[1])
            dz = float(parts[2]) if len(parts) >= 3 else 0.0
            segs.append(TelescopeSegment(length_m=L, q_m3s=q_m3h / 3600.0, dz_m=dz))
        if not segs:
            raise ValueError("Додайте хоча б один сегмент.")
        return segs

    def _run(self):
        try:
            h_in = float(self.var_h_in.get().replace(",", "."))
            h_end = float(self.var_h_end.get().replace(",", "."))
            v_max = float(self.var_vmax.get().replace(",", "."))
            c_hw = float(self.var_c_hw.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Помилка", "Перевірте числові поля зверху.")
            return
        try:
            segs = self._parse_segments()
        except ValueError as e:
            messagebox.showerror("Помилка", str(e))
            return

        try:
            res = optimize_submain_telescope(
                segs,
                h_in,
                h_end,
                material=self.var_mat.get().strip() or "PVC",
                c_hw=c_hw,
                v_max_m_s=v_max,
            )
        except Exception as e:
            messagebox.showerror("Помилка", str(e))
            return

        self.out.delete("1.0", tk.END)
        w = self.out
        w.insert(tk.END, res.message + "\n\n")
        if not res.feasible:
            return
        w.insert(tk.END, "Сегм.  L, м   Q, м³/год   d, мм   PN   v, м/с   ΔH_HW, м\n")
        for p in res.picks:
            s = p.sku
            seg = segs[p.segment_index]
            q_h = seg.q_m3s * 3600.0
            w.insert(
                tk.END,
                f"{p.segment_index + 1:>4}  {seg.length_m:>6.1f}  {q_h:>10.2f}  {s.d_nom_mm:>6.0f}  {s.pn:>4}  {p.v_m_s:>6.2f}  {p.hf_m:>10.3f}\n",
            )
        w.insert(tk.END, f"\nΣΔZ по сегментах: {res.total_dz_m:.3f} м\n")
        w.insert(
            tk.END,
            "\nІндекс вартості — умовний (DN і PN), для порівняння варіантів у каталозі; "
            "реальні ціни зазвичай залежать від постачальника.\n",
        )


def main():
    app = SubmainTelescopeCalculator()
    app.mainloop()


if __name__ == "__main__":
    main()
