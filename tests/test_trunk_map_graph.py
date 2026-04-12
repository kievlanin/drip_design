"""Тести топології графа магістралі з карти (trunk_map_nodes + trunk_map_segments)."""

import unittest

from modules.hydraulic_module import trunk_map_graph as tg


def _n(kind: str) -> dict:
    return {"kind": kind, "x": 0.0, "y": 0.0}


class TestTrunkMapGraph(unittest.TestCase):
    def test_ensure_trunk_node_ids(self):
        nodes = [{"kind": "source"}, {"kind": "bend", "id": "T9"}]
        tg.ensure_trunk_node_ids(nodes)
        self.assertEqual(nodes[0]["id"], "T0")
        self.assertEqual(nodes[1]["id"], "T9")

    def test_line_pump_to_consumer_ok(self):
        nodes = [_n("source"), _n("consumption")]
        segs = [{"node_indices": [0, 1], "path_local": [(0, 0), (1, 0)]}]
        self.assertEqual(tg.validate_trunk_map_graph(nodes, segs), [])
        self.assertEqual(tg.validate_trunk_map_graph(nodes, segs, complete_only=False), [])

    def test_pump_bend_consumer_ok(self):
        nodes = [_n("source"), _n("bend"), _n("consumption")]
        segs = [{"node_indices": [0, 1, 2], "path_local": [(0, 0), (1, 0), (2, 0)]}]
        self.assertEqual(tg.validate_trunk_map_graph(nodes, segs), [])

    def test_junction_one_branch_relaxed_ok_strict_fails(self):
        nodes = [_n("source"), _n("junction"), _n("consumption")]
        segs = [{"node_indices": [0, 1, 2], "path_local": [(0, 0), (1, 0), (2, 0)]}]
        self.assertEqual(tg.validate_trunk_map_graph(nodes, segs, complete_only=False), [])
        err = tg.validate_trunk_map_graph(nodes, segs, complete_only=True)
        self.assertTrue(any("два виходи" in e for e in err))

    def test_junction_two_branches_ok(self):
        nodes = [_n("source"), _n("junction"), _n("consumption"), _n("consumption")]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0, 0), (10, 0)]},
            {"node_indices": [1, 2], "path_local": [(10, 0), (10, 10)]},
            {"node_indices": [1, 3], "path_local": [(10, 0), (10, -10)]},
        ]
        self.assertEqual(tg.validate_trunk_map_graph(nodes, segs), [])

    def test_duplicate_undirected_edge_fails(self):
        nodes = [_n("source"), _n("consumption")]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0, 0), (5, 0)]},
            {"node_indices": [0, 1], "path_local": [(0, 0), (5, 0)]},
        ]
        err = tg.validate_trunk_map_graph(nodes, segs)
        self.assertTrue(any("Дубль магістралі" in e for e in err))

    def test_cycle_fails(self):
        nodes = [_n("source"), _n("bend"), _n("bend"), _n("bend")]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0, 0), (1, 0)]},
            {"node_indices": [1, 2], "path_local": [(1, 0), (2, 0)]},
            {"node_indices": [2, 3], "path_local": [(2, 0), (3, 0)]},
            {"node_indices": [3, 1], "path_local": [(3, 0), (1, 0)]},
        ]
        err = tg.validate_trunk_map_graph(nodes, segs)
        self.assertTrue(any("цикл" in e for e in err))

    def test_two_sources_fails(self):
        nodes = [_n("source"), _n("source"), _n("consumption")]
        segs = [{"node_indices": [0, 2], "path_local": [(0, 0), (1, 0)]}]
        err = tg.validate_trunk_map_graph(nodes, segs)
        self.assertTrue(err)
        self.assertTrue(any("корінь" in e or "насос" in e for e in err))

    def test_consumer_with_child_fails(self):
        nodes = [_n("source"), _n("consumption"), _n("bend")]
        segs = [
            {"node_indices": [0, 1], "path_local": [(0, 0), (1, 0)]},
            {"node_indices": [1, 2], "path_local": [(1, 0), (2, 0)]},
        ]
        err = tg.validate_trunk_map_graph(nodes, segs)
        self.assertTrue(any("Споживач" in e or "сток" in e for e in err))

    def test_build_oriented_edges(self):
        nodes = [_n("source"), _n("consumption")]
        segs = [{"node_indices": [0, 1], "path_local": [(0, 0), (5, 0)]}]
        directed, errs = tg.build_oriented_edges(nodes, segs)
        self.assertEqual(errs, [])
        self.assertEqual(directed, [(0, 1)])

    def test_expand_chain_to_pair_edges(self):
        nodes = [
            {"kind": "source", "x": 0.0, "y": 0.0},
            {"kind": "bend", "x": 1.0, "y": 0.0},
            {"kind": "consumption", "x": 2.0, "y": 0.0},
        ]
        segs = [{"node_indices": [0, 1, 2], "path_local": [(0, 0), (1, 0), (2, 0)]}]
        exp = tg.expand_trunk_segments_to_pair_edges(segs, nodes)
        self.assertEqual(len(exp), 2)
        self.assertEqual(exp[0]["node_indices"], [0, 1])
        self.assertEqual(exp[0]["path_local"], [(0.0, 0.0), (1.0, 0.0)])
        self.assertEqual(exp[1]["node_indices"], [1, 2])
        self.assertEqual(exp[1]["path_local"], [(1.0, 0.0), (2.0, 0.0)])
        self.assertEqual(tg.validate_trunk_map_graph(nodes, exp), [])

    def test_expand_idempotent_two_node_segment(self):
        nodes = [_n("source"), _n("consumption")]
        segs = [{"node_indices": [0, 1], "path_local": [(0, 0), (5, 0)]}]
        exp = tg.expand_trunk_segments_to_pair_edges(segs, nodes)
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["node_indices"], [0, 1])
        self.assertEqual(exp[0]["path_local"], [(0.0, 0.0), (5.0, 0.0)])

    def test_expand_keeps_interior_polyline_on_edge(self):
        nodes = [_n("source"), _n("consumption")]
        segs = [
            {
                "node_indices": [0, 1],
                "path_local": [(0.0, 0.0), (2.0, 1.0), (5.0, 0.0)],
            }
        ]
        exp = tg.expand_trunk_segments_to_pair_edges(segs, nodes)
        self.assertEqual(len(exp), 1)
        self.assertEqual(len(exp[0]["path_local"]), 3)

    def test_normalize_legacy_valve_kinds(self):
        nodes = [_n("source"), {"kind": "valve", "x": 0.0, "y": 0.0}, _n("consumption")]
        segs = [{"node_indices": [0, 1, 2], "path_local": [(0, 0), (1, 0), (2, 0)]}]
        tg.normalize_legacy_trunk_valve_kinds(nodes, segs)
        self.assertEqual(nodes[1]["kind"], "bend")
        nodes2 = [
            _n("source"),
            {"kind": "valve", "x": 0.0, "y": 0.0},
            _n("consumption"),
            _n("consumption"),
        ]
        segs2 = [
            {"node_indices": [0, 1], "path_local": [(0, 0), (10, 0)]},
            {"node_indices": [1, 2], "path_local": [(10, 0), (10, 10)]},
            {"node_indices": [1, 3], "path_local": [(10, 0), (10, -10)]},
        ]
        tg.normalize_legacy_trunk_valve_kinds(nodes2, segs2)
        self.assertEqual(nodes2[1]["kind"], "junction")


if __name__ == "__main__":
    unittest.main()
