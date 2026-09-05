from __future__ import annotations

from pathlib import Path
import base64
import tempfile

from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity
from noetrium_platform.foundation.governance.system_registry.runtime import (
    build_default_system_registry,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext
from noetrium_platform.evidence.observability.capture.api import RawObservationEnvelope
from noetrium_platform.evidence.observability.capture.runtime import (
    RegistryBoundRawObservationGateway,
)
from tests._concurrency_support import drain_test_concurrency_runtimes, raw_observation_lake


def test_registry_bound_gateway_preserves_source_bytes_and_topology() -> None:
    try:
        with tempfile.TemporaryDirectory() as td:
            lake = raw_observation_lake(Path(td))
            systems = build_default_system_registry()
            gateway = RegistryBoundRawObservationGateway(lake, systems)
            system = SystemIdentity("model", ("serving", "endpoint"))
            source = b"{" + b'"token":1' + b"}\x00"
            context = ExecutionContext("run-capture", "trace-capture", "span-capture")
            receipt = gateway.capture(
                RawObservationEnvelope(
                    event_id="event-capture-1",
                    family="llm.attempt.raw",
                    context=context,
                    system=system,
                    producer_id="test.capture",
                    payload={
                        "role": "assistant",
                        "model": "test-model",
                        "endpoint": "deployment-1",
                        "attempt": 1,
                        "status": "ok",
                    },
                    raw_payload=source,
                    occurred_at=1.0,
                    recorded_at=2.0,
                    content_type="application/json",
                )
            )
            row = lake.read("run-capture", "llm.attempt.raw")[0]
            capture = row["payload"]["__capture"]
            assert receipt.sequence == 1
            assert base64.b64decode(capture["raw_payload_b64"]) == source
            assert capture["system"] == system.key
            assert capture["topology_digest"] == systems.topology_digest
            assert gateway.health().accepted == 1
            lake.close()
    finally:
        drain_test_concurrency_runtimes()


def test_registry_bound_gateway_rejects_unknown_system() -> None:
    try:
        with tempfile.TemporaryDirectory() as td:
            lake = raw_observation_lake(Path(td))
            gateway = RegistryBoundRawObservationGateway(
                lake, build_default_system_registry()
            )
            envelope = RawObservationEnvelope(
                event_id="unknown-system-event",
                family="study.raw",
                context=ExecutionContext("run-unknown", "trace", "span"),
                system=SystemIdentity("unknown"),
                producer_id="test.capture",
                payload={"kind": "task", "status": "running"},
                raw_payload=b"{}",
                occurred_at=1.0,
                recorded_at=1.0,
            )
            try:
                gateway.capture(envelope)
            except KeyError:
                pass
            else:
                raise AssertionError("unknown system should fail closed")
            assert gateway.health().rejected == 1
            lake.close()
    finally:
        drain_test_concurrency_runtimes()

def test_registry_bound_raw_log_sink_mirrors_semantic_log() -> None:
    from noetrium_platform.evidence.observability.logging.composition import (
        LogQueryBinding,
        LogSinkBinding,
        compose_logging_system,
    )
    from noetrium_platform.evidence.observability.logging.storage.runtime import (
        InMemoryLogStore,
    )
    from noetrium_platform.evidence.observability.logging.record.api import LogLevel
    from noetrium_platform.foundation.kernel.kernel import canonical_digest
    from noetrium_platform.composition.platform_meta import build_in_memory_platform_meta
    from noetrium_platform.evidence.observability.logging.context.api import DiagnosticAddress
    from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE

    try:
        with tempfile.TemporaryDirectory() as td:
            lake = raw_observation_lake(Path(td))
            meta = build_default_system_registry()
            gateway = RegistryBoundRawObservationGateway(lake, meta)
            store = InMemoryLogStore()
            composition = compose_logging_system(
                sink=LogSinkBinding(store, "test-store", canonical_digest("store")),
                query=LogQueryBinding(store, "test-store", canonical_digest("store")),
                planner=build_in_memory_platform_meta().capability_composition,
                systems=meta,
                raw_gateway=gateway,
            )
            writer = composition.logging.bind(
                logger="test.logger",
                address=DiagnosticAddress(
                    scope_path=(PLATFORM_SCOPE,),
                    system_path=(SystemIdentity("platform"),),
                    trace_id="trace-log",
                ),
            )
            writer.log(LogLevel.INFO, event="CAPTURED", message="captured")
            assert len(store.query(event="CAPTURED")) == 1
            assert len(lake.read(PLATFORM_SCOPE.key, "operation.raw")) == 1
            lake.close()
    finally:
        drain_test_concurrency_runtimes()
