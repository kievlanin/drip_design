from dataclasses import dataclass, field
from typing import Any, Mapping


def _copy_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _copy_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass
class HydraulicResultsSnapshot:
    sections: list[Any] = field(default_factory=list)
    valves: dict[str, Any] = field(default_factory=dict)
    emitters: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Any) -> "HydraulicResultsSnapshot":
        payload = _copy_dict(data)
        return cls(
            sections=_copy_list(payload.pop("sections", [])),
            valves=_copy_dict(payload.pop("valves", {})),
            emitters=_copy_dict(payload.pop("emitters", {})),
            extras=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.extras)
        out["sections"] = list(self.sections)
        out["valves"] = dict(self.valves)
        out["emitters"] = dict(self.emitters)
        return out


@dataclass
class HydraulicRunSnapshot:
    report: str = ""
    results: HydraulicResultsSnapshot = field(default_factory=HydraulicResultsSnapshot)

    @classmethod
    def from_mapping(cls, data: Any) -> "HydraulicRunSnapshot":
        payload = _copy_dict(data)
        return cls(
            report=str(payload.get("report", "") or ""),
            results=HydraulicResultsSnapshot.from_mapping(payload.get("results")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report,
            "results": self.results.to_dict(),
        }


@dataclass
class BomSnapshot:
    items: list[Any] = field(default_factory=list)
    fitting_items: list[Any] = field(default_factory=list)
    frozen_count: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Any) -> "BomSnapshot":
        payload = _copy_dict(data)
        frozen = payload.pop("frozen_count", 0)
        try:
            frozen_count = int(frozen)
        except (TypeError, ValueError):
            frozen_count = 0
        return cls(
            items=_copy_list(payload.pop("items", [])),
            fitting_items=_copy_list(payload.pop("fitting_items", [])),
            frozen_count=frozen_count,
            extras=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.extras)
        out["items"] = list(self.items)
        out["fitting_items"] = list(self.fitting_items)
        out["frozen_count"] = int(self.frozen_count)
        return out
