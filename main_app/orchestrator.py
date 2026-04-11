from shapely.geometry import LineString

from modules.geo_module.engine import GeoModule
from modules.hydraulic_module.engine import HydraulicModule
from modules.bom_module.engine import BOMModule


class IrrigationOrchestrator:
    """Coordinates Geo, Hydraulic and BOM modules via DTO exchange."""

    def __init__(self):
        self.geo_module = GeoModule()
        self.hydraulic_module = HydraulicModule()
        self.bom_module = BOMModule()

        self.last_hydraulic = {"report": "", "results": {"sections": [], "valves": {}, "emitters": {}}}
        self.last_bom = {"items": [], "fitting_items": [], "frozen_count": 0}
        self.last_stress = {"report": "", "results": {"sections": [], "valves": {}, "emitters": {}}}

    def get_default_pipe_db(self):
        return self.hydraulic_module.get_pipes_db()

    def sync_topography_from_ui(self, topo):
        self.geo_module.set_elevation_points(getattr(topo, "elevation_points", []))
        self.geo_module.set_srtm_boundary(getattr(topo, "srtm_boundary_pts_local", []))

    def sync_topography_to_ui(self, topo):
        topo.elevation_points = self.geo_module.get_elevation_points()
        topo.srtm_boundary_pts_local = self.geo_module.get_srtm_boundary()

    def build_contours(
        self,
        boundary,
        step_z,
        grid_size,
        elevation_points=None,
        progress_cb=None,
        interp_method: str = "idw",
    ):
        dto = {
            "boundary": boundary,
            "step_z": step_z,
            "grid_size": grid_size,
            "elevation_points": elevation_points,
            "progress_cb": progress_cb,
            "interp_method": interp_method,
        }
        return self.geo_module.build_contours(dto)

    def fetch_srtm_grid(self, boundary_coords, geo_ref, resolution):
        dto = {
            "boundary_coords": boundary_coords,
            "geo_ref": geo_ref,
            "resolution": resolution,
        }
        return self.geo_module.fetch_srtm_grid(dto)

    def download_srtm_tiles(self, geo_ref, bounds_xy):
        dto = {
            "geo_ref": geo_ref,
            "bounds_xy": bounds_xy,
        }
        return self.geo_module.download_srtm_tiles(dto)

    def run_hydraulic_preset(self, dto):
        result = self.hydraulic_module.run(dto)
        self.last_hydraulic = result
        return result

    def run_bom(self, sections, pipes_db, quantization=None):
        dto = {
            "sections": sections,
            "pipes_db": pipes_db,
            "quantization": quantization or {},
        }
        self.last_bom = self.bom_module.build_bom(dto)
        return self.last_bom

    def freeze_bom(self):
        return self.bom_module.freeze(self.last_bom.get("items", []))

    def run_stress_test(self, hydraulic_dto):
        adjusted = dict(hydraulic_dto)
        sections = self.last_hydraulic.get("results", {}).get("sections", [])
        if sections:
            adjusted["submain_lines"] = [sec["coords"] for sec in sections if sec.get("coords")]
            adjusted["all_lats"] = [
                LineString(coords)
                for coords in [
                    list(lat.coords) if hasattr(lat, "coords") else lat
                    for lat in hydraulic_dto.get("all_lats", [])
                ]
                if len(coords) > 1
            ]
        result = self.hydraulic_module.run(adjusted)
        self.last_stress = result
        return result

