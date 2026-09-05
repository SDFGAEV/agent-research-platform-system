from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from noetrium_platform.research.experimentation.workload.api import WorkloadBatchBindingPort

from noetrium_platform.research.experimentation.workload.api import WorkloadBatchResult

from noetrium_platform.foundation.kernel.kernel import ExecutionContext

from .workload import (
    WorkloadCheckpointBindingPort,
    WorkloadCheckpointBundle,
    WorkloadCheckpointManifest,
    WorkloadCheckpointStore,
    WorkloadExecutionCut,
)


class WorkloadCheckpointPublicationPort(Protocol):
    """Durably publish the latest committed checkpoint identity for recovery."""

    def published(self, manifest: WorkloadCheckpointManifest) -> None: ...


@dataclass(frozen=True, slots=True)
class CheckpointedWorkloadBatchResult:
    """Typed batch outcome shared by checkpoint executors and project adapters."""

    batch: WorkloadBatchResult
    latest_checkpoint_id: str | None
    resumed_from_checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.batch) is not WorkloadBatchResult:
            raise ValueError("checkpointed workload result batch must be WorkloadBatchResult")
        for field, value in (
            ("latest_checkpoint_id", self.latest_checkpoint_id),
            ("resumed_from_checkpoint_id", self.resumed_from_checkpoint_id),
        ):
            if value is not None and (type(value) is not str or not value.strip()):
                raise ValueError(f"checkpointed workload result {field} must be a non-empty string or None")


class WorkloadCheckpointCoordinatorPort(Protocol):
    def capture(
        self,
        *,
        binding: WorkloadCheckpointBindingPort,
        context: ExecutionContext,
        execution_cut: WorkloadExecutionCut,
    ) -> WorkloadCheckpointManifest: ...

    def restore(
        self,
        checkpoint_id: str,
        *,
        binding: WorkloadCheckpointBindingPort,
        context: ExecutionContext,
    ) -> WorkloadCheckpointBundle: ...


class WorkloadCheckpointedBatchExecutorPort(Protocol):
    """Project-facing seam for checkpoint-aware workload execution."""

    def execute(
        self,
        batch_binding: "WorkloadBatchBindingPort",
        *,
        checkpoint_binding: WorkloadCheckpointBindingPort,
        resume_checkpoint_id: str | None = None,
    ) -> CheckpointedWorkloadBatchResult: ...


__all__ = [
    "CheckpointedWorkloadBatchResult",
    "WorkloadCheckpointCoordinatorPort",
    "WorkloadCheckpointPublicationPort",
    "WorkloadCheckpointedBatchExecutorPort",
]
