"""Write contours to GeoPackage (streaming) or GeoJSON (buffered)."""

from __future__ import annotations

import os
from typing import List, Tuple

from shapely.geometry.base import BaseGeometry

from .gdal_util import require_ogr_osr


def _shapely_to_ogr(geom: BaseGeometry, ogr):
    g = ogr.CreateGeometryFromWkt(geom.wkt)
    if g is None:
        raise ValueError("OGR could not parse geometry WKT from Shapely")
    return g


class ContourFeatureSink:
    """
    GeoPackage: features are written incrementally (suitable for large outputs).
    GeoJSON: all features are held in memory until ``close()`` (OGR GeoJSON is not
    append-friendly). Prefer GPKG for very large contour sets.
    """

    def __init__(
        self,
        path: str,
        format_name: str,
        layer_name: str,
        projection_wkt: str,
    ):
        self.path = path
        self.format_name = format_name.lower().strip()
        self.layer_name = layer_name
        self.projection_wkt = projection_wkt
        self._ogr, self._osr = require_ogr_osr()
        self._ds = None
        self._layer = None
        self._buffer: List[Tuple[BaseGeometry, float]] = []

    def __enter__(self) -> "ContourFeatureSink":
        if self.format_name in ("gpkg", "geopackage"):
            self._open_gpkg(new=True)
        elif self.format_name in ("geojson", "json"):
            pass
        else:
            raise ValueError(f"Unsupported format: {self.format_name} (use gpkg or geojson)")
        return self

    def _srs(self):
        srs = self._osr.SpatialReference()
        if self.projection_wkt:
            if srs.ImportFromWkt(self.projection_wkt) != 0:
                srs.SetLocalCS("unknown")
        else:
            srs.SetLocalCS("unknown")
        return srs

    def _open_gpkg(self, new: bool) -> None:
        drv = self._ogr.GetDriverByName("GPKG")
        if drv is None:
            raise RuntimeError("GPKG driver not available in OGR")
        if new and os.path.isfile(self.path):
            drv.DeleteDataSource(self.path)
        self._ds = drv.CreateDataSource(self.path)
        if self._ds is None:
            raise RuntimeError(f"Could not create GeoPackage: {self.path}")
        srs = self._srs()
        self._layer = self._ds.CreateLayer(
            self.layer_name, srs, geom_type=self._ogr.wkbLineString
        )
        elev_def = self._ogr.FieldDefn("elevation", self._ogr.OFTReal)
        self._layer.CreateField(elev_def)

    def write_feature(self, geometry: BaseGeometry, elevation: float) -> None:
        if self.format_name in ("gpkg", "geopackage"):
            assert self._layer is not None
            defn = self._layer.GetLayerDefn()
            feat = self._ogr.Feature(defn)
            feat.SetGeometry(_shapely_to_ogr(geometry, self._ogr))
            feat.SetField("elevation", float(elevation))
            if self._layer.CreateFeature(feat) != 0:
                raise RuntimeError("CreateFeature failed")
            feat.Destroy()
        else:
            self._buffer.append((geometry, float(elevation)))

    def close(self) -> None:
        if self.format_name in ("geojson", "json"):
            drv = self._ogr.GetDriverByName("GeoJSON")
            if drv is None:
                raise RuntimeError("GeoJSON driver not available in OGR")
            if os.path.isfile(self.path):
                drv.DeleteDataSource(self.path)
            self._ds = drv.CreateDataSource(self.path)
            if self._ds is None:
                raise RuntimeError(f"Could not create GeoJSON: {self.path}")
            srs = self._srs()
            self._layer = self._ds.CreateLayer(
                self.layer_name, srs, geom_type=self._ogr.wkbLineString
            )
            self._layer.CreateField(self._ogr.FieldDefn("elevation", self._ogr.OFTReal))
            for geom, elev in self._buffer:
                defn = self._layer.GetLayerDefn()
                feat = self._ogr.Feature(defn)
                feat.SetGeometry(_shapely_to_ogr(geom, self._ogr))
                feat.SetField("elevation", elev)
                self._layer.CreateFeature(feat)
                feat.Destroy()
            self._buffer.clear()
        self._layer = None
        self._ds = None

    def __exit__(self, *args) -> None:
        self.close()


def write_geojson_buffered(
    path: str,
    layer_name: str,
    projection_wkt: str,
    features: List[Tuple[BaseGeometry, float]],
) -> None:
    """Utility: write a list of (geometry, elevation) to GeoJSON in one shot."""
    sink = ContourFeatureSink(path, "geojson", layer_name, projection_wkt)
    sink._buffer = list(features)
    sink.close()
