from __future__ import annotations

import pytest

from noetrium_platform.capabilities.participant.agent.api import (
    AgentActionSequence,
    AgentActionSummary,
    AgentGoal,
    AgentLoopCheckpoint,
    AgentMemoryCheckpoint,
    AgentMemoryContext,
    AgentObservation,
    AgentPlanningRequest,
    AgentReceiptCheckpoint,
    AgentSafetyDecision,
    AgentSafetyDisposition,
    AgentSkillDescription,
    AgentSkillSelection,
)
from noetrium_platform.capabilities.participant.agent.runtime import AgentCognitionLoop
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


class _Observation:
    def observe(self, context):
        del context
        return AgentObservation("obs:resume", "world-v1", {"goal_complete": False})


class _Memory:
    def __init__(self):
        self.restored = None

    def checkpoint(self):
        return AgentMemoryCheckpoint(0, ())

    def restore(self, checkpoint):
        self.restored = checkpoint

    def recall(self, goal, observation, context):
        del goal, context
        return AgentMemoryContext("", observation.generation)

    def record(self, receipt, context):
        raise AssertionError("resume completion must not execute another action")


class _Planner:
    def plan(self, request: AgentPlanningRequest):
        return AgentSkillSelection("finish", {}, completion_claim=True)


class _Skills:
    def describe(self):
        return (AgentSkillDescription("finish", "control", "finish goal", "{}", False),)

    def expand(self, selection, *, observation, context, sequence_id):
        del observation, context
        return AgentActionSequence(sequence_id, selection.skill_id, (), completion_claim=True)


class _Executor:
    def execute(self, step, context):
        raise AssertionError("resume completion must not execute another action")


class _Safety:
    def review(self, goal, observation, selection, sequence, context):
        del goal, observation, selection, sequence, context
        return AgentSafetyDecision(AgentSafetyDisposition.ALLOW, "allowed", "test")


class _Completion:
    def is_complete(self, goal, observation, *, planner_finished, last_receipt):
        del goal, observation
        return bool(
            planner_finished
            and last_receipt
            and last_receipt.accepted
            and last_receipt.verified is True
            and last_receipt.effect_id == "effect:1"
            and last_receipt.effect_certainty == "confirmed"
        )


class _Evidence:
    def ingest(self, observation, context):
        del observation, context


class _Progress:
    def __init__(self):
        self.checkpoints = []

    def persist(self, checkpoint, context):
        del context
        self.checkpoints.append(checkpoint)


def _checkpoint(goal: AgentGoal) -> AgentLoopCheckpoint:
    summary = AgentActionSummary(
        "action:1", "move", "skill.move", True, True,
        observation_digest="state:prior",
    )
    receipt = AgentReceiptCheckpoint(
        "action:1", "move", "skill.move", "sequence:1", True, True,
        effect_id="effect:1", effect_certainty="confirmed",
    )
    return AgentLoopCheckpoint(
        "agent-cognition-checkpoint.v2", "session:1", goal.digest,
        1, 1, 0, 1, "state:prior", (summary,), receipt,
        AgentMemoryCheckpoint(4, ()),
    )


def test_resume_reconstructs_last_receipt_for_completion_semantics() -> None:
    goal = AgentGoal("goal:1", "finish safely", max_steps=3)
    progress = _Progress()
    memory = _Memory()
    checkpoint = _checkpoint(goal)
    loop = AgentCognitionLoop(
        observation=_Observation(), planner=_Planner(), skills=_Skills(), executor=_Executor(),
        memory=memory, safety=_Safety(), completion=_Completion(), evidence=_Evidence(),
        progress=progress, clock=lambda: 1.0,
    )
    result = loop.run(
        goal,
        ExecutionContext("run:1", "trace:1", "span:1"),
        session_id="session:1",
        checkpoint=checkpoint,
    )

    assert result.success is True
    assert result.steps == 1
    assert memory.restored == checkpoint.memory_checkpoint
    assert result.action_receipts == ()
    assert result.checkpoint.last_receipt is not None
    assert result.checkpoint.last_receipt.effect_id == "effect:1"
    assert progress.checkpoints[-1].last_receipt == result.checkpoint.last_receipt


def test_checkpoint_rejects_trajectory_without_recovery_receipt() -> None:
    goal = AgentGoal("goal:1", "finish safely")
    summary = AgentActionSummary("action:1", "move", "skill.move", True, True)
    with pytest.raises(ValueError, match="trajectory/receipt"):
        AgentLoopCheckpoint(
            "agent-cognition-checkpoint.v2",
            "session:1",
            goal.digest,
            1,
            1,
            0,
            1,
            "state:prior",
            (summary,),
            None,
        )
