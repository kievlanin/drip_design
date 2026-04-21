from modules.geo_module.topography_core import TopoEngine


class GeoModule:
    """Autonomous geo module with DTO-style API."""

    def __init__(self):
        self.engine = TopoEngine()

    def set_elevation_points(self, points):
        self.engine.elevation_points = list(points or [])

    def get_elevation_points(self):
        return list(self.engine.elevation_points)

    def set_srtm_boundary(self, boundary_points):
        self.engine.srtm_boundary_pts_local = list(boundary_points or [])

    def get_srtm_boundary(self):
        return list(self.engine.srtm_boundary_pts_local)

    def add_point(self, x, y, z):
        self.engine.add_point(x, y, z)

    def clear(self):
        self.engine.clear()

    def clear_srtm_boundary(self):
        self.engine.clear_srtm_boundary()

    def get_z(self, x, y):
        return self.engine.get_z(x, y)

    def build_contours(self, dto):
        boundary = dto.get("boundary", dto.get("boundary_coords"))
        step_z = float(dto.get("step_z", 1.0))
        grid_size = float(dto.get("grid_size", 5.0))
        elev = dto.get("elevation_points")
        prog = dto.get("progress_cb")
        interp = str(dto.get("interp_method") or "idw").strip().lower()
        return self.engine.generate_contours(
            boundary,
            step_z=step_z,
            grid_size=grid_size,
            elevation_points=elev,
            progress_cb=prog,
            interp_method=interp,
        )

    def fetch_srtm_grid(self, dto):
        boundary_coords = dto.get("boundary_coords", [])
        geo_ref = dto.get("geo_ref")
        resolution = float(dto.get("resolution", 30.0))
        source_mode = str(dto.get("source_mode", "auto") or "auto")
        res = self.engine.fetch_srtm_grid(
            boundary_coords,
            geo_ref,
            resolution,
            source_mode=source_mode,
        )
        if isinstance(res, dict):
            out = dict(res)
            out.setdefault("elevation_points", self.get_elevation_points())
            return out
        return {"count": int(res), "elevation_points": self.get_elevation_points()}

    def download_srtm_tiles(self, dto):
        from modules.geo_module import srtm_tiles

        geo_ref = dto.get("geo_ref")
        bb = dto.get("bounds_xy")
        if not geo_ref or not bb or len(bb) != 4:
            raise ValueError("Потрібні bounds (minx,miny,maxx,maxy) і гео-прив'язка.")
        minx, miny, maxx, maxy = bb
        tile_src = srtm_tiles.tile_source_for_schedule_mode(str(dto.get("source_mode", "auto") or "auto"))
        return srtm_tiles.download_tiles_for_xy_bounds(
            float(minx), float(miny), float(maxx), float(maxy), tuple(geo_ref), tile_source=tile_src
        )

