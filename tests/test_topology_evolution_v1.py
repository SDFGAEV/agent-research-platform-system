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


def test_sqlite_evolution_store_rehydrates_observations_and_proposals(tmp_path) -> None:
    from noetrium_platform.foundation.governance.evolution.providers import (
        SQLiteEvolutionStore,
    )

    registry = build_default_system_registry()
    store = SQLiteEvolutionStore(tmp_path / "evolution.sqlite")
    controller = RegistryDrivenEvolutionController(registry, store=store, minimum_samples=3)
    system = SystemIdentity("observability", ("logging",))
    for index in range(3):
        controller.observe(
            _observation(controller, system, ObservationOutcome.FAILURE, 0.2, index)
        )
    signal = next(
        item for item in controller.assess().signals if item.kind is SignalKind.FAILURE_CLUSTER
    )
    proposal = controller.propose(
        signal,
        change_contract_id="persistent.v1",
        implementation_digest=_digest("implementation"),
        configuration_digest=_digest("configuration"),
        validation_plan_digest=_digest("validation"),
        rollback_anchor_digest=_digest("rollback"),
    )

    restored_store = SQLiteEvolutionStore(tmp_path / "evolution.sqlite")
    restored = RegistryDrivenEvolutionController(
        registry,
        store=restored_store,
        minimum_samples=3,
    )

    assert len(restored_store.observations()) == 3
    assert restored_store.proposals() == (proposal,)
    assert any(item.kind is SignalKind.FAILURE_CLUSTER for item in restored.assess().signals)


def test_sqlite_evolution_store_rejects_conflicting_immutable_records(tmp_path) -> None:
    from noetrium_platform.foundation.governance.evolution.providers import (
        EvolutionStoreConflict,
        SQLiteEvolutionStore,
    )

    registry = build_default_system_registry()
    store = SQLiteEvolutionStore(tmp_path / "evolution.sqlite")
    system = SystemIdentity("observability", ("logging",))
    first = _observation(
        RegistryDrivenEvolutionController(registry),
        system,
        ObservationOutcome.SUCCESS,
        0.1,
        1,
    )
    store.append_observation(first)
    conflicting = TopologyObservation(
        observation_id=first.observation_id,
        system=first.system,
        topology_generation=first.topology_generation,
        topology_digest=first.topology_digest,
        operation_id="different-operation",
        duration_seconds=first.duration_seconds,
        outcome=first.outcome,
    )

    with pytest.raises(EvolutionStoreConflict):
        store.append_observation(conflicting)


def test_sqlite_evolution_store_fails_closed_on_corrupt_payload(tmp_path) -> None:
    import sqlite3

    from noetrium_platform.foundation.governance.evolution.providers import (
        EvolutionStoreIntegrityError,
        SQLiteEvolutionStore,
    )

    path = tmp_path / "evolution.sqlite"
    store = SQLiteEvolutionStore(path)
    registry = build_default_system_registry()
    observation = _observation(
        RegistryDrivenEvolutionController(registry),
        SystemIdentity("observability", ("logging",)),
        ObservationOutcome.SUCCESS,
        0.1,
        1,
    )
    store.append_observation(observation)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE evolution_observations SET payload=? WHERE observation_id=?",
            (b"corrupt", observation.observation_id),
        )
        connection.commit()

    with pytest.raises(EvolutionStoreIntegrityError):
        store.observations()


def test_evolution_lifecycle_requires_evidence_and_supports_rollback() -> None:
    registry = build_default_system_registry()
    controller = RegistryDrivenEvolutionController(registry, minimum_samples=3)
    system = SystemIdentity("observability", ("logging",))
    for index in range(3):
        controller.observe(
            _observation(controller, system, ObservationOutcome.FAILURE, 0.2, index)
        )
    signal = next(
        item for item in controller.assess().signals if item.kind is SignalKind.FAILURE_CLUSTER
    )
    proposal = controller.propose(
        signal,
        change_contract_id="lifecycle.v1",
        implementation_digest=_digest("implementation"),
        configuration_digest=_digest("configuration"),
        validation_plan_digest=_digest("validation"),
        rollback_anchor_digest=_digest("rollback"),
    )

    with pytest.raises(ValueError, match="illegal evolution transition"):
        controller.advance(
            proposal.proposal_id,
            EvolutionStage.PROMOTED,
            evidence_refs=("validation-evidence",),
            reason_digest=_digest("reason"),
            decision_contract_id="decision.v1",
            decision_implementation_digest=_digest("decision-implementation"),
            decision_configuration_digest=_digest("decision-configuration"),
        )

    validated = controller.advance(
        proposal.proposal_id,
        EvolutionStage.VALIDATED,
        evidence_refs=("validation-evidence",),
        reason_digest=_digest("validated"),
        decision_contract_id="decision.v1",
        decision_implementation_digest=_digest("decision-implementation"),
        decision_configuration_digest=_digest("decision-configuration"),
    )
    promoted = controller.advance(
        proposal.proposal_id,
        EvolutionStage.PROMOTED,
        evidence_refs=("canary-evidence",),
        reason_digest=_digest("promoted"),
        decision_contract_id="decision.v1",
        decision_implementation_digest=_digest("decision-implementation"),
        decision_configuration_digest=_digest("decision-configuration"),
    )
    rolled_back = controller.advance(
        proposal.proposal_id,
        EvolutionStage.ROLLED_BACK,
        evidence_refs=("rollback-evidence",),
        reason_digest=_digest("rollback"),
        decision_contract_id="decision.v1",
        decision_implementation_digest=_digest("decision-implementation"),
        decision_configuration_digest=_digest("decision-configuration"),
    )

    assert validated.from_stage is EvolutionStage.PROPOSED
    assert promoted.from_stage is EvolutionStage.VALIDATED
    assert rolled_back.from_stage is EvolutionStage.PROMOTED
    assert controller.current_stage(proposal.proposal_id) is EvolutionStage.ROLLED_BACK


def test_persisted_evolution_transitions_rehydrate_contiguous_state(tmp_path) -> None:
    from noetrium_platform.foundation.governance.evolution.providers import (
        SQLiteEvolutionStore,
    )

    registry = build_default_system_registry()
    store = SQLiteEvolutionStore(tmp_path / "evolution.sqlite")
    controller = RegistryDrivenEvolutionController(registry, store=store, minimum_samples=1)
    system = SystemIdentity("observability", ("logging",))
    controller.observe(_observation(controller, system, ObservationOutcome.FAILURE, 0.2, 1))
    signal = next(
        item for item in controller.assess().signals if item.kind is SignalKind.FAILURE_CLUSTER
    )
    proposal = controller.propose(
        signal,
        change_contract_id="rehydration.v1",
        implementation_digest=_digest("implementation"),
        configuration_digest=_digest("configuration"),
        validation_plan_digest=_digest("validation"),
        rollback_anchor_digest=_digest("rollback"),
    )
    controller.advance(
        proposal.proposal_id,
        EvolutionStage.QUARANTINED,
        evidence_refs=("quarantine-evidence",),
        reason_digest=_digest("quarantine"),
        decision_contract_id="decision.v1",
        decision_implementation_digest=_digest("decision-implementation"),
        decision_configuration_digest=_digest("decision-configuration"),
    )

    restored = RegistryDrivenEvolutionController(
        registry,
        store=SQLiteEvolutionStore(tmp_path / "evolution.sqlite"),
        minimum_samples=1,
    )

    assert restored.current_stage(proposal.proposal_id) is EvolutionStage.QUARANTINED


def test_operation_bridge_records_all_outcomes_and_reraises_failures(tmp_path) -> None:
    import asyncio

    from noetrium_platform.foundation.governance.evolution.providers import (
        SQLiteEvolutionStore,
    )

    registry = build_default_system_registry()
    store = SQLiteEvolutionStore(tmp_path / "operation-observations.sqlite")
    controller = RegistryDrivenEvolutionController(registry, store=store)
    system = SystemIdentity("observability", ("logging",))

    with controller.operation(system, "success", evidence_refs=("trace-success",)):
        pass

    with pytest.raises(RuntimeError, match="downstream failure"):
        with controller.operation(system, "failure", evidence_refs=("trace-failure",)):
            raise RuntimeError("downstream failure")

    with pytest.raises(TimeoutError):
        with controller.operation(system, "timeout"):
            raise TimeoutError("deadline exceeded")

    with pytest.raises(asyncio.CancelledError):
        with controller.operation(system, "cancelled"):
            raise asyncio.CancelledError()

    observations = store.observations()
    assert tuple(item.outcome for item in observations) == (
        ObservationOutcome.SUCCESS,
        ObservationOutcome.FAILURE,
        ObservationOutcome.TIMEOUT,
        ObservationOutcome.CANCELLED,
    )
    assert all(item.topology_generation == registry.generation for item in observations)
    assert all(item.topology_digest == registry.topology_digest for item in observations)
    assert observations[0].evidence_refs == ("trace-success",)
    assert observations[1].evidence_refs == ("trace-failure",)


def test_operation_bridge_binds_observation_to_entry_topology() -> None:
    registry = build_default_system_registry()
    controller = RegistryDrivenEvolutionController(registry)
    system = SystemIdentity("observability", ("logging",))
    with controller.operation(system, "topology-sensitive"):
        registry.register(_synthetic_descriptor())

    assessment = controller.assess()
    assert any(item.kind is SignalKind.TOPOLOGY_DRIFT for item in assessment.signals)
    assert assessment.drifts[0].kind is DriftKind.STALE_GENERATION


def test_parallel_operation_observations_are_isolated(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from noetrium_platform.foundation.governance.evolution.providers import (
        SQLiteEvolutionStore,
    )

    registry = build_default_system_registry()
    store = SQLiteEvolutionStore(tmp_path / "parallel-observations.sqlite")
    controller = RegistryDrivenEvolutionController(registry, store=store)
    system = SystemIdentity("observability", ("logging",))

    def run(index: int) -> None:
        with controller.operation(system, f"parallel-{index}"):
            pass

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(run, range(32)))

    observations = store.observations()
    assert len(observations) == 32
    assert {item.operation_id for item in observations} == {
        f"parallel-{index}" for index in range(32)
    }
