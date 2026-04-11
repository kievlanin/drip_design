"""Contour generation from an in-memory elevation tile using GDAL ContourGenerate."""

from __future__ import annotations

from typing import List, Tuple

from shapely.geometry.base import BaseGeometry

from .dem_loader import TileData
from .gdal_util import require_gdal, require_ogr_osr


def _ogr_geom_to_shapely(ogr_geom) -> BaseGeometry:
    wkt = ogr_geom.ExportToWkt()
    try:
        from shapely import from_wkt

        return from_wkt(wkt)
    except Exception:
        from shapely.wkt import loads

        return loads(wkt)


def generate_contours_for_tile(
    tile: TileData,
    interval: float,
    contour_base: float = 0.0,
) -> List[Tuple[BaseGeometry, float]]:
    """
    Build isolines for one tile. Returns (geometry, elevation) pairs in raster CRS
    (typically projected meters).
    """
    gdal = require_gdal()
    ogr, osr = require_ogr_osr()

    rows, cols = tile.array.shape
    mem_driver = gdal.GetDriverByName("MEM")
    if mem_driver is None:
        raise RuntimeError("GDAL MEM raster driver not available")
    mem_ds = mem_driver.Create("", cols, rows, 1, gdal.GDT_Float64)
    if mem_ds is None:
        raise RuntimeError("Failed to create in-memory raster")
    mem_ds.SetGeoTransform(tile.geotransform)
    if tile.projection_wkt:
        mem_ds.SetProjection(tile.projection_wkt)
    band = mem_ds.GetRasterBand(1)
    band.WriteArray(tile.array)
    if tile.nodata is not None:
        band.SetNoDataValue(float(tile.nodata))

    mem_vect = ogr.GetDriverByName("Memory")
    if mem_vect is None:
        raise RuntimeError("OGR Memory driver not available")
    vec_ds = mem_vect.CreateDataSource("contours_mem")
    srs = osr.SpatialReference()
    if tile.projection_wkt:
        err = srs.ImportFromWkt(tile.projection_wkt)
        if err != 0:
            srs = None
    if srs is None:
        srs = osr.SpatialReference()
        srs.SetLocalCS("unknown")
    layer = vec_ds.CreateLayer("contours", srs, geom_type=ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn("ID", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn("ELEV", ogr.OFTReal))
    defn = layer.GetLayerDefn()
    id_idx = defn.GetFieldIndex("ID")
    elev_idx = defn.GetFieldIndex("ELEV")

    use_nd = 1 if tile.nodata is not None else 0
    nd_val = float(tile.nodata) if tile.nodata is not None else 0.0

    err = 1
    tried_ex = False
    if hasattr(gdal, "ContourGenerateEx"):
        tried_ex = True
        opts = [
            f"INTERVAL={float(interval)}",
            f"OFFSET={float(contour_base)}",
            "ID_FIELD=ID",
            "ELEV_FIELD=ELEV",
        ]
        if tile.nodata is not None:
            opts.append(f"NODATA={float(tile.nodata)}")
        err = gdal.ContourGenerateEx(band, layer, options=opts)
    if err != 0:
        if tried_ex:
            vec_ds = None
            vec_ds = mem_vect.CreateDataSource("contours_mem_fb")
            layer = vec_ds.CreateLayer("contours", srs, geom_type=ogr.wkbLineString)
            layer.CreateField(ogr.FieldDefn("ID", ogr.OFTInteger))
            layer.CreateField(ogr.FieldDefn("ELEV", ogr.OFTReal))
            defn = layer.GetLayerDefn()
            id_idx = defn.GetFieldIndex("ID")
            elev_idx = defn.GetFieldIndex("ELEV")
        err = gdal.ContourGenerate(
            band,
            float(interval),
            float(contour_base),
            0,
            use_nd,
            nd_val,
            layer,
            id_idx,
            elev_idx,
        )
    if err != 0:
        raise RuntimeError(f"Contour generation failed with error code {err}")

    results: List[Tuple[BaseGeometry, float]] = []
    layer.ResetReading()
    feat = layer.GetNextFeature()
    while feat is not None:
        g = feat.GetGeometryRef()
        if g is not None:
            elev = feat.GetFieldAsDouble(elev_idx)
            clone = g.Clone()
            results.append((_ogr_geom_to_shapely(clone), elev))
        feat.Destroy()
        feat = layer.GetNextFeature()

    mem_ds = None
    vec_ds = None
    return results
