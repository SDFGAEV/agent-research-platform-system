from __future__ import annotations

import hashlib

import pytest

from noetrium_platform.foundation.governance.evolution import (
    DriftKind,
    EvolutionStage,
    ObservationOutcome,
    RegistryDrivenEvolutionController,
    SignalKind,
    TopologyObservation,
)
from noetrium_platform.foundation.governance.system_registry.api import (
    AuthorityDescriptor,
    SystemDescriptor,
    SystemIdentity,
    SystemLayer,
)
from noetrium_platform.foundation.governance.system_registry.runtime import (
    build_default_system_registry,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _synthetic_descriptor() -> SystemDescriptor:
    return SystemDescriptor(
        identity=SystemIdentity("governance", ("synthetic",)),
        layer=SystemLayer.GOVERNANCE,
        package_prefix="noetrium_platform.foundation.governance.synthetic",
        authorities=(AuthorityDescriptor("synthetic_authority"),),
        owns="synthetic test topology",
        must_not_own="runtime behavior",
    )


def _observation(controller, system: SystemIdentity, outcome, duration: float, index: int):
    return TopologyObservation(
        observation_id=f"observation-{index}",
        system=system,
        topology_generation=controller.systems.generation,
        topology_digest=controller.systems.topology_digest,
        operation_id=f"operation-{index}",
        duration_seconds=duration,
        outcome=outcome,
        evidence_refs=(f"evidence-{index}",),
    )


def test_discovery_is_typed_idempotent_and_auto_enrolls() -> None:
    registry = build_default_system_registry()
    controller = RegistryDrivenEvolutionController(registry)
    descriptor = _synthetic_descriptor()
    source_digest = _digest("synthetic-source")

    first = controller.discover(
        "test-source",
        (descriptor,),
        source_digest=source_digest,
    )
    second = controller.discover(
        "test-source",
        (descriptor,),
        source_digest=source_digest,
    )

    assert first.registered == ("governance/synthetic",)
    assert second.already_registered == ("governance/synthetic",)
    assert registry.contains("governance/synthetic")


def test_unknown_runtime_node_becomes_explicit_topology_signal() -> None:
    registry = build_default_system_registry()
    controller = RegistryDrivenEvolutionController(registry)
    unknown = SystemIdentity("governance", ("unknown",))
    observation = TopologyObservation(
        observation_id="unknown-observation",
        system=unknown,
        topology_generation=registry.generation,
        topology_digest=registry.topology_digest,
        operation_id="operation",
        duration_seconds=0.1,
        outcome=ObservationOutcome.FAILURE,
    )

    controller.observe(observation)
    assessment = controller.assess()

    assert assessment.drifts[0].kind is DriftKind.UNKNOWN_NODE
    assert assessment.signals[0].kind is SignalKind.TOPOLOGY_DRIFT


def test_failure_cluster_generates_digest_bound_proposal() -> None:
    registry = build_default_system_registry()
    controller = RegistryDrivenEvolutionController(registry, minimum_samples=3)
    system = SystemIdentity("observability", ("logging",))

    for index in range(3):
        controller.observe(
            _observation(
                controller,
                system,
                ObservationOutcome.FAILURE,
                0.2,
                index,
            )
        )

    assessment = controller.assess()
    signal = next(item for item in assessment.signals if item.kind is SignalKind.FAILURE_CLUSTER)
    proposal = controller.propose(
        signal,
        change_contract_id="observability.optimization.v1",
        implementation_digest=_digest("implementation"),
        configuration_digest=_digest("configuration"),
        validation_plan_digest=_digest("validation"),
        rollback_anchor_digest=_digest("rollback"),
    )

    assert proposal.stage is EvolutionStage.PROPOSED
    assert proposal.signal.digest()
    assert proposal.predecessor_topology_digest == registry.topology_digest
    assert proposal.digest()


def test_proposal_rejects_stale_topology() -> None:
    registry = build_default_system_registry()
    controller = RegistryDrivenEvolutionController(registry, minimum_samples=1)
    system = SystemIdentity("observability", ("logging",))
    controller.observe(_observation(controller, system, ObservationOutcome.FAILURE, 0.2, 1))
    signal = controller.assess().signals[0]

    registry.register(_synthetic_descriptor())

    with pytest.raises(ValueError, match="stale topology"):
        controller.propose(
            signal,
            change_contract_id="test.v1",
            implementation_digest=_digest("implementation"),
            configuration_digest=_digest("configuration"),
            validation_plan_digest=_digest("validation"),
            rollback_anchor_digest=_digest("rollback"),
        )


def test_platform_composition_exposes_evolution_through_a_narrow_port() -> None:
    from noetrium_platform.composition.platform_meta import build_in_memory_platform_meta

    meta = build_in_memory_platform_meta()

    assert meta.evolution.systems is meta.systems
    assert meta.evolution.assess().topology_generation == meta.systems.generation
