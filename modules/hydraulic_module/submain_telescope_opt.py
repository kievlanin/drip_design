"""
Підбір діаметрів сабмейну (телескоп) за відомою витратою по ділянках і бюджетом втрат напору.

Приклад: біля насоса 30 м вод. ст., біля дальнього поля потрібно ≥ 20 м —
на лінійні втрати + різницю рельєфу лишається бюджет ≈ 10 м (якщо dz задані окремо).

Вартість у базі труб немає — використовується індекс вартості (монотонний по DN і PN)
для «не дорого»: спершу мінімальні прийнятні діаметри, потім локальне потовщення там,
де найбільше знімає втрати на одиницю індексу.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import lateral_drip_core as lat
from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C


def _default_pipes_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "pipes_db.json"


def load_pipes_db(path: Optional[Path] = None) -> dict:
    p = path or _default_pipes_db_path()
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _sku_cost_index(d_nom_mm: float, pn_str: str) -> float:
    try:
        pn = float(str(pn_str).replace(",", "."))
    except ValueError:
        pn = 6.0
    return (float(d_nom_mm) ** 1.25) * (1.0 + 0.03 * max(pn, 4.0))


@dataclass(frozen=True)
class PipeSKU:
    material: str
    pn: str
    d_nom_mm: float
    d_inner_mm: float
    cost_index_per_m: float


@dataclass
class TelescopeSegment:
    length_m: float
    q_m3s: float
    dz_m: float = 0.0


@dataclass
class SegmentPick:
    segment_index: int
    sku: PipeSKU
    hf_m: float
    v_m_s: float


@dataclass
class TelescopeOptimizeResult:
    picks: List[SegmentPick]
    total_hf_m: float
    total_dz_m: float
    friction_budget_m: float
    cost_index_total: float
    feasible: bool
    message: str = ""


def build_sku_list(
    pipes_db: dict,
    material: str,
    allowed_pipes: Optional[dict] = None,
) -> List[PipeSKU]:
    mat = pipes_db.get(material) or {}
    skus: List[PipeSKU] = []
    for pn_str, by_nom in mat.items():
        if not isinstance(by_nom, dict):
            continue
        allow_dns = None
        if allowed_pipes and material in allowed_pipes:
            allow_dns = allowed_pipes[material].get(str(pn_str))
        for d_key, pdata in by_nom.items():
            if allow_dns is not None and str(d_key) not in [str(x) for x in allow_dns]:
                continue
            d_nom = float(d_key)
            if isinstance(pdata, dict):
                d_inner = float(pdata.get("id", d_nom))
            else:
                d_inner = d_nom
            skus.append(
                PipeSKU(
                    material=material,
                    pn=str(pn_str),
                    d_nom_mm=d_nom,
                    d_inner_mm=d_inner,
                    cost_index_per_m=_sku_cost_index(d_nom, pn_str),
                )
            )
    skus.sort(key=lambda s: (s.d_inner_mm, s.pn, s.d_nom_mm))
    return skus


def _velocity_m_s(q_m3s: float, d_inner_m: float) -> float:
    if d_inner_m <= 1e-9:
        return 999.0
    a = math.pi * (d_inner_m * 0.5) ** 2
    return q_m3s / a if a > 1e-12 else 999.0


def _choices_for_segment(
    q_m3s: float,
    all_skus: Sequence[PipeSKU],
    v_max_m_s: float,
) -> List[PipeSKU]:
    """Допустимі SKU з v ≤ v_max; унікалізуємо по d_inner (беремо найдешевший індекс на розмір)."""
    by_inner: Dict[float, PipeSKU] = {}
    for sku in all_skus:
        d_m = sku.d_inner_mm / 1000.0
        v = _velocity_m_s(q_m3s, d_m)
        if v > v_max_m_s + 1e-4:
            continue
        prev = by_inner.get(sku.d_inner_mm)
        if prev is None or sku.cost_index_per_m < prev.cost_index_per_m:
            by_inner[sku.d_inner_mm] = sku
    if not by_inner:
        sku_max = max(all_skus, key=lambda s: s.d_inner_mm)
        return [sku_max]
    return sorted(by_inner.values(), key=lambda s: s.d_inner_mm)


def _hf(seg: TelescopeSegment, sku: PipeSKU, c_hw: float) -> float:
    d_m = sku.d_inner_mm / 1000.0
    return lat.hazen_williams_hloss_m(seg.q_m3s, seg.length_m, d_m, c_hw)


def _total_hf(segments: List[TelescopeSegment], skus: List[PipeSKU], c_hw: float) -> float:
    return sum(_hf(s, k, c_hw) for s, k in zip(segments, skus))


def _total_cost(segments: List[TelescopeSegment], skus: List[PipeSKU]) -> float:
    return sum(s.length_m * k.cost_index_per_m for s, k in zip(segments, skus))


def optimize_submain_telescope(
    segments: List[TelescopeSegment],
    h_inlet_m: float,
    h_end_min_m: float,
    *,
    pipes_db: Optional[dict] = None,
    material: str = "PVC",
    allowed_pipes: Optional[dict] = None,
    c_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
    v_max_m_s: float = 2.5,
    pipes_db_path: Optional[Path] = None,
) -> TelescopeOptimizeResult:
    """
    h_inlet_m — напір біля насоса / початку сабмейну (м вод. ст.).
    h_end_min_m — мінімально потрібний напір в кінці (м вод. ст.).
    dz_m по сегментах — споживання напору на рельєф (+ угору вздовж потоку).
    Бюджет на втрати тертя: h_inlet - h_end_min - sum(dz).
    """
    if not segments:
        return TelescopeOptimizeResult(
            picks=[],
            total_hf_m=0.0,
            total_dz_m=0.0,
            friction_budget_m=0.0,
            cost_index_total=0.0,
            feasible=True,
            message="Немає сегментів.",
        )
    for s in segments:
        if s.length_m < 0 or s.q_m3s < 0:
            raise ValueError("Довжина та витрата сегмента не можуть бути від'ємними.")

    db = pipes_db if pipes_db is not None else load_pipes_db(pipes_db_path)
    all_skus = build_sku_list(db, material, allowed_pipes)
    if not all_skus:
        raise ValueError(f"Каталог труб порожній для матеріалу «{material}».")

    sum_dz = sum(s.dz_m for s in segments)
    friction_budget = float(h_inlet_m) - float(h_end_min_m) - sum_dz
    if friction_budget <= 0:
        return TelescopeOptimizeResult(
            picks=[],
            total_hf_m=0.0,
            total_dz_m=sum_dz,
            friction_budget_m=friction_budget,
            cost_index_total=0.0,
            feasible=False,
            message="Бюджет втрат напору ≤ 0: збільшіть тиск біля насоса, зменшите вимогу в кінці або рельєф.",
        )

    choices: List[List[PipeSKU]] = [_choices_for_segment(s.q_m3s, all_skus, v_max_m_s) for s in segments]
    idx = [0] * len(segments)
    for i, ch in enumerate(choices):
        if not ch:
            idx[i] = 0
        else:
            idx[i] = 0

    def assignment() -> List[PipeSKU]:
        return [choices[i][idx[i]] for i in range(len(segments))]

    def upgrade_best() -> bool:
        cur_skus = assignment()
        cur_hf = _total_hf(segments, cur_skus, c_hw)
        if cur_hf <= friction_budget + 1e-4:
            return False
        best_i = -1
        best_score = -1.0
        for i in range(len(segments)):
            if idx[i] + 1 >= len(choices[i]):
                continue
            sk0 = choices[i][idx[i]]
            sk1 = choices[i][idx[i] + 1]
            dhf = _hf(segments[i], sk0, c_hw) - _hf(segments[i], sk1, c_hw)
            dcost = segments[i].length_m * (sk1.cost_index_per_m - sk0.cost_index_per_m)
            if dhf <= 1e-9 or dcost <= 0:
                continue
            score = dhf / dcost
            if score > best_score:
                best_score = score
                best_i = i
        if best_i < 0:
            return False
        idx[best_i] += 1
        return True

    max_upgrades = sum(max(0, len(c) - 1) for c in choices) * 2 + len(segments) + 10
    n_up = 0
    while _total_hf(segments, assignment(), c_hw) > friction_budget + 1e-4 and n_up < max_upgrades:
        if not upgrade_best():
            cur_hf = _total_hf(segments, assignment(), c_hw)
            return TelescopeOptimizeResult(
                picks=[],
                total_hf_m=cur_hf,
                total_dz_m=sum_dz,
                friction_budget_m=friction_budget,
                cost_index_total=0.0,
                feasible=False,
                message=(
                    f"Не вистачає бюджету втрат: потрібно зняти ΔH_friction ≈ {cur_hf:.2f} м при бюджеті {friction_budget:.2f} м. "
                    "Збільшіть діаметри в каталозі, v_max або тиск біля насоса."
                ),
            )
        n_up += 1

    final_skus = assignment()
    thf = _total_hf(segments, final_skus, c_hw)
    tcost = _total_cost(segments, final_skus)
    picks = []
    for i, (seg, sku) in enumerate(zip(segments, final_skus)):
        d_m = sku.d_inner_mm / 1000.0
        picks.append(
            SegmentPick(
                segment_index=i,
                sku=sku,
                hf_m=_hf(seg, sku, c_hw),
                v_m_s=_velocity_m_s(seg.q_m3s, d_m),
            )
        )
    slack = friction_budget - thf
    msg = (
        f"Сумарні втрати HW: {thf:.3f} м при бюджеті {friction_budget:.3f} м "
        f"(запас {slack:.3f} м). Індекс вартості (умовний): {tcost:.1f}."
    )
    return TelescopeOptimizeResult(
        picks=picks,
        total_hf_m=thf,
        total_dz_m=sum_dz,
        friction_budget_m=friction_budget,
        cost_index_total=tcost,
        feasible=True,
        message=msg,
    )


__all__ = [
    "PipeSKU",
    "SegmentPick",
    "TelescopeOptimizeResult",
    "TelescopeSegment",
    "build_sku_list",
    "load_pipes_db",
    "optimize_submain_telescope",
]
