from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from noetrium_platform.capabilities.environment.runtime.api import (
    ActionReconciliationResult,
    ActionRequest,
    ActionResult,
    EnvironmentCapability,
    EnvironmentCapabilityDescriptor,
    EnvironmentCapabilityUnsupported,
    EnvironmentDiagnosticsPort,
    EnvironmentIdentity,
    EnvironmentImplementation,
    EnvironmentProviderCapabilities,
    EnvironmentQuery,
    EnvironmentQueryResult,
    EnvironmentSession,
    EnvironmentSessionDiagnostics,
    Observation,
)
from noetrium_platform.foundation.kernel.kernel import (
    EffectReceipt,
    ExecutionContext,
    JsonValue,
    canonical_digest,
)
from noetrium_platform.infrastructure.reliability.effect.api import PreparedEffectHandle

from ..api import (
    MinecraftEnvironmentSpec,
    MinecraftObservationEvent,
    MinecraftSessionRuntimeIdentity,
    minecraft_action_catalog,
)
from ..api.ports import (
    MinecraftBridgePort,
    MinecraftCheckpointPort,
    MinecraftDiagnosticsPort,
    MinecraftSessionServices,
)
from .state import MinecraftStateProjection
from .checkpoint import (
    MinecraftCheckpointCoordinator,
    MinecraftCheckpointCodec,
    MinecraftSessionCheckpointPort,
)
from .session_diagnostics import MinecraftSessionDiagnosticRecorder, safe_exception_message
from .action_coordinator import (
    MinecraftActionCoordinator,
    MinecraftActionCoordinatorBindings,
)
from .errors import MinecraftCheckpointUnavailable, MinecraftEnvironmentFailure
from .event_views import minecraft_events_payload


class MinecraftBridgeFactory(Protocol):
    def __call__(self, spec: MinecraftEnvironmentSpec) -> MinecraftBridgePort: ...


@dataclass(frozen=True, slots=True)
class MinecraftEnvironmentImplementation(EnvironmentImplementation):
    """Scientific-independent MC implementation identity and provider selection."""

    spec: MinecraftEnvironmentSpec
    bridge_factory: MinecraftBridgeFactory
    checkpoint: MinecraftCheckpointPort | None = None
    checkpoint_coordinator: MinecraftSessionCheckpointPort | None = None

    @property
    def identity(self) -> EnvironmentIdentity:
        return EnvironmentIdentity(
            environment_id="minecraft",
            implementation_version=self.spec.implementation_version,
            abi_version=self.spec.abi_version,
            schema_version=self.spec.schema_version,
            artifact_digest=self.spec.scientific_identity_digest(),
        )


class MinecraftEnvironmentSession(EnvironmentSession):
    """MC session over the bridge seam and an optional authoritative world checkpoint."""

    _CHECKPOINT_SCHEMA = MinecraftCheckpointCodec.SCHEMA

    def __init__(
        self,
        *,
        session_id: str,
        implementation: MinecraftEnvironmentImplementation,
        bridge: MinecraftBridgePort,
        diagnostics: MinecraftDiagnosticsPort | None = None,
        checkpoint_coordinator: MinecraftSessionCheckpointPort | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("Minecraft session_id must be non-empty")
        self.session_id = session_id
        self.implementation = implementation
        self.identity = implementation.identity
        self._provider_instance_id = f"{self.identity.environment_id}:{session_id}"
        self._bridge = bridge
        self._checkpoint_coordinator = checkpoint_coordinator or MinecraftCheckpointCoordinator()
        self._diagnostic_recorder = MinecraftSessionDiagnosticRecorder(
            session_id=session_id,
            sink=diagnostics,
        )
        self._closed = False
        self._observation_sequence = 0
        self._restore_faulted = False
        self._last_observation: Observation | None = None
        self._state = MinecraftStateProjection(max_entities=implementation.spec.max_entities)
        self._actions = MinecraftActionCoordinator(
            session_id=session_id,
            generation=self.generation,
            provider_instance_id=self._provider_instance_id,
            spec=implementation.spec,
            bridge=bridge,
            bindings=MinecraftActionCoordinatorBindings(
                event_log=self._event_log,
                failure_log=self._failure_log,
                ingest_events=self._ingest_events,
                observation=self._observation,
                state_payload=self._state_payload,
                last_observation=lambda: self._last_observation,
            ),
        )
        self._event_log("lifecycle", "MC_SESSION_START", level="INFO", attributes={"session_id": session_id})
        try:
            self._bridge.configure_action_recovery(self._provider_instance_id)
            self._bridge.start()
        except Exception as exc:
            self._failure_log("start", exc)
            raise MinecraftEnvironmentFailure(
                "start",
                safe_exception_message(exc),
                cause_code=str(getattr(exc, "cause_code", "MINECRAFT_BRIDGE_START_FAILED")),
            ) from exc

    @property
    def generation(self) -> str:
        return self.identity.artifact_digest

    @property
    def action_recovery_durability(self) -> str:
        durability = str(getattr(self._bridge, "action_recovery_durability", "process_local"))
        return "crash_durable" if durability == "crash_durable" else "process_local"

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Minecraft environment session is closed")
        if self._restore_faulted:
            raise RuntimeError("Minecraft environment session is unusable after restore failure")

    def _event_log(
        self,
        phase: str,
        event: str,
        *,
        level: str = "DEBUG",
        attributes: Mapping[str, JsonValue] | None = None,
        correlation_refs: tuple[str, ...] = (),
    ) -> None:
        self._diagnostic_recorder.event(
            phase,
            event,
            level=level,
            attributes=attributes,
            correlation_refs=correlation_refs,
        )

    def _failure_log(self, phase: str, exc: BaseException, *, code: str | None = None) -> None:
        self._diagnostic_recorder.failure(phase, exc, code=code)

    def _ingest_events(
        self,
        events: tuple[MinecraftObservationEvent, ...],
        *,
        phase: str,
        refresh_entities: bool = False,
    ) -> None:
        try:
            if refresh_entities:
                self._state.replace_entities()
            for event in events:
                self._state.ingest(event)
        except Exception as exc:
            self._failure_log(f"{phase}.state", exc, code="MINECRAFT_STATE_PROJECTION_FAILED")
            raise MinecraftEnvironmentFailure(
                f"{phase}.state",
                safe_exception_message(exc),
                cause_code="MINECRAFT_STATE_PROJECTION_FAILED",
            ) from exc

    def _state_payload(self) -> dict[str, JsonValue]:
        return {
            "state": self._state.compact(),
            "state_digest": self._state.snapshot_digest(),
        }

    def _observation(
        self,
        *,
        payload: Mapping[str, JsonValue],
        artifact_refs: tuple[str, ...] = (),
    ) -> Observation:
        self._observation_sequence += 1
        observation = Observation(
            observation_id=f"minecraft:{self.session_id}:observation:{self._observation_sequence}",
            generation=self.generation,
            payload=dict(payload),
            artifact_refs=artifact_refs,
        )
        self._last_observation = observation
        return observation

    def observe(self, context: ExecutionContext) -> Observation:
        self._assert_open()
        self._event_log("observe", "MC_OBSERVE_START", attributes={"task_id": context.task_id})
        try:
            snapshot = self._bridge.command(
                "snapshot",
                {"context": {"run_id": context.run_id, "task_id": context.task_id}},
                timeout_s=self.implementation.spec.bridge.command_timeout_s,
            )
            if self._bridge.supports_command("observe_entities"):
                entities = self._bridge.command(
                    "observe_entities",
                    {"max_distance": 32, "limit": self.implementation.spec.max_entities},
                    timeout_s=self.implementation.spec.bridge.command_timeout_s,
                )
            else:
                error = MinecraftEnvironmentFailure(
                    "observe.entities",
                    "bridge does not declare observe_entities",
                    cause_code="MINECRAFT_ENTITY_OBSERVATION_UNAVAILABLE",
                )
                self._failure_log("observe.entities", error)
                raise error
        except Exception as exc:
            self._failure_log("observe", exc)
            raise MinecraftEnvironmentFailure(
                "observe",
                safe_exception_message(exc),
                cause_code=str(getattr(exc, "cause_code", "MINECRAFT_OBSERVE_FAILED")),
            ) from exc
        events = snapshot.events + entities.events
        if not any(event.kind in {"self_snapshot", "spawn_snapshot"} for event in events):
            error = MinecraftEnvironmentFailure(
                "observe",
                "bridge returned no self snapshot",
                cause_code="MINECRAFT_EMPTY_OBSERVATION",
            )
            self._failure_log("observe", error)
            raise error
        self._ingest_events(snapshot.events, phase="observe")
        self._ingest_events(entities.events, phase="observe.entities", refresh_entities=True)
        self._event_log("observe", "MC_OBSERVE_END", attributes={"event_count": len(events)})
        return self._observation(
            payload={
                "kind": "minecraft_snapshot",
                "events": minecraft_events_payload(events),
                "bridge_diagnostics": {
                    "snapshot": dict(snapshot.diagnostics),
                    "entities": dict(entities.diagnostics),
                },
                **self._state_payload(),
            }
        )

    def _task_event(self, status: str, context: ExecutionContext) -> Observation:
        payload = {
            "task_id": str(context.task_id or ""),
            "status": status,
            "context": {
                "run_id": context.run_id,
                "study_id": context.study_id,
                "task_id": context.task_id,
            },
        }
        result = self._bridge.command(
            "task_event",
            payload,
            timeout_s=self.implementation.spec.bridge.command_timeout_s,
        )
        self._ingest_events(result.events, phase="task_event")
        return self._observation(
            payload={
                "kind": "minecraft_task_event",
                "events": minecraft_events_payload(result.events),
                "bridge_diagnostics": dict(result.diagnostics),
                **self._state_payload(),
            }
        )

    def begin_task(self, metadata: Mapping[str, JsonValue], context: ExecutionContext) -> Observation:
        self._assert_open()
        del metadata
        return self._task_event("STARTED", context)

    def end_task(self, metadata: Mapping[str, JsonValue], context: ExecutionContext) -> Observation:
        self._assert_open()
        status = str(metadata.get("status") or "ENDED")
        return self._task_event(status, context)

    def act(self, request: ActionRequest) -> ActionResult:
        self._assert_open()
        return self._actions.act(request)

    def prepare_action_recovery(
        self, request: ActionRequest, context: ExecutionContext
    ) -> PreparedEffectHandle:
        self._assert_open()
        return self._actions.prepare_action_recovery(request, context)

    def execute_prepared_action(
        self, request: ActionRequest, handle: PreparedEffectHandle
    ) -> ActionResult:
        self._assert_open()
        return self._actions.execute_prepared_action(request, handle)

    def reconcile_prepared_action(
        self, handle: PreparedEffectHandle, context: ExecutionContext
    ) -> ActionReconciliationResult:
        self._assert_open()
        return self._actions.reconcile_prepared_action(handle, context)

    def reconcile(self, effect: EffectReceipt, context: ExecutionContext) -> EffectReceipt:
        self._assert_open()
        return self._actions.reconcile(effect, context)

    def query(self, request: EnvironmentQuery) -> EnvironmentQueryResult:
        self._assert_open()
        if request.query_type == "capabilities":
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {
                    "environment": "minecraft",
                    "generation": self.generation,
                    "capabilities": [
                        {
                            "capability_id": descriptor.capability_id,
                            "version": descriptor.version,
                            "action_types": list(descriptor.action_types),
                            "query_types": list(descriptor.query_types),
                            "metadata": descriptor.metadata,
                        }
                        for descriptor in self.capability_descriptors()
                    ],
                },
                self._last_observation,
            )
        if request.query_type == "state":
            return EnvironmentQueryResult(
                request.query_id,
                True,
                self._state_payload(),
                self._last_observation,
            )
        if request.query_type == "entity":
            query = str(request.payload.get("name", "")).lower() if isinstance(request.payload, Mapping) else ""
            entities = [
                value.compact()
                for value in self._state.entities.values()
                if not query or query in str(value.compact()).lower()
            ]
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {"entities": entities, "count": len(entities)},
                self._last_observation,
            )
        if request.query_type == "task":
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {
                    "task_id": request.context.task_id,
                    "state_digest": self._state.snapshot_digest(),
                },
                self._last_observation,
            )
        raise EnvironmentCapabilityUnsupported(f"query:{request.query_type}")

    def capability_descriptors(self) -> tuple[EnvironmentCapabilityDescriptor, ...]:
        return (
            EnvironmentCapabilityDescriptor(
                capability_id="minecraft.world",
                version=self.implementation.spec.schema_version,
                action_types=tuple(contract.action_type for contract in minecraft_action_catalog()),
                query_types=("capabilities", "state", "entity", "task"),
                metadata={
                    "bridge": "replaceable",
                    "max_entities": self.implementation.spec.max_entities,
                    "state_digest": self._state.snapshot_digest(),
                },
            ),
        )

    def checkpoint(self) -> bytes:
        self._assert_open()
        provider = self.implementation.checkpoint
        if provider is None:
            self._event_log("checkpoint", "MC_CHECKPOINT_UNAVAILABLE", level="WARNING")
            raise MinecraftCheckpointUnavailable(
                "Minecraft session has no authoritative world checkpoint provider"
            )
        try:
            payload, world_bytes = self._checkpoint_coordinator.capture(
                provider=provider,
                session_id=self.session_id,
                generation=self.generation,
                observation_sequence=self._observation_sequence,
                actions=self._actions.snapshot(),
                state=self._state,
                last_observation=self._last_observation,
            )
        except Exception as exc:
            self._failure_log("checkpoint", exc, code="MINECRAFT_CHECKPOINT_CAPTURE_FAILED")
            raise
        self._event_log(
            "checkpoint",
            "MC_CHECKPOINT_CAPTURED",
            level="INFO",
            attributes={"bytes": len(payload), "world_bytes": world_bytes},
        )
        return payload

    def restore(self, payload: bytes) -> None:
        self._assert_open()
        provider = self.implementation.checkpoint
        if provider is None:
            self._event_log("restore", "MC_RESTORE_UNAVAILABLE", level="WARNING")
            raise MinecraftCheckpointUnavailable(
                "Minecraft session has no authoritative world checkpoint provider"
            )
        try:
            restored = self._checkpoint_coordinator.decode(
                payload,
                session_id=self.session_id,
                generation=self.generation,
                max_entities=self.implementation.spec.max_entities,
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, UnicodeError) as exc:
            self._failure_log("restore.decode", exc, code="MINECRAFT_CHECKPOINT_INVALID")
            raise MinecraftEnvironmentFailure(
                "restore.decode",
                safe_exception_message(exc),
                cause_code="MINECRAFT_CHECKPOINT_INVALID",
            ) from exc

        bridge_stopped = False
        try:
            self._bridge.close()
            bridge_stopped = True
            provider.restore(restored.world_payload, session_id=self.session_id, context=None)
            self._bridge.start()
            bridge_stopped = False
        except Exception as exc:
            self._restore_faulted = True
            recovery_error: BaseException | None = None
            if bridge_stopped:
                try:
                    self._bridge.start()
                except BaseException as recovery_exc:
                    recovery_error = recovery_exc
            detail = safe_exception_message(exc)
            if recovery_error is not None:
                detail += (
                    "; bridge recovery failed: "
                    f"{safe_exception_message(recovery_error)}"
                )
            self._failure_log("restore", exc, code="MINECRAFT_CHECKPOINT_RESTORE_FAILED")
            raise MinecraftEnvironmentFailure(
                "restore",
                detail,
                cause_code="MINECRAFT_CHECKPOINT_RESTORE_FAILED",
                diagnostics={
                    "bridge_recovery_failed": recovery_error is not None,
                },
            ) from exc
        self._state = restored.state
        self._observation_sequence = restored.observation_sequence
        self._actions.replace(restored.actions)
        self._last_observation = restored.last_observation
        self._event_log(
            "restore",
            "MC_CHECKPOINT_RESTORED",
            level="INFO",
            attributes={"bytes": len(payload), "world_bytes": len(restored.world_payload)},
        )

    def diagnostics_snapshot(self) -> EnvironmentSessionDiagnostics:
        supported = [
            EnvironmentCapability.DIAGNOSTICS,
            EnvironmentCapability.RECONCILE,
            EnvironmentCapability.QUERY,
        ]
        if self.implementation.checkpoint is not None:
            supported.extend((EnvironmentCapability.SNAPSHOT, EnvironmentCapability.RESTORE))
        return EnvironmentSessionDiagnostics(
            session_id=self.session_id,
            environment=self.identity,
            generation=self.generation,
            ready=not self._closed and not self._restore_faulted,
            closed=self._closed,
            capabilities=EnvironmentProviderCapabilities(tuple(supported)),
            state_digest=self._state.snapshot_digest(),
        )

    def diagnostics(self) -> dict[str, object]:
        return {
            "environment": "minecraft",
            "session_id": self.session_id,
            "generation": self.generation,
            "closed": self._closed,
            "observation_sequence": self._observation_sequence,
            "known_action_ids": len(self._actions),
            "diagnostic_sink_failures": self._diagnostic_recorder.sink_failures,
            "restore_faulted": self._restore_faulted,
            "checkpoint_provider": self.implementation.checkpoint is not None,
            "state_digest": self._state.snapshot_digest(),
            "state_last_event_sequence": self._state.last_event_sequence,
            "state_entity_count": len(self._state.entities),
        }

    def close(self) -> None:
        if self._closed:
            return
        self._event_log("lifecycle", "MC_SESSION_CLOSE", level="INFO")
        try:
            self._bridge.close()
        except Exception as exc:
            self._failure_log("close", exc, code="MINECRAFT_BRIDGE_CLOSE_FAILED")
            raise MinecraftEnvironmentFailure(
                "close",
                safe_exception_message(exc),
                cause_code="MINECRAFT_BRIDGE_CLOSE_FAILED",
            ) from exc
        self._closed = True


class MinecraftEnvironmentRuntime:
    """Session lifecycle owner; it does not own MC semantics or server lifecycle."""

    RUNTIME_ID = "minecraft.environment.session"
    RUNTIME_VERSION = "2"
    RUNTIME_ABI_VERSION = "1"

    def __init__(
        self,
        bridge_factory: MinecraftBridgeFactory,
        *,
        diagnostics: MinecraftDiagnosticsPort | None = None,
    ) -> None:
        self._bridge_factory = bridge_factory
        self._diagnostics = diagnostics
        self._runtime_identity = MinecraftSessionRuntimeIdentity(
            self.RUNTIME_ID,
            self.RUNTIME_VERSION,
            self.RUNTIME_ABI_VERSION,
            canonical_digest(
                {
                    "runtime_id": self.RUNTIME_ID,
                    "runtime_version": self.RUNTIME_VERSION,
                    "runtime_abi_version": self.RUNTIME_ABI_VERSION,
                    "session_contract": MinecraftEnvironmentSession._CHECKPOINT_SCHEMA,
                }
            ),
        )

    @property
    def runtime_identity(self) -> MinecraftSessionRuntimeIdentity:
        return self._runtime_identity

    def open_session(
        self,
        implementation: MinecraftEnvironmentImplementation,
        *,
        session_id: str,
        services: MinecraftSessionServices,
    ) -> MinecraftEnvironmentSession:
        del services
        if not isinstance(implementation, MinecraftEnvironmentImplementation):
            raise TypeError(
                "MinecraftEnvironmentRuntime requires "
                "MinecraftEnvironmentImplementation"
            )
        bridge = self._bridge_factory(implementation.spec)
        return MinecraftEnvironmentSession(
            session_id=session_id,
            implementation=implementation,
            bridge=bridge,
            diagnostics=self._diagnostics,
            checkpoint_coordinator=implementation.checkpoint_coordinator,
        )


__all__ = [
    "MinecraftBridgeFactory",
    "MinecraftCheckpointUnavailable",
    "MinecraftEnvironmentFailure",
    "MinecraftEnvironmentImplementation",
    "MinecraftEnvironmentRuntime",
    "MinecraftEnvironmentSession",
]
