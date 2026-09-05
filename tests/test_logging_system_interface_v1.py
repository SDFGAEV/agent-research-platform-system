from __future__ import annotations

import pytest

from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity
from noetrium_platform.evidence.observability.logging.composition import (
    ExceptionDescriptorBinding,
    LogQueryBinding,
    LogSinkBinding,
    compose_logging_system,
)
from noetrium_platform.evidence.observability.logging.context.api import DiagnosticAddress
from noetrium_platform.evidence.observability.logging.record.api import ExceptionDescriptorPort, LogLevel
from noetrium_platform.evidence.observability.logging.storage.runtime import InMemoryLogStore
from noetrium_platform.composition.platform_meta import build_in_memory_platform_meta
from noetrium_platform.foundation.kernel.kernel import canonical_digest
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE


class MarkerExceptionDescriptor(ExceptionDescriptorPort):
    def describe(self, exc: BaseException):
        from noetrium_platform.foundation.kernel.kernel.errors import SafeExceptionDescriptor

        return SafeExceptionDescriptor(
            error_type="custom",
            qualified_type="custom.Error",
            safe_message="custom-safe",
            error_digest="d" * 64,
        )


def address() -> DiagnosticAddress:
    return DiagnosticAddress(
        scope_path=(PLATFORM_SCOPE,),
        system_path=(SystemIdentity("platform"),),
        component_id="test.component",
        trace_id="trace-1",
    )


def compose_test_logging(
    store: InMemoryLogStore,
    *,
    exception_descriptor: ExceptionDescriptorBinding | None = None,
    metrics=None,
):
    meta = build_in_memory_platform_meta()
    return compose_logging_system(
        sink=LogSinkBinding(
            store,
            "tests.in-memory-log-store.v1",
            canonical_digest({"store": "in-memory"}),
        ),
        query=LogQueryBinding(
            store,
            "tests.in-memory-log-store.v1",
            canonical_digest({"store": "in-memory"}),
        ),
        exception_descriptor=exception_descriptor,
        planner=meta.capability_composition,
        systems=meta.systems,
        metrics=metrics,
    )


def test_logging_system_binds_internal_writer_and_unified_query() -> None:
    store = InMemoryLogStore()
    composition = compose_test_logging(store)
    logging = composition.logging
    assert {
        (edge.requirement.requirement_id, edge.offer.owner.key)
        for edge in composition.plan.edges
    } == {
        ("sink", "system:observability/logging/storage"),
        ("query", "system:observability/logging/storage"),
        ("exception-descriptor", "system:observability/logging/record"),
    }
    writer = logging.bind(logger="platform.test", address=address())
    writer.child(component_id="child").failure(
        event="FAILURE_OBSERVED",
        message="failure reference",
        failure_id="failure-1",
    )

    rows = logging.query(trace_id="trace-1")
    assert len(rows) == 1
    assert rows[0].failure_refs == ("failure-1",)
    assert dict(rows[0].attributes) == {}


class _MetricSink:
    def observe(self, context, name: str, value: float, **dimensions: str) -> object:
        del context, name, value, dimensions
        return None


def test_logging_composition_activates_registry_observations_when_metrics_are_bound() -> None:
    store = InMemoryLogStore()
    composition = compose_test_logging(store, metrics=_MetricSink())
    assert composition.observations is not None
    assert len(composition.observations.bindings()) > 0


def test_exception_policy_is_injected_at_logging_composition() -> None:
    store = InMemoryLogStore()
    logging = compose_test_logging(
        store,
        exception_descriptor=ExceptionDescriptorBinding(
            MarkerExceptionDescriptor(),
            "tests.marker-exception-descriptor.v1",
            canonical_digest({"policy": "marker"}),
        ),
    ).logging
    writer = logging.bind(logger="platform.test", address=address())
    writer.exception(event="BROKEN", message="broken", exc=RuntimeError("raw"))
    row = store.query(event="BROKEN")[0]
    assert row.exception is not None
    assert row.exception.safe_message == "custom-safe"


def test_logging_binding_rejects_unregistered_system_identity() -> None:
    store = InMemoryLogStore()
    logging = compose_test_logging(store).logging
    with pytest.raises(KeyError):
        logging.bind(
            logger="platform.test",
            address=DiagnosticAddress(
                scope_path=(PLATFORM_SCOPE,),
                system_path=(SystemIdentity("unknown"),),
            ),
        )
