from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .contracts import EnvironmentIdentity, EnvironmentSession
from .errors import EnvironmentCapabilityUnsupported


class EnvironmentCapability(StrEnum):
    """Optional provider capabilities that must never be inferred by probing internals."""

    SNAPSHOT = "snapshot"
    RESTORE = "restore"
    RECONCILE = "reconcile"
    DIAGNOSTICS = "diagnostics"
    QUERY = "query"
    COORDINATION = "coordination"
    RAW_RECORDS = "raw_records"
    ARTIFACTS = "artifacts"
    TASKS = "tasks"


@dataclass(frozen=True, slots=True)
class EnvironmentProviderCapabilities:
    supported: tuple[EnvironmentCapability, ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(self.supported))
        if any(not isinstance(item, EnvironmentCapability) for item in normalized):
            raise TypeError("environment capabilities must use EnvironmentCapability values")
        if EnvironmentCapability.RESTORE in normalized and EnvironmentCapability.SNAPSHOT not in normalized:
            raise ValueError("restore capability requires snapshot capability")
        object.__setattr__(self, "supported", normalized)

    @classmethod
    def fully_recoverable(cls) -> "EnvironmentProviderCapabilities":
        """Return the baseline lifecycle set, not every optional domain feature."""
        return cls((
            EnvironmentCapability.SNAPSHOT,
            EnvironmentCapability.RESTORE,
            EnvironmentCapability.RECONCILE,
            EnvironmentCapability.DIAGNOSTICS,
        ))

    def supports(self, capability: EnvironmentCapability) -> bool:
        if not isinstance(capability, EnvironmentCapability):
            raise TypeError("capability must be an EnvironmentCapability")
        return capability in self.supported

    def require(self, capability: EnvironmentCapability) -> None:
        if not self.supports(capability):
            raise EnvironmentCapabilityUnsupported(capability.value)


class EnvironmentSessionServices(Protocol):
    """Marker for explicitly composed services; providers must not discover a service locator."""


@dataclass(frozen=True, slots=True)
class EnvironmentSessionDiagnostics:
    session_id: str
    environment: EnvironmentIdentity
    generation: str
    ready: bool
    closed: bool
    capabilities: EnvironmentProviderCapabilities
    state_digest: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("environment diagnostics session_id must be non-empty")
        if not isinstance(self.environment, EnvironmentIdentity):
            raise TypeError("environment diagnostics require EnvironmentIdentity")
        if not isinstance(self.generation, str) or not self.generation.strip():
            raise ValueError("environment diagnostics generation must be non-empty")
        if not isinstance(self.ready, bool) or not isinstance(self.closed, bool):
            raise TypeError("environment diagnostics ready/closed must be booleans")
        if not isinstance(self.capabilities, EnvironmentProviderCapabilities):
            raise TypeError("environment diagnostics require typed capabilities")
        if self.state_digest is not None:
            if (
                self.state_digest != self.state_digest.lower()
                or len(self.state_digest) != 64
                or any(char not in "0123456789abcdef" for char in self.state_digest)
            ):
                raise ValueError("environment diagnostics state_digest must be canonical SHA-256")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise ValueError("environment diagnostics evidence refs must be non-empty strings")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("environment diagnostics evidence refs must be unique")


@runtime_checkable
class EnvironmentDiagnosticsPort(Protocol):
    def diagnostics_snapshot(self) -> EnvironmentSessionDiagnostics: ...


@runtime_checkable
class EnvironmentProviderPort(Protocol):
    @property
    def identity(self) -> EnvironmentIdentity: ...

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities: ...

    def open_session(
        self,
        *,
        session_id: str,
        services: EnvironmentSessionServices,
    ) -> EnvironmentSession: ...


__all__ = [
    "EnvironmentCapability",
    "EnvironmentDiagnosticsPort",
    "EnvironmentProviderCapabilities",
    "EnvironmentProviderPort",
    "EnvironmentSessionDiagnostics",
    "EnvironmentSessionServices",
]
