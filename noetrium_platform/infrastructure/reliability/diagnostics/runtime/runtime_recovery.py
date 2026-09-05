from __future__ import annotations

from dataclasses import dataclass

from noetrium_platform.infrastructure.reliability.recovery.api import (
    RecoveryActionCode,
    RecoveryAutomation,
    RecoveryDecisionReport,
)
from noetrium_platform.evidence.observability.status.api import PlatformStatus

from .runtime_recovery_classifier import classify_snapshot_recovery


@dataclass(frozen=True, slots=True)
class RuntimeAutomationAssessment:
    """Machine-actionable recovery projection with explicit unknown-state closure."""

    decision: RecoveryDecisionReport
    safe_actions: tuple[RecoveryActionCode, ...]
    conditional_actions: tuple[RecoveryActionCode, ...]
    blocked_actions: tuple[RecoveryActionCode, ...]
    unknown_reason_codes: tuple[str, ...]

    @property
    def can_run_automatically(self) -> bool:
        return bool(self.safe_actions) and not self.blocked_actions and not self.unknown_reason_codes


class RuntimeRecoveryDecisionService:
    """Pure status-to-recovery routing. It observes no stores and performs no effects."""

    def plan(self, status: PlatformStatus) -> RecoveryDecisionReport:
        return RecoveryDecisionReport(
            tuple(
                recommendation
                for snapshot in status.snapshots
                for recommendation in classify_snapshot_recovery(snapshot)
            )
        )

    def assess(self, status: PlatformStatus) -> RuntimeAutomationAssessment:
        decision = self.plan(status)
        unknown = tuple(sorted({
            code
            for item in decision.recommendations
            if item.action is RecoveryActionCode.MANUAL_DIAGNOSIS
            for code in item.reason_codes
        }))
        return RuntimeAutomationAssessment(
            decision=decision,
            safe_actions=tuple(item.action for item in decision.safe),
            conditional_actions=tuple(
                item.action for item in decision.recommendations
                if item.automation is RecoveryAutomation.CONDITIONAL
            ),
            blocked_actions=tuple(item.action for item in decision.blocked),
            unknown_reason_codes=unknown,
        )


__all__ = ["RuntimeAutomationAssessment", "RuntimeRecoveryDecisionService"]
