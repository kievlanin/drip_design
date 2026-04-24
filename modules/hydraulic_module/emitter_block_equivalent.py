"""
Еквівалентна модель поливного блоку як «одна крапельниця».

Припущення:
- Сталий (квазістаціонарний) режим.
- Некомпенсовані емітери описуються законом q = K * H^x.
- Для closed-form агрегації використовується спільний x для всіх емітерів.
- K_eq прив'язаний до обраного опорного напору p_ref і поточного профілю напорів.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Sequence, Union

_EPS = 1e-12


@dataclass(frozen=True)
class EquivalentEmitterModel:
    k_eq: float
    x: float
    p_ref_m: float
    meta: Dict[str, float] = field(default_factory=dict)


def _safe_head(h: float, *, clamp_nonpositive: bool) -> float:
    if clamp_nonpositive:
        return max(_EPS, float(h))
    return float(h)


def equivalent_k_at_ref(
    *,
    pressures: Sequence[float],
    k_each: Union[float, Sequence[float]],
    x: float,
    p_ref: float,
    clamp_nonpositive: bool = True,
) -> float:
    """Обчислити K_eq з K_i та локальних напорів P_i при спільному x."""
    if not pressures:
        return 0.0
    x_f = float(x)
    p_ref_eff = _safe_head(float(p_ref), clamp_nonpositive=clamp_nonpositive)
    if isinstance(k_each, (int, float)):
        k_values = [float(k_each)] * len(pressures)
    else:
        k_values = [float(v) for v in k_each]
        if len(k_values) != len(pressures):
            raise ValueError("k_each sequence must match pressures length")
    num = 0.0
    for k_i, p_i in zip(k_values, pressures):
        p_eff = _safe_head(float(p_i), clamp_nonpositive=clamp_nonpositive)
        num += float(k_i) * (p_eff**x_f)
    den = p_ref_eff**x_f
    if den <= _EPS:
        return 0.0
    return max(0.0, num / den)


def equivalent_k_from_total_flow(
    *,
    q_total: float,
    x: float,
    p_ref: float,
    clamp_nonpositive: bool = True,
) -> float:
    """
    Обчислити K_eq з сукупного виливу блоку при опорному напорі p_ref.

    Підходить, коли відомий лише Q_total у робочій точці.
    """
    q = max(0.0, float(q_total))
    x_f = float(x)
    p_ref_eff = _safe_head(float(p_ref), clamp_nonpositive=clamp_nonpositive)
    den = p_ref_eff**x_f
    if den <= _EPS:
        return 0.0
    return q / den


def block_flow_at_ref(k_eq: float, x: float, p_in: float) -> float:
    """Q(P_in) для еквівалентного емітера: q = K_eq * P^x."""
    p = max(0.0, float(p_in))
    return max(0.0, float(k_eq) * (p ** float(x)))


def mean_positive(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if float(v) > 0.0]
    if not vals:
        return None
    return sum(vals) / float(len(vals))
