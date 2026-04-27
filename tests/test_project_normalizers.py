from main_app.io.project_normalizers import normalize_consumer_schedule_payload


def test_normalize_consumer_schedule_defaults_for_missing_payload():
    data = normalize_consumer_schedule_payload(None)

    assert data["groups"] == []
    assert data["irrigation_slots"] == [[] for _ in range(48)]


def test_normalize_consumer_schedule_dedupes_groups_and_slots():
    data = normalize_consumer_schedule_payload(
        {
            "groups": [
                {"title": "  ", "nodes": [" C1 ", "C1", "", "C2"]},
                {"title": "Slot A", "node_ids": ["N1", "N1", "N2"]},
            ],
            "irrigation_slots": [[" C1 ", "C1", "", "C2"]],
        }
    )

    assert data["groups"] == [
        {"title": "Група", "node_ids": ["C1", "C2"]},
        {"title": "Slot A", "node_ids": ["N1", "N2"]},
    ]
    assert data["irrigation_slots"][0] == ["C1", "C2"]
    assert len(data["irrigation_slots"]) == 48


def test_normalize_consumer_schedule_clamps_numeric_and_aliases_goal():
    data = normalize_consumer_schedule_payload(
        {
            "max_pump_head_m": "500",
            "trunk_schedule_v_max_mps": "9",
            "trunk_schedule_test_q_m3h": "20000",
            "trunk_schedule_test_h_m": "450",
            "trunk_schedule_max_sections_per_edge": "9",
            "trunk_display_velocity_warn_mps": "12",
            "trunk_schedule_opt_goal": "cost_index",
            "trunk_pipes_selected": 1,
        }
    )

    assert data["max_pump_head_m"] == 400.0
    assert data["trunk_schedule_v_max_mps"] == 8.0
    assert data["trunk_schedule_test_q_m3h"] == 10000.0
    assert data["trunk_schedule_test_h_m"] == 400.0
    assert data["trunk_schedule_max_sections_per_edge"] == 4
    assert data["trunk_display_velocity_warn_mps"] == 8.0
    assert data["trunk_schedule_opt_goal"] == "money"
    assert data["trunk_pipes_selected"] is True


def test_normalize_consumer_schedule_cleans_label_positions_and_source_mode():
    data = normalize_consumer_schedule_payload(
        {
            "field_valve_label_pos": {
                " valve-1 ": ["1.5", 2],
                "": [9, 9],
                "bad": ["x", 1],
            },
            "srtm_source_mode": "open_elevation",
        }
    )

    assert data["field_valve_label_pos"] == {"valve-1": [1.5, 2.0]}
    assert data["srtm_source_mode"] == "open_elevation"


def test_normalize_consumer_schedule_rejects_unknown_source_mode_and_goal():
    data = normalize_consumer_schedule_payload(
        {
            "trunk_schedule_opt_goal": "unexpected",
            "srtm_source_mode": "unknown",
        }
    )

    assert data["trunk_schedule_opt_goal"] == "weight"
    assert data["srtm_source_mode"] == "auto"
