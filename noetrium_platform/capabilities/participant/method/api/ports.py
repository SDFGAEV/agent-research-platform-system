from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from noetrium_platform.foundation.kernel.kernel import canonical_digest, require_sha256

from .contracts import MethodIdentity, MethodSession
from .observability import MethodObservationOutboxFactoryPort, MethodServices


@dataclass(frozen=True, slots=True)
class MethodRuntimeIdentity:
    """Identity of the session execution engine, independent of method behavior."""

    runtime_id: str
    runtime_version: str
    abi_version: str
    artifact_digest: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.runtime_id, self.runtime_version, self.abi_version, self.artifact_digest
        )):
            raise ValueError("method runtime identity fields must be non-empty")
        require_sha256(self.artifact_digest, "method runtime artifact_digest")


@dataclass(frozen=True, slots=True)
class MethodRuntimeBinding:
    """Explicit pairing of independent scientific implementation and runtime identities."""

    implementation: MethodIdentity
    runtime: MethodRuntimeIdentity

    def digest(self) -> str:
        return canonical_digest({
            "method_implementation": self.implementation,
            "method_session_runtime": self.runtime,
        })

 

@runtime_checkable
class MethodImplementation(Protocol):
    """Scientific method implementation/configuration without lifecycle execution authority."""

    @property
    def identity(self) -> MethodIdentity: ...


@runtime_checkable
class MethodSessionRuntime(Protocol):
    """Execution engine that opens sessions for a separately supplied implementation."""

    @property
    def runtime_identity(self) -> MethodRuntimeIdentity: ...

    def open_session(
        self,
        implementation: MethodImplementation,
        *,
        binding: MethodRuntimeBinding,
        session_id: str,
        services: MethodServices,
    ) -> MethodSession: ...


@runtime_checkable
class MethodEndpointPort(Protocol):
    """Project/method-facing bound implementation + runtime endpoint."""

    @property
    def identity(self) -> MethodIdentity: ...

    @property
    def runtime_identity(self) -> MethodRuntimeIdentity: ...

    @property
    def binding(self) -> MethodRuntimeBinding: ...

    @property
    def binding_digest(self) -> str: ...

    def open_session(self, *, session_id: str, services: MethodServices) -> MethodSession: ...


@runtime_checkable
class MethodEndpointFactoryPort(Protocol):
    """Composition seam for binding a scientific implementation to a session runtime."""

    def bind(
        self,
        implementation: MethodImplementation,
        runtime: MethodSessionRuntime,
    ) -> MethodEndpointPort: ...


@dataclass(frozen=True, slots=True)
class MethodCompositionPorts:
    """All generic Participant/Method services a concrete method may require to compose."""

    endpoint_factory: MethodEndpointFactoryPort
    observation_outbox_factory: MethodObservationOutboxFactoryPort


__all__ = [
    "MethodCompositionPorts",
    "MethodEndpointFactoryPort",
    "MethodEndpointPort",
    "MethodImplementation",
    "MethodRuntimeBinding",
    "MethodRuntimeIdentity",
    "MethodSessionRuntime",
]
