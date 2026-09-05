from __future__ import annotations

import math

from ..api.contracts import MetricDefinition, MetricKind


RESERVED_DIMENSIONS = frozenset({"system", "topology_generation"})


class MetricRegistry:
    def __init__(self) -> None:
        self._defs: dict[str, MetricDefinition] = {}

    def register(self, definition: MetricDefinition) -> None:
        if definition.name in self._defs and self._defs[definition.name] != definition:
            raise ValueError(f"metric redefined: {definition.name}")
        self._defs[definition.name] = definition

    def definition(self, name: str) -> MetricDefinition:
        try:
            return self._defs[name]
        except KeyError as exc:
            raise KeyError(f"unregistered metric: {name}") from exc

    def validate(self, name: str, dimensions: dict[str, str]) -> None:
        definition = self.definition(name)
        unknown = set(dimensions) - set(definition.allowed_dimensions) - RESERVED_DIMENSIONS
        if unknown:
            raise ValueError(f"metric {name} unknown dimensions: {sorted(unknown)}")
        for key, value in dimensions.items():
            if not isinstance(value, str):
                raise TypeError(f"metric {name} dimension {key} must be str")
            if len(value) > 128:
                raise ValueError(f"metric {name} dimension {key} too long")

    def validate_observation(self, name: str, value: float, dimensions: dict[str, str]) -> float:
        self.validate(name, dimensions)
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"metric {name} must be finite")
        definition = self.definition(name)
        if definition.kind == MetricKind.COUNTER and numeric < 0:
            raise ValueError(f"counter {name} cannot observe a negative increment")
        if definition.unit == "ratio" and not 0.0 <= numeric <= 1.0:
            raise ValueError(f"ratio metric {name} must be in [0,1]")
        return numeric

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._defs))


__all__ = ["MetricRegistry"]
