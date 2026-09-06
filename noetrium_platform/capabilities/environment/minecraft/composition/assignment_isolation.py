from __future__ import annotations

from dataclasses import replace

from noetrium_platform.capabilities.environment.api import (
    EnvironmentAssignmentIdentity,
    EnvironmentAssignmentIsolationPort,
    EnvironmentAssignmentIsolationReceipt,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest

from ..api import (
    MinecraftBranchRuntimeFactoryPort,
    MinecraftBranchRuntimePort,
    MinecraftBranchRuntimeRequest,
    MinecraftWorldBranch,
)
from .branch_runtime import MinecraftBranchRuntimeError
class MinecraftBranchAssignmentIsolation(EnvironmentAssignmentIsolationPort):
    """Bind one Minecraft branch runtime to one generic assignment lifecycle."""

    def __init__(
        self,
        *,
        binding: MinecraftBranchRuntimePort,
        branch: MinecraftWorldBranch,
        durability: str = "unknown",
    ) -> None:
        if durability not in {"crash_durable", "process_local", "unknown"}:
            raise ValueError("invalid Minecraft assignment isolation durability")
        self._binding = binding
        self._branch = branch
        self._durability = durability
        self._prepared: EnvironmentAssignmentIsolationReceipt | None = None
        self._finalized = False

    @property
    def branch(self) -> MinecraftWorldBranch:
        return self._branch

    @property
    def binding(self) -> MinecraftBranchRuntimePort:
        return self._binding
    def _check_identity(self, identity: EnvironmentAssignmentIdentity) -> None:
        if identity.assignment_id != self._branch.branch_id:
            raise ValueError(
                "Minecraft branch id does not match environment assignment id"
            )
        if identity.environment_id not in {
            "minecraft",
            "minecraft.mineflayer.jsonl.v1",
        }:
            raise ValueError("assignment identity is not for Minecraft")

    def _state_digest(
        self,
        identity: EnvironmentAssignmentIdentity,
        *,
        state: str,
    ) -> str:
        return canonical_digest(
            {
                "assignment": identity.digest,
                "branch": self._branch,
                "environment_generation": self._binding.environment_generation,
                "state": state,
            }
        )
    def prepare_assignment(
        self, identity: EnvironmentAssignmentIdentity
    ) -> EnvironmentAssignmentIsolationReceipt:
        self._check_identity(identity)
        if self._prepared is not None:
            if self._prepared.assignment_id != identity.assignment_id:
                raise ValueError("Minecraft assignment isolation was already prepared")
            return self._prepared
        receipt = EnvironmentAssignmentIsolationReceipt(
            assignment_id=identity.assignment_id,
            isolation_id=f"minecraft-branch:{self._branch.branch_id}",
            state="prepared",
            durability=self._durability,
            environment_state_digest=self._state_digest(
                identity, state="prepared"
            ),
            evidence_refs=(
                f"minecraft-world-cut:{self._branch.cut_id}",
                self._branch.cleanup_ref,
            ),
        )
        self._prepared = receipt
        return receipt
    def finalize_assignment(
        self,
        identity: EnvironmentAssignmentIdentity,
        receipt: EnvironmentAssignmentIsolationReceipt,
    ) -> EnvironmentAssignmentIsolationReceipt:
        self._check_identity(identity)
        if self._prepared is None or receipt != self._prepared:
            raise ValueError("Minecraft assignment isolation receipt mismatch")
        if self._finalized:
            if receipt.state == "finalized":
                return receipt
            raise ValueError("Minecraft assignment isolation was already finalized")
        try:
            self._binding.close()
        except BaseException as exc:
            failed = replace(
                receipt,
                state="failed",
                environment_state_digest=self._state_digest(
                    identity, state="failed"
                ),
                evidence_refs=receipt.evidence_refs + ("minecraft-cleanup:failed",),
            )
            self._prepared = failed
            raise MinecraftBranchRuntimeError(
                "Minecraft assignment isolation cleanup failed",
                phase="close",
                cause=exc,
            ) from exc
        finalized = replace(
            receipt,
            state="finalized",
            environment_state_digest=self._state_digest(
                identity, state="finalized"
            ),
        )
        self._prepared = finalized
        self._finalized = True
        return finalized
class MinecraftBranchAssignmentIsolationFactory:
    """Create the provider isolation adapter after materializing a branch."""

    def __init__(
        self,
        runtime_factory: MinecraftBranchRuntimeFactoryPort,
        *,
        durability: str = "unknown",
    ) -> None:
        self._runtime_factory = runtime_factory
        self._durability = durability

    def open(
        self,
        identity: EnvironmentAssignmentIdentity,
        request: MinecraftBranchRuntimeRequest,
    ) -> MinecraftBranchAssignmentIsolation:
        if request.branch.branch_id != identity.assignment_id:
            raise ValueError(
                "Minecraft branch request does not match assignment identity"
            )
        binding = self._runtime_factory.open(request)
        return MinecraftBranchAssignmentIsolation(
            binding=binding,
            branch=request.branch,
            durability=self._durability,
        )


__all__ = [
    "MinecraftBranchAssignmentIsolation",
    "MinecraftBranchAssignmentIsolationFactory",
]
