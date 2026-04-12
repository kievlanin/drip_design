"""
Діалоги без системного звуку Windows (tkinter messagebox викликає MessageBeep).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

_OnClose = Optional[Callable[[], None]]


def _dialog(parent: tk.Misc, title: str, message: str, accent: str, on_close: _OnClose = None) -> None:
    if parent is None:
        return
    try:
        top = tk.Toplevel(parent.winfo_toplevel())
    except tk.TclError:
        return
    top.title(title)
    top.transient(parent.winfo_toplevel())
    top.resizable(True, True)
    top.configure(bg="#1a1e24")
    frm = tk.Frame(top, bg="#1a1e24", padx=14, pady=12)
    frm.pack(fill=tk.BOTH, expand=True)
    tk.Label(
        frm,
        text=title,
        bg="#1a1e24",
        fg=accent,
        font=("Segoe UI", 11, "bold"),
        justify=tk.LEFT,
        anchor=tk.W,
    ).pack(fill=tk.X, pady=(0, 8))
    wrap = tk.Frame(frm, bg="#222831", highlightthickness=1, highlightbackground="#3d4654")
    wrap.pack(fill=tk.BOTH, expand=True)
    sc = ttk.Scrollbar(wrap)
    sc.pack(side=tk.RIGHT, fill=tk.Y)
    lines = max(5, min(22, message.count("\n") + 3))
    txt = tk.Text(
        wrap,
        height=lines,
        width=72,
        wrap=tk.WORD,
        bg="#222831",
        fg="#E8EAED",
        font=("Consolas", 9),
        insertbackground="#E8EAED",
        relief=tk.FLAT,
        yscrollcommand=sc.set,
    )
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sc.config(command=txt.yview)
    txt.insert(tk.END, message)
    txt.config(state=tk.DISABLED)

    def _ok() -> None:
        try:
            top.destroy()
        except tk.TclError:
            pass
        if on_close is not None:
            try:
                on_close()
            except Exception:
                pass

    row = tk.Frame(frm, bg="#1a1e24")
    row.pack(fill=tk.X, pady=(12, 0))
    ttk.Button(row, text="OK", command=_ok).pack(side=tk.RIGHT)
    top.protocol("WM_DELETE_WINDOW", _ok)
    top.grab_set()
    try:
        top.focus_force()
    except tk.TclError:
        pass


def silent_showinfo(
    parent: Optional[tk.Misc], title: str, message: str, *, on_close: _OnClose = None
) -> None:
    _dialog(parent, title, message, "#64B5F6", on_close)


def silent_showwarning(
    parent: Optional[tk.Misc], title: str, message: str, *, on_close: _OnClose = None
) -> None:
    _dialog(parent, title, message, "#FFB74D", on_close)


def silent_showerror(
    parent: Optional[tk.Misc], title: str, message: str, *, on_close: _OnClose = None
) -> None:
    _dialog(parent, title, message, "#EF5350", on_close)


def silent_askyesno(parent: Optional[tk.Misc], title: str, message: str) -> bool:
    """Так / Ні без системного beep (на відміну від tkinter.messagebox.askyesno)."""
    out: dict = {"ok": False}
    if parent is None:
        return False
    try:
        top = tk.Toplevel(parent.winfo_toplevel())
    except tk.TclError:
        return False
    top.title(title)
    top.transient(parent.winfo_toplevel())
    top.resizable(True, True)
    top.configure(bg="#1a1e24")
    frm = tk.Frame(top, bg="#1a1e24", padx=14, pady=12)
    frm.pack(fill=tk.BOTH, expand=True)
    tk.Label(
        frm,
        text=title,
        bg="#1a1e24",
        fg="#FFB74D",
        font=("Segoe UI", 11, "bold"),
        justify=tk.LEFT,
        anchor=tk.W,
    ).pack(fill=tk.X, pady=(0, 8))
    wrap = tk.Frame(frm, bg="#222831", highlightthickness=1, highlightbackground="#3d4654")
    wrap.pack(fill=tk.BOTH, expand=True)
    sc = ttk.Scrollbar(wrap)
    sc.pack(side=tk.RIGHT, fill=tk.Y)
    lines = max(5, min(18, message.count("\n") + 3))
    txt = tk.Text(
        wrap,
        height=lines,
        width=72,
        wrap=tk.WORD,
        bg="#222831",
        fg="#E8EAED",
        font=("Consolas", 9),
        insertbackground="#E8EAED",
        relief=tk.FLAT,
        yscrollcommand=sc.set,
    )
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sc.config(command=txt.yview)
    txt.insert(tk.END, message)
    txt.config(state=tk.DISABLED)

    def _finish_yes() -> None:
        out["ok"] = True
        try:
            top.destroy()
        except tk.TclError:
            pass

    def _finish_no() -> None:
        try:
            top.destroy()
        except tk.TclError:
            pass

    row = tk.Frame(frm, bg="#1a1e24")
    row.pack(fill=tk.X, pady=(12, 0))
    ttk.Button(row, text="Ні", command=_finish_no).pack(side=tk.RIGHT)
    ttk.Button(row, text="Так", command=_finish_yes).pack(side=tk.RIGHT, padx=(0, 8))
    top.protocol("WM_DELETE_WINDOW", _finish_no)
    top.grab_set()
    try:
        top.focus_force()
    except tk.TclError:
        pass
    try:
        top.wait_window(top)
    except tk.TclError:
        pass
    return bool(out["ok"])
