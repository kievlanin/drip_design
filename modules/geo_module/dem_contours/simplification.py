"""Douglas–Peucker style simplification via GEOS (Shapely)."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, List, Tuple

from shapely.geometry import MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, unary_union


def simplify_geometry(geom: BaseGeometry, tolerance: float | None) -> BaseGeometry:
    """
    Simplify a line or collection of lines. `tolerance` is in the same units as
    coordinates (e.g. meters for projected CRS). None or <= 0 skips simplification.
    """
    if tolerance is None or tolerance <= 0:
        return geom
    return geom.simplify(float(tolerance), preserve_topology=True)


def simplify_contour_features(
    features: Iterable[Tuple[BaseGeometry, float]],
    tolerance: float | None,
) -> List[Tuple[BaseGeometry, float]]:
    out: List[Tuple[BaseGeometry, float]] = []
    for geom, elev in features:
        sg = simplify_geometry(geom, tolerance)
        if not sg.is_empty:
            out.append((sg, elev))
    return out


def merge_contours_by_elevation(
    features: List[Tuple[BaseGeometry, float]],
    z_precision: int = 9,
) -> List[Tuple[BaseGeometry, float]]:
    """
    Group line geometries by rounded elevation and merge with unary_union + linemerge
    to reduce duplicate segments from overlapping tiles. Can use substantial RAM for
    large datasets; prefer streaming without merge for huge rasters.
    """
    by_z: dict[float, list] = defaultdict(list)
    for geom, elev in features:
        key = round(float(elev), z_precision)
        by_z[key].append(geom)

    out: List[Tuple[BaseGeometry, float]] = []
    for z_key in sorted(by_z.keys()):
        geoms = by_z[z_key]
        u = unary_union(geoms)
        if u.is_empty:
            continue
        if u.geom_type == "LineString":
            merged = u
        elif u.geom_type == "MultiLineString":
            merged = linemerge(u)
        else:
            lines = [
                g
                for g in getattr(u, "geoms", [])
                if getattr(g, "geom_type", None) == "LineString"
            ]
            if not lines:
                continue
            merged = linemerge(MultiLineString(lines))
        if merged.geom_type == "LineString":
            out.append((merged, z_key))
        elif merged.geom_type == "MultiLineString":
            for ln in merged.geoms:
                if not ln.is_empty:
                    out.append((ln, z_key))
        elif not merged.is_empty:
            out.append((merged, z_key))
    return out
