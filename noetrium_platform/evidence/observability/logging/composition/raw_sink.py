from __future__ import annotations

from noetrium_platform.foundation.kernel.kernel import ExecutionContext, canonical_bytes
from noetrium_platform.evidence.observability.capture.api import RawObservationEnvelope
from noetrium_platform.evidence.observability.capture.runtime import (
    RegistryBoundRawObservationGateway,
)
from noetrium_platform.evidence.observability.logging.record.api import LogRecord
from noetrium_platform.evidence.observability.logging.sink.api import LogSinkPort


class RegistryBoundRawLogSink(LogSinkPort):
    """Mirror semantic logs into the single lossless raw observation lake."""

    producer_id = "observability.logging.raw-mirror.v1"

    def __init__(self, sink: LogSinkPort, gateway: RegistryBoundRawObservationGateway) -> None:
        self._sink = sink
        self._gateway = gateway

    def append(self, record: LogRecord) -> None:
        if not record.address.system_path:
            raise ValueError("raw log mirror requires a registry-bound system path")
        system = record.address.system_path[-1]
        payload = {
            "operation_type": record.event,
            "status": record.level.value,
            "log_id": record.log_id,
            "logger": record.logger,
            "message": record.message,
            "created_at": record.created_at,
            "attributes": dict(record.attributes),
            "correlation_refs": record.correlation_refs,
            "failure_refs": record.failure_refs,
            "artifact_refs": record.artifact_refs,
            "system_path": tuple(item.key for item in record.address.system_path),
        }
        context = ExecutionContext(
            run_id=record.address.scope.key,
            trace_id=record.address.trace_id or record.log_id,
            span_id=record.address.span_id or record.log_id,
            operation_id=record.address.operation_id,
            component_id=record.address.component_id,
        )
        self._gateway.capture(
            RawObservationEnvelope(
                event_id=record.log_id,
                family="operation.raw",
                context=context,
                system=system,
                producer_id=self.producer_id,
                payload=payload,
                raw_payload=canonical_bytes(payload),
                occurred_at=record.created_at,
                recorded_at=record.created_at,
                content_type="application/json",
                status=record.level.value,
                outcome=record.level.value,
                correlation_id=context.trace_id,
                dimensions={"logger": record.logger},
            )
        )
        self._sink.append(record)


__all__ = ["RegistryBoundRawLogSink"]
