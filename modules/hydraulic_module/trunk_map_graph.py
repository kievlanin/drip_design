"""
Топологія графа магістралі з даних карти (trunk_map_nodes + trunk_map_segments).

Семантика (узгоджено з trunk_tree_compute.TrunkTreeNode.kind):
- source      — насос / витік (єдиний корінь дерева, без вхідних ребер).
- valve       — кран на магістралі: споживання з можливим відведенням (лист, прохід як пікет, або розгалуження).
- consumption — споживач / сток (лист, без вихідних ребер).
- junction    — розгалуження / сумматор (один вхід; у зібраному графі ≥2 виходи, див. validate_trunk_map_graph(..., complete_only)).
- bend        — пікет / проміжна точка на осі труби (рівно один вхід і один вихід).

У файлі/пам’яті кожен запис trunk_map_segments — одне ребро (труба між двома вузлами);
полілінія зберігається в path_local цього ребра. Ребро після орієнтації від витоку
залишається ділянкою труби між двома вузлами.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    MutableMapping,
    MutableSequence,
    Optional,
    Sequence,
    Set,
    Tuple,
)

# Допустимі kind у JSON/карті (як у validate_trunk_tree)
KIND_SOURCE = "source"
KIND_VALVE = "valve"
KIND_CONSUMPTION = "consumption"
KIND_JUNCTION = "junction"
KIND_BEND = "bend"
VALID_KINDS = frozenset({KIND_SOURCE, KIND_VALVE, KIND_CONSUMPTION, KIND_JUNCTION, KIND_BEND})


def is_trunk_root_kind(kind: str) -> bool:
    """Єдиний корінь дерева — лише насос (source). Кран (valve) не є витоком."""
    k = (kind or "").strip().lower()
    return k == KIND_SOURCE


def ensure_trunk_node_ids(nodes: MutableSequence[MutableMapping[str, Any]]) -> None:
    """Дописує стабільні id T0, T1, … якщо відсутні."""
    for i, row in enumerate(nodes):
        if not isinstance(row, MutableMapping):
            continue
        tid = str(row.get("id", "")).strip()
        if not tid:
            row["id"] = f"T{i}"


def _node_kind(nodes: Sequence[Mapping[str, Any]], idx: int) -> str:
    if idx < 0 or idx >= len(nodes):
        return ""
    return str(nodes[idx].get("kind", "")).strip().lower()


def undirected_edges_from_segments(
    segments: Sequence[Mapping[str, Any]],
) -> Tuple[List[Tuple[int, int]], List[str]]:
    """
    З відрізків node_indices [i0,i1,…] будує список неорієнтованих ребер (послідовні пари).
    Повертає (edges, errors).
    """
    errors: List[str] = []
    edges: List[Tuple[int, int]] = []
    seen_und: Set[Tuple[int, int]] = set()
    for si, seg in enumerate(segments):
        if not isinstance(seg, Mapping):
            continue
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) < 2:
            errors.append(f"Відрізок #{si + 1}: потрібно щонайменше два вузли.")
            continue
        try:
            idxs = [int(x) for x in ni]
        except (TypeError, ValueError):
            errors.append(f"Відрізок #{si + 1}: некоректні індекси вузлів.")
            continue
        for a, b in zip(idxs[:-1], idxs[1:]):
            if a == b:
                errors.append(f"Відрізок #{si + 1}: повтор вузла підряд у послідовності.")
                continue
            key = (min(a, b), max(a, b))
            if key in seen_und:
                errors.append(
                    f"Дубль магістралі між вузлами {key[0]} і {key[1]}: одне ребро — одна труба між парою вузлів."
                )
                continue
            seen_und.add(key)
            edges.append((a, b))
    return edges, errors


def expand_trunk_segments_to_pair_edges(
    segments: Sequence[Mapping[str, Any]],
    nodes: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Канонічне сховище: один запис trunk_map_segments = одне ребро = одна труба між двома вузлами.

    Ланцюг [i0,i1,…,ik] з path_local узгодженим за довжиною з індексами розбивається на k ребер;
    для кожного ребра path_local — відповідний відрізок полілінії (або пряма між вузлами).
    """
    out: List[Dict[str, Any]] = []

    def _parse_path_local(raw: Any) -> List[Optional[Tuple[float, float]]]:
        pl: List[Optional[Tuple[float, float]]] = []
        if not isinstance(raw, list):
            return pl
        for p in raw:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    pl.append((float(p[0]), float(p[1])))
                except (TypeError, ValueError):
                    pl.append(None)
            else:
                pl.append(None)
        return pl

    def _node_xy(i: int) -> Optional[Tuple[float, float]]:
        if i < 0 or i >= len(nodes):
            return None
        try:
            return (float(nodes[i]["x"]), float(nodes[i]["y"]))
        except (KeyError, TypeError, ValueError):
            return None

    for seg in segments:
        if not isinstance(seg, Mapping):
            continue
        ni = seg.get("node_indices")
        if not isinstance(ni, list) or len(ni) < 2:
            continue
        try:
            idxs = [int(x) for x in ni]
        except (TypeError, ValueError):
            continue
        pl = _parse_path_local(seg.get("path_local"))

        if len(idxs) == 2:
            a, b = idxs[0], idxs[1]
            loc2: List[Tuple[float, float]] = []
            if len(pl) >= 2:
                for t in pl:
                    if t is None:
                        loc2 = []
                        break
                    loc2.append((float(t[0]), float(t[1])))
            if len(loc2) >= 2:
                out.append({"node_indices": [a, b], "path_local": loc2})
            else:
                pa, pb = _node_xy(a), _node_xy(b)
                if pa is not None and pb is not None:
                    out.append({"node_indices": [a, b], "path_local": [pa, pb]})
            continue

        aligned = len(pl) == len(idxs) and all(p is not None for p in pl)
        for k in range(len(idxs) - 1):
            a, b = idxs[k], idxs[k + 1]
            if aligned and pl[k] is not None and pl[k + 1] is not None:
                tk, tk1 = pl[k], pl[k + 1]
                edge_pl = [(float(tk[0]), float(tk[1])), (float(tk1[0]), float(tk1[1]))]
            else:
                pa, pb = _node_xy(a), _node_xy(b)
                if pa is None or pb is None:
                    continue
                edge_pl = [pa, pb]
            out.append({"node_indices": [a, b], "path_local": edge_pl})

    return out


def _build_adj(n: int, edges: Sequence[Tuple[int, int]]) -> List[List[int]]:
    adj: List[List[int]] = [[] for _ in range(n)]
    for a, b in edges:
        if 0 <= a < n and 0 <= b < n:
            adj[a].append(b)
            adj[b].append(a)
    return adj


def orient_tree_from_source(
    n: int,
    undirected_edges: Sequence[Tuple[int, int]],
    source_idx: int,
) -> Tuple[Optional[List[Tuple[int, int]]], List[str]]:
    """
    Будує орієнтовані ребра (батько → дитина) від витоку; дерево без циклів.
    """
    errors: List[str] = []
    if source_idx < 0 or source_idx >= n:
        return None, ["Некоректний індекс витоку."]
    verts: Set[int] = set()
    for a, b in undirected_edges:
        verts.add(a)
        verts.add(b)
    if verts and source_idx not in verts:
        return None, ["Витік не входить до жодного відрізка магістралі."]
    adj = _build_adj(n, undirected_edges)
    visited: Set[int] = {source_idx}
    parent: Dict[int, Optional[int]] = {source_idx: None}
    directed: List[Tuple[int, int]] = []
    q = deque([source_idx])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in visited:
                visited.add(v)
                parent[v] = u
                directed.append((u, v))
                q.append(v)
            elif v != parent[u]:
                errors.append("У графі магістралі виявлено цикл (неможливе дерево від витоку).")
                return None, errors
    if verts:
        missing = verts - visited
        if missing:
            errors.append(
                f"Є вузли, недосяжні з витоку (інша компонента зв’язності): {sorted(missing)}."
            )
            return None, errors
    return directed, []


def validate_trunk_map_graph(
    nodes: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
    *,
    complete_only: bool = True,
) -> List[str]:
    """
    Перевірка топології дерева магістралі та ролей вузлів.

    complete_only=False — під час поетапного малювання на карті: сумматор (junction)
    може мати поки що одну вихідну гілку; інші правила (цикл, сток, пікет) лишаються жорсткими.
    complete_only=True — фінальна перевірка: розгалуження має ≥2 виходи.
    """
    errors: List[str] = []
    n = len(nodes)
    if n == 0:
        if segments:
            errors.append("Є відрізки, але немає вузлів магістралі.")
        return errors

    for i, row in enumerate(nodes):
        if not isinstance(row, Mapping):
            errors.append(f"Вузол #{i}: некоректний запис.")
            continue
        k = str(row.get("kind", "")).strip().lower()
        if k not in VALID_KINDS:
            errors.append(f"Вузол #{i}: невідомий kind={row.get('kind')!r}.")

    src_indices = [i for i in range(n) if is_trunk_root_kind(_node_kind(nodes, i))]
    if len(src_indices) != 1:
        if len(src_indices) == 0:
            errors.append("Потрібен рівно один витік — насос (kind=source).")
        else:
            errors.append(f"Очікується один насос (source), знайдено вузлів source: {len(src_indices)}.")
        return errors

    source_idx = src_indices[0]
    und_edges, e_err = undirected_edges_from_segments(segments)
    errors.extend(e_err)
    if errors:
        return errors

    if not und_edges:
        return errors

    directed, o_err = orient_tree_from_source(n, und_edges, source_idx)
    errors.extend(o_err)
    if directed is None:
        return errors

    children: Dict[int, List[int]] = defaultdict(list)
    indeg = [0] * n
    for u, v in directed:
        children[u].append(v)
        indeg[v] += 1

    source_out = len(children[source_idx])

    verts_in_graph: Set[int] = set()
    for a, b in und_edges:
        verts_in_graph.add(a)
        verts_in_graph.add(b)

    for i in range(n):
        if i not in verts_in_graph:
            continue
        k = _node_kind(nodes, i)
        outdeg = len(children[i])
        di = indeg[i]
        if i == source_idx:
            if di != 0:
                errors.append(f"Витік (вузол {i}): не повинно бути вхідних ребер у дереві.")
            if verts_in_graph and source_out == 0:
                errors.append("Від насоса (витоку) має виходити хоча б одне ребро — труба магістралі.")
            continue
        if di != 1:
            errors.append(f"Вузол {i}: у дереві очікується рівно один вхід, має {di}.")

        if k == KIND_CONSUMPTION:
            if outdeg != 0:
                errors.append(
                    f"Споживач / сток (вузол {i}): не повинно бути вихідних ребер (лист дерева)."
                )
        elif k == KIND_JUNCTION:
            if outdeg < 1:
                errors.append(
                    f"Розгалуження / сумматор (вузол {i}): має бути хоча б одна вихідна магістраль."
                )
            elif complete_only and outdeg < 2:
                errors.append(
                    f"Розгалуження / сумматор (вузол {i}): у зібраному графі потрібні щонайменше два виходи (дві гілки труби)."
                )
        elif k == KIND_BEND:
            if outdeg != 1:
                errors.append(
                    f"Пікет (вузол {i}): на трасі має бути рівно одне вихідне ребро до наступного вузла."
                )
        elif k == KIND_SOURCE:
            errors.append(f"Вузол {i}: другий насос (витік) у графі недопустимий.")

    return errors


def trunk_map_edge_lengths_m(
    nodes: Sequence[Mapping[str, Any]],
    directed_edges: Sequence[Tuple[int, int]],
) -> List[Tuple[int, int, float]]:
    """Довжини ребер у метрах (локальні x, y)."""
    out: List[Tuple[int, int, float]] = []
    for u, v in directed_edges:
        try:
            x0 = float(nodes[u]["x"])
            y0 = float(nodes[u]["y"])
            x1 = float(nodes[v]["x"])
            y1 = float(nodes[v]["y"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        out.append((u, v, math.hypot(x1 - x0, y1 - y0)))
    return out


def build_oriented_edges(
    nodes: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
) -> Tuple[Optional[List[Tuple[int, int]]], List[str]]:
    """Орієнтовані ребра від єдиного source або None + помилки."""
    n = len(nodes)
    src_indices = [i for i in range(n) if is_trunk_root_kind(_node_kind(nodes, i))]
    if len(src_indices) != 1:
        return None, []
    und, e_err = undirected_edges_from_segments(segments)
    if e_err:
        return None, e_err
    if not und:
        return [], []
    return orient_tree_from_source(n, und, src_indices[0])
