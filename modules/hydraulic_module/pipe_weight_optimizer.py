"""
Оптимізація труб за мінімальною вагою при бюджеті втрат напору (ΔH, Hazen–Williams).

Обмеження швидкості потоку опційне: у OptimizationConstraints max_velocity_m_s <= 0
означає, що діаметри підбираються лише за ΔH і каталогом (швидкість не фільтрує кандидатів).

Модуль не прив'язаний до UI і працює з готовими сегментами/каталогом.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C
from .lateral_drip_core import hazen_williams_hloss_m


@dataclass(frozen=True)
class PipeOption:
    material: str
    pn: str
    d_nom_mm: float
    d_inner_mm: float
    c_hw: float = DEFAULT_HAZEN_WILLIAMS_C
    weight_kg_m: float = 0.0
    price_per_m: float = 0.0


@dataclass(frozen=True)
class SegmentDemand:
    id: str
    length_m: float
    q_m3s: float
    dz_m: float = 0.0
    min_length_m: float = 0.0
    allowed_d_nom_mm: Optional[Tuple[float, ...]] = None


@dataclass(frozen=True)
class OptimizationConstraints:
    max_head_loss_m: float
    # ≤ 0 — обмеження швидкості не застосовується (підбір лише за ΔH та вагою/каталогом).
    max_velocity_m_s: float = 0.0
    min_segment_length_m: float = 0.0
    max_active_segments: int = 2
    objective: str = "weight"
    # > 0 — після підбору довжини секцій округлюються до кроку (м) і повторно перевіряється ΔH.
    length_round_step_m: float = 0.0


@dataclass(frozen=True)
class SegmentChoice:
    segment_id: str
    d_nom_mm: float
    d_inner_mm: float
    pn: str
    material: str
    head_loss_m: float
    velocity_m_s: float
    weight_kg: float
    objective_cost: float = 0.0


@dataclass(frozen=True)
class AllocationChoice:
    d_nom_mm: float
    d_inner_mm: float
    material: str
    pn: str
    length_m: float
    head_loss_m: float
    velocity_m_s: float
    weight_kg: float
    objective_cost: float = 0.0


@dataclass(frozen=True)
class OptimizationResult:
    feasible: bool
    message: str
    total_weight_kg: float
    total_head_loss_m: float
    total_objective_cost: float = 0.0
    choices: Tuple[SegmentChoice, ...] = ()
    allocations: Tuple[AllocationChoice, ...] = ()


def _normalized_objective_name(name: str) -> str:
    n = str(name or "weight").strip().lower()
    if n not in ("weight", "money"):
        return "weight"
    return n


def _option_objective_cost_per_m(opt: PipeOption, objective: str) -> float:
    objective = _normalized_objective_name(objective)
    if objective == "money":
        if float(opt.price_per_m) > 1e-12:
            return float(opt.price_per_m)
    return float(opt.weight_kg_m)


def estimate_weight_kg_m(d_nom_mm: float, pn: str) -> float:
    """
    Якщо вага відсутня в БД, оцінка за зовнішнім діаметром та PN.
    Дає монотонний ріст із діаметром/тиском, чого достатньо для оптимізації.
    """
    try:
        pn_f = float(str(pn).replace(",", "."))
    except ValueError:
        pn_f = 6.0
    return 0.0012 * float(d_nom_mm) * (1.0 + 0.06 * max(0.0, pn_f - 4.0))


def _velocity_m_s(q_m3s: float, d_inner_mm: float) -> float:
    d_m = float(d_inner_mm) / 1000.0
    if d_m <= 1e-9:
        return 999.0
    area = 3.141592653589793 * (d_m * 0.5) ** 2
    return q_m3s / area if area > 1e-12 else 999.0


def _hf_m(q_m3s: float, length_m: float, d_inner_mm: float, c_hw: float) -> float:
    return hazen_williams_hloss_m(q_m3s, length_m, float(d_inner_mm) / 1000.0, c_hw)


def _allocations_from_lengths(
    parts: Sequence[Tuple[PipeOption, float]],
    q_m3s: float,
    objective: str,
) -> Tuple[float, float, float, Tuple[AllocationChoice, ...]]:
    """Повертає (total_weight, total_hf, total_obj, allocations)."""
    objective = _normalized_objective_name(objective)
    allocs: List[AllocationChoice] = []
    tw = hf = obj = 0.0
    for opt, ln in parts:
        if ln <= 1e-12:
            continue
        hfi = _hf_m(q_m3s, ln, opt.d_inner_mm, opt.c_hw)
        wi = opt.weight_kg_m * ln
        oi = _option_objective_cost_per_m(opt, objective) * ln
        hf += hfi
        tw += wi
        obj += oi
        allocs.append(
            AllocationChoice(
                d_nom_mm=opt.d_nom_mm,
                d_inner_mm=opt.d_inner_mm,
                material=opt.material,
                pn=opt.pn,
                length_m=float(ln),
                head_loss_m=hfi,
                velocity_m_s=_velocity_m_s(q_m3s, opt.d_inner_mm),
                weight_kg=wi,
                objective_cost=oi,
            )
        )
    return tw, hf, obj, tuple(allocs)


def _allocations_upstream_heavy_first(
    allocs: Tuple[AllocationChoice, ...],
) -> Tuple[AllocationChoice, ...]:
    """
    Уздовж полілінії trunk (від першого вузла ребра до останнього): спочатку більший d_inner
    (апстрім / ближче до джерела), далі менший — типова схема телескопа.
    """
    if len(allocs) < 2:
        return allocs
    return tuple(reversed(allocs))


def _snap_allocations_to_length_step(
    allocs: Tuple[AllocationChoice, ...],
    *,
    total_length_m: float,
    min_segment_length_m: float,
    step_m: float,
    q_m3s: float,
    max_head_loss_m: float,
) -> Optional[Tuple[AllocationChoice, ...]]:
    """Округлення довжин до кроку з корекцією суми; None якщо не вдалося зберегти ΔH та мін. довжину."""
    if step_m <= 1e-9 or not allocs:
        return allocs
    lmin = max(0.0, float(min_segment_length_m))
    st = float(step_m)
    lens = [max(lmin, round(float(a.length_m) / st) * st) for a in allocs]
    idx = max(range(len(lens)), key=lambda i: lens[i])
    diff = float(total_length_m) - sum(lens)
    lens[idx] = max(lmin, lens[idx] + diff)
    lens[idx] = max(lmin, round(lens[idx] / st) * st)
    diff2 = float(total_length_m) - sum(lens)
    lens[idx] = max(lmin, lens[idx] + diff2)
    for ln in lens:
        if ln < lmin - 1e-9:
            return None
    if abs(sum(lens) - float(total_length_m)) > 1e-2:
        return None
    out: List[AllocationChoice] = []
    hf = tw = oc = 0.0
    for a, ln in zip(allocs, lens):
        hfi = _hf_m(q_m3s, ln, a.d_inner_mm, DEFAULT_HAZEN_WILLIAMS_C)
        wpm = float(a.weight_kg) / max(1e-9, float(a.length_m))
        opm = float(a.objective_cost) / max(1e-9, float(a.length_m))
        hf += hfi
        tw += wpm * ln
        oc += opm * ln
        out.append(
            AllocationChoice(
                d_nom_mm=a.d_nom_mm,
                d_inner_mm=a.d_inner_mm,
                material=a.material,
                pn=a.pn,
                length_m=float(ln),
                head_loss_m=hfi,
                velocity_m_s=_velocity_m_s(q_m3s, a.d_inner_mm),
                weight_kg=wpm * ln,
                objective_cost=opm * ln,
            )
        )
    if hf > max_head_loss_m + 1e-6:
        return None
    return tuple(out)


def _grid_search_adjacent_multi_segment(
    feasible_opts: Sequence[PipeOption],
    *,
    total_length_m: float,
    q_m3s: float,
    constraints: OptimizationConstraints,
    objective: str,
    max_seg: int,
    lmin: float,
) -> Optional[OptimizationResult]:
    """
    3–4 секції лише по «сходинці» суміжних діаметрів у відсортованому каталозі;
    довжини — сітка на відрізку з дотриманням Lсегм ≥ lmin.
    """
    objective = _normalized_objective_name(objective)
    max_seg = int(max(3, min(4, max_seg)))
    opts = sorted(feasible_opts, key=lambda o: (o.d_inner_mm, o.d_nom_mm))
    if len(opts) < max_seg:
        return None
    L = float(total_length_m)
    Hmax = float(constraints.max_head_loss_m)
    best: Optional[OptimizationResult] = None
    n = 34

    def consider(parts: Sequence[Tuple[PipeOption, float]]) -> None:
        nonlocal best
        tw, hf, obj, allocs = _allocations_from_lengths(parts, q_m3s, objective)
        if hf > Hmax + 1e-6:
            return
        if best is None or obj < best.total_objective_cost - 1e-9:
            best = OptimizationResult(
                True,
                f"Оптимізація виконана ({len(allocs)} сегменти).",
                tw,
                hf,
                obj,
                allocations=allocs,
            )

    if max_seg == 3 and L + 1e-9 >= 3.0 * lmin:
        for i in range(len(opts) - 2):
            o0, o1, o2 = opts[i], opts[i + 1], opts[i + 2]
            k0 = _hf_m(q_m3s, 1.0, o0.d_inner_mm, o0.c_hw)
            k1 = _hf_m(q_m3s, 1.0, o1.d_inner_mm, o1.c_hw)
            k2 = _hf_m(q_m3s, 1.0, o2.d_inner_mm, o2.c_hw)
            for a in range(n + 1):
                La = lmin + (L - 3.0 * lmin) * (a / max(n, 1))
                R = L - La
                if R < 2.0 * lmin - 1e-9:
                    continue
                for b in range(n + 1):
                    Lb = lmin + (R - 2.0 * lmin) * (b / max(n, 1))
                    Lc = R - Lb
                    if Lc < lmin - 1e-9:
                        continue
                    hf = k0 * La + k1 * Lb + k2 * Lc
                    if hf > Hmax + 1e-6:
                        continue
                    consider(((o0, La), (o1, Lb), (o2, Lc)))

    if max_seg == 4 and L + 1e-9 >= 4.0 * lmin:
        m = 22
        for i in range(len(opts) - 3):
            o0, o1, o2, o3 = opts[i], opts[i + 1], opts[i + 2], opts[i + 3]
            k0 = _hf_m(q_m3s, 1.0, o0.d_inner_mm, o0.c_hw)
            k1 = _hf_m(q_m3s, 1.0, o1.d_inner_mm, o1.c_hw)
            k2 = _hf_m(q_m3s, 1.0, o2.d_inner_mm, o2.c_hw)
            k3 = _hf_m(q_m3s, 1.0, o3.d_inner_mm, o3.c_hw)
            for a in range(m + 1):
                La = lmin + (L - 4.0 * lmin) * (a / max(m, 1))
                R1 = L - La
                if R1 < 3.0 * lmin - 1e-9:
                    continue
                for b in range(m + 1):
                    Lb = lmin + (R1 - 3.0 * lmin) * (b / max(m, 1))
                    R2 = R1 - Lb
                    if R2 < 2.0 * lmin - 1e-9:
                        continue
                    for c in range(m + 1):
                        Lc = lmin + (R2 - 2.0 * lmin) * (c / max(m, 1))
                        Ld = R2 - Lc
                        if Ld < lmin - 1e-9:
                            continue
                        hf = k0 * La + k1 * Lb + k2 * Lc + k3 * Ld
                        if hf > Hmax + 1e-6:
                            continue
                        consider(((o0, La), (o1, Lb), (o2, Lc), (o3, Ld)))
    return best


def build_pipe_options_from_db(
    pipes_db: Mapping[str, Mapping[str, Mapping[str, Mapping[str, float]]]],
    *,
    material: str,
    allowed_pipes: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None,
    c_hw: float = DEFAULT_HAZEN_WILLIAMS_C,
) -> List[PipeOption]:
    """
    Якщо allowed_pipes містить запис для material із непорожньою таблицею PN→Ø,
    беруться лише ці PN і лише зазначені номінали (вузька магістраль у проєкті).

    Якщо allowed_pipes=None — як раніше: усі PN і всі Ø з каталогу для матеріалу.
    Якщо для material задано порожній {} — жодних опцій.
    """
    out: List[PipeOption] = []
    mat = pipes_db.get(material) or {}
    if not isinstance(mat, Mapping):
        return out

    root = allowed_pipes if isinstance(allowed_pipes, Mapping) else None
    raw_allow = root.get(material) if root is not None else None
    allow_by_pn: Mapping[str, Sequence[str]] = raw_allow if isinstance(raw_allow, Mapping) else {}

    if root is not None and material in root and not allow_by_pn:
        return out

    restrictive = root is not None and material in root and bool(allow_by_pn)

    def _mat_pn_key(pn_raw: object) -> Optional[str]:
        if pn_raw in mat:
            return str(pn_raw)
        s = str(pn_raw).strip()
        if s in mat:
            return s
        for k in mat.keys():
            if str(k).strip() == s:
                return str(k)
        return None

    pn_blocks: List[Tuple[str, Mapping[str, Any], Optional[Set[str]]]]
    if restrictive:
        pn_blocks = []
        for pn_key, ods in allow_by_pn.items():
            rk = _mat_pn_key(pn_key)
            if rk is None:
                continue
            by_d = mat.get(rk)
            if not isinstance(by_d, Mapping):
                continue
            if not isinstance(ods, (list, tuple)):
                continue
            allow_set = {str(x).strip() for x in ods if str(x).strip()}
            if not allow_set:
                continue
            pn_blocks.append((rk, by_d, allow_set))
    else:
        pn_blocks = []
        for pn_key, by_d in mat.items():
            if not isinstance(by_d, Mapping):
                continue
            pn_blocks.append((str(pn_key), by_d, None))

    for pn, by_d, allow_set in pn_blocks:
        for d_nom_s, row in by_d.items():
            if allow_set is not None and str(d_nom_s).strip() not in allow_set:
                continue
            try:
                d_nom = float(d_nom_s)
            except (TypeError, ValueError):
                continue
            d_inner = d_nom
            w_kg_m = estimate_weight_kg_m(d_nom, str(pn))
            price_m = 0.0
            if isinstance(row, Mapping):
                try:
                    d_inner = float(row.get("id", d_nom))
                except (TypeError, ValueError):
                    d_inner = d_nom
                raw_w = row.get("weight_kg_m")
                if raw_w is None:
                    raw_w = row.get("weight")
                if raw_w is not None:
                    try:
                        w_kg_m = max(0.0, float(raw_w))
                    except (TypeError, ValueError):
                        pass
                raw_price = row.get("price_per_m")
                if raw_price is None:
                    raw_price = row.get("price")
                if raw_price is None:
                    raw_price = row.get("cost_index")
                if raw_price is not None:
                    try:
                        price_m = max(0.0, float(raw_price))
                    except (TypeError, ValueError):
                        price_m = 0.0
            out.append(
                PipeOption(
                    material=material,
                    pn=str(pn),
                    d_nom_mm=d_nom,
                    d_inner_mm=d_inner,
                    c_hw=float(c_hw),
                    weight_kg_m=float(w_kg_m),
                    price_per_m=float(price_m),
                )
            )
    out.sort(key=lambda x: (x.d_inner_mm, x.d_nom_mm, x.pn))
    return out


def optimize_fixed_topology_by_weight(
    segments: Sequence[SegmentDemand],
    options: Sequence[PipeOption],
    constraints: OptimizationConstraints,
) -> OptimizationResult:
    objective = _normalized_objective_name(constraints.objective)
    if not segments:
        return OptimizationResult(True, "Немає сегментів для оптимізації.", 0.0, 0.0, 0.0)
    if not options:
        return OptimizationResult(False, "Порожній перелік діаметрів.", 0.0, 0.0, 0.0)

    by_seg: List[List[PipeOption]] = []
    for seg in segments:
        if seg.length_m <= 1e-9:
            return OptimizationResult(False, f"Сегмент {seg.id}: нульова довжина.", 0.0, 0.0)
        if seg.q_m3s < -1e-12:
            return OptimizationResult(False, f"Сегмент {seg.id}: від'ємна витрата.", 0.0, 0.0)
        min_req = max(float(constraints.min_segment_length_m), float(seg.min_length_m))
        if seg.length_m > 1e-9 and seg.length_m + 1e-9 < min_req:
            return OptimizationResult(
                False,
                f"Сегмент {seg.id}: довжина {seg.length_m:.3f} м < мінімально дозволеної {min_req:.3f} м.",
                0.0,
                0.0,
                0.0,
            )
        filtered = list(options)
        if seg.allowed_d_nom_mm:
            allowed = {round(float(d), 6) for d in seg.allowed_d_nom_mm}
            filtered = [o for o in filtered if round(float(o.d_nom_mm), 6) in allowed]
        v_lim = float(constraints.max_velocity_m_s)
        if v_lim > 1e-12:
            filtered = [
                o
                for o in filtered
                if _velocity_m_s(seg.q_m3s, o.d_inner_mm) <= v_lim + 1e-6
            ]
        if not filtered:
            if v_lim > 1e-12:
                msg = f"Сегмент {seg.id}: немає діаметра в межах v≤{v_lim:.2f} м/с."
            else:
                msg = f"Сегмент {seg.id}: немає діаметра в каталозі (перевірте allowed / матеріал)."
            return OptimizationResult(False, msg, 0.0, 0.0)
        # Для покрокового "upgrade" потрібна монотонна драбина за гідравлікою:
        # індекс зростає -> d_inner зростає -> втрати не збільшуються.
        filtered.sort(
            key=lambda o: (
                o.d_inner_mm,
                _option_objective_cost_per_m(o, objective),
                o.d_nom_mm,
                o.pn,
            )
        )
        by_seg.append(filtered)

    idx = [0 for _ in segments]

    def current_totals() -> Tuple[float, float, float]:
        weight = 0.0
        objective_cost = 0.0
        hf = 0.0
        for i, seg in enumerate(segments):
            opt = by_seg[i][idx[i]]
            weight += opt.weight_kg_m * seg.length_m
            objective_cost += _option_objective_cost_per_m(opt, objective) * seg.length_m
            hf += _hf_m(seg.q_m3s, seg.length_m, opt.d_inner_mm, opt.c_hw)
        return weight, objective_cost, hf

    max_iters = sum(max(0, len(v) - 1) for v in by_seg) + len(by_seg) + 5
    it = 0
    while True:
        total_weight, total_objective_cost, total_hf = current_totals()
        if total_hf <= constraints.max_head_loss_m + 1e-6:
            break
        if it >= max_iters:
            return OptimizationResult(
                False,
                (
                    f"Не вдалось вкластися в ΔH≤{constraints.max_head_loss_m:.3f} м; "
                    f"поточні втрати {total_hf:.3f} м."
                ),
                total_weight,
                total_hf,
                total_objective_cost=total_objective_cost,
            )
        best_i = -1
        best_score = -1.0
        for i, seg in enumerate(segments):
            if idx[i] + 1 >= len(by_seg[i]):
                continue
            cur = by_seg[i][idx[i]]
            nxt = by_seg[i][idx[i] + 1]
            hf_cur = _hf_m(seg.q_m3s, seg.length_m, cur.d_inner_mm, cur.c_hw)
            hf_nxt = _hf_m(seg.q_m3s, seg.length_m, nxt.d_inner_mm, nxt.c_hw)
            dh = hf_cur - hf_nxt
            dw = (
                _option_objective_cost_per_m(nxt, objective)
                - _option_objective_cost_per_m(cur, objective)
            ) * seg.length_m
            if dh <= 1e-12 or dw < -1e-12:
                continue
            score = dh / max(dw, 1e-9)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i < 0:
            return OptimizationResult(
                False,
                (
                    f"Немає доступного переходу діаметра для виконання ΔH≤{constraints.max_head_loss_m:.3f} м. "
                    "Розширте перелік діаметрів або збільшіть допустимий перепад."
                ),
                total_weight,
                total_hf,
                total_objective_cost=total_objective_cost,
            )
        idx[best_i] += 1
        it += 1

    # Фаза 2: після досягнення ΔH-обмеження жадібний підйом може залишити «зайво товсті»
    # ділянки з малою втратою. Локально зменшуємо d, поки сумарні втрати лишаються у бюджеті
    # і зменшується вага (типова ситуація: коротка гілка до споживача).
    max_refine = sum(len(v) for v in by_seg) * 3 + 20
    for _ in range(max_refine):
        tw0, obj0, hf0 = current_totals()
        if hf0 > constraints.max_head_loss_m + 1e-6:
            break
        best_j = -1
        best_dw = 0.0
        for j, seg in enumerate(segments):
            if idx[j] <= 0:
                continue
            cur = by_seg[j][idx[j]]
            prv = by_seg[j][idx[j] - 1]
            hf_cur = _hf_m(seg.q_m3s, seg.length_m, cur.d_inner_mm, cur.c_hw)
            hf_prv = _hf_m(seg.q_m3s, seg.length_m, prv.d_inner_mm, prv.c_hw)
            dhf = hf_prv - hf_cur
            if hf0 + dhf > constraints.max_head_loss_m + 1e-6:
                continue
            dw = (
                _option_objective_cost_per_m(cur, objective)
                - _option_objective_cost_per_m(prv, objective)
            ) * seg.length_m
            if dw > best_dw + 1e-12:
                best_dw = dw
                best_j = j
        if best_j < 0:
            break
        idx[best_j] -= 1

    total_weight, total_objective_cost, total_hf = current_totals()
    choices: List[SegmentChoice] = []
    for i, seg in enumerate(segments):
        opt = by_seg[i][idx[i]]
        hf = _hf_m(seg.q_m3s, seg.length_m, opt.d_inner_mm, opt.c_hw)
        v = _velocity_m_s(seg.q_m3s, opt.d_inner_mm)
        choices.append(
            SegmentChoice(
                segment_id=seg.id,
                d_nom_mm=opt.d_nom_mm,
                d_inner_mm=opt.d_inner_mm,
                pn=opt.pn,
                material=opt.material,
                head_loss_m=hf,
                velocity_m_s=v,
                weight_kg=opt.weight_kg_m * seg.length_m,
                objective_cost=_option_objective_cost_per_m(opt, objective) * seg.length_m,
            )
        )
    return OptimizationResult(
        True,
        "Оптимізація виконана.",
        total_weight_kg=total_weight,
        total_head_loss_m=total_hf,
        total_objective_cost=total_objective_cost,
        choices=tuple(choices),
    )


def optimize_single_line_allocation_by_weight(
    *,
    total_length_m: float,
    q_m3s: float,
    options: Sequence[PipeOption],
    constraints: OptimizationConstraints,
) -> OptimizationResult:
    """
    Оптимізація однієї лінії з вільним розподілом довжини між діаметрами.
    Підтримує 1…4 активні сегменти (телескоп по суміжних діаметрах для 3–4),
    мінімальну довжину та опційне округлення length_round_step_m.
    """
    objective = _normalized_objective_name(constraints.objective)
    if total_length_m <= 1e-9:
        return OptimizationResult(True, "Нульова довжина лінії.", 0.0, 0.0, 0.0)
    v_lim = float(constraints.max_velocity_m_s)
    if v_lim > 1e-12:
        feasible_opts = [
            o for o in options if _velocity_m_s(q_m3s, o.d_inner_mm) <= v_lim + 1e-6
        ]
    else:
        feasible_opts = list(options)
    if not feasible_opts:
        return OptimizationResult(
            False,
            "Немає діаметра в каталозі для цієї лінії."
            if v_lim <= 1e-12
            else "Немає діаметра, що задовольняє обмеження швидкості.",
            0.0,
            0.0,
            0.0,
        )
    feasible_opts.sort(
        key=lambda o: (_option_objective_cost_per_m(o, objective), o.d_inner_mm)
    )

    best: Optional[OptimizationResult] = None
    lmin = max(0.0, constraints.min_segment_length_m)

    def _candidate_single(opt: PipeOption) -> OptimizationResult:
        hf = _hf_m(q_m3s, total_length_m, opt.d_inner_mm, opt.c_hw)
        w = opt.weight_kg_m * total_length_m
        obj = _option_objective_cost_per_m(opt, objective) * total_length_m
        if hf > constraints.max_head_loss_m + 1e-6:
            return OptimizationResult(False, "single infeasible", w, hf, obj)
        a = AllocationChoice(
            d_nom_mm=opt.d_nom_mm,
            d_inner_mm=opt.d_inner_mm,
            material=opt.material,
            pn=opt.pn,
            length_m=total_length_m,
            head_loss_m=hf,
            velocity_m_s=_velocity_m_s(q_m3s, opt.d_inner_mm),
            weight_kg=w,
            objective_cost=obj,
        )
        return OptimizationResult(
            True,
            "Оптимізація виконана (1 сегмент).",
            w,
            hf,
            obj,
            allocations=(a,),
        )

    for opt in feasible_opts:
        c = _candidate_single(opt)
        if c.feasible and (
            best is None or c.total_objective_cost < best.total_objective_cost - 1e-9
        ):
            best = c

    if constraints.max_active_segments >= 2 and total_length_m >= 2.0 * lmin + 1e-9:
        for light in feasible_opts:
            for heavy in feasible_opts:
                if heavy.d_inner_mm <= light.d_inner_mm:
                    continue
                k_light = _hf_m(q_m3s, 1.0, light.d_inner_mm, light.c_hw)
                k_heavy = _hf_m(q_m3s, 1.0, heavy.d_inner_mm, heavy.c_hw)
                if k_heavy >= k_light - 1e-12:
                    continue
                need = (k_light * total_length_m - constraints.max_head_loss_m) / (k_light - k_heavy)
                l_heavy = max(lmin, min(total_length_m - lmin, need))
                l_light = total_length_m - l_heavy
                # Аналітично l_light може бути < lmin, але фізично допустима комбінація — вузька мін. довжини lmin.
                if l_light < lmin - 1e-9:
                    l_light = float(lmin)
                    l_heavy = float(total_length_m) - l_light
                    if l_heavy < lmin - 1e-9:
                        continue
                hf = k_light * l_light + k_heavy * l_heavy
                if hf > constraints.max_head_loss_m + 1e-6:
                    continue
                w = light.weight_kg_m * l_light + heavy.weight_kg_m * l_heavy
                obj = (
                    _option_objective_cost_per_m(light, objective) * l_light
                    + _option_objective_cost_per_m(heavy, objective) * l_heavy
                )
                allocs = (
                    AllocationChoice(
                        d_nom_mm=light.d_nom_mm,
                        d_inner_mm=light.d_inner_mm,
                        material=light.material,
                        pn=light.pn,
                        length_m=l_light,
                        head_loss_m=k_light * l_light,
                        velocity_m_s=_velocity_m_s(q_m3s, light.d_inner_mm),
                        weight_kg=light.weight_kg_m * l_light,
                        objective_cost=_option_objective_cost_per_m(light, objective) * l_light,
                    ),
                    AllocationChoice(
                        d_nom_mm=heavy.d_nom_mm,
                        d_inner_mm=heavy.d_inner_mm,
                        material=heavy.material,
                        pn=heavy.pn,
                        length_m=l_heavy,
                        head_loss_m=k_heavy * l_heavy,
                        velocity_m_s=_velocity_m_s(q_m3s, heavy.d_inner_mm),
                        weight_kg=heavy.weight_kg_m * l_heavy,
                        objective_cost=_option_objective_cost_per_m(heavy, objective) * l_heavy,
                    ),
                )
                c = OptimizationResult(
                    True,
                    "Оптимізація виконана (телескоп 2 сегменти).",
                    w,
                    hf,
                    obj,
                    allocations=allocs,
                )
                if best is None or c.total_objective_cost < best.total_objective_cost - 1e-9:
                    best = c

    mseg = int(constraints.max_active_segments)
    if mseg >= 3:
        g3 = _grid_search_adjacent_multi_segment(
            feasible_opts,
            total_length_m=total_length_m,
            q_m3s=q_m3s,
            constraints=constraints,
            objective=objective,
            max_seg=3,
            lmin=lmin,
        )
        if g3 is not None and (
            best is None or g3.total_objective_cost < best.total_objective_cost - 1e-9
        ):
            best = g3
    if mseg >= 4:
        g4 = _grid_search_adjacent_multi_segment(
            feasible_opts,
            total_length_m=total_length_m,
            q_m3s=q_m3s,
            constraints=constraints,
            objective=objective,
            max_seg=4,
            lmin=lmin,
        )
        if g4 is not None and (
            best is None or g4.total_objective_cost < best.total_objective_cost - 1e-9
        ):
            best = g4

    if best is not None:
        if len(best.allocations) >= 2:
            best = OptimizationResult(
                best.feasible,
                best.message,
                best.total_weight_kg,
                best.total_head_loss_m,
                best.total_objective_cost,
                best.choices,
                _allocations_upstream_heavy_first(best.allocations),
            )
        step_r = float(constraints.length_round_step_m)
        if step_r > 1e-9 and best.allocations:
            snapped = _snap_allocations_to_length_step(
                best.allocations,
                total_length_m=total_length_m,
                min_segment_length_m=lmin,
                step_m=step_r,
                q_m3s=q_m3s,
                max_head_loss_m=float(constraints.max_head_loss_m),
            )
            if snapped is not None:
                tw = sum(float(a.weight_kg) for a in snapped)
                hf = sum(float(a.head_loss_m) for a in snapped)
                oc = sum(float(a.objective_cost) for a in snapped)
                best = OptimizationResult(
                    True,
                    str(best.message) + " (довжини округлено до кроку).",
                    tw,
                    hf,
                    oc,
                    allocations=snapped,
                )
        return best

    min_hf = min(_hf_m(q_m3s, total_length_m, o.d_inner_mm, o.c_hw) for o in feasible_opts)
    return OptimizationResult(
        False,
        (
            f"Заданий перепад тиску недосяжний: мінімально можливі втрати ≈ {min_hf:.3f} м, "
            f"допустимо {constraints.max_head_loss_m:.3f} м."
        ),
        0.0,
        min_hf,
        0.0,
    )


__all__ = [
    "AllocationChoice",
    "OptimizationConstraints",
    "OptimizationResult",
    "PipeOption",
    "SegmentChoice",
    "SegmentDemand",
    "build_pipe_options_from_db",
    "estimate_weight_kg_m",
    "optimize_fixed_topology_by_weight",
    "optimize_single_line_allocation_by_weight",
]
