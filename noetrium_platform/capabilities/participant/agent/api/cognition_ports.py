from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonScalar

from .cognition import (
    AgentActionSequence,
    AgentActionStep,
    AgentGoal,
    AgentLoopCheckpoint,
    AgentMemoryContext,
    AgentModeDecision,
    AgentObservation,
    AgentPlanningRequest,
    AgentSafetyDecision,
    AgentSkillDescription,
    AgentSkillRecord,
    AgentSkillSelection,
    AgentStepReceipt,
)


class AgentObservationPort(Protocol):
    def observe(self, context: ExecutionContext) -> AgentObservation: ...


class AgentPlannerPort(Protocol):
    def plan(self, request: AgentPlanningRequest) -> AgentSkillSelection: ...


class AgentSkillCatalogPort(Protocol):
    def describe(self) -> tuple[AgentSkillDescription, ...]: ...

    def expand(
        self,
        selection: AgentSkillSelection,
        *,
        observation: AgentObservation,
        context: ExecutionContext,
        sequence_id: str,
    ) -> AgentActionSequence: ...


class AgentSkillLibraryPort(Protocol):
    """Persistent structured skill memory; implementations own storage."""

    def search(
        self,
        goal: AgentGoal,
        observation: AgentObservation,
        *,
        limit: int,
        context: ExecutionContext,
    ) -> tuple[AgentSkillRecord, ...]: ...

    def record(
        self,
        sequence: AgentActionSequence,
        receipts: tuple[AgentStepReceipt, ...],
        *,
        success: bool,
        context: ExecutionContext,
    ) -> None: ...


class AgentActionExecutorPort(Protocol):
    def execute(self, step: AgentActionStep, context: ExecutionContext) -> AgentStepReceipt: ...


class AgentMemoryPort(Protocol):
    def checkpoint(self) -> "AgentMemoryCheckpoint": ...

    def restore(self, checkpoint: AgentMemoryCheckpoint) -> None: ...

    def recall(
        self,
        goal: AgentGoal,
        observation: AgentObservation,
        context: ExecutionContext,
    ) -> AgentMemoryContext: ...

    def record(self, receipt: AgentStepReceipt, context: ExecutionContext) -> None: ...


class AgentSafetySupervisorPort(Protocol):
    def review(
        self,
        goal: AgentGoal,
        observation: AgentObservation,
        selection: AgentSkillSelection,
        sequence: AgentActionSequence,
        context: ExecutionContext,
    ) -> AgentSafetyDecision: ...


class AgentReactiveModePort(Protocol):
    """Review a selected sequence against higher-priority world modes."""

    def review(
        self,
        goal: AgentGoal,
        observation: AgentObservation,
        selection: AgentSkillSelection,
        sequence: AgentActionSequence,
        context: ExecutionContext,
    ) -> AgentModeDecision | None: ...


class AgentCompletionPort(Protocol):
    def is_complete(
        self,
        goal: AgentGoal,
        observation: AgentObservation,
        *,
        planner_finished: bool,
        last_receipt: AgentStepReceipt | None,
    ) -> bool: ...


class AgentEvidencePort(Protocol):
    def ingest(self, observation: AgentObservation, context: ExecutionContext) -> None: ...


class AgentProgressPort(Protocol):
    def persist(self, checkpoint: AgentLoopCheckpoint, context: ExecutionContext) -> None: ...


class AgentDiagnosticsPort(Protocol):
    def event(
        self, event: str, *, level: str = "DEBUG",
        attributes: Mapping[str, JsonScalar] | None = None,
    ) -> None: ...

    def failure(self, code: str, message: str, *, phase: str) -> None: ...


__all__ = [
    "AgentActionExecutorPort",
    "AgentCompletionPort",
    "AgentDiagnosticsPort",
    "AgentEvidencePort",
    "AgentMemoryPort",
    "AgentObservationPort",
    "AgentPlannerPort",
    "AgentProgressPort",
    "AgentReactiveModePort",
    "AgentSafetySupervisorPort",
    "AgentSkillCatalogPort",
    "AgentSkillLibraryPort",
]
