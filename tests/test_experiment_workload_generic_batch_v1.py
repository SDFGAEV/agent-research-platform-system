from __future__ import annotations

from dataclasses import dataclass

import pytest

from noetrium_platform.capabilities.environment.runtime.api import ActionRequest, ActionResult, Observation
from noetrium_platform.research.experimentation.experiment.api import ExperimentTaskSpec, FailureScope
from noetrium_platform.research.experimentation.workload.api import (
    WorkloadBatchResult,
    WorkloadDecision,
    WorkloadTaskResult,
)
from noetrium_platform.research.experimentation.workload.runtime import (
    GenericWorkloadBatchExecutor,
    GenericWorkloadTaskRunner,
)
from noetrium_platform.capabilities.participant.method.api import MethodTaskCompletionReceipt, RecallResult
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


class _Method:
    def recall(self, request):
        return RecallResult("non-mc memory", "method-g0")

    def task_completed(self, result, context):
        return MethodTaskCompletionReceipt(context.task_id or "task", "method-g0")


class _Environment:
    def __init__(self) -> None:
        self.requests: list[ActionRequest] = []

    def observe(self, context):
        return Observation(f"obs:{context.task_id}:initial", "non-mc-g0", {"state": {"done": False}})

    def act(self, request):
        self.requests.append(request)
        return ActionResult(
            request.action_id,
            True,
            Observation(f"obs:{request.action_id}", "non-mc-g0", {"state": {"done": True}}),
            None,
            {"verified": True},
        )


class _Evidence:
    def ingest_observation(self, observation, context):
        return (observation.observation_id,)


class _Planner:
    def decide(self, *, task, context, state, memory_context, step, prior_actions):
        return WorkloadDecision("advance", {"step": step}, "non-mc")


class _State:
    def state(self, observation):
        return observation.payload["state"]


class _Completion:
    def is_complete(self, *, task, state, planner_finished, last_action):
        return bool(state.get("done"))

    def utility(self, *, task, success, state):
        return 1.0 if success else 0.0


class _FailurePolicy:
    def scope(self, phase, exception):
        del phase, exception
        return FailureScope.TASK


@dataclass
class _Binding:
    tasks: tuple[ExperimentTaskSpec, ...]
    context: ExecutionContext
    environment: _Environment
    records: list[tuple[str, bool]]
    closed: bool = False

    def runner_for(self, task):
        return GenericWorkloadTaskRunner(
            environment=self.environment,
            method=_Method(),
            evidence=_Evidence(),
            planner=_Planner(),
            state=_State(),
            completion=_Completion(),
            failure_policy=_FailurePolicy(),
        )

    def record_result(self, *, task, result, context):
        self.records.append((task.task_id, result.success))

    def close(self):
        self.closed = True


def test_non_minecraft_backend_uses_the_same_generic_batch_executor():
    context = ExecutionContext("run", "trace", "span", study_id="study")
    binding = _Binding(
        (
            ExperimentTaskSpec("root", "closed-world", "advance"),
            ExperimentTaskSpec("child", "closed-world", "advance", depends_on_task_ids=("root",)),
        ),
        context,
        _Environment(),
        [],
    )

    result = GenericWorkloadBatchExecutor().execute(binding)

    assert [item.task_id for item in result.task_results] == ["root", "child"]
    assert all(item.success for item in result.task_results)
    assert binding.records == [("root", True), ("child", True)]
    assert binding.closed is True


class _CutObserver:
    def __init__(self) -> None:
        self.committed: list[tuple[str, str]] = []

    def after_task(self, *, task, result, context) -> None:
        del context
        self.committed.append((task.task_id, result.task_id))


def test_batch_cut_observer_receives_incremental_committed_task_notifications() -> None:
    context = ExecutionContext("run", "trace", "span", study_id="study")
    binding = _Binding(
        (
            ExperimentTaskSpec("root", "closed-world", "advance"),
            ExperimentTaskSpec("child", "closed-world", "advance", depends_on_task_ids=("root",)),
        ),
        context,
        _Environment(),
        [],
    )
    observer = _CutObserver()

    GenericWorkloadBatchExecutor(observer).execute(binding)

    assert observer.committed == [("root", "root"), ("child", "child")]


def _batch_task_result(task_id: str) -> WorkloadTaskResult:
    return WorkloadTaskResult(
        task_id=task_id, family="family", success=True, utility=1.0,
        steps=1, duration_s=0.1, lineage_id=f"lineage:{task_id}",
    )


def test_workload_batch_result_requires_immutable_typed_unique_receipts() -> None:
    first = _batch_task_result("task-1")
    second = _batch_task_result("task-2")
    assert WorkloadBatchResult((first, second)).task_results == (first, second)

    with pytest.raises(ValueError, match="immutable tuple"):
        WorkloadBatchResult([first])
    with pytest.raises(ValueError, match="WorkloadTaskResult"):
        WorkloadBatchResult((first, object()))
    with pytest.raises(ValueError, match="unique"):
        WorkloadBatchResult((first, first))
