"""
Багатовузловий Ньютон–Рафсон для лінії крапельного зрошення:
рівняння безперервності з q_seg = (Δh / C)^0.54 (узгоджено з інверсією Hazen–Williams),
вилив q_emit = K * max(H − E, 0)^x, де H — лінія напору (вод. ст.), E — висота рельєфу.

Відмінно від solve_lateral_newton_raphson у lateral_drip_core: там 1D shooting для H_тупика
поверх зворотного HW; тут одночасно вирішуються напори у всіх вузлах випуску.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C
from .lateral_drip_core import lph_to_m3s

# Показник у q = (Δh/C)^pw відповідає 1/1.852 (Hazen–Williams по Q).
_PW = 0.54
_MIN_DP = 1e-10
_MIN_HP = 1e-9


def emitter_flow_uniformity_metrics(
    q_values: Sequence[float],
) -> Dict[str, float]:
    """
    q_values — витрати емітерів у довільних, але однакових одиницях (наприклад л/год).

    Повертає:
      du_low_quarter_pct — Statistical uniformity (низька чверть): середнє найнижчих 25 %
        поділене на середнє всіх, у відсотках (ASABE).
      christiansen_cu — коефіцієнт рівномірності Крістіансена: 1 − Σ|qi−q̄|/(n·q̄).
    """
    vals = [float(q) for q in q_values if float(q) > 1e-18]
    n = len(vals)
    if n < 2:
        return {"du_low_quarter_pct": 100.0, "christiansen_cu": 1.0}
    mean_q = sum(vals) / n
    if mean_q <= 1e-18:
        return {"du_low_quarter_pct": 0.0, "christiansen_cu": 0.0}
    sorted_v = sorted(vals)
    k_lq = max(1, int(math.ceil(0.25 * n)))
    mean_lq = sum(sorted_v[:k_lq]) / k_lq
    du_pct = 100.0 * mean_lq / mean_q
    dev = sum(abs(v - mean_q) for v in vals)
    cu = 1.0 - dev / (n * mean_q)
    cu = max(0.0, min(1.0, cu))
    return {"du_low_quarter_pct": float(du_pct), "christiansen_cu": float(cu)}


def hw_friction_coeff_segment_m(L_m: float, D_m: float, C_hw: float) -> float:
    """Сегментний коефіцієнт C: Δh [м] = C * Q^1.852, Q [м³/с] → Q = (Δh/C)^0.54."""
    L_m = float(L_m)
    D_m = float(D_m)
    C_hw = float(C_hw)
    if L_m <= 1e-15 or D_m <= 1e-12 or C_hw <= 1e-9:
        return 1e12
    return 10.67 * L_m / (C_hw**1.852 * D_m**4.87)


def emit_positions_along_wing(length_m: float, e_step_m: float) -> List[float]:
    """Позиції емітерів від врізки (м), узгоджено з backwards_step_method."""
    length_m = float(length_m)
    if length_m < 0.1:
        return []
    es = max(float(e_step_m), 1e-9)
    n = max(1, int(length_m / es))
    raw_emit = sorted({round(min(i * es, length_m), 6) for i in range(1, n + 1)})
    x_min = 1e-5
    out = [p for p in raw_emit if p > x_min]
    if not out:
        x1 = min(es, length_m)
        if x1 > x_min:
            out = [round(x1, 6)]
        elif length_m > x_min:
            out = [round(length_m, 6)]
    return out


def emitter_K_m3s_and_x(
    e_flow_lph: float,
    h_ref_m: float = 10.0,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float]:
    """K у q_m3s = K * h_p^x, h_p — напір на крапельницю (м)."""
    eo = emitter_opts or {}
    k_e = eo.get("k_coeff")
    x_e = eo.get("x_exp")
    try:
        kd = float(eo.get("kd_coeff", 1.0) or 1.0)
    except (TypeError, ValueError):
        kd = 1.0
    if kd <= 1e-12:
        kd = 1.0
    if k_e is not None and x_e is not None:
        try:
            kf = float(k_e) * kd
            xf = float(x_e)
            return max(lph_to_m3s(kf), 1e-18), xf
        except (TypeError, ValueError):
            pass
    xf = 0.5
    kf = float(e_flow_lph) / math.sqrt(10.0) * kd
    return max(lph_to_m3s(kf), 1e-18), xf


def _thomas_solve(
    lower: List[float], diag: List[float], upper: List[float], rhs: List[float]
) -> List[float]:
    n = len(diag)
    bp = list(diag)
    dp = list(rhs)
    for i in range(1, n):
        piv = bp[i - 1]
        if abs(piv) < 1e-22:
            piv = 1e-22 if piv >= 0 else -1e-22
        m = lower[i] / piv
        bp[i] -= m * upper[i - 1]
        dp[i] -= m * dp[i - 1]
    piv = bp[-1]
    if abs(piv) < 1e-22:
        piv = 1e-22 if piv >= 0 else -1e-22
    x = [0.0] * n
    x[-1] = dp[-1] / piv
    for i in range(n - 2, -1, -1):
        piv = bp[i]
        if abs(piv) < 1e-22:
            piv = 1e-22 if piv >= 0 else -1e-22
        x[i] = (dp[i] - upper[i] * x[i + 1]) / piv
    return x


def newton_raphson_trickle_network(
    H0: float,
    C_segments: List[float],
    E_nodes: List[float],
    K_m3s: float,
    x_exp: float,
    *,
    max_iter: int = 120,
    tol: float = 1e-5,
    relax: float = 0.65,
) -> Tuple[List[float], int, bool]:
    """
    H0 — повний гідравлічний напір на врізці (p/ρg+z у датумі E), той самий датум, що й E_nodes.
    Практично: H0 = h_врізка_робочий + z(0), де h_врізка — з профіля сабмейна (м вод. ст.).
    Невідомі H[0..N-1] — лінійні напори в вузлах випуску.
    C_segments[k] — опір сегмента, що входить у вузол k (від H0 або H[k-1] до H[k]).
    E_nodes[k] — z рельєфу у вузлі k.
    """
    n = len(E_nodes)
    if n == 0:
        return [], 0, True
    if len(C_segments) != n:
        raise ValueError("len(C_segments) має дорівнювати кількості вузлів N")
    # Початок: легкий спад напору вздовж лінії (рівномірне H0 дає нульові Δh між вузлами).
    H = []
    for j in range(n):
        frac = (j + 1) / max(n, 1)
        hj = max(E_nodes[j] + 0.25, float(H0) * (1.0 - 0.18 * frac))
        H.append(hj)
    pw = _PW
    K = max(float(K_m3s), 1e-18)
    x_e = max(float(x_exp), 1e-6)
    H0 = float(H0)
    rlx = max(0.05, min(1.0, float(relax)))
    converged = False
    last_it = 0

    for it in range(int(max_iter)):
        last_it = it + 1
        f: List[float] = []
        ld: List[float] = []
        ddiag: List[float] = []
        ud: List[float] = []

        for j in range(n):
            H_prev = H0 if j == 0 else H[j - 1]
            Hj = H[j]
            dp_prev = max(H_prev - Hj, _MIN_DP)
            Cj = max(C_segments[j], 1e-18)
            q_in = (dp_prev / Cj) ** pw

            if j < n - 1:
                H_next = H[j + 1]
                dp_next = max(Hj - H_next, _MIN_DP)
                Cn = max(C_segments[j + 1], 1e-18)
                q_out = (dp_next / Cn) ** pw
            else:
                dp_next = _MIN_DP
                q_out = 0.0

            hp = max(Hj - E_nodes[j], _MIN_HP)
            q_emit = K * (hp**x_e)
            fj = -q_in + q_out + q_emit
            f.append(fj)

            # ∂f/∂H[j-1] (нижня діагональ), ∂f/∂H[j+1] (верхня)
            df_d_prev = 0.0
            if j > 0:
                df_d_prev = -(pw / Cj) * ((dp_prev / Cj) ** (pw - 1.0))
            df_d_next = 0.0
            if j < n - 1:
                Cn = max(C_segments[j + 1], 1e-18)
                df_d_next = -(pw / Cn) * ((dp_next / Cn) ** (pw - 1.0))

            qin_diag = (pw / Cj) * ((dp_prev / Cj) ** (pw - 1.0))
            qout_diag = 0.0
            if j < n - 1:
                Cn = max(C_segments[j + 1], 1e-18)
                qout_diag = (pw / Cn) * ((dp_next / Cn) ** (pw - 1.0))

            em_deriv = K * x_e * (hp ** (x_e - 1.0))
            dj = qin_diag + qout_diag + em_deriv
            ddiag.append(dj)
            ld.append(df_d_prev)
            ud.append(df_d_next)

        ld[0] = 0.0
        if n > 0:
            ud[-1] = 0.0

        try:
            Z = _thomas_solve(ld, ddiag, ud, f)
        except (ZeroDivisionError, ValueError):
            break

        zm = max(abs(z) for z in Z) if Z else 0.0
        for j in range(n):
            H[j] = H[j] - rlx * Z[j]
        # Фізично напір уздовж латераля не зростає від врізки до тупика (без насосів на лінії).
        cap = float(H0) - 1e-4
        for j in range(n):
            if j == 0:
                H[j] = min(H[j], cap)
            else:
                H[j] = min(H[j], H[j - 1] - 1e-4)
            H[j] = max(H[j], E_nodes[j] + _MIN_HP)

        fn = max(abs(fj) for fj in f) if f else 0.0
        if zm < float(tol) or fn < 1e-5:
            converged = True
            break

    return H, last_it, converged


def solve_wing_trickle_nr(
    H_connection_m: float,
    length_m: float,
    e_step_m: float,
    e_flow_lph: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    emitter_opts: Optional[Dict[str, Any]] = None,
    *,
    max_iter: int = 150,
    tol: float = 1e-5,
) -> Tuple[List[dict], float, int, bool]:
    """
    Один крило латераля: врізка x=0, тупик у довжину length_m.
    Повертає (profile_rows, Q_total_m3s, newton_iters, converged).
    profile_rows: поле h — **напір на крапельницю (м вод. ст.)** = H_line − z_рельєф, узгоджено
    з бісекцією/зворотним HW (не п'єзометрика p/ρg+z у вузлі, щоб H відповідав крану/сабмейну).
    """
    length_m = float(length_m)
    if length_m < 0.1:
        return [], 0.0, 0, True

    xs = emit_positions_along_wing(length_m, e_step_m)
    if not xs:
        return [], 0.0, 0, True

    z_conn = float(z_at_x(0.0))
    n = len(xs)
    E_nodes = [float(z_at_x(xs[j])) for j in range(n)]

    seg_len: List[float] = []
    for j in range(n):
        x_prev = 0.0 if j == 0 else xs[j - 1]
        Lseg = xs[j] - x_prev
        if j == n - 1 and length_m > xs[-1] + 1e-9:
            Lseg += length_m - xs[-1]
        seg_len.append(max(Lseg, 1e-9))

    C_seg = [
        hw_friction_coeff_segment_m(L, float(d_inner_m), float(C_hw)) for L in seg_len
    ]

    K_m3s, x_e = emitter_K_m3s_and_x(e_flow_lph, h_ref_m, emitter_opts)

    H0_line = float(H_connection_m) + z_conn
    H_nodes, iters, ok = newton_raphson_trickle_network(
        H0_line,
        C_seg,
        E_nodes,
        K_m3s,
        x_e,
        max_iter=max_iter,
        tol=tol,
    )

    q_emit_m3s: List[float] = []
    for j in range(n):
        hp = max(H_nodes[j] - E_nodes[j], 0.0)
        q_emit_m3s.append(K_m3s * (max(hp, _MIN_HP) ** x_e))

    Q_total = sum(q_emit_m3s)

    q_pipe_downstream: List[float] = []
    acc = 0.0
    for j in range(n - 1, -1, -1):
        acc += q_emit_m3s[j]
        q_pipe_downstream.append(acc)
    q_pipe_downstream.reverse()

    profile: List[dict] = []
    for j in range(n):
        x = xs[j]
        el = E_nodes[j] - z_conn
        h_press = max(H_nodes[j] - E_nodes[j], 0.0)
        q_em_lph = q_emit_m3s[j] * 1000.0 * 3600.0
        q_pipe_lph = q_pipe_downstream[j] * 1000.0 * 3600.0
        profile.append(
            {
                "x": round(x, 4),
                "h": round(h_press, 2),
                "q": round(q_pipe_lph, 2),
                "q_emit": round(q_em_lph, 4),
                "elev": round(el, 3),
            }
        )

    return profile, float(Q_total), int(iters), bool(ok)


__all__ = [
    "emit_positions_along_wing",
    "emitter_flow_uniformity_metrics",
    "emitter_K_m3s_and_x",
    "hw_friction_coeff_segment_m",
    "newton_raphson_trickle_network",
    "solve_wing_trickle_nr",
]
