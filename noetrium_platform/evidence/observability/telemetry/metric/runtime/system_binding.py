from __future__ import annotations

from noetrium_platform.evidence.observability.api import ContextMetricSink
from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
    SystemRegistryPort,
)


class SystemBoundMetricSink:
    """Attach a validated topology identity to every metric observation.

    The wrapper adds one reserved, queryable dimension and leaves storage,
    batching, and metric definitions owned by the injected sink.
    """

    def __init__(
        self,
        sink: ContextMetricSink,
        systems: SystemRegistryPort,
        system: SystemIdentity,
    ) -> None:
        self._sink = sink
        self._systems = systems
        self._descriptor: SystemDescriptor = systems.validate(system)

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
        context,
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
