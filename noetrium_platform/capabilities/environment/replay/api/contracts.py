from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib

from noetrium_platform.foundation.kernel.kernel import (
    JsonObject,
    JsonValue,
    canonical_digest,
    freeze_json,
    thaw_json,
)


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    event_id: str
    sequence: int
    kind: str
    raw_payload: bytes
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    dimensions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.kind.strip():
            raise ValueError("replay event identity is incomplete")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("replay event sequence must be positive")
        if type(self.raw_payload) is not bytes:
            raise TypeError("replay event raw_payload must be bytes")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "dimensions", freeze_json(self.dimensions))

    @property
    def raw_payload_sha256(self) -> str:
        return hashlib.sha256(self.raw_payload).hexdigest()

    @property
    def event_digest(self) -> str:
        return canonical_digest({
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "raw_payload_sha256": self.raw_payload_sha256,
            "payload": thaw_json(self.payload),
            "dimensions": thaw_json(self.dimensions),
        })


@dataclass(frozen=True, slots=True)
class ReplayTrace:
    trace_id: str
    environment_id: str
    events: tuple[ReplayEvent, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trace_id.strip() or not self.environment_id.strip():
            raise ValueError("replay trace identity is incomplete")
        if not self.events:
            raise ValueError("replay trace must contain events")
        sequences = tuple(event.sequence for event in self.events)
        if sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences)):
            raise ValueError("replay event sequences must be increasing and unique")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    @property
    def trace_digest(self) -> str:
        return canonical_digest({
            "trace_id": self.trace_id,
            "environment_id": self.environment_id,
            "events": [event.event_digest for event in self.events],
            "metadata": thaw_json(self.metadata),
        })

    def record(self) -> JsonObject:
        return {
            "trace_id": self.trace_id,
            "environment_id": self.environment_id,
            "trace_digest": self.trace_digest,
            "event_count": len(self.events),
            "metadata": thaw_json(self.metadata),
        }


__all__ = ["ReplayEvent", "ReplayTrace"]
