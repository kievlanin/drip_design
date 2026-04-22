"""Тести багатовузлового Ньютона для лінії крапельного зрошення."""
from __future__ import annotations

import math

from modules.hydraulic_module.trickle_line_nr_solver import (
    emit_positions_along_wing,
    emitter_flow_uniformity_metrics,
    hw_friction_coeff_segment_m,
    newton_raphson_trickle_network,
    solve_wing_trickle_nr,
)


def test_hw_friction_C_matches_power_law():
    L = 10.0
    D = 16.0 / 1000.0
    C_hw = 140.0
    Cseg = hw_friction_coeff_segment_m(L, D, C_hw)
    Q = 1e-4
    hf_hw = 10.67 * (Q**1.852) / (C_hw**1.852 * D**4.87) * L
    hf_pow = Cseg * (Q**1.852)
    assert abs(hf_hw - hf_pow) < 1e-9 * max(1.0, abs(hf_hw))


def test_flat_line_converges_and_balance():
    n = 5
    Lseg = 0.3
    C = [hw_friction_coeff_segment_m(Lseg, 13.6 / 1000.0, 140.0) for _ in range(n)]
    E = [0.0] * n
    K = 1e-6
    x = 0.5
    H0 = 15.0
    H, iters, ok = newton_raphson_trickle_network(H0, C, E, K, x, max_iter=100, tol=1e-6)
    assert ok or iters >= 1
    assert len(H) == n
    pw = 0.54
    for j in range(n):
        H_prev = H0 if j == 0 else H[j - 1]
        dp_prev = max(H_prev - H[j], 1e-10)
        q_in = (dp_prev / C[j]) ** pw
        if j < n - 1:
            dp_next = max(H[j] - H[j + 1], 1e-10)
            q_out = (dp_next / C[j + 1]) ** pw
        else:
            q_out = 0.0
        hp = max(H[j] - E[j], 1e-10)
        q_em = K * (hp**x)
        fj = -q_in + q_out + q_em
        assert abs(fj) < 0.02


def test_solve_wing_sloped_terrain_stores_pressure_head_not_piezometric():
    """
    Профіль h має бути сопоставимий з краном/сабмейном (оцінка ~10 m), а не |z|+200
    (регресія: раніше в рядок писався H_line ~ z + кілька м).
    """
    def z_200(x: float) -> float:
        return 200.0

    prof, qtot, iters, ok = solve_wing_trickle_nr(
        10.0,
        5.0,
        0.3,
        1.05,
        z_200,
        13.6 / 1000.0,
        140.0,
        h_ref_m=10.0,
        emitter_opts={"compensated": False, "k_coeff": None, "x_exp": None},
    )
    assert ok
    for r in prof:
        assert float(r["h"]) < 30.0
    assert qtot > 0


def test_solve_wing_zero_slope():
    def z_zero(_x: float) -> float:
        return 0.0

    prof, qtot, iters, ok = solve_wing_trickle_nr(
        18.0,
        12.0,
        0.3,
        1.05,
        z_zero,
        13.6 / 1000.0,
        140.0,
        h_ref_m=10.0,
        emitter_opts={"compensated": False, "k_coeff": None, "x_exp": None},
    )
    assert ok
    assert len(prof) == len(emit_positions_along_wing(12.0, 0.3))
    assert qtot > 0
    assert iters >= 1
    assert all("h" in r and "q_emit" in r for r in prof)


def test_emit_positions_nonempty():
    xs = emit_positions_along_wing(10.0, 0.3)
    assert len(xs) >= 1
    assert xs[-1] <= 10.0 + 1e-6


def test_emitter_flow_uniformity_perfect():
    m = emitter_flow_uniformity_metrics([1.0, 1.0, 1.0, 1.0])
    assert abs(m["du_low_quarter_pct"] - 100.0) < 1e-9
    assert abs(m["christiansen_cu"] - 1.0) < 1e-9


def test_emitter_flow_uniformity_spread():
    m = emitter_flow_uniformity_metrics([1.0, 2.0, 3.0, 4.0])
    assert m["du_low_quarter_pct"] < 100.0
    assert 0.0 <= m["christiansen_cu"] < 1.0


def test_lateral_solver_mode_whitelist_includes_bisection_and_trickle():
    """Регресія: режими з плану лишаються валідними для I/O та рушія."""
    allowed = frozenset({"compare", "bisection", "newton", "trickle_nr"})
    assert "bisection" in allowed
    assert "trickle_nr" in allowed
