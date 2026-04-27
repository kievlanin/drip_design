from modules.hydraulic_module import api


def test_hydraulic_api_exposes_pipe_candidate_helpers():
    pipes_db = {
        "PVC": {
            "6": {
                "50": {"id": 44.0, "color": "#111111"},
                "63": {"id": 55.0, "color": "#222222"},
            }
        }
    }
    allowed = {"PVC": {"6": ["63", "50"]}}

    normalized = api.normalize_allowed_pipes_map(allowed)
    candidates = api.allowed_pipe_candidates_sorted(normalized, pipes_db)
    picked = api.pick_smallest_allowed_pipe_for_inner_req(candidates, 50.0)

    assert [candidate["d"] for candidate in candidates] == [50, 63]
    assert picked["d"] == 63
    assert api.pn_sort_tuple("10") < api.pn_sort_tuple("abc")
