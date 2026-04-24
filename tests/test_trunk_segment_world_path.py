"""Тести геометрії магістралі: двовузлове ребро + path_local (план profil-magistral-lateral-q-audit)."""

import unittest
import tkinter as tk
from unittest.mock import MagicMock, patch

from main_app.ui.dripcad_legacy import DripCAD


class TestTrunkSegmentWorldPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._root = tk.Tk()
        cls._root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls._root.destroy()

    @patch("main_app.ui.dripcad_legacy.ControlPanel")
    def test_two_nodes_prefers_longer_path_local(self, _mock_cp):
        _mock_cp.return_value = MagicMock()
        app = DripCAD(self.__class__._root)
        app.trunk_map_nodes = [
            {"x": 0.0, "y": 0.0},
            {"x": 10.0, "y": 0.0},
        ]
        seg = {
            "node_indices": [0, 1],
            "path_local": [(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)],
        }
        pl = app._trunk_segment_world_path(seg)
        self.assertEqual(len(pl), 3)
        self.assertAlmostEqual(pl[1][0], 5.0)
        self.assertAlmostEqual(pl[1][1], 3.0)

    @patch("main_app.ui.dripcad_legacy.ControlPanel")
    def test_two_nodes_keeps_chord_when_path_local_is_short_straight(self, _mock_cp):
        _mock_cp.return_value = MagicMock()
        app = DripCAD(self.__class__._root)
        app.trunk_map_nodes = [
            {"x": 0.0, "y": 0.0},
            {"x": 10.0, "y": 0.0},
        ]
        seg = {
            "node_indices": [0, 1],
            "path_local": [(0.0, 0.0), (10.0, 0.0)],
        }
        pl = app._trunk_segment_world_path(seg)
        self.assertEqual(len(pl), 2)
        self.assertAlmostEqual(pl[0][0], 0.0)
        self.assertAlmostEqual(pl[-1][0], 10.0)

    @patch("main_app.ui.dripcad_legacy.ControlPanel")
    def test_two_nodes_reverses_path_local_to_match_nodes(self, _mock_cp):
        _mock_cp.return_value = MagicMock()
        app = DripCAD(self.__class__._root)
        app.trunk_map_nodes = [
            {"x": 0.0, "y": 0.0},
            {"x": 10.0, "y": 0.0},
        ]
        seg = {
            "node_indices": [0, 1],
            "path_local": [(10.0, 0.0), (5.0, 3.0), (0.0, 0.0)],
        }
        pl = app._trunk_segment_world_path(seg)
        self.assertEqual(len(pl), 3)
        self.assertAlmostEqual(pl[0][0], 0.0)
        self.assertAlmostEqual(pl[-1][0], 10.0)


if __name__ == "__main__":
    unittest.main()
