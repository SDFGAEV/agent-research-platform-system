from __future__ import annotations

import pytest

from noetrium_platform.foundation.governance.system_registry.api import (
    AuthorityDescriptor,
    SystemDescriptor,
    SystemIdentity,
    SystemLayer,
    SystemRegistryChange,
)
from noetrium_platform.foundation.governance.system_registry.runtime import (
    SystemRegistryNotFound,
    build_default_system_registry,
)
from noetrium_platform.evidence.observability.telemetry.metric.api import (
    MetricDefinition,
    MetricKind,
)
from noetrium_platform.evidence.observability.telemetry.metric.runtime import MetricRegistry
from noetrium_platform.evidence.observability.logging.record.runtime import (
    SystemBoundMetricSink,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


def test_registry_exposes_stable_generation_and_digest() -> None:
    registry = build_default_system_registry()
    assert registry.generation == len(registry.list())
    first = registry.topology_digest
    assert first
    assert registry.validate(SystemIdentity("platform")).identity.key == "platform"
    assert registry.topology_digest == first


def test_registry_validation_fails_closed_for_unknown_identity() -> None:
    registry = build_default_system_registry()
    with pytest.raises(SystemRegistryNotFound):
        registry.validate(SystemIdentity("unknown"))


class _Sink:
    def __init__(self) -> None:
        self.rows: list[tuple[object, str, float, dict[str, str]]] = []

    def observe(self, context, name: str, value: float, **dimensions: str) -> object:
        self.rows.append((context, name, value, dimensions))
        return len(self.rows)


def test_system_bound_metric_sink_adds_topology_identity() -> None:
    registry = build_default_system_registry()
    sink = _Sink()
    bound = SystemBoundMetricSink(
        sink,
        registry,
        SystemIdentity("observability", ("logging",)),
    )
    context = ExecutionContext("run", "trace", "span")
    bound.observe(context, "runtime.control.action.count", 1.0)
    assert sink.rows[0][3] == {
        "system": "observability/logging",
        "topology_generation": str(registry.generation),
    }
    assert bound.topology_digest == registry.topology_digest


def test_metric_registry_accepts_reserved_topology_dimensions() -> None:
    registry = MetricRegistry()
    registry.register(
        MetricDefinition(
            "test.latency",
            MetricKind.HISTOGRAM,
            "seconds",
            (),
            "test metric",
        )
    )
    registry.validate_observation(
        "test.latency",
        0.25,
        {
            "system": "observability/logging",
            "topology_generation": "160",
        },
    )


class _Logging:
    def __init__(self) -> None:
        self.addresses = []

    def bind(self, *, logger: str, address):
        self.addresses.append((logger, address))
        return object()


def test_observation_factory_binds_the_complete_registered_topology() -> None:
    from noetrium_platform.evidence.observability.logging.record.runtime import (
        SystemObservationFactory,
    )

    systems = build_default_system_registry()
    logging = _Logging()
    factory = SystemObservationFactory(systems, logging, _Sink())
    bindings = factory.bind_all(trace_id="trace")
    assert len(bindings) == len(systems.list())
    assert bindings[0].topology_generation == systems.generation
    assert bindings[0].topology_digest == systems.topology_digest
    assert bindings[0].address.system_path[-1] == bindings[0].descriptor.identity
    child = factory.bind(SystemIdentity("observability", ("logging",)))
    assert tuple(item.key for item in child.address.system_path) == (
        "observability",
        "observability/logging",
    )


def test_observation_factory_rebinds_when_registry_grows() -> None:
    from noetrium_platform.evidence.observability.logging.record.runtime import (
        SystemObservationFactory,
    )

    systems = build_default_system_registry()
    logging = _Logging()
    factory = SystemObservationFactory(systems, logging, _Sink())
    initial = factory.bind_all(trace_id="trace")

    descriptor = SystemDescriptor(
        identity=SystemIdentity("governance", ("dynamic-observability",)),
        layer=SystemLayer.GOVERNANCE,
        package_prefix="noetrium_platform.foundation.governance.dynamic_observability",
        authorities=(AuthorityDescriptor("dynamic_observability_authority"),),
        owns="dynamic observation test node",
        must_not_own="business behavior",
    )
    systems.register(descriptor)

    current = factory.bindings()
    assert len(initial) == len(systems.list()) - 1
    assert len(current) == len(systems.list())
    dynamic = next(item for item in current if item.descriptor.identity == descriptor.identity)
    assert dynamic.topology_generation == systems.generation
    assert dynamic.topology_digest == systems.topology_digest

    logging_count = len(logging.addresses)
    factory.close()
    systems.register(
        SystemDescriptor(
            identity=SystemIdentity("governance", ("after-close",)),
            layer=SystemLayer.GOVERNANCE,
            package_prefix="noetrium_platform.foundation.governance.after_close",
            authorities=(AuthorityDescriptor("after_close_authority"),),
            owns="closed factory test node",
            must_not_own="business behavior",
        )
    )
    assert len(logging.addresses) == logging_count


def test_registry_batches_topology_notifications() -> None:
    systems = build_default_system_registry()
    events: list[object] = []
    systems.subscribe(events.append)
    descriptors = tuple(
        SystemDescriptor(
            identity=SystemIdentity("governance", (name,)),
            layer=SystemLayer.GOVERNANCE,
            package_prefix=f"noetrium_platform.foundation.governance.{name}",
            authorities=(AuthorityDescriptor(f"{name}_authority"),),
            owns="batch notification test node",
            must_not_own="business behavior",
        )
        for name in ("batch_a", "batch_b")
    )

    registered = systems.register_many(descriptors)

    assert tuple(item.identity.key for item in registered) == (
        "governance/batch_a",
        "governance/batch_b",
    )
    assert len(events) == 1
    assert isinstance(events[0], SystemRegistryChange)
    change = events[0]
    assert tuple(item.identity.key for item in change.registered) == (
        "governance/batch_a",
        "governance/batch_b",
    )
    assert change.generation == systems.generation
    assert change.topology_digest == systems.topology_digest
