from __future__ import annotations

from dataclasses import dataclass

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
    SystemRegistryPort,
)
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE, ScopeIdentity
from noetrium_platform.evidence.observability.api import ContextMetricSink
from noetrium_platform.evidence.observability.logging.context.api import DiagnosticAddress
from noetrium_platform.evidence.observability.logging.record.api import (
    LogWriterPort,
    LoggingSystemPort,
)
from noetrium_platform.evidence.observability.telemetry.metric.runtime import (
    SystemBoundMetricSink,
)


@dataclass(frozen=True, slots=True)
class SystemObservationBinding:
    """One topology-anchored observation bundle for a system node."""

    descriptor: SystemDescriptor
    address: DiagnosticAddress
    logger: LogWriterPort
    metrics: SystemBoundMetricSink
    topology_generation: int
    topology_digest: str


class SystemObservationFactory:
    """Create validated, topology-aware observation bindings at composition time."""

    def __init__(
        self,
        systems: SystemRegistryPort,
        logging: LoggingSystemPort,
        metrics: ContextMetricSink,
    ) -> None:
        self._systems = systems
        self._logging = logging
        self._metrics = metrics

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
        """Bind every registered node once for automatic baseline coverage."""

        return tuple(
            self.bind(
                descriptor.identity,
                scope=scope,
                trace_id=trace_id,
            )
            for descriptor in self._systems.list()
        )


__all__ = ["SystemObservationBinding", "SystemObservationFactory"]
