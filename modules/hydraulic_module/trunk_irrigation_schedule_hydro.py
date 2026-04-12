"""
Гідравліка магістралі за сценаріями поливу (irrigation_slots).

Для кожного непорожнього слота: активні споживачі з Q_тест (за замовчуванням однакові;
на вузлі можна задати trunk_schedule_q_m3h / trunk_schedule_h_m), решта Q=0;
перевірка H ≥ H_ціль для кожного активного (ціль теж може бути індивідуальною на вузлі).
Втрати Hazen–Williams як у trunk_tree_compute.

Заданий pump_operating_head_m — робочий напір на насосі (м вод. ст.): один розрахунок HW на слот;
перевірка, що в активних споживачів H ≥ target_head_m. Якщо ні — у issues додається орієнтовний
мінімум H (бінарний пошук). max_pipe_velocity_mps — перевірка швидкості в трубах.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .trunk_map_graph import build_oriented_edges
from .trunk_tree_compute import (
    TrunkTreeEdge,
    TrunkTreeNode,
    TrunkTreeSpec,
    compute_trunk_tree_steady,
)


def _node_id(nodes: Sequence[Mapping[str, Any]], i: int) -> str:
    if i < 0 or i >= len(nodes):
        return ""
    return str(nodes[i].get("id", "")).strip() or f"T{i}"


def _segment_length_m(nodes: Sequence[Mapping[str, Any]], seg: Mapping[str, Any]) -> float:
    """Довжина труби: ребро з двома вузлами — пряма відстань між ними (топологія графа), не полілінія path_local."""
    ni = seg.get("node_indices")
    if isinstance(ni, list) and len(ni) == 2:
        try:
            a, b = int(ni[0]), int(ni[1])
            x0 = float(nodes[a]["x"])
            y0 = float(nodes[a]["y"])
            x1 = float(nodes[b]["x"])
            y1 = float(nodes[b]["y"])
            d = math.hypot(x1 - x0, y1 - y0)
            if d > 1e-9:
                return d
        except (KeyError, TypeError, ValueError, IndexError):
            pass
    pl = seg.get("path_local")
    if isinstance(pl, list) and len(pl) >= 2:
        pts: List[Tuple[float, float]] = []
        for p in pl:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    pts.append((float(p[0]), float(p[1])))
                except (TypeError, ValueError):
                    continue
        if len(pts) >= 2:
            s = 0.0
            for i in range(len(pts) - 1):
                s += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            if s > 1e-6:
                return s
    return 0.0


def _trunk_tree_edge_props(payload: Mapping[str, Any]) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """(parent_id, child_id) -> (d_inner_mm, c_hw)."""
    out: Dict[Tuple[str, str], Tuple[float, float]] = {}
    edges_in = payload.get("edges")
    if not isinstance(edges_in, list):
        return out
    for row in edges_in:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("parent_id", "")).strip()
        cid = str(row.get("child_id", "")).strip()
        if not pid or not cid:
            continue
        try:
            dmm = float(row.get("d_inner_mm", 90.0))
        except (TypeError, ValueError):
            dmm = 90.0
        try:
            chw = float(row.get("c_hw", 140.0))
        except (TypeError, ValueError):
            chw = 140.0
        out[(pid, cid)] = (dmm, chw)
    return out


def _trunk_consumer_schedule_q_m3h(
    nodes: Sequence[Mapping[str, Any]],
    id_to_idx: Mapping[str, int],
    nid: str,
    default_q_m3h: float,
) -> float:
    """Витрата для сценарію поливу (м³/год); за замовчуванням — аргумент q_consumer_m3h."""
    idx = id_to_idx.get(nid)
    if idx is None:
        return max(0.0, float(default_q_m3h))
    node = nodes[idx]
    raw = node.get("trunk_schedule_q_m3h")
    if raw is None:
        return max(0.0, float(default_q_m3h))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(default_q_m3h))


def _trunk_consumer_schedule_target_head_m(
    nodes: Sequence[Mapping[str, Any]],
    id_to_idx: Mapping[str, int],
    nid: str,
    default_h_m: float,
) -> float:
    """Цільовий мін. напір у споживача (м вод. ст.); за замовчуванням — target_head_m."""
    idx = id_to_idx.get(nid)
    if idx is None:
        return max(0.0, float(default_h_m))
    node = nodes[idx]
    raw = node.get("trunk_schedule_h_m")
    if raw is None:
        return max(0.0, float(default_h_m))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(default_h_m))


def _issues_pipe_velocity(
    res: Any,
    max_velocity_mps: float,
    *,
    max_messages: int = 5,
) -> List[str]:
    """Перевищення швидкості потоку в трубах (за результатом compute_trunk_tree_steady)."""
    if max_velocity_mps <= 0:
        return []
    out: List[str] = []
    lim = float(max_velocity_mps)
    offenders: List[Any] = []
    for e in getattr(res, "edges", ()) or ():
        try:
            v = float(getattr(e, "velocity_m_s", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if v <= lim + 1e-6:
            continue
        offenders.append(e)
    for e in offenders[:max_messages]:
        try:
            v = float(getattr(e, "velocity_m_s", 0.0) or 0.0)
            q_m3h = float(getattr(e, "q_m3s", 0.0) or 0.0) * 3600.0
            dmm = float(getattr(e, "d_inner_mm", 0.0) or 0.0)
            pa = str(getattr(e, "parent_id", ""))
            cb = str(getattr(e, "child_id", ""))
        except (TypeError, ValueError):
            pa, cb, q_m3h, dmm, v = "?", "?", 0.0, 0.0, 0.0
        qs = f"{q_m3h:.3f}".rstrip("0").rstrip(".")
        out.append(
            f"v={v:.2f} м/с > {lim:.2f} м/с на {pa}→{cb} "
            f"(d≈{dmm:.0f} мм, Q≈{qs} м³/год)."
        )
    if len(offenders) > max_messages:
        out.append(f"… ще {len(offenders) - max_messages} ділянок з v > {lim:.2f} м/с.")
    return out


def _segment_index_for_uv(
    segments: Sequence[Mapping[str, Any]], u: int, v: int
) -> Optional[int]:
    for si, seg in enumerate(segments):
        if not isinstance(seg, Mapping):
            continue
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) != 2:
            continue
        try:
            a, b = int(ni[0]), int(ni[1])
        except (TypeError, ValueError):
            continue
        if {a, b} == {u, v}:
            return si
    return None


def compute_trunk_irrigation_schedule_hydro(
    trunk_nodes: Sequence[Mapping[str, Any]],
    trunk_segments: Sequence[Mapping[str, Any]],
    irrigation_slots: Sequence[Sequence[str]],
    trunk_tree_payload: Mapping[str, Any],
    *,
    q_consumer_m3h: float = 60.0,
    target_head_m: float = 40.0,
    default_d_inner_mm: float = 90.0,
    default_c_hw: float = 140.0,
    source_head_search_max_m: float = 220.0,
    max_pipe_velocity_mps: float = 2.0,
    pump_operating_head_m: float = 50.0,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Повертає (cache_dict, global_messages).
    cache_dict: seg_dominant_slot, envelope, per_slot, test params.
    """
    global_issues: List[str] = []
    lim_vel = max(0.0, float(max_pipe_velocity_mps))
    pump_h = max(1.0, min(400.0, float(pump_operating_head_m)))
    limits_block: Dict[str, float] = {
        "max_pipe_velocity_mps": lim_vel,
        "pump_operating_head_m": pump_h,
    }
    empty: Dict[str, Any] = {
        "seg_dominant_slot": {},
        "segment_hover": {},
        "envelope": {"max_source_head_m": 0.0, "max_total_q_m3s": 0.0},
        "per_slot": {},
        "test_q_m3h": float(q_consumer_m3h),
        "test_h_m": float(target_head_m),
        "limits": dict(limits_block),
    }

    nodes = list(trunk_nodes)
    segs = list(trunk_segments)
    if not nodes or not segs:
        global_issues.append("Немає вузлів або відрізків магістралі.")
        return empty, global_issues

    directed, o_err = build_oriented_edges(nodes, segs)
    if directed is None or o_err:
        global_issues.extend(o_err or ["Не вдалося орієнтувати дерево магістралі."])
        return empty, global_issues

    src_idx = None
    for i in range(len(nodes)):
        k = str(nodes[i].get("kind", "")).strip().lower()
        if k == "source":
            src_idx = i
            break
    if src_idx is None:
        global_issues.append("Не знайдено вузол-витік (source).")
        return empty, global_issues

    source_id = _node_id(nodes, src_idx)
    props = _trunk_tree_edge_props(trunk_tree_payload)
    q_default_m3h = max(0.0, float(q_consumer_m3h))

    tree_nodes: List[TrunkTreeNode] = []
    for i in range(len(nodes)):
        nid = _node_id(nodes, i)
        k = str(nodes[i].get("kind", "")).strip().lower()
        if k in ("consumption", "valve"):
            kind = "consumption"
        elif k == "source":
            kind = "source"
        elif k == "junction":
            kind = "junction"
        else:
            kind = "bend"
        tree_nodes.append(TrunkTreeNode(id=nid, kind=kind, q_demand_m3s=0.0))

    tree_edges: List[TrunkTreeEdge] = []
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        si = _segment_index_for_uv(segs, u, v)
        if si is None:
            global_issues.append(f"Немає відрізка для ребра {pid}→{cid}.")
            continue
        seg = segs[si]
        lm = _segment_length_m(nodes, seg)
        if lm <= 1e-9:
            global_issues.append(f"Нульова довжина ребра {pid}→{cid}.")
            continue
        dmm, chw = props.get((pid, cid), (default_d_inner_mm, default_c_hw))
        if dmm <= 0:
            dmm = default_d_inner_mm
        tree_edges.append(
            TrunkTreeEdge(
                parent_id=pid,
                child_id=cid,
                length_m=float(lm),
                d_inner_mm=float(dmm),
                c_hw=float(chw),
                dz_m=0.0,
            )
        )

    if len(tree_edges) != len(nodes) - 1:
        global_issues.append(
            f"Очікується {len(nodes) - 1} ребер дерева, зібрано {len(tree_edges)} "
            f"({len(nodes)} вузлів на мапі — у зв’язному дереві має бути рівно стільки відрізків, скільки вузлів мінус один). "
            "Ймовірно є вузол без труби, зайве ребро або розрив графа — перевірте trunk_map_segments."
        )
        return empty, global_issues

    id_to_idx = {_node_id(nodes, i): i for i in range(len(nodes))}

    def make_spec(active: Set[str], source_head: float) -> TrunkTreeSpec:
        tnodes = []
        for tn in tree_nodes:
            qd = 0.0
            if tn.kind == "consumption" and tn.id in active:
                qh = _trunk_consumer_schedule_q_m3h(nodes, id_to_idx, tn.id, q_default_m3h)
                qd = qh / 3600.0
            tnodes.append(
                TrunkTreeNode(id=tn.id, kind=tn.kind, q_demand_m3s=qd)
            )
        return TrunkTreeSpec(
            nodes=tuple(tnodes),
            edges=tuple(tree_edges),
            source_id=source_id,
            source_head_m=float(source_head),
        )

    def min_head_at_active(res, active: Set[str]) -> Optional[float]:
        if not active:
            return None
        heads = []
        for nid in active:
            if nid not in res.node_head_m:
                return None
            heads.append(float(res.node_head_m[nid]))
        return min(heads) if heads else None

    def all_active_heads_meet_targets(res, active: Set[str], tol: float = 1e-3) -> bool:
        for nid in active:
            if nid not in res.node_head_m:
                return False
            h = float(res.node_head_m[nid])
            tgt = _trunk_consumer_schedule_target_head_m(
                nodes, id_to_idx, nid, float(target_head_m)
            )
            if h < tgt - tol:
                return False
        return True

    def max_head_deficit_vs_targets(res, active: Set[str]) -> Optional[float]:
        mx = 0.0
        for nid in active:
            if nid not in res.node_head_m:
                return None
            h = float(res.node_head_m[nid])
            tgt = _trunk_consumer_schedule_target_head_m(
                nodes, id_to_idx, nid, float(target_head_m)
            )
            mx = max(mx, max(0.0, tgt - h))
        return mx

    def estimate_min_source_head_for_target(active: Set[str]) -> Optional[float]:
        """Мінімальний H на насосі, щоб у всіх активних споживачів H ≥ індивідуальної цілі (для підказки)."""
        if not active:
            return None
        lo, hi = 0.0, float(source_head_search_max_m)
        spec_hi = make_spec(active, hi)
        rh = compute_trunk_tree_steady(spec_hi)
        if rh.issues:
            return None
        if not all_active_heads_meet_targets(rh, active, tol=1e-3):
            return None
        for _ in range(48):
            mid = 0.5 * (lo + hi)
            spec_m = make_spec(active, mid)
            rm = compute_trunk_tree_steady(spec_m)
            if rm.issues:
                return None
            if all_active_heads_meet_targets(rm, active, tol=1e-4):
                hi = mid
            else:
                lo = mid
        spec_f = make_spec(active, hi)
        rf = compute_trunk_tree_steady(spec_f)
        if rf.issues:
            return None
        return float(hi)

    def evaluate_at_given_pump_head(
        active: Set[str], head_m: float
    ) -> Tuple[Optional[float], Optional[Any], List[str]]:
        """Один стійкий розрахунок при заданому напорі на насосі."""
        issues: List[str] = []
        if not active:
            return None, None, issues
        spec = make_spec(active, float(head_m))
        res = compute_trunk_tree_steady(spec)
        if res.issues:
            return None, None, list(res.issues)
        mh = min_head_at_active(res, active)
        if mh is None:
            return None, None, ["Немає напору у споживачів."]
        return float(head_m), res, issues

    slots_list = list(irrigation_slots) if irrigation_slots else []
    while len(slots_list) < 48:
        slots_list.append([])
    per_slot: Dict[int, Dict[str, Any]] = {}
    edge_q_by_slot: Dict[int, Dict[Tuple[str, str], float]] = {}
    max_h = 0.0
    max_q = 0.0

    for slot_i in range(48):
        raw = slots_list[slot_i] if slot_i < len(slots_list) else []
        if not isinstance(raw, list):
            continue
        active: Set[str] = set()
        for x in raw:
            s = str(x).strip()
            if s and s in id_to_idx:
                active.add(s)
        if not active:
            continue
        hs, res, iss = evaluate_at_given_pump_head(active, pump_h)
        if hs is None or res is None:
            per_slot[slot_i] = {
                "issues": iss,
                "source_head_m": None,
                "total_q_m3s": None,
                "edge_q": {},
                "min_consumer_head_m": None,
                "head_deficit_m": None,
            }
            continue
        eq: Dict[Tuple[str, str], float] = {}
        for e in res.edges:
            eq[(e.parent_id, e.child_id)] = float(e.q_m3s)
        edge_q_by_slot[slot_i] = eq
        tq = float(res.total_q_m3s)
        slot_issues: List[str] = []
        mh_cons = min_head_at_active(res, active)
        deficit_mx = max_head_deficit_vs_targets(res, active)
        if deficit_mx is not None and deficit_mx > 1e-3:
            th_max = max(
                _trunk_consumer_schedule_target_head_m(
                    nodes, id_to_idx, nid, float(target_head_m)
                )
                for nid in active
            )
            slot_issues.append(
                f"При заданому H_насос={pump_h:.2f} м мін. напір серед активних споживачів ≈ {mh_cons:.2f} м вод. ст.; "
                f"є дефіцит до індивідуальних цілей (макс. ≈ {deficit_mx:.2f} м, найвища ціль ≈ {th_max:.1f} м). "
                f"Збільшіть діаметри труб або напір насоса."
            )
            h_need = estimate_min_source_head_for_target(active)
            if h_need is not None:
                slot_issues.append(
                    f"Орієнтовно мінімальний потрібний напір насоса, щоб виконати цілі по всіх активних споживачах: "
                    f"H ≥ {h_need:.2f} м вод. ст."
                )
        slot_issues.extend(_issues_pipe_velocity(res, lim_vel))
        head_deficit_m: Optional[float] = None
        if deficit_mx is not None:
            head_deficit_m = float(deficit_mx)
        per_slot[slot_i] = {
            "issues": slot_issues,
            "source_head_m": float(hs),
            "total_q_m3s": tq,
            "edge_q": eq,
            "node_head_m": dict(res.node_head_m),
            "min_consumer_head_m": float(mh_cons) if mh_cons is not None else None,
            "head_deficit_m": head_deficit_m,
        }
        max_h = max(max_h, float(hs))
        max_q = max(max_q, tq)

    seg_dominant: Dict[int, int] = {}
    for si, seg in enumerate(segs):
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) != 2:
            continue
        try:
            a, b = int(ni[0]), int(ni[1])
        except (TypeError, ValueError):
            continue
        pa, pb = _node_id(nodes, a), _node_id(nodes, b)
        best_slot: Optional[int] = None
        best_q = -1.0
        for sidx, eqm in edge_q_by_slot.items():
            q1 = float(eqm.get((pa, pb), 0.0))
            q2 = float(eqm.get((pb, pa), 0.0))
            qv = max(q1, q2)
            if qv > best_q + 1e-12:
                best_q = qv
                best_slot = int(sidx)
        if best_slot is not None and best_q > 1e-9:
            seg_dominant[si] = best_slot

    uv_to_dmm: Dict[Tuple[int, int], float] = {}
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        dmm, _chw = props.get((pid, cid), (default_d_inner_mm, default_c_hw))
        if dmm <= 0:
            dmm = default_d_inner_mm
        uv_to_dmm[(min(int(u), int(v)), max(int(u), int(v)))] = float(dmm)

    segment_hover: Dict[str, Dict[str, Any]] = {}
    for si, seg in enumerate(segs):
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) != 2:
            continue
        try:
            a, b = int(ni[0]), int(ni[1])
        except (TypeError, ValueError):
            continue
        pa, pb = _node_id(nodes, a), _node_id(nodes, b)
        best_slot_h: Optional[int] = None
        best_q_h = -1.0
        for sidx, eqm in edge_q_by_slot.items():
            q1 = float(eqm.get((pa, pb), 0.0))
            q2 = float(eqm.get((pb, pa), 0.0))
            qv = max(q1, q2)
            if qv > best_q_h + 1e-12:
                best_q_h = qv
                best_slot_h = int(sidx)
        dom_slot: Optional[int] = None
        q_line = 0.0
        if best_slot_h is not None and best_q_h > 1e-9:
            dom_slot = best_slot_h
            q_line = float(best_q_h)
        dmm_seg = float(uv_to_dmm.get((min(a, b), max(a, b)), default_d_inner_mm))
        segment_hover[str(si)] = {
            "d_inner_mm": dmm_seg,
            "q_m3s": q_line,
            "dominant_slot": dom_slot,
        }

    out = {
        "seg_dominant_slot": seg_dominant,
        "segment_hover": segment_hover,
        "envelope": {"max_source_head_m": float(max_h), "max_total_q_m3s": float(max_q)},
        "per_slot": {str(k): v for k, v in per_slot.items()},
        "test_q_m3h": float(q_consumer_m3h),
        "test_h_m": float(target_head_m),
        "limits": dict(limits_block),
    }
    return out, global_issues