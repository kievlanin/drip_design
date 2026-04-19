"""
Розрахунок магістралі як дерева (відкритий граф без циклів).

Узгоджено з проєктом: втрати Hazen–Williams як у lateral_drip_core (D у метрах, Q у м³/с).
Вузли: витік (source), поворот/розгалуження (bend, junction), споживання (consumption) з заданою Q.

Стійкий режим: одна п'єзометрична висота на витоку; у вузлах consumption — локальні відбори q_demand_m3s.
Опційно dz_m на ребрі: Φ_дитина = Φ_батько − hf + dz_m (dz_m = z_батько − z_дитина за замовчуванням).

Подальше розширення: часові ряди Q(t), локальні втрати на трійниках, прив’язка до field_blocks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C
from .lateral_drip_core import hazen_williams_hloss_m


@dataclass(frozen=True)
class TrunkTreeNode:
    """Вузол топології магістралі."""

    id: str
    kind: str
    """«source» | «bend» | «junction» | «consumption»"""
    q_demand_m3s: float = 0.0
    """Для consumption: витрата, що «знімається» в цьому вузлі (м³/с), навіть якщо вузол не лист."""


@dataclass(frozen=True)
class TrunkTreeEdge:
    """Орієнтоване ребро батько → дитина (трубна ділянка)."""

    parent_id: str
    child_id: str
    length_m: float
    d_inner_mm: float
    c_hw: float = DEFAULT_HAZEN_WILLIAMS_C
    dz_m: float = 0.0
    sections: Tuple[Tuple[float, float, float], ...] = ()
    """
    Упор на п'єзометричну лінію: H_дитина = H_батько − hf + dz_m.
    Типово dz_m = z_батько − z_дитина (напір збільшується при спуску дитини вниз).
    """


@dataclass(frozen=True)
class TrunkTreeSpec:
    """Повна специфікація дерева для одного стійкого кроку."""

    nodes: Tuple[TrunkTreeNode, ...]
    edges: Tuple[TrunkTreeEdge, ...]
    source_id: str
    source_head_m: float
    """П'єзометричний напір (або тисковий у спрощеному режимі без рельєфу) у витоку, м."""


@dataclass(frozen=True)
class TrunkEdgeResult:
    parent_id: str
    child_id: str
    q_m3s: float
    length_m: float
    d_inner_mm: float
    c_hw: float
    head_loss_m: float
    dz_m: float
    h_upstream_m: float
    h_downstream_m: float
    velocity_m_s: float


@dataclass
class TrunkTreeResult:
    """Результат обходу: напори у вузлах і деталізація по ребрах."""

    node_head_m: Dict[str, float]
    edges: Tuple[TrunkEdgeResult, ...]
    total_q_m3s: float
    issues: Tuple[str, ...] = ()


def validate_trunk_tree(spec: TrunkTreeSpec) -> List[str]:
    """Повертає список помилок; порожній список означає, що структура прийнятна."""
    err: List[str] = []
    if not spec.nodes:
        err.append("Порожній список вузлів.")
        return err
    nodes = {n.id: n for n in spec.nodes}
    if len(nodes) != len(spec.nodes):
        err.append("Дубльовані id вузлів.")
        return err

    if spec.source_id not in nodes:
        err.append(f"Витік source_id={spec.source_id!r} відсутній серед вузлів.")
        return err

    sources = [n for n in spec.nodes if n.kind == "source"]
    if len(sources) != 1:
        err.append(f"Очікується рівно один вузол kind=source, знайдено {len(sources)}.")
    if sources and sources[0].id != spec.source_id:
        err.append("Єдиний source має збігатися з source_id.")

    kinds = {"source", "bend", "junction", "consumption"}
    for n in spec.nodes:
        if n.kind not in kinds:
            err.append(f"Вузол {n.id!r}: невідомий kind={n.kind!r}.")
        if n.kind == "consumption" and n.q_demand_m3s < -1e-15:
            err.append(f"Вузол споживання {n.id!r}: q_demand_m3s не може бути від’ємним.")
        if n.kind == "source" and n.q_demand_m3s > 1e-15:
            err.append(f"Витік {n.id!r}: q_demand_m3s має бути 0 (використовуйте дочірні ребра).")

    seen_pairs = set()
    children: Dict[str, List[str]] = {nid: [] for nid in nodes}
    parents: Dict[str, List[str]] = {nid: [] for nid in nodes}

    for e in spec.edges:
        if e.parent_id not in nodes or e.child_id not in nodes:
            err.append(
                f"Ребро {e.parent_id!r}→{e.child_id!r}: невідомий вузол."
            )
            continue
        key = (e.parent_id, e.child_id)
        if key in seen_pairs:
            err.append(f"Повтор ребра {e.parent_id!r}→{e.child_id!r}.")
        seen_pairs.add(key)
        children[e.parent_id].append(e.child_id)
        parents[e.child_id].append(e.parent_id)
        if e.length_m < -1e-9:
            err.append(f"Ребро {e.parent_id!r}→{e.child_id!r}: length_m < 0.")
        if e.d_inner_mm <= 0:
            err.append(f"Ребро {e.parent_id!r}→{e.child_id!r}: d_inner_mm має бути > 0.")
        if e.sections:
            s_len = 0.0
            for si, sec in enumerate(e.sections):
                try:
                    lm, dmm, chw = float(sec[0]), float(sec[1]), float(sec[2])
                except (TypeError, ValueError, IndexError):
                    err.append(f"Ребро {e.parent_id!r}→{e.child_id!r}: некоректна секція #{si+1}.")
                    continue
                if lm <= 0.0 or dmm <= 0.0:
                    err.append(f"Ребро {e.parent_id!r}→{e.child_id!r}: секція #{si+1} має невалідні L/d.")
                s_len += max(0.0, lm)
            if s_len > 1e-9 and abs(s_len - float(e.length_m)) > 1e-4:
                err.append(
                    f"Ребро {e.parent_id!r}→{e.child_id!r}: сума довжин секцій {s_len:.3f} м "
                    f"не дорівнює length_m {float(e.length_m):.3f} м."
                )

    for nid, ps in parents.items():
        if nid == spec.source_id:
            if ps:
                err.append("До витоку не може входити ребро.")
        elif len(ps) != 1:
            err.append(
                f"Вузол {nid!r}: очікується рівно один батько у дереві, має {len(ps)}."
            )

    # Зв’язність і відсутність циклів: BFS від витоку
    visited = {spec.source_id}
    stack = [spec.source_id]
    while stack:
        u = stack.pop()
        for v in children.get(u, []):
            if v in visited:
                err.append(f"Виявлено цикл або повторний шлях до вузла {v!r}.")
                break
            visited.add(v)
            stack.append(v)
        else:
            continue
        break

    if len(visited) != len(nodes):
        missing = set(nodes) - visited
        err.append(f"Вузли недосяжні з витоку: {sorted(missing)}.")

    if len(spec.edges) != len(nodes) - 1 and len(nodes) > 0:
        err.append(
            f"Для дерева очікується |E| = |V|−1; |V|={len(nodes)}, |E|={len(spec.edges)}."
        )

    for n in spec.nodes:
        if n.kind == "consumption" and len(children.get(n.id, [])) > 1:
            err.append(
                f"Споживання {n.id!r}: допускається не більше одного дочірнього ребра (ланцюг відборів)."
            )
        if n.kind != "consumption" and n.id != spec.source_id and not children.get(n.id):
            err.append(
                f"Внутрішній вузол {n.id!r} без дочірніх ребер (очікується хоча б одне ребро вниз)."
            )

    return err


def _build_children_map(edges: Sequence[TrunkTreeEdge]) -> Dict[str, List[TrunkTreeEdge]]:
    ch: Dict[str, List[TrunkTreeEdge]] = {}
    for e in edges:
        ch.setdefault(e.parent_id, []).append(e)
    return ch


def _subtree_q_m3s(
    node_id: str,
    nodes: Mapping[str, TrunkTreeNode],
    children_edges: Mapping[str, Sequence[TrunkTreeEdge]],
    memo: MutableMapping[str, float],
) -> float:
    if node_id in memo:
        return memo[node_id]
    n = nodes[node_id]
    s = max(0.0, float(n.q_demand_m3s)) if n.kind == "consumption" else 0.0
    for e in children_edges.get(node_id, ()):
        s += _subtree_q_m3s(e.child_id, nodes, children_edges, memo)
    memo[node_id] = s
    return s


def compute_trunk_tree_steady(spec: TrunkTreeSpec) -> TrunkTreeResult:
    """
    Стійкий розрахунок: підсумовування Q з листя до витоку, рознесення H від витоку вниз по HW.
    При помилці валідації повертає TrunkTreeResult з порожніми полями та заповненими issues.
    """
    issues = tuple(validate_trunk_tree(spec))
    if issues:
        return TrunkTreeResult(
            node_head_m={},
            edges=tuple(),
            total_q_m3s=0.0,
            issues=issues,
        )

    nodes = {n.id: n for n in spec.nodes}
    children_edges = _build_children_map(spec.edges)
    memo_q: Dict[str, float] = {}
    total_q = sum(
        _subtree_q_m3s(e.child_id, nodes, children_edges, memo_q)
        for e in children_edges.get(spec.source_id, ())
    )

    node_head: Dict[str, float] = {}
    edge_results: List[TrunkEdgeResult] = []

    def dfs_visit(parent_id: str, h_parent: float) -> None:
        node_head[parent_id] = h_parent
        for e in children_edges.get(parent_id, ()):
            q = _subtree_q_m3s(e.child_id, nodes, children_edges, memo_q)
            if e.sections:
                hf = 0.0
                v_max = 0.0
                d_eff_mm = float(e.d_inner_mm)
                for sec in e.sections:
                    lm, dmm, chw = float(sec[0]), float(sec[1]), float(sec[2])
                    d_m_sec = dmm / 1000.0
                    hf += hazen_williams_hloss_m(q, lm, d_m_sec, chw)
                    area_sec = math.pi * (d_m_sec / 2.0) ** 2
                    v_sec = q / area_sec if area_sec > 1e-18 else 0.0
                    v_max = max(v_max, v_sec)
                    d_eff_mm = min(d_eff_mm, dmm)
                v = v_max
            else:
                d_m = float(e.d_inner_mm) / 1000.0
                hf = hazen_williams_hloss_m(q, float(e.length_m), d_m, float(e.c_hw))
                area = math.pi * (d_m / 2.0) ** 2
                v = q / area if area > 1e-18 else 0.0
                d_eff_mm = float(e.d_inner_mm)
            h_child = h_parent - hf + float(e.dz_m)
            edge_results.append(
                TrunkEdgeResult(
                    parent_id=e.parent_id,
                    child_id=e.child_id,
                    q_m3s=q,
                    length_m=float(e.length_m),
                    d_inner_mm=float(d_eff_mm),
                    c_hw=float(e.c_hw),
                    head_loss_m=hf,
                    dz_m=float(e.dz_m),
                    h_upstream_m=h_parent,
                    h_downstream_m=h_child,
                    velocity_m_s=v,
                )
            )
            dfs_visit(e.child_id, h_child)

    dfs_visit(spec.source_id, float(spec.source_head_m))

    return TrunkTreeResult(
        node_head_m=node_head,
        edges=tuple(edge_results),
        total_q_m3s=total_q,
        issues=tuple(),
    )
