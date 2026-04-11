"""
Один крило латераля (врізка → тупик): лінійний ухил рельєфу, без Shapely.
Повертає лише числовий результат і профіль для графіка.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

from . import lateral_drip_core as lat
from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C

Mode = Literal["tip", "shoot"]
ShootSolver = Literal["bisection", "newton"]


def _z_at_x_linear_slope(slope_pct: float):
    """x — від врізки до тупика (м). Ухил % > 0: Z зменшується до тупика."""

    def z_at_x(x_m: float) -> float:
        return -(float(slope_pct) / 100.0) * float(x_m)

    return z_at_x


@dataclass(frozen=True)
class LateralFieldInput:
    d_inner_m: float
    length_m: float
    slope_pct: float = 0.0
    c_hw: float = DEFAULT_HAZEN_WILLIAMS_C
    e_step_m: float = 0.3
    e_flow_lph: float = 1.05
    h_ref_m: float = 10.0
    h_tip_m: float = 10.0
    h_sub_target_m: float = 12.0
    compensated: bool = False
    h_min_m: float = 1.0
    mode: Mode = "tip"
    shoot_solver: ShootSolver = "bisection"
    affine_tol_m: float = 0.05


@dataclass(frozen=True)
class LateralFieldResult:
    h_at_connection_m: float
    q_total_m3s: float
    q_total_lph: float
    q_total_m3h: float
    h_tip_m: float
    profile: List[dict]
    length_m: float


def compute_lateral_field(inp: LateralFieldInput) -> LateralFieldResult:
    L = float(inp.length_m)
    d_in = float(inp.d_inner_m)
    e_step = float(inp.e_step_m)
    if L < 0.1:
        raise ValueError("Довжина має бути ≥ 0,1 м.")
    if d_in <= 0 or e_step <= 0:
        raise ValueError("Внутрішній діаметр і крок емітерів мають бути > 0.")

    z_at_x = _z_at_x_linear_slope(inp.slope_pct)
    c_hw = float(inp.c_hw)
    e_flow = float(inp.e_flow_lph)
    h_ref = float(inp.h_ref_m)
    h_min = max(0.05, float(inp.h_min_m))
    eo = {"compensated": bool(inp.compensated), "h_min_m": h_min}

    def _shoot_h_tip() -> float:
        h_sub = float(inp.h_sub_target_m)
        if str(inp.shoot_solver).strip().lower() == "newton":
            h_tip, _it = lat.solve_lateral_newton_raphson(
                h_sub,
                L,
                e_step,
                e_flow,
                z_at_x,
                d_in,
                c_hw,
                h_ref_m=h_ref,
                emitter_opts=eo,
            )
            return float(h_tip)
        h_tip, _it = lat.solve_lateral_shooting_bisection(
            h_sub,
            L,
            e_step,
            e_flow,
            z_at_x,
            d_in,
            c_hw,
            h_ref_m=h_ref,
            emitter_opts=eo,
        )
        return float(h_tip)

    h_tip_use = float(inp.h_tip_m)
    nodes_rev: List[dict] = []
    q_m3s = 0.0

    if inp.mode == "shoot":
        if eo["compensated"]:
            aff = lat.try_compensated_affine_tip(
                float(inp.h_sub_target_m),
                L,
                e_step,
                e_flow,
                z_at_x,
                d_in,
                c_hw,
                h_ref_m=h_ref,
                emitter_opts=eo,
                tol_m=float(inp.affine_tol_m),
            )
            if aff is not None:
                h_tip_use, _, nodes_rev, q_m3s = aff
            else:
                h_tip_use = _shoot_h_tip()
                _, q_m3s, nodes_rev = lat.backwards_step_method(
                    L,
                    e_step,
                    e_flow,
                    h_tip_use,
                    z_at_x,
                    d_in,
                    c_hw,
                    h_ref_m=h_ref,
                    emitter_opts=eo,
                )
        else:
            h_tip_use = _shoot_h_tip()
            _, q_m3s, nodes_rev = lat.backwards_step_method(
                L,
                e_step,
                e_flow,
                h_tip_use,
                z_at_x,
                d_in,
                c_hw,
                h_ref_m=h_ref,
                emitter_opts=eo,
            )
    else:
        _, q_m3s, nodes_rev = lat.backwards_step_method(
            L,
            e_step,
            e_flow,
            h_tip_use,
            z_at_x,
            d_in,
            c_hw,
            h_ref_m=h_ref,
            emitter_opts=eo,
        )

    prof = lat.wing_profile_from_backwards_nodes(nodes_rev)
    h_at_conn = float(prof[0]["h"]) if prof else 0.0
    q_tot = float(q_m3s)
    q_lph = q_tot * 1000.0 * 3600.0
    return LateralFieldResult(
        h_at_connection_m=h_at_conn,
        q_total_m3s=q_tot,
        q_total_lph=q_lph,
        q_total_m3h=q_lph / 1000.0,
        h_tip_m=float(h_tip_use),
        profile=prof,
        length_m=L,
    )


__all__ = [
    "LateralFieldInput",
    "LateralFieldResult",
    "Mode",
    "ShootSolver",
    "compute_lateral_field",
]
