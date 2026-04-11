__all__ = ["HydraulicModule"]


def __getattr__(name: str):
    if name == "HydraulicModule":
        from .engine import HydraulicModule

        return HydraulicModule
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
