from types import SimpleNamespace

from main_app.io.project_trunk import collect_trunk_save_payload


def test_collect_trunk_save_payload_copies_sections_to_legacy_key():
    app = SimpleNamespace(
        trunk_map_nodes=[{"id": "T1", "kind": "source"}, {"id": "T2", "kind": "consumption"}],
        trunk_map_segments=[
            {
                "node_indices": [0, 1],
                "sections": [{"d": 63, "L": 10.0}],
            },
            "bad",
        ],
        trunk_allowed_pipes={"PVC": {"6": ["63"]}},
        trunk_irrigation_hydro_cache={"seg_dominant_slot": {0: 1}},
    )

    trunk_payload, nodes, segments, hydro_cache = collect_trunk_save_payload(app)

    assert nodes == app.trunk_map_nodes
    assert segments == [
        {
            "node_indices": [0, 1],
            "sections": [{"d": 63, "L": 10.0}],
            "telescoped_sections": [{"d": 63, "L": 10.0}],
        }
    ]
    assert trunk_payload["nodes"] == nodes
    assert trunk_payload["segments"] == segments
    assert trunk_payload["allowed_pipes"] == {"PVC": {"6": ["63"]}}
    assert hydro_cache == {"seg_dominant_slot": {0: 1}}


def test_collect_trunk_save_payload_uses_telescoped_sections_fallback():
    app = SimpleNamespace(
        trunk_map_nodes=[],
        trunk_map_segments=[
            {
                "node_indices": [0, 1],
                "telescoped_sections": [{"d": 75, "L": 8.0}],
            }
        ],
        trunk_allowed_pipes=None,
        trunk_irrigation_hydro_cache=None,
    )

    trunk_payload, nodes, segments, hydro_cache = collect_trunk_save_payload(app)

    assert nodes == []
    assert segments[0]["sections"] == [{"d": 75, "L": 8.0}]
    assert trunk_payload["allowed_pipes"] == {}
    assert hydro_cache is None
