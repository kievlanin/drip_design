import json
d = json.load(open('designs/atest00/atest00.json'))
cs = d.get('consumer_schedule', {})
nodes = d.get('trunk_map_nodes', [])
print('Consumer nodes:')
for n in nodes:
    if n.get('kind') == 'consumption':
        nid = n.get('id')
        q = n.get('trunk_schedule_q_m3h')
        h = n.get('trunk_schedule_h_m')
        print(f'  {nid}: q={q}, h={h}')

slots = cs.get('irrigation_slots', [])
print(f'Slots count: {len(slots)}')
non_empty = [s for s in slots if s]
print(f'Non-empty slots: {len(non_empty)}')
if non_empty:
    print(f'First non-empty: {non_empty[0]}')

# Check max_target_head from slots
print()
print('cs.max_pump_head_m:', cs.get('max_pump_head_m'))
print('cs.trunk_schedule_test_h_m:', cs.get('trunk_schedule_test_h_m'))
print('cs.trunk_schedule_test_q_m3h:', cs.get('trunk_schedule_test_q_m3h'))
