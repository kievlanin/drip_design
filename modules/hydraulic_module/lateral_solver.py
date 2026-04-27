"""
Прямий розрахунок втрат на латералі: зворотна інтеграція HW + підбір тиску на кінці
методом бісекції (shooting) та Ньютона–Рафсона. Див. shooting_method_solver.md, newton_raphson_solver.md.

Геометрія (Shapely) — лише тут; числове ядро — lateral_drip_core.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points

from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C
from .lateral_drip_core import (
    approx_wing_q_m3s_nominal,
    backwards_step_method,
    build_wing_data_from_tip,
    emitter_flow_lph,
    hazen_williams_hloss_m,
    lph_to_m3s,
    solve_lateral_newton_raphson,
    solve_lateral_shooting_bisection,
    try_compensated_affine_tip,
    wing_profile_from_backwards_nodes,
)

# Макс. зазор (м) між латералем і сабмейном: вважаємо врізку навіть без точного перетину поліліній.
SUBMAIN_LATERAL_SNAP_M = 4.0

__all__ = [
    "approx_wing_q_m3s_nominal",
    "backwards_step_method",
    "build_wing_data_from_tip",
    "SUBMAIN_LATERAL_SNAP_M",
    "connection_distance_along_lateral",
    "emitter_flow_lph",
    "hazen_williams_hloss_m",
    "interpolate_head_along_submain",
    "lph_to_m3s",
    "nearest_submain_chainage_any",
    "probe_lateral_dripline",
    "solve_lateral_newton_raphson",
    "solve_lateral_shooting_bisection",
    "try_compensated_affine_tip",
    "wing_profile_from_backwards_nodes",
    "emitter_head_min_max_for_h_sub",
]


def _z_at_x_from_connection(
    lat: LineString,
    lat_len: float,
    conn_dist: float,
    is_l1: bool,
    topo_get_z: Callable[[float, float], float],
) -> Callable[[float], float]:
    """Як у hydraulics_core.calc_wing: x від врізки до тупика, da — відстань від початку полілінії латераля."""

    def z_at_x(x_from_conn: float) -> float:
        da = (conn_dist - x_from_conn) if is_l1 else (conn_dist + x_from_conn)
        da = max(0.0, min(lat_len, da))
        pt = lat.interpolate(da)
        return float(topo_get_z(pt.x, pt.y))

    return z_at_x


def probe_lateral_dripline(
    lat: LineString,
    conn_dist_m: float,
    h_tip_m: float,
    e_step: float,
    e_flow_lph: float,
    topo_get_z: Callable[[float, float], float],
    *,
    emitter_opts: Optional[Dict[str, Any]] = None,
    d_inner_m: float = 13.6 / 1000.0,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
) -> Dict[str, Any]:
    """
    Два крила від врізки в сабмейн; на обох тупиках один і той самий H_tip (м вод. ст.).
    Повертає напір біля врізки з кожного крила (зворотний HW) та сумарну витрату.
    """
    lat_len = float(lat.length)
    conn = max(0.0, min(lat_len, float(conn_dist_m)))
    d_in = float(d_inner_m)
    c_hw = float(C_hw)
    eo = emitter_opts or {}

    L1 = conn
    L2 = max(0.0, lat_len - conn)

    z1 = _z_at_x_from_connection(lat, lat_len, conn, True, topo_get_z)
    z2 = _z_at_x_from_connection(lat, lat_len, conn, False, topo_get_z)

    h1, q1, _ = backwards_step_method(
        L1, e_step, e_flow_lph, h_tip_m, z1, d_in, c_hw, h_ref_m, emitter_opts=eo
    )
    h2, q2, _ = backwards_step_method(
        L2, e_step, e_flow_lph, h_tip_m, z2, d_in, c_hw, h_ref_m, emitter_opts=eo
    )

    q_total = q1 + q2
    return {
        "H_at_connection_wing1_m": float(h1),
        "H_at_connection_wing2_m": float(h2),
        "Q_wing1_m3s": float(q1),
        "Q_wing2_m3s": float(q2),
        "Q_total_m3s": float(q_total),
        "Q_total_lph": float(q_total) * 1000.0 * 3600.0,
        "L1_m": float(L1),
        "L2_m": float(L2),
        "conn_dist_m": float(conn),
    }


def nearest_submain_chainage_any(px: float, py: float, submain_lines: list) -> Tuple[int, float]:
    """
    Найближча магістраль і відстань s від початку полілінії для точки врізки (для розподілу Q).
    Якщо магістралей немає — (0, 0.0).
    """
    best = None
    p = Point(px, py)
    for sm_idx, sm_coords in enumerate(submain_lines):
        if len(sm_coords) < 2:
            continue
        ls = LineString(sm_coords)
        d = ls.distance(p)
        if best is None or d < best[0]:
            best = (d, sm_idx, float(ls.project(p)))
    if best is None:
        return 0, 0.0
    return best[1], best[2]


def interpolate_head_along_submain(
    px: float,
    py: float,
    submain_lines: list,
    submain_profiles: dict,
    max_dist: float = 0.6,
    default_h: float = 10.0,
) -> float:
    best = None
    p = Point(px, py)
    for sm_idx, sm_coords in enumerate(submain_lines):
        if len(sm_coords) < 2:
            continue
        ls = LineString(sm_coords)
        d = ls.distance(p)
        if d > max_dist:
            continue
        if best is None or d < best[0]:
            s_along = ls.project(p)
            best = (d, sm_idx, s_along)

    if best is None:
        return default_h

    sm_idx, s_along = best[1], best[2]
    prof = submain_profiles.get(str(sm_idx), [])
    if not prof:
        return default_h
    if len(prof) == 1:
        return float(prof[0].get("h", default_h))

    pairs = [(float(r["s"]), float(r["h"])) for r in prof]
    pairs.sort(key=lambda t: t[0])
    if s_along <= pairs[0][0]:
        return pairs[0][1]
    if s_along >= pairs[-1][0]:
        return pairs[-1][1]
    for i in range(len(pairs) - 1):
        s0, h0 = pairs[i]
        s1, h1 = pairs[i + 1]
        if s0 <= s_along <= s1:
            if abs(s1 - s0) < 1e-9:
                return h0
            t = (s_along - s0) / (s1 - s0)
            return h0 + t * (h1 - h0)
    return default_h


def connection_distance_along_lateral(
    lat: LineString, submain_lines: List[list], *, snap_m: Optional[float] = None
) -> float:
    """Відстань від початку полілінії латераля до точки врізки в сабмейн (як у HydraulicEngine)."""
    snap = float(SUBMAIN_LATERAL_SNAP_M if snap_m is None else snap_m)
    vs_geom = [coords for coords in submain_lines if len(coords) > 1]
    sm_multi_geom = MultiLineString(vs_geom) if vs_geom else None
    conn_dist = 0.0
    if sm_multi_geom:
        inter = lat.intersection(sm_multi_geom)
        if not inter.is_empty:
            if inter.geom_type == "Point":
                conn_dist = lat.project(inter)
            elif inter.geom_type == "LineString":
                conn_dist = lat.project(inter.interpolate(0.5, normalized=True))
            elif hasattr(inter, "geoms") and len(inter.geoms) > 0:
                g0 = inter.geoms[0]
                if g0.geom_type == "Point":
                    conn_dist = lat.project(g0)
                elif g0.geom_type == "LineString":
                    conn_dist = lat.project(g0.interpolate(0.5, normalized=True))
        else:
            pt_lat, pt_sm = nearest_points(lat, sm_multi_geom)
            if pt_lat.distance(pt_sm) < snap:
                conn_dist = lat.project(pt_lat)
    return float(conn_dist)


def emitter_head_min_max_for_h_sub(
    lat: LineString,
    conn_dist_m: float,
    h_sub_target: float,
    e_step: float,
    e_flow_lph: float,
    topo_get_z: Callable[[float, float], float],
    *,
    d_inner_m: float = 13.6 / 1000.0,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    emitter_opts: Optional[Dict[str, Any]] = None,
    tol_shoot_m: float = 0.06,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Мін./макс. напір H (м вод. ст.) у вузлах з виливом (q_emit>0) на обох крилах
    при цільовому напорі біля врізки h_sub_target. Використовується для підбору діапазону H_врізки.
    Для стійкості пошуку завжди застосовується бісекція shooting (як у типовому режимі lat_mode=bisection).
    """
    lat_len = float(lat.length)
    conn = max(0.0, min(lat_len, float(conn_dist_m)))
    L1 = conn
    L2 = max(0.0, lat_len - conn)
    d_in = float(d_inner_m)
    c_hw = float(C_hw)
    eo = emitter_opts or {}
    hs: List[float] = []

    def wing(length: float, is_l1: bool) -> None:
        if length < 0.1:
            return
        z_at = _z_at_x_from_connection(lat, lat_len, conn, is_l1, topo_get_z)
        H_use, _it = solve_lateral_shooting_bisection(
            float(h_sub_target),
            length,
            e_step,
            e_flow_lph,
            z_at,
            d_in,
            c_hw,
            h_ref_m=h_ref_m,
            tol_m=tol_shoot_m,
            max_iter=44,
            emitter_opts=eo,
        )
        _hc, _q, nodes_rev = backwards_step_method(
            length,
            e_step,
            e_flow_lph,
            H_use,
            z_at,
            d_in,
            c_hw,
            h_ref_m=h_ref_m,
            emitter_opts=eo,
        )
        for row in wing_profile_from_backwards_nodes(nodes_rev):
            if float(row.get("q_emit", 0)) > 1e-4:
                hs.append(float(row["h"]))

    wing(L1, True)
    wing(L2, False)
    if not hs:
        return None, None
    return min(hs), max(hs)
