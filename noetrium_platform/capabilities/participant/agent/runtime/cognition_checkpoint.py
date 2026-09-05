from __future__ import annotations

from typing import Callable

from noetrium_platform.foundation.kernel.kernel import ExecutionContext

from ..api.cognition import (
    AgentActionSummary,
    AgentCognitionError,
    AgentGoal,
    AgentLoopCheckpoint,
    AgentLoopResult,
    AgentLoopTerminationReason,
    AgentObservation,
    AgentReceiptCheckpoint,
    AgentStepReceipt,
)
from ..api.cognition_ports import AgentMemoryPort, AgentProgressPort
from .cognition_state import CognitionCounters


FailureSink = Callable[..., None]


class CognitionCheckpointPhase:
    """Own durable cognition checkpoint construction and publication."""

    def __init__(self, *, progress: AgentProgressPort, memory: AgentMemoryPort, failure: FailureSink) -> None:
        self._progress = progress
        self._memory = memory
        self._failure = failure

    def persist(
        self,
        *,
        goal: AgentGoal,
        session_id: str,
        counters: CognitionCounters,
        observation: AgentObservation,
        summaries: tuple[AgentActionSummary, ...],
        last_receipt: AgentStepReceipt | None,
        context: ExecutionContext,
    ) -> AgentLoopCheckpoint:
        try:
            memory_checkpoint = self._memory.checkpoint()
        except BaseException as exc:
            self._failure("AGENT_CHECKPOINT_FAILED", str(exc), phase="checkpoint")
            raise AgentCognitionError(
                "checkpoint", "AGENT_CHECKPOINT_FAILED", str(exc), cause=exc
            ) from exc
        checkpoint = AgentLoopCheckpoint(
            schema_version="agent-cognition-checkpoint.v2",
            session_id=session_id,
            goal_digest=goal.digest,
            step=counters.step,
            plan_calls=counters.plan_calls,
            no_progress_steps=counters.no_progress_steps,
            same_action_runs=counters.same_action_runs,
            last_observation_digest=observation.state_digest,
            action_summaries=summaries,
            last_receipt=(
                None if last_receipt is None
                else AgentReceiptCheckpoint.from_receipt(last_receipt)
            ),
            memory_checkpoint=memory_checkpoint,
        )
        try:
            self._progress.persist(checkpoint, context)
        except BaseException as exc:
            self._failure("AGENT_CHECKPOINT_FAILED", str(exc), phase="checkpoint")
            raise AgentCognitionError(
                "checkpoint", "AGENT_CHECKPOINT_FAILED", str(exc), cause=exc
            ) from exc
        return checkpoint


def build_cognition_result(
    *,
    success: bool,
    termination: AgentLoopTerminationReason,
    counters: CognitionCounters,
    memory_queries: int,
    selected_skills: tuple[str, ...],
    receipts: tuple[AgentStepReceipt, ...],
    observation: AgentObservation,
    checkpoint: AgentLoopCheckpoint,
    failure_code: str = "",
    diagnostics: dict[str, object] | None = None,
) -> AgentLoopResult:
    combined_diagnostics: dict[str, object] = {
        "checkpoint_digest": checkpoint.digest,
        "last_observation_digest": observation.state_digest,
        "plan_calls": counters.plan_calls,
    }
    if diagnostics:
        combined_diagnostics.update(diagnostics)
    return AgentLoopResult(
        success=success,
        termination=termination,
        steps=counters.step,
        plan_calls=counters.plan_calls,
        memory_queries=memory_queries,
        selected_skills=selected_skills,
        action_receipts=receipts,
        final_observation=observation,
        checkpoint=checkpoint,
        failure_code=failure_code,
        diagnostics=combined_diagnostics,
    )


__all__ = ["CognitionCheckpointPhase", "build_cognition_result"]
