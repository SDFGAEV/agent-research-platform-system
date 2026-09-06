from __future__ import annotations

import pytest

from noetrium_platform.capabilities.environment.api import (
    EnvironmentAssignmentIdentity,
    EnvironmentAssignmentIsolationPort,
)
from noetrium_platform.capabilities.environment.minecraft.api import MinecraftWorldBranch
from noetrium_platform.capabilities.environment.minecraft.composition import (
    MinecraftBranchAssignmentIsolation,
)


class Binding:
    environment_generation = "g" * 64

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _branch() -> MinecraftWorldBranch:
    return MinecraftWorldBranch(
        branch_id="assignment-1",
        cut_id="cut-1",
        workdir=r"C:\mc\branches\assignment-1",
        level_name="assignment-1-world",
        manifest_digest="a" * 64,
        cleanup_ref="cleanup:assignment-1",
    )
def _identity() -> EnvironmentAssignmentIdentity:
    return EnvironmentAssignmentIdentity(
        assignment_id="assignment-1",
        study_id="study",
        plan_digest="b" * 64,
        variant_id="fixed-c",
        repetition=0,
        seed="Seed-C",
        environment_id="minecraft",
    )


def test_minecraft_branch_isolation_implements_generic_port() -> None:
    binding = Binding()
    isolation = MinecraftBranchAssignmentIsolation(
        binding=binding,
        branch=_branch(),
        durability="crash_durable",
    )
    assert isinstance(isolation, EnvironmentAssignmentIsolationPort)

    prepared = isolation.prepare_assignment(_identity())
    assert prepared.state == "prepared"
    assert prepared.durability == "crash_durable"
    assert isolation.prepare_assignment(_identity()) == prepared
    assert binding.closed == 0

    finalized = isolation.finalize_assignment(_identity(), prepared)
    assert finalized.state == "finalized"
    assert finalized.environment_state_digest != prepared.environment_state_digest
    assert binding.closed == 1
    assert isolation.finalize_assignment(_identity(), finalized) == finalized
def test_minecraft_branch_isolation_rejects_cross_assignment_use() -> None:
    binding = Binding()
    isolation = MinecraftBranchAssignmentIsolation(
        binding=binding,
        branch=_branch(),
    )
    wrong = EnvironmentAssignmentIdentity(
        assignment_id="assignment-2",
        study_id="study",
        plan_digest="b" * 64,
        variant_id="fixed-c",
        repetition=0,
        seed="Seed-C",
        environment_id="minecraft",
    )
    with pytest.raises(ValueError, match="does not match"):
        isolation.prepare_assignment(wrong)
