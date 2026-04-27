import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('designs/atest00/atest00.json', encoding='utf-8'))
nodes = d['trunk_map_nodes']

print('=== trunk_map_segments sections ===')
for s in d.get('trunk_map_segments', []):
    ni = s.get('node_indices', [])
    ids = [nodes[i].get('id') for i in ni] if ni else []
    lm = s.get('length_m', 0)
    print(f"  ni={ni} ids={ids} L={lm:.1f}m")
    for sc in s.get('sections', []) or []:
        mat = sc.get('material')
        pn = sc.get('pn')
        d_nom = sc.get('d_nom_mm')
        lsec = sc.get('length_m', 0)
        print(f"    {mat} PN{pn} d={d_nom} L={lsec:.1f}m")

print()
print('=== trunk_tree edges ===')
tt = d.get('trunk_tree', {})
for e in tt.get('edges', []) or []:
    pid = e.get('parent_id')
    cid = e.get('child_id')
    lm = e.get('length_m', 0)
    print(f"  {pid}->{cid} L={lm:.1f}m")
    for sc in e.get('sections', []) or []:
        mat = sc.get('material')
        pn = sc.get('pn')
        d_nom = sc.get('d_nom_mm')
        lsec = sc.get('length_m', 0)
        print(f"    {mat} PN{pn} d={d_nom} L={lsec:.1f}m")
