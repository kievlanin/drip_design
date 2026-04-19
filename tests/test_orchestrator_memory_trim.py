from main_app.orchestrator import IrrigationOrchestrator


def test_trim_auxiliary_results_after_persist_clears_fittings_and_stress_results():
    orch = IrrigationOrchestrator()
    orch.last_bom = {
        "items": [{"a": 1}],
        "fitting_items": [{"kind": "elbow", "n": 3}],
        "frozen_count": 2,
    }
    orch.last_stress = {
        "report": "stress ok",
        "results": {"sections": [{"L": 1.0}], "valves": {"k": 1}, "emitters": {"e": 2}},
    }
    orch.trim_auxiliary_results_after_persist()
    assert orch.last_bom["items"] == [{"a": 1}]
    assert orch.last_bom["fitting_items"] == []
    assert orch.last_bom["frozen_count"] == 2
    assert orch.last_stress["report"] == "stress ok"
    assert orch.last_stress["results"]["sections"] == []
