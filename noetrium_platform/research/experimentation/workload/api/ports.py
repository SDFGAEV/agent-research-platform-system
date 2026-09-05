from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from noetrium_platform.research.experimentation.experiment.api import ExperimentTaskSpec, FailureScope
from noetrium_platform.research.experimentation.run.api import RunDiagnosticsPort
from noetrium_platform.capabilities.environment.runtime.api import ActionRequest, ActionResult, Observation
from noetrium_platform.capabilities.participant.method.api.contracts import MethodSession
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonValue

from .contracts import WorkloadDecision, WorkloadTaskResult


class WorkloadEnvironmentPort(Protocol):
    """Environment session seam used by the generic task runner."""

    def observe(self, context: ExecutionContext) -> Observation: ...

    def act(self, request: ActionRequest) -> ActionResult: ...


class WorkloadBoundaryPort(Protocol):
    """Optional workload-boundary adapter, separate from environment state."""

    def begin(self, metadata: Mapping[str, JsonValue], context: ExecutionContext) -> Observation | None: ...

    def end(self, metadata: Mapping[str, JsonValue], context: ExecutionContext) -> Observation | None: ...


class WorkloadFailurePolicyPort(Protocol):
    """Classify a failure without hiding the original exception."""

    def scope(self, phase: str, exception: BaseException) -> FailureScope: ...


class WorkloadStatePort(Protocol):
    """Project/environment adapter that exposes the decision state."""

    def state(self, observation: Observation) -> Mapping[str, JsonValue]: ...


class WorkloadPlannerPort(Protocol):
    """Planner seam. Domain action schemas stay in the injected adapter."""

    def decide(
        self,
        *,
        task: ExperimentTaskSpec,
        context: ExecutionContext,
        state: Mapping[str, JsonValue],
        memory_context: str,
        step: int,
        prior_actions: tuple[Mapping[str, JsonValue], ...],
    ) -> WorkloadDecision: ...


class WorkloadCompletionPort(Protocol):
    """Domain completion/utility semantics, never owned by the runner."""

    def is_complete(
        self,
        *,
        task: ExperimentTaskSpec,
        state: Mapping[str, JsonValue],
        planner_finished: bool,
        last_action: ActionResult | None,
    ) -> bool: ...

    def utility(self, *, task: ExperimentTaskSpec, success: bool, state: Mapping[str, JsonValue]) -> float: ...


class WorkloadEvidencePort(Protocol):
    def ingest_observation(self, observation: Observation, context: ExecutionContext) -> tuple[str, ...]: ...


class WorkloadActionAdapterPort(Protocol):
    """Optional adapter for action identity/recording; execution stays generic."""

    def action_id(self, task: ExperimentTaskSpec, step: int) -> str: ...


class WorkloadTaskRunnerPort(Protocol):
    def run(self, task: ExperimentTaskSpec, context: ExecutionContext) -> WorkloadTaskResult: ...


class WorkloadExecutionCutObserverPort(Protocol):
    """Observe a committed task boundary for checkpoint/evidence systems."""

    def after_task(
        self,
        *,
        task: ExperimentTaskSpec,
        result: WorkloadTaskResult,
        context: ExecutionContext,
    ) -> None: ...


class WorkloadBatchBindingPort(Protocol):
    tasks: tuple[ExperimentTaskSpec, ...]
    context: ExecutionContext

    def runner_for(self, task: ExperimentTaskSpec) -> WorkloadTaskRunnerPort: ...

    def record_result(
        self,
        *,
        task: ExperimentTaskSpec,
        result: WorkloadTaskResult,
        context: ExecutionContext,
    ) -> None: ...

    def close(self) -> None: ...


class WorkloadDiagnosticsPort(RunDiagnosticsPort, Protocol):
    pass


__all__ = [
    "WorkloadActionAdapterPort",
    "WorkloadBatchBindingPort",
    "WorkloadBoundaryPort",
    "WorkloadCompletionPort",
    "WorkloadDiagnosticsPort",
    "WorkloadExecutionCutObserverPort",
    "WorkloadEnvironmentPort",
    "WorkloadEvidencePort",
    "WorkloadFailurePolicyPort",
    "WorkloadPlannerPort",
    "WorkloadStatePort",
    "WorkloadTaskRunnerPort",
]
