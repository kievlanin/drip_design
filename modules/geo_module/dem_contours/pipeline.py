"""Orchestrate tiled DEM → contours → simplify → GeoPackage, GeoJSON, or DXF R12."""

from __future__ import annotations

import argparse
import gc
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
from shapely.geometry.base import BaseGeometry

from .contour_generator import generate_contours_for_tile
from .dem_loader import DemDataset, TileData
from .simplification import merge_contours_by_elevation, simplify_contour_features
from .storage import ContourFeatureSink
from .tiling import iter_tiles


@dataclass
class PipelineConfig:
    """Configuration for run_pipeline (also used by the CLI)."""

    dem_path: str
    output_path: str
    output_format: str = "gpkg"
    tile_size: int = 1024
    overlap: int = 2
    interval: float = 1.0
    contour_base: float = 0.0
    simplify_tolerance: Optional[float] = 0.5
    z_min: Optional[float] = None
    z_max: Optional[float] = None
    smooth_sigma: float = 0.0
    merge_boundaries: bool = False
    layer_name: str = "contours"


@contextmanager
def _output_sink(cfg: PipelineConfig, projection_wkt: str) -> Iterator[Any]:
    """GeoPackage / GeoJSON via OGR, or DXF R12 (2D POLYLINE + elevation 38) without OGR for DXF."""
    if cfg.output_format == "dxf":
        from .dxf_export import ContourDXFSink

        with ContourDXFSink(cfg.output_path, cfg.layer_name) as sink:
            yield sink
    else:
        with ContourFeatureSink(
            cfg.output_path,
            cfg.output_format,
            cfg.layer_name,
            projection_wkt,
        ) as sink:
            yield sink


def _apply_smooth(array: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return array
    try:
        from scipy.ndimage import gaussian_filter

        return gaussian_filter(array, sigma=float(sigma))
    except ImportError:
        warnings.warn(
            "smooth_sigma > 0 but scipy is not installed; skipping Gaussian smooth",
            UserWarning,
            stacklevel=2,
        )
        return array


def _filter_by_z(
    features: List[Tuple[BaseGeometry, float]],
    z_min: Optional[float],
    z_max: Optional[float],
) -> List[Tuple[BaseGeometry, float]]:
    if z_min is None and z_max is None:
        return features
    out = []
    for g, z in features:
        if z_min is not None and z < z_min:
            continue
        if z_max is not None and z > z_max:
            continue
        out.append((g, z))
    return out


def run_pipeline(cfg: PipelineConfig) -> Dict[str, Any]:
    """
    Process the DEM in tiles: read window → optional smooth → GDAL contours →
    simplify → optional z filter → optional merge (overlap tiles) → write.

    **Seams:** With default ``overlap=2``, neighboring tiles share a 2-pixel strip.
    Use ``merge_boundaries=True`` to union/linemerge per elevation (uses RAM for all
    features). For very large areas, keep ``merge_boundaries=False`` and post-process
    in GIS, or accept duplicate segments near tile edges.

    **DXF:** ``output_format='dxf'`` writes AutoCAD R12 (AC1009) ASCII: only simple 2D
    ``POLYLINE`` entities; contour height is DXF elevation (group ``38``), vertices use
    ``10``/``20`` only.
    """
    collected: List[Tuple[BaseGeometry, float]] = []
    tile_count = 0
    feature_count = 0

    with DemDataset(cfg.dem_path) as dem:
        wkt = dem.projection_wkt

        if cfg.merge_boundaries:
            for win in iter_tiles(dem.width, dem.height, cfg.tile_size, cfg.overlap):
                tile_count += 1
                raw = dem.load_tile(win.col_off, win.row_off, win.width, win.height)
                arr = raw.array
                if cfg.smooth_sigma > 0:
                    arr = _apply_smooth(arr, cfg.smooth_sigma)
                tile = TileData(
                    array=arr,
                    geotransform=raw.geotransform,
                    nodata=raw.nodata,
                    projection_wkt=raw.projection_wkt,
                )
                feats = generate_contours_for_tile(
                    tile,
                    interval=cfg.interval,
                    contour_base=cfg.contour_base,
                )
                feats = _filter_by_z(feats, cfg.z_min, cfg.z_max)
                feats = simplify_contour_features(feats, cfg.simplify_tolerance)
                collected.extend(feats)
                feature_count += len(feats)
                del raw, tile, feats, arr
                gc.collect()

            merged = merge_contours_by_elevation(collected)
            with _output_sink(cfg, wkt) as sink:
                for g, z in merged:
                    sink.write_feature(g, z)
            return {
                "tiles": tile_count,
                "features_written": len(merged),
                "merge": True,
            }

        with _output_sink(cfg, wkt) as sink:
            for win in iter_tiles(dem.width, dem.height, cfg.tile_size, cfg.overlap):
                tile_count += 1
                raw = dem.load_tile(win.col_off, win.row_off, win.width, win.height)
                arr = raw.array
                if cfg.smooth_sigma > 0:
                    arr = _apply_smooth(arr, cfg.smooth_sigma)
                tile = TileData(
                    array=arr,
                    geotransform=raw.geotransform,
                    nodata=raw.nodata,
                    projection_wkt=raw.projection_wkt,
                )
                feats = generate_contours_for_tile(
                    tile,
                    interval=cfg.interval,
                    contour_base=cfg.contour_base,
                )
                feats = _filter_by_z(feats, cfg.z_min, cfg.z_max)
                feats = simplify_contour_features(feats, cfg.simplify_tolerance)
                for g, z in feats:
                    sink.write_feature(g, z)
                    feature_count += 1
                del raw, tile, feats, arr
                gc.collect()

    return {
        "tiles": tile_count,
        "features_written": feature_count,
        "merge": False,
    }


def _parse_args(argv: Optional[List[str]] = None) -> PipelineConfig:
    p = argparse.ArgumentParser(
        description="Generate 1 m (or custom interval) contours from a GeoTIFF DEM using tiled GDAL processing.",
    )
    p.add_argument("dem", help="Input DEM path (GeoTIFF or any single-band GDAL raster)")
    p.add_argument("output", help="Output path (.gpkg, .geojson, or .dxf)")
    p.add_argument(
        "--format",
        choices=("gpkg", "geojson", "dxf"),
        default=None,
        help="Output format (default: from output extension)",
    )
    p.add_argument("--tile-size", type=int, default=1024, help="Tile size in pixels (default 1024)")
    p.add_argument(
        "--overlap",
        type=int,
        default=2,
        help="Pixel overlap between adjacent tiles for seam handling (default 2)",
    )
    p.add_argument("--interval", type=float, default=1.0, help="Contour interval in z units (default 1)")
    p.add_argument(
        "--contour-base",
        type=float,
        default=0.0,
        help="Contour offset / base level (GDAL contour base)",
    )
    p.add_argument(
        "--simplify",
        type=float,
        default=0.5,
        help="Douglas–Peucker tolerance in ground units; 0 disables (default 0.5)",
    )
    p.add_argument("--z-min", type=float, default=None, help="Minimum elevation to keep")
    p.add_argument("--z-max", type=float, default=None, help="Maximum elevation to keep")
    p.add_argument(
        "--smooth-sigma",
        type=float,
        default=0.0,
        help="Gaussian smooth sigma in pixels (0=off; requires scipy)",
    )
    p.add_argument(
        "--merge",
        action="store_true",
        help="Merge contours per elevation after all tiles (high RAM; reduces overlap duplicates)",
    )
    p.add_argument("--layer", type=str, default="contours", help="Output layer name")
    ns = p.parse_args(argv)

    out = ns.output.lower()
    fmt = ns.format
    if fmt is None:
        if out.endswith(".geojson") or out.endswith(".json"):
            fmt = "geojson"
        elif out.endswith(".dxf"):
            fmt = "dxf"
        else:
            fmt = "gpkg"

    tol = ns.simplify
    if tol <= 0:
        tol = None

    return PipelineConfig(
        dem_path=ns.dem,
        output_path=ns.output,
        output_format=fmt,
        tile_size=ns.tile_size,
        overlap=ns.overlap,
        interval=ns.interval,
        contour_base=ns.contour_base,
        simplify_tolerance=tol,
        z_min=ns.z_min,
        z_max=ns.z_max,
        smooth_sigma=ns.smooth_sigma,
        merge_boundaries=ns.merge,
        layer_name=ns.layer,
    )


def main(argv: Optional[List[str]] = None) -> int:
    cfg = _parse_args(argv)
    try:
        stats = run_pipeline(cfg)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(
        f"Done: {stats['tiles']} tiles, {stats['features_written']} features written "
        f"(merge={'on' if stats['merge'] else 'off'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
