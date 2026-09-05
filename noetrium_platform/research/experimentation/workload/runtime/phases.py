from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import time

from noetrium_platform.capabilities.environment.runtime.api import (
    ActionRequest,
    ActionResult,
    Observation,
    action_request_digest,
    require_action_result_identity,
)
from noetrium_platform.research.experimentation.experiment.api import ExperimentTaskSpec, FailureScope
from noetrium_platform.capabilities.participant.method.api.contracts import MethodSession, MethodTaskOutcome, RecallRequest
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonValue

from ..api import (
    WorkloadActionAdapterPort,
    WorkloadBoundaryPort,
    WorkloadCompletionPort,
    WorkloadDecision,
    WorkloadEnvironmentPort,
    WorkloadEvidencePort,
    WorkloadPlannerPort,
    WorkloadStatePort,
    WorkloadTaskResult,
)
from .diagnostics import WorkloadDiagnosticEmitter, WorkloadFailureRouter


@dataclass(frozen=True, slots=True)
class WorkloadTaskBootstrap:
    task_context: ExecutionContext
    metadata: Mapping[str, JsonValue]
    state: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class WorkloadDecisionCycle:
    step: int
    cycle_id: str
    context: ExecutionContext
    started: float
    decision: WorkloadDecision


@dataclass(frozen=True, slots=True)
class WorkloadActionExecution:
    state: Mapping[str, JsonValue]
    last_action: ActionResult
    action_record: Mapping[str, JsonValue]
    cycle_record: Mapping[str, JsonValue]


class WorkloadObservationProjector:
    def __init__(self, evidence: WorkloadEvidencePort, state: WorkloadStatePort) -> None:
        self._evidence = evidence
        self._state = state

    def project(self, observation: Observation, context: ExecutionContext) -> Mapping[str, JsonValue]:
        self._evidence.ingest_observation(observation, context)
        return dict(self._state.state(observation))


class WorkloadBootstrapPhase:
    def __init__(
        self,
        environment: WorkloadEnvironmentPort,
        boundary: WorkloadBoundaryPort | None,
        projector: WorkloadObservationProjector,
        diagnostics: WorkloadDiagnosticEmitter,
        failures: WorkloadFailureRouter,
    ) -> None:
        self._environment = environment
        self._boundary = boundary
        self._projector = projector
        self._diagnostics = diagnostics
        self._failures = failures

    def run(self, task: ExperimentTaskSpec, context: ExecutionContext) -> WorkloadTaskBootstrap:
        task_context = replace(context, task_id=task.task_id, decision_cycle_id=None)
        metadata: Mapping[str, JsonValue] = {
            "task_id": task.task_id,
            "family": task.family,
            "objective": task.objective,
            "context": task.context,
            "lineage_id": task.lineage_id,
            "status": "STARTED",
        }
        self._diagnostics.event(
            "TASK_START", level="INFO", task_id=task.task_id, family=task.family
        )
        state: Mapping[str, JsonValue] = {}
        try:
            task_event = None if self._boundary is None else self._boundary.begin(metadata, task_context)
            if task_event is not None:
                state = self._projector.project(task_event, task_context)
            initial = self._environment.observe(task_context)
            state = self._projector.project(initial, task_context)
        except Exception as exc:
            self._failures.raise_classified(
                "initial_observe",
                "WORKLOAD_INITIAL_OBSERVE_FAILED",
                exc,
            )
        return WorkloadTaskBootstrap(task_context, metadata, state)


class WorkloadDecisionPhase:
    def __init__(
        self,
        method: MethodSession,
        planner: WorkloadPlannerPort,
        failures: WorkloadFailureRouter,
    ) -> None:
        self._method = method
        self._planner = planner
        self._failures = failures

    def run(
        self,
        *,
        task: ExperimentTaskSpec,
        task_context: ExecutionContext,
        state: Mapping[str, JsonValue],
        step: int,
        prior_actions: tuple[Mapping[str, JsonValue], ...],
    ) -> WorkloadDecisionCycle:
        cycle_id = f"{task.task_id}:cycle:{step}"
        cycle_context = replace(
            task_context,
            span_id=f"{task.task_id}:span:{step}",
            parent_span_id=task_context.span_id,
            decision_cycle_id=cycle_id,
        )
        started = time.monotonic()
        try:
            recalled = self._method.recall(
                RecallRequest(task.objective, cycle_context, limit=8)
            )
            decision = self._planner.decide(
                task=task,
                context=cycle_context,
                state=state,
                memory_context=recalled.context_text,
                step=step,
                prior_actions=prior_actions,
            )
            if not isinstance(decision, WorkloadDecision):
                raise TypeError("workload planner returned an invalid decision")
        except Exception as exc:
            self._failures.raise_classified(
                "decision",
                "WORKLOAD_DECISION_FAILED",
                exc,
            )
        return WorkloadDecisionCycle(step, cycle_id, cycle_context, started, decision)


class WorkloadActionPhase:
    def __init__(
        self,
        environment: WorkloadEnvironmentPort,
        action_adapter: WorkloadActionAdapterPort,
        projector: WorkloadObservationProjector,
        diagnostics: WorkloadDiagnosticEmitter,
        failures: WorkloadFailureRouter,
    ) -> None:
        self._environment = environment
        self._action_adapter = action_adapter
        self._projector = projector
        self._diagnostics = diagnostics
        self._failures = failures

    def run(
        self,
        *,
        task: ExperimentTaskSpec,
        cycle: WorkloadDecisionCycle,
        state: Mapping[str, JsonValue],
    ) -> WorkloadActionExecution:
        decision = cycle.decision
        action_started = time.monotonic()
        action_id = self._action_adapter.action_id(task, cycle.step)
        request = ActionRequest(
            action_id,
            decision.action_type,
            dict(decision.payload),
            cycle.context,
        )
        request_digest = action_request_digest(request)
        self._diagnostics.event(
            "ACTION_STARTED",
            task_id=task.task_id,
            step=cycle.step,
            action_id=action_id,
            action_type=decision.action_type,
            action_request_digest=request_digest,
            decision_cycle_id=cycle.cycle_id,
        )
        try:
            result = require_action_result_identity(
                request,
                self._environment.act(request),
                source="workload environment",
            )
            next_state = state
            if result.observation is not None:
                next_state = self._projector.project(result.observation, cycle.context)
        except Exception as exc:
            self._diagnostics.event(
                "ACTION_FINISHED",
                level="ERROR",
                task_id=task.task_id,
                step=cycle.step,
                action_id=action_id,
                action_type=decision.action_type,
                action_request_digest=request_digest,
                decision_cycle_id=cycle.cycle_id,
                accepted=False,
                verified=None,
                duration_s=time.monotonic() - action_started,
                observation_id=None,
                observation_generation=None,
                effect_id=None,
                failure_type=type(exc).__name__,
            )
            self._failures.raise_classified("action", "WORKLOAD_ACTION_FAILED", exc)
        action_duration = time.monotonic() - action_started
        verified_raw = result.diagnostics.get("verified") if isinstance(result.diagnostics, Mapping) else None
        verified = verified_raw if isinstance(verified_raw, bool) else None
        self._diagnostics.event(
            "ACTION_FINISHED",
            level="INFO" if result.accepted else "WARNING",
            task_id=task.task_id,
            step=cycle.step,
            action_id=action_id,
            action_type=decision.action_type,
            action_request_digest=request_digest,
            decision_cycle_id=cycle.cycle_id,
            accepted=result.accepted,
            verified=verified,
            duration_s=action_duration,
            observation_id=None if result.observation is None else result.observation.observation_id,
            observation_generation=None if result.observation is None else result.observation.generation,
            effect_id=None if result.effect is None else result.effect.effect_id,
        )
        action_record: Mapping[str, JsonValue] = {
            "action_id": action_id,
            "action_type": decision.action_type,
            "payload": dict(decision.payload),
            "accepted": result.accepted,
            "verified": verified,
            "rationale": decision.rationale,
            "decision_cycle_id": cycle.cycle_id,
        }
        cycle_record: Mapping[str, JsonValue] = {
            "decision_cycle_id": cycle.cycle_id,
            "step": cycle.step,
            "action_type": decision.action_type,
            "accepted": result.accepted,
            "verified": verified,
            "action_duration_s": action_duration,
            "cycle_duration_s": time.monotonic() - cycle.started,
            "action_request_digest": request_digest,
        }
        self._diagnostics.metric(
            "action_latency_s",
            action_duration,
            labels={"family": task.family, "action": decision.action_type},
        )
        self._diagnostics.event(
            "TASK_ACTION",
            task_id=task.task_id,
            step=cycle.step,
            action_type=decision.action_type,
            verified=verified,
        )
        return WorkloadActionExecution(next_state, result, action_record, cycle_record)


def completion_records(cycle: WorkloadDecisionCycle) -> tuple[Mapping[str, JsonValue], Mapping[str, JsonValue]]:
    decision = cycle.decision
    action_record: Mapping[str, JsonValue] = {
        "action_type": decision.action_type,
        "payload": dict(decision.payload),
        "rationale": decision.rationale,
        "completion_claim": decision.completion_claim,
        "decision_cycle_id": cycle.cycle_id,
    }
    cycle_record: Mapping[str, JsonValue] = {
        "decision_cycle_id": cycle.cycle_id,
        "step": cycle.step,
        "action_type": decision.action_type,
        "cycle_duration_s": time.monotonic() - cycle.started,
    }
    return action_record, cycle_record


class WorkloadCompletionPhase:
    def __init__(
        self,
        completion: WorkloadCompletionPort,
        method: MethodSession,
        boundary: WorkloadBoundaryPort | None,
        evidence: WorkloadEvidencePort,
        diagnostics: WorkloadDiagnosticEmitter,
        failures: WorkloadFailureRouter,
    ) -> None:
        self._completion = completion
        self._method = method
        self._boundary = boundary
        self._evidence = evidence
        self._diagnostics = diagnostics
        self._failures = failures

    def is_complete(
        self,
        *,
        task: ExperimentTaskSpec,
        state: Mapping[str, JsonValue],
        planner_finished: bool,
        last_action: ActionResult | None,
    ) -> bool:
        return self._completion.is_complete(
            task=task,
            state=state,
            planner_finished=planner_finished,
            last_action=last_action,
        )

    def finalize(
        self,
        *,
        task: ExperimentTaskSpec,
        bootstrap: WorkloadTaskBootstrap,
        state: Mapping[str, JsonValue],
        actions: tuple[Mapping[str, JsonValue], ...],
        cycles: tuple[Mapping[str, JsonValue], ...],
        memory_queries: int,
        planner_finished: bool,
        last_action: ActionResult | None,
        failure_reason: str,
        started: float,
    ) -> WorkloadTaskResult:
        if time.monotonic() - started >= task.max_seconds:
            failure_reason = failure_reason or "task_timeout"
        success = (
            self.is_complete(
                task=task,
                state=state,
                planner_finished=planner_finished,
                last_action=last_action,
            )
            if not failure_reason
            else False
        )
        if not success and not failure_reason:
            failure_reason = "completion_predicate_not_satisfied"
        utility = self._completion.utility(task=task, success=success, state=state)
        try:
            completion_receipt = self._method.task_completed(
                MethodTaskOutcome(
                    task_id=task.task_id,
                    family=task.family,
                    lineage_id=task.lineage_id,
                    success=success,
                    utility=utility,
                    steps=len(actions),
                    failure_reason=failure_reason,
                    memory_queries=memory_queries,
                ),
                bootstrap.task_context,
            )
        except Exception as exc:
            self._failures.raise_classified(
                "task_completion",
                "WORKLOAD_TASK_COMPLETION_FAILED",
                exc,
            )
        try:
            end_event = None if self._boundary is None else self._boundary.end(
                {
                    **bootstrap.metadata,
                    "status": "SUCCEEDED" if success else "FAILED",
                    "failure_reason": failure_reason,
                },
                bootstrap.task_context,
            )
            if end_event is not None:
                self._evidence.ingest_observation(end_event, bootstrap.task_context)
        except Exception as exc:
            self._failures.raise_classified("task_end", "WORKLOAD_TASK_END_FAILED", exc)
        duration = time.monotonic() - started
        self._diagnostics.metric(
            "duration_s",
            duration,
            labels={"family": task.family, "result": "success" if success else "failure"},
        )
        self._diagnostics.event(
            "TASK_END",
            level="INFO" if success else "WARNING",
            task_id=task.task_id,
            success=success,
            steps=len(actions),
            failure_reason=failure_reason,
        )
        return WorkloadTaskResult(
            task_id=task.task_id,
            family=task.family,
            lineage_id=task.lineage_id,
            success=success,
            utility=utility,
            steps=len(actions),
            duration_s=duration,
            failure_reason=failure_reason,
            memory_queries=memory_queries,
            planner_actions=actions,
            decision_cycles=cycles,
            completion_receipt=completion_receipt,
            failure_scope=FailureScope.TASK.value,
            diagnostics=(
                {"diagnostic_sink_errors": self._diagnostics.errors}
                if self._diagnostics.errors
                else {}
            ),
        )


@dataclass(frozen=True, slots=True)
class WorkloadRuntimePhases:
    diagnostics: WorkloadDiagnosticEmitter
    bootstrap: WorkloadBootstrapPhase
    decision: WorkloadDecisionPhase
    action: WorkloadActionPhase
    completion: WorkloadCompletionPhase


def build_workload_runtime_phases(
    *,
    environment: WorkloadEnvironmentPort,
    method: MethodSession,
    evidence: WorkloadEvidencePort,
    planner: WorkloadPlannerPort,
    state: WorkloadStatePort,
    completion: WorkloadCompletionPort,
    failure_policy,
    diagnostics,
    boundary: WorkloadBoundaryPort | None,
    action_adapter: WorkloadActionAdapterPort,
    event_prefix: str,
    metric_prefix: str,
    max_diagnostic_errors: int,
) -> WorkloadRuntimePhases:
    emitter = WorkloadDiagnosticEmitter(
        diagnostics, event_prefix=event_prefix, metric_prefix=metric_prefix,
        max_errors=max_diagnostic_errors,
    )
    failures = WorkloadFailureRouter(failure_policy, emitter)
    projector = WorkloadObservationProjector(evidence, state)
    return WorkloadRuntimePhases(
        emitter,
        WorkloadBootstrapPhase(environment, boundary, projector, emitter, failures),
        WorkloadDecisionPhase(method, planner, failures),
        WorkloadActionPhase(environment, action_adapter, projector, emitter, failures),
        WorkloadCompletionPhase(completion, method, boundary, evidence, emitter, failures),
    )


__all__ = [
    "WorkloadActionExecution",
    "WorkloadActionPhase",
    "WorkloadBootstrapPhase",
    "WorkloadCompletionPhase",
    "WorkloadDecisionCycle",
    "WorkloadDecisionPhase",
    "WorkloadObservationProjector",
    "WorkloadRuntimePhases",
    "WorkloadTaskBootstrap",
    "build_workload_runtime_phases",
    "completion_records",
]
