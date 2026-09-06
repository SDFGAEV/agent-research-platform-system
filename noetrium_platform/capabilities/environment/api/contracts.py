from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from noetrium_platform.foundation.kernel.kernel import (
    ExecutionContext,
    JsonInput,
    JsonValue,
    SystemIdentity,
    SystemPort,
    SystemSpec,
    canonical_digest,
)
from noetrium_platform.foundation.kernel.kernel.operation import EffectReceipt
from noetrium_platform.infrastructure.reliability.effect.api import PreparedEffectHandle


@dataclass(frozen=True, slots=True)
class EnvironmentIdentity:
    environment_id: str
    implementation_version: str
    abi_version: str
    schema_version: str
    artifact_digest: str = ""


@dataclass(frozen=True, slots=True)
class EnvironmentAssignmentIdentity:
    """Provider-neutral identity for one isolated scientific assignment."""

    assignment_id: str
    study_id: str
    plan_digest: str
    variant_id: str
    repetition: int
    seed: str
    environment_id: str

    def __post_init__(self) -> None:
        if (
            not self.assignment_id.strip()
            or not self.study_id.strip()
            or len(self.plan_digest) != 64
            or not self.variant_id.strip()
            or self.repetition < 0
            or not self.seed.strip()
            or not self.environment_id.strip()
        ):
            raise ValueError("environment assignment identity is invalid")

    @property
    def digest(self) -> str:
        return canonical_digest({
            "assignment_id": self.assignment_id,
            "study_id": self.study_id,
            "plan_digest": self.plan_digest,
            "variant_id": self.variant_id,
            "repetition": self.repetition,
            "seed": self.seed,
            "environment_id": self.environment_id,
        })


@dataclass(frozen=True, slots=True)
class EnvironmentAssignmentIsolationReceipt:
    """Lifecycle receipt; it does not claim scientific task success."""

    assignment_id: str
    isolation_id: str
    state: str
    durability: str
    environment_state_digest: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.assignment_id.strip()
            or not self.isolation_id.strip()
            or self.state not in {"prepared", "finalized", "failed"}
            or self.durability not in {"crash_durable", "process_local", "unknown"}
            or len(self.environment_state_digest) != 64
        ):
            raise ValueError("environment assignment isolation receipt is invalid")


@runtime_checkable
class EnvironmentAssignmentIsolationPort(Protocol):
    """Generic lifecycle seam for world/session isolation across environments."""

    def prepare_assignment(
        self, identity: EnvironmentAssignmentIdentity
    ) -> EnvironmentAssignmentIsolationReceipt: ...

    def finalize_assignment(
        self,
        identity: EnvironmentAssignmentIdentity,
        receipt: EnvironmentAssignmentIsolationReceipt,
    ) -> EnvironmentAssignmentIsolationReceipt: ...


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    generation: str
    payload: JsonInput
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_id: str
    action_type: str
    payload: JsonInput
    context: ExecutionContext


def action_request_digest(request: ActionRequest) -> str:
    """Stable scientific action identity; excludes tracing/span-only fields."""

    context = request.context
    return canonical_digest(
        {
            "action_id": request.action_id,
            "action_type": request.action_type,
            "payload": request.payload,
            "run_id": context.run_id,
            "study_id": context.study_id,
            "lifetime_id": context.lifetime_id,
            "task_id": context.task_id,
            "decision_cycle_id": context.decision_cycle_id,
            "checkpoint_id": context.checkpoint_id,
            "source_generation": context.generation("environment"),
        }
    )


@dataclass(frozen=True, slots=True)
class ActionResult:
    action_id: str
    accepted: bool
    observation: Observation | None
    effect: EffectReceipt | None
    diagnostics: dict[str, JsonValue]


class ActionReconciliationDisposition(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ActionReconciliationResult:
    action_id: str
    disposition: ActionReconciliationDisposition
    result: ActionResult | None
    diagnostics: dict[str, JsonValue]


@runtime_checkable
class DurablePreparedActionSession(Protocol):
    action_recovery_durability: str

    def prepare_action_recovery(
        self, request: ActionRequest, context: ExecutionContext
    ) -> PreparedEffectHandle: ...

    def execute_prepared_action(
        self, request: ActionRequest, handle: PreparedEffectHandle
    ) -> ActionResult: ...

    def reconcile_prepared_action(
        self, handle: PreparedEffectHandle, context: ExecutionContext
    ) -> ActionReconciliationResult: ...


@runtime_checkable
class EnvironmentSession(Protocol):
    def observe(self, context: ExecutionContext) -> Observation: ...
    def act(self, request: ActionRequest) -> ActionResult: ...
    def reconcile(self, effect: EffectReceipt, context: ExecutionContext) -> EffectReceipt: ...
    def checkpoint(self) -> bytes: ...
    def restore(self, payload: bytes) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class EnvironmentImplementation(Protocol):
    @property
    def identity(self) -> EnvironmentIdentity: ...
