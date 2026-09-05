from __future__ import annotations
from dataclasses import dataclass
from noetrium_platform.foundation.scope.api import ScopeIdentity
from noetrium_platform.infrastructure.resources.resolution.contracts import ResolutionPolicy

@dataclass(frozen=True, slots=True)
class ModelAssignment:
    role: str
    model_id: str
    scope: ScopeIdentity
    policy: ResolutionPolicy = ResolutionPolicy.INHERIT

@dataclass(frozen=True, slots=True)
class ResolvedModelAssignment:
    role: str
    model_id: str
    requested_scope: ScopeIdentity
    source_scopes: tuple[ScopeIdentity, ...]

__all__ = ["ModelAssignment", "ResolvedModelAssignment"]
