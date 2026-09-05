from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from noetrium_platform.foundation.kernel.kernel import JsonValue, canonical_digest, freeze_json, thaw_json


class SoftwareActionKind(StrEnum):
    READ = "read"
    EDIT = "edit"
    EXECUTE = "execute"
    TEST = "test"
    BUILD = "build"


@dataclass(frozen=True, slots=True)
class SoftwareEnvironmentSpec:
    environment_id: str
    revision: str
    workspace_root: str
    repository_digest: str = ""
    supported_actions: tuple[SoftwareActionKind, ...] = ()
    metadata: dict[str, JsonValue] = None

    def __post_init__(self) -> None:
        if not self.environment_id.strip() or not self.revision.strip() or not self.workspace_root.strip():
            raise ValueError("software environment identity is required")
        if any(not isinstance(item, SoftwareActionKind) for item in self.supported_actions):
            raise TypeError("software actions must use SoftwareActionKind")
        object.__setattr__(self, "metadata", freeze_json(self.metadata or {}))

    @property
    def spec_digest(self) -> str:
        return canonical_digest({
            "environment_id": self.environment_id, "revision": self.revision,
            "workspace_root": self.workspace_root, "repository_digest": self.repository_digest,
            "supported_actions": [item.value for item in self.supported_actions],
            "metadata": thaw_json(self.metadata),
        })


__all__ = ["SoftwareActionKind", "SoftwareEnvironmentSpec"]
