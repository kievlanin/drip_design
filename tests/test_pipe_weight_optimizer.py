"""Тести оптимізації труб за вагою."""

import unittest

from modules.hydraulic_module.pipe_weight_optimizer import (
    OptimizationConstraints,
    PipeOption,
    SegmentDemand,
    build_pipe_options_from_db,
    optimize_fixed_topology_by_weight,
    optimize_single_line_allocation_by_weight,
)
from modules.hydraulic_module.submain_telescope_opt import (
    TelescopeSegment,
    optimize_submain_telescope_by_weight,
)
from modules.hydraulic_module.trunk_irrigation_schedule_hydro import (
    optimize_trunk_diameters_by_weight,
)


class TestPipeWeightOptimizer(unittest.TestCase):
    def test_build_pipe_options_restricts_to_explicit_pn_only(self):
        """Якщо в allowed_pipes лише один PN — не підтягувати інші PN з каталогу."""
        pipes_db = {
            "PVC": {
                "4": {"50": {"id": 46.0}},
                "6": {"90": {"id": 84.6}, "110": {"id": 103.6}},
            }
        }
        opts = build_pipe_options_from_db(
            pipes_db,
            material="PVC",
            allowed_pipes={"PVC": {"6": ["90", "110"]}},
            c_hw=140.0,
        )
        self.assertEqual(len(opts), 2)
        self.assertTrue(all(o.pn == "6" for o in opts))
        self.assertEqual({o.d_nom_mm for o in opts}, {90.0, 110.0})

    def _options(self):
        return [
            PipeOption("PE", "6", 63.0, 58.2, c_hw=140.0, weight_kg_m=1.6),
            PipeOption("PE", "6", 75.0, 69.2, c_hw=140.0, weight_kg_m=2.2),
            PipeOption("PE", "6", 90.0, 83.0, c_hw=140.0, weight_kg_m=3.1),
        ]

    def test_fixed_topology_selects_feasible_lightest(self):
        segs = [
            SegmentDemand(id="A", length_m=80.0, q_m3s=0.010),
            SegmentDemand(id="B", length_m=60.0, q_m3s=0.006),
        ]
        res = optimize_fixed_topology_by_weight(
            segs,
            self._options(),
            OptimizationConstraints(max_head_loss_m=16.0, max_velocity_m_s=2.6),
        )
        self.assertTrue(res.feasible)
        self.assertLessEqual(res.total_head_loss_m, 16.0 + 1e-6)
        self.assertEqual(len(res.choices), 2)

    def test_fixed_topology_detects_infeasible(self):
        segs = [SegmentDemand(id="A", length_m=200.0, q_m3s=0.015)]
        res = optimize_fixed_topology_by_weight(
            segs,
            self._options(),
            OptimizationConstraints(max_head_loss_m=0.2, max_velocity_m_s=2.6),
        )
        self.assertFalse(res.feasible)
        self.assertTrue(bool(res.message.strip()))

    def test_velocity_limit_disabled_allows_smaller_diameter(self):
        """max_velocity_m_s <= 0 does not filter diameters by flow velocity."""
        segs = [SegmentDemand(id="A", length_m=50.0, q_m3s=0.020)]
        with_limit = optimize_fixed_topology_by_weight(
            segs,
            self._options(),
            OptimizationConstraints(max_head_loss_m=25.0, max_velocity_m_s=0.5),
        )
        no_limit = optimize_fixed_topology_by_weight(
            segs,
            self._options(),
            OptimizationConstraints(max_head_loss_m=25.0, max_velocity_m_s=0.0),
        )
        self.assertFalse(with_limit.feasible)
        self.assertTrue(no_limit.feasible)
        self.assertGreater(no_limit.choices[0].d_inner_mm, 0.0)

    def test_single_line_allocation_respects_min_segment(self):
        res = optimize_single_line_allocation_by_weight(
            total_length_m=100.0,
            q_m3s=0.012,
            options=self._options(),
            constraints=OptimizationConstraints(
                max_head_loss_m=8.0,
                max_velocity_m_s=2.6,
                min_segment_length_m=20.0,
                max_active_segments=2,
            ),
        )
        self.assertTrue(res.feasible)
        self.assertLessEqual(res.total_head_loss_m, 8.0 + 1e-6)
        self.assertGreaterEqual(sum(x.length_m for x in res.allocations), 99.999)
        for alloc in res.allocations:
            self.assertGreaterEqual(alloc.length_m, 20.0 - 1e-6)

    def test_single_line_money_objective_prefers_cheaper_per_meter(self):
        options = [
            PipeOption("PE", "6", 63.0, 58.2, c_hw=140.0, weight_kg_m=1.0, price_per_m=20.0),
            PipeOption("PE", "6", 75.0, 69.2, c_hw=140.0, weight_kg_m=1.5, price_per_m=8.0),
        ]
        res = optimize_single_line_allocation_by_weight(
            total_length_m=60.0,
            q_m3s=0.004,
            options=options,
            constraints=OptimizationConstraints(
                max_head_loss_m=20.0,
                max_velocity_m_s=2.6,
                min_segment_length_m=0.0,
                max_active_segments=1,
                objective="money",
            ),
        )
        self.assertTrue(res.feasible)
        self.assertGreater(len(res.allocations), 0)
        self.assertAlmostEqual(res.allocations[0].d_inner_mm, 69.2, places=3)

    def test_submain_weight_api(self):
        pipes_db = {
            "PE": {
                "6": {
                    "63": {"id": 58.2, "weight_kg_m": 1.7},
                    "75": {"id": 69.2, "weight_kg_m": 2.1},
                    "90": {"id": 83.0, "weight_kg_m": 3.0},
                }
            }
        }
        segs = [
            TelescopeSegment(length_m=80.0, q_m3s=0.010, dz_m=0.0),
            TelescopeSegment(length_m=40.0, q_m3s=0.006, dz_m=0.0),
        ]
        res = optimize_submain_telescope_by_weight(
            segs,
            h_inlet_m=36.0,
            h_end_min_m=25.0,
            pipes_db=pipes_db,
            material="PE",
            v_max_m_s=2.6,
        )
        self.assertTrue(res.feasible)
        self.assertGreater(len(res.picks), 0)
        self.assertIn("Сумарна вага", res.message)

    def test_trunk_weight_api(self):
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 100.0, "y": 0.0, "trunk_schedule_q_m3h": 40.0},
        ]
        segs = [{"node_indices": [0, 1], "path_local": [(0.0, 0.0), (100.0, 0.0)]}]
        pipes_db = {
            "PE": {
                "6": {
                    "63": {"id": 58.2, "weight_kg_m": 1.7},
                    "75": {"id": 69.2, "weight_kg_m": 2.2},
                    "90": {"id": 83.0, "weight_kg_m": 3.0},
                }
            }
        }
        out, issues = optimize_trunk_diameters_by_weight(
            nodes,
            segs,
            irrigation_slots=[["C1"]],
            pipes_db=pipes_db,
            material="PE",
            max_head_loss_m=6.0,
            max_velocity_mps=2.6,
        )
        self.assertFalse(issues)
        self.assertTrue(out["feasible"])
        self.assertGreater(len(out["picks"]), 0)

    def test_trunk_short_segment_absorbed_by_previous(self):
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "A", "kind": "bend", "x": 80.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 83.0, "y": 0.0, "trunk_schedule_q_m3h": 35.0},
        ]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0.0, 0.0), (80.0, 0.0)]},
            {"node_indices": [1, 2], "path_local": [(80.0, 0.0), (83.0, 0.0)]},
        ]
        pipes_db = {
            "PE": {
                "6": {
                    "63": {"id": 58.2, "weight_kg_m": 1.7},
                    "75": {"id": 69.2, "weight_kg_m": 2.2},
                    "90": {"id": 83.0, "weight_kg_m": 3.0},
                }
            }
        }
        out, issues = optimize_trunk_diameters_by_weight(
            nodes,
            segs,
            irrigation_slots=[["C1"]],
            pipes_db=pipes_db,
            material="PE",
            max_head_loss_m=6.0,
            max_velocity_mps=2.6,
            min_segment_length_m=6.0,
        )
        self.assertFalse(issues)
        self.assertTrue(out["feasible"])
        picks = {row["edge_id"]: row for row in out["picks"]}
        self.assertIn("S->A", picks)
        self.assertIn("A->C1", picks)
        self.assertEqual(picks["S->A"]["d_inner_mm"], picks["A->C1"]["d_inner_mm"])

    def test_trunk_money_objective_returns_sections_with_min_length(self):
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 120.0, "y": 0.0, "trunk_schedule_q_m3h": 25.0},
        ]
        segs = [{"node_indices": [0, 1], "path_local": [(0.0, 0.0), (120.0, 0.0)]}]
        pipes_db = {
            "PE": {
                "6": {
                    "63": {"id": 58.2, "weight_kg_m": 1.7, "price_per_m": 15.0},
                    "75": {"id": 69.2, "weight_kg_m": 2.2, "price_per_m": 12.0},
                    "90": {"id": 83.0, "weight_kg_m": 3.0, "price_per_m": 18.0},
                }
            }
        }
        out, issues = optimize_trunk_diameters_by_weight(
            nodes,
            segs,
            irrigation_slots=[["C1"]],
            pipes_db=pipes_db,
            material="PE",
            max_head_loss_m=25.0,
            max_velocity_mps=0.0,
            min_segment_length_m=10.0,
            objective="money",
            max_sections_per_edge=2,
        )
        self.assertFalse(issues)
        self.assertTrue(out["feasible"])
        self.assertEqual(out.get("objective"), "money")
        picks = out.get("picks") or []
        self.assertTrue(picks)
        sec = picks[0].get("sections") or []
        if sec:
            for row in sec:
                self.assertGreaterEqual(float(row.get("length_m", 0.0)), 10.0 - 1e-6)

    def test_trunk_money_without_prices_returns_issues(self):
        nodes = [
            {"id": "S", "kind": "source", "x": 0.0, "y": 0.0},
            {"id": "C1", "kind": "consumption", "x": 100.0, "y": 0.0, "trunk_schedule_q_m3h": 25.0},
        ]
        segs = [{"node_indices": [0, 1], "path_local": [(0.0, 0.0), (100.0, 0.0)]}]
        pipes_db = {
            "PE": {
                "6": {
                    "63": {"id": 58.2, "weight_kg_m": 1.7},
                    "75": {"id": 69.2, "weight_kg_m": 2.2},
                }
            }
        }
        out, issues = optimize_trunk_diameters_by_weight(
            nodes,
            segs,
            irrigation_slots=[["C1"]],
            pipes_db=pipes_db,
            material="PE",
            max_head_loss_m=10.0,
            max_velocity_mps=0.0,
            objective="money",
        )
        self.assertTrue(issues)
        self.assertFalse(out["feasible"])

    def test_telescope_two_segments_heavier_upstream_first(self):
        """Полілінія parent→child: перша секція по дузі — більший d_inner (апстрім)."""
        options = [
            PipeOption("PE", "6", 75.0, 69.2, c_hw=140.0, weight_kg_m=2.2),
            PipeOption("PE", "6", 90.0, 83.0, c_hw=140.0, weight_kg_m=3.1),
            PipeOption("PE", "6", 110.0, 102.0, c_hw=140.0, weight_kg_m=4.0),
        ]
        res = optimize_single_line_allocation_by_weight(
            total_length_m=100.0,
            q_m3s=0.020,
            options=options,
            constraints=OptimizationConstraints(
                max_head_loss_m=8.0,
                max_velocity_m_s=0.0,
                min_segment_length_m=15.0,
                max_active_segments=2,
            ),
        )
        self.assertTrue(res.feasible)
        self.assertEqual(len(res.allocations), 2)
        self.assertGreater(
            res.allocations[0].d_inner_mm,
            res.allocations[1].d_inner_mm,
        )

    def test_single_line_respects_max_three_segments_and_min_length(self):
        """Режим max_active_segments=3 дозволяє до трьох секцій; мін. довжина дотримується."""
        options = [
            PipeOption("PE", "6", 50.0, 46.0, c_hw=140.0, weight_kg_m=0.9, price_per_m=1.0),
            PipeOption("PE", "6", 63.0, 58.2, c_hw=140.0, weight_kg_m=1.2, price_per_m=1.2),
            PipeOption("PE", "6", 75.0, 69.2, c_hw=140.0, weight_kg_m=1.5, price_per_m=1.5),
            PipeOption("PE", "6", 90.0, 83.0, c_hw=140.0, weight_kg_m=2.0, price_per_m=2.0),
        ]
        res = optimize_single_line_allocation_by_weight(
            total_length_m=120.0,
            q_m3s=0.012,
            options=options,
            constraints=OptimizationConstraints(
                max_head_loss_m=18.0,
                max_velocity_m_s=0.0,
                min_segment_length_m=8.0,
                max_active_segments=3,
                objective="weight",
            ),
        )
        self.assertTrue(res.feasible)
        self.assertGreaterEqual(len(res.allocations), 1)
        self.assertLessEqual(len(res.allocations), 3)
        for a in res.allocations:
            self.assertGreaterEqual(a.length_m, 8.0 - 1e-6)


if __name__ == "__main__":
    unittest.main()
