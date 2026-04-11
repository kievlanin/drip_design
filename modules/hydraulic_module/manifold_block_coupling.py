"""
Два блоки на одній магістралі: ближній тап (upstream), нога до дальнього, дальній тап.
Між тапами в магістралі тече лише витрата дальнього блоку; втрати HW залежать від Q_дальній.

Це спрощена 1D-модель без геометрії CAD — для оцінки «що має бути біля ближнього,
щоб дальнє поле одержало потрібний H врізки при одночасному поливі».
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import lateral_drip_core as lat
from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C
from .lateral_field_compute import LateralFieldInput, LateralFieldResult, compute_lateral_field


@dataclass(frozen=True)
class ManifoldNearFarLegInput:
    """Ближній блок upstream від дальнього; сегмент магістралі несе Q дальнього."""

    h_at_near_tap_m: float
    leg_length_m: float
    d_manifold_inner_m: float
    near_lateral: LateralFieldInput
    far_lateral: LateralFieldInput
    c_manifold_hw: float = DEFAULT_HAZEN_WILLIAMS_C


@dataclass(frozen=True)
class ManifoldNearFarLegResult:
    near: LateralFieldResult
    far: LateralFieldResult
    h_far_at_tap_m: float
    manifold_head_loss_m: float
    iterations: int
    converged: bool


def _h_tip_seed(h_sub: float) -> float:
    return max(0.05, min(float(h_sub) * 0.88, 35.0))


def solve_near_far_shared_manifold_leg(
    inp: ManifoldNearFarLegInput,
    *,
    max_iter: int = 50,
    tol_h_m: float = 0.002,
    relax: float = 0.65,
) -> ManifoldNearFarLegResult:
    h_near = float(inp.h_at_near_tap_m)
    Lm = float(inp.leg_length_m)
    dm = float(inp.d_manifold_inner_m)
    cm = float(inp.c_manifold_hw)
    near = inp.near_lateral
    far = inp.far_lateral
    if Lm < 0 or dm <= 0:
        raise ValueError("Довжина ноги магістралі ≥ 0, внутрішній діаметр магістралі > 0.")

    # Старт: втрати з номінальної оцінки Q дальнього при H ≈ h_near
    eo_far = {"compensated": bool(far.compensated), "h_min_m": max(0.05, float(far.h_min_m))}
    q_est = lat.approx_wing_q_m3s_nominal(
        float(far.length_m),
        float(far.e_step_m),
        float(far.e_flow_lph),
        h_near,
        float(far.h_ref_m),
        eo_far,
    )
    hf = lat.hazen_williams_hloss_m(q_est, Lm, dm, cm)
    h_far = max(0.08, h_near - hf)

    converged = False
    it_done = 0
    for it in range(max_iter):
        it_done = it + 1
        far_in = replace(
            far,
            mode="shoot",
            h_sub_target_m=float(h_far),
            h_tip_m=_h_tip_seed(h_far),
        )
        res_far = compute_lateral_field(far_in)
        qf = float(res_far.q_total_m3s)
        hf_new = lat.hazen_williams_hloss_m(qf, Lm, dm, cm)
        h_far_next = h_near - hf_new
        if h_far_next < 0.05:
            raise ValueError(
                "Напір біля дальнього врізання занадто низький: збільшіть H біля ближнього блоку, "
                "збільшіть діаметр магістралі або скоротіть ногу до дальнього."
            )
        if abs(h_far_next - h_far) <= tol_h_m:
            h_far = h_far_next
            converged = True
            break
        h_far = h_far + relax * (h_far_next - h_far)

    far_final = replace(
        far,
        mode="shoot",
        h_sub_target_m=float(h_far),
        h_tip_m=_h_tip_seed(h_far),
    )
    res_far = compute_lateral_field(far_final)
    hf_final = lat.hazen_williams_hloss_m(float(res_far.q_total_m3s), Lm, dm, cm)
    h_far_check = h_near - hf_final

    near_in = replace(
        near,
        mode="shoot",
        h_sub_target_m=float(h_near),
        h_tip_m=_h_tip_seed(h_near),
    )
    res_near = compute_lateral_field(near_in)

    return ManifoldNearFarLegResult(
        near=res_near,
        far=res_far,
        h_far_at_tap_m=float(h_far_check),
        manifold_head_loss_m=float(hf_final),
        iterations=it_done,
        converged=converged,
    )


def required_h_near_tap_for_far_connection(
    h_far_target_m: float,
    leg_length_m: float,
    d_manifold_inner_m: float,
    far_lateral: LateralFieldInput,
    *,
    c_manifold_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
) -> float:
    """
    Мінімальний напір біля ближнього тапа (після всього upstream), щоб біля дальнього
    було h_far_target_m при течії Q_дальній(h_far_target).
    H_near ≈ h_far_target + HW(Q_far, L_нога, D_магістралі).
    """
    hf = float(h_far_target_m)
    fl = replace(
        far_lateral,
        mode="shoot",
        h_sub_target_m=hf,
        h_tip_m=_h_tip_seed(hf),
    )
    res = compute_lateral_field(fl)
    qf = float(res.q_total_m3s)
    dhl = lat.hazen_williams_hloss_m(qf, float(leg_length_m), float(d_manifold_inner_m), float(c_manifold_hw))
    return hf + dhl


__all__ = [
    "ManifoldNearFarLegInput",
    "ManifoldNearFarLegResult",
    "required_h_near_tap_for_far_connection",
    "solve_near_far_shared_manifold_leg",
]
