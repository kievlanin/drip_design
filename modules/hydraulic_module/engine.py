from modules.hydraulic_module.hydraulics_core import HydraulicEngine


class HydraulicModule:
    """Autonomous hydraulic module with DTO-style API."""

    def __init__(self):
        self.engine = HydraulicEngine()

    def run(self, dto):
        report, results = self.engine.calculate_network(dto)
        return {"report": report, "results": results}

    def get_pipes_db(self):
        return self.engine.pipes_db

