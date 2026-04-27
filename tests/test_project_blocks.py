from types import SimpleNamespace

from shapely.geometry import LineString

from main_app.io.project_blocks import (
    field_block_from_dict,
    field_blocks_to_save_payload,
    translate_field_block,
)


def test_field_block_from_dict_restores_runtime_geometry():
    block = field_block_from_dict(
        {
            "ring": [[0, 0, 99], [1, 0], [1, 1]],
            "edge_angle": "12.5",
            "submain": [[[0, 0], [1, 0]]],
            "auto": [[[0, 0], [0, 1]], [[1, 1]]],
            "manual": [[[2, 0], [2, 1]]],
            "params": {"lat": "0.9"},
            "submain_segment_plan": {"0": {"locked": True}},
        }
    )

    assert block["ring"] == [(0, 0), (1, 0), (1, 1)]
    assert block["edge_angle"] == 12.5
    assert block["submain_lines"] == [[[0, 0], [1, 0]]]
    assert [list(lat.coords) for lat in block["auto_laterals"]] == [[(0.0, 0.0), (0.0, 1.0)]]
    assert [list(lat.coords) for lat in block["manual_laterals"]] == [[(2.0, 0.0), (2.0, 1.0)]]
    assert block["params"] == {"lat": "0.9"}
    assert block["submain_segment_plan"] == {"0": {"locked": True}}


def test_field_blocks_to_save_payload_serializes_linestrings():
    app = SimpleNamespace(
        field_blocks=[
            {
                "ring": [(0, 0), (1, 0), (1, 1)],
                "edge_angle": 33.0,
                "submain_lines": [[[0, 0], [1, 0]]],
                "auto_laterals": [LineString([(0, 0), (0, 1)])],
                "manual_laterals": [LineString([(2, 0), (2, 1)])],
                "params": {"emit": "0.3"},
                "submain_segment_plan": {"0": {"locked": True}},
            }
        ]
    )

    payload, rings = field_blocks_to_save_payload(app)

    assert rings == [[(0, 0), (1, 0), (1, 1)]]
    assert payload == [
        {
            "ring": [(0, 0), (1, 0), (1, 1)],
            "edge_angle": 33.0,
            "submain": [[[0, 0], [1, 0]]],
            "auto": [[(0.0, 0.0), (0.0, 1.0)]],
            "manual": [[(2.0, 0.0), (2.0, 1.0)]],
            "params": {"emit": "0.3"},
            "submain_segment_plan": {"0": {"locked": True}},
        }
    ]


def test_translate_field_block_offsets_geometry():
    block = {
        "ring": [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
        "edge_angle": 0.1,
        "submain_lines": [[[0.0, 0.0], [1.0, 0.0]]],
        "auto_laterals": [LineString([(0.0, 0.0), (0.0, 1.0)])],
        "manual_laterals": [LineString([(2.0, 0.0), (2.0, 1.0)])],
        "params": {"a": 1},
        "submain_segment_plan": {"0": {"k": 2}},
    }
    t = translate_field_block(block, 3.0, 4.0)
    assert t["ring"][0] == (3.0, 4.0)
    assert t["submain_lines"] == [[[3.0, 4.0], [4.0, 4.0]]]
    assert [list(x.coords) for x in t["auto_laterals"]] == [[(3.0, 4.0), (3.0, 5.0)]]
    assert [list(x.coords) for x in t["manual_laterals"]] == [[(5.0, 4.0), (5.0, 5.0)]]
    assert t["params"] == {"a": 1}
    assert t["submain_segment_plan"] == {"0": {"k": 2}}
    assert t["edge_angle"] == 0.1
