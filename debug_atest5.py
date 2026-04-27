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
    optimize_trunk_diameters_by_weight,
)
from modules.hydraulic_module.pipe_weight_optimizer import _hf_m

layflat_allowed = {'Layflat': allowed.get('Layflat', {})}
eff = _normalize_allowed_pipes_map_simple(layflat_allowed)
cands = _allowed_pipe_candidates_sorted_trunk(eff, pipes_db)
print(f'Layflat candidates ({len(cands)}):')
Q = 35.0 / 3600
L = 780.3
budget = 17.0
for c in cands:
    mat = c.get('mat'); pn = c.get('pn'); dnom = c.get('d'); inner = c.get('inner'); chw = c.get('c_hw', 140)
    hf = _hf_m(Q, L, inner, chw)
    ok = 'OK' if hf <= budget else 'FAIL'
    print(f'  {mat} PN{pn} d={dnom} inner={inner}: hf={hf:.2f}m [{ok}]')

print()
print('Running optimize_trunk_diameters_by_weight with budget=17m...')
result, issues = optimize_trunk_diameters_by_weight(
    nodes, segs, slots,
    pipes_db=pipes_db,
    material='Layflat',
    allowed_pipes=allowed,
    max_head_loss_m=17.0,
    max_velocity_mps=3.0,
    default_q_m3h=60.0,
    min_segment_length_m=10.0,
    objective='weight',
    max_sections_per_edge=2,
    pump_operating_head_m=32.0,
    schedule_target_head_m=15.0,
)
feasible = result.get('feasible')
print(f'Feasible: {feasible}')
print('Picks:')
for p in result.get('picks', []):
    eid = p.get('edge_id')
    secs = p.get('sections', [])
    print(f'  {eid}:')
    for sc in secs:
        print(f'    d={sc["d_nom_mm"]}mm L={sc["length_m"]:.1f}m')
