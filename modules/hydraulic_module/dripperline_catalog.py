import json
from typing import Any, Dict, List, Optional

from main_app.paths import DRIPPERLINES_DB_PATH


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return float(default)


def _to_opt_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_working_pressure(v: Any) -> Dict[str, Any]:
    raw = "" if v is None else str(v).strip()
    if not raw:
        return {"raw": "", "max": None, "options": []}
    parts = [p.strip() for p in raw.replace(",", ".").split("/") if p.strip()]
    opts = []
    for p in parts:
        try:
            opts.append(float(p))
        except ValueError:
            pass
    if not opts:
        one = _to_opt_float(raw)
        opts = [one] if one is not None else []
    return {"raw": raw, "max": (max(opts) if opts else None), "options": opts}


def load_dripperlines_catalog() -> List[Dict[str, Any]]:
    """Load dripperline catalog normalized for calculations."""
    if not DRIPPERLINES_DB_PATH.exists():
        return []
    try:
        with open(DRIPPERLINES_DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        return []

    items = db.get("series", []) if isinstance(db, dict) else db
    if not isinstance(items, list):
        return []

    out: List[Dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        series_name = str(row.get("series", "")).strip()
        tech = row.get("technical_data", [])
        if not isinstance(tech, list):
            tech = []
        norm_tech = []
        for it in tech:
            if not isinstance(it, dict):
                continue
            wp = _parse_working_pressure(it.get("max_working_pressure_bar"))
            norm_tech.append(
                {
                    "model": str(it.get("model", "")).strip(),
                    "inside_diameter_mm": _to_float(it.get("inside_diameter_mm"), 0.0),
                    "wall_thickness_mm": _to_float(it.get("wall_thickness_mm"), 0.0),
                    "outside_diameter_mm": _to_float(it.get("outside_diameter_mm"), 0.0),
                    "max_working_pressure_bar_raw": wp["raw"],
                    "max_working_pressure_bar_max": wp["max"],
                    "max_working_pressure_bar_options": wp["options"],
                    "max_flushing_pressure_bar": _to_opt_float(it.get("max_flushing_pressure_bar")),
                    "kd": _to_float(it.get("kd"), 0.0),
                }
            )
        out.append({"series": series_name, "technical_data": norm_tech})
    return out


def find_dripperline(catalog: List[Dict[str, Any]], series: str, model: str) -> Optional[Dict[str, Any]]:
    s = str(series).strip().lower()
    m = str(model).strip().lower()
    for block in catalog:
        if str(block.get("series", "")).strip().lower() != s:
            continue
        for it in block.get("technical_data", []) or []:
            if str(it.get("model", "")).strip().lower() == m:
                return it
    return None

