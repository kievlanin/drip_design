"""
Export isolines to ASCII DXF AutoCAD R12 (AC1009).

Only POLYLINE entities: simple 2D polylines (vertices 10, 20 only). Contour height
is stored as DXF elevation (group 38) — AutoCAD treats this as the Z of the whole
polyline (standard R12 pattern for planar contours at one level).
"""

from __future__ import annotations

import os
import re
from typing import Iterator, List, Tuple

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry


def _sanitize_layer(name: str) -> str:
    s = re.sub(r"[^\w\-]", "_", name.strip() or "contours")
    return s[:255] if len(s) > 255 else s


def _fmt(v: float) -> str:
    return f"{float(v):.6f}".rstrip("0").rstrip(".")


def _linestring_to_xy(line: LineString) -> List[Tuple[float, float]]:
    return [(float(c[0]), float(c[1])) for c in line.coords]


def iter_polylines_xy(geometry: BaseGeometry) -> Iterator[List[Tuple[float, float]]]:
    """Yield XY vertex lists for each contour segment (LineString part)."""
    gt = geometry.geom_type
    if gt == "LineString":
        pts = _linestring_to_xy(geometry)
        if len(pts) >= 2:
            yield pts
    elif gt == "MultiLineString":
        for ls in geometry.geoms:
            yield from iter_polylines_xy(ls)
    elif gt == "GeometryCollection":
        for g in geometry.geoms:
            yield from iter_polylines_xy(g)


def _write_polyline_2d_elevation(
    fp,
    layer: str,
    vertices_xy: List[Tuple[float, float]],
    elevation: float,
) -> None:
    """R12 open 2D POLYLINE: 70=0, group 38 = contour Z; VERTEX only 10,20."""
    lyr = _sanitize_layer(layer)
    z = float(elevation)
    fp.write("0\nPOLYLINE\n")
    fp.write(f"8\n{lyr}\n")
    fp.write("66\n1\n")
    fp.write("70\n0\n")
    fp.write(f"38\n{_fmt(z)}\n")
    fp.write("10\n0.0\n20\n0.0\n")
    for x, y in vertices_xy:
        fp.write("0\nVERTEX\n")
        fp.write(f"8\n{lyr}\n")
        fp.write(f"10\n{_fmt(x)}\n")
        fp.write(f"20\n{_fmt(y)}\n")
    fp.write("0\nSEQEND\n")
    fp.write(f"8\n{lyr}\n")


def _write_dxf_preamble(fp, layer: str) -> None:
    lyr = _sanitize_layer(layer)
    fp.write("0\nSECTION\n2\nHEADER\n")
    fp.write("9\n$ACADVER\n1\nAC1009\n")
    fp.write("0\nENDSEC\n")
    fp.write("0\nSECTION\n2\nTABLES\n")
    fp.write("0\nTABLE\n2\nLAYER\n70\n1\n")
    fp.write("0\nLAYER\n")
    fp.write(f"2\n{lyr}\n")
    fp.write("70\n0\n62\n7\n")  # color 7 = white/black
    fp.write("6\nCONTINUOUS\n")
    fp.write("0\nENDTAB\n")
    fp.write("0\nENDSEC\n")
    fp.write("0\nSECTION\n2\nENTITIES\n")


def _write_dxf_trailer(fp) -> None:
    fp.write("0\nENDSEC\n0\nEOF\n")


class ContourDXFSink:
    """
    Stream isolines to DXF R12 (AC1009): only simple 2D POLYLINEs; elevation in group 38.
    """

    def __init__(self, path: str, layer_name: str = "contours"):
        self.path = path
        self.layer_name = layer_name
        self._fp = None
        self._count = 0

    def __enter__(self) -> "ContourDXFSink":
        if os.path.isfile(self.path):
            os.unlink(self.path)
        self._fp = open(self.path, "w", encoding="ascii", errors="replace", newline="\n")
        _write_dxf_preamble(self._fp, self.layer_name)
        return self

    def write_feature(self, geometry: BaseGeometry, elevation: float) -> None:
        if self._fp is None:
            raise RuntimeError("ContourDXFSink not opened")
        elev = float(elevation)
        for vertices_xy in iter_polylines_xy(geometry):
            _write_polyline_2d_elevation(self._fp, self.layer_name, vertices_xy, elev)
            self._count += 1

    def close(self) -> None:
        if self._fp is not None:
            _write_dxf_trailer(self._fp)
            self._fp.close()
            self._fp = None

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def features_written(self) -> int:
        return self._count


def write_contours_dxf(
    features: List[Tuple[BaseGeometry, float]],
    path: str,
    layer_name: str = "contours",
) -> int:
    """Write a list of (geometry, elevation) to DXF; returns number of polylines."""
    with ContourDXFSink(path, layer_name) as sink:
        for geom, elev in features:
            sink.write_feature(geom, elev)
        n = sink.features_written
    return n
