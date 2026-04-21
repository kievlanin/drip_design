"""
Гідравліка магістралі за сценаріями поливу (irrigation_slots).

Для кожного непорожнього слота: активні споживачі з Q_тест (за замовчуванням однакові;
на вузлі можна задати trunk_schedule_q_m3h / trunk_schedule_h_m), решта Q=0;
перевірка H ≥ H_ціль для кожного активного (ціль теж може бути індивідуальною на вузлі).
Втрати Hazen–Williams як у trunk_tree_compute.

Заданий pump_operating_head_m — типово абсолютний п'єзометричний H на джерелі (м вод. ст.);
якщо у вузлі source задано pump_suction_xy_offset_m і є surface_z_at_xy — трактується як ΔH насоса,
H_source = Z_топо(всмоктування) + ΔH (+ опційно pump_install_geodetic_dz_m). Опційно pump_install_geodetic_dz_m
додається до заданого напору (перепад входу/виходу насоса в проєкті, м).

Опційно surface_z_at_xy(x, y) — рельєф (м); на ребрі dz_m = Z(батько) − Z(дитина) для compute_trunk_tree_steady.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from .trunk_map_graph import build_oriented_edges
from .pipe_weight_optimizer import (
    OptimizationConstraints,
    SegmentDemand,
    build_pipe_options_from_db,
    optimize_fixed_topology_by_weight,
    optimize_single_line_allocation_by_weight,
)
from .hydraulics_constants import hazen_c_from_pipe_entry
from .trunk_tree_compute import (
    TrunkTreeEdge,
    TrunkTreeNode,
    TrunkTreeSpec,
    compute_trunk_tree_steady,
)

_TRUNK_MICRO_SECTION_MERGE_M = 5.0


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


def _collapse_short_telescope_sections(
    sections: Sequence[Mapping[str, Any]],
    *,
    max_tail_m: float = _TRUNK_MICRO_SECTION_MERGE_M,
) -> List[Dict[str, Any]]:
    """
    Прибрати "мікрохвости" телескопа: секції з L <= max_tail_m доклеюються до сусідньої.
    Пріоритет — попередня (upstream) секція.
    """
    out: List[Dict[str, Any]] = [dict(s) for s in (sections or []) if isinstance(s, Mapping)]
    if len(out) <= 1:
        return out

    tail_lim = max(0.0, float(max_tail_m))
    eps = 1e-9

    def _num(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    while len(out) > 1:
        changed = False
        for i, sec in enumerate(out):
            li = _num(sec.get("length_m"))
            if li is None or li <= eps or li > tail_lim + eps:
                continue
            j = i - 1 if i > 0 else 1
            rec = out[j]
            lr = _num(rec.get("length_m"))
            if lr is None or lr < 0.0:
                lr = 0.0
            rec["length_m"] = float(lr + li)
            for key in ("head_loss_m", "weight_kg", "objective_cost"):
                rv = _num(rec.get(key))
                sv = _num(sec.get(key))
                add = 0.0
                if rv is not None and lr > eps:
                    add = float(rv / lr) * li
                elif sv is not None and li > eps:
                    add = float(sv / li) * li
                if add > 0.0:
                    rec[key] = float((rv if rv is not None else 0.0) + add)
            out.pop(i)
            changed = True
            break
        if not changed:
            break
    return out


def _rescale_hw_section_tuples_to_edge_length(
    sections: Sequence[Any], length_m: float
) -> Tuple[Tuple[float, float, float], ...]:
    """
    Сума length у секціях HW (кортежі L_m, d_mm, C) має збігатися з length_m ребра — інакше validate_trunk_tree падає.
    Пропорційно масштабує L по секціях; d_inner та C не змінюються.
    """
    lm_edge = float(length_m)
    if lm_edge <= 1e-12:
        return ()
    rows: List[Tuple[float, float, float]] = []
    s_old = 0.0
    for sec in sections:
        try:
            L = float(sec[0])
            d = float(sec[1])
            c = float(sec[2])
        except (TypeError, ValueError, IndexError):
            continue
        if L <= 1e-12 or d <= 1e-12:
            continue
        rows.append((L, d, c))
        s_old += L
    if not rows:
        return ()
    if abs(s_old - lm_edge) <= 1e-4:
        return tuple(rows)
    scale = lm_edge / s_old
    scaled_lens: List[float] = []
    for r in rows[:-1]:
        scaled_lens.append(max(0.0, r[0] * scale))
    last = max(0.0, lm_edge - sum(scaled_lens))
    scaled_lens.append(last)
    return tuple((float(ln), float(d), float(c)) for (L, d, c), ln in zip(rows, scaled_lens))


def _trunk_tree_edge_props(payload: Mapping[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(parent_id, child_id) -> {'d_inner_mm', 'c_hw', 'sections'}."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
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
        sections: List[Tuple[float, float, float]] = []
        raw_secs = row.get("sections")
        if not isinstance(raw_secs, list):
            raw_secs = row.get("telescoped_sections")
        if isinstance(raw_secs, list):
            for sec in raw_secs:
                if not isinstance(sec, Mapping):
                    continue
                try:
                    sl = float(sec.get("length_m", 0.0))
                    sd = float(sec.get("d_inner_mm", dmm))
                    sc = float(sec.get("c_hw", chw))
                except (TypeError, ValueError):
                    continue
                if sl > 1e-9 and sd > 1e-9:
                    sections.append((sl, sd, sc))
        out[(pid, cid)] = {"d_inner_mm": dmm, "c_hw": chw, "sections": sections}
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


def _parse_pump_suction_xy_offset_m(
    raw: Any,
) -> Optional[Tuple[float, float]]:
    """Зсув точки всмоктування від вузла source: (dx_m, dy_m) у локальних XY."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            return (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            return None
    if isinstance(raw, Mapping):
        try:
            dx = float(raw.get("x_m", raw.get("dx_m", 0.0)))
            dy = float(raw.get("y_m", raw.get("dy_m", 0.0)))
        except (TypeError, ValueError):
            return None
        return (dx, dy)
    return None


def _effective_pump_source_head_m(
    nodes: Sequence[Mapping[str, Any]],
    src_idx: int,
    pump_head_m: float,
    surface_z_at_xy: Optional[Callable[[float, float], float]],
) -> Tuple[float, Optional[float], str]:
    """
    П'єзометричний напір у вузлі source для TrunkTreeSpec.

    За замовчуванням pump_head_m — абсолютний H на джерелі (режим absolute).

    pump_install_geodetic_dz_m на вузлі source (м): додається до pump_head_m
    (корекція на перепад висот між входом і виходом насоса / монтаж, Z_вх − Z_вих у проєкті).

    Якщо задано pump_suction_xy_offset_m [dx,dy] і є surface_z_at_xy:
    pump_head_m (+ geodetic) трактується як ΔH насоса, Z всмоктування — топо в (x+dx, y+dy),
    H_source = Z_всмоктування + pump_head_m + geodetic (режим suction_z_plus_delta).
    """
    mph = float(pump_head_m)
    n = nodes[int(src_idx)]
    gdz = 0.0
    raw_gdz = n.get("pump_install_geodetic_dz_m")
    if raw_gdz is not None:
        try:
            gdz = float(raw_gdz)
        except (TypeError, ValueError):
            gdz = 0.0
    delta_core = mph + gdz
    off = _parse_pump_suction_xy_offset_m(n.get("pump_suction_xy_offset_m"))
    if off is not None and surface_z_at_xy is not None:
        try:
            xs = float(n["x"])
            ys = float(n["y"])
        except (KeyError, TypeError, ValueError):
            return max(0.5, min(900.0, delta_core)), None, "absolute_plus_geodetic_dz" if gdz else "absolute"
        dx, dy = off[0], off[1]
        try:
            z_suc = float(surface_z_at_xy(xs + dx, ys + dy))
        except Exception:
            return max(0.5, min(900.0, delta_core)), None, "absolute_plus_geodetic_dz" if gdz else "absolute"
        h_src = z_suc + delta_core
        return max(0.5, min(900.0, h_src)), z_suc, "suction_z_plus_delta"
    if abs(gdz) > 1e-12:
        return max(0.5, min(900.0, delta_core)), None, "absolute_plus_geodetic_dz"
    return max(0.5, min(900.0, mph)), None, "absolute"


def _edge_dz_m_from_surface(
    nodes: Sequence[Mapping[str, Any]],
    parent_idx: int,
    child_idx: int,
    surface_z_at_xy: Optional[Callable[[float, float], float]],
) -> float:
    """
    Різниця висот рельєфу вздовж орієнтованого ребра parent→child: Z(parent) − Z(child) (м),
    як у TrunkTreeEdge.dz_m для compute_trunk_tree_steady (h_дитина = h_батько − hf + dz_m).
    """
    if surface_z_at_xy is None:
        return 0.0
    try:
        pu = nodes[int(parent_idx)]
        cv = nodes[int(child_idx)]
        zp = float(surface_z_at_xy(float(pu["x"]), float(pu["y"])))
        zc = float(surface_z_at_xy(float(cv["x"]), float(cv["y"])))
        return zp - zc
    except Exception:
        return 0.0


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


def _best_q_slot_and_pair_for_segment_chain(
    nodes: Sequence[Mapping[str, Any]],
    idxs: Sequence[int],
    edge_q_by_slot: Mapping[int, Mapping[Tuple[str, str], float]],
) -> Tuple[float, Optional[int], Optional[Tuple[int, int]]]:
    """
    Для ланцюга node_indices [i0,i1,…] — максимум Q (м³/с) по всіх послідовних парах і слотах,
    плюс слот і пара вузлів, де досягнуто максимум (для d_inner з uv_to_dmm).
    """
    best_q_h = -1.0
    best_slot_h: Optional[int] = None
    best_pair: Optional[Tuple[int, int]] = None
    for a, b in zip(idxs[:-1], idxs[1:]):
        pa, pb = _node_id(nodes, int(a)), _node_id(nodes, int(b))
        if not pa or not pb:
            continue
        for sidx, eqm in edge_q_by_slot.items():
            q1 = float(eqm.get((pa, pb), 0.0))
            q2 = float(eqm.get((pb, pa), 0.0))
            qv = max(q1, q2)
            if qv > best_q_h + 1e-12:
                best_q_h = qv
                best_slot_h = int(sidx)
                best_pair = (int(a), int(b))
    return float(best_q_h), best_slot_h, best_pair


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
    max_pipe_velocity_mps: float = 0.0,
    pump_operating_head_m: float = 50.0,
    use_required_pump_head: bool = False,
    surface_z_at_xy: Optional[Callable[[float, float], float]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Повертає (cache_dict, global_messages).
    cache_dict: seg_dominant_slot, envelope, per_slot, test params.
    """
    global_issues: List[str] = []
    lim_vel = max(0.0, float(max_pipe_velocity_mps))
    pump_h = max(1.0, min(400.0, float(pump_operating_head_m)))
    limits_block: Dict[str, Any] = {
        "max_pipe_velocity_mps": lim_vel,
        "pump_operating_head_m": pump_h,
        "effective_pump_source_head_m": pump_h,
        "pump_source_head_mode": "absolute",
    }
    mode_block: Dict[str, Any] = {
        "pump_head_mode": "required" if bool(use_required_pump_head) else "fixed"
    }
    empty: Dict[str, Any] = {
        "seg_dominant_slot": {},
        "segment_hover": {},
        "envelope": {
            "max_source_head_m": 0.0,
            "max_total_q_m3s": 0.0,
            "worst_min_consumer_head_m": None,
            "min_required_source_head_m": None,
        },
        "per_slot": {},
        "test_q_m3h": float(q_consumer_m3h),
        "test_h_m": float(target_head_m),
        "limits": dict(limits_block),
        "mode": dict(mode_block),
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

    eff_pump_h, z_suc_sampled, pump_head_mode = _effective_pump_source_head_m(
        nodes, int(src_idx), pump_h, surface_z_at_xy
    )
    limits_block["effective_pump_source_head_m"] = float(eff_pump_h)
    limits_block["pump_source_head_mode"] = str(pump_head_mode)
    if z_suc_sampled is not None:
        limits_block["pump_suction_z_sampled_m"] = float(z_suc_sampled)
    else:
        limits_block.pop("pump_suction_z_sampled_m", None)

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
        p = props.get((pid, cid), {})
        dmm = float(p.get("d_inner_mm", default_d_inner_mm) or default_d_inner_mm)
        chw = float(p.get("c_hw", default_c_hw) or default_c_hw)
        sections_raw = p.get("sections") if isinstance(p.get("sections"), list) else []
        sections = _rescale_hw_section_tuples_to_edge_length(sections_raw, float(lm))
        if dmm <= 0:
            dmm = default_d_inner_mm
        dz_m = _edge_dz_m_from_surface(nodes, int(u), int(v), surface_z_at_xy)
        tree_edges.append(
            TrunkTreeEdge(
                parent_id=pid,
                child_id=cid,
                length_m=float(lm),
                d_inner_mm=float(dmm),
                c_hw=float(chw),
                dz_m=float(dz_m),
                sections=tuple(sections),
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

    def worst_deficit_consumer_id(res, active: Set[str]) -> Optional[str]:
        """Вузол з максимальним (ціль − факт); при рівності — лексикографічно менший id."""
        best_nid: Optional[str] = None
        best_def = -1.0
        for nid in active:
            if nid not in res.node_head_m:
                continue
            h = float(res.node_head_m[nid])
            tgt = _trunk_consumer_schedule_target_head_m(
                nodes, id_to_idx, nid, float(target_head_m)
            )
            d = max(0.0, tgt - h)
            if d < 1e-9:
                continue
            if best_nid is None or d > best_def + 1e-9:
                best_def = d
                best_nid = nid
            elif abs(d - best_def) <= 1e-9 and nid < str(best_nid):
                best_nid = nid
        return best_nid

    def min_head_consumer_id(res, active: Set[str]) -> Optional[str]:
        """Споживач з мінімальним фактичним напором (для підпису при лише v-попередженні)."""
        best_nid: Optional[str] = None
        best_h = 1e18
        for nid in active:
            if nid not in res.node_head_m:
                continue
            h = float(res.node_head_m[nid])
            if h < best_h - 1e-9:
                best_h = h
                best_nid = nid
        return best_nid

    min_src_for_active_cache: Dict[FrozenSet[str], Optional[float]] = {}

    def estimate_min_source_head_for_target(active: Set[str]) -> Optional[float]:
        """Мінімальний H на насосі, щоб у всіх активних споживачів H ≥ індивідуальної цілі (для підказки)."""
        if not active:
            return None
        key_a = frozenset(active)
        if key_a in min_src_for_active_cache:
            return min_src_for_active_cache[key_a]
        out: Optional[float] = None
        lo, hi = 0.0, float(source_head_search_max_m)
        spec_hi = make_spec(active, hi)
        rh = compute_trunk_tree_steady(spec_hi)
        if rh.issues or not all_active_heads_meet_targets(rh, active, tol=1e-3):
            min_src_for_active_cache[key_a] = out
            return out
        for _ in range(48):
            mid = 0.5 * (lo + hi)
            spec_m = make_spec(active, mid)
            rm = compute_trunk_tree_steady(spec_m)
            if rm.issues:
                min_src_for_active_cache[key_a] = out
                return out
            if all_active_heads_meet_targets(rm, active, tol=1e-4):
                hi = mid
            else:
                lo = mid
        spec_f = make_spec(active, hi)
        rf = compute_trunk_tree_steady(spec_f)
        if not rf.issues:
            out = float(hi)
        min_src_for_active_cache[key_a] = out
        return out

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
    worst_min_consumer_head_m: Optional[float] = None
    worst_min_required_source_head_m: Optional[float] = None

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
        if use_required_pump_head:
            h_need = estimate_min_source_head_for_target(active)
            if h_need is None:
                per_slot[slot_i] = {
                    "issues": [
                        "Для заданих діаметрів і цілей H не вдалось оцінити потрібний напір насоса "
                        f"(межа пошуку {source_head_search_max_m:.1f} м)."
                    ],
                    "source_head_m": None,
                    "total_q_m3s": None,
                    "edge_q": {},
                    "min_consumer_head_m": None,
                    "head_deficit_m": None,
                    "chart_focus_consumer_id": None,
                    "min_required_source_head_m": None,
                }
                continue
            hs, res, iss = evaluate_at_given_pump_head(active, float(h_need))
        else:
            hs, res, iss = evaluate_at_given_pump_head(active, eff_pump_h)
        if hs is None or res is None:
            per_slot[slot_i] = {
                "issues": iss,
                "source_head_m": None,
                "total_q_m3s": None,
                "edge_q": {},
                "min_consumer_head_m": None,
                "head_deficit_m": None,
                "chart_focus_consumer_id": None,
                "min_required_source_head_m": None,
            }
            continue
        min_req_src: Optional[float] = None
        if use_required_pump_head:
            try:
                min_req_src = float(hs)
            except (TypeError, ValueError):
                min_req_src = None
        else:
            min_req_src = estimate_min_source_head_for_target(active)
        eq: Dict[Tuple[str, str], float] = {}
        eh: Dict[Tuple[str, str], Tuple[float, float]] = {}
        for e in res.edges:
            eq[(e.parent_id, e.child_id)] = float(e.q_m3s)
            eh[(e.parent_id, e.child_id)] = (
                float(getattr(e, "h_upstream_m", 0.0)),
                float(getattr(e, "h_downstream_m", 0.0)),
            )
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
            if use_required_pump_head:
                slot_issues.append(
                    f"Навіть при розрахунковому H_насос={float(hs):.2f} м лишається дефіцит напору "
                    f"(макс. ≈ {deficit_mx:.2f} м, найвища ціль ≈ {th_max:.1f} м). "
                    "Перевірте геометрію/цілі або збільште межу пошуку H."
                )
            else:
                slot_issues.append(
                    f"При H на джерелі={eff_pump_h:.2f} м (з поля «напір насоса»: {pump_h:.2f} м) мін. напір серед активних споживачів ≈ {mh_cons:.2f} м вод. ст.; "
                    f"є дефіцит до індивідуальних цілей (макс. ≈ {deficit_mx:.2f} м, найвища ціль ≈ {th_max:.1f} м). "
                    f"Збільшіть діаметри труб або напір насоса / скоригуйте рельєф всмоктування."
                )
                if min_req_src is not None:
                    slot_issues.append(
                        f"Орієнтовно мінімальний потрібний напір насоса, щоб виконати цілі по всіх активних споживачах: "
                        f"H ≥ {min_req_src:.2f} м вод. ст."
                    )
        slot_issues.extend(_issues_pipe_velocity(res, lim_vel))
        head_deficit_m: Optional[float] = None
        if deficit_mx is not None:
            head_deficit_m = float(deficit_mx)
        chart_focus_id: Optional[str] = None
        if head_deficit_m is not None and head_deficit_m > 1e-3:
            chart_focus_id = worst_deficit_consumer_id(res, active)
        if chart_focus_id is None and mh_cons is not None:
            chart_focus_id = min_head_consumer_id(res, active)
        per_slot[slot_i] = {
            "issues": slot_issues,
            "source_head_m": float(hs),
            "total_q_m3s": tq,
            "edge_q": _edge_float_map_to_json_keys(eq),
            "edge_h": _edge_head_pair_map_to_json_keys(eh),
            "node_head_m": dict(res.node_head_m),
            "min_consumer_head_m": float(mh_cons) if mh_cons is not None else None,
            "head_deficit_m": head_deficit_m,
            "chart_focus_consumer_id": chart_focus_id,
            "min_required_source_head_m": float(min_req_src) if min_req_src is not None else None,
        }
        if min_req_src is not None:
            mrv = float(min_req_src)
            worst_min_required_source_head_m = (
                mrv
                if worst_min_required_source_head_m is None
                else max(worst_min_required_source_head_m, mrv)
            )
        max_h = max(max_h, float(hs))
        max_q = max(max_q, tq)
        if mh_cons is not None:
            vmc = float(mh_cons)
            worst_min_consumer_head_m = (
                vmc
                if worst_min_consumer_head_m is None
                else min(worst_min_consumer_head_m, vmc)
            )

    seg_dominant: Dict[int, int] = {}
    for si, seg in enumerate(segs):
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) < 2:
            continue
        try:
            idxs = [int(x) for x in ni]
        except (TypeError, ValueError):
            continue
        best_q, best_slot, _bp = _best_q_slot_and_pair_for_segment_chain(
            nodes, idxs, edge_q_by_slot
        )
        if best_slot is not None and best_q > 1e-9:
            seg_dominant[si] = int(best_slot)

    uv_to_dmm: Dict[Tuple[int, int], float] = {}
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        p = props.get((pid, cid), {})
        dmm = float(p.get("d_inner_mm", default_d_inner_mm) or default_d_inner_mm)
        if dmm <= 0:
            dmm = default_d_inner_mm
        uv_to_dmm[(min(int(u), int(v)), max(int(u), int(v)))] = float(dmm)

    segment_hover: Dict[str, Dict[str, Any]] = {}
    for si, seg in enumerate(segs):
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) < 2:
            continue
        try:
            idxs = [int(x) for x in ni]
        except (TypeError, ValueError):
            continue
        best_q_h, best_slot_h, best_pair = _best_q_slot_and_pair_for_segment_chain(
            nodes, idxs, edge_q_by_slot
        )
        dom_slot: Optional[int] = None
        q_line = 0.0
        if best_slot_h is not None and best_q_h > 1e-9:
            dom_slot = best_slot_h
            q_line = float(best_q_h)
        if best_pair is not None:
            a0, b0 = best_pair
            dmm_seg = float(
                uv_to_dmm.get((min(a0, b0), max(a0, b0)), default_d_inner_mm)
            )
        else:
            dmm_seg = float(default_d_inner_mm)
            for a, b in zip(idxs[:-1], idxs[1:]):
                cand = float(
                    uv_to_dmm.get((min(int(a), int(b)), max(int(a), int(b))), 0.0)
                    or 0.0
                )
                if cand > 1e-9:
                    dmm_seg = max(dmm_seg, cand)
        if dmm_seg <= 1e-9:
            dmm_seg = float(default_d_inner_mm)
        segment_hover[str(si)] = {
            "d_inner_mm": dmm_seg,
            "q_m3s": q_line,
            "dominant_slot": dom_slot,
        }

    out = {
        "seg_dominant_slot": seg_dominant,
        "segment_hover": segment_hover,
        "envelope": {
            "max_source_head_m": float(max_h),
            "max_total_q_m3s": float(max_q),
            "worst_min_consumer_head_m": float(worst_min_consumer_head_m)
            if worst_min_consumer_head_m is not None
            else None,
            "min_required_source_head_m": float(worst_min_required_source_head_m)
            if worst_min_required_source_head_m is not None
            else None,
        },
        "per_slot": {str(k): v for k, v in per_slot.items()},
        "test_q_m3h": float(q_consumer_m3h),
        "test_h_m": float(target_head_m),
        "limits": dict(limits_block),
        "mode": dict(mode_block),
    }
    return out, global_issues


def _normalize_allowed_pipes_map_simple(ap: Any) -> Dict[str, Any]:
    """Легка копія normalize_allowed_pipes_map без залежності від hydraulics_core (shapely)."""
    out: Dict[str, Any] = {}
    if not isinstance(ap, dict):
        return out
    for mat, pns in ap.items():
        if not isinstance(pns, dict):
            continue
        mkey = str(mat).strip()
        if not mkey:
            continue
        sub: Dict[str, Any] = {}
        for pn, ods in pns.items():
            if not isinstance(ods, list):
                continue
            pk = str(pn).strip()
            olist = [str(o).strip() for o in ods if str(o).strip()]
            sub[pk] = olist
        if sub:
            out[mkey] = sub
    return out


def _pn_sort_tuple_simple(pn_val: Any) -> Tuple[Any, Any]:
    s = str(pn_val).replace(",", ".").strip()
    try:
        return (0, float(s))
    except ValueError:
        return (1, s)


def _allowed_pipe_candidates_sorted_trunk(
    eff_allowed: Mapping[str, Any], pipes_db: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Плоский список дозволених позицій каталогу (як allowed_pipe_candidates_sorted), без shapely."""
    out: List[Dict[str, Any]] = []
    eff = _normalize_allowed_pipes_map_simple(eff_allowed) or {}
    pdb = dict(pipes_db)
    for mat, pns in eff.items():
        mat_db = pdb.get(mat)
        if not isinstance(mat_db, dict):
            continue
        if not isinstance(pns, dict):
            continue
        for pn, ods in pns.items():
            if not isinstance(ods, list) or not ods:
                continue
            avail = mat_db.get(str(pn), {})
            if not avail:
                continue
            allowed_set = {str(o).strip() for o in ods if str(o).strip()}
            for d_nom, pipe_data in avail.items():
                if str(d_nom).strip() not in allowed_set:
                    continue
                d_inner = float(
                    pipe_data.get("id", float(d_nom))
                    if isinstance(pipe_data, dict)
                    else float(d_nom)
                )
                color = (
                    pipe_data.get("color", "#FFFFFF")
                    if isinstance(pipe_data, dict)
                    else "#FFFFFF"
                )
                out.append(
                    {
                        "mat": str(mat),
                        "pn": str(pn),
                        "d": int(float(d_nom)),
                        "inner": d_inner,
                        "color": color,
                        "c_hw": hazen_c_from_pipe_entry(pipe_data),
                    }
                )
    out.sort(key=lambda c: (c["inner"], c["mat"], _pn_sort_tuple_simple(c["pn"]), c["d"]))
    return out


def estimate_min_pump_head_m_uniform_largest_allowed_pipe(
    trunk_nodes: Sequence[Mapping[str, Any]],
    trunk_segments: Sequence[Mapping[str, Any]],
    irrigation_slots: Sequence[Sequence[str]],
    *,
    pipes_db: Mapping[str, Any],
    eff_allowed_pipes: Mapping[str, Any],
    q_consumer_m3h: float,
    target_head_m: float,
    max_pipe_velocity_mps: float = 0.0,
    surface_z_at_xy: Optional[Callable[[float, float], float]] = None,
) -> Optional[float]:
    """
    Оціночний мінімальний напір на джерелі (м вод. ст.), якщо усі ребра магістралі — одна труба
    з найбільшим внутрішнім діаметром з перетину eff_allowed_pipes ∩ pipes_db.
    Використовується для підстановки замість 0 у полі «Напір насоса (задано)».
    """
    eff = _normalize_allowed_pipes_map_simple(eff_allowed_pipes) or {}
    cands = _allowed_pipe_candidates_sorted_trunk(eff, pipes_db)
    if not cands:
        return None
    pick = cands[-1]
    nodes = list(trunk_nodes)
    segs = list(trunk_segments)
    if len(nodes) < 2 or not segs:
        return None
    directed, o_err = build_oriented_edges(nodes, segs)
    if directed is None or o_err or not directed:
        return None
    src_ok = False
    for i in range(len(nodes)):
        if str(nodes[i].get("kind", "")).strip().lower() == "source":
            if str(_node_id(nodes, i)).strip():
                src_ok = True
            break
    if not src_ok:
        return None
    edges_out: List[Dict[str, Any]] = []
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        si = _segment_index_for_uv(segs, int(u), int(v))
        if si is None or si < 0 or si >= len(segs):
            return None
        lm = float(_segment_length_m(nodes, segs[si]))
        if lm <= 1e-9:
            continue
        dn = float(pick["d"])
        din = float(pick["inner"])
        try:
            chw = float(pick.get("c_hw") or 140.0)
        except (TypeError, ValueError):
            chw = 140.0
        sec = [
            {
                "length_m": lm,
                "d_nom_mm": dn,
                "d_inner_mm": din,
                "material": str(pick["mat"]),
                "pn": str(pick["pn"]),
                "c_hw": chw,
                "head_loss_m": 0.0,
                "weight_kg": 0.0,
                "objective_cost": 0.0,
            }
        ]
        edges_out.append(
            {
                "parent_id": pid,
                "child_id": cid,
                "length_m": lm,
                "d_inner_mm": din,
                "c_hw": chw,
                "sections": copy.deepcopy(sec),
                "telescoped_sections": copy.deepcopy(sec),
            }
        )
    if not edges_out:
        return None
    payload = {"edges": edges_out}
    cache, issues = compute_trunk_irrigation_schedule_hydro(
        nodes,
        segs,
        irrigation_slots,
        payload,
        q_consumer_m3h=float(q_consumer_m3h),
        target_head_m=float(target_head_m),
        max_pipe_velocity_mps=float(max_pipe_velocity_mps),
        pump_operating_head_m=220.0,
        use_required_pump_head=False,
        surface_z_at_xy=surface_z_at_xy,
    )
    if issues:
        return None
    env = cache.get("envelope") if isinstance(cache.get("envelope"), dict) else {}
    mrs = env.get("min_required_source_head_m")
    if mrs is None:
        return None
    try:
        return max(0.0, float(mrs))
    except (TypeError, ValueError):
        return None


def _pick_by_edge_id(picks: Sequence[Mapping[str, Any]], pa: str, ch: str) -> Optional[Dict[str, Any]]:
    key = f"{pa}->{ch}"
    for row in picks:
        if not isinstance(row, dict):
            continue
        if str(row.get("edge_id", "")).strip() == key:
            return row
    return None


def _path_edges_parent_to_child(
    parent_edge_by_child: Mapping[str, Tuple[str, str]], *, source_id: str, leaf_id: str
) -> List[Tuple[str, str]]:
    """Ребра (батько, дитина) від джерела до листа (порядок від витоку)."""
    out_rev: List[Tuple[str, str]] = []
    cur = str(leaf_id).strip()
    src = str(source_id).strip()
    if not cur or not src:
        return []
    while cur != src:
        pr = parent_edge_by_child.get(cur)
        if pr is None:
            return []
        out_rev.append((str(pr[0]).strip(), str(pr[1]).strip()))
        cur = str(pr[0]).strip()
    return list(reversed(out_rev))


def _edge_float_map_to_json_keys(m: Mapping[Tuple[str, str], float]) -> Dict[str, float]:
    """JSON-сумісні ключі ребер «parent->child» (tuple ключі json.dump не приймає)."""
    out: Dict[str, float] = {}
    for (a, b), v in m.items():
        key = f"{str(a).strip()}->{str(b).strip()}"
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _edge_head_pair_map_to_json_keys(
    m: Mapping[Tuple[str, str], Tuple[float, float]]
) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for (a, b), t in m.items():
        key = f"{str(a).strip()}->{str(b).strip()}"
        if not isinstance(t, (list, tuple)) or len(t) < 2:
            continue
        try:
            out[key] = [float(t[0]), float(t[1])]
        except (TypeError, ValueError):
            continue
    return out


def _edge_head_loss_from_slot_row(row: Mapping[str, Any], pa: str, ch: str) -> Optional[float]:
    eh = row.get("edge_h")
    if not isinstance(eh, dict):
        return None
    pa, ch = str(pa).strip(), str(ch).strip()
    for k, t in eh.items():
        if not isinstance(t, (list, tuple)) or len(t) < 2:
            continue
        a: Optional[str] = None
        b: Optional[str] = None
        if isinstance(k, tuple) and len(k) >= 2:
            a, b = str(k[0]).strip(), str(k[1]).strip()
        elif isinstance(k, str):
            kk = k.strip()
            if "->" in kk:
                p0, p1 = kk.split("->", 1)
                a, b = p0.strip(), p1.strip()
        if a is None or b is None:
            continue
        if {a, b} != {pa, ch}:
            continue
        try:
            return float(t[0]) - float(t[1])
        except (TypeError, ValueError):
            return None
    return None


def refine_trunk_picks_pressure_tightening(
    trunk_nodes: Sequence[Mapping[str, Any]],
    trunk_segments: Sequence[Mapping[str, Any]],
    irrigation_slots: Sequence[Sequence[str]],
    picks: List[Dict[str, Any]],
    *,
    pump_operating_head_m: float,
    schedule_target_head_m: float,
    default_q_m3h: float,
    max_pipe_velocity_mps: float,
    options: Sequence,
    edge_len: Dict[Tuple[str, str], float],
    edge_peak_q: Dict[Tuple[str, str], float],
    parent_edge_by_child: Mapping[str, Tuple[str, str]],
    min_segment_length_m: float,
    max_sections_per_edge: int,
    objective: str,
    length_round_step_m: float,
    head_tol_m: float = 0.28,
    max_iters: int = 28,
    surface_z_at_xy: Optional[Callable[[float, float], float]] = None,
) -> List[str]:
    """
    Після мінімізації вартості за бюджетом ΔH — «підтягнути» рішення до цільового напору на споживачах:
    збільшувати допустимі втрати HW на окремих ребрах (дешевший телескоп / менший d),
    поки у всіх слотах H ≥ цілі з запасом не більшим за head_tol_m (і H на насосі не перевищено — фіксований pump_operating_head_m).
    """
    msgs: List[str] = []
    nodes = list(trunk_nodes)
    slots_list = list(irrigation_slots) if irrigation_slots else []
    if not nodes or not picks:
        return msgs
    src_id = ""
    for i in range(len(nodes)):
        if str(nodes[i].get("kind", "")).strip().lower() == "source":
            src_id = str(nodes[i].get("id", "")).strip() or f"T{i}"
            break
    if not src_id:
        return msgs
    id_to_idx = {_node_id(nodes, i): i for i in range(len(nodes))}
    objective = str(objective or "weight").strip().lower()
    if objective in ("cost_index",):
        objective = "money"
    if objective not in ("weight", "money"):
        objective = "weight"

    def _payload_edges_from_picks() -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for row in picks:
            if not isinstance(row, dict):
                continue
            eid = str(row.get("edge_id", "")).strip()
            if "->" not in eid:
                continue
            pa, pb = eid.split("->", 1)
            pa, pb = pa.strip(), pb.strip()
            secs = row.get("sections")
            if not isinstance(secs, list):
                secs = []
            rows.append(
                {
                    "parent_id": pa,
                    "child_id": pb,
                    "d_inner_mm": float(row.get("d_inner_mm", 90.0) or 90.0),
                    "c_hw": float(row.get("c_hw", 140.0) or 140.0),
                    "sections": copy.deepcopy(secs),
                    "telescoped_sections": copy.deepcopy(row.get("telescoped_sections"))
                    if isinstance(row.get("telescoped_sections"), list)
                    else copy.deepcopy(secs),
                }
            )
        return rows

    def _all_slots_pressure_ok(payload_edges: List[Dict[str, Any]]) -> bool:
        cache, giss = compute_trunk_irrigation_schedule_hydro(
            nodes,
            trunk_segments,
            slots_list,
            {"edges": payload_edges},
            q_consumer_m3h=float(default_q_m3h),
            target_head_m=float(schedule_target_head_m),
            max_pipe_velocity_mps=float(max_pipe_velocity_mps),
            pump_operating_head_m=float(pump_operating_head_m),
            use_required_pump_head=False,
            surface_z_at_xy=surface_z_at_xy,
        )
        if giss:
            return False
        for sk, row in (cache.get("per_slot") or {}).items():
            if not isinstance(row, dict):
                continue
            if row.get("issues"):
                return False
            try:
                si = int(sk)
            except (TypeError, ValueError):
                continue
            if si < 0 or si >= len(slots_list):
                continue
            raw_slot = slots_list[si]
            if not isinstance(raw_slot, list) or not raw_slot:
                continue
            active = {str(x).strip() for x in raw_slot if str(x).strip()}
            nh = row.get("node_head_m")
            if not isinstance(nh, dict):
                return False
            for nid in active:
                if nid not in nh:
                    return False
                h = float(nh[nid])
                tgt = _trunk_consumer_schedule_target_head_m(
                    nodes, id_to_idx, nid, float(schedule_target_head_m)
                )
                if h < tgt - 1e-3:
                    return False
        return True

    def _min_margin_m(payload_edges: List[Dict[str, Any]]) -> float:
        cache, giss = compute_trunk_irrigation_schedule_hydro(
            nodes,
            trunk_segments,
            slots_list,
            {"edges": payload_edges},
            q_consumer_m3h=float(default_q_m3h),
            target_head_m=float(schedule_target_head_m),
            max_pipe_velocity_mps=float(max_pipe_velocity_mps),
            pump_operating_head_m=float(pump_operating_head_m),
            use_required_pump_head=False,
            surface_z_at_xy=surface_z_at_xy,
        )
        if giss:
            return -1.0
        m_best = 1e18
        for sk, row in (cache.get("per_slot") or {}).items():
            if not isinstance(row, dict) or row.get("issues"):
                continue
            try:
                si = int(sk)
            except (TypeError, ValueError):
                continue
            if si < 0 or si >= len(slots_list):
                continue
            raw_slot = slots_list[si]
            if not isinstance(raw_slot, list) or not raw_slot:
                continue
            active = {str(x).strip() for x in raw_slot if str(x).strip()}
            nh = row.get("node_head_m")
            if not isinstance(nh, dict):
                continue
            for nid in active:
                if nid not in nh:
                    continue
                h = float(nh[nid])
                tgt = _trunk_consumer_schedule_target_head_m(
                    nodes, id_to_idx, nid, float(schedule_target_head_m)
                )
                m_best = min(m_best, h - tgt)
        return float(m_best) if m_best < 0.99e18 else -1.0

    edges_payload = _payload_edges_from_picks()
    if not edges_payload or not _all_slots_pressure_ok(edges_payload):
        return msgs

    improved = 0
    for _it in range(int(max_iters)):
        margin = _min_margin_m(edges_payload)
        if margin < 0 or margin <= float(head_tol_m) + 1e-3:
            break
        cache, _g = compute_trunk_irrigation_schedule_hydro(
            nodes,
            trunk_segments,
            slots_list,
            {"edges": edges_payload},
            q_consumer_m3h=float(default_q_m3h),
            target_head_m=float(schedule_target_head_m),
            max_pipe_velocity_mps=float(max_pipe_velocity_mps),
            pump_operating_head_m=float(pump_operating_head_m),
            use_required_pump_head=False,
            surface_z_at_xy=surface_z_at_xy,
        )
        crit_slot: Optional[int] = None
        crit_nid: Optional[str] = None
        crit_m = 1e18
        for sk, row in (cache.get("per_slot") or {}).items():
            if not isinstance(row, dict) or row.get("issues"):
                continue
            try:
                si = int(sk)
            except (TypeError, ValueError):
                continue
            if si < 0 or si >= len(slots_list):
                continue
            raw_slot = slots_list[si]
            if not isinstance(raw_slot, list) or not raw_slot:
                continue
            active = {str(x).strip() for x in raw_slot if str(x).strip()}
            nh = row.get("node_head_m")
            if not isinstance(nh, dict):
                continue
            for nid in active:
                if nid not in nh:
                    continue
                h = float(nh[nid])
                tgt = _trunk_consumer_schedule_target_head_m(
                    nodes, id_to_idx, nid, float(schedule_target_head_m)
                )
                mm = h - tgt
                if mm < crit_m - 1e-9:
                    crit_m = mm
                    crit_slot = si
                    crit_nid = nid
        if crit_slot is None or crit_nid is None:
            break
        row_ref = (cache.get("per_slot") or {}).get(str(int(crit_slot)))
        if not isinstance(row_ref, dict):
            break
        path = _path_edges_parent_to_child(parent_edge_by_child, source_id=src_id, leaf_id=str(crit_nid))
        if not path:
            break
        inc_cap = max(0.0, float(margin) - float(head_tol_m))
        if inc_cap <= 1e-6:
            break
        step = min(0.55, max(0.06, inc_cap * 0.65))
        progressed = False
        for pa, ch in path:
            pick = _pick_by_edge_id(picks, pa, ch)
            if pick is None:
                continue
            lm = float(edge_len.get((pa, ch), 0.0))
            if lm <= 1e-9:
                continue
            qv = float(edge_peak_q.get((pa, ch), 0.0))
            hf_now = _edge_head_loss_from_slot_row(row_ref, pa, ch)
            if hf_now is None or hf_now <= 1e-9:
                continue
            old_obj = float(pick.get("objective_cost", 1e18) or 1e18)
            new_budget = float(hf_now) + float(step)
            edge_opt = optimize_single_line_allocation_by_weight(
                total_length_m=lm,
                q_m3s=qv,
                options=options,
                constraints=OptimizationConstraints(
                    max_head_loss_m=float(new_budget),
                    max_velocity_m_s=float(max_pipe_velocity_mps),
                    min_segment_length_m=float(min_segment_length_m),
                    max_active_segments=max(1, int(max_sections_per_edge)),
                    objective=str(objective),
                    length_round_step_m=float(length_round_step_m),
                ),
            )
            if not edge_opt.feasible or not edge_opt.allocations:
                continue
            new_obj = float(edge_opt.total_objective_cost)
            if new_obj >= old_obj - 1e-6:
                continue
            tent_edges = copy.deepcopy(edges_payload)
            tent_row: Optional[Dict[str, Any]] = None
            for er in tent_edges:
                if str(er.get("parent_id")) == pa and str(er.get("child_id")) == ch:
                    tent_row = er
                    secs = [
                        {
                            "length_m": float(a.length_m),
                            "d_nom_mm": float(a.d_nom_mm),
                            "d_inner_mm": float(a.d_inner_mm),
                            "material": str(a.material),
                            "pn": str(a.pn),
                            "head_loss_m": float(a.head_loss_m),
                            "weight_kg": float(a.weight_kg),
                            "objective_cost": float(a.objective_cost),
                        }
                        for a in edge_opt.allocations
                    ]
                    secs = _collapse_short_telescope_sections(secs)
                    er["sections"] = secs
                    er["telescoped_sections"] = copy.deepcopy(secs)
                    try:
                        er["d_inner_mm"] = min(float(x.get("d_inner_mm", 90.0)) for x in secs if isinstance(x, dict))
                    except (TypeError, ValueError):
                        er["d_inner_mm"] = float(edge_opt.allocations[0].d_inner_mm)
                    break
            if tent_row is None:
                continue
            if not _all_slots_pressure_ok(tent_edges):
                continue
            edges_payload = tent_edges
            pick["sections"] = copy.deepcopy(tent_row["sections"])
            pick["telescoped_sections"] = copy.deepcopy(
                tent_row.get("telescoped_sections", tent_row["sections"])
            )
            sec_rows = pick.get("sections") if isinstance(pick.get("sections"), list) else []
            pick["head_loss_m"] = sum(float(x.get("head_loss_m", 0.0) or 0.0) for x in sec_rows if isinstance(x, dict))
            pick["weight_kg"] = sum(float(x.get("weight_kg", 0.0) or 0.0) for x in sec_rows if isinstance(x, dict))
            pick["objective_cost"] = sum(
                float(x.get("objective_cost", 0.0) or 0.0) for x in sec_rows if isinstance(x, dict)
            )
            try:
                pick["d_inner_mm"] = float(tent_row["d_inner_mm"])
            except (TypeError, ValueError, KeyError):
                pass
            if sec_rows:
                try:
                    pick["d_nom_mm"] = float(sec_rows[-1].get("d_nom_mm", pick.get("d_nom_mm", 0.0)) or 0.0)
                except (TypeError, ValueError):
                    pass
            improved += 1
            progressed = True
            break
        if not progressed:
            break

    if improved > 0:
        msgs.append(f"Підтягування до цільового H: оновлено ребер (ітерацій успіху): {improved}.")
    return msgs


def optimize_trunk_diameters_by_weight(
    trunk_nodes: Sequence[Mapping[str, Any]],
    trunk_segments: Sequence[Mapping[str, Any]],
    irrigation_slots: Sequence[Sequence[str]],
    *,
    pipes_db: Mapping[str, Any],
    material: str,
    allowed_pipes: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None,
    max_head_loss_m: float,
    max_velocity_mps: float = 0.0,
    default_q_m3h: float = 60.0,
    min_segment_length_m: float = 0.0,
    c_hw: float = 140.0,
    objective: str = "weight",
    max_sections_per_edge: int = 2,
    pump_operating_head_m: Optional[float] = None,
    schedule_target_head_m: Optional[float] = None,
    surface_z_at_xy: Optional[Callable[[float, float], float]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Рекомендація діаметрів магістралі за критерієм вартості (weight/money).
    Q для ребра береться як максимум по слотах поливу.
    """
    issues: List[str] = []
    objective_clean = str(objective or "weight").strip().lower()
    if objective_clean in ("cost_index",):
        objective_clean = "money"
    if objective_clean not in ("weight", "money"):
        objective_clean = "weight"
    nodes = list(trunk_nodes)
    segs = list(trunk_segments)
    if not nodes or not segs:
        return {"feasible": False, "message": "Немає вузлів або сегментів."}, ["Немає вузлів або сегментів."]
    directed, o_err = build_oriented_edges(nodes, segs)
    if directed is None or o_err:
        return {"feasible": False, "message": "Не вдалося орієнтувати граф магістралі."}, list(
            o_err or ["Не вдалося орієнтувати граф магістралі."]
        )
    parent_edge_by_child: Dict[str, Tuple[str, str]] = {}
    for u, v in directed:
        parent_edge_by_child[_node_id(nodes, v)] = (_node_id(nodes, u), _node_id(nodes, v))
    id_to_idx = {_node_id(nodes, i): i for i in range(len(nodes))}
    child_map: Dict[str, List[str]] = {}
    for uu, vv in directed:
        child_map.setdefault(_node_id(nodes, uu), []).append(_node_id(nodes, vv))
    edge_peak_q: Dict[Tuple[str, str], float] = {
        (_node_id(nodes, u), _node_id(nodes, v)): 0.0 for u, v in directed
    }
    slots_list = list(irrigation_slots) if irrigation_slots else []
    for slot in slots_list:
        active = {str(x).strip() for x in (slot or []) if str(x).strip() in id_to_idx}
        for u, v in directed:
            pid, cid = _node_id(nodes, u), _node_id(nodes, v)
            # Якщо у піддереві child є активні споживачі, Q на ребрі = їх сума.
            # Тут використовуємо легкий DFS без залежності від trunk_tree_payload.
            q_m3h = 0.0
            stack = [cid]
            visited: Set[str] = set()
            while stack:
                nid = stack.pop()
                if nid in visited:
                    continue
                visited.add(nid)
                if nid in active:
                    q_m3h += _trunk_consumer_schedule_q_m3h(nodes, id_to_idx, nid, float(default_q_m3h))
                for ch in child_map.get(nid, []):
                    stack.append(ch)
            edge_peak_q[(pid, cid)] = max(edge_peak_q[(pid, cid)], q_m3h / 3600.0)

    options = build_pipe_options_from_db(
        pipes_db,
        material=material,
        allowed_pipes=allowed_pipes,
        c_hw=float(c_hw),
    )
    if objective_clean == "money" and not any(float(o.price_per_m) > 1e-12 for o in options):
        return (
            {
                "feasible": False,
                "message": "Для критерію money у каталозі потрібні додатні ціни price_per_m (грн/м).",
                "total_weight_kg": 0.0,
                "total_head_loss_m": 0.0,
                "total_objective_cost": 0.0,
                "objective": "money",
                "picks": [],
            },
            [
                "У дозволених трубах для обраного матеріалу немає price_per_m. "
                "Оберіть критерій «weight» або додайте ціни в базу труб."
            ],
        )
    min_seg = max(0.0, float(min_segment_length_m))
    # Округлення довжин секцій — лише коли з’явиться окремий параметр у проєкті; зараз 0 (без побічних ефектів).
    length_round_step_m = 0.0
    edge_len: Dict[Tuple[str, str], float] = {}
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        si = _segment_index_for_uv(segs, u, v)
        if si is None:
            issues.append(f"Немає сегмента для ребра {pid}→{cid}.")
            continue
        lm = _segment_length_m(nodes, segs[si])
        if lm <= 1e-9:
            issues.append(f"Нульова довжина сегмента {pid}→{cid}.")
            continue
        edge_len[(pid, cid)] = float(lm)
    if issues:
        return {"feasible": False, "message": "Помилки геометрії/топології."}, issues

    absorbed_by: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for key, lm in edge_len.items():
        if min_seg <= 1e-9 or lm + 1e-9 >= min_seg:
            continue
        parent = parent_edge_by_child.get(key[0])
        if parent is None:
            continue
        while parent in absorbed_by:
            parent = absorbed_by[parent]
        absorbed_by[key] = parent

    agg_len: Dict[Tuple[str, str], float] = {}
    for key, lm in edge_len.items():
        root = key
        while root in absorbed_by:
            root = absorbed_by[root]
        agg_len[root] = agg_len.get(root, 0.0) + float(lm)

    demands: List[SegmentDemand] = []
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        key = (pid, cid)
        if key in absorbed_by:
            continue
        lm = float(agg_len.get(key, edge_len.get(key, 0.0)))
        if lm <= 1e-9:
            continue
        demands.append(
            SegmentDemand(
                id=f"{pid}->{cid}",
                length_m=float(lm),
                q_m3s=float(edge_peak_q.get((pid, cid), 0.0)),
                min_length_m=0.0,
            )
        )
    res = optimize_fixed_topology_by_weight(
        demands,
        options,
        OptimizationConstraints(
            max_head_loss_m=float(max_head_loss_m),
            max_velocity_m_s=float(max_velocity_mps),
            min_segment_length_m=0.0,
            objective=str(objective_clean),
        ),
    )
    chosen = {c.segment_id: c for c in res.choices}
    # Глобальний slack: нерозподілений резерв бюджету після вибору одиночних труб per-ребро.
    # Розподіляємо його пропорційно до довжини ребра і додаємо до edge_budget_hf, щоб
    # внутрішній телескоп-солвер реально мав місце підібрати комбінацію «товста + тонка»
    # (напр., 110/90 250/50 для одного довгого ребра з бюджетом 10 м, де солвер із
    # лише 7.77 м поточних втрат 110 інакше відсіває 90/110 як infeasible).
    try:
        total_res_hf = float(res.total_head_loss_m or 0.0)
    except (TypeError, ValueError):
        total_res_hf = 0.0
    global_slack_hf = max(0.0, float(max_head_loss_m) - total_res_hf)
    total_edge_len_m = 0.0
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        key = (pid, cid)
        total_edge_len_m += float(edge_len.get(key, 0.0))
    picks: List[Dict[str, Any]] = []
    total_weight = 0.0
    total_objective_cost = 0.0
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        key = (pid, cid)
        root = key
        while root in absorbed_by:
            root = absorbed_by[root]
        ch = chosen.get(f"{root[0]}->{root[1]}")
        if ch is None:
            continue
        lm = float(edge_len.get(key, 0.0))
        # Вага ребра рахується від його фактичної довжини з діаметром поглинача.
        unit_w = float(ch.weight_kg / max(1e-9, float(agg_len.get(root, lm if lm > 0 else 1.0))))
        base_edge_budget = max(0.0, float(ch.head_loss_m)) * (
            lm / max(1e-9, float(agg_len.get(root, lm if lm > 0 else 1.0)))
        )
        slack_share = (
            global_slack_hf * (lm / total_edge_len_m)
            if total_edge_len_m > 1e-9
            else 0.0
        )
        edge_budget_hf = float(base_edge_budget) + float(slack_share)
        telescoped_sections: List[Dict[str, Any]] = []
        telescoped_hf = edge_budget_hf
        telescoped_weight = unit_w * lm
        telescoped_obj = float(ch.objective_cost / max(1e-9, float(agg_len.get(root, lm if lm > 0 else 1.0)))) * lm
        if lm > 1e-9:
            edge_opt = optimize_single_line_allocation_by_weight(
                total_length_m=lm,
                q_m3s=float(edge_peak_q.get((pid, cid), 0.0)),
                options=options,
                constraints=OptimizationConstraints(
                    max_head_loss_m=float(edge_budget_hf),
                    max_velocity_m_s=float(max_velocity_mps),
                    min_segment_length_m=float(min_seg),
                    max_active_segments=max(1, int(max_sections_per_edge)),
                    objective=str(objective_clean),
                    length_round_step_m=float(length_round_step_m),
                ),
            )
            if edge_opt.feasible and edge_opt.allocations:
                telescoped_sections = _collapse_short_telescope_sections([
                    {
                        "length_m": float(a.length_m),
                        "d_nom_mm": float(a.d_nom_mm),
                        "d_inner_mm": float(a.d_inner_mm),
                        "material": str(a.material),
                        "pn": str(a.pn),
                        "head_loss_m": float(a.head_loss_m),
                        "weight_kg": float(a.weight_kg),
                        "objective_cost": float(a.objective_cost),
                    }
                    for a in edge_opt.allocations
                ])
                telescoped_hf = sum(float(x.get("head_loss_m", 0.0) or 0.0) for x in telescoped_sections)
                telescoped_weight = sum(float(x.get("weight_kg", 0.0) or 0.0) for x in telescoped_sections)
                telescoped_obj = sum(
                    float(x.get("objective_cost", 0.0) or 0.0) for x in telescoped_sections
                )
        if not telescoped_sections and lm > 1e-9:
            # Fallback: single-line allocator не знайшов телескоп — кладемо одну секцію з picks top-level
            # (d_nom, d_inner, material, pn). Інакше ребро в JSON лишається без sections і UI/збереження
            # не «бачить» фактично обраної труби (pipe_material/pipe_pn/pipe_od).
            telescoped_sections = [
                {
                    "length_m": float(lm),
                    "d_nom_mm": float(ch.d_nom_mm),
                    "d_inner_mm": float(ch.d_inner_mm),
                    "material": str(ch.material),
                    "pn": str(ch.pn),
                    "head_loss_m": float(telescoped_hf),
                    "weight_kg": float(telescoped_weight),
                    "objective_cost": float(telescoped_obj),
                }
            ]
        picks.append(
            {
                "edge_id": f"{pid}->{cid}",
                "d_nom_mm": ch.d_nom_mm,
                "d_inner_mm": ch.d_inner_mm,
                "material": ch.material,
                "pn": ch.pn,
                "head_loss_m": telescoped_hf,
                "velocity_m_s": ch.velocity_m_s,
                "weight_kg": telescoped_weight,
                "objective_cost": telescoped_obj,
                "sections": telescoped_sections,
                "telescoped_sections": copy.deepcopy(telescoped_sections),
            }
        )
        total_weight += telescoped_weight
        total_objective_cost += telescoped_obj
    if (
        bool(res.feasible)
        and picks
        and pump_operating_head_m is not None
        and schedule_target_head_m is not None
    ):
        rmsgs = refine_trunk_picks_pressure_tightening(
            nodes,
            segs,
            slots_list,
            picks,
            pump_operating_head_m=float(pump_operating_head_m),
            schedule_target_head_m=float(schedule_target_head_m),
            default_q_m3h=float(default_q_m3h),
            max_pipe_velocity_mps=float(max_velocity_mps),
            options=options,
            edge_len=edge_len,
            edge_peak_q=edge_peak_q,
            parent_edge_by_child=parent_edge_by_child,
            min_segment_length_m=min_seg,
            max_sections_per_edge=max_sections_per_edge,
            objective=str(objective_clean),
            length_round_step_m=float(length_round_step_m),
            surface_z_at_xy=surface_z_at_xy,
        )
        if rmsgs:
            issues.extend(rmsgs)
        total_weight = sum(float(p.get("weight_kg", 0.0) or 0.0) for p in picks if isinstance(p, dict))
        total_objective_cost = sum(
            float(p.get("objective_cost", 0.0) or 0.0) for p in picks if isinstance(p, dict)
        )
    msg = str(res.message)
    if absorbed_by:
        msg = f"{msg} Короткі сегменти поглинуто попередніми: {len(absorbed_by)}."
    return {
        "feasible": bool(res.feasible),
        "message": msg,
        "total_weight_kg": float(total_weight if picks else res.total_weight_kg),
        "total_head_loss_m": float(res.total_head_loss_m),
        "total_objective_cost": float(total_objective_cost if picks else res.total_objective_cost),
        "objective": str(objective_clean),
        "picks": picks,
    }, issues