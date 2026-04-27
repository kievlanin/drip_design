import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

d = json.load(open('designs/atest00/atest00.json', encoding='utf-8'))
nodes = d['trunk_map_nodes']
segs = d['trunk_map_segments']
cs = d.get('consumer_schedule', {})
pipes_db = json.load(open('designs/atest00/pipes_db.json', encoding='utf-8'))
allowed = d.get('allowed_pipes', {})
slots = cs.get('irrigation_slots', [[] for _ in range(48)])

from modules.hydraulic_module.trunk_irrigation_schedule_hydro import (
    _normalize_allowed_pipes_map_simple,
    _allowed_pipe_candidates_sorted_trunk,
    _trunk_bend_only_chain_coalesce,
    _node_id,
    _segment_index_for_uv,
    _segment_length_m,
    build_pipe_options_from_db,
)
from modules.hydraulic_module.trunk_map_graph import build_oriented_edges
from modules.hydraulic_module.pipe_weight_optimizer import (
    SegmentDemand, OptimizationConstraints, _hf_m
)
from modules.hydraulic_module.pipe_weight_optimizer import optimize_fixed_topology_by_weight

directed, _ = build_oriented_edges(nodes, segs)
print('Directed:', [(nodes[u]['id'], nodes[v]['id']) for u,v in directed])

id_to_idx = {_node_id(nodes, i): i for i in range(len(nodes))}
child_map = {}
for uu, vv in directed:
    child_map.setdefault(_node_id(nodes, uu), []).append(_node_id(nodes, vv))

# Compute edge Q
edge_peak_q = {(_node_id(nodes, u), _node_id(nodes, v)): 0.0 for u, v in directed}
default_q_m3h = 60.0
for slot in slots:
    active = {str(x).strip() for x in (slot or []) if str(x).strip() in id_to_idx}
    for u, v in directed:
        pid, cid = _node_id(nodes, u), _node_id(nodes, v)
        q_m3h = 0.0
        stack = [cid]
        visited = set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            if nid in active:
                node = nodes[id_to_idx[nid]]
                raw = node.get('trunk_schedule_q_m3h') or node.get('q_m3h', default_q_m3h)
                try:
                    q = float(raw)
                except:
                    q = default_q_m3h
                q_m3h += q
            for ch in child_map.get(nid, []):
                stack.append(ch)
        edge_peak_q[(pid, cid)] = max(edge_peak_q[(pid, cid)], q_m3h / 3600.0)

print('Edge peak Q (m3/s):')
for k, v in edge_peak_q.items():
    print(f'  {k}: {v*3600:.2f} m3/h')

# Edge lengths
edge_len = {}
for u, v in directed:
    pid, cid = _node_id(nodes, u), _node_id(nodes, v)
    si = _segment_index_for_uv(segs, u, v)
    if si is not None:
        lm = _segment_length_m(nodes, segs[si])
        edge_len[(pid, cid)] = float(lm)

print('Edge lengths:')
for k, v in edge_len.items():
    print(f'  {k}: {v:.1f}m')

# Bend coalesce
bend_coalesce = _trunk_bend_only_chain_coalesce(nodes, directed, edge_len)
print('Bend coalesce:', bend_coalesce)

def _after_bend(k):
    return bend_coalesce.get(k, k)

agg_bend = {}
for k, lm in edge_len.items():
    r0 = _after_bend(k)
    agg_bend[r0] = agg_bend.get(r0, 0.0) + float(lm)
print('Aggregated bend lengths:', agg_bend)

agg_len = dict(agg_bend)  # No short-segment absorption for now

# Demands
roots_for_demands = set()
for u, v in directed:
    pid, cid = _node_id(nodes, u), _node_id(nodes, v)
    key = (pid, cid)
    t = _after_bend(key)
    roots_for_demands.add(t)

print('Roots for demands:', roots_for_demands)

demands = []
for pid, cid in sorted(roots_for_demands, key=str):
    tkey = (pid, cid)
    lm = float(agg_len.get(tkey, 0.0))
    q = float(edge_peak_q.get((pid, cid), 0.0))
    print(f'  Demand: {pid}->{cid} L={lm:.1f}m Q={q*3600:.2f}m3/h')
    if lm > 1e-9:
        demands.append(SegmentDemand(id=f'{pid}->{cid}', length_m=lm, q_m3s=q, min_length_m=0.0))

options = build_pipe_options_from_db(pipes_db, material='Layflat', allowed_pipes=allowed, c_hw=140.0)
print(f'\nPipe options: {len(options)}')
for o in options:
    print(f'  {o.material} PN{o.pn} d_nom={o.d_nom_mm} d_inner={o.d_inner_mm}')

res = optimize_fixed_topology_by_weight(
    demands, options,
    OptimizationConstraints(
        max_head_loss_m=17.0,
        max_velocity_m_s=3.0,
        min_segment_length_m=0.0,
        objective='weight',
    )
)
print(f'\nTop-level optimization: feasible={res.feasible}')
try:
    msg = res.message
    print(f'Message: {msg}')
except:
    pass
print('Choices:')
for c in res.choices:
    print(f'  {c.segment_id}: d_nom={c.d_nom_mm} hf={c.head_loss_m:.2f}m')
