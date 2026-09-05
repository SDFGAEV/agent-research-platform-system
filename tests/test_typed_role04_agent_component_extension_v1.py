from __future__ import annotations

from noetrium_platform.capabilities.participant.agent.runtime import AgentCognitionLoop
from noetrium_platform.capabilities.participant.agent.api import (
    AgentActionSequence,
    AgentActionStep,
    AgentGoal,
    AgentMemoryContext,
    AgentObservation,
    AgentSafetyDecision,
    AgentSafetyDisposition,
    AgentSkillDescription,
    AgentSkillSelection,
    AgentStepReceipt,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


class PaperOnlyPlanner:
    """Synthetic downstream component; its algorithm/name is absent from Platform source."""

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, request):
        self.calls += 1
        return AgentSkillSelection("paper.step", {"ordinal": request.step})


class Observation:
    def __init__(self) -> None:
        self.calls = 0

    def observe(self, context):
        del context
        self.calls += 1
        return AgentObservation(
            f"obs:{self.calls}", "world-v1", {"done": self.calls >= 2, "step": self.calls}
        )


class Skills:
    def describe(self):
        return (AgentSkillDescription("paper.step", "paper", "step", "{}", True),)

    def expand(self, selection, *, observation, context, sequence_id):
        del observation, context
        return AgentActionSequence(
            sequence_id,
            selection.skill_id,
            (AgentActionStep(f"action:{sequence_id}", "paper", selection.arguments, selection.skill_id, sequence_id, 0),),
        )


class Executor:
    def execute(self, step, context):
        del context
        return AgentStepReceipt(step.action_id, step.action_type, step.skill_id, step.sequence_id, True, True)


class Memory:
    def recall(self, goal, observation, context):
        del goal, context
        return AgentMemoryContext("", observation.generation)

    def record(self, receipt, context):
        del receipt, context


class Safety:
    def review(self, goal, observation, selection, sequence, context):
        del goal, observation, selection, sequence, context
        return AgentSafetyDecision(AgentSafetyDisposition.ALLOW, "allowed", "paper-test")


class Completion:
    def is_complete(self, goal, observation, *, planner_finished, last_receipt):
        del goal, planner_finished, last_receipt
        return observation.state["done"] is True


class Evidence:
    def ingest(self, observation, context):
        del observation, context


class Progress:
    def __init__(self) -> None:
        self.checkpoints = []

    def persist(self, checkpoint, context):
        del context
        self.checkpoints.append(checkpoint)


def test_public_agent_facade_supports_downstream_planner_replacement_without_private_imports():
    planner = PaperOnlyPlanner()
    observation = Observation()
    progress = Progress()
    loop = AgentCognitionLoop(
        observation=observation,
        planner=planner,
        skills=Skills(),
        executor=Executor(),
        memory=Memory(),
        safety=Safety(),
        completion=Completion(),
        evidence=Evidence(),
        progress=progress,
        clock=lambda: 1.0,
    )

    result = loop.run(
        AgentGoal("paper-goal", "exercise custom planner", max_steps=2),
        ExecutionContext("run-paper", "trace-paper", "span-paper"),
        session_id="paper-session",
    )

    assert result.success is True
    assert result.steps == 1
    assert planner.calls == 1
    assert observation.calls == 2
    assert progress.checkpoints
