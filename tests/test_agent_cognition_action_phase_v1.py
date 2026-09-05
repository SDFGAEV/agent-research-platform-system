from __future__ import annotations

import pytest

from noetrium_platform.capabilities.participant.agent.api import (
    AgentActionSequence,
    AgentActionStep,
    AgentCognitionError,
    AgentGoal,
    AgentMemoryCheckpoint,
    AgentMemoryContext,
    AgentObservation,
    AgentSafetyDecision,
    AgentSafetyDisposition,
    AgentSkillDescription,
    AgentSkillSelection,
    AgentStepReceipt,
)
from noetrium_platform.capabilities.participant.agent.runtime import AgentCognitionLoop
from noetrium_platform.capabilities.participant.agent.runtime.cognition_action import CognitionActionPhase
from noetrium_platform.capabilities.participant.agent.runtime.cognition_observation import CognitionObservationPhase
from noetrium_platform.foundation.kernel.kernel import ExecutionContext


_CONTEXT = ExecutionContext("run:1", "trace:1", "span:1")


class _Evidence:
    def __init__(self): self.observations = []
    def ingest(self, observation, context):
        del context
        self.observations.append(observation)


class _Memory:
    def __init__(self): self.receipts = []
    def recall(self, goal, observation, context):
        del goal, context
        return AgentMemoryContext("", observation.generation)
    def checkpoint(self):
        return AgentMemoryCheckpoint(0, ())

    def restore(self, checkpoint):
        assert isinstance(checkpoint, AgentMemoryCheckpoint)

    def record(self, receipt, context):
        del context
        self.receipts.append(receipt)


class _Observation:
    def __init__(self, states):
        self._states = iter(states)
        self.calls = 0
    def observe(self, context):
        del context
        self.calls += 1
        return AgentObservation(f"obs:{self.calls}", "world-v1", next(self._states))


class _Executor:
    def __init__(self, *, mismatch: bool = False): self.mismatch = mismatch
    def execute(self, step, context):
        del context
        return AgentStepReceipt(
            step.action_id,
            step.action_type,
            "wrong-skill" if self.mismatch else step.skill_id,
            step.sequence_id,
            True,
            True,
        )


def _phase(*, executor=None, observation=None, memory=None, evidence=None):
    failures = []
    observation_port = observation or _Observation(({"x": 1},))
    evidence_port = evidence or _Evidence()
    observation_phase = CognitionObservationPhase(
        observation=observation_port,
        evidence=evidence_port,
        event=lambda *args, **kwargs: None,
        failure=lambda code, message, **kwargs: failures.append((code, message, kwargs)),
    )
    return CognitionActionPhase(
        executor=executor or _Executor(),
        observation=observation_phase,
        memory=memory or _Memory(),
        event=lambda *args, **kwargs: None,
        failure=lambda code, message, **kwargs: failures.append((code, message, kwargs)),
    ), failures


def test_action_phase_rejects_full_receipt_identity_mismatch() -> None:
    phase, failures = _phase(executor=_Executor(mismatch=True))
    step = AgentActionStep("a:1", "move", {}, "skill.move", "seq:1", 0)
    with pytest.raises(AgentCognitionError) as error:
        phase.execute(step, _CONTEXT, completed_step=1)
    assert error.value.phase == "action"
    assert error.value.code == "AGENT_ACTION_FAILED"
    assert failures[-1][0] == "AGENT_ACTION_FAILED"


def test_action_phase_reobserves_when_receipt_has_no_observation() -> None:
    observation = _Observation(({"x": 2},))
    evidence = _Evidence()
    memory = _Memory()
    phase, _ = _phase(observation=observation, evidence=evidence, memory=memory)
    step = AgentActionStep("a:1", "move", {}, "skill.move", "seq:1", 0)
    result = phase.execute(step, _CONTEXT, completed_step=1)
    assert result.observation.state["x"] == 2
    assert observation.calls == 1
    assert evidence.observations == [result.observation]
    assert memory.receipts == [result.receipt]


class _Planner:
    def plan(self, request):
        return AgentSkillSelection("skill.move", {"target": request.step})


class _Skills:
    def describe(self):
        return (AgentSkillDescription("skill.move", "move", "move", "{}", True),)
    def expand(self, selection, *, observation, context, sequence_id):
        del observation, context
        step = AgentActionStep(
            f"action:{sequence_id}", "move", selection.arguments,
            selection.skill_id, sequence_id, 0,
        )
        return AgentActionSequence(sequence_id, selection.skill_id, (step,))


class _Safety:
    def review(self, goal, observation, selection, sequence, context):
        del goal, observation, selection, sequence, context
        return AgentSafetyDecision(AgentSafetyDisposition.ALLOW, "allowed", "test")


class _Completion:
    def is_complete(self, goal, observation, *, planner_finished, last_receipt):
        del goal, planner_finished, last_receipt
        return observation.state.get("done") is True


class _Progress:
    def __init__(self): self.items = []
    def persist(self, checkpoint, context):
        del context
        self.items.append(checkpoint)


def test_fallback_observation_progress_is_not_misclassified_as_stalled() -> None:
    observations = _Observation((
        {"x": 0, "done": False},
        {"x": 1, "done": False},
        {"x": 2, "done": True},
    ))
    progress = _Progress()
    loop = AgentCognitionLoop(
        observation=observations,
        planner=_Planner(),
        skills=_Skills(),
        executor=_Executor(),
        memory=_Memory(),
        safety=_Safety(),
        completion=_Completion(),
        evidence=_Evidence(),
        progress=progress,
        clock=lambda: 1.0,
    )
    result = loop.run(
        AgentGoal(
            "goal:progress",
            "move twice",
            max_steps=3,
            no_progress_limit=1,
            same_action_limit=3,
        ),
        _CONTEXT,
        session_id="session:progress",
    )
    assert result.success is True
    assert result.steps == 2
    assert result.final_observation.state["x"] == 2


def test_receipt_observation_evidence_failure_keeps_observation_phase_identity() -> None:
    class _ReceiptExecutor:
        def execute(self, step, context):
            del context
            return AgentStepReceipt(
                step.action_id,
                step.action_type,
                step.skill_id,
                step.sequence_id,
                True,
                True,
                AgentObservation("obs:receipt", "world-v1", {"x": 2}),
            )

    class _FailingEvidence:
        def ingest(self, observation, context):
            del observation, context
            raise OSError("evidence unavailable")

    phase, failures = _phase(executor=_ReceiptExecutor(), evidence=_FailingEvidence())
    step = AgentActionStep("a:receipt", "move", {}, "skill.move", "seq:receipt", 0)
    with pytest.raises(AgentCognitionError) as error:
        phase.execute(step, _CONTEXT, completed_step=1)
    assert error.value.phase == "post_action_receipt_observe"
    assert error.value.code == "AGENT_OBSERVATION_FAILED"
    assert failures[-1][0] == "AGENT_OBSERVATION_FAILED"