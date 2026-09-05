from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import math

from noetrium_platform.foundation.governance.system_registry.api.contracts import (
    SystemDescriptor,
    SystemIdentity,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be canonical non-empty text")
    return value


def _sha256(value: object, field: str) -> str:
    digest = _text(value, field)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _positive(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _refs(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    refs = tuple(_text(item, field) for item in value)
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _stable_id(*parts: str) -> str:
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ObservationOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class DriftKind(StrEnum):
    UNKNOWN_NODE = "unknown-node"
    STALE_GENERATION = "stale-generation"
    DIGEST_MISMATCH = "digest-mismatch"


class SignalKind(StrEnum):
    FAILURE_CLUSTER = "failure-cluster"
    LATENCY_ANOMALY = "latency-anomaly"
    TOPOLOGY_DRIFT = "topology-drift"


class EvolutionStage(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled-back"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class TopologyObservation:
    observation_id: str
    system: SystemIdentity
    topology_generation: int
    topology_digest: str
    operation_id: str
    duration_seconds: float
    outcome: ObservationOutcome
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id")
        _positive(self.topology_generation, "topology_generation")
        _sha256(self.topology_digest, "topology_digest")
        _text(self.operation_id, "operation_id")
        if not isinstance(self.duration_seconds, (int, float)) or not math.isfinite(
            float(self.duration_seconds)
        ) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and non-negative")
        if not isinstance(self.outcome, ObservationOutcome):
            raise TypeError("outcome must be ObservationOutcome")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class TopologyDrift:
    kind: DriftKind
    system: SystemIdentity
    expected_generation: int
    expected_digest: str
    observed_generation: int
    observed_digest: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DriftKind):
            raise TypeError("kind must be DriftKind")
        _positive(self.expected_generation, "expected_generation")
        _sha256(self.expected_digest, "expected_digest")
        _positive(self.observed_generation, "observed_generation")
        _sha256(self.observed_digest, "observed_digest")
        _text(self.reason, "reason")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    source_id: str
    source_digest: str
    registered: tuple[str, ...]
    already_registered: tuple[str, ...]
    rejected: tuple[str, ...]
    topology_generation: int
    topology_digest: str

    def __post_init__(self) -> None:
        _text(self.source_id, "source_id")
        _sha256(self.source_digest, "source_digest")
        for field in ("registered", "already_registered", "rejected"):
            object.__setattr__(self, field, _refs(getattr(self, field), field))
        _positive(self.topology_generation, "topology_generation")
        _sha256(self.topology_digest, "topology_digest")

    def digest(self) -> str:
        return canonical_digest(self)



@dataclass(frozen=True, slots=True)
class ImprovementSignal:
    signal_id: str
    target: SystemIdentity
    kind: SignalKind
    topology_generation: int
    topology_digest: str
    severity: int
    sample_size: int
    evidence_refs: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id")
        if not isinstance(self.kind, SignalKind):
            raise TypeError("kind must be SignalKind")
        _positive(self.topology_generation, "topology_generation")
        _sha256(self.topology_digest, "topology_digest")
        if type(self.severity) is not int or not 1 <= self.severity <= 5:
            raise ValueError("severity must be an integer from 1 to 5")
        _positive(self.sample_size, "sample_size")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        _text(self.description, "description")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class EvolutionProposal:
    proposal_id: str
    signal: ImprovementSignal
    predecessor_topology_digest: str
    change_contract_id: str
    implementation_digest: str
    configuration_digest: str
    validation_plan_digest: str
    rollback_anchor_digest: str
    stage: EvolutionStage = EvolutionStage.PROPOSED

    def __post_init__(self) -> None:
        _text(self.proposal_id, "proposal_id")
        _sha256(self.predecessor_topology_digest, "predecessor_topology_digest")
        _text(self.change_contract_id, "change_contract_id")
        _sha256(self.implementation_digest, "implementation_digest")
        _sha256(self.configuration_digest, "configuration_digest")
        _sha256(self.validation_plan_digest, "validation_plan_digest")
        _sha256(self.rollback_anchor_digest, "rollback_anchor_digest")
        if not isinstance(self.stage, EvolutionStage):
            raise TypeError("stage must be EvolutionStage")
        if self.signal.topology_digest != self.predecessor_topology_digest:
            raise ValueError("proposal must bind the signal topology digest")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class EvolutionAssessment:
    topology_generation: int
    topology_digest: str
    signals: tuple[ImprovementSignal, ...]
    drifts: tuple[TopologyDrift, ...]
    observed_systems: tuple[str, ...]
    unobserved_systems: tuple[str, ...]

    def __post_init__(self) -> None:
        _positive(self.topology_generation, "topology_generation")
        _sha256(self.topology_digest, "topology_digest")
        if not isinstance(self.signals, tuple) or not all(
            isinstance(item, ImprovementSignal) for item in self.signals
        ):
            raise TypeError("signals must contain ImprovementSignal values")
        if not isinstance(self.drifts, tuple) or not all(
            isinstance(item, TopologyDrift) for item in self.drifts
        ):
            raise TypeError("drifts must contain TopologyDrift values")
        object.__setattr__(self, "observed_systems", _refs(self.observed_systems, "observed_systems"))
        object.__setattr__(self, "unobserved_systems", _refs(self.unobserved_systems, "unobserved_systems"))

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class EvolutionTransition:
    transition_id: str
    proposal_id: str
    proposal_digest: str
    from_stage: EvolutionStage
    to_stage: EvolutionStage
    evidence_refs: tuple[str, ...]
    reason_digest: str
    decision_contract_id: str
    decision_implementation_digest: str
    decision_configuration_digest: str
    transition_generation: int

    def __post_init__(self) -> None:
        _text(self.transition_id, "transition_id")
        _text(self.proposal_id, "proposal_id")
        _sha256(self.proposal_digest, "proposal_digest")
        if not isinstance(self.from_stage, EvolutionStage):
            raise TypeError("from_stage must be EvolutionStage")
        if not isinstance(self.to_stage, EvolutionStage):
            raise TypeError("to_stage must be EvolutionStage")
        if self.from_stage is self.to_stage:
            raise ValueError("evolution transition must change stage")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        if not self.evidence_refs:
            raise ValueError("evolution transition requires evidence")
        _sha256(self.reason_digest, "reason_digest")
        _text(self.decision_contract_id, "decision_contract_id")
        _sha256(self.decision_implementation_digest, "decision_implementation_digest")
        _sha256(self.decision_configuration_digest, "decision_configuration_digest")
        _positive(self.transition_generation, "transition_generation")

    def digest(self) -> str:
        return canonical_digest(self)


__all__ = [
    "DiscoveryReport",
    "DriftKind",
    "EvolutionAssessment",
    "EvolutionProposal",
    "EvolutionStage",
    "EvolutionTransition",
    "ImprovementSignal",
    "ObservationOutcome",
    "SignalKind",
    "TopologyDrift",
    "TopologyObservation",
]