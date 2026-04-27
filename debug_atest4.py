import json, sys
sys.path.insert(0, '.')

d = json.load(open('designs/atest00/atest00.json'))
nodes = d['trunk_map_nodes']
segs = d['trunk_map_segments']
cs = d.get('consumer_schedule', {})
pipes_db = json.load(open('designs/atest00/pipes_db.json'))
allowed = d.get('allowed_pipes', {})
slots = cs.get('irrigation_slots', [[] for _ in range(48)])

mph = 32.0
dh_worst = 15.0  # T2 has trunk_schedule_h_m=15.0
budget = max(0.1, mph - dh_worst + 0.0003)
print(f'mph={mph}, dh_worst={dh_worst}, budget={budget:.4f}m')

from modules.hydraulic_module.trunk_irrigation_schedule_hydro import optimize_trunk_diameters_by_weight

result, issues = optimize_trunk_diameters_by_weight(
    nodes,
    segs,
    slots,
    pipes_db=pipes_db,
    material='Layflat',
    allowed_pipes=allowed,
    max_head_loss_m=budget,
    max_velocity_mps=3.0,
    default_q_m3h=60.0,
    min_segment_length_m=10.0,
    objective='weight',
    max_sections_per_edge=2,
    pump_operating_head_m=mph,
    schedule_target_head_m=dh_worst,
)

feasible = result.get('feasible')
print(f'Feasible: {feasible}')
print()
print('Picks:')
for p in result.get('picks', []):
    eid = p.get('edge_id')
    secs = p.get('sections', [])
    print(f'  {eid}:')
    for sc in secs:
        dnom = sc["d_nom_mm"]
        lm = sc["length_m"]
        print(f'    d={dnom}mm L={lm:.1f}m')
