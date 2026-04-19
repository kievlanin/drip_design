#!/usr/bin/env python3
"""
Headless baseline for RAM / allocation hotspots (plan: RAM і швидкість DripCAD).

Usage (from repo root):
  .venv\\Scripts\\python.exe scripts/dripcad_memory_baseline.py
  .venv\\Scripts\\python.exe scripts/dripcad_memory_baseline.py --cprofile

Prints tracemalloc top lines after importing heavy modules and running a tiny
TopoEngine contour stub (no Tk).
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import tracemalloc
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _import_heavy_modules():
    from modules.geo_module.topography_core import TopoEngine  # noqa: WPS433
    from modules.hydraulic_module import trunk_map_graph  # noqa: WPS433
    from modules.hydraulic_module.engine import HydraulicModule  # noqa: WPS433

    return TopoEngine, trunk_map_graph, HydraulicModule


def _micro_topo_work(TopoEngine):
    from shapely.geometry import box

    te = TopoEngine()
    te.elevation_points = [(i * 1.0, 0.0, float(i % 7)) for i in range(80)]
    b = box(0, -5, 79, 5)
    te.generate_contours(b, step_z=0.5, grid_size=6.0, elevation_points=te.elevation_points)


def main() -> int:
    p = argparse.ArgumentParser(description="DripCAD memory / import baseline")
    p.add_argument(
        "--cprofile",
        action="store_true",
        help="Also run cProfile on import + micro topo work",
    )
    args = p.parse_args()

    tracemalloc.start()
    TopoEngine, _tg, HydraulicModule = _import_heavy_modules()
    _ = HydraulicModule()
    _micro_topo_work(TopoEngine)
    snap = tracemalloc.take_snapshot()
    top_stats = snap.statistics("lineno")[:18]
    print("tracemalloc top (lineno) after import + micro contours:")
    for s in top_stats:
        print(s)
    current, peak = tracemalloc.get_traced_memory()
    print(f"tracemalloc current={current / 1024:.1f} KiB peak={peak / 1024:.1f} KiB")
    tracemalloc.stop()

    if args.cprofile:
        pr = cProfile.Profile()
        pr.enable()
        TopoEngine2, _, HydraulicModule2 = _import_heavy_modules()
        _ = HydraulicModule2()
        _micro_topo_work(TopoEngine2)
        pr.disable()
        buf = io.StringIO()
        ps = pstats.Stats(pr, stream=buf).sort_stats(pstats.SortKey.CUMULATIVE)
        ps.print_stats(28)
        print("\ncProfile (cumulative, top 28):")
        print(buf.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
