from main_app.contracts import BomSnapshot, HydraulicRunSnapshot


def test_hydraulic_snapshot_preserves_extra_result_keys():
    snap = HydraulicRunSnapshot.from_mapping(
        {
            "report": "ok",
            "results": {
                "sections": [{"L": 12.5}],
                "valves": {"v1": {"q": 1}},
                "emitters": {"e1": {"h": 10}},
                "submain_profiles": {"0": [{"s": 0.0, "h": 11.0}]},
            },
        }
    )

    data = snap.to_dict()

    assert data["report"] == "ok"
    assert data["results"]["sections"] == [{"L": 12.5}]
    assert data["results"]["submain_profiles"] == {"0": [{"s": 0.0, "h": 11.0}]}


def test_bom_snapshot_preserves_extra_keys_and_normalizes_defaults():
    snap = BomSnapshot.from_mapping(
        {
            "items": [{"key": "p1"}],
            "custom_note": "keep me",
            "frozen_count": "3",
        }
    )

    data = snap.to_dict()

    assert data["items"] == [{"key": "p1"}]
    assert data["fitting_items"] == []
    assert data["frozen_count"] == 3
    assert data["custom_note"] == "keep me"
