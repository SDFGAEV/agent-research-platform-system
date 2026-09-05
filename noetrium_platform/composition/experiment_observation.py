from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
import time

from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, canonical_bytes, thaw_json
from noetrium_platform.evidence.observability.capture.api import RawObservationEnvelope
from noetrium_platform.evidence.observability.capture.runtime import (
    RegistryBoundRawObservationGateway,
)
from noetrium_platform.research.experimentation.experiment.api import (
    ObservationEnvelope,
    ObservationSinkPort,
    RawRecord,
    RawRecordStorePort,
)


class LakeBackedExperimentObservationLedger(ObservationSinkPort):
    """Experiment observation view that emits every fact to the raw lake."""

    def __init__(
        self,
        gateway: RegistryBoundRawObservationGateway,
        *,
        system: SystemIdentity = SystemIdentity("experimentation", ("experiment",)),
        producer_id: str = "experimentation.observation-ledger.v1",
    ) -> None:
        self._gateway = gateway
        self._system = system
        self._producer_id = producer_id
        self._gateway.register_producer(self._producer_id, self._system)
        self._lock = RLock()
        self._observations: list[ObservationEnvelope] = []

    def append(self, observation: ObservationEnvelope) -> None:
        context = observation_context(observation)
        payload = {
            "kind": observation.kind.value,
            "status": "observed",
            "experiment_id": observation.experiment_id,
            "run_id": observation.run_id,
            "unit_id": observation.unit_id,
            "schema_id": observation.schema_id,
            "producer_id": observation.producer_id,
            "logical_time": observation.logical_time,
            "observation_digest": observation.observation_digest,
            "payload": observation.payload,
        }
        self._gateway.capture(
            RawObservationEnvelope(
                event_id=observation.observation_digest,
                family="study.raw",
                context=context,
                system=self._system,
                producer_id=self._producer_id,
                payload=payload,
                raw_payload=canonical_bytes(payload),
                occurred_at=time.time(),
                recorded_at=time.time(),
                content_type="application/json",
                status="observed",
                outcome="observed",
                stream_id=f"{observation.run_id}:{observation.unit_id}",
                correlation_id=context.trace_id,
                dimensions={"observation_kind": observation.kind.value},
            )
        )
        with self._lock:
            self._observations.append(observation)

    def snapshot(self) -> tuple[ObservationEnvelope, ...]:
        with self._lock:
            return tuple(self._observations)

class LakeBackedRawRecordStore(RawRecordStorePort):
    """Adapt the experiment projection API onto the authoritative raw lake."""

    def __init__(
        self,
        gateway: RegistryBoundRawObservationGateway,
        *,
        system: SystemIdentity = SystemIdentity("experimentation", ("experiment",)),
    ) -> None:
        self._gateway = gateway
        self._system = system
        self._records: list[RawRecord] = []
        self._lock = RLock()
        self._gateway.register_producer("experimentation.raw-record-adapter.v1", system)

    def append_raw_record(self, record: RawRecord) -> None:
        payload = record.payload if isinstance(record.payload, Mapping) else {"value": record.payload}
        event_payload = {
            "kind": record.record_type,
            "status": record.status,
            "raw_record_digest": record.record_digest,
            "payload": thaw_json(payload),
        }
        context = ExecutionContext(
            run_id=record.run_id,
            trace_id=record.trace_id or record.correlation_id,
            span_id=record.span_id or record.record_digest,
            study_id=record.experiment_id,
            task_id=record.unit_id,
        )
        self._gateway.capture(
            RawObservationEnvelope(
                event_id=record.record_digest,
                family="study.raw",
                context=context,
                system=self._system,
                producer_id="experimentation.raw-record-adapter.v1",
                payload=event_payload,
                raw_payload=record.raw_payload,
                occurred_at=0.0,
                recorded_at=0.0,
                content_type=record.content_type,
                content_encoding=record.content_encoding,
                status=record.status,
                outcome=record.outcome,
                dimensions=thaw_json(record.dimensions),
            )
        )
        with self._lock:
            self._records.append(record)

    def raw_snapshot(self) -> tuple[RawRecord, ...]:
        with self._lock:
            return tuple(self._records)


def observation_context(observation: ObservationEnvelope):
    return ExecutionContext(
        run_id=observation.run_id,
        trace_id=f"observation:{observation.observation_digest}",
        span_id=f"observation:{observation.sequence}",
        study_id=observation.experiment_id,
        task_id=observation.unit_id,
    )


__all__ = ["LakeBackedExperimentObservationLedger", "LakeBackedRawRecordStore"]
