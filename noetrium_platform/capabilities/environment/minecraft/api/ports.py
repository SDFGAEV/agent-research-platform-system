from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from noetrium_platform.capabilities.environment.runtime.api import (
    ActionReconciliationDisposition,
    ActionRequest,
    ActionResult,
    EnvironmentSession,
    Observation,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonInput, JsonValue
from noetrium_platform.infrastructure.lifecycle.service.api.ports import ServiceReadyObservation, ServiceStartOutcome, ServiceStopOutcome

from .contracts import (
    MinecraftBranchRuntimeRequest,
    MinecraftObservationEvent,
    MinecraftConsoleCommandResult,
    MinecraftRconEndpoint,
    MinecraftServerSpec,
    MinecraftWorldBranch,
    MinecraftWorldCut,
    MinecraftWorldQuiescence,
)
from .scenario import MinecraftScenarioReceipt


class MinecraftServerLifecyclePort(Protocol):
    """Narrow lifecycle port consumed by a branch binder."""

    def start(self) -> ServiceStartOutcome: ...
    def verify_ready(self) -> ServiceReadyObservation: ...
    def stop(self) -> ServiceStopOutcome: ...


class MinecraftServerEndpointBindingPort(Protocol):
    """Publish one exact READY process generation into endpoint authority."""

    def bind_ready(self, readiness: ServiceReadyObservation) -> None: ...


class MinecraftSessionServices(Protocol):
    """Marker for services supplied by the outer participant composition."""


class MinecraftBranchServerFactoryPort(Protocol):
    def create(
        self,
        server_spec: MinecraftServerSpec,
        *,
        environment_generation: str,
    ) -> MinecraftServerLifecyclePort: ...


class MinecraftBranchRuntimePort(Protocol):
    @property
    def environment_generation(self) -> str: ...

    def open_session(self, services: MinecraftSessionServices) -> EnvironmentSession: ...
    def close(self) -> None: ...


class MinecraftBranchRuntimeFactoryPort(Protocol):
    def open(self, request: MinecraftBranchRuntimeRequest) -> MinecraftBranchRuntimePort: ...


@dataclass(frozen=True, slots=True)
class MinecraftBridgeCommandResult:
    command: str
    acknowledged: bool
    verified: bool | None
    events: tuple[MinecraftObservationEvent, ...] = ()
    diagnostics: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MinecraftReconciliation:
    action_id: str
    disposition: ActionReconciliationDisposition
    observation: Observation | None = None
    diagnostics: Mapping[str, JsonValue] = field(default_factory=dict)


class MinecraftBridgePort(Protocol):
    """Bridge seam; the session does not depend on Mineflayer or Node."""

    @property
    def action_recovery_durability(self) -> str: ...

    def configure_action_recovery(self, namespace: str) -> None: ...

    def start(self) -> None: ...

    def supports_command(self, command: str) -> bool: ...

    def command(
        self,
        command: str,
        payload: Mapping[str, JsonInput],
        *,
        timeout_s: float,
    ) -> MinecraftBridgeCommandResult: ...

    def reconcile_action(
        self,
        action_id: str,
        *,
        request: ActionRequest,
        context: ExecutionContext,
        request_digest: str | None = None,
    ) -> MinecraftReconciliation: ...

    def close(self) -> None: ...


class MinecraftDiagnosticsPort(Protocol):
    """MC-owned diagnostic seam; storage and policy stay outside MC."""

    def event(
        self,
        *,
        phase: str,
        event: str,
        attributes: Mapping[str, JsonValue] | None = None,
        level: str = "DEBUG",
        correlation_refs: tuple[str, ...] = (),
    ) -> None: ...

    def failure(
        self,
        *,
        phase: str,
        code: str,
        message: str,
        exception: BaseException | None = None,
        attributes: Mapping[str, JsonValue] | None = None,
        correlation_refs: tuple[str, ...] = (),
    ) -> None: ...

    def metric(
        self,
        *,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
    ) -> None: ...


class MinecraftCheckpointPort(Protocol):
    """World checkpoint seam; a client-state snapshot is not a world snapshot."""

    def capture(self, *, session_id: str, context: ExecutionContext | None) -> bytes: ...

    def restore(
        self,
        payload: bytes,
        *,
        session_id: str,
        context: ExecutionContext | None,
    ) -> None: ...


class MinecraftServerConsolePort(Protocol):
    """Narrow MC server-control seam; process supervision stays generic."""

    def execute(
        self,
        command: str,
        *,
        timeout_s: float,
    ) -> MinecraftConsoleCommandResult: ...


class MinecraftScenarioProvisioningPort(Protocol):
    """Apply and prove one immutable source-world scenario."""

    def apply(self) -> MinecraftScenarioReceipt: ...


class MinecraftWorldQuiescencePort(Protocol):
    """Provider-specific save/quiesce control; it owns no snapshot bytes."""

    def save_and_quiesce(
        self,
        *,
        session_id: str,
        context: ExecutionContext | None,
    ) -> MinecraftWorldQuiescence: ...

    def resume(
        self,
        quiescence: MinecraftWorldQuiescence,
        *,
        session_id: str,
        context: ExecutionContext | None,
    ) -> None: ...


class MinecraftWorldCutPort(Protocol):
    """World-cut and isolated-branch seam used by experiment composition."""

    def capture(
        self,
        *,
        session_id: str,
        context: ExecutionContext | None,
    ) -> MinecraftWorldCut: ...

    def materialize_branch(
        self,
        cut: MinecraftWorldCut,
        *,
        branch_id: str,
        destination_workdir: str,
    ) -> MinecraftWorldBranch: ...

    def release_branch(self, branch: MinecraftWorldBranch) -> str: ...


class MinecraftWorldCutMetadataStorePort(Protocol):
    """Durable publication seam for world-cut manifests and branch metadata."""

    def publish(self, path: str, payload: bytes) -> None: ...


class MinecraftExperimentHostPort(Protocol):
    """Reusable MC experiment host surface exposed to project composition."""

    world_cuts: MinecraftWorldCutPort
    branch_runtime_factory: MinecraftBranchRuntimeFactoryPort
    source_scenario_receipt: MinecraftScenarioReceipt | None

    def start_source(self) -> ServiceStartOutcome: ...
    def process_identity_digest(self) -> str: ...
    def stop_source(self) -> ServiceStopOutcome | None: ...


__all__ = [
    "MinecraftBridgeCommandResult",
    "MinecraftSessionServices",
    "MinecraftBranchRuntimeFactoryPort",
    "MinecraftBranchRuntimePort",
    "MinecraftBranchServerFactoryPort",
    "MinecraftBridgePort",
    "MinecraftDiagnosticsPort",
    "MinecraftCheckpointPort",
    "MinecraftConsoleCommandResult",
    "MinecraftRconEndpoint",
    "MinecraftServerConsolePort",
    "MinecraftScenarioProvisioningPort",
    "MinecraftWorldBranch",
    "MinecraftWorldCut",
    "MinecraftWorldCutMetadataStorePort",
    "MinecraftWorldCutPort",
    "MinecraftExperimentHostPort",
    "MinecraftWorldQuiescence",
    "MinecraftWorldQuiescencePort",
    "MinecraftReconciliation",
    "MinecraftServerLifecyclePort",
    "MinecraftServerEndpointBindingPort",
]
