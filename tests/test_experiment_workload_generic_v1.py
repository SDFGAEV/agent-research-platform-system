from __future__ import annotations

import pytest

from noetrium_platform.capabilities.environment.runtime.api import ActionRequest, ActionResult, Observation
from noetrium_platform.research.experimentation.experiment.api import ExperimentTaskSpec, FailureScope
from noetrium_platform.research.experimentation.workload.api import (
    WorkloadDecision,
    WorkloadTaskRunError,
)
from noetrium_platform.research.experimentation.workload.runtime import GenericWorkloadTaskRunner
from noetrium_platform.capabilities.participant.method.api import (
    MethodTaskCompletionReceipt,
    MethodTaskOutcome,
    RecallResult,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


class _Method:
    def __init__(self):
        self.outcomes = []

    def recall(self, request):
        return RecallResult("", "method-g0")

    def task_completed(self, result, context):
        self.outcomes.append(result)
        return MethodTaskCompletionReceipt(context.task_id or "task", "method-g0")


class _Environment:
    def __init__(self):
        self.requests: list[ActionRequest] = []

    def begin_task(self, metadata, context):
        return None

    def observe(self, context):
        return Observation("obs-0", "env-g0", {"state": {"done": False}})

    def act(self, request):
        self.requests.append(request)
        return ActionResult(
            request.action_id,
            True,
            Observation("obs-1", "env-g0", {"state": {"done": True}}),
            None,
            {"verified": True},
        )

    def end_task(self, metadata, context):
        return None


class _Evidence:
    def __init__(self):
        self.observations = []

    def ingest_observation(self, observation, context):
        self.observations.append(observation.observation_id)
        return (observation.observation_id,)


class _Planner:
    def decide(self, *, task, context, state, memory_context, step, prior_actions):
        return WorkloadDecision("advance", {"step": step}, "test")


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


class _Diagnostics:
    def __init__(self):
        self.events = []

    def event(self, event="", *, phase="workload", attributes=None, level="DEBUG", correlation_refs=()):
        self.events.append((event, phase, dict(attributes or {}), level, correlation_refs))

    def metric(self, name="", value=0.0, *, labels=None):
        pass

    def failure(self, code="", message="", *, phase="workload", exception=None, attributes=None, correlation_refs=()):
        pass


def test_generic_workload_runner_is_domain_neutral_and_preserves_action_identity():
    environment = _Environment()
    evidence = _Evidence()
    method = _Method()
    runner = GenericWorkloadTaskRunner(
        environment=environment,
        method=method,
        evidence=evidence,
        planner=_Planner(),
        state=_State(),
        completion=_Completion(),
        failure_policy=_FailurePolicy(),
    )

    result = runner.run(
        ExperimentTaskSpec("task-1", "navigation", "reach target", max_steps=2),
        ExecutionContext(run_id="run", trace_id="trace", span_id="span", study_id="study"),
    )

    assert result.success is True
    assert result.steps == 1
    assert environment.requests[0].action_id == "task-1:action:0"
    assert evidence.observations == ["obs-0", "obs-1"]
    assert method.outcomes == [
        MethodTaskOutcome(
            task_id="task-1",
            family="navigation",
            lineage_id="task-1",
            success=True,
            utility=1.0,
            steps=1,
            memory_queries=1,
        )
    ]


def test_generic_workload_runner_emits_exact_action_lifecycle_evidence() -> None:
    diagnostics = _Diagnostics()
    runner = GenericWorkloadTaskRunner(
        environment=_Environment(),
        method=_Method(),
        evidence=_Evidence(),
        planner=_Planner(),
        state=_State(),
        completion=_Completion(),
        failure_policy=_FailurePolicy(),
        diagnostics=diagnostics,
    )
    context = ExecutionContext(run_id="run", trace_id="trace", span_id="span", study_id="study")

    runner.run(ExperimentTaskSpec("task-1", "navigation", "reach target", max_steps=2), context)

    lifecycle = {event: attributes for event, _, attributes, _, _ in diagnostics.events}
    started = lifecycle["WORKLOAD_ACTION_STARTED"]
    finished = lifecycle["WORKLOAD_ACTION_FINISHED"]
    assert started["action_id"] == "task-1:action:0"
    assert started["action_request_digest"] == finished["action_request_digest"]
    assert finished["accepted"] is True
    assert finished["verified"] is True
    assert finished["observation_id"] == "obs-1"
    assert finished["observation_generation"] == "env-g0"
    assert finished["duration_s"] >= 0


def test_generic_workload_runner_closes_action_event_when_result_identity_drifts() -> None:
    class DriftingEnvironment(_Environment):
        def act(self, request):
            return ActionResult("another-action", True, None, None, {})

    diagnostics = _Diagnostics()
    runner = GenericWorkloadTaskRunner(
        environment=DriftingEnvironment(),
        method=_Method(),
        evidence=_Evidence(),
        planner=_Planner(),
        state=_State(),
        completion=_Completion(),
        failure_policy=_FailurePolicy(),
        diagnostics=diagnostics,
    )

    with pytest.raises(WorkloadTaskRunError, match="action identity mismatch"):
        runner.run(
            ExperimentTaskSpec("task-1", "navigation", "reach target", max_steps=2),
            ExecutionContext(run_id="run", trace_id="trace", span_id="span", study_id="study"),
        )

    lifecycle = [row for row in diagnostics.events if row[0].startswith("WORKLOAD_ACTION_")]
    assert [row[0] for row in lifecycle] == ["WORKLOAD_ACTION_STARTED", "WORKLOAD_ACTION_FINISHED"]
    assert lifecycle[-1][2]["accepted"] is False
    assert lifecycle[-1][2]["failure_type"] == "ActionIdentityViolation"


def _runner(**overrides):
    arguments = {
        "environment": _Environment(),
        "method": _Method(),
        "evidence": _Evidence(),
        "planner": _Planner(),
        "state": _State(),
        "completion": _Completion(),
        "failure_policy": _FailurePolicy(),
    }
    arguments.update(overrides)
    return GenericWorkloadTaskRunner(**arguments)


def _task_context():
    return (
        ExperimentTaskSpec("task-1", "navigation", "reach target", max_steps=2),
        ExecutionContext(run_id="run", trace_id="trace", span_id="span", study_id="study"),
    )


def test_runner_preserves_decision_failure_phase_after_phase_split() -> None:
    class FailingPlanner(_Planner):
        def decide(self, **kwargs):
            del kwargs
            raise RuntimeError("planner failed")

    task, context = _task_context()
    with pytest.raises(WorkloadTaskRunError) as caught:
        _runner(planner=FailingPlanner()).run(task, context)

    assert caught.value.phase == "decision"
    assert caught.value.code == "WORKLOAD_DECISION_FAILED"
    assert caught.value.scope is FailureScope.TASK


def test_runner_preserves_method_completion_failure_phase_after_phase_split() -> None:
    class FailingMethod(_Method):
        def task_completed(self, result, context):
            del result, context
            raise RuntimeError("completion failed")

    task, context = _task_context()
    with pytest.raises(WorkloadTaskRunError) as caught:
        _runner(method=FailingMethod()).run(task, context)

    assert caught.value.phase == "task_completion"
    assert caught.value.code == "WORKLOAD_TASK_COMPLETION_FAILED"


class _Boundary:
    def begin(self, metadata, context):
        del metadata, context
        return None

    def end(self, metadata, context):
        del metadata, context
        return None


def test_runner_preserves_boundary_end_failure_phase_after_phase_split() -> None:
    class FailingBoundary(_Boundary):
        def end(self, metadata, context):
            del metadata, context
            raise RuntimeError("boundary end failed")

    task, context = _task_context()
    with pytest.raises(WorkloadTaskRunError) as caught:
        _runner(boundary=FailingBoundary()).run(task, context)

    assert caught.value.phase == "task_end"
    assert caught.value.code == "WORKLOAD_TASK_END_FAILED"
    assert caught.value.scope is FailureScope.TASK


def test_diagnostic_sink_failure_remains_observation_only() -> None:
    class FailingDiagnostics(_Diagnostics):
        def event(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("event sink unavailable")

        def metric(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("metric sink unavailable")

        def failure(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("failure sink unavailable")

    task, context = _task_context()
    result = _runner(diagnostics=FailingDiagnostics()).run(task, context)

    assert result.success is True
    errors = result.diagnostics["diagnostic_sink_errors"]
    assert errors
    assert any(str(item).startswith("event:") for item in errors)
    assert any(str(item).startswith("metric:") for item in errors)
