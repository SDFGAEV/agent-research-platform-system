from noetrium_platform.research.experimentation.experiment.api import (
    ExecutionMode,
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentUnit,
    ExperimentUnitKind,
    ObservationEnvelope,
    ObservationKind,
    UnitOutcome,
    UnitOutcomeState,
)
from noetrium_platform.research.experimentation.study.runtime import (
    InMemoryObservationLedger,
    UniversalExperimentKernel,
    UniversalExperimentRunner,
)

SHA = "a" * 64


def definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        "project", "experiment", "protocol", ExperimentUnitKind.EPISODE,
        ExecutionMode.SIMULATION, SHA, "b" * 64, "c" * 64, "d" * 64, "e" * 64,
    )


def unit(unit_id: str, ordinal: int) -> ExperimentUnit:
    return ExperimentUnit(unit_id, ExperimentUnitKind.EPISODE, SHA, "b" * 64, "seed", ordinal=ordinal)


class Executor:
    def execute_unit(self, owned: ExperimentUnit, run_id: str, sink: InMemoryObservationLedger) -> UnitOutcome:
        observation = ObservationEnvelope(
            "experiment", run_id, owned.unit_id, 0, "t0", "simulator",
            "episode.v1", ObservationKind.TRAJECTORY, {"return": 1.0},
        )
        sink.append(observation)
        return UnitOutcome(owned.unit_id, UnitOutcomeState.SUCCEEDED, 1, (observation.observation_digest,))


class FailingExecutor(Executor):
    def execute_unit(self, owned: ExperimentUnit, run_id: str, sink: InMemoryObservationLedger) -> UnitOutcome:
        if owned.unit_id == "episode-b":
            raise RuntimeError("simulated failure")
        return super().execute_unit(owned, run_id, sink)


def test_runner_produces_terminal_report_and_observations() -> None:
    plan = UniversalExperimentKernel().compile(
        definition(), (unit("episode-a", 0), unit("episode-b", 1))
    )
    report = UniversalExperimentRunner().run(plan, "run-1", Executor())

    assert report.state is ExperimentLifecycleState.COMPLETED
    assert len(report.outcomes) == 2
    assert report.findings == ()
    assert report.report_digest


def test_runner_preserves_partial_failure_for_recovery() -> None:
    plan = UniversalExperimentKernel().compile(
        definition(), (unit("episode-a", 0), unit("episode-b", 1))
    )
    report = UniversalExperimentRunner().run(plan, "run-2", FailingExecutor())

    assert report.state is ExperimentLifecycleState.PARTIAL
    assert report.outcomes[1].state is UnitOutcomeState.FAILED
    assert report.outcomes[1].error_code == "executor.RuntimeError"


def test_doctor_detects_sequence_gap_without_ledger() -> None:
    plan = UniversalExperimentKernel().compile(definition(), (unit("episode-a", 0),))
    observation = ObservationEnvelope(
        "experiment", "run-3", "episode-a", 1, "t1", "simulator",
        "episode.v1", ObservationKind.EVENT, {"event": "late"},
    )
    report = UniversalExperimentKernel().inspect(plan, (observation,))

    assert not report.healthy
    assert any(item.code == "observation.sequence_gap" for item in report.findings)
