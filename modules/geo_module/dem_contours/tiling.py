"""Pixel-space tiling with optional overlap to reduce seam artifacts between tiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Tuple


@dataclass(frozen=True)
class PixelWindow:
    """Inclusive origin and size in pixel coordinates (column, row)."""

    col_off: int
    row_off: int
    width: int
    height: int


def iter_tiles(
    raster_width: int,
    raster_height: int,
    tile_size: int,
    overlap: int = 0,
) -> Iterator[PixelWindow]:
    """
    Yield windows covering the full raster. Adjacent tiles share `overlap` pixels
    on their common edge so contour lines can be merged or deduped downstream.

    Step is (tile_size - overlap). Overlap must satisfy 0 <= overlap < tile_size.
    """
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be in [0, tile_size)")

    step = tile_size - overlap
    row = 0
    while row < raster_height:
        col = 0
        while col < raster_width:
            w = min(tile_size, raster_width - col)
            h = min(tile_size, raster_height - row)
            if w > 0 and h > 0:
                yield PixelWindow(col, row, w, h)
            col += step
        row += step


def list_tiles(
    raster_width: int,
    raster_height: int,
    tile_size: int,
    overlap: int = 0,
) -> List[PixelWindow]:
    return list(iter_tiles(raster_width, raster_height, tile_size, overlap))
