"""Read DEM rasters by pixel window (no full-array load)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .gdal_util import require_gdal


@dataclass
class TileData:
    """One window of elevation values with georeferencing for that window."""

    array: np.ndarray
    geotransform: Tuple[float, float, float, float, float, float]
    nodata: Optional[float]
    projection_wkt: str


def window_geotransform(
    gt: Tuple[float, float, float, float, float, float],
    col_off: int,
    row_off: int,
) -> Tuple[float, float, float, float, float, float]:
    """Adjust GDAL geotransform for a sub-window starting at (col_off, row_off)."""
    return (
        gt[0] + col_off * gt[1] + row_off * gt[2],
        gt[1],
        gt[2],
        gt[3] + col_off * gt[4] + row_off * gt[5],
        gt[4],
        gt[5],
    )


class DemDataset:
    """Thin wrapper around a single-band GDAL raster for windowed reads."""

    def __init__(self, path: str):
        gdal = require_gdal()
        self._ds = gdal.Open(str(path), gdal.GA_ReadOnly)
        if self._ds is None:
            raise FileNotFoundError(f"Could not open raster: {path}")
        self._band = self._ds.GetRasterBand(1)
        self.projection_wkt = self._ds.GetProjection() or ""
        self.geotransform = self._ds.GetGeoTransform()
        self.nodata: Optional[float] = self._band.GetNoDataValue()
        self.width = self._ds.RasterXSize
        self.height = self._ds.RasterYSize

    def load_tile(self, col_off: int, row_off: int, win_w: int, win_h: int) -> TileData:
        """
        Read a pixel window [col_off:col_off+win_w, row_off:row_off+win_h].
        Array shape is (win_h, win_w) in GDAL row-major order.
        """
        arr = self._band.ReadAsArray(col_off, row_off, win_w, win_h)
        if arr is None:
            raise RuntimeError(
                f"ReadAsArray failed for window ({col_off}, {row_off}, {win_w}, {win_h})"
            )
        arr = np.asarray(arr, dtype=np.float64)
        gt = window_geotransform(self.geotransform, col_off, row_off)
        return TileData(
            array=arr,
            geotransform=gt,
            nodata=self.nodata,
            projection_wkt=self.projection_wkt,
        )

    def close(self) -> None:
        self._ds = None
        self._band = None

    def __enter__(self) -> "DemDataset":
        return self

    def __exit__(self, *args) -> None:
        self.close()
