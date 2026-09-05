from __future__ import annotations

from dataclasses import replace
import time
from typing import Callable

from noetrium_platform.foundation.kernel.kernel import ExecutionContext
from noetrium_platform.foundation.kernel.kernel.errors import describe_exception, redact_text

from ..api.cognition import (
    AgentCognitionError,
    AgentGoal,
    AgentLoopCheckpoint,
    AgentLoopResult,
    AgentLoopTerminationReason,
    AgentObservation,
    AgentStepReceipt,
)
from ..api.cognition_ports import (
    AgentActionExecutorPort,
    AgentCompletionPort,
    AgentDiagnosticsPort,
    AgentEvidencePort,
    AgentMemoryPort,
    AgentObservationPort,
    AgentPlannerPort,
    AgentProgressPort,
    AgentReactiveModePort,
    AgentSafetySupervisorPort,
    AgentSkillCatalogPort,
    AgentSkillLibraryPort,
)
from .cognition_action import CognitionActionPhase
from .cognition_checkpoint import CognitionCheckpointPhase, build_cognition_result
from .cognition_context import CognitionContextPhase
from .cognition_observation import CognitionObservationPhase
from .cognition_planning import CognitionPlanningPhase, PlanningDisposition
from .cognition_reasoning import CognitionReasoningPhase
from .cognition_state import CognitionCounters


class AgentCognitionLoop:
    """Durable, environment-neutral cognition loop.

    The loop deliberately owns only cognition sequencing.  It does not know
    environment actions, model providers, storage backends, or experiment
    semantics.  Those concerns enter through the typed ports and therefore
    remain replaceable while every decision, action, observation, and
    checkpoint is still attributable to one goal and one execution context.
    """

    def __init__(
        self,
        *,
        observation: AgentObservationPort,
        planner: AgentPlannerPort,
        skills: AgentSkillCatalogPort,
        executor: AgentActionExecutorPort,
        memory: AgentMemoryPort,
        safety: AgentSafetySupervisorPort,
        completion: AgentCompletionPort,
        evidence: AgentEvidencePort,
        progress: AgentProgressPort,
        skill_library: AgentSkillLibraryPort | None = None,
        reactive_modes: AgentReactiveModePort | None = None,
        diagnostics: AgentDiagnosticsPort | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.completion = completion
        self.diagnostics = diagnostics
        self._memory = memory
        self.clock = clock
        self._diagnostic_failures: list[dict[str, object]] = []
        self._observation_phase = CognitionObservationPhase(
            observation=observation,
            evidence=evidence,
            event=self._event,
            failure=self._failure,
        )
        self._context_phase = CognitionContextPhase(
            memory=memory,
            skill_library=skill_library,
            failure=self._failure,
        )
        self._planning = CognitionPlanningPhase(
            skills=skills,
            safety=safety,
            completion=completion,
            skill_library=skill_library,
            reactive_modes=reactive_modes,
            event=self._event,
            failure=self._failure,
        )
        self._reasoning = CognitionReasoningPhase(
            planner=planner,
            available_skills=self._planning.available_skills,
            failure=self._failure,
        )
        self._action = CognitionActionPhase(
            executor=executor,
            observation=self._observation_phase,
            memory=memory,
            event=self._event,
            failure=self._failure,
        )
        self._checkpoint_phase = CognitionCheckpointPhase(
            progress=progress,
            memory=memory,
            failure=self._failure,
        )

    def _event(self, name: str, *, level: str = "DEBUG", **attributes: object) -> None:
        if self.diagnostics is None:
            return
        normalized = {
            key: value
            for key, value in attributes.items()
            if value is None or isinstance(value, (str, int, float, bool))
        }
        try:
            self.diagnostics.event(name, level=level, attributes=normalized)
        except Exception as exc:
            # A diagnostic sink must never mask an environment or planner
            # result, but its failure is itself observable forensic evidence.
            self._record_diagnostic_failure("event", name, exc)
            return

    def _failure(self, code: str, message: str, *, phase: str) -> None:
        if self.diagnostics is None:
            return
        try:
            self.diagnostics.failure(code, redact_text(message), phase=phase)
        except Exception as exc:
            self._record_diagnostic_failure("failure", code, exc, phase=phase)
            return

    def _record_diagnostic_failure(
        self,
        operation: str,
        code: str,
        error: Exception,
        *,
        phase: str | None = None,
    ) -> None:
        self._diagnostic_failures.append(
            {
                "operation": operation,
                "code": code,
                "phase": phase,
                "error_type": type(error).__name__,
                "error_message": describe_exception(error).safe_message,
            }
        )

    def diagnostic_failures(self) -> tuple[dict[str, object], ...]:
        """Return auxiliary diagnostic-sink failures without masking primary work."""

        return tuple(dict(item) for item in self._diagnostic_failures)

    @staticmethod
    def _context(context: ExecutionContext, goal: AgentGoal, suffix: str) -> ExecutionContext:
        return replace(
            context,
            task_id=goal.goal_id,
            decision_cycle_id=f"{goal.goal_id}:{suffix}",
            component_id="participant.agent.cognition",
        )

    def run(
        self,
        goal: AgentGoal,
        context: ExecutionContext,
        *,
        session_id: str | None = None,
        checkpoint: AgentLoopCheckpoint | None = None,
    ) -> AgentLoopResult:
        run_session_id = session_id or f"{context.run_id}:{goal.goal_id}"
        if checkpoint is not None and checkpoint.goal_digest != goal.digest:
            raise ValueError("agent cognition checkpoint belongs to another goal")
        if checkpoint is not None and checkpoint.session_id != run_session_id:
            raise ValueError("agent cognition checkpoint belongs to another session")
        if checkpoint is not None and checkpoint.memory_checkpoint is not None:
            try:
                self._memory.restore(checkpoint.memory_checkpoint)
            except BaseException as exc:
                self._failure("AGENT_MEMORY_RESTORE_FAILED", str(exc), phase="restore")
                raise AgentCognitionError(
                    "restore", "AGENT_MEMORY_RESTORE_FAILED", str(exc), cause=exc
                ) from exc
        counters = CognitionCounters.from_checkpoint(checkpoint)
        summaries = list(checkpoint.action_summaries if checkpoint is not None else ())
        receipts: list[AgentStepReceipt] = []
        selected_skills: list[str] = [summary.skill_id for summary in summaries]
        memory_queries = 0
        invalid_completion_claims = 0
        started = self.clock()
        loop_context = self._context(context, goal, "observe:initial")
        observation = self._observation_phase.observe(loop_context, phase="initial_observe")
        last_action_type = summaries[-1].action_type if summaries else ""
        last_receipt = None if checkpoint is None or checkpoint.last_receipt is None else checkpoint.last_receipt.to_receipt()

        while counters.step < goal.max_steps:
            if self.clock() - started > goal.max_seconds:
                checkpoint_value = self._checkpoint_phase.persist(
                    goal=goal, session_id=run_session_id, counters=counters,
                    observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=loop_context,
                )
                return build_cognition_result(
                    success=False, termination=AgentLoopTerminationReason.TIMEOUT,
                    counters=counters, memory_queries=memory_queries,
                    selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                    observation=observation, checkpoint=checkpoint_value,
                    failure_code="AGENT_LOOP_TIMEOUT",
                )

            try:
                initially_complete = self.completion.is_complete(
                    goal, observation, planner_finished=False, last_receipt=last_receipt
                )
            except AgentCognitionError:
                raise
            except BaseException as exc:
                self._failure("AGENT_PLANNING_FAILED", str(exc), phase="planning")
                raise AgentCognitionError("planning", "AGENT_PLANNING_FAILED", str(exc), cause=exc) from exc
            if initially_complete:
                checkpoint_value = self._checkpoint_phase.persist(
                    goal=goal, session_id=run_session_id, counters=counters,
                    observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=loop_context,
                )
                return build_cognition_result(
                    success=True, termination=AgentLoopTerminationReason.COMPLETED,
                    counters=counters, memory_queries=memory_queries,
                    selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                    observation=observation, checkpoint=checkpoint_value,
                )

            plan_context = self._context(context, goal, f"plan:{counters.plan_calls}")
            context_snapshot = self._context_phase.gather(
                goal=goal,
                observation=observation,
                context=plan_context,
            )
            memory_queries += 1
            reasoning = self._reasoning.reason(
                goal=goal,
                observation=observation,
                context_snapshot=context_snapshot,
                plan_context=plan_context,
                step=counters.step,
                plan_call=counters.plan_calls,
                prior_actions=tuple(summaries),
            )
            planning = self._planning.plan(
                selection=reasoning.selection,
                goal=goal,
                observation=observation,
                plan_context=plan_context,
                plan_call=counters.plan_calls,
                last_receipt=last_receipt,
            )
            counters = counters.with_plan_calls(planning.next_plan_call)

            if planning.disposition is PlanningDisposition.SAFETY_ABORT:
                checkpoint_value = self._checkpoint_phase.persist(
                    goal=goal, session_id=run_session_id, counters=counters,
                    observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=plan_context,
                )
                return build_cognition_result(
                    success=False, termination=AgentLoopTerminationReason.SAFETY_ABORT,
                    counters=counters, memory_queries=memory_queries,
                    selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                    observation=observation, checkpoint=checkpoint_value,
                    failure_code="AGENT_SAFETY_ABORT",
                )
            if planning.disposition is PlanningDisposition.MODE_ABORT:
                checkpoint_value = self._checkpoint_phase.persist(
                    goal=goal, session_id=run_session_id, counters=counters,
                    observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=plan_context,
                )
                return build_cognition_result(
                    success=False, termination=AgentLoopTerminationReason.INTERRUPTED,
                    counters=counters, memory_queries=memory_queries,
                    selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                    observation=observation, checkpoint=checkpoint_value,
                    failure_code="AGENT_MODE_ABORT",
                )
            if planning.disposition is PlanningDisposition.COMPLETED:
                checkpoint_value = self._checkpoint_phase.persist(
                    goal=goal, session_id=run_session_id, counters=counters,
                    observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=plan_context,
                )
                return build_cognition_result(
                    success=True, termination=AgentLoopTerminationReason.COMPLETED,
                    counters=counters, memory_queries=memory_queries,
                    selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                    observation=observation, checkpoint=checkpoint_value,
                )
            if planning.disposition is PlanningDisposition.UNGROUNDED_COMPLETION:
                invalid_completion_claims += 1
                if invalid_completion_claims > goal.max_replans:
                    checkpoint_value = self._checkpoint_phase.persist(
                        goal=goal, session_id=run_session_id, counters=counters,
                        observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=plan_context,
                    )
                    return build_cognition_result(
                        success=False, termination=AgentLoopTerminationReason.INVALID_PLAN,
                        counters=counters, memory_queries=memory_queries,
                        selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                        observation=observation, checkpoint=checkpoint_value,
                        failure_code="AGENT_UNGROUNDED_COMPLETION_CLAIM",
                    )
                continue
            if planning.disposition is PlanningDisposition.REPLAN:
                continue
            sequence = planning.sequence

            if not sequence.steps:
                raise AgentCognitionError("planning", "AGENT_EMPTY_SEQUENCE", "non-completion sequence is empty")
            sequence_failed = False
            sequence_receipts: list[AgentStepReceipt] = []
            for step in sequence.steps:
                if counters.step >= goal.max_steps:
                    break
                action_context = self._context(context, goal, f"cycle:{counters.step}")
                previous_digest = observation.state_digest
                action_result = self._action.execute(
                    step,
                    action_context,
                    completed_step=counters.step + 1,
                )
                receipt = action_result.receipt
                observation = action_result.observation
                receipts.append(receipt)
                sequence_receipts.append(receipt)
                summaries.append(action_result.summary)
                selected_skills.append(step.skill_id)
                counters = counters.after_action(
                    progressed=observation.state_digest != previous_digest,
                    repeated_action=step.action_type == last_action_type,
                )
                last_action_type = step.action_type
                last_receipt = receipt
                checkpoint_value = self._checkpoint_phase.persist(
                    goal=goal, session_id=run_session_id, counters=counters,
                    observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=action_context,
                )
                if self.completion.is_complete(
                    goal, observation, planner_finished=False, last_receipt=last_receipt
                ):
                    self._planning.record_skill(
                        sequence, tuple(sequence_receipts), success=True, context=action_context
                    )
                    return build_cognition_result(
                        success=True, termination=AgentLoopTerminationReason.COMPLETED,
                        counters=counters, memory_queries=memory_queries,
                        selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                        observation=observation, checkpoint=checkpoint_value,
                    )
                if not receipt.accepted:
                    sequence_failed = True
                    break
                if counters.no_progress_steps >= goal.no_progress_limit:
                    self._planning.record_skill(
                        sequence, tuple(sequence_receipts), success=False, context=action_context
                    )
                    return build_cognition_result(
                        success=False, termination=AgentLoopTerminationReason.STALLED,
                        counters=counters, memory_queries=memory_queries,
                        selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                        observation=observation, checkpoint=checkpoint_value,
                        failure_code="AGENT_NO_PROGRESS",
                    )
                if counters.same_action_runs >= goal.same_action_limit:
                    self._planning.record_skill(
                        sequence, tuple(sequence_receipts), success=False, context=action_context
                    )
                    return build_cognition_result(
                        success=False, termination=AgentLoopTerminationReason.STALLED,
                        counters=counters, memory_queries=memory_queries,
                        selected_skills=tuple(selected_skills), receipts=tuple(receipts),
                        observation=observation, checkpoint=checkpoint_value,
                        failure_code="AGENT_REPEATED_ACTION",
                    )
            self._planning.record_skill(
                sequence,
                tuple(sequence_receipts),
                success=not sequence_failed and self.completion.is_complete(
                    goal, observation, planner_finished=False, last_receipt=last_receipt
                ),
                context=loop_context,
            )
            if sequence_failed:
                # The failed receipt remains in the trajectory; the next plan
                # receives it through prior_actions and may choose recovery.
                continue

        checkpoint_value = self._checkpoint_phase.persist(
            goal=goal, session_id=run_session_id, counters=counters,
            observation=observation, summaries=tuple(summaries), last_receipt=last_receipt, context=loop_context,
        )
        return build_cognition_result(
            success=False, termination=AgentLoopTerminationReason.MAX_STEPS,
            counters=counters, memory_queries=memory_queries,
            selected_skills=tuple(selected_skills), receipts=tuple(receipts),
            observation=observation, checkpoint=checkpoint_value,
            failure_code="AGENT_MAX_STEPS",
        )


__all__ = ["AgentCognitionLoop"]
