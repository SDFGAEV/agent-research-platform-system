from __future__ import annotations

import pytest

from noetrium_platform.capabilities.participant.agent.api import (
    AgentCognitionError,
    AgentGoal,
    AgentLoopTerminationReason,
    AgentObservation,
)
from noetrium_platform.capabilities.participant.agent.runtime import InMemoryAgentMemory
from noetrium_platform.capabilities.participant.agent.runtime.cognition_checkpoint import (
    CognitionCheckpointPhase,
    build_cognition_result,
)
from noetrium_platform.capabilities.participant.agent.runtime.cognition_state import CognitionCounters
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


_CONTEXT = ExecutionContext("run:state", "trace:state", "span:state")


class _Progress:
    def __init__(self, error=None):
        self.items = []
        self.error = error

    def persist(self, checkpoint, context):
        del context
        if self.error is not None:
            raise self.error
        self.items.append(checkpoint)


def test_cognition_counters_advance_monotonically() -> None:
    counters = CognitionCounters(plan_calls=2, no_progress_steps=3, same_action_runs=2)
    counters = counters.with_plan_calls(3)
    counters = counters.after_action(progressed=True, repeated_action=True)
    assert counters.step == 1
    assert counters.plan_calls == 3
    assert counters.no_progress_steps == 0
    assert counters.same_action_runs == 3
    with pytest.raises(ValueError, match="cannot move backwards"):
        counters.with_plan_calls(2)


def test_cognition_counters_track_no_progress_and_new_action_run() -> None:
    counters = CognitionCounters(step=4, no_progress_steps=1, same_action_runs=5)
    next_value = counters.after_action(progressed=False, repeated_action=False)
    assert next_value.step == 5
    assert next_value.no_progress_steps == 2
    assert next_value.same_action_runs == 1


def test_checkpoint_phase_persists_exact_runtime_state() -> None:
    progress = _Progress()
    failures = []
    memory = InMemoryAgentMemory()
    phase = CognitionCheckpointPhase(
        progress=progress,
        memory=memory,
        failure=lambda code, message, **kwargs: failures.append((code, message, kwargs)),
    )
    goal = AgentGoal("goal:state", "persist state")
    observation = AgentObservation("obs:state", "world-v1", {"x": 3})
    counters = CognitionCounters(step=2, plan_calls=4, no_progress_steps=1, same_action_runs=2)
    checkpoint = phase.persist(
        goal=goal,
        session_id="session:state",
        counters=counters,
        observation=observation,
        summaries=(),
        last_receipt=None,
        context=_CONTEXT,
    )
    assert progress.items == [checkpoint]
    assert checkpoint.step == 2
    assert checkpoint.plan_calls == 4
    assert checkpoint.last_observation_digest == observation.state_digest
    assert checkpoint.memory_checkpoint is not None
    assert checkpoint.memory_checkpoint == memory.checkpoint()
    assert failures == []


def test_checkpoint_phase_wraps_persistence_failure() -> None:
    failures = []
    phase = CognitionCheckpointPhase(
        progress=_Progress(RuntimeError("disk unavailable")),
        memory=InMemoryAgentMemory(),
        failure=lambda code, message, **kwargs: failures.append((code, message, kwargs)),
    )
    with pytest.raises(AgentCognitionError) as error:
        phase.persist(
            goal=AgentGoal("goal:failure", "persist state"),
            session_id="session:failure",
            counters=CognitionCounters(),
            observation=AgentObservation("obs:failure", "world-v1", {"x": 0}),
            summaries=(),
            last_receipt=None,
            context=_CONTEXT,
        )
    assert error.value.phase == "checkpoint"
    assert error.value.code == "AGENT_CHECKPOINT_FAILED"
    assert failures[-1][0] == "AGENT_CHECKPOINT_FAILED"


def test_result_builder_binds_checkpoint_and_observation_diagnostics() -> None:
    progress = _Progress()
    phase = CognitionCheckpointPhase(progress=progress, memory=InMemoryAgentMemory(), failure=lambda *args, **kwargs: None)
    observation = AgentObservation("obs:result", "world-v1", {"done": True})
    counters = CognitionCounters(step=1, plan_calls=2)
    checkpoint = phase.persist(
        goal=AgentGoal("goal:result", "finish"),
        session_id="session:result",
        counters=counters,
        observation=observation,
        summaries=(),
        last_receipt=None,
        context=_CONTEXT,
    )
    result = build_cognition_result(
        success=True,
        termination=AgentLoopTerminationReason.COMPLETED,
        counters=counters,
        memory_queries=3,
        selected_skills=(),
        receipts=(),
        observation=observation,
        checkpoint=checkpoint,
    )
    assert result.diagnostics["checkpoint_digest"] == checkpoint.digest
    assert result.diagnostics["last_observation_digest"] == observation.state_digest
    assert result.plan_calls == 2


def test_goal_and_observation_detach_from_caller_owned_json() -> None:
    context = {"route": [{"x": 1}]}
    state = {"inventory": [{"item": "oak"}]}
    evidence = {"proof": [{"ok": True}]}
    goal = AgentGoal("goal:immutable", "hold immutable context", context=context)
    observation = AgentObservation(
        "obs:immutable", "world-v1", state, evidence_payload=evidence
    )

    context["route"][0]["x"] = 2
    state["inventory"][0]["item"] = "stone"
    evidence["proof"][0]["ok"] = False
    assert goal.context["route"][0]["x"] == 1
    assert observation.state["inventory"][0]["item"] == "oak"
    assert observation.evidence_payload["proof"][0]["ok"] is True

    with pytest.raises((TypeError, AttributeError)):
        goal.context["route"].append({"x": 3})
    with pytest.raises(TypeError):
        observation.state["inventory"][0]["item"] = "tampered"
