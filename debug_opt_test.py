"""Debug script for trunk optimization."""
import json, sys
sys.path.insert(0, '.')
d = json.load(open('designs/atest00/atest00.json'))
db = json.load(open('designs/atest00/pipes_db.json'))
from modules.hydraulic_module.trunk_irrigation_schedule_hydro import optimize_trunk_diameters_by_weight

nodes = d['trunk_map_nodes']
segs  = d['trunk_map_segments']
cs    = d.get('consumer_schedule', {})
slots = cs.get('irrigation_slots', [])
mph   = float(cs.get('max_pump_head_m', 40.0))
dh    = 17.0   # target head at consumers (from node h values)
ap    = d.get('allowed_pipes', {})

for mat in ['Layflat', 'PVC', 'PE']:
    print(f'\n=== {mat} ===')
    out, issues = optimize_trunk_diameters_by_weight(
        nodes, segs, slots,
        pipes_db=db,
        material=mat,
        allowed_pipes={mat: ap[mat]},
        max_head_loss_m=float(mph - dh),
        default_q_m3h=60.0,
        pump_operating_head_m=float(mph),
        schedule_target_head_m=float(dh),
    )
    print('feasible:', out.get('feasible'))
    print('message:', out.get('message'))
    if issues:
        print('issues:', issues[:3])
    for p in out.get('picks', []):
        secs = p.get('sections', [])
        eid = p.get('edge_id', '?')
        hf = p.get('head_loss_m', 0)
        print(f'  {eid}: hf={hf:.2f}m')
        for s in secs:
            mat2 = s.get('material', '?')
            dnom = s.get('d_nom_mm', 0)
            pn   = s.get('pn', '?')
            lm   = s.get('length_m', 0)
            print(f'    {mat2} d={dnom}mm pn={pn} L={lm:.1f}m')
