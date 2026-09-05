from __future__ import annotations

from typing import Protocol, runtime_checkable

from noetrium_platform.foundation.governance.system_registry.api import SystemDescriptor
from .contracts import (
    DiscoveryReport,
    EvolutionAssessment,
    EvolutionProposal,
    ImprovementSignal,
    TopologyObservation,
)


@runtime_checkable
class SystemEvolutionPort(Protocol):
    """Narrow control-plane port for topology-driven automatic improvement."""

    def discover(
        self,
        source_id: str,
        descriptors: tuple[SystemDescriptor, ...],
        *,
        source_digest: str,
    ) -> DiscoveryReport:
        ...

    def observe(self, observation: TopologyObservation) -> None:
        ...

    def assess(self) -> EvolutionAssessment:
        ...

    def propose(
        self,
        signal: ImprovementSignal,
        *,
        change_contract_id: str,
        implementation_digest: str,
        configuration_digest: str,
        validation_plan_digest: str,
        rollback_anchor_digest: str,
    ) -> EvolutionProposal:
        ...


__all__ = ["SystemEvolutionPort"]
