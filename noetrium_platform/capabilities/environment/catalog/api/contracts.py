from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from noetrium_platform.foundation.scope.api import ScopeIdentity
from noetrium_platform.infrastructure.resources.resolution.contracts import ResolutionPolicy


class ExecutionEnvironmentKind(StrEnum):
    PYTHON = "python"
    CONDA = "conda"
    MAMBA = "mamba"
    NODE = "node"
    CONTAINER = "container"
    NATIVE = "native"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class EnvironmentTemplate:
    template_id: str
    kind: ExecutionEnvironmentKind
    scope: ScopeIdentity
    base_spec_id: str | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    spec_id: str
    kind: ExecutionEnvironmentKind
    scope: ScopeIdentity
    parent_spec_id: str | None = None
    template_id: str | None = None
    requirements: tuple[tuple[str, str], ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EnvironmentOverlay:
    overlay_id: str
    target_spec_id: str
    scope: ScopeIdentity
    requirements: tuple[tuple[str, str], ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EnvironmentAssignment:
    name: str
    spec_id: str
    scope: ScopeIdentity
    policy: ResolutionPolicy = ResolutionPolicy.INHERIT


@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentSpec:
    spec_id: str
    kind: ExecutionEnvironmentKind
    requested_scope: ScopeIdentity
    source_scopes: tuple[ScopeIdentity, ...]
    source_spec_ids: tuple[str, ...]
    applied_overlay_ids: tuple[str, ...]
    requirements: tuple[tuple[str, str], ...]
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class EnvironmentInstance:
    instance_id: str
    resolved_spec_digest: str
    backend: str
    runtime_reference: str
    scope: ScopeIdentity


@dataclass(frozen=True, slots=True)
class EnvironmentBinding:
    binding_id: str
    scope: ScopeIdentity
    role: str
    instance_id: str


__all__ = [
    "EnvironmentAssignment",
    "EnvironmentBinding",
    "EnvironmentInstance",
    "EnvironmentOverlay",
    "EnvironmentSpec",
    "EnvironmentTemplate",
    "ExecutionEnvironmentKind",
    "ResolvedEnvironmentSpec",
]
