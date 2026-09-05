from __future__ import annotations

from collections.abc import Mapping
import time

from noetrium_platform.capabilities.environment.runtime.api import ActionResult
from noetrium_platform.research.experimentation.experiment.api import ExperimentTaskSpec
from noetrium_platform.capabilities.participant.method.api.contracts import MethodSession
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonValue

from ..api import (
    WorkloadActionAdapterPort,
    WorkloadBoundaryPort,
    WorkloadCompletionPort,
    WorkloadDiagnosticsPort,
    WorkloadEnvironmentPort,
    WorkloadEvidencePort,
    WorkloadFailurePolicyPort,
    WorkloadPlannerPort,
    WorkloadStatePort,
    WorkloadTaskResult,
)
from .diagnostics import WorkloadDiagnosticEmitter, WorkloadFailureRouter
from .phases import (
    WorkloadActionPhase,
    WorkloadBootstrapPhase,
    WorkloadCompletionPhase,
    WorkloadDecisionPhase,
    build_workload_runtime_phases,
    completion_records,
)


class _DefaultActionAdapter:
    def action_id(self, task: ExperimentTaskSpec, step: int) -> str:
        return f"{task.task_id}:action:{step}"



class GenericWorkloadTaskRunner:
    """Sequence one task through explicit bootstrap, decision, action and completion phases."""

    def __init__(
        self,
        *,
        environment: WorkloadEnvironmentPort,
        method: MethodSession,
        evidence: WorkloadEvidencePort,
        planner: WorkloadPlannerPort,
        state: WorkloadStatePort,
        completion: WorkloadCompletionPort,
        failure_policy: WorkloadFailurePolicyPort,
        diagnostics: WorkloadDiagnosticsPort | None = None,
        boundary: WorkloadBoundaryPort | None = None,
        action_adapter: WorkloadActionAdapterPort | None = None,
        max_diagnostic_errors: int = 64,
        event_prefix: str = "WORKLOAD",
        metric_prefix: str = "workload",
    ) -> None:
        if max_diagnostic_errors <= 0:
            raise ValueError("max_diagnostic_errors must be positive")
        if not event_prefix.strip() or not metric_prefix.strip():
            raise ValueError("workload diagnostic prefixes must be non-empty")
        self.environment = environment
        self.method = method
        self.evidence = evidence
        self.planner = planner
        self.state = state
        self.completion = completion
        self.failure_policy = failure_policy
        self.diagnostics = diagnostics
        self.boundary = boundary
        self.action_adapter = action_adapter or _DefaultActionAdapter()
        self.event_prefix = event_prefix
        self.metric_prefix = metric_prefix

        self._bind_runtime_phases(max_diagnostic_errors)

    def _bind_runtime_phases(self, max_diagnostic_errors: int) -> None:
        runtime = build_workload_runtime_phases(
            environment=self.environment, method=self.method, evidence=self.evidence,
            planner=self.planner, state=self.state, completion=self.completion,
            failure_policy=self.failure_policy, diagnostics=self.diagnostics, boundary=self.boundary,
            action_adapter=self.action_adapter, event_prefix=self.event_prefix,
            metric_prefix=self.metric_prefix, max_diagnostic_errors=max_diagnostic_errors,
        )
        self._diagnostics = runtime.diagnostics
        self._bootstrap = runtime.bootstrap
        self._decision = runtime.decision
        self._action = runtime.action
        self._completion = runtime.completion

    @property
    def diagnostic_errors(self) -> tuple[str, ...]:
        return self._diagnostics.errors

    def run(self, task: ExperimentTaskSpec, context: ExecutionContext) -> WorkloadTaskResult:
        self._diagnostics.clear()
        started = time.monotonic()
        bootstrap = self._bootstrap.run(task, context)
        state: Mapping[str, JsonValue] = bootstrap.state
        actions: list[Mapping[str, JsonValue]] = []
        cycles: list[Mapping[str, JsonValue]] = []
        memory_queries = 0
        planner_finished = False
        failure_reason = ""
        last_action: ActionResult | None = None

        for step in range(task.max_steps):
            if self._completion.is_complete(
                task=task,
                state=state,
                planner_finished=planner_finished,
                last_action=last_action,
            ):
                break
            if time.monotonic() - started > task.max_seconds:
                failure_reason = "task_timeout"
                break

            decision_cycle = self._decision.run(
                task=task,
                task_context=bootstrap.task_context,
                state=state,
                step=step,
                prior_actions=tuple(actions),
            )
            memory_queries += 1
            decision = decision_cycle.decision
            if decision.completion_claim or decision.action_type == "finish":
                planner_finished = True
                action_record, cycle_record = completion_records(decision_cycle)
                actions.append(action_record)
                cycles.append(cycle_record)
                break

            action = self._action.run(
                task=task,
                cycle=decision_cycle,
                state=state,
            )
            state = action.state
            last_action = action.last_action
            actions.append(action.action_record)
            cycles.append(action.cycle_record)

        return self._completion.finalize(
            task=task,
            bootstrap=bootstrap,
            state=state,
            actions=tuple(actions),
            cycles=tuple(cycles),
            memory_queries=memory_queries,
            planner_finished=planner_finished,
            last_action=last_action,
            failure_reason=failure_reason,
            started=started,
        )


__all__ = ["GenericWorkloadTaskRunner"]
