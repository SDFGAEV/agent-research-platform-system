from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from noetrium_platform.capabilities.environment.api.provider import (
    EnvironmentProviderCapabilities,
    EnvironmentSessionDiagnostics,
)

from noetrium_platform.foundation.kernel.kernel import (
    EffectCertainty,
    EffectClass,
    EffectReceipt,
    ExecutionContext,
    JsonValue,
    canonical_digest,
)

from ..api import (
    ActionIdentityViolation,
    ActionRequest,
    ActionResult,
    EnvironmentCapability,
    EnvironmentCapabilityDescriptor,
    EnvironmentCapabilityUnsupported,
    EnvironmentIdentity,
    EnvironmentImplementation,
    EnvironmentProviderCapabilities,
    EnvironmentQuery,
    EnvironmentQueryResult,
    EnvironmentSession,
    EnvironmentSessionDiagnostics,
    Observation,
    StateMachineDynamicsPort,
    StateMachineEnvironmentSpec,
    StateTransition,
    action_request_digest,
    freeze_json_mapping,
    thaw_json_mapping,
)

from .state_machine_checkpoint import (
    AppliedStateMachineAction,
    StateMachineCheckpointCodec,
    StateMachineCheckpointError,
)



@dataclass(frozen=True, slots=True)
class StateMachineEnvironmentImplementation(EnvironmentImplementation):
    spec: StateMachineEnvironmentSpec
    dynamics: StateMachineDynamicsPort

    def __post_init__(self) -> None:
        if self.dynamics.identity != self.spec.dynamics:
            raise ValueError("state-machine dynamics identity does not match the environment spec")

    @property
    def identity(self) -> EnvironmentIdentity:
        return EnvironmentIdentity(
            environment_id=self.spec.environment_id,
            implementation_version=self.spec.implementation_version,
            abi_version=self.spec.abi_version,
            schema_version=self.spec.schema_version,
            artifact_digest=self.spec.scientific_identity_digest(),
        )


class StateMachineEnvironmentSession(EnvironmentSession):
    """Exact, checkpointable session for deterministic non-open-world domains."""

    _CHECKPOINT_SCHEMA = StateMachineCheckpointCodec.SCHEMA

    def __init__(
        self,
        *,
        session_id: str,
        implementation: StateMachineEnvironmentImplementation,
    ) -> None:
        if not session_id.strip():
            raise ValueError("state-machine session_id must be non-empty")
        self.session_id = session_id
        self.implementation = implementation
        self.identity = implementation.identity
        self._provider_instance_id = f"{self.identity.environment_id}:{session_id}"
        self._checkpoint_codec = StateMachineCheckpointCodec(
            session_id=session_id,
            environment_generation=self.generation,
            provider_instance_id=self._provider_instance_id,
        )
        self._state = freeze_json_mapping(
            implementation.spec.initial_state,
            field="initial_state",
        )
        self._observation_sequence = 0
        self._actions: dict[str, AppliedStateMachineAction] = {}
        self._closed = False

    @property
    def generation(self) -> str:
        return self.identity.artifact_digest

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("state-machine environment session is closed")

    def _observation(
        self,
        *,
        kind: str,
        extra: Mapping[str, JsonValue] | None = None,
        artifact_refs: tuple[str, ...] = (),
    ) -> Observation:
        self._observation_sequence += 1
        state = thaw_json_mapping(self._state)
        return Observation(
            observation_id=(
                f"state-machine:{self.session_id}:observation:{self._observation_sequence}"
            ),
            generation=self.generation,
            payload={
                "kind": kind,
                "state": state,
                "state_digest": canonical_digest(state),
                **dict(extra or {}),
            },
            artifact_refs=artifact_refs,
        )

    def observe(self, context: ExecutionContext) -> Observation:
        del context
        self._assert_open()
        return self._observation(kind="state_machine_snapshot")

    def query(self, request: EnvironmentQuery) -> EnvironmentQueryResult:
        self._assert_open()
        if request.query_type == "state":
            state = thaw_json_mapping(self._state)
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {
                    "state": state,
                    "state_digest": canonical_digest(state),
                    "generation": self.generation,
                },
                self._observation(kind="state_machine_query"),
            )
        if request.query_type == "capabilities":
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {
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
                self._observation(kind="state_machine_capabilities"),
            )
        raise EnvironmentCapabilityUnsupported(f"query:{request.query_type}")

    def capability_descriptors(self) -> tuple[EnvironmentCapabilityDescriptor, ...]:
        return (
            EnvironmentCapabilityDescriptor(
                "environment.state_machine",
                self.implementation.spec.schema_version,
                action_types=self.implementation.spec.action_types,
                query_types=("state", "capabilities"),
                metadata={"deterministic": True},
            ),
        )

    def act(self, request: ActionRequest) -> ActionResult:
        self._assert_open()
        if request.action_type not in self.implementation.spec.action_types:
            raise ValueError(
                f"unsupported state-machine action type: {request.action_type}"
            )
        request_payload = thaw_json_mapping(
            freeze_json_mapping(request.payload, field="request.payload")
        )
        digest = action_request_digest(request)
        prior = self._actions.get(request.action_id)
        if prior is not None:
            if prior.request_digest != digest:
                raise ActionIdentityViolation(
                    f"state-machine action identity was reused with drift: {request.action_id}"
                )
            return prior.result

        transition = self.implementation.dynamics.transition(
            self._state,
            request,
            request.context,
        )
        if not isinstance(transition, StateTransition):
            raise TypeError("state-machine dynamics returned an invalid transition")
        next_state = freeze_json_mapping(transition.state, field="transition.state")
        if not transition.accepted and canonical_digest(next_state) != canonical_digest(self._state):
            raise ValueError("a rejected state-machine transition cannot mutate state")
        self._state = next_state
        certainty = (
            EffectCertainty.EFFECT_CONFIRMED
            if transition.accepted
            else EffectCertainty.EFFECT_REJECTED
        )
        effect = EffectReceipt(
            effect_id=f"state-machine-action:{request.action_id}",
            request_digest=digest,
            effect_class=EffectClass.IDEMPOTENT,
            certainty=certainty,
            provider_instance_id=self._provider_instance_id,
            verification_required=False,
            after_artifact=canonical_digest(self._state),
            provider_receipt=request.action_id,
        )
        observation = self._observation(
            kind="state_machine_transition",
            extra={
                "action": {
                    "action_id": request.action_id,
                    "action_type": request.action_type,
                    "payload": request_payload,
                },
                "accepted": transition.accepted,
                "transition_diagnostics": thaw_json_mapping(transition.diagnostics),
            },
            artifact_refs=transition.artifact_refs,
        )
        result = ActionResult(
            action_id=request.action_id,
            accepted=transition.accepted,
            observation=observation,
            effect=effect,
            diagnostics={
                "environment": "state_machine",
                "verified": True,
                "state_digest": effect.after_artifact,
                **thaw_json_mapping(transition.diagnostics),
            },
        )
        self._actions[request.action_id] = AppliedStateMachineAction(digest, result)
        return result

    def reconcile(self, effect: EffectReceipt, context: ExecutionContext) -> EffectReceipt:
        del context
        self._assert_open()
        action_id = effect.provider_receipt
        if not action_id or action_id not in self._actions:
            raise ActionIdentityViolation(
                "state-machine effect does not identify an applied session action"
            )
        applied = self._actions[action_id]
        if applied.request_digest != effect.request_digest:
            raise ActionIdentityViolation(
                "state-machine effect request digest does not match the action ledger"
            )
        authoritative = applied.result.effect
        if authoritative is None:
            raise RuntimeError("state-machine action ledger has no effect receipt")
        return replace(authoritative, verification_required=False)

    def checkpoint(self) -> bytes:
        self._assert_open()
        return self._checkpoint_codec.encode(
            state=self._state,
            observation_sequence=self._observation_sequence,
            actions=self._actions,
        )

    def restore(self, payload: bytes) -> None:
        self._assert_open()
        decoded = self._checkpoint_codec.decode(payload)
        self._state = decoded.state
        self._observation_sequence = decoded.observation_sequence
        self._actions = dict(decoded.actions)

    def diagnostics_snapshot(self) -> EnvironmentSessionDiagnostics:
        return EnvironmentSessionDiagnostics(
            session_id=self.session_id,
            environment=self.identity,
            generation=self.generation,
            ready=not self._closed,
            closed=self._closed,
            capabilities=EnvironmentProviderCapabilities((
                EnvironmentCapability.SNAPSHOT,
                EnvironmentCapability.RESTORE,
                EnvironmentCapability.RECONCILE,
                EnvironmentCapability.DIAGNOSTICS,
                EnvironmentCapability.QUERY,
            )),
            state_digest=canonical_digest(self._state),
        )

    def diagnostics(self) -> dict[str, object]:
        snapshot = self.diagnostics_snapshot()
        return {
            "environment": "state_machine",
            "session_id": snapshot.session_id,
            "generation": snapshot.generation,
            "closed": snapshot.closed,
            "state_digest": snapshot.state_digest,
            "observation_sequence": self._observation_sequence,
            "known_action_ids": len(self._actions),
        }

    def close(self) -> None:
        self._closed = True


class StateMachineEnvironmentRuntime:
    """Lifecycle owner for injected closed-world dynamics."""

    def open_session(
        self,
        implementation: object,
        *,
        session_id: str,
        services: object,
    ) -> StateMachineEnvironmentSession:
        del services
        if not isinstance(implementation, StateMachineEnvironmentImplementation):
            raise TypeError(
                "StateMachineEnvironmentRuntime requires StateMachineEnvironmentImplementation"
            )
        return StateMachineEnvironmentSession(
            session_id=session_id,
            implementation=implementation,
        )


__all__ = [
    "StateMachineCheckpointError",
    "StateMachineEnvironmentImplementation",
    "StateMachineEnvironmentRuntime",
    "StateMachineEnvironmentSession",
]
