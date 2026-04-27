import copy
import json
import math
import os
import tempfile
from typing import Any


def _json_dict_key_str(key: Any) -> str:
    """JSON object keys must be strings; normalize composite/numeric cache keys."""
    if isinstance(key, str):
        return key
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, int):
        return str(int(key))
    if isinstance(key, float):
        if math.isnan(key) or math.isinf(key):
            return "_invalid_numeric_key_"
        if abs(key - round(key)) < 1e-9:
            return str(int(round(key)))
        return str(key)
    if isinstance(key, tuple) and len(key) == 2:
        return f"{str(key[0]).strip()}->{str(key[1]).strip()}"
    return str(key)


def sanitize_for_json_export(obj: Any, *, _depth: int = 0) -> Any:
    """
    Recursively convert structures to json.dumps-safe data with string dict keys.
    """
    if _depth > 120:
        return None
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            out[_json_dict_key_str(key)] = sanitize_for_json_export(value, _depth=_depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json_export(item, _depth=_depth + 1) for item in obj]
    if isinstance(obj, (set, frozenset)):
        return [sanitize_for_json_export(item, _depth=_depth + 1) for item in obj]
    return str(obj)


def atomic_write_text(filepath: str, text: str) -> None:
    """Write UTF-8 text atomically in the target directory."""
    filepath = os.path.abspath(filepath)
    directory = os.path.dirname(filepath)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".drdproj_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as write_file:
            write_file.write(text)
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def format_json_decode_error(filepath: str, err: json.JSONDecodeError) -> str:
    """Return a readable decode error with nearby source lines."""
    filepath = os.path.abspath(filepath)
    lines: list[str] = []
    try:
        with open(filepath, "r", encoding="utf-8") as read_file:
            lines = read_file.read().splitlines()
    except Exception:
        return f"{filepath}\n{err}"
    line_no = int(getattr(err, "lineno", 1) or 1)
    col_no = int(getattr(err, "colno", 1) or 1)
    start = max(0, line_no - 4)
    end = min(len(lines), line_no + 3)
    parts = [f"Файл: {filepath}", f"Помилка JSON: {err}", f"Рядок {line_no}, колонка {col_no}:", ""]
    for index in range(start, end):
        prefix = ">" if index + 1 == line_no else " "
        parts.append(f"{prefix} {index + 1:5d}: {lines[index]}")
    return "\n".join(parts)


def normalize_trunk_irrigation_hydro_cache_from_json(cache: dict[str, Any]) -> dict[str, Any]:
    """Restore int segment keys after json.load converted them to strings."""
    if not isinstance(cache, dict):
        return cache
    out = copy.deepcopy(cache)
    seg_dominant_slot = out.get("seg_dominant_slot")
    if isinstance(seg_dominant_slot, dict):
        normalized: dict[int, Any] = {}
        for key, value in seg_dominant_slot.items():
            try:
                int_key = int(key)
            except (TypeError, ValueError):
                continue
            if value is None:
                normalized[int_key] = None
                continue
            try:
                normalized[int_key] = int(value)
            except (TypeError, ValueError):
                try:
                    normalized[int_key] = int(float(value))
                except (TypeError, ValueError):
                    normalized[int_key] = value
        out["seg_dominant_slot"] = normalized
    return out
