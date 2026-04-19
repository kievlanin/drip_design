"""
1D lateral drip hydraulics: Hazen–Williams, backwards integration, shooting solvers.
No Shapely — safe to import without geometry dependencies (e.g. field calculator).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C


def hazen_williams_hloss_m(
    Q_m3s: float, L_m: float, D_m: float, C: float = DEFAULT_HAZEN_WILLIAMS_C
) -> float:
    if L_m <= 1e-12 or Q_m3s <= 1e-15 or D_m <= 1e-9:
        return 0.0
    return 10.67 * (Q_m3s**1.852) / (C**1.852) / (D_m**4.87) * L_m


def emitter_flow_lph(
    h_head_m: float,
    e_flow_nom_lph: float,
    h_ref_m: float = 10.0,
    *,
    compensated: bool = False,
    h_min_work_m: float = 1.0,
    k_coeff: Optional[float] = None,
    x_exp: Optional[float] = None,
    kd_coeff: Optional[float] = 1.0,
) -> float:
    """
    compensated=False: розрахунок за степеневим законом q = k * H^x (з kd-множником).
    Якщо k/x не задані явно, використовується сумісний fallback:
    x=0.5, k таке, щоб при H=10 м отримати e_flow_nom_lph.
    compensated=True: компенсована — номінальний л/год при H ≥ H_мін; нижче — лінійне зниження до 0.
    """
    if h_head_m <= 0:
        return 0.0
    try:
        kd = float(kd_coeff if kd_coeff is not None else 1.0)
    except (TypeError, ValueError):
        kd = 1.0
    if kd <= 1e-12:
        kd = 1.0
    if compensated:
        hm = max(1e-6, float(h_min_work_m))
        if h_head_m >= hm:
            return float(e_flow_nom_lph) * kd
        return float(e_flow_nom_lph) * kd * (float(h_head_m) / hm)
    h_pos = max(1e-9, h_head_m)
    # Основний шлях: завжди через k та x.
    k_eff = None
    x_eff = None
    if k_coeff is not None and x_exp is not None:
        try:
            k_eff = float(k_coeff)
            x_eff = float(x_exp)
        except (TypeError, ValueError):
            k_eff = None
            x_eff = None
    if k_eff is None or x_eff is None:
        # Сумісний fallback для старих даних без k/x:
        # прив'язка номіналу до канонічних 10 м без використання змінного H_ref.
        x_eff = 0.5
        k_eff = float(e_flow_nom_lph) / math.sqrt(10.0)
    return max(0.0, float(k_eff) * kd * (h_pos ** float(x_eff)))


def lph_to_m3s(q_lph: float) -> float:
    return (q_lph / 1000.0) / 3600.0


# Допуск «плоского» поля по напору рельєфу вздовж ланцюга (м вод. ст. / геометрична висота).
_FLAT_ELEV_SPAN_M = 0.05


def _hw_backwards_sweep_once(
    chain: List[float],
    M: int,
    has_emitter: List[bool],
    q_lph: List[float],
    H: List[float],
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float,
) -> None:
    """Один зворотний прохід HW + dz; оновлює H[0..M-1], H[-1] не змінює."""
    n_seg = len(chain) - 1
    strict_chain = n_seg > 0 and all(
        chain[j + 1] > chain[j] + 1e-12 for j in range(n_seg)
    )
    suffix_lph: List[float] = []
    if strict_chain:
        suffix_lph = [0.0] * n_seg
        acc = 0.0
        for k in range(len(chain) - 1, 0, -1):
            if has_emitter[k]:
                acc += q_lph[k]
            suffix_lph[k - 1] = acc
    for j in range(M - 1, -1, -1):
        x_lo, x_hi = chain[j], chain[j + 1]
        dx = x_hi - x_lo
        if dx < 1e-12:
            H[j] = H[j + 1]
            continue
        if strict_chain and j < len(suffix_lph):
            Q_lph = suffix_lph[j]
        else:
            Q_lph = sum(
                q_lph[k]
                for k in range(len(chain))
                if has_emitter[k] and chain[k] > x_lo + 1e-9
            )
        Q_m3s = lph_to_m3s(Q_lph)
        hf = hazen_williams_hloss_m(Q_m3s, dx, d_inner_m, C_hw)
        dz = z_at_x(x_lo) - z_at_x(x_hi)
        H[j] = H[j + 1] + hf + dz


def backwards_step_method(
    length: float,
    e_step: float,
    e_flow_lph: float,
    H_tip: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, List[dict]]:
    """
    Зворотний прохід від тупика (x = length) до врізки (x = 0).
    На кінці труби заданий напір H_tip (вода доходить до тупика з цим тиском після останнього емітера).

    Повертає: (H_connection, Q_total_m3s, nodes_tip_to_conn) — вузли для зворотного списку на графік.
    """
    if length < 0.1:
        return float(H_tip), 0.0, []

    N = max(1, int(length / e_step))
    raw_emit = sorted({round(min(i * e_step, length), 6) for i in range(1, N + 1)})
    # Вузол x=0 — це врізка; позиції, що округлюються до 0, не додаються в chain (умова x > 0),
    # тоді has_emitter лишається False для всіх k>0 і на графіку Q=0 при ненульовому H.
    _x_min_emit = 1e-5
    emit_positions = [p for p in raw_emit if p > _x_min_emit]
    if not emit_positions:
        x1 = min(float(e_step), float(length))
        if x1 > _x_min_emit:
            emit_positions = [round(x1, 6)]
        elif float(length) > _x_min_emit:
            emit_positions = [round(float(length), 6)]

    chain = [0.0]
    for x in emit_positions:
        if x > chain[-1] + 1e-9:
            chain.append(x)
    if length > chain[-1] + 1e-6:
        chain.append(length)

    M = len(chain) - 1
    has_emitter = []
    for k in range(len(chain)):
        he = k > 0 and any(abs(chain[k] - ex) < 1e-4 for ex in emit_positions)
        has_emitter.append(he)

    H = [0.0] * len(chain)
    q_lph = [0.0] * len(chain)
    H[-1] = max(1e-6, float(H_tip))

    z_conn = z_at_x(0.0)
    eo = emitter_opts or {}
    comp = bool(eo.get("compensated", False))
    h_min_e = float(eo.get("h_min_m", 1.0))
    k_e = eo.get("k_coeff")
    x_e = eo.get("x_exp")
    kd_e = eo.get("kd_coeff", 1.0)

    # Компенсована крапельниця: при H ≥ H_мін вилив = номінал (не залежить від H).
    # Не починаємо з q=0 — інакше зайві ітерації Пікара; q оновлюється лише в зоні H < H_мін.
    if comp:
        q0 = emitter_flow_lph(
            float(H[-1]),
            e_flow_lph,
            h_ref_m,
            compensated=True,
            h_min_work_m=h_min_e,
            k_coeff=k_e,
            x_exp=x_e,
            kd_coeff=kd_e,
        )
        for k in range(len(chain)):
            if has_emitter[k]:
                q_lph[k] = q0

    # Раніше було it >= 6 для будь-якого режиму — змушувало ганяти 7+ циклів навіть при сталому q.
    max_picard = 12 if comp else 30
    min_it_for_h_tol = 1 if comp else 3

    zs_chain = [z_at_x(float(xc)) for xc in chain]
    flat_elev = (max(zs_chain) - min(zs_chain)) <= _FLAT_ELEV_SPAN_M if zs_chain else True

    skip_picard = False
    if comp and flat_elev:
        H_fast = list(H)
        q_fast = list(q_lph)
        _hw_backwards_sweep_once(
            chain, M, has_emitter, q_fast, H_fast, z_at_x, d_inner_m, C_hw
        )
        for k in range(len(chain)):
            if has_emitter[k]:
                q_fast[k] = emitter_flow_lph(
                    H_fast[k],
                    e_flow_lph,
                    h_ref_m,
                    compensated=True,
                    h_min_work_m=h_min_e,
                    k_coeff=k_e,
                    x_exp=x_e,
                    kd_coeff=kd_e,
                )
        ok_fast = True
        for k in range(len(chain)):
            if not has_emitter[k]:
                continue
            if H_fast[k] < h_min_e - 0.02:
                ok_fast = False
                break
            qn = emitter_flow_lph(
                H_fast[k],
                e_flow_lph,
                h_ref_m,
                compensated=True,
                h_min_work_m=h_min_e,
                k_coeff=k_e,
                x_exp=x_e,
                kd_coeff=kd_e,
            )
            if abs(q_fast[k] - qn) > 1e-4:
                ok_fast = False
                break
        if ok_fast:
            for k in range(len(chain)):
                H[k] = H_fast[k]
                q_lph[k] = q_fast[k]
            skip_picard = True

    if not skip_picard:
        for it in range(max_picard):
            H_start = tuple(H)
            q_before = list(q_lph)
            _hw_backwards_sweep_once(
                chain, M, has_emitter, q_lph, H, z_at_x, d_inner_m, C_hw
            )
            for k in range(len(chain)):
                if has_emitter[k]:
                    q_lph[k] = emitter_flow_lph(
                        H[k],
                        e_flow_lph,
                        h_ref_m,
                        compensated=comp,
                        h_min_work_m=h_min_e,
                        k_coeff=k_e,
                        x_exp=x_e,
                        kd_coeff=kd_e,
                    )
            if comp and it >= 1:
                dqmx = max(
                    (
                        abs(q_lph[k] - q_before[k])
                        for k in range(len(chain))
                        if has_emitter[k]
                    ),
                    default=0.0,
                )
                if dqmx < 1e-8:
                    break
            if it >= min_it_for_h_tol and max(
                abs(H[j] - H_start[j]) for j in range(len(H))
            ) < 1e-5:
                break

    H_conn = H[0]
    Q_total_m3s = lph_to_m3s(sum(q_lph[k] for k in range(len(chain)) if has_emitter[k]))

    # Q у трубі після вузла idx (до тупика): сума виливів на k > idx. Для графіка Q(s) один масштаб.
    # Окремо q_emit — вилив з емітера (л/год), інакше на графіку сумарна ~300 л/г затискає одиничні ~1 л/г до осі.
    q_pipe_at = [0.0] * len(chain)
    acc_p = 0.0
    for k in range(len(chain) - 1, 0, -1):
        if has_emitter[k]:
            acc_p += q_lph[k]
        q_pipe_at[k - 1] = acc_p

    nodes_rev = []
    for idx in range(len(chain) - 1, -1, -1):
        x = chain[idx]
        el = z_at_x(x) - z_conn
        q_pipe = float(q_pipe_at[idx])
        q_em = float(q_lph[idx]) if has_emitter[idx] else 0.0
        nodes_rev.append(
            {
                "x": round(x, 4),
                "h": round(H[idx], 4),
                "q": round(q_pipe, 4),
                "q_emit": round(q_em, 4),
                "elev": round(el, 4),
            }
        )

    return H_conn, Q_total_m3s, nodes_rev


def wing_profile_from_backwards_nodes(nodes_rev: List[dict]) -> List[dict]:
    """Формат профілю крила з уже порахованого зворотного проходу (без другого виклику backwards)."""
    out = []
    for row in reversed(nodes_rev):
        out.append(
            {
                "x": float(row["x"]),
                "h": round(float(row["h"]), 2),
                "q": round(float(row["q"]), 2),
                "q_emit": round(float(row.get("q_emit", 0.0)), 2),
                "elev": round(float(row["elev"]), 3),
            }
        )
    return out


def approx_wing_q_m3s_nominal(
    length_m: float,
    e_step_m: float,
    e_flow_lph: float,
    h_connection_m: float,
    h_ref_m: float = 10.0,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Швидка оцінка сумарної витрати крила без зворотного HW і бісекції.
    Використовується лише для розподілу Q(s) на сабмейні перед другим проходом;
    фінальний профіль латераля знову рахується повним shooting + backwards.
    """
    if length_m < 0.1 or e_step_m <= 1e-12:
        return 0.0
    n = max(0, int(length_m / e_step_m))
    if n <= 0:
        return 0.0
    eo = emitter_opts or {}
    q_e = emitter_flow_lph(
        max(0.05, float(h_connection_m)),
        e_flow_lph,
        h_ref_m,
        compensated=bool(eo.get("compensated", False)),
        h_min_work_m=float(eo.get("h_min_m", 1.0)),
        k_coeff=eo.get("k_coeff"),
        x_exp=eo.get("x_exp"),
        kd_coeff=eo.get("kd_coeff", 1.0),
    )
    return lph_to_m3s(n * q_e)


def _error_vs_target(
    H_tip: float,
    H_target: float,
    length: float,
    e_step: float,
    e_flow_lph: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float,
    h_ref_m: float,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> float:
    h0, _, _ = backwards_step_method(
        length,
        e_step,
        e_flow_lph,
        H_tip,
        z_at_x,
        d_inner_m,
        C_hw,
        h_ref_m,
        emitter_opts=emitter_opts,
    )
    return h0 - H_target


def try_compensated_affine_tip(
    H_sub_target: float,
    length: float,
    e_step: float,
    e_flow_lph: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    *,
    emitter_opts: Optional[Dict[str, Any]] = None,
    tol_m: float = 0.05,
) -> Optional[Tuple[float, int, List[dict], float]]:
    """
    Компенсована крапельниця при H ≥ H_мін: вилив постійний → HW-втрати не залежать від абсолютного H_tip,
    H_біля врізки = H_tip + K (K стале для фіксованого q). Два зворотні проходи замість бісекції.

    Успіх: (H_tip, iters, nodes_rev, Q_wing_m3s) — nodes_rev з останнього зворотного (не третій виклик у ядрі).
    Якщо на крилі є зона H < H_мін (q залежить від H), перевірка не зійдеться — повертається None.
    """
    eo = emitter_opts or {}
    if not bool(eo.get("compensated")) or length < 0.1:
        return None
    A = max(0.5, min(float(H_sub_target), 35.0))
    ha, _, _ = backwards_step_method(
        length,
        e_step,
        e_flow_lph,
        A,
        z_at_x,
        d_inner_m,
        C_hw,
        h_ref_m,
        emitter_opts=eo,
    )
    h_tip = float(H_sub_target) - (float(ha) - A)
    if h_tip < 0.05:
        h_tip = 0.05
    h_check, q_tot, nodes2 = backwards_step_method(
        length,
        e_step,
        e_flow_lph,
        h_tip,
        z_at_x,
        d_inner_m,
        C_hw,
        h_ref_m,
        emitter_opts=eo,
    )
    if abs(float(h_check) - float(H_sub_target)) <= max(float(tol_m), 0.03):
        return (h_tip, 1, nodes2, float(q_tot))
    return None


def solve_lateral_shooting_bisection(
    H_target: float,
    length: float,
    e_step: float,
    e_flow_lph: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    tol_m: float = 0.01,
    max_iter: int = 80,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> Tuple[float, int]:
    if length < 0.1:
        return max(0.0, H_target), 0

    H_hi = max(float(H_target), 15.0, 1.0)

    def f(ht: float) -> float:
        return _error_vs_target(
            ht,
            H_target,
            length,
            e_step,
            e_flow_lph,
            z_at_x,
            d_inner_m,
            C_hw,
            h_ref_m,
            emitter_opts,
        )

    f_lo = f(0.0)
    f_hi = f(H_hi)
    expand = 0
    while f_hi < 0 and expand < 30:
        H_hi *= 1.6
        f_hi = f(H_hi)
        expand += 1

    if f_lo >= 0:
        return 0.0, 0
    if f_hi < 0:
        return H_hi, max_iter

    a, b = 0.0, H_hi
    it = 0
    while (b - a) > tol_m and it < max_iter:
        it += 1
        mid = 0.5 * (a + b)
        fm = f(mid)
        if fm > 0:
            b = mid
        else:
            a = mid
    return 0.5 * (a + b), it


def solve_lateral_newton_raphson(
    H_target: float,
    length: float,
    e_step: float,
    e_flow_lph: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    tol_m: float = 0.001,
    max_iter: int = 50,
    delta_h: float = 0.001,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> Tuple[float, int]:
    h_curr = max(0.05, float(H_target) * 0.85)
    it = 0
    for _ in range(max_iter):
        it += 1
        h_start1, _, _ = backwards_step_method(
            length,
            e_step,
            e_flow_lph,
            h_curr,
            z_at_x,
            d_inner_m,
            C_hw,
            h_ref_m,
            emitter_opts=emitter_opts,
        )
        err = h_start1 - H_target
        if abs(err) < tol_m:
            return h_curr, it
        h_start2, _, _ = backwards_step_method(
            length,
            e_step,
            e_flow_lph,
            h_curr + delta_h,
            z_at_x,
            d_inner_m,
            C_hw,
            h_ref_m,
            emitter_opts=emitter_opts,
        )
        deriv = (h_start2 - h_start1) / delta_h
        if abs(deriv) < 1e-12:
            break
        h_next = h_curr - err / deriv
        if h_next < 0:
            h_next = h_curr * 0.5
        if abs(h_next - h_curr) < tol_m * 0.05:
            return h_next, it
        h_curr = h_next
    return h_curr, it


def build_wing_data_from_tip(
    H_tip_final: float,
    length: float,
    e_step: float,
    e_flow_lph: float,
    z_at_x: Callable[[float], float],
    d_inner_m: float,
    C_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    h_ref_m: float = 10.0,
    emitter_opts: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    _, _, nodes_rev = backwards_step_method(
        length,
        e_step,
        e_flow_lph,
        H_tip_final,
        z_at_x,
        d_inner_m,
        C_hw,
        h_ref_m,
        emitter_opts=emitter_opts,
    )
    return wing_profile_from_backwards_nodes(nodes_rev)
