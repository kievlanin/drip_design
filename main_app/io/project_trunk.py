import copy
from typing import Any

from modules.hydraulic_module.trunk_map_graph import ensure_trunk_node_ids


def collect_trunk_save_payload(app: Any) -> tuple[dict[str, Any], list[Any], list[dict[str, Any]], dict[str, Any] | None]:
    """
    Prepare trunk-related JSON payloads from app state.

    Returns:
        (trunk package, normalized nodes, normalized segments, hydro cache copy)
    """
    trunk_nodes = getattr(app, "trunk_map_nodes", None) or []
    if trunk_nodes:
        ensure_trunk_node_ids(trunk_nodes)

    trunk_segments = []
    for segment in list(getattr(app, "trunk_map_segments", []) or []):
        if not isinstance(segment, dict):
            continue
        record = dict(segment)
        raw_sections = record.get("sections")
        if not isinstance(raw_sections, list):
            raw_sections = record.get("telescoped_sections")
        if isinstance(raw_sections, list):
            record["sections"] = copy.deepcopy(raw_sections)
            record["telescoped_sections"] = copy.deepcopy(raw_sections)
        trunk_segments.append(record)

    allowed_pipes = getattr(app, "trunk_allowed_pipes", None)
    if not isinstance(allowed_pipes, dict):
        allowed_pipes = {}

    hydro_cache = getattr(app, "trunk_irrigation_hydro_cache", None)
    hydro_cache_json = copy.deepcopy(hydro_cache) if isinstance(hydro_cache, dict) else None
    trunk_payload = {
        "nodes": list(trunk_nodes),
        "segments": trunk_segments,
        "allowed_pipes": copy.deepcopy(allowed_pipes),
    }
    return trunk_payload, list(trunk_nodes), trunk_segments, hydro_cache_json
