"""Lazy GDAL import with a clear error message."""


def require_gdal():
    try:
        from osgeo import gdal  # type: ignore

        gdal.UseExceptions()
        return gdal
    except ImportError as e:
        raise ImportError(
            "DEM contour pipeline requires GDAL Python bindings (osgeo.gdal). "
            "Install via conda-forge: conda install gdal; "
            "or OSGeo4W on Windows; PyPI wheels: pip install gdal (platform-dependent)."
        ) from e


def require_ogr_osr():
    try:
        from osgeo import ogr, osr  # type: ignore

        return ogr, osr
    except ImportError as e:
        raise ImportError(
            "GDAL vector bindings (osgeo.ogr, osgeo.osr) are required for contours and output."
        ) from e
