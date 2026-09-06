from __future__ import annotations

import pytest

from noetrium_platform.capabilities.environment.api import (
    EnvironmentAssignmentIdentity,
    EnvironmentAssignmentIsolationReceipt,
)


def test_assignment_identity_is_provider_neutral_and_digestable() -> None:
    identity = EnvironmentAssignmentIdentity(
        assignment_id="a-1",
        study_id="study",
        plan_digest="a" * 64,
        variant_id="fixed-c",
        repetition=0,
        seed="Seed-C",
        environment_id="minecraft",
    )
    assert len(identity.digest) == 64


def test_isolation_receipt_rejects_scientific_success_as_state() -> None:
    with pytest.raises(ValueError):
        EnvironmentAssignmentIsolationReceipt(
            assignment_id="a-1",
            isolation_id="iso",
            state="success",
            durability="crash_durable",
            environment_state_digest="b" * 64,
        )
