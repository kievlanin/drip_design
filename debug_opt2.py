"""Full optimization simulation for atest00."""
import json, sys, io, copy
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

d = json.load(open('designs/atest00/atest00.json', encoding='utf-8'))
db = json.load(open('designs/atest00/pipes_db.json', encoding='utf-8'))

from modules.hydraulic_module.trunk_irrigation_schedule_hydro import (
    optimize_trunk_diameters_by_weight,
    compute_trunk_irrigation_schedule_hydro,
)
def normalize_allowed_pipes_map(ap: dict) -> dict:
    out = {}
    if not isinstance(ap, dict):
        return out
    for mat, pns in ap.items():
        if not isinstance(pns, dict):
            continue
        mkey = str(mat).strip()
        if not mkey:
            continue
        sub = {}
        for pn, ods in pns.items():
            if not isinstance(ods, list):
                continue
            pk = str(pn).strip()
            olist = [str(o).strip() for o in ods if str(o).strip()]
            sub[pk] = olist
        if sub:
            out[mkey] = sub
    return out

nodes  = d['trunk_map_nodes']
segs   = d['trunk_map_segments']
cs     = d.get('consumer_schedule', {})
slots  = cs.get('irrigation_slots', [])
mph    = float(cs.get('max_pump_head_m', 40.0))
ap     = d.get('allowed_pipes', {})

# What params does UI actually use?
dq   = float(cs.get('trunk_schedule_test_q_m3h') or 60.0)
dh_input = float(cs.get('trunk_schedule_test_h_m') or 40.0)  # This is source head, not target

# For trunk consumer target: node-level trunk_schedule_h_m
consumer_h = [n.get('trunk_schedule_h_m') for n in nodes if n.get('kind') == 'consumption']
print('Consumer node target heads:', consumer_h)
print('trunk_schedule_test_h_m (source head in UI?):', dh_input)
print('max_pump_head_m:', mph)
print('dq:', dq)

# Compute max target head among active slots
active_h_vals = []
for slot in slots:
    if not slot:
        continue
    for nid in slot:
        for n in nodes:
            if n.get('id') == nid and n.get('kind') == 'consumption':
                h = n.get('trunk_schedule_h_m')
                if h is not None:
                    active_h_vals.append(float(h))
dh_worst = max(active_h_vals) if active_h_vals else float(dq)  # dq as fallback (wrong)
print('dh_worst (max consumer target head):', dh_worst)

budget = max(0.1, mph - dh_worst)
print('Budget for optimization (mph - dh_worst):', budget)

# eff_use - all non-empty materials
eff_all = normalize_allowed_pipes_map(ap)
print('Materials available:', list(eff_all.keys()))

# Now run optimization for each material
best = None
for mat, pns in eff_all.items():
    out, issues = optimize_trunk_diameters_by_weight(
        nodes, segs, slots,
        pipes_db=db,
        material=mat,
        allowed_pipes={mat: pns},
        max_head_loss_m=float(budget),
        max_velocity_mps=0.0,
        default_q_m3h=float(dq),
        min_segment_length_m=0.0,
        objective='weight',
        max_sections_per_edge=int(cs.get('trunk_schedule_max_sections_per_edge') or 2),
        pump_operating_head_m=float(mph),
        schedule_target_head_m=float(dh_worst),
    )
    if not out.get('feasible'):
        print(f'{mat}: INFEASIBLE - {out.get("message", "")}')
        continue
    tw = float(out.get('total_weight_kg', 0) or 0)
    tc = float(out.get('total_objective_cost', tw) or tw)
    print(f'{mat}: feasible, weight={tw:.1f}kg')
    if best is None or tc < best[0]:
        best = (tc, mat, out)

if best:
    print(f'\nBest material: {best[1]}, weight={best[0]:.1f}kg')
    for p in best[2].get('picks', []):
        eid = p.get('edge_id')
        hf = p.get('head_loss_m', 0)
        secs = p.get('sections', [])
        tsecs = p.get('telescoped_sections', secs)
        print(f'  {eid}: hf={hf:.2f}m, {len(tsecs)} section(s)')
        for s in tsecs:
            print(f'    {s.get("material")} PN{s.get("pn")} d={s.get("d_nom_mm")} L={s.get("length_m",0):.1f}m')
else:
    print('\nNo feasible solution found!')
