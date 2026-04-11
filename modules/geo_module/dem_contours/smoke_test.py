"""
Smoke test: build a small synthetic GeoTIFF (planar ramp), run the tiled pipeline,
assert output features exist. Requires GDAL + numpy + shapely.

Run from repo root:
  py -m modules.geo_module.dem_contours.smoke_test
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np


def main() -> int:
    try:
        from osgeo import gdal
    except ImportError:
        print("SKIP: GDAL (osgeo) not installed", file=sys.stderr)
        return 0

    gdal.UseExceptions()

    from modules.geo_module.dem_contours.pipeline import PipelineConfig, run_pipeline

    # 128×128 DEM: z increases with x so contours are vertical-ish lines
    w = h = 128
    xs = np.linspace(0, 50, w, dtype=np.float64)
    arr = np.tile(xs, (h, 1))

    dem_path = tempfile.NamedTemporaryFile(suffix=".tif", delete=False).name
    out_gpkg = tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False).name
    try:
        from osgeo import osr

        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(dem_path, w, h, 1, gdal.GDT_Float64)
        ds.SetGeoTransform((500000.0, 1.0, 0.0, 6000000.0, 0.0, -1.0))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(32633)
        ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.WriteArray(arr)
        band.SetNoDataValue(-9999.0)
        ds.FlushCache()
        ds = None

        cfg = PipelineConfig(
            dem_path=dem_path,
            output_path=out_gpkg,
            output_format="gpkg",
            tile_size=64,
            overlap=2,
            interval=5.0,
            contour_base=0.0,
            simplify_tolerance=0.1,
            merge_boundaries=True,
            layer_name="contours",
        )
        stats = run_pipeline(cfg)
        assert stats["tiles"] >= 4, stats
        assert stats["features_written"] > 0, stats

        from osgeo import ogr

        drv_o = ogr.GetDriverByName("GPKG")
        ods = drv_o.Open(out_gpkg, 0)
        assert ods is not None
        layer = ods.GetLayerByName("contours")
        assert layer is not None
        n = layer.GetFeatureCount()
        assert n == stats["features_written"]
        ods = None
        print(f"OK: smoke_test passed ({n} features, {stats['tiles']} tiles)")
        return 0
    finally:
        for p in (dem_path, out_gpkg):
            if os.path.isfile(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
