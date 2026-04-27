import sys, json, copy
sys.path.insert(0, '.')

d = json.load(open('designs/atest00/atest00.json'))
nodes = d['trunk_map_nodes']
segs = d['trunk_map_segments']

print('=== SEGMENTS & SECTIONS ===')
for i, s in enumerate(segs):
    ni = s.get('node_indices')
    secs = s.get('sections', [])
    print(f'  seg{i} ni={ni}:')
    for sc in secs:
        dnom = sc["d_nom_mm"]
        lm = sc["length_m"]
        print(f'    d={dnom}mm L={lm:.1f}m')

# Simulate trunk_tree_data (after sync_trunk_tree_data_from_trunk_map)
from modules.hydraulic_module.trunk_map_graph import build_oriented_edges
directed, err = build_oriented_edges(nodes, segs)
print()
print('=== DIRECTED EDGES (BFS from source) ===')
for u, v in directed:
    pu = nodes[u].get('id'); pv = nodes[v].get('id')
    print(f'  {pu}(i={u}) -> {pv}(i={v})')

print()
print('=== TRUNK_TREE in JSON ===')
tree = d.get('trunk_tree', {})
for e in tree.get('edges', []):
    print(f'  parent={e["parent_id"]} child={e["child_id"]}')

# Now simulate _trunk_sections_rows_align_to_ni0 for seg1
print()
print('=== SIMULATING _trunk_sections_rows_align_to_ni0 for seg1 (ni=[2,3]) ===')
seg1 = segs[1]
ni = seg1.get('node_indices')
ia, ib = int(ni[0]), int(ni[-1])
id_a = str(nodes[ia].get('id', '')).strip() or f'T{ia}'
id_b = str(nodes[ib].get('id', '')).strip() or f'T{ib}'
print(f'  ia={ia} id_a={id_a}, ib={ib} id_b={id_b}')

# Use the trunk_tree as trunk_tree_data (as it would be at load time)
td_edges = tree.get('edges', [])
print(f'  trunk_tree edges count: {len(td_edges)}')
for e in td_edges:
    pa = str(e.get('parent_id', '')).strip()
    ch = str(e.get('child_id', '')).strip()
    if {pa, ch} == {id_a, id_b}:
        print(f'  FOUND edge: parent={pa}, child={ch}')
        print(f'  ch==id_a? {ch} == {id_a} -> {ch == id_a}')
        if ch == id_a:
            print(f'  -> REVERSE sections')
        else:
            print(f'  -> NO reversal, sections as-is')
        break

print()
print('=== RUN OPTIMIZATION TO CHECK OUTPUT ===')
from modules.hydraulic_module.trunk_irrigation_schedule_hydro import optimize_trunk_diameters_by_weight

# Read pipes_db from designs/atest00/pipes_db.json
pipes_db = json.load(open('designs/atest00/pipes_db.json'))
slots = d.get('consumer_schedule', {}).get('irrigation_slots', [])
params = d.get('params', {})
allowed = d.get('allowed_pipes', {})

result, issues = optimize_trunk_diameters_by_weight(
    nodes,
    segs,
    slots,
    pipes_db=pipes_db,
    material='Layflat',
    allowed_pipes=allowed,
    max_head_loss_m=20.0,
    max_velocity_mps=3.0,
    default_q_m3h=35.0,
    min_segment_length_m=10.0,
    objective='weight',
    max_sections_per_edge=2,
)

print(f'Feasible: {result.get("feasible")}')
print(f'Message: {result.get("message")}')
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
