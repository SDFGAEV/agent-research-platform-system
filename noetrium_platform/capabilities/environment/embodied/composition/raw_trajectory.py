from __future__ import annotations

from collections.abc import Callable
import time

from noetrium_platform.evidence.observability.capture.api import (
    RawObservationEnvelope,
    RawObservationReceipt,
)
from noetrium_platform.evidence.observability.capture.runtime.gateway import (
    RegistryBoundRawObservationGateway,
)
from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, thaw_json

from ..api import EmbodiedCaptureReceipt, EmbodiedEvent


class RegistryBoundEmbodiedTrajectorySink:
    """Lossless embodied trajectory adapter backed by the platform raw lake."""

    FAMILY = "embodied.trajectory.raw"
    SYSTEM = SystemIdentity("environment", ("embodied",))

    def __init__(
        self,
        gateway: RegistryBoundRawObservationGateway,
        *,
        producer_id: str,
        producer_version: str = "unknown",
        producer_instance_id: str = "unknown",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not producer_id.strip():
            raise ValueError("producer_id must be non-empty")
        self._gateway = gateway
        self._producer_id = producer_id
        self._producer_version = producer_version
        self._producer_instance_id = producer_instance_id
        self._clock = clock
        gateway.register_producer(producer_id, self.SYSTEM)

    def capture(
        self, event: EmbodiedEvent, context: ExecutionContext
    ) -> EmbodiedCaptureReceipt:
        projection = dict(thaw_json(event.normalized_payload))
        projection.update(
            {
                "kind": event.kind.value,
                "episode_id": event.episode_id,
                "status": event.status,
                "event_id": event.event_id,
                "sequence": event.sequence,
                "event_time_ns": event.event_time_ns,
                "event_digest": event.event_digest,
                "source_id": event.source_id,
                "embodiment_id": event.embodiment_id,
                "environment_id": event.environment_id,
                "task_id": event.task_id,
                "step_index": event.step_index,
                "sensor_id": event.sensor_id,
                "action_id": event.action_id,
                "outcome": event.outcome,
                "terminated": event.terminated,
                "truncated": event.truncated,
                "dimensions": thaw_json(event.dimensions),
            }
        )
        receipt = self._gateway.capture(
            RawObservationEnvelope(
                event_id=event.event_id,
                family=self.FAMILY,
                context=context,
                system=self.SYSTEM,
                producer_id=self._producer_id,
                producer_version=self._producer_version,
                producer_instance_id=self._producer_instance_id,
                payload=projection,
                raw_payload=event.raw_payload,
                occurred_at=event.event_time_ns / 1_000_000_000,
                recorded_at=self._clock(),
                status=event.status,
                outcome=event.outcome,
                stream_id=event.episode_id,
                dimensions=event.dimensions,
            )
        )
        return EmbodiedCaptureReceipt(
            event_id=event.event_id,
            episode_id=event.episode_id,
            sequence=event.sequence,
            family=self.FAMILY,
            record_sha256=receipt.record_sha256,
            raw_payload_sha256=event.raw_payload_sha256,
        )


__all__ = ["RegistryBoundEmbodiedTrajectorySink"]
