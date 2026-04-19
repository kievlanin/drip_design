"""Тести гідравліки магістралі за слотами поливу."""

import unittest

from modules.hydraulic_module.trunk_irrigation_schedule_hydro import (
    compute_trunk_irrigation_schedule_hydro,
    optimize_trunk_diameters_by_weight,
)


class TestTrunkIrrigationScheduleHydro(unittest.TestCase):
    def _basic_nodes_and_segments(self):
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 100.0, "y": 0.0},
        ]
        segs = [{"node_indices": [0, 1], "path_local": [(0.0, 0.0), (100.0, 0.0)]}]
        return nodes, segs

    def test_single_consumer_uses_schedule_q(self):
        nodes, segs = self._basic_nodes_and_segments()
        nodes[1]["trunk_schedule_q_m3h"] = 60.0
        slots = [["C1"]]
        cache, issues = compute_trunk_irrigation_schedule_hydro(nodes, segs, slots, {})
        self.assertFalse(issues)
        ps0 = cache["per_slot"].get("0", {})
        self.assertAlmostEqual(float(ps0.get("total_q_m3s", 0.0)), 60.0 / 3600.0, places=8)

    def test_schedule_q_ignores_branch_count_fields(self):
        nodes, segs = self._basic_nodes_and_segments()
        nodes[1]["trunk_schedule_q_m3h"] = 25.0
        nodes[1]["trunk_branch_mode"] = "hydrants"
        nodes[1]["trunk_branch_count"] = 3
        nodes[1]["trunk_branch_q_each_m3h"] = 25.0
        slots = [["C1"]]
        cache, issues = compute_trunk_irrigation_schedule_hydro(nodes, segs, slots, {})
        self.assertFalse(issues)
        ps0 = cache["per_slot"].get("0", {})
        self.assertAlmostEqual(float(ps0.get("total_q_m3s", 0.0)), 25.0 / 3600.0, places=8)

    def test_intermediate_consumer_in_chain_is_supported(self):
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 100.0, "y": 0.0, "trunk_schedule_q_m3h": 20.0},
            {"id": "B1", "kind": "bend", "x": 200.0, "y": 0.0},
            {"id": "C2", "kind": "consumption", "x": 300.0, "y": 0.0, "trunk_schedule_q_m3h": 10.0},
        ]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0.0, 0.0), (100.0, 0.0)]},
            {"node_indices": [1, 2], "path_local": [(100.0, 0.0), (200.0, 0.0)]},
            {"node_indices": [2, 3], "path_local": [(200.0, 0.0), (300.0, 0.0)]},
        ]
        slots = [["C1", "C2"]]
        cache, issues = compute_trunk_irrigation_schedule_hydro(nodes, segs, slots, {})
        self.assertFalse(issues)
        ps0 = cache["per_slot"].get("0", {})
        self.assertAlmostEqual(float(ps0.get("total_q_m3s", 0.0)), 30.0 / 3600.0, places=8)
        edge_q = ps0.get("edge_q", {})
        self.assertAlmostEqual(float(edge_q.get("S->C1", 0.0)), 30.0 / 3600.0, places=8)
        self.assertAlmostEqual(float(edge_q.get("C1->B1", 0.0)), 10.0 / 3600.0, places=8)
        self.assertAlmostEqual(float(edge_q.get("B1->C2", 0.0)), 10.0 / 3600.0, places=8)
        sh = cache.get("segment_hover") or {}
        self.assertEqual(len(sh), 3)
        self.assertIn("0", sh)
        self.assertIn("1", sh)
        self.assertIn("2", sh)

    def test_fixed_pump_envelope_has_min_required_source_head(self):
        """У режимі заданого H насоса в envelope є оцінка мінімального напору на джерелі."""
        nodes, segs = self._basic_nodes_and_segments()
        slots = [["C1"]]
        cache, issues = compute_trunk_irrigation_schedule_hydro(
            nodes,
            segs,
            slots,
            {},
            pump_operating_head_m=50.0,
            use_required_pump_head=False,
            target_head_m=16.0,
            q_consumer_m3h=12.0,
        )
        self.assertFalse(issues)
        env = cache.get("envelope") or {}
        mrs = env.get("min_required_source_head_m")
        self.assertIsNotNone(mrs)
        self.assertGreater(float(mrs), 15.5)
        self.assertLessEqual(float(mrs), 50.0)
        ps0 = cache["per_slot"].get("0", {})
        self.assertAlmostEqual(
            float(ps0.get("min_required_source_head_m", 0.0)), float(mrs), places=6
        )

    def test_two_slots_different_edge_flows(self):
        """Різні слоти дають різний Q на ребрах (динаміка Q(t))."""
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 100.0, "y": 0.0, "trunk_schedule_q_m3h": 40.0},
            {"id": "C2", "kind": "consumption", "x": 200.0, "y": 0.0, "trunk_schedule_q_m3h": 15.0},
        ]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0.0, 0.0), (100.0, 0.0)]},
            {"node_indices": [1, 2], "path_local": [(100.0, 0.0), (200.0, 0.0)]},
        ]
        slots: list = [[] for _ in range(48)]
        slots[0] = ["C1"]
        slots[1] = ["C2"]
        cache, issues = compute_trunk_irrigation_schedule_hydro(nodes, segs, slots, {})
        self.assertFalse(issues)
        ps0 = cache["per_slot"].get("0", {})
        ps1 = cache["per_slot"].get("1", {})
        eq0 = ps0.get("edge_q", {})
        eq1 = ps1.get("edge_q", {})
        self.assertAlmostEqual(float(eq0.get("S->C1", 0.0)), 40.0 / 3600.0, places=6)
        self.assertAlmostEqual(float(eq0.get("C1->C2", 0.0)), 0.0, places=6)
        self.assertAlmostEqual(float(eq1.get("S->C1", 0.0)), 15.0 / 3600.0, places=6)
        self.assertAlmostEqual(float(eq1.get("C1->C2", 0.0)), 15.0 / 3600.0, places=6)

    def test_pressure_tightening_cheaper_upstream_with_surplus_head(self):
        """
        При надлишку H на споживачі крок refine має дозволити дешевшу/тоншу трубу на апстрім-ребрі,
        якщо у всіх слотах лишається H ≥ цілі та заданий H_насос не змінюється.
        """
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "A", "kind": "bend", "x": 120.0, "y": 0.0},
            {
                "id": "C1",
                "kind": "consumption",
                "x": 200.0,
                "y": 0.0,
                "trunk_schedule_q_m3h": 18.0,
            },
        ]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0.0, 0.0), (120.0, 0.0)]},
            {"node_indices": [1, 2], "path_local": [(120.0, 0.0), (200.0, 0.0)]},
        ]
        pipes_db = {
            "PE": {
                "6": {
                    "63": {"id": 58.2, "weight_kg_m": 1.1},
                    "75": {"id": 69.2, "weight_kg_m": 1.5},
                    "90": {"id": 83.0, "weight_kg_m": 2.2},
                    "110": {"id": 103.6, "weight_kg_m": 3.5},
                }
            }
        }
        slots = [["C1"]]
        base_kw = dict(
            trunk_nodes=nodes,
            trunk_segments=segs,
            irrigation_slots=slots,
            pipes_db=pipes_db,
            material="PE",
            max_head_loss_m=10.0,
            max_velocity_mps=0.0,
            default_q_m3h=18.0,
            min_segment_length_m=0.0,
            max_sections_per_edge=2,
            objective="weight",
        )
        out_no, iss_no = optimize_trunk_diameters_by_weight(**base_kw)
        out_yes, iss_yes = optimize_trunk_diameters_by_weight(
            **base_kw,
            pump_operating_head_m=48.0,
            schedule_target_head_m=12.0,
        )
        self.assertFalse(iss_no)
        self.assertTrue(out_no["feasible"])
        self.assertTrue(out_yes["feasible"])
        self.assertLess(
            float(out_yes["total_objective_cost"]),
            float(out_no["total_objective_cost"]) - 1e-6,
        )
        self.assertTrue(any("Підтягування" in str(m) for m in iss_yes))
        by_no = {p["edge_id"]: p for p in out_no["picks"]}
        by_yes = {p["edge_id"]: p for p in out_yes["picks"]}
        self.assertGreater(
            float(by_no["S->A"]["d_inner_mm"]),
            float(by_yes["S->A"]["d_inner_mm"]) + 1e-6,
        )
        edges_payload: list = []
        for p in out_yes["picks"]:
            if not isinstance(p, dict):
                continue
            eid = str(p.get("edge_id", "")).strip()
            if "->" not in eid:
                continue
            pa, pb = eid.split("->", 1)
            secs = p.get("sections")
            if not isinstance(secs, list):
                secs = []
            edges_payload.append(
                {
                    "parent_id": pa.strip(),
                    "child_id": pb.strip(),
                    "d_inner_mm": float(p.get("d_inner_mm", 90.0)),
                    "c_hw": 140.0,
                    "sections": secs,
                }
            )
        cache, giss = compute_trunk_irrigation_schedule_hydro(
            nodes,
            segs,
            slots,
            {"edges": edges_payload},
            pump_operating_head_m=48.0,
            target_head_m=12.0,
            q_consumer_m3h=18.0,
            max_pipe_velocity_mps=0.0,
            use_required_pump_head=False,
        )
        self.assertFalse(giss)
        row = (cache.get("per_slot") or {}).get("0") or {}
        self.assertFalse(row.get("issues"))
        nh = (row.get("node_head_m") or {}).get("C1")
        self.assertIsNotNone(nh)
        self.assertGreaterEqual(float(nh), 12.0 - 1e-3)


    def test_single_edge_uses_global_slack_for_telescope(self):
        """
        Одне довге ребро з «тісним» бюджетом: суцільний великий діаметр вкладається в ΔH,
        але телескоп light+heavy легший → оптимізатор має знайти його, використовуючи
        глобальний slack (max_head_loss_m − ΣвтратОбранихТруб), а не лише локальний HW
        обраної однієї труби.
        """
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {
                "id": "C1",
                "kind": "consumption",
                "x": 300.0,
                "y": 0.0,
                "trunk_schedule_q_m3h": 50.0,
                "trunk_schedule_h_m": 10.0,
            },
        ]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0.0, 0.0), (300.0, 0.0)]},
        ]
        pipes_db = {
            "PVC": {
                "6": {
                    "90": {"id": 84.6, "weight_kg_m": 1.04},
                    "110": {"id": 103.6, "weight_kg_m": 1.50},
                }
            }
        }
        slots = [["C1"]]
        out, iss = optimize_trunk_diameters_by_weight(
            trunk_nodes=nodes,
            trunk_segments=segs,
            irrigation_slots=slots,
            pipes_db=pipes_db,
            material="PVC",
            max_head_loss_m=10.0,
            max_velocity_mps=0.0,
            default_q_m3h=50.0,
            min_segment_length_m=0.0,
            max_sections_per_edge=2,
            objective="weight",
            pump_operating_head_m=20.0,
            schedule_target_head_m=10.0,
        )
        self.assertFalse(iss)
        self.assertTrue(out["feasible"])
        picks = out.get("picks") or []
        self.assertEqual(len(picks), 1)
        secs = picks[0].get("sections") or []
        # Очікується телескоп 2 секцій: тонша 90 + товща 110.
        self.assertEqual(len(secs), 2)
        d_inners = sorted(float(s.get("d_inner_mm", 0.0)) for s in secs)
        self.assertAlmostEqual(d_inners[0], 84.6, places=2)
        self.assertAlmostEqual(d_inners[1], 103.6, places=2)
        d_noms = sorted(float(s.get("d_nom_mm", 0.0)) for s in secs)
        self.assertAlmostEqual(d_noms[0], 90.0, places=2)
        self.assertAlmostEqual(d_noms[1], 110.0, places=2)
        total_len = sum(float(s.get("length_m", 0.0)) for s in secs)
        self.assertAlmostEqual(total_len, 300.0, places=3)
        # Сума втрат вкладається в бюджет 10 м.
        total_hf = sum(float(s.get("head_loss_m", 0.0)) for s in secs)
        self.assertLessEqual(total_hf, 10.0 + 1e-6)


if __name__ == "__main__":
    unittest.main()
