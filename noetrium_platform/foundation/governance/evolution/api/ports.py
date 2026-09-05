from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
)
from .contracts import (
    DiscoveryReport,
    EvolutionAssessment,
    EvolutionProposal,
    EvolutionStage,
    EvolutionTransition,
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

    def operation(
        self,
        system: SystemIdentity,
        operation_id: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> AbstractContextManager[None]:
        """Observe one synchronous operation without coupling callers to storage."""
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

    def advance(
        self,
        proposal_id: str,
        to_stage: EvolutionStage,
        *,
        evidence_refs: tuple[str, ...],
        reason_digest: str,
        decision_contract_id: str,
        decision_implementation_digest: str,
        decision_configuration_digest: str,
    ) -> EvolutionTransition:
        ...


@runtime_checkable
class EvolutionStateStorePort(Protocol):
    """Durable authority for evolution evidence and immutable proposals."""

    def append_observation(self, observation: TopologyObservation) -> None:
        ...

    def append_discovery(self, report: DiscoveryReport) -> None:
        ...

    def put_proposal(self, proposal: EvolutionProposal) -> None:
        ...

    def append_transition(self, transition: EvolutionTransition) -> None:
        ...

    def observations(self) -> tuple[TopologyObservation, ...]:
        ...

    def proposals(self) -> tuple[EvolutionProposal, ...]:
        ...

    def transitions(self) -> tuple[EvolutionTransition, ...]:
        ...


__all__ = ["EvolutionStateStorePort", "SystemEvolutionPort"]
