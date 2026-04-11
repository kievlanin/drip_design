"""
GeoTIFF DEM → contours (tiled, low-RAM). Requires GDAL Python bindings (osgeo.gdal);
see requirements-dem.txt.

CLI::

    py -m modules.geo_module.dem_contours --help
    py -m modules.geo_module.dem_contours input.tif output.gpkg --tile-size 1024 --overlap 2
    py -m modules.geo_module.dem_contours input.tif contours.dxf --format dxf

Smoke test (with GDAL installed)::

    py -m modules.geo_module.dem_contours.smoke_test
"""

from .pipeline import PipelineConfig, run_pipeline

__all__ = ["run_pipeline", "PipelineConfig"]
