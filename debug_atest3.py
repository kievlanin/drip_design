import json, sys
sys.path.insert(0, '.')

d = json.load(open('designs/atest00/atest00.json'))
nodes = d['trunk_map_nodes']
segs = d['trunk_map_segments']
cs = d.get('consumer_schedule', {})
pipes_db = json.load(open('designs/atest00/pipes_db.json'))
allowed = d.get('allowed_pipes', {})
slots = cs.get('irrigation_slots', [[] for _ in range(48)])

mph = float(cs.get('max_pump_head_m', 30.0))
dq = float(cs.get('trunk_schedule_test_q_m3h', 35.0))
dh = float(cs.get('trunk_schedule_test_h_m', 15.0))

# Compute dh_worst (max target h from slots)
# consumer T2 has h=15.0
consumer_h = {}
for n in nodes:
    if n.get('kind') == 'consumption':
        nid = n.get('id')
        h = float(n.get('trunk_schedule_h_m', dh))
        consumer_h[nid] = h

# Compute dh_worst from slots
dh_worst = dh
for slot in slots:
    if not slot:
        continue
    for cid in slot:
        h = consumer_h.get(str(cid).strip(), dh)
        if h > dh_worst:
            dh_worst = h

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
    max_velocity_mps=float(cs.get('trunk_schedule_v_max_mps') or 0.0) or 3.0,
    default_q_m3h=dq,
    min_segment_length_m=10.0,
    objective='weight',
    max_sections_per_edge=2,
    pump_operating_head_m=mph,
    schedule_target_head_m=dh_worst,
)

feasible = result.get('feasible')
msg = result.get('message', '')
print(f'Feasible: {feasible}')
try:
    print(f'Message: {msg.encode("ascii", errors="replace").decode("ascii")}')
except:
    print('Message: (encode error)')
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
