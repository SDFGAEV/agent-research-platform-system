from __future__ import annotations

from noetrium_platform.research.experimentation.workload.api import (
    WorkloadBatchBindingPort,
    WorkloadExecutionCutObserverPort,
    WorkloadTaskResult,
)
from noetrium_platform.research.experimentation.workload.runtime import (
    GenericWorkloadBatchExecutor,
    WorkloadBatchCloseError,
    WorkloadBatchResult,
)
from noetrium_platform.research.experimentation.experiment.api import ExperimentTaskSpec
from noetrium_platform.foundation.kernel.kernel import ExecutionContext

from ..api import (
    CheckpointedWorkloadBatchResult,
    WorkloadCheckpointBindingPort,
    WorkloadCheckpointComponentPort,
    WorkloadCheckpointCoordinatorPort,
    WorkloadCheckpointPublicationPort,
    WorkloadExecutionCut,
)
from .workload_progress import WorkloadProgressCheckpointComponent


class WorkloadResumeIntegrityError(RuntimeError):
    """A restored result prefix cannot be proven to match its execution cut."""


def _require_resume_checkpoint_id(value: str | None) -> None:
    if value is not None and (type(value) is not str or not value.strip()):
        raise ValueError("resume_checkpoint_id must be a non-empty string or None")


class _ProgressBinding:
    """Compose executor-owned progress with domain-owned atomic checkpoint parts."""

    def __init__(
        self,
        source: WorkloadCheckpointBindingPort,
        progress: WorkloadProgressCheckpointComponent,
    ) -> None:
        self._source = source
        self._progress = progress
        self.run_id = source.run_id
        self.study_id = source.study_id
        self.workload_id = source.workload_id
        self.branch_id = source.branch_id
        self.source_cut_id = source.source_cut_id
        self.environment_generation = source.environment_generation
        self.method_generation = source.method_generation
        self.task_manifest_digest = source.task_manifest_digest
        self.checkpoint_compatibility_digest = source.checkpoint_compatibility_digest

    def checkpoint_components(self) -> tuple[WorkloadCheckpointComponentPort, ...]:
        components = self._source.checkpoint_components()
        if any(item.component_id == self._progress.component_id for item in components):
            raise WorkloadResumeIntegrityError(
                f"checkpoint binding already owns reserved component {self._progress.component_id!r}"
            )
        return components + (self._progress,)


class _ProgressObserver(WorkloadExecutionCutObserverPort):
    def __init__(
        self,
        *,
        binding: WorkloadCheckpointBindingPort,
        coordinator: WorkloadCheckpointCoordinatorPort,
        progress: WorkloadProgressCheckpointComponent,
        publication: WorkloadCheckpointPublicationPort | None = None,
        completed_task_ids: tuple[str, ...] = (),
    ) -> None:
        self._binding = binding
        self._coordinator = coordinator
        self._progress = progress
        self._publication = publication
        self._completed_task_ids = list(completed_task_ids)
        self.latest_checkpoint_id: str | None = None

    def after_task(
        self,
        *,
        task: ExperimentTaskSpec,
        result: WorkloadTaskResult,
        context: ExecutionContext,
    ) -> None:
        del task
        self._progress.append(result)
        self._completed_task_ids.append(result.task_id)
        manifest = self._coordinator.capture(
            binding=self._binding,
            context=context,
            execution_cut=WorkloadExecutionCut(
                completed_task_ids=tuple(self._completed_task_ids),
                status="after_task",
            ),
        )
        if self._publication is not None:
            self._publication.published(manifest)
        self.latest_checkpoint_id = manifest.checkpoint_id


class CheckpointedWorkloadBatchExecutor:
    """Environment-neutral task-batch resume/capture orchestration.

    Domain bindings provide only identity plus environment/method/evidence checkpoint
    components.  The platform owns committed workload progress, verifies that the
    restored receipts match the persisted task cut, and resumes the generic executor
    at the exact suffix boundary.
    """

    def __init__(
        self,
        coordinator: WorkloadCheckpointCoordinatorPort,
        publication: WorkloadCheckpointPublicationPort | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._publication = publication

    def execute(
        self,
        batch_binding: WorkloadBatchBindingPort,
        *,
        checkpoint_binding: WorkloadCheckpointBindingPort,
        resume_checkpoint_id: str | None = None,
    ) -> CheckpointedWorkloadBatchResult:
        _require_resume_checkpoint_id(resume_checkpoint_id)
        progress = WorkloadProgressCheckpointComponent()
        composite = _ProgressBinding(checkpoint_binding, progress)
        prior_results: tuple[WorkloadTaskResult, ...] = ()
        completed_task_ids: tuple[str, ...] = ()
        try:
            if resume_checkpoint_id is not None:
                bundle = self._coordinator.restore(
                    resume_checkpoint_id,
                    binding=composite,
                    context=batch_binding.context,
                )
                cut = bundle.manifest.execution_cut
                if cut.status != "after_task":
                    raise WorkloadResumeIntegrityError(
                        "workload resume only accepts committed after-task cuts"
                    )
                prior_results = progress.results
                completed_task_ids = tuple(result.task_id for result in prior_results)
                if completed_task_ids != cut.completed_task_ids:
                    raise WorkloadResumeIntegrityError(
                        "restored workload results do not match the checkpoint execution cut"
                    )
        except BaseException as primary:
            try:
                batch_binding.close()
            except BaseException as cleanup:
                raise WorkloadBatchCloseError(primary, cleanup) from primary
            raise

        observer = _ProgressObserver(
            binding=composite,
            coordinator=self._coordinator,
            progress=progress,
            publication=self._publication,
            completed_task_ids=completed_task_ids,
        )
        batch = GenericWorkloadBatchExecutor(observer).execute(
            batch_binding,
            prior_results=prior_results,
        )
        return CheckpointedWorkloadBatchResult(
            batch=batch,
            latest_checkpoint_id=observer.latest_checkpoint_id,
            resumed_from_checkpoint_id=resume_checkpoint_id,
        )


__all__ = [
    "CheckpointedWorkloadBatchExecutor",
    "CheckpointedWorkloadBatchResult",
    "WorkloadResumeIntegrityError",
]
