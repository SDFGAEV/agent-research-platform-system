from noetrium_platform.research.experimentation.study.api import StudyAssignment
from noetrium_platform.research.experimentation.study.runtime import (
    StudyMatrixUniversalProjection,
)
from noetrium_platform.research.experimentation.experiment.api import ExperimentUnitKind

SHA = "a" * 64


def test_matrix_assignments_project_to_family_neutral_units() -> None:
    assignments = (
        StudyAssignment("study", "control", 0, "seed-a", "task-a"),
        StudyAssignment("study", "treatment", 0, "seed-a"),
        StudyAssignment("study", "treatment", 1, "seed-b", "episode-b"),
    )
    plan = StudyMatrixUniversalProjection.plan("experiment", SHA, assignments)

    assert len(plan.units) == 3
    assert plan.units[0].kind is ExperimentUnitKind.TASK
    assert plan.units[1].kind is ExperimentUnitKind.GENERIC
    assert plan.units[2].kind is ExperimentUnitKind.TASK
    assert len({item.unit_id for item in plan.units}) == 3
