from __future__ import annotations

import unittest

from noetrium_platform.infrastructure.reliability.diagnostics.runtime import RuntimeRecoveryDecisionService
from noetrium_platform.infrastructure.reliability.recovery.api import RecoveryActionCode, RecoveryAutomation
from noetrium_platform.evidence.observability.status.api import HealthState, PlatformStatus, SubsystemSnapshot


class RuntimeRecoveryDecisionV170Tests(unittest.TestCase):
    def plan(self, *snapshots: SubsystemSnapshot):
        return RuntimeRecoveryDecisionService().plan(PlatformStatus(tuple(snapshots)))

    def test_identity_drift_fails_closed_and_suppresses_mutating_suggestions(self):
        report = self.plan(SubsystemSnapshot(
            "server_session",
            HealthState.DEGRADED_OPERATIONAL,
            "drift",
            reason_codes=("controller_command_drift", "session_missing"),
        ))
        self.assertTrue(report.blocked)
        self.assertEqual(len(report.recommendations), 1)
        item = report.recommendations[0]
        self.assertIs(item.action, RecoveryActionCode.BLOCK_IDENTITY_DRIFT)
        self.assertIs(item.automation, RecoveryAutomation.FORBIDDEN)
        self.assertEqual(item.reason_codes, ("controller_command_drift",))

    def test_concurrent_runtime_conditions_remain_separate_recovery_steps(self):
        report = self.plan(SubsystemSnapshot(
            "runtime",
            HealthState.DEGRADED_EVIDENCE,
            "running plus tail mismatch",
            reason_codes=("runtime_transaction_in_progress", "runtime_history_tail_mismatch"),
        ))
        self.assertFalse(report.blocked)
        self.assertEqual(
            [item.action for item in report.recommendations],
            [
                RecoveryActionCode.RECONCILE_RUNTIME_HISTORY,
                RecoveryActionCode.RECONCILE_RUNTIME_TRANSACTION,
            ],
        )
        self.assertTrue(all(item.automation is RecoveryAutomation.CONDITIONAL for item in report.recommendations))

    def test_disposable_forensic_projection_is_the_only_unconditional_safe_action(self):
        report = self.plan(SubsystemSnapshot(
            "forensics",
            HealthState.DEGRADED_EVIDENCE,
            "projection stale",
            reason_codes=("forensic_projection_stale",),
        ))
        self.assertEqual(len(report.safe), 1)
        self.assertIs(report.safe[0].action, RecoveryActionCode.REBUILD_DERIVED_STATE)
        self.assertEqual(report.safe[0].required_checks, ())

    def test_missing_controller_requires_cross_authority_checks_before_reconcile(self):
        report = self.plan(SubsystemSnapshot(
            "server_session",
            HealthState.DEGRADED_OPERATIONAL,
            "missing",
            reason_codes=("session_missing",),
        ))
        item = report.recommendations[0]
        self.assertIs(item.action, RecoveryActionCode.RECONCILE_PERSISTENT_SESSION)
        self.assertIs(item.automation, RecoveryAutomation.CONDITIONAL)
        self.assertIn("verify_service_state", item.required_checks)
        self.assertIn("verify_runtime_state", item.required_checks)

    def test_unknown_machine_reason_never_becomes_automatic_recovery(self):
        report = self.plan(SubsystemSnapshot(
            "future_component",
            HealthState.FAILED,
            "future reason",
            reason_codes=("future_reason",),
        ))
        item = report.recommendations[0]
        self.assertIs(item.action, RecoveryActionCode.MANUAL_DIAGNOSIS)
        self.assertIs(item.automation, RecoveryAutomation.FORBIDDEN)

    def test_report_serialization_is_machine_stable(self):
        report = self.plan(SubsystemSnapshot(
            "forensics",
            HealthState.DEGRADED_EVIDENCE,
            "projection stale",
            reason_codes=("forensic_projection_stale",),
        ))
        payload = report.to_dict()
        self.assertEqual(payload["schema_version"], "recovery-decision.v1")
        self.assertFalse(payload["blocked"])
        self.assertEqual(payload["recommendations"][0]["action"], "rebuild_derived_state")


    def test_assess_exposes_safe_conditional_and_unknown_closure(self):
        service = RuntimeRecoveryDecisionService()
        safe = service.assess(PlatformStatus((SubsystemSnapshot(
            "forensics", HealthState.DEGRADED_EVIDENCE, "projection stale",
            reason_codes=("forensic_projection_stale",),
        ),)))
        self.assertTrue(safe.can_run_automatically)
        self.assertEqual(safe.safe_actions, (RecoveryActionCode.REBUILD_DERIVED_STATE,))
        unknown = service.assess(PlatformStatus((SubsystemSnapshot(
            "future_component", HealthState.FAILED, "future reason",
            reason_codes=("future_reason",),
        ),)))
        self.assertFalse(unknown.can_run_automatically)
        self.assertEqual(unknown.unknown_reason_codes, ("future_reason",))


if __name__ == "__main__":
    unittest.main()
