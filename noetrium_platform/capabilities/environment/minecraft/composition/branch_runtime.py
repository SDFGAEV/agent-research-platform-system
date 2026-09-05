from __future__ import annotations

from dataclasses import replace
import math
from typing import Protocol

from noetrium_platform.capabilities.environment.runtime.api import DurablePreparedActionSession, EnvironmentSession
from noetrium_platform.infrastructure.resources.allocation.api import (
    EndpointAllocation,
    EndpointAllocationState,
    EndpointBindingProof,
    EndpointAllocationPort,
    EndpointLeaseGuardFactoryPort,
    EndpointLeaseGuardPort,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest
from noetrium_platform.infrastructure.lifecycle.service.api import ServiceReadyObservation

from ..api import (
    MinecraftBranchRuntimeFactoryPort,
    MinecraftBranchRuntimePort,
    MinecraftBranchRuntimeRequest,
    MinecraftBranchServerFactoryPort,
    MinecraftCheckpointPort,
    MinecraftEnvironmentSpec,
    MinecraftServerSpec,
    MinecraftServerLifecyclePort,
    MinecraftServerEndpointBindingPort,
    MinecraftSessionServices,
)
from .environment import MinecraftEnvironmentAssembly
from ..runtime import MinecraftEnvironmentImplementation, MinecraftEnvironmentRuntime


class MinecraftBranchRuntimeError(RuntimeError):
    """A branch runtime failed and its cleanup may also require inspection."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        cause: BaseException | None = None,
        cleanup_errors: tuple[BaseException, ...] = (),
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.cause = cause
        self.cleanup_errors = cleanup_errors


class MinecraftBranchEnvironmentFactoryPort(Protocol):
    def compose(
        self,
        spec: MinecraftEnvironmentSpec,
        *,
        checkpoint: MinecraftCheckpointPort | None = None,
    ) -> MinecraftEnvironmentAssembly: ...


class MinecraftBranchCheckpointFactoryPort(Protocol):
    """Create one authoritative checkpoint provider after branch endpoints are frozen."""

    def create(
        self,
        *,
        server: MinecraftServerLifecyclePort,
        server_spec: MinecraftServerSpec,
        environment_generation: str,
        endpoint_binding: MinecraftServerEndpointBindingPort,
    ) -> MinecraftCheckpointPort: ...


class _EndpointGenerationAllocationPort(EndpointAllocationPort, Protocol):
    """ROLE02 generation-fenced endpoint authority required by MC restarts."""

    def replace_bound(
        self,
        proof: EndpointBindingProof,
        *,
        expected_previous_binding_proof_digest: str,
    ) -> EndpointAllocation: ...


class _MinecraftEndpointBindingAuthority(MinecraftServerEndpointBindingPort):
    """Bind each exact server process generation to its reserved endpoints."""

    def __init__(
        self,
        *,
        allocation: EndpointAllocation,
        rcon_allocation: EndpointAllocation | None,
        endpoint_allocations: _EndpointGenerationAllocationPort,
        environment_generation: str,
    ) -> None:
        self.allocation = allocation
        self.rcon_allocation = rcon_allocation
        self._endpoint_allocations = endpoint_allocations
        self._environment_generation = environment_generation

    @staticmethod
    def _ready_at(readiness: ServiceReadyObservation) -> float:
        try:
            observed_at = readiness.ready_at
        except AttributeError as exc:
            raise MinecraftBranchRuntimeError(
                "branch readiness omitted authoritative ready_at",
                phase="bind",
                cause=exc,
            ) from exc
        if (
            isinstance(observed_at, bool)
            or not isinstance(observed_at, (int, float))
            or not math.isfinite(float(observed_at))
            or observed_at <= 0
        ):
            raise MinecraftBranchRuntimeError(
                "branch readiness ready_at is not finite and positive",
                phase="bind",
            )
        return float(observed_at)

    def _confirm(
        self,
        allocation: EndpointAllocation,
        *,
        readiness: ServiceReadyObservation,
        binder_identity_digest: str,
        observed_at: float,
    ) -> EndpointAllocation:
        proof = EndpointBindingProof(
            allocation_id=allocation.allocation_id,
            endpoint=allocation.endpoint,
            lease_fencing_token=allocation.lease_fencing_token,
            binder_identity_digest=binder_identity_digest,
            observed_at_epoch_s=observed_at,
            evidence_ref=readiness.ready_evidence_ref,
        )
        proof_digest = proof.digest()
        if allocation.state is EndpointAllocationState.RESERVED:
            return self._endpoint_allocations.confirm_bound(proof)
        if allocation.state is not EndpointAllocationState.BOUND:
            raise MinecraftBranchRuntimeError(
                "branch endpoint is neither reserved nor bound",
                phase="bind",
            )
        if allocation.binding_proof_digest == proof_digest:
            return self._endpoint_allocations.confirm_bound(proof)
        previous = allocation.binding_proof_digest
        if previous is None:
            raise MinecraftBranchRuntimeError(
                "bound branch endpoint has no prior binding proof digest",
                phase="bind",
            )
        return self._endpoint_allocations.replace_bound(
            proof,
            expected_previous_binding_proof_digest=previous,
        )

    def bind_ready(self, readiness: ServiceReadyObservation) -> None:
        if not isinstance(readiness, ServiceReadyObservation):
            raise MinecraftBranchRuntimeError(
                "branch readiness did not return typed ServiceReadyObservation",
                phase="bind",
            )
        observed_at = self._ready_at(readiness)
        binder_identity_digest = canonical_digest(
            {
                "contract_digest": readiness.contract_digest,
                "process": readiness.process,
                "environment_generation": self._environment_generation,
            }
        )
        self.allocation = self._confirm(
            self.allocation,
            readiness=readiness,
            binder_identity_digest=binder_identity_digest,
            observed_at=observed_at,
        )
        if self.rcon_allocation is not None:
            self.rcon_allocation = self._confirm(
                self.rcon_allocation,
                readiness=readiness,
                binder_identity_digest=binder_identity_digest,
                observed_at=observed_at,
            )


class _LeaseGuardedEnvironmentSession:
    """Delegate an environment session while enforcing endpoint lease health."""

    def __init__(self, session: EnvironmentSession, guard: EndpointLeaseGuardPort) -> None:
        self._session = session
        self._guard = guard

    def _call(self, name: str, *args: object, **kwargs: object):
        self._guard.assert_healthy()
        result = getattr(self._session, name)(*args, **kwargs)
        self._guard.assert_healthy()
        return result

    def observe(self, context):
        return self._call("observe", context)

    def act(self, request):
        return self._call("act", request)

    def reconcile(self, effect, context):
        return self._call("reconcile", effect, context)

    def query(self, request):
        return self._call("query", request)

    def capability_descriptors(self):
        return self._call("capability_descriptors")

    def diagnostics_snapshot(self):
        return self._call("diagnostics_snapshot")

    def checkpoint(self) -> bytes:
        return self._call("checkpoint")

    def restore(self, payload: bytes) -> None:
        self._call("restore", payload)

    def close(self) -> None:
        # Cleanup must never be blocked by a previously failed heartbeat.
        self._session.close()


class _LeaseGuardedPreparedEnvironmentSession(_LeaseGuardedEnvironmentSession):
    """Preserve prepared-effect capability only when the delegate truly owns it."""

    def __init__(
        self, session: DurablePreparedActionSession, guard: EndpointLeaseGuardPort
    ) -> None:
        super().__init__(session, guard)

    @property
    def action_recovery_durability(self) -> str:
        return self._session.action_recovery_durability

    def prepare_action_recovery(self, request, context):
        return self._call("prepare_action_recovery", request, context)

    def execute_prepared_action(self, request, handle):
        return self._call("execute_prepared_action", request, handle)

    def reconcile_prepared_action(self, handle, context):
        return self._call("reconcile_prepared_action", handle, context)


class MinecraftBranchRuntimeBinding(MinecraftBranchRuntimePort):
    """Own one branch's server/session/endpoint lifecycle in reverse order."""

    def __init__(
        self,
        *,
        endpoint_binding: _MinecraftEndpointBindingAuthority,
        implementation: MinecraftEnvironmentImplementation,
        environment_runtime: MinecraftEnvironmentRuntime,
        server: MinecraftServerLifecyclePort,
        session_id: str,
        endpoint_allocations: EndpointAllocationPort,
        lease_guard: EndpointLeaseGuardPort,
    ) -> None:
        self._endpoint_binding = endpoint_binding
        self.implementation = implementation
        self._environment_runtime = environment_runtime
        self._server = server
        self._session_id = session_id
        self._endpoint_allocations = endpoint_allocations
        self._lease_guard = lease_guard
        self._session: EnvironmentSession | None = None
        self._closed = False
        self._released_allocation_ids: set[str] = set()

    @property
    def allocation(self) -> EndpointAllocation:
        return self._endpoint_binding.allocation

    @property
    def rcon_allocation(self) -> EndpointAllocation | None:
        return self._endpoint_binding.rcon_allocation

    @property
    def environment_generation(self) -> str:
        return self.implementation.identity.artifact_digest

    def _confirm_bound_endpoints(self, readiness: ServiceReadyObservation) -> None:
        self._endpoint_binding.bind_ready(readiness)

    def open_session(self, services: MinecraftSessionServices) -> EnvironmentSession:
        if self._closed:
            raise MinecraftBranchRuntimeError("branch runtime is closed")
        if self._session is not None:
            return self._session
        try:
            self._server.start()
            readiness = self._server.verify_ready()
            self._confirm_bound_endpoints(readiness)
            self._lease_guard.assert_healthy()
            raw_session = self._environment_runtime.open_session(
                self.implementation,
                session_id=self._session_id,
                services=services,
            )
            self._session = (
                _LeaseGuardedPreparedEnvironmentSession(raw_session, self._lease_guard)
                if isinstance(raw_session, DurablePreparedActionSession)
                else _LeaseGuardedEnvironmentSession(raw_session, self._lease_guard)
            )
            self._lease_guard.assert_healthy()
            return self._session
        except BaseException as exc:
            cleanup_errors: list[BaseException] = []
            try:
                self._server.stop()
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            try:
                self._lease_guard.close()
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            try:
                self._release_allocations()
            except BaseException as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                raise MinecraftBranchRuntimeError(
                    "branch runtime start failed and cleanup failed",
                    phase="start",
                    cause=exc,
                    cleanup_errors=tuple(cleanup_errors),
                ) from exc
            raise MinecraftBranchRuntimeError(
                "branch runtime start failed",
                phase="start",
                cause=exc,
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        if self._session is not None:
            try:
                self._session.close()
            except BaseException as exc:
                errors.append(exc)
        try:
            self._server.stop()
        except BaseException as exc:
            errors.append(exc)
        try:
            self._lease_guard.close()
        except BaseException as exc:
            errors.append(exc)
        try:
            self._release_allocations()
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise MinecraftBranchRuntimeError(
                f"branch runtime close failed ({len(errors)} cleanup errors)",
                phase="close",
                cleanup_errors=tuple(errors),
            ) from errors[0]
        self._closed = True

    def _release_allocations(self) -> None:
        errors: list[BaseException] = []
        for allocation in (self.rcon_allocation, self.allocation):
            if allocation is None:
                continue
            if allocation.allocation_id in self._released_allocation_ids:
                continue
            try:
                self._endpoint_allocations.release(allocation.allocation_id)
                self._released_allocation_ids.add(allocation.allocation_id)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise MinecraftBranchRuntimeError(
                "branch endpoint allocation cleanup failed",
                phase="close",
                cleanup_errors=tuple(errors),
            ) from errors[0]


class MinecraftBranchRuntimeFactory(MinecraftBranchRuntimeFactoryPort):
    """Environment-owned branch binder over explicit resource/service seams."""

    def __init__(
        self,
        *,
        endpoint_allocations: _EndpointGenerationAllocationPort,
        environment_factory: MinecraftBranchEnvironmentFactoryPort,
        server_factory: MinecraftBranchServerFactoryPort,
        checkpoint_factory: MinecraftBranchCheckpointFactoryPort | None = None,
        lease_guard_factory: EndpointLeaseGuardFactoryPort,
        action_recovery_root: str | None = None,
    ) -> None:
        self._endpoint_allocations = endpoint_allocations
        self._environment_factory = environment_factory
        self._server_factory = server_factory
        self._checkpoint_factory = checkpoint_factory
        self._lease_guard_factory = lease_guard_factory
        self._action_recovery_root = action_recovery_root

    def open(self, request: MinecraftBranchRuntimeRequest) -> MinecraftBranchRuntimeBinding:
        allocation = self._endpoint_allocations.allocate(request.endpoint_allocation)
        rcon_allocation: EndpointAllocation | None = None
        lease_guard: EndpointLeaseGuardPort | None = None
        try:
            if request.rcon_endpoint_allocation is not None:
                rcon_allocation = self._endpoint_allocations.allocate(request.rcon_endpoint_allocation)
            allocation_ids = tuple(
                row.allocation_id
                for row in (allocation, rcon_allocation)
                if row is not None
            )
            lease_guard = self._lease_guard_factory.create(allocation_ids)
            lease_guard.start()
            endpoint = allocation.endpoint
            bridge_spec = request.environment_template.bridge
            if bridge_spec.action_recovery_root is None and self._action_recovery_root is not None:
                bridge_spec = replace(
                    bridge_spec, action_recovery_root=self._action_recovery_root
                )
            environment_spec = replace(
                request.environment_template,
                endpoint=replace(
                    request.environment_template.endpoint,
                    host=endpoint.host,
                    port=endpoint.port,
                ),
                bridge=bridge_spec,
            )
            server_spec = replace(
                request.server_template,
                host=endpoint.host,
                port=endpoint.port,
                workdir=request.branch.workdir,
                level_name=request.branch.level_name,
            )
            if rcon_allocation is not None:
                assert server_spec.rcon_endpoint is not None
                server_spec = replace(
                    server_spec,
                    rcon_endpoint=replace(
                        server_spec.rcon_endpoint,
                        host=rcon_allocation.endpoint.host,
                        port=rcon_allocation.endpoint.port,
                    ),
                )
            environment_generation = environment_spec.scientific_identity_digest()
            endpoint_binding = _MinecraftEndpointBindingAuthority(
                allocation=allocation,
                rcon_allocation=rcon_allocation,
                endpoint_allocations=self._endpoint_allocations,
                environment_generation=environment_generation,
            )
            server = self._server_factory.create(
                server_spec,
                environment_generation=environment_generation,
            )
            checkpoint = None
            if self._checkpoint_factory is not None:
                checkpoint = self._checkpoint_factory.create(
                    server=server,
                    server_spec=server_spec,
                    environment_generation=environment_generation,
                    endpoint_binding=endpoint_binding,
                )
                environment = self._environment_factory.compose(
                    environment_spec,
                    checkpoint=checkpoint,
                )
            else:
                # Preserve compatibility with environment factories that predate
                # the optional checkpoint keyword when no provider is composed.
                environment = self._environment_factory.compose(environment_spec)
            if environment.implementation.identity.artifact_digest != environment_generation:
                raise MinecraftBranchRuntimeError(
                    "branch environment generation drifted during composition",
                    phase="compose",
                )
            return MinecraftBranchRuntimeBinding(
                endpoint_binding=endpoint_binding,
                implementation=environment.implementation,
                environment_runtime=environment.runtime,
                server=server,
                session_id=request.session_id,
                endpoint_allocations=self._endpoint_allocations,
                lease_guard=lease_guard,
            )
        except BaseException as exc:
            cleanup_errors: list[BaseException] = []
            if lease_guard is not None:
                try:
                    lease_guard.close()
                except BaseException as cleanup_exc:
                    cleanup_errors.append(cleanup_exc)
            for current in (rcon_allocation, allocation):
                if current is None:
                    continue
                try:
                    self._endpoint_allocations.release(current.allocation_id)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                raise MinecraftBranchRuntimeError(
                    "branch runtime composition failed and allocation cleanup failed",
                    phase="compose",
                    cause=exc,
                    cleanup_errors=tuple(cleanup_errors),
                ) from exc
            raise


__all__ = [
    "MinecraftBranchEnvironmentFactoryPort",
    "MinecraftBranchCheckpointFactoryPort",
    "MinecraftBranchRuntimeBinding",
    "MinecraftBranchRuntimeError",
    "MinecraftBranchRuntimeFactory",
]
