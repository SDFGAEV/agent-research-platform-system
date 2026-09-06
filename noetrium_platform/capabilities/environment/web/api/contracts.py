from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from noetrium_platform.foundation.kernel.kernel import JsonValue, canonical_digest, freeze_json, thaw_json


class WebActionKind(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    SUBMIT = "submit"


@dataclass(frozen=True, slots=True)
class WebEnvironmentSpec:
    environment_id: str
    revision: str
    origin: str = ""
    supports_dom: bool = True
    supported_actions: tuple[WebActionKind, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.environment_id.strip() or not self.revision.strip():
            raise ValueError("Web environment identity is required")
        if not isinstance(self.supports_dom, bool):
            raise TypeError("Web DOM flag must be boolean")
        if any(not isinstance(item, WebActionKind) for item in self.supported_actions):
            raise TypeError("Web actions must use WebActionKind")
        if len(self.supported_actions) != len(set(self.supported_actions)):
            raise ValueError("Web actions must be unique")
        object.__setattr__(self, "metadata", freeze_json(self.metadata or {}))

    @property
    def spec_digest(self) -> str:
        return canonical_digest({
            "environment_id": self.environment_id, "revision": self.revision,
            "origin": self.origin, "supports_dom": self.supports_dom,
            "supported_actions": [item.value for item in self.supported_actions],
            "metadata": thaw_json(self.metadata),
        })


__all__ = ["WebActionKind", "WebEnvironmentSpec"]
