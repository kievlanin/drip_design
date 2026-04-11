"""Спільні підказки для віджетів Tk (українською)."""

from __future__ import annotations

import tkinter as tk


def attach_tooltip(widget: tk.Misc, text: str, *, wraplength: int = 260) -> None:
    """Золотий текст на темному тлі (панель керування)."""
    tip: dict = {"win": None}

    def _show(_event=None):
        if tip["win"] is not None:
            return
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        lbl = tk.Label(
            tw,
            text=text,
            bg="#111111",
            fg="#FFD700",
            font=("Arial", 11),
            relief=tk.SOLID,
            bd=1,
            padx=6,
            pady=4,
            justify=tk.LEFT,
            wraplength=wraplength,
        )
        lbl.pack()
        tw.update_idletasks()
        x = widget.winfo_rootx() + 12
        y_above = widget.winfo_rooty() - tw.winfo_reqheight() - 6
        y = max(8, y_above)
        tw.wm_geometry(f"+{x}+{int(y)}")
        tip["win"] = tw

    def _hide(_event=None):
        w = tip["win"]
        tip["win"] = None
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    widget.bind("<Enter>", _show, add="+")
    widget.bind("<Leave>", _hide, add="+")
    widget.bind("<ButtonPress-1>", _hide, add="+")


def attach_tooltip_dark(
    widget: tk.Misc,
    text: str,
    *,
    above: bool = False,
    wraplength: int = 300,
) -> None:
    """Світлий текст на сірому тлі (вбудована карта). above=True — показати над віджетом."""
    tip: dict = {"win": None}

    def _show(_event=None):
        if tip["win"] is not None:
            return
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        lb = tk.Label(
            tw,
            text=text,
            bg="#2d2d2d",
            fg="#f0f0f0",
            font=("Segoe UI", 9),
            relief=tk.SOLID,
            bd=1,
            justify=tk.LEFT,
            wraplength=wraplength,
        )
        lb.pack(ipadx=5, ipady=3)
        tw.update_idletasks()
        tw_w = max(1, int(tw.winfo_reqwidth()))
        tw_h = max(1, int(tw.winfo_reqheight()))
        wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
        ww = max(1, int(widget.winfo_width()))
        cx = wx + max(0, (ww - tw_w) // 2)
        if above:
            y = wy - tw_h - 6
        else:
            y = wy + int(widget.winfo_height()) + 2
            cx = wx + 8
        tw.wm_geometry(f"+{cx}+{int(y)}")
        tip["win"] = tw

    def _hide(_event=None):
        w = tip["win"]
        tip["win"] = None
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    widget.bind("<Enter>", _show, add="+")
    widget.bind("<Leave>", _hide, add="+")
    widget.bind("<ButtonPress-1>", _hide, add="+")
