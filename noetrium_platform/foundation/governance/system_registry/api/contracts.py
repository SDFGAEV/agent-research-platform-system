from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


STANDARD_SYSTEM_SHAPE: tuple[str, ...] = ("api", "runtime", "providers", "composition")


class SystemLayer(StrEnum):
    PLATFORM = "platform"
    KERNEL = "kernel"
    SCOPE = "scope"
    PORTFOLIO = "portfolio"
    EXPERIMENTATION = "experimentation"
    EXECUTION = "execution"
    PARTICIPANT = "participant"
    DATA = "data"
    RUNTIME = "runtime"
    ENVIRONMENT = "environment"
    ARTIFACT = "artifact"
    PROMPT = "prompt"
    MODEL = "model"
    RESOURCE = "resource"
    INFRASTRUCTURE = "infrastructure"
    RELIABILITY = "reliability"
    OBSERVABILITY = "observability"
    GOVERNANCE = "governance"
    OPERATOR = "operator"
    COMPOSITION = "composition"
    COMPONENTS = "components"
    ORCHESTRATION = "orchestration"


@dataclass(frozen=True, slots=True, order=True)
class SystemIdentity:
    """Stable identity for a system node at any depth in the system tree."""

    system_id: str
    subsystem_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.system_id.strip():
            raise ValueError("system_id must be non-empty")
        if any(not segment.strip() or "/" in segment for segment in self.subsystem_path):
            raise ValueError("subsystem path segments must be non-empty and cannot contain '/'")

    @property
    def key(self) -> str:
        return "/".join((self.system_id, *self.subsystem_path))

    @property
    def is_system(self) -> bool:
        return not self.subsystem_path

    @property
    def depth(self) -> int:
        return len(self.subsystem_path)

    @property
    def parent_key(self) -> str | None:
        if self.is_system:
            return None
        return "/".join((self.system_id, *self.subsystem_path[:-1]))


@dataclass(frozen=True, slots=True)
class AuthorityDescriptor:
    authority_id: str
    state_kinds: tuple[str, ...] = ()
    effect_kinds: tuple[str, ...] = ()
    artifact_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.authority_id.strip():
            raise ValueError("authority_id must be non-empty")


@dataclass(frozen=True, slots=True)
class SystemDescriptor:
    identity: SystemIdentity
    layer: SystemLayer
    package_prefix: str
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    authorities: tuple[AuthorityDescriptor, ...] = ()
    components: tuple[str, ...] = ()
    owns: str = ""
    must_not_own: str = ""
    shape: tuple[str, ...] = STANDARD_SYSTEM_SHAPE

    def __post_init__(self) -> None:
        platform_prefix = (
            self.package_prefix == "noetrium_platform"
            or self.package_prefix.startswith("noetrium_platform.")
        )
        component_prefix = self.identity.system_id == "components" and (
            self.package_prefix == "components" or self.package_prefix.startswith("components.")
        )
        orchestration_prefix = self.identity.system_id == "orchestration" and (
            self.package_prefix == "orchestration" or self.package_prefix.startswith("orchestration.")
        )
        if not platform_prefix and not component_prefix and not orchestration_prefix:
            raise ValueError("system package_prefix must be inside noetrium_platform or a registered root extension namespace")

    @property
    def parent_key(self) -> str | None:
        return self.identity.parent_key

    @property
    def authority_id(self) -> str | None:
        """Return the canonical authority id when this descriptor has one."""

        if len(self.authorities) != 1:
            return None
        return self.authorities[0].authority_id


@dataclass(frozen=True, slots=True)
class SystemRegistryChange:
    """Immutable topology mutation snapshot delivered to registry subscribers."""

    registered: tuple[SystemDescriptor, ...]
    generation: int
    topology_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.registered, tuple) or not self.registered:
            raise ValueError("registered must be a non-empty tuple")
        if not all(isinstance(item, SystemDescriptor) for item in self.registered):
            raise TypeError("registered must contain SystemDescriptor values")
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("generation must be a positive integer")
        if (
            not isinstance(self.topology_digest, str)
            or len(self.topology_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.topology_digest)
        ):
            raise ValueError("topology_digest must be a lowercase SHA-256 digest")


__all__ = [
    "AuthorityDescriptor",
    "STANDARD_SYSTEM_SHAPE",
    "SystemDescriptor",
    "SystemIdentity",
    "SystemRegistryChange",
    "SystemLayer",
]
