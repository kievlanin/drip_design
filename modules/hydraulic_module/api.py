"""
Public hydraulic helpers consumed by the UI layer.

This module is a stable façade over lower-level hydraulic internals so UI code
does not import private helpers from hydraulics_core directly.
"""

from modules.hydraulic_module.hydraulics_core import (
    HydraulicEngine,
    _pn_sort_tuple as pn_sort_tuple,
    allowed_pipe_candidates_sorted,
    normalize_allowed_pipes_map,
    pick_smallest_allowed_pipe_for_inner_req,
)

__all__ = [
    "HydraulicEngine",
    "allowed_pipe_candidates_sorted",
    "normalize_allowed_pipes_map",
    "pick_smallest_allowed_pipe_for_inner_req",
    "pn_sort_tuple",
]
