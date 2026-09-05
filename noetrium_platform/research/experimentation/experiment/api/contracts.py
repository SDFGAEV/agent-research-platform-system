from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Protocol

from noetrium_platform.foundation.kernel.kernel import JsonValue, canonical_digest, freeze_json, require_sha256
from noetrium_platform.capabilities.participant.core.api.contracts import (
    ParticipantImplementationIdentity,
    ParticipantRuntimeBinding,
    ParticipantSessionRuntimeIdentity,
)
from noetrium_platform.foundation.scope.api import ScopeIdentity, ScopeKind


@dataclass(frozen=True, slots=True)
class ExperimentParticipantSpec:
    role: str
    implementation: ParticipantImplementationIdentity
    runtime: ParticipantSessionRuntimeIdentity
    configuration_digest: str | None = None
    depends_on_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("participant role must be non-empty")
        if re.fullmatch(r"[a-z][a-z0-9_]*", self.implementation.kind) is None:
            raise ValueError("participant kind must be a safe operation namespace token")
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", self.role) is None:
            raise ValueError("participant role must be a safe topology token")
        if self.role in self.depends_on_roles:
            raise ValueError(f"participant {self.role} cannot depend on itself")
        if self.configuration_digest is not None:
            require_sha256(self.configuration_digest, "participant configuration_digest")

    def runtime_binding(self) -> ParticipantRuntimeBinding:
        return ParticipantRuntimeBinding(
            self.role,
            self.implementation,
            self.runtime,
            self.configuration_digest,
        )


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    experiment_id: str
    study_id: str
    project_id: str
    participants: tuple[ExperimentParticipantSpec, ...]
    model_stack_digest: str | None
    prompt_generation: str | None
    workload_digest: str
    seed_digest: str
    repetitions: int
    trial_protocol_id: str
    trial_protocol_configuration_digest: str

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must be non-empty")
        if not self.study_id.strip():
            raise ValueError("study_id must be non-empty")
        if not self.project_id.strip():
            raise ValueError("project_id must be non-empty")
        if self.model_stack_digest is not None:
            require_sha256(self.model_stack_digest, "model_stack_digest")
        if self.prompt_generation is not None and not self.prompt_generation.strip():
            raise ValueError("prompt_generation cannot be empty when provided")
        for name, value in (("workload_digest", self.workload_digest), ("seed_digest", self.seed_digest)):
            require_sha256(value, name)
        if self.repetitions <= 0:
            raise ValueError("repetitions must be positive")
        if not self.trial_protocol_id.strip():
            raise ValueError("trial_protocol_id must be non-empty")
        require_sha256(self.trial_protocol_configuration_digest, "trial_protocol_configuration_digest")

    @property
    def scope(self) -> ScopeIdentity:
        return ScopeIdentity(ScopeKind.EXPERIMENT, self.experiment_id)

    def identity_digest(self) -> str:
        return canonical_digest(self)


class ExperimentUnitKind(StrEnum):
    GENERIC = "generic"
    TASK = "task"
    EPISODE = "episode"
    SESSION = "session"
    WINDOW = "window"
    PARTICIPANT = "participant"
    GRAPH_NODE = "graph_node"
    ALLOCATION = "allocation"


class ExecutionMode(StrEnum):
    BATCH = "batch"
    INTERACTIVE = "interactive"
    STREAMING = "streaming"
    ONLINE = "online"
    SIMULATION = "simulation"
    DISTRIBUTED = "distributed"
    HUMAN_IN_LOOP = "human_in_loop"
    ADAPTIVE = "adaptive"


class ObservationKind(StrEnum):
    MEASUREMENT = "measurement"
    EVENT = "event"
    TRACE = "trace"
    TRAJECTORY = "trajectory"
    ARTIFACT = "artifact"
    TELEMETRY = "telemetry"


class ExperimentLifecycleState(StrEnum):
    DECLARED = "declared"
    RESOLVED = "resolved"
    FROZEN = "frozen"
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    RESUMING = "resuming"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field_name} must be a tuple")
    if any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must be unique")
    return value


def _digests(value: object, field_name: str) -> tuple[str, ...]:
    values = _strings(value, field_name)
    for item in values:
        require_sha256(item, f"{field_name} item")
    return values


@dataclass(frozen=True, slots=True)
class ExperimentUnit:
    unit_id: str
    kind: ExperimentUnitKind
    input_digest: str
    condition_digest: str
    seed: str
    parent_unit_ids: tuple[str, ...] = ()
    ordinal: int = 0
    unit_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.unit_id, "experiment unit unit_id")
        if not isinstance(self.kind, ExperimentUnitKind):
            raise TypeError("experiment unit kind must be ExperimentUnitKind")
        require_sha256(self.input_digest, "experiment unit input_digest")
        require_sha256(self.condition_digest, "experiment unit condition_digest")
        _text(self.seed, "experiment unit seed")
        _strings(self.parent_unit_ids, "experiment unit parent_unit_ids")
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("experiment unit ordinal must be a non-negative integer")
        object.__setattr__(self, "unit_digest", canonical_digest({
            "unit_id": self.unit_id, "kind": self.kind.value,
            "input_digest": self.input_digest, "condition_digest": self.condition_digest,
            "seed": self.seed, "parent_unit_ids": self.parent_unit_ids,
            "ordinal": self.ordinal,
        }))


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    project_id: str
    experiment_id: str
    protocol_id: str
    unit_kind: ExperimentUnitKind
    execution_mode: ExecutionMode
    design_protocol_digest: str
    observation_protocol_digest: str
    analysis_plan_digest: str
    implementation_digest: str
    resource_policy_digest: str
    input_cut_digest: str | None = None
    objective: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    definition_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (("project_id", self.project_id), ("experiment_id", self.experiment_id), ("protocol_id", self.protocol_id)):
            _text(value, f"experiment definition {name}")
        if not isinstance(self.unit_kind, ExperimentUnitKind):
            raise TypeError("experiment definition unit_kind must be ExperimentUnitKind")
        if not isinstance(self.execution_mode, ExecutionMode):
            raise TypeError("experiment definition execution_mode must be ExecutionMode")
        for name, value in (("design_protocol_digest", self.design_protocol_digest),
                            ("observation_protocol_digest", self.observation_protocol_digest),
                            ("analysis_plan_digest", self.analysis_plan_digest),
                            ("implementation_digest", self.implementation_digest),
                            ("resource_policy_digest", self.resource_policy_digest)):
            require_sha256(value, f"experiment definition {name}")
        if self.input_cut_digest is not None:
            require_sha256(self.input_cut_digest, "experiment definition input_cut_digest")
        if type(self.objective) is not str:
            raise TypeError("experiment definition objective must be a string")
        frozen = freeze_json(self.metadata)
        if not isinstance(frozen, Mapping):
            raise TypeError("experiment definition metadata must be a mapping")
        object.__setattr__(self, "metadata", frozen)
        object.__setattr__(self, "definition_digest", canonical_digest({
            "project_id": self.project_id, "experiment_id": self.experiment_id,
            "protocol_id": self.protocol_id, "unit_kind": self.unit_kind.value,
            "execution_mode": self.execution_mode.value,
            "design_protocol_digest": self.design_protocol_digest,
            "observation_protocol_digest": self.observation_protocol_digest,
            "analysis_plan_digest": self.analysis_plan_digest,
            "implementation_digest": self.implementation_digest,
            "resource_policy_digest": self.resource_policy_digest,
            "input_cut_digest": self.input_cut_digest,
            "objective": self.objective, "metadata": self.metadata,
        }))


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment_id: str
    definition_digest: str
    units: tuple[ExperimentUnit, ...]
    planner_id: str = "universal-static-planner-v1"
    plan_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.experiment_id, "experiment plan experiment_id")
        require_sha256(self.definition_digest, "experiment plan definition_digest")
        if type(self.units) is not tuple or not self.units:
            raise ValueError("experiment plan units must be a non-empty tuple")
        if any(type(unit) is not ExperimentUnit for unit in self.units):
            raise TypeError("experiment plan units must contain ExperimentUnit")
        ids = tuple(unit.unit_id for unit in self.units)
        if len(ids) != len(set(ids)):
            raise ValueError("experiment plan unit ids must be unique")
        known = set(ids)
        ordinals = {unit.unit_id: unit.ordinal for unit in self.units}
        for unit in self.units:
            unknown = set(unit.parent_unit_ids) - known
            if unknown:
                raise ValueError(f"experiment plan has unknown parent units: {sorted(unknown)}")
            if any(ordinals[parent] >= unit.ordinal for parent in unit.parent_unit_ids):
                raise ValueError("experiment plan parent units must precede their child")
        _text(self.planner_id, "experiment plan planner_id")
        object.__setattr__(self, "plan_digest", canonical_digest({
            "experiment_id": self.experiment_id, "definition_digest": self.definition_digest,
            "units": tuple(unit.unit_digest for unit in self.units), "planner_id": self.planner_id,
        }))

    def unit(self, unit_id: str) -> ExperimentUnit:
        matches = tuple(unit for unit in self.units if unit.unit_id == unit_id)
        if len(matches) != 1:
            raise KeyError(f"experiment plan has no unique unit {unit_id!r}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ObservationEnvelope:
    experiment_id: str
    run_id: str
    unit_id: str
    sequence: int
    logical_time: str
    producer_id: str
    schema_id: str
    kind: ObservationKind
    payload: JsonValue
    lineage_digests: tuple[str, ...] = ()
    observation_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (("experiment_id", self.experiment_id), ("run_id", self.run_id),
                            ("unit_id", self.unit_id), ("logical_time", self.logical_time),
                            ("producer_id", self.producer_id), ("schema_id", self.schema_id)):
            _text(value, f"observation {name}")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("observation sequence must be a non-negative integer")
        if not isinstance(self.kind, ObservationKind):
            raise TypeError("observation kind must be ObservationKind")
        _digests(self.lineage_digests, "observation lineage_digests")
        frozen = freeze_json(self.payload)
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "observation_digest", canonical_digest({
            "experiment_id": self.experiment_id, "run_id": self.run_id, "unit_id": self.unit_id,
            "sequence": self.sequence, "logical_time": self.logical_time,
            "producer_id": self.producer_id, "schema_id": self.schema_id,
            "kind": self.kind.value, "payload": frozen, "lineage_digests": self.lineage_digests,
        }))


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    analysis_id: str
    estimator_id: str
    input_kinds: tuple[ObservationKind, ...]
    grouping_keys: tuple[str, ...] = ()
    comparison: str | None = None
    frozen: bool = True
    analysis_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.analysis_id, "analysis plan analysis_id")
        _text(self.estimator_id, "analysis plan estimator_id")
        if type(self.input_kinds) is not tuple or not self.input_kinds:
            raise ValueError("analysis plan input_kinds must be non-empty")
        if any(not isinstance(item, ObservationKind) for item in self.input_kinds):
            raise TypeError("analysis plan input_kinds must contain ObservationKind")
        _strings(self.grouping_keys, "analysis plan grouping_keys")
        if self.comparison is not None:
            _text(self.comparison, "analysis plan comparison")
        if type(self.frozen) is not bool or not self.frozen:
            raise ValueError("analysis plan must be frozen before execution")
        object.__setattr__(self, "analysis_digest", canonical_digest({
            "analysis_id": self.analysis_id, "estimator_id": self.estimator_id,
            "input_kinds": tuple(item.value for item in self.input_kinds),
            "grouping_keys": self.grouping_keys, "comparison": self.comparison,
            "frozen": self.frozen,
        }))


@dataclass(frozen=True, slots=True)
class ExperimentTransition:
    experiment_id: str
    previous: ExperimentLifecycleState
    current: ExperimentLifecycleState
    logical_time: str
    reason: str
    receipt_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.experiment_id, "transition experiment_id")
        if not isinstance(self.previous, ExperimentLifecycleState) or not isinstance(self.current, ExperimentLifecycleState):
            raise TypeError("transition states must be ExperimentLifecycleState")
        _text(self.logical_time, "transition logical_time")
        _text(self.reason, "transition reason")
        object.__setattr__(self, "receipt_digest", canonical_digest({
            "experiment_id": self.experiment_id, "previous": self.previous.value,
            "current": self.current.value, "logical_time": self.logical_time, "reason": self.reason,
        }))


@dataclass(frozen=True, slots=True)
class DoctorFinding:
    code: str
    severity: FindingSeverity
    scope: str
    message: str
    blocking: bool = False
    recovery_action: str | None = None
    finding_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.code, "doctor finding code")
        if not isinstance(self.severity, FindingSeverity):
            raise TypeError("doctor finding severity must be FindingSeverity")
        _text(self.scope, "doctor finding scope")
        _text(self.message, "doctor finding message")
        if type(self.blocking) is not bool:
            raise TypeError("doctor finding blocking must be boolean")
        if self.recovery_action is not None:
            _text(self.recovery_action, "doctor finding recovery_action")
        object.__setattr__(self, "finding_digest", canonical_digest({
            "code": self.code, "severity": self.severity.value, "scope": self.scope,
            "message": self.message, "blocking": self.blocking,
            "recovery_action": self.recovery_action,
        }))


class ExperimentUnitPlannerPort(Protocol):
    def plan(self, definition: ExperimentDefinition, units: tuple[ExperimentUnit, ...]) -> ExperimentPlan:
        ...


class ExperimentModePort(Protocol):
    def execute(self, plan: ExperimentPlan, sink: "ObservationSinkPort") -> None:
        ...


class ObservationSinkPort(Protocol):
    def append(self, observation: ObservationEnvelope) -> None:
        ...


class ExperimentDoctorPort(Protocol):
    def inspect(self, plan: ExperimentPlan, observations: tuple[ObservationEnvelope, ...]) -> tuple[DoctorFinding, ...]:
        ...


class UnitOutcomeState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYABLE = "retryable"


@dataclass(frozen=True, slots=True)
class UnitOutcome:
    unit_id: str
    state: UnitOutcomeState
    attempts: int
    observation_digests: tuple[str, ...] = ()
    error_code: str | None = None
    outcome_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.unit_id, "unit outcome unit_id")
        if not isinstance(self.state, UnitOutcomeState):
            raise TypeError("unit outcome state must be UnitOutcomeState")
        if type(self.attempts) is not int or self.attempts <= 0:
            raise ValueError("unit outcome attempts must be positive")
        _digests(self.observation_digests, "unit outcome observation_digests")
        if self.error_code is not None:
            _text(self.error_code, "unit outcome error_code")
        if self.state is UnitOutcomeState.SUCCEEDED and self.error_code is not None:
            raise ValueError("succeeded unit outcome cannot carry an error")
        if self.state is UnitOutcomeState.FAILED and self.error_code is None:
            raise ValueError("failed unit outcome requires an error code")
        object.__setattr__(self, "outcome_digest", canonical_digest({
            "unit_id": self.unit_id, "state": self.state.value, "attempts": self.attempts,
            "observation_digests": self.observation_digests, "error_code": self.error_code,
        }))


@dataclass(frozen=True, slots=True)
class ExperimentRunReport:
    experiment_id: str
    run_id: str
    plan_digest: str
    state: ExperimentLifecycleState
    outcomes: tuple[UnitOutcome, ...]
    findings: tuple[DoctorFinding, ...] = ()
    report_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.experiment_id, "experiment run report experiment_id")
        _text(self.run_id, "experiment run report run_id")
        require_sha256(self.plan_digest, "experiment run report plan_digest")
        if self.state not in (ExperimentLifecycleState.COMPLETED,
                               ExperimentLifecycleState.PARTIAL,
                               ExperimentLifecycleState.FAILED):
            raise ValueError("experiment run report state must be terminal")
        if type(self.outcomes) is not tuple or any(type(item) is not UnitOutcome for item in self.outcomes):
            raise TypeError("experiment run report outcomes must contain UnitOutcome")
        if type(self.findings) is not tuple or any(type(item) is not DoctorFinding for item in self.findings):
            raise TypeError("experiment run report findings must contain DoctorFinding")
        ids = tuple(item.unit_id for item in self.outcomes)
        if len(ids) != len(set(ids)):
            raise ValueError("experiment run report outcomes must be unique per unit")
        object.__setattr__(self, "report_digest", canonical_digest({
            "experiment_id": self.experiment_id, "run_id": self.run_id,
            "plan_digest": self.plan_digest, "state": self.state.value,
            "outcomes": tuple(item.outcome_digest for item in self.outcomes),
            "findings": tuple(item.finding_digest for item in self.findings),
        }))


class ExperimentUnitExecutorPort(Protocol):
    def execute_unit(self, unit: ExperimentUnit, run_id: str, sink: ObservationSinkPort) -> UnitOutcome:
        ...

__all__ = [
    "ExperimentParticipantSpec", "ExperimentSpec", "AnalysisPlan", "DoctorFinding",
    "ExecutionMode", "ExperimentDefinition", "ExperimentLifecycleState", "ExperimentModePort",
    "ExperimentPlan", "ExperimentRunReport", "ExperimentTransition", "ExperimentUnit",
    "ExperimentUnitExecutorPort", "ExperimentUnitKind", "ExperimentUnitPlannerPort",
    "FindingSeverity", "ObservationEnvelope", "ObservationKind", "ObservationSinkPort",
    "ExperimentDoctorPort", "UnitOutcome", "UnitOutcomeState",
]
