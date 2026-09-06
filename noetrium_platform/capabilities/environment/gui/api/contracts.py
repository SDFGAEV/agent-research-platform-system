from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from noetrium_platform.foundation.kernel.kernel import JsonValue, canonical_digest, freeze_json, thaw_json


class GuiActionKind(StrEnum):
    CLICK = "click"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    DRAG = "drag"
    WAIT = "wait"


@dataclass(frozen=True, slots=True)
class GuiEnvironmentSpec:
    environment_id: str
    revision: str
    viewport: tuple[int, int] = (0, 0)
    supports_accessibility: bool = False
    supported_actions: tuple[GuiActionKind, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.environment_id.strip() or not self.revision.strip():
            raise ValueError("GUI environment identity is required")
        if len(self.viewport) != 2 or any(type(v) is not int or v < 0 for v in self.viewport):
            raise ValueError("GUI viewport must contain two non-negative integers")
        if not isinstance(self.supports_accessibility, bool):
            raise TypeError("GUI accessibility flag must be boolean")
        if any(not isinstance(item, GuiActionKind) for item in self.supported_actions):
            raise TypeError("GUI actions must use GuiActionKind")
        if len(self.supported_actions) != len(set(self.supported_actions)):
            raise ValueError("GUI actions must be unique")
        object.__setattr__(self, "metadata", freeze_json(self.metadata or {}))

    @property
    def spec_digest(self) -> str:
        return canonical_digest({
            "environment_id": self.environment_id, "revision": self.revision,
            "viewport": list(self.viewport),
            "supports_accessibility": self.supports_accessibility,
            "supported_actions": [item.value for item in self.supported_actions],
            "metadata": thaw_json(self.metadata),
        })


__all__ = ["GuiActionKind", "GuiEnvironmentSpec"]
