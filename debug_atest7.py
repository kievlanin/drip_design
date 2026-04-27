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
    optimize_trunk_diameters_by_weight,
)

print('Nodes:')
for n in nodes:
    print(f"  id={n.get('id')} kind={n.get('kind')} label={n.get('label')}")
print()
print('Segments (node_indices, length_m):')
for s in segs:
    ni = s.get('node_indices', [])
    lm = s.get('length_m', 0)
    # Convert indices to ids
    ids = [nodes[i].get('id') for i in ni] if ni else []
    print(f"  id={s.get('id')} ni={ni} ids={ids} L={lm:.1f}m")
print()

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
print(f"Feasible: {result.get('feasible')}")
print('Picks (raw):')
for p in result.get('picks', []):
    print(f"  {p}")
print()
print('out_segs sections:')
for s in result.get('out_segs', []):
    ni = s.get('node_indices', [])
    ids = [nodes[i].get('id') for i in ni] if ni else []
    secs = s.get('sections', [])
    print(f"  seg {s.get('id')} ni={ni} ids={ids}")
    for sc in secs:
        print(f"    {sc}")
