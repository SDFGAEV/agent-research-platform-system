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

class RawRecordStorePort(Protocol):
    """Append-only sink for lossless events, independent of observations."""

    def append_raw_record(self, record: "RawRecord") -> None:
        ...

    def raw_snapshot(self) -> tuple["RawRecord", ...]:
        ...


@dataclass(frozen=True, slots=True)
class RawRecord:
    """Immutable, lossless event envelope plus re-buildable projections.

    raw_payload is the exact source byte sequence. It is never decoded,
    normalized, redacted, truncated, or replaced by payload. The envelope
    captures identity, causality, clock quality, producer, outcome and
    provenance; dimensions is an extensible namespace for usage, timing,
    resource, cost, model, artifact, environment, quality and custom facts.
    """

    experiment_id: str
    run_id: str
    unit_id: str
    sequence: int
    occurred_at: str
    recorded_at: str
    producer_id: str
    schema_id: str
    record_type: str
    raw_payload: bytes
    payload: JsonValue
    stream_id: str = ""
    attempt_id: str = ""
    parent_record_digests: tuple[str, ...] = ()
    causation_id: str | None = None
    correlation_id: str = ""
    trace_id: str | None = None
    span_id: str | None = None
    event_name: str = ""
    operation_id: str = ""
    status: str = "unknown"
    outcome: str | None = None
    monotonic_ns: int | None = None
    clock_source: str = "wall"
    clock_uncertainty_ns: int | None = None
    producer_version: str = "unknown"
    producer_instance_id: str = ""
    dimensions: JsonValue = field(default_factory=dict)
    source_location: JsonValue = field(default_factory=dict)
    privacy: JsonValue = field(default_factory=dict)
    sampled: bool = True
    sampling_rate: float = 1.0
    content_type: str = "application/octet-stream"
    content_encoding: str = "identity"
    lineage_digests: tuple[str, ...] = ()
    raw_payload_digest: str = field(init=False)
    record_digest: str = field(init=False)

    def __post_init__(self) -> None:
        defaults = {
            "stream_id": self.run_id + ":" + self.unit_id,
            "attempt_id": "attempt-0",
            "correlation_id": self.run_id,
            "event_name": self.record_type,
            "operation_id": self.record_type,
            "producer_instance_id": self.producer_id,
        }
        for name, value in defaults.items():
            if not getattr(self, name):
                object.__setattr__(self, name, value)
        for name, value in (
            ("experiment_id", self.experiment_id), ("run_id", self.run_id),
            ("unit_id", self.unit_id), ("stream_id", self.stream_id),
            ("attempt_id", self.attempt_id), ("occurred_at", self.occurred_at),
            ("recorded_at", self.recorded_at), ("producer_id", self.producer_id),
            ("producer_version", self.producer_version),
            ("producer_instance_id", self.producer_instance_id),
            ("schema_id", self.schema_id), ("record_type", self.record_type),
            ("clock_source", self.clock_source), ("content_type", self.content_type),
            ("content_encoding", self.content_encoding), ("event_name", self.event_name),
            ("operation_id", self.operation_id), ("status", self.status),
        ):
            _text(value, f"raw record {name}")
        for name, value in (
            ("causation_id", self.causation_id), ("correlation_id", self.correlation_id),
            ("trace_id", self.trace_id), ("span_id", self.span_id),
            ("outcome", self.outcome),
        ):
            if value is not None:
                _text(value, f"raw record {name}")
        if type(self.raw_payload) is not bytes:
            raise TypeError("raw record raw_payload must be exact bytes")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("raw record sequence must be non-negative")
        for name, value in (
            ("monotonic_ns", self.monotonic_ns),
            ("clock_uncertainty_ns", self.clock_uncertainty_ns),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"raw record {name} must be non-negative when provided")
        if type(self.sampled) is not bool:
            raise TypeError("raw record sampled must be boolean")
        if type(self.sampling_rate) not in (int, float) or not 0.0 < float(self.sampling_rate) <= 1.0:
            raise ValueError("raw record sampling_rate must be in (0, 1]")
        _digests(self.parent_record_digests, "raw record parent_record_digests")
        _digests(self.lineage_digests, "raw record lineage_digests")
        frozen_payload = freeze_json(self.payload)
        frozen_dimensions = freeze_json(self.dimensions)
        frozen_source = freeze_json(self.source_location)
        frozen_privacy = freeze_json(self.privacy)
        for name, value in (
            ("dimensions", frozen_dimensions), ("source_location", frozen_source),
            ("privacy", frozen_privacy),
        ):
            if not isinstance(value, Mapping):
                raise TypeError(f"raw record {name} must be a mapping")
        object.__setattr__(self, "payload", frozen_payload)
        object.__setattr__(self, "dimensions", frozen_dimensions)
        object.__setattr__(self, "source_location", frozen_source)
        object.__setattr__(self, "privacy", frozen_privacy)
        raw_digest = canonical_digest({"raw_payload_hex": self.raw_payload.hex()})
        object.__setattr__(self, "raw_payload_digest", raw_digest)
        object.__setattr__(self, "record_digest", canonical_digest({
            "experiment_id": self.experiment_id, "run_id": self.run_id,
            "unit_id": self.unit_id, "sequence": self.sequence,
            "stream_id": self.stream_id, "attempt_id": self.attempt_id,
            "parent_record_digests": self.parent_record_digests,
            "causation_id": self.causation_id, "correlation_id": self.correlation_id,
            "trace_id": self.trace_id, "span_id": self.span_id,
            "occurred_at": self.occurred_at, "recorded_at": self.recorded_at,
            "monotonic_ns": self.monotonic_ns, "clock_source": self.clock_source,
            "clock_uncertainty_ns": self.clock_uncertainty_ns,
            "producer_id": self.producer_id, "producer_version": self.producer_version,
            "producer_instance_id": self.producer_instance_id,
            "schema_id": self.schema_id, "record_type": self.record_type,
            "event_name": self.event_name, "operation_id": self.operation_id,
            "status": self.status, "outcome": self.outcome,
            "raw_payload_digest": raw_digest, "payload": frozen_payload,
            "dimensions": frozen_dimensions, "source_location": frozen_source,
            "privacy": frozen_privacy, "sampled": self.sampled,
            "sampling_rate": float(self.sampling_rate),
            "content_type": self.content_type, "content_encoding": self.content_encoding,
            "lineage_digests": self.lineage_digests,
        }))


class MetricAggregation(StrEnum):
    COUNT = "count"
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    STDDEV = "stddev"
    P50 = "p50"
    P95 = "p95"
    FIRST = "first"
    LAST = "last"
    DISTINCT_COUNT = "distinct_count"


class MetricMissingPolicy(StrEnum):
    SKIP = "skip"
    ZERO = "zero"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class MetricPredicate:
    path: tuple[str, ...]
    equals: JsonValue
    predicate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _strings(self.path, "metric predicate path")
        frozen = freeze_json(self.equals)
        object.__setattr__(self, "equals", frozen)
        object.__setattr__(self, "predicate_digest", canonical_digest({
            "path": self.path, "equals": frozen,
        }))


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    aggregation: MetricAggregation
    record_types: tuple[str, ...] = ()
    schema_ids: tuple[str, ...] = ()
    value_path: tuple[str, ...] = ()
    group_by: tuple[tuple[str, ...], ...] = ()
    predicates: tuple[MetricPredicate, ...] = ()
    missing: MetricMissingPolicy = MetricMissingPolicy.SKIP
    unit: str | None = None
    description: str = ""
    definition_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.metric_id, "metric definition metric_id")
        if not isinstance(self.aggregation, MetricAggregation):
            raise TypeError("metric definition aggregation must be MetricAggregation")
        if type(self.record_types) is not tuple or any(type(item) is not str or not item.strip() for item in self.record_types):
            raise TypeError("metric definition record_types must contain strings")
        if type(self.schema_ids) is not tuple or any(type(item) is not str or not item.strip() for item in self.schema_ids):
            raise TypeError("metric definition schema_ids must contain strings")
        _strings(self.value_path, "metric definition value_path")
        if self.aggregation is not MetricAggregation.COUNT and not self.value_path:
            raise ValueError("non-count metric requires value_path")
        if type(self.group_by) is not tuple or any(type(path) is not tuple or not path for path in self.group_by):
            raise TypeError("metric definition group_by must contain non-empty paths")
        for path in self.group_by:
            _strings(path, "metric definition group_by path")
        if type(self.predicates) is not tuple or any(type(item) is not MetricPredicate for item in self.predicates):
            raise TypeError("metric definition predicates must contain MetricPredicate")
        if not isinstance(self.missing, MetricMissingPolicy):
            raise TypeError("metric definition missing must be MetricMissingPolicy")
        if self.unit is not None:
            _text(self.unit, "metric definition unit")
        if type(self.description) is not str:
            raise TypeError("metric definition description must be a string")
        object.__setattr__(self, "definition_digest", canonical_digest({
            "metric_id": self.metric_id, "aggregation": self.aggregation.value,
            "record_types": self.record_types, "schema_ids": self.schema_ids,
            "value_path": self.value_path, "group_by": self.group_by,
            "predicates": tuple(item.predicate_digest for item in self.predicates),
            "missing": self.missing.value, "unit": self.unit,
            "description": self.description,
        }))


@dataclass(frozen=True, slots=True)
class MetricValue:
    metric_id: str
    group_key: tuple[JsonValue, ...]
    value: JsonValue
    sample_size: int
    record_digests: tuple[str, ...]
    value_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.metric_id, "metric value metric_id")
        if type(self.group_key) is not tuple:
            raise TypeError("metric value group_key must be a tuple")
        if type(self.sample_size) is not int or self.sample_size < 0:
            raise ValueError("metric value sample_size must be non-negative")
        _digests(self.record_digests, "metric value record_digests")
        object.__setattr__(self, "value", freeze_json(self.value))
        object.__setattr__(self, "value_digest", canonical_digest({
            "metric_id": self.metric_id, "group_key": self.group_key,
            "value": self.value, "sample_size": self.sample_size,
            "record_digests": self.record_digests,
        }))


@dataclass(frozen=True, slots=True)
class MetricReport:
    raw_cut_digest: str
    definitions_digest: str
    values: tuple[MetricValue, ...]
    report_digest: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256(self.raw_cut_digest, "metric report raw_cut_digest")
        require_sha256(self.definitions_digest, "metric report definitions_digest")
        if type(self.values) is not tuple or any(type(item) is not MetricValue for item in self.values):
            raise TypeError("metric report values must contain MetricValue")
        object.__setattr__(self, "report_digest", canonical_digest({
            "raw_cut_digest": self.raw_cut_digest,
            "definitions_digest": self.definitions_digest,
            "values": tuple(item.value_digest for item in self.values),
        }))


__all__ = [
    "ExperimentParticipantSpec", "ExperimentSpec", "AnalysisPlan", "DoctorFinding",
    "ExecutionMode", "ExperimentDefinition", "ExperimentLifecycleState", "ExperimentModePort",
    "ExperimentPlan", "ExperimentRunReport", "ExperimentTransition", "ExperimentUnit",
    "ExperimentUnitExecutorPort", "ExperimentUnitKind", "ExperimentUnitPlannerPort",
    "FindingSeverity", "ObservationEnvelope", "ObservationKind", "ObservationSinkPort",
    "ExperimentDoctorPort", "UnitOutcome", "UnitOutcomeState", "RawRecordStorePort",
    "RawRecord", "MetricAggregation", "MetricMissingPolicy", "MetricPredicate",
    "MetricDefinition", "MetricValue", "MetricReport",
]
