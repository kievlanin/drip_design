"""Unit-тести модуля trunk_tree_compute (магістраль-деверо, HW)."""

import math
import unittest

from modules.hydraulic_module.lateral_drip_core import hazen_williams_hloss_m
from modules.hydraulic_module.trunk_tree_compute import (
    TrunkTreeEdge,
    TrunkTreeNode,
    TrunkTreeSpec,
    compute_trunk_tree_steady,
    validate_trunk_tree,
)


class TestTrunkTreeCompute(unittest.TestCase):
    def test_single_pipe_matches_hw(self):
        """Витік → споживання: один відрізок, hf як у hazen_williams_hloss_m."""
        q = 0.012  # м³/с
        L = 120.0
        d_mm = 96.0
        d_m = d_mm / 1000.0
        c = 140.0
        spec = TrunkTreeSpec(
            nodes=(
                TrunkTreeNode("S", "source"),
                TrunkTreeNode("C", "consumption", q_demand_m3s=q),
            ),
            edges=(TrunkTreeEdge("S", "C", L, d_mm, c_hw=c),),
            source_id="S",
            source_head_m=25.0,
        )
        self.assertEqual(validate_trunk_tree(spec), [])
        res = compute_trunk_tree_steady(spec)
        self.assertEqual(res.issues, ())
        self.assertAlmostEqual(res.total_q_m3s, q, places=9)
        er = res.edges[0]
        hf_exp = hazen_williams_hloss_m(q, L, d_m, c)
        self.assertAlmostEqual(er.head_loss_m, hf_exp, places=6)
        self.assertAlmostEqual(er.h_downstream_m, 25.0 - hf_exp, places=6)
        self.assertAlmostEqual(res.node_head_m["C"], 25.0 - hf_exp, places=6)

    def test_two_branches_sum_q(self):
        """Розгалуження: на магістралі Q = Q1 + Q2."""
        spec = TrunkTreeSpec(
            nodes=(
                TrunkTreeNode("S", "source"),
                TrunkTreeNode("J", "junction"),
                TrunkTreeNode("A", "consumption", q_demand_m3s=0.005),
                TrunkTreeNode("B", "consumption", q_demand_m3s=0.008),
            ),
            edges=(
                TrunkTreeEdge("S", "J", 50.0, 110.0),
                TrunkTreeEdge("J", "A", 80.0, 90.0),
                TrunkTreeEdge("J", "B", 90.0, 90.0),
            ),
            source_id="S",
            source_head_m=30.0,
        )
        self.assertEqual(validate_trunk_tree(spec), [])
        res = compute_trunk_tree_steady(spec)
        self.assertEqual(res.issues, ())
        self.assertAlmostEqual(res.total_q_m3s, 0.013, places=9)
        by_pair = {(e.parent_id, e.child_id): e for e in res.edges}
        self.assertAlmostEqual(by_pair[("S", "J")].q_m3s, 0.013, places=9)
        self.assertAlmostEqual(by_pair[("J", "A")].q_m3s, 0.005, places=9)
        self.assertAlmostEqual(by_pair[("J", "B")].q_m3s, 0.008, places=9)

    def test_dz_raises_downstream_head(self):
        """dz_m > 0 збільшує напір у дитини (спуск рельєфу)."""
        q = 0.002
        spec = TrunkTreeSpec(
            nodes=(
                TrunkTreeNode("S", "source"),
                TrunkTreeNode("C", "consumption", q_demand_m3s=q),
            ),
            edges=(TrunkTreeEdge("S", "C", 10.0, 75.0, dz_m=2.0),),
            source_id="S",
            source_head_m=10.0,
        )
        res = compute_trunk_tree_steady(spec)
        er = res.edges[0]
        h_no_dz = 10.0 - er.head_loss_m
        self.assertAlmostEqual(er.h_downstream_m, h_no_dz + 2.0, places=6)

    def test_cycle_or_two_parents_rejected(self):
        spec = TrunkTreeSpec(
            nodes=(
                TrunkTreeNode("S", "source"),
                TrunkTreeNode("A", "bend"),
                TrunkTreeNode("B", "bend"),
            ),
            edges=(
                TrunkTreeEdge("S", "A", 1.0, 90.0),
                TrunkTreeEdge("A", "B", 1.0, 90.0),
                TrunkTreeEdge("B", "A", 1.0, 90.0),
            ),
            source_id="S",
            source_head_m=10.0,
        )
        issues = validate_trunk_tree(spec)
        self.assertTrue(len(issues) > 0)
        res = compute_trunk_tree_steady(spec)
        self.assertTrue(len(res.issues) > 0)

    def test_consumption_chain_node_passes_own_and_downstream_flow(self):
        spec = TrunkTreeSpec(
            nodes=(
                TrunkTreeNode("S", "source"),
                TrunkTreeNode("C1", "consumption", q_demand_m3s=0.004),
                TrunkTreeNode("C2", "consumption", q_demand_m3s=0.003),
            ),
            edges=(
                TrunkTreeEdge("S", "C1", 20.0, 90.0),
                TrunkTreeEdge("C1", "C2", 20.0, 90.0),
            ),
            source_id="S",
            source_head_m=30.0,
        )
        self.assertEqual(validate_trunk_tree(spec), [])
        res = compute_trunk_tree_steady(spec)
        self.assertEqual(res.issues, ())
        by_pair = {(e.parent_id, e.child_id): e for e in res.edges}
        self.assertAlmostEqual(by_pair[("S", "C1")].q_m3s, 0.007, places=9)
        self.assertAlmostEqual(by_pair[("C1", "C2")].q_m3s, 0.003, places=9)
        self.assertAlmostEqual(res.total_q_m3s, 0.007, places=9)

    def test_edge_sections_headloss_is_sum_of_section_losses(self):
        q = 0.006
        sec1 = (40.0, 63.0, 140.0)
        sec2 = (60.0, 75.0, 140.0)
        spec = TrunkTreeSpec(
            nodes=(
                TrunkTreeNode("S", "source"),
                TrunkTreeNode("C", "consumption", q_demand_m3s=q),
            ),
            edges=(
                TrunkTreeEdge(
                    "S",
                    "C",
                    100.0,
                    63.0,
                    c_hw=140.0,
                    sections=(sec1, sec2),
                ),
            ),
            source_id="S",
            source_head_m=30.0,
        )
        res = compute_trunk_tree_steady(spec)
        self.assertEqual(res.issues, ())
        er = res.edges[0]
        hf_exp = (
            hazen_williams_hloss_m(q, sec1[0], sec1[1] / 1000.0, sec1[2])
            + hazen_williams_hloss_m(q, sec2[0], sec2[1] / 1000.0, sec2[2])
        )
        self.assertAlmostEqual(er.head_loss_m, hf_exp, places=6)


if __name__ == "__main__":
    unittest.main()
