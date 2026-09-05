from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from enum import StrEnum

from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonObject, JsonValue, freeze_json


class RetentionClass(StrEnum):
    HOT_DEBUG = "hot_debug"
    RUN_DURABLE = "run_durable"
    SCIENTIFIC_DURABLE = "scientific_durable"


@dataclass(frozen=True, slots=True)
class RawObservationSchema:
    family: str
    schema_version: str
    required_fields: tuple[str, ...]
    retention: RetentionClass
    description: str

    def __post_init__(self) -> None:
        if not self.family.strip() or not self.schema_version.strip():
            raise ValueError("raw observation family and schema_version must be non-empty")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("raw observation required_fields must be unique")
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("raw observation required_fields must be non-empty")


@dataclass(frozen=True, slots=True)
class RawObservationReceipt:
    family: str
    schema_version: str
    run_id: str
    segment_path: str
    sequence: int
    record_sha256: str
    bytes_written: int

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.family, self.schema_version, self.run_id, self.segment_path)):
            raise ValueError("raw observation receipt identity fields must be non-empty")
        if self.sequence <= 0 or self.bytes_written <= 0:
            raise ValueError("raw observation receipt sequence and bytes_written must be positive")
        if len(self.record_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.record_sha256):
            raise ValueError("raw observation receipt record_sha256 must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class RawObservationEnvelope:
    """Registry-bound lossless event submitted to the existing raw lake.

    ``raw_payload`` is retained byte-for-byte. ``payload`` and the metadata
    namespaces are projections only and can be extended without changing the
    authoritative capture path.
    """

    event_id: str
    family: str
    context: ExecutionContext
    system: SystemIdentity
    producer_id: str
    payload: JsonObject
    raw_payload: bytes
    occurred_at: float
    recorded_at: float
    status: str = "ok"
    outcome: str | None = None
    producer_version: str = "unknown"
    producer_instance_id: str = "unknown"
    stream_id: str = ""
    attempt_id: str = ""
    correlation_id: str = ""
    causation_id: str | None = None
    parent_event_ids: tuple[str, ...] = ()
    dimensions: Mapping[str, JsonValue] = field(default_factory=dict)
    source_location: Mapping[str, JsonValue] = field(default_factory=dict)
    content_type: str = "application/octet-stream"
    content_encoding: str = "identity"
    sampled: bool = True
    sampling_rate: float = 1.0

    def __post_init__(self) -> None:
        for name, value in (("event_id", self.event_id), ("family", self.family),
                            ("producer_id", self.producer_id), ("status", self.status),
                            ("producer_version", self.producer_version),
                            ("producer_instance_id", self.producer_instance_id),
                            ("content_type", self.content_type),
                            ("content_encoding", self.content_encoding)):
            if type(value) is not str or not value.strip():
                raise ValueError(f"raw observation {name} must be non-empty")
        if not isinstance(self.context, ExecutionContext):
            raise TypeError("raw observation context must be ExecutionContext")
        if not isinstance(self.system, SystemIdentity):
            raise TypeError("raw observation system must be SystemIdentity")
        if type(self.raw_payload) is not bytes:
            raise TypeError("raw observation raw_payload must be exact bytes")
        payload = freeze_json(self.payload)
        dimensions = freeze_json(self.dimensions)
        source_location = freeze_json(self.source_location)
        if not isinstance(payload, Mapping):
            raise TypeError("raw observation payload must be a mapping")
        if not isinstance(dimensions, Mapping) or not isinstance(source_location, Mapping):
            raise TypeError("raw observation dimensions and source_location must be mappings")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "source_location", source_location)
        for name, value in (("occurred_at", self.occurred_at), ("recorded_at", self.recorded_at),
                            ("sampling_rate", self.sampling_rate)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))):
                raise TypeError(f"raw observation {name} must be finite numeric")
        if not 0.0 < float(self.sampling_rate) <= 1.0:
            raise ValueError("raw observation sampling_rate must be in (0, 1]")
        if type(self.sampled) is not bool:
            raise TypeError("raw observation sampled must be boolean")
        if not self.stream_id:
            object.__setattr__(self, "stream_id", self.context.run_id)
        if not self.attempt_id:
            object.__setattr__(self, "attempt_id", "attempt-0")
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", self.context.trace_id)
        if any(type(item) is not str or not item.strip() for item in self.parent_event_ids):
            raise ValueError("raw observation parent_event_ids must contain non-empty strings")

    @property
    def raw_payload_sha256(self) -> str:
        import hashlib
        return hashlib.sha256(self.raw_payload).hexdigest()


@dataclass(frozen=True, slots=True)
class RawCaptureHealth:
    accepted: int
    duplicates: int
    rejected: int
    topology_generation: int
    topology_digest: str
    registered_producers: tuple[str, ...] = ()
    observed_producers: tuple[str, ...] = ()
    missing_producers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("accepted", self.accepted), ("duplicates", self.duplicates), ("rejected", self.rejected), ("topology_generation", self.topology_generation)):
            if type(value) is not int or value < 0:
                raise ValueError(f"raw capture health {name} must be non-negative")
        if len(self.topology_digest) != 64 or any(char not in "0123456789abcdef" for char in self.topology_digest):
            raise ValueError("raw capture health topology_digest must be lowercase SHA-256")


class RawObservationCorruptionError(RuntimeError):
    pass
