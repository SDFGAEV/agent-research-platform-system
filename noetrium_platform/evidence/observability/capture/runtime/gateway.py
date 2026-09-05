from __future__ import annotations

import base64
from threading import RLock
import time

from noetrium_platform.foundation.governance.system_registry.api import SystemRegistryPort
from noetrium_platform.foundation.kernel.kernel import JsonObject, thaw_json

from ..api.contracts import RawCaptureHealth, RawObservationEnvelope, RawObservationReceipt
from .lake import RawObservationLake


class RegistryBoundRawObservationGateway:
    """Single capture gateway joining lossless facts to system topology."""

    def __init__(self, lake: RawObservationLake, systems: SystemRegistryPort) -> None:
        self._lake = lake
        self._systems = systems
        self._lock = RLock()
        self._accepted = 0
        self._duplicates = 0
        self._rejected = 0
        self._seen: set[str] = set()
        self._registered: dict[str, str] = {}
        self._observed: set[str] = set()

    def register_producer(self, producer_id: str, system: object) -> None:
        if not producer_id.strip():
            raise ValueError("producer_id must be non-empty")
        identity = self._systems.validate(system)
        with self._lock:
            self._registered[producer_id] = identity.identity.key

    def capture(self, envelope: RawObservationEnvelope) -> RawObservationReceipt:
        try:
            identity = self._systems.validate(envelope.system)
            payload = self._payload(envelope, identity.identity.key)
            with self._lock:
                duplicate = envelope.event_id in self._seen
                if duplicate:
                    self._duplicates += 1
                self._observed.add(envelope.producer_id)
            receipt = self._lake.append_once(
                envelope.context,
                envelope.family,
                payload,
                idempotency_key=envelope.event_id,
                timestamp=envelope.recorded_at,
            )
            with self._lock:
                self._seen.add(envelope.event_id)
                self._accepted += 1
            return receipt
        except BaseException:
            with self._lock:
                self._rejected += 1
            raise

    def append(self, envelope: RawObservationEnvelope) -> RawObservationReceipt:
        return self.capture(envelope)

    def _payload(self, envelope: RawObservationEnvelope, system_key: str) -> JsonObject:
        payload = dict(thaw_json(envelope.payload))
        if "__capture" in payload:
            raise ValueError("payload key '__capture' is reserved by capture")
        capture = {
            "event_id": envelope.event_id,
            "system": system_key,
            "topology_generation": self._systems.generation,
            "topology_digest": self._systems.topology_digest,
            "producer_id": envelope.producer_id,
            "producer_version": envelope.producer_version,
            "producer_instance_id": envelope.producer_instance_id,
            "occurred_at": envelope.occurred_at,
            "recorded_at": envelope.recorded_at,
            "status": envelope.status,
            "outcome": envelope.outcome,
            "stream_id": envelope.stream_id,
            "attempt_id": envelope.attempt_id,
            "correlation_id": envelope.correlation_id,
            "causation_id": envelope.causation_id,
            "parent_event_ids": envelope.parent_event_ids,
            "dimensions": thaw_json(envelope.dimensions),
            "source_location": thaw_json(envelope.source_location),
            "content_type": envelope.content_type,
            "content_encoding": envelope.content_encoding,
            "sampled": envelope.sampled,
            "sampling_rate": envelope.sampling_rate,
            "raw_payload_b64": base64.b64encode(envelope.raw_payload).decode("ascii"),
            "raw_payload_sha256": envelope.raw_payload_sha256,
        }
        payload["__capture"] = capture
        return payload

    def health(self) -> RawCaptureHealth:
        with self._lock:
            missing = tuple(sorted(set(self._registered) - self._observed))
            return RawCaptureHealth(
                self._accepted,
                self._duplicates,
                self._rejected,
                self._systems.generation,
                self._systems.topology_digest,
                tuple(sorted(self._registered)),
                tuple(sorted(self._observed)),
                missing,
            )

    def close(self) -> None:
        self._lake.close()


__all__ = ["RegistryBoundRawObservationGateway"]
