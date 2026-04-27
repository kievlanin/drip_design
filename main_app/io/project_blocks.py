import copy
from typing import Any

from shapely.geometry import LineString


def field_block_from_dict(item: dict[str, Any]) -> dict[str, Any]:
    """Restore one field block from JSON field_blocks_data."""
    ring = [tuple(point[:2]) if len(point) >= 2 else tuple(point) for point in item.get("ring", [])]
    edge_angle = item.get("edge_angle")
    if edge_angle is not None:
        edge_angle = float(edge_angle)
    params = item.get("params")
    if not isinstance(params, dict):
        params = {}
    segment_plan = item.get("submain_segment_plan")
    if not isinstance(segment_plan, dict):
        segment_plan = {}
    return {
        "ring": ring,
        "edge_angle": edge_angle,
        "submain_lines": [list(line) for line in item.get("submain", [])],
        "auto_laterals": [LineString(coords) for coords in item.get("auto", []) if len(coords) > 1],
        "manual_laterals": [LineString(coords) for coords in item.get("manual", []) if len(coords) > 1],
        "params": dict(params),
        "submain_segment_plan": segment_plan,
    }


def field_blocks_to_save_payload(app: Any) -> tuple[list[dict[str, Any]], list[Any]]:
    """Serialize runtime field blocks into JSON payload and legacy rings-only payload."""
    blocks = getattr(app, "field_blocks", None)
    if not blocks:
        return [], []
    payload = []
    rings_only = []
    for block in blocks:
        rings_only.append(block["ring"])
        params = block.get("params")
        if not isinstance(params, dict):
            params = {}
        segment_plan = block.get("submain_segment_plan")
        if not isinstance(segment_plan, dict):
            segment_plan = {}
        payload.append(
            {
                "ring": block["ring"],
                "edge_angle": block.get("edge_angle"),
                "submain": block["submain_lines"],
                "auto": [list(lat.coords) for lat in block["auto_laterals"]],
                "manual": [list(lat.coords) for lat in block["manual_laterals"]],
                "params": dict(params),
                "submain_segment_plan": segment_plan,
            }
        )
    return payload, rings_only


def translate_field_block(block: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    """Паралельний перенос усіх координат блоку; глибокі копії params / submain_segment_plan."""
    ring: list[tuple[float, float] | tuple] = [
        (float(p[0]) + dx, float(p[1]) + dy) for p in (block.get("ring") or []) if len(p) >= 2
    ]
    submain_lines: list[list[list[float]]] = []
    for line in block.get("submain_lines") or []:
        pl: list[list[float]] = []
        for p in line:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pl.append([float(p[0]) + dx, float(p[1]) + dy])
        if pl:
            submain_lines.append(pl)
    auto_laterals: list[LineString] = []
    for lat in block.get("auto_laterals") or []:
        if hasattr(lat, "coords") and not lat.is_empty:
            auto_laterals.append(
                LineString([(float(c[0]) + dx, float(c[1]) + dy) for c in lat.coords])
            )
    manual_laterals: list[LineString] = []
    for lat in block.get("manual_laterals") or []:
        if hasattr(lat, "coords") and not lat.is_empty:
            manual_laterals.append(
                LineString([(float(c[0]) + dx, float(c[1]) + dy) for c in lat.coords])
            )
    params = copy.deepcopy(block.get("params") or {})
    if not isinstance(params, dict):
        params = {}
    segment_plan = copy.deepcopy(block.get("submain_segment_plan") or {})
    if not isinstance(segment_plan, dict):
        segment_plan = {}
    edge = block.get("edge_angle")
    if edge is not None:
        try:
            edge = float(edge)
        except (TypeError, ValueError):
            edge = None
    return {
        "ring": ring,
        "edge_angle": edge,
        "submain_lines": submain_lines,
        "auto_laterals": auto_laterals,
        "manual_laterals": manual_laterals,
        "params": params,
        "submain_segment_plan": segment_plan,
    }
