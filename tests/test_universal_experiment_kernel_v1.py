from dataclasses import replace

import pytest

from noetrium_platform.research.experimentation.experiment.api import (
    AnalysisPlan,
    ExecutionMode,
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentUnit,
    ExperimentUnitKind,
    FindingSeverity,
    ObservationEnvelope,
    ObservationKind,
)
from noetrium_platform.research.experimentation.study.runtime import (
    ExperimentLifecycle,
    InMemoryObservationLedger,
    UniversalExperimentKernel,
)

SHA = "a" * 64
SHA_B = "b" * 64


def definition(mode: ExecutionMode = ExecutionMode.INTERACTIVE) -> ExperimentDefinition:
    return ExperimentDefinition(
        project_id="project",
        experiment_id="experiment",
        protocol_id="protocol.v1",
        unit_kind=ExperimentUnitKind.SESSION,
        execution_mode=mode,
        design_protocol_digest=SHA,
        observation_protocol_digest=SHA_B,
        analysis_plan_digest="c" * 64,
        implementation_digest="d" * 64,
        resource_policy_digest="e" * 64,
        objective="measure a generic session",
        metadata={"family": "agent", "version": 1},
    )


def unit(unit_id: str, ordinal: int) -> ExperimentUnit:
    return ExperimentUnit(
        unit_id=unit_id,
        kind=ExperimentUnitKind.SESSION,
        input_digest=SHA,
        condition_digest=SHA_B,
        seed=f"seed-{ordinal}",
        ordinal=ordinal,
    )


def observation(unit_id: str, sequence: int = 0) -> ObservationEnvelope:
    return ObservationEnvelope(
        experiment_id="experiment",
        run_id="run-1",
        unit_id=unit_id,
        sequence=sequence,
        logical_time=f"2026-09-05T00:00:0{sequence}Z",
        producer_id="adapter",
        schema_id="session.event.v1",
        kind=ObservationKind.EVENT,
        payload={"action": "step", "value": sequence},
    )


def test_generic_kernel_compiles_non_benchmark_units() -> None:
    kernel = UniversalExperimentKernel()
    plan = kernel.compile(definition(), (unit("session-a", 0), unit("session-b", 1)))

    assert plan.experiment_id == "experiment"
    assert tuple(item.unit_id for item in plan.units) == ("session-a", "session-b")
    assert plan.plan_digest != definition().definition_digest


def test_all_execution_modes_share_the_same_contract() -> None:
    for mode in ExecutionMode:
        plan = UniversalExperimentKernel().compile(
            definition(mode), (unit(f"{mode.value}-unit", 0),)
        )
        assert plan.units[0].kind is ExperimentUnitKind.SESSION
        assert plan.plan_digest


def test_observation_ledger_enforces_per_unit_order() -> None:
    ledger = InMemoryObservationLedger()
    ledger.append(observation("session-a"))
    with pytest.raises(ValueError, match="sequence gap"):
        ledger.append(observation("session-a", sequence=2))


def test_doctor_detects_missing_and_unknown_units() -> None:
    kernel = UniversalExperimentKernel()
    plan = kernel.compile(definition(), (unit("session-a", 0), unit("session-b", 1)))
    report = kernel.inspect(plan, (observation("foreign"),))

    assert not report.healthy
    assert {item.code for item in report.findings} == {"unit.missing", "unit.unknown"}
    assert all(item.severity in (FindingSeverity.ERROR, FindingSeverity.CRITICAL) for item in report.findings)


def test_doctor_accepts_complete_observation_cut() -> None:
    kernel = UniversalExperimentKernel()
    plan = kernel.compile(definition(), (unit("session-a", 0), unit("session-b", 1)))
    report = kernel.inspect(plan, (observation("session-a"), observation("session-b")))

    assert report.healthy
    assert report.findings == ()


def test_lifecycle_is_explicit_and_terminal() -> None:
    lifecycle = ExperimentLifecycle("experiment")
    lifecycle.transition(ExperimentLifecycleState.RESOLVED, "t1", "bindings resolved")
    lifecycle.transition(ExperimentLifecycleState.FROZEN, "t2", "protocol frozen")
    lifecycle.transition(ExperimentLifecycleState.PLANNED, "t3", "units planned")
    lifecycle.transition(ExperimentLifecycleState.RUNNING, "t4", "execution started")
    lifecycle.transition(ExperimentLifecycleState.COMPLETED, "t5", "all units observed")

    assert lifecycle.state is ExperimentLifecycleState.COMPLETED
    assert len(lifecycle.transitions) == 5
    with pytest.raises(ValueError, match="illegal experiment transition"):
        lifecycle.transition(ExperimentLifecycleState.RUNNING, "t6", "must fail")


def test_analysis_plan_is_frozen_before_execution() -> None:
    plan = AnalysisPlan(
        "analysis.v1",
        "paired-bootstrap",
        (ObservationKind.MEASUREMENT, ObservationKind.TRAJECTORY),
        grouping_keys=("condition", "unit"),
        comparison="control-treatment",
    )
    assert plan.analysis_digest
    with pytest.raises(ValueError, match="frozen"):
        replace(plan, frozen=False)
