from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from noetrium_platform.foundation.kernel.kernel import (
    JsonObject,
    JsonValue,
    canonical_digest,
    freeze_json,
    thaw_json,
)


@dataclass(frozen=True, slots=True)
class SyntheticEnvironmentSpec:
    environment_id: str
    revision: str
    initial_state: Mapping[str, JsonValue]
    action_types: tuple[str, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("environment_id", self.environment_id),
            ("revision", self.revision),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not self.action_types or any(not item.strip() for item in self.action_types):
            raise ValueError("synthetic action_types must be non-empty")
        if len(self.action_types) != len(set(self.action_types)):
            raise ValueError("synthetic action_types must be unique")
        object.__setattr__(self, "initial_state", freeze_json(self.initial_state))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "environment_id": self.environment_id,
            "revision": self.revision,
            "initial_state": thaw_json(self.initial_state),
            "action_types": list(self.action_types),
            "metadata": thaw_json(self.metadata),
        }

    @property
    def spec_digest(self) -> str:
        return canonical_digest(self.record())


__all__ = ["SyntheticEnvironmentSpec"]
