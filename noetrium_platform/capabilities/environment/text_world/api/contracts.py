from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from noetrium_platform.foundation.kernel.kernel import JsonValue, canonical_digest, freeze_json, thaw_json


class TextWorldActionKind(StrEnum):
    COMMAND = "command"
    SPEAK = "speak"
    LOOK = "look"
    INVENTORY = "inventory"


@dataclass(frozen=True, slots=True)
class TextWorldEnvironmentSpec:
    environment_id: str
    revision: str
    turn_based: bool = True
    action_vocabulary: tuple[TextWorldActionKind, ...] = ()
    metadata: dict[str, JsonValue] = None

    def __post_init__(self) -> None:
        if not self.environment_id.strip() or not self.revision.strip():
            raise ValueError("text-world environment identity is required")
        if not isinstance(self.turn_based, bool):
            raise TypeError("text-world turn_based flag must be boolean")
        if any(not isinstance(item, TextWorldActionKind) for item in self.action_vocabulary):
            raise TypeError("text-world actions must use TextWorldActionKind")
        object.__setattr__(self, "metadata", freeze_json(self.metadata or {}))

    @property
    def spec_digest(self) -> str:
        return canonical_digest({
            "environment_id": self.environment_id, "revision": self.revision,
            "turn_based": self.turn_based,
            "action_vocabulary": [item.value for item in self.action_vocabulary],
            "metadata": thaw_json(self.metadata),
        })


__all__ = ["TextWorldActionKind", "TextWorldEnvironmentSpec"]
