from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .contracts import Observation

from noetrium_platform.foundation.kernel.kernel import (
    ExecutionContext,
    JsonInput,
    JsonObject,
    JsonValue,
    canonical_digest,
    freeze_json,
    thaw_json,
)

class EnvironmentActionPhase(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    DISPATCHED = "dispatched"
    OBSERVED = "observed"
    SETTLED = "settled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class EnvironmentQueryKind(StrEnum):
    STATE = "state"
    CAPABILITIES = "capabilities"
    ENTITY = "entity"
    TASK = "task"
    ARTIFACT = "artifact"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class EnvironmentCapabilityDescriptor:
    capability_id: str
    version: str
    action_types: tuple[str, ...] = ()
    query_types: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.version.strip():
            raise ValueError("environment capability identity is required")
        if any(not value.strip() for value in self.action_types + self.query_types):
            raise ValueError("environment capability names must be non-empty")
        object.__setattr__(self, "metadata", dict(thaw_json(freeze_json(self.metadata))))


@dataclass(frozen=True, slots=True)
class EnvironmentQuery:
    query_id: str
    query_type: str
    payload: JsonInput
    context: ExecutionContext

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.query_type.strip():
            raise ValueError("environment query identity is required")
        object.__setattr__(self, "payload", freeze_json(self.payload))


@dataclass(frozen=True, slots=True)
class EnvironmentQueryResult:
    query_id: str
    supported: bool
    payload: JsonObject
    observation: Observation | None
    diagnostics: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise ValueError("environment query result query_id is required")
        if not isinstance(self.supported, bool):
            raise TypeError("environment query result supported must be boolean")
        object.__setattr__(self, "payload", dict(thaw_json(freeze_json(self.payload))))
        object.__setattr__(self, "diagnostics", dict(thaw_json(freeze_json(self.diagnostics))))


@dataclass(frozen=True, slots=True)
class EnvironmentActionLifecycle:
    action_id: str
    request_digest: str
    phase: EnvironmentActionPhase
    accepted: bool | None
    terminal: bool
    evidence_refs: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        if not self.action_id.strip() or len(self.request_digest) != 64:
            raise ValueError("environment action lifecycle identity is invalid")
        if not isinstance(self.phase, EnvironmentActionPhase):
            raise TypeError("environment action lifecycle phase is invalid")
        if not isinstance(self.terminal, bool):
            raise TypeError("environment action lifecycle terminal must be boolean")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("environment action lifecycle evidence refs must be non-empty")

@dataclass(frozen=True, slots=True)
class EnvironmentRawEventRecord:
    event_id: str
    stream_id: str
    sequence: int
    kind: str
    occurred_at_ns: int
    raw_payload: bytes
    normalized_payload: JsonObject
    source_id: str
    context: ExecutionContext
    parent_event_id: str | None = None
    dimensions: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.stream_id.strip() or not self.kind.strip() or not self.source_id.strip():
            raise ValueError("environment raw event identity is incomplete")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("environment raw event sequence must be positive")
        if type(self.occurred_at_ns) is not int or self.occurred_at_ns <= 0:
            raise ValueError("environment raw event occurred_at_ns must be positive")
        if type(self.raw_payload) is not bytes:
            raise TypeError("environment raw event raw_payload must be exact bytes")
        object.__setattr__(self, "normalized_payload", dict(thaw_json(freeze_json(self.normalized_payload))))
        object.__setattr__(self, "dimensions", dict(thaw_json(freeze_json(self.dimensions))))

    @property
    def raw_payload_sha256(self) -> str:
        import hashlib
        return hashlib.sha256(self.raw_payload).hexdigest()

    @property
    def record_digest(self) -> str:
        return canonical_digest({
            "event_id": self.event_id,
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "occurred_at_ns": self.occurred_at_ns,
            "raw_payload_sha256": self.raw_payload_sha256,
            "source_id": self.source_id,
            "parent_event_id": self.parent_event_id,
            "dimensions": self.dimensions,
        })


@dataclass(frozen=True, slots=True)
class EnvironmentRawEventReceipt:
    event_id: str
    stream_id: str
    sequence: int
    record_digest: str
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.stream_id.strip():
            raise ValueError("environment raw event receipt identity is incomplete")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("environment raw event receipt sequence must be positive")
        for name, value in (
            ("record_digest", self.record_digest),
            ("raw_payload_sha256", self.raw_payload_sha256),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or value != value.lower()
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")

@runtime_checkable
class EnvironmentQueryPort(Protocol):
    def query(self, request: EnvironmentQuery) -> EnvironmentQueryResult: ...


@runtime_checkable
class EnvironmentCapabilityPort(Protocol):
    def capability_descriptors(self) -> tuple[EnvironmentCapabilityDescriptor, ...]: ...


@runtime_checkable
class EnvironmentRawRecordSinkPort(Protocol):
    def append(self, record: EnvironmentRawEventRecord) -> EnvironmentRawEventReceipt: ...


@dataclass(frozen=True, slots=True)
class EnvironmentCoordinationRequest:
    coordination_id: str
    operation: str
    payload: JsonInput
    context: ExecutionContext

    def __post_init__(self) -> None:
        if not self.coordination_id.strip() or not self.operation.strip():
            raise ValueError("environment coordination identity is required")
        object.__setattr__(self, "payload", freeze_json(self.payload))


@dataclass(frozen=True, slots=True)
class EnvironmentCoordinationReceipt:
    coordination_id: str
    accepted: bool
    evidence_refs: tuple[str, ...] = ()
    diagnostics: dict[str, JsonValue] = field(default_factory=dict)


@runtime_checkable
class EnvironmentCoordinationPort(Protocol):
    def coordinate(self, request: EnvironmentCoordinationRequest) -> EnvironmentCoordinationReceipt: ...


__all__ = [
    "EnvironmentActionLifecycle",
    "EnvironmentActionPhase",
    "EnvironmentCapabilityDescriptor",
    "EnvironmentCoordinationPort",
    "EnvironmentCoordinationReceipt",
    "EnvironmentCoordinationRequest",
    "EnvironmentQuery",
    "EnvironmentQueryKind",
    "EnvironmentQueryPort",
    "EnvironmentQueryResult",
    "EnvironmentRawEventReceipt",
    "EnvironmentRawEventRecord",
    "EnvironmentRawRecordSinkPort",
]
