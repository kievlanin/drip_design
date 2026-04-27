from typing import Any


def normalize_consumer_schedule_payload(raw: Any) -> dict[str, Any]:
    """Normalize persisted irrigation schedule payload while preserving legacy defaults."""
    out_groups = []
    if isinstance(raw, dict):
        groups = raw.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                title = str(group.get("title", "")).strip() or "Група"
                ids = group.get("node_ids")
                if ids is None:
                    ids = group.get("nodes")
                if not isinstance(ids, list):
                    ids = []
                clean = []
                for item in ids:
                    value = str(item).strip()
                    if value and value not in clean:
                        clean.append(value)
                out_groups.append({"title": title, "node_ids": clean})

    slots: list[list[str]] = []
    raw_slots = raw.get("irrigation_slots") if isinstance(raw, dict) else None
    if isinstance(raw_slots, list):
        for index in range(48):
            cell: list[str] = []
            if index < len(raw_slots) and isinstance(raw_slots[index], list):
                for item in raw_slots[index]:
                    value = str(item).strip()
                    if value and value not in cell:
                        cell.append(value)
            slots.append(cell)
    else:
        slots = [[] for _ in range(48)]

    out: dict[str, Any] = {"groups": out_groups, "irrigation_slots": slots}
    if not isinstance(raw, dict):
        return out

    _copy_float_range(raw, out, "max_pump_head_m", 0.0, 400.0)
    _copy_float_range(raw, out, "trunk_schedule_v_max_mps", 0.0, 8.0)
    _copy_float_range(raw, out, "trunk_schedule_test_q_m3h", 0.0, 10000.0)
    _copy_float_range(raw, out, "trunk_schedule_test_h_m", 0.0, 400.0)
    _copy_int_range(raw, out, "trunk_schedule_max_sections_per_edge", 1, 4)
    _copy_float_range(raw, out, "trunk_display_velocity_warn_mps", 0.0, 8.0)

    goal = str(raw.get("trunk_schedule_opt_goal", "weight")).strip().lower()
    if goal not in ("weight", "money", "cost_index"):
        goal = "weight"
    if goal == "cost_index":
        goal = "money"
    out["trunk_schedule_opt_goal"] = goal
    out["trunk_pipes_selected"] = bool(raw.get("trunk_pipes_selected", False))
    out["field_valve_label_pos"] = _normalize_xy_map(raw.get("field_valve_label_pos"))

    source_mode = str(raw.get("srtm_source_mode", "auto")).strip().lower()
    if source_mode not in ("auto", "skadi_local", "open_elevation", "earthdata"):
        source_mode = "auto"
    out["srtm_source_mode"] = source_mode
    return out


def _copy_float_range(
    src: dict[str, Any],
    dst: dict[str, Any],
    key: str,
    min_value: float,
    max_value: float,
) -> None:
    try:
        value = src.get(key)
        if value is None or str(value).strip() == "":
            return
        number = float(value)
        if number >= min_value:
            dst[key] = max(min_value, min(max_value, float(number)))
    except (TypeError, ValueError):
        pass


def _copy_int_range(
    src: dict[str, Any],
    dst: dict[str, Any],
    key: str,
    min_value: int,
    max_value: int,
) -> None:
    try:
        value = src.get(key)
        if value is None or str(value).strip() == "":
            return
        number = int(float(value))
        dst[key] = max(min_value, min(max_value, int(number)))
    except (TypeError, ValueError):
        pass


def _normalize_xy_map(raw: Any) -> dict[str, list[float]]:
    clean: dict[str, list[float]] = {}
    if not isinstance(raw, dict):
        return clean
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if isinstance(raw_value, (list, tuple)) and len(raw_value) >= 2:
            try:
                clean[key] = [float(raw_value[0]), float(raw_value[1])]
            except (TypeError, ValueError):
                pass
    return clean
