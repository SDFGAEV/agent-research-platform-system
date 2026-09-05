from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
    SystemRegistryChange,
    SystemRegistryPort,
)
from noetrium_platform.evidence.observability.logging.query.api import LogQueryPort
from noetrium_platform.evidence.observability.logging.record.api import (
    ExceptionDescriptorPort,
    LogLevel,
    LogRecord,
    LogWriterPort,
    LoggingSystemPort,
)
from noetrium_platform.evidence.observability.logging.record.runtime.logger import StructuredLogger
from noetrium_platform.evidence.observability.logging.sink.api import LogSinkPort
from noetrium_platform.evidence.observability.logging.context.api import DiagnosticAddress
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE, ScopeIdentity
from noetrium_platform.foundation.kernel.kernel import JsonValue


class StructuredLoggingSystem(LoggingSystemPort):
    """Composes leaf seams without owning storage or query implementations."""

    def __init__(
        self,
        sink: LogSinkPort,
        query: LogQueryPort,
        *,
        systems: SystemRegistryPort,
        exception_descriptor: ExceptionDescriptorPort | None = None,
    ) -> None:
        self._sink = sink
        self._query = query
        self._systems = systems
        self._exception_descriptor = exception_descriptor

    def _validate_address(self, address: DiagnosticAddress) -> None:
        previous: SystemIdentity | None = None
        for identity in address.system_path:
            self._systems.validate(identity)
            if previous is not None and identity.parent_key != previous.key:
                raise ValueError(
                    f"diagnostic system path is not contiguous: {previous.key!r} -> {identity.key!r}"
                )
            previous = identity

    def bind(
        self,
        *,
        logger: str,
        address: DiagnosticAddress,
        attributes: Mapping[str, JsonValue] | None = None,
    ) -> LogWriterPort:
        self._validate_address(address)
        return StructuredLogger(
            self._sink,
            logger=logger,
            address=address,
            attributes=attributes,
            exception_descriptor=self._exception_descriptor,
        )

    def query(
        self,
        *,
        scope: ScopeIdentity | None = None,
        system: SystemIdentity | None = None,
        component_id: str | None = None,
        trace_id: str | None = None,
        level_at_least: LogLevel | None = None,
        event: str | None = None,
        limit: int = 1000,
    ) -> tuple[LogRecord, ...]:
        if system is not None:
            self._systems.validate(system)
        return self._query.query(
            scope=scope,
            system=system,
            component_id=component_id,
            trace_id=trace_id,
            level_at_least=level_at_least,
            event=event,
            limit=limit,
        )


@dataclass(frozen=True, slots=True)
class SystemObservationBinding:
    """One topology-anchored observation bundle for a registered system node."""

    descriptor: SystemDescriptor
    address: DiagnosticAddress
    logger: LogWriterPort
    metrics: "SystemBoundMetricSink"
    topology_generation: int
    topology_digest: str


class SystemBoundMetricSink:
    """Inject validated registry identity into every metric observation."""

    def __init__(
        self,
        sink: object,
        systems: SystemRegistryPort,
        system: SystemIdentity,
    ) -> None:
        self._sink = sink
        self._systems = systems
        self._descriptor = systems.validate(system)

    @property
    def system(self) -> SystemIdentity:
        return self._descriptor.identity

    @property
    def topology_generation(self) -> int:
        return self._systems.generation

    @property
    def topology_digest(self) -> str:
        return self._systems.topology_digest

    def observe(
        self,
        context: object,
        name: str,
        value: float,
        **dimensions: str,
    ) -> object:
        existing = dimensions.get("system")
        if existing is not None and existing != self.system.key:
            raise ValueError(
                f"metric system dimension conflicts with bound system: {existing!r}"
            )
        generation = str(self.topology_generation)
        existing_generation = dimensions.get("topology_generation")
        if existing_generation is not None and existing_generation != generation:
            raise ValueError(
                "metric topology_generation conflicts with the bound registry generation"
            )
        dimensions["system"] = self.system.key
        dimensions["topology_generation"] = generation
        return self._sink.observe(context, name, value, **dimensions)


class SystemObservationFactory:
    """Create one validated logger/metric bundle for each registry node."""

    def __init__(
        self,
        systems: SystemRegistryPort,
        logging: LoggingSystemPort,
        metrics: object,
    ) -> None:
        self._systems = systems
        self._logging = logging
        self._metrics = metrics
        self._lock = RLock()
        self._bindings: dict[str, SystemObservationBinding] = {}
        self._active = False
        self._closed = False
        self._scope = PLATFORM_SCOPE
        self._trace_id: str | None = None
        self._unsubscribe = self._systems.subscribe(self._on_topology_change)

    def _on_topology_change(
        self,
        _change: SystemRegistryChange,
    ) -> None:
        if not self._active:
            return
        with self._lock:
            self._bindings = {
                descriptor.identity.key: self.bind(
                    descriptor.identity,
                    scope=self._scope,
                    trace_id=self._trace_id,
                )
                for descriptor in self._systems.list()
            }

    def _path(self, system: SystemIdentity) -> tuple[SystemIdentity, ...]:
        ancestors = tuple(
            descriptor.identity
            for descriptor in reversed(self._systems.ancestors(system.key))
        )
        return ancestors + (system,)

    def bind(
        self,
        system: SystemIdentity,
        *,
        logger: str | None = None,
        scope: ScopeIdentity = PLATFORM_SCOPE,
        component_id: str | None = None,
        operation_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> SystemObservationBinding:
        descriptor = self._systems.validate(system)
        address = DiagnosticAddress(
            scope_path=(scope,),
            system_path=self._path(system),
            component_id=component_id,
            operation_id=operation_id,
            trace_id=trace_id,
            span_id=span_id,
        )
        return SystemObservationBinding(
            descriptor=descriptor,
            address=address,
            logger=self._logging.bind(
                logger=logger or f"system.{system.key.replace('/', '.')}",
                address=address,
            ),
            metrics=SystemBoundMetricSink(self._metrics, self._systems, system),
            topology_generation=self._systems.generation,
            topology_digest=self._systems.topology_digest,
        )

    def bind_all(
        self,
        *,
        scope: ScopeIdentity = PLATFORM_SCOPE,
        trace_id: str | None = None,
    ) -> tuple[SystemObservationBinding, ...]:
        """Bind all nodes and keep the set synchronized with future registrations."""

        with self._lock:
            if self._closed:
                raise RuntimeError("system observation factory is closed")
            self._scope = scope
            self._trace_id = trace_id
            self._active = True
            self._bindings = {
                descriptor.identity.key: self.bind(
                    descriptor.identity,
                    scope=scope,
                    trace_id=trace_id,
                )
                for descriptor in self._systems.list()
            }
            return tuple(self._bindings.values())

    def bindings(self) -> tuple[SystemObservationBinding, ...]:
        """Return the current registry-synchronized binding snapshot."""

        with self._lock:
            return tuple(self._bindings.values())

    def close(self) -> None:
        """Stop automatic rebinding and release the registry subscription."""

        with self._lock:
            if not self._closed:
                self._closed = True
                self._active = False
                self._unsubscribe()


__all__ = [
    "StructuredLoggingSystem",
    "SystemBoundMetricSink",
    "SystemObservationBinding",
    "SystemObservationFactory",
]
