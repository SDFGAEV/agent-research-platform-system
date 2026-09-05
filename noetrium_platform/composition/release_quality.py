from __future__ import annotations

from noetrium_platform.research.execution.admission.api import AdmissionBudget
from pathlib import Path
import hashlib
import os

from noetrium_platform.foundation.governance.api import RepositorySourceIndexPort
from noetrium_platform.foundation.governance.release.api import ReleaseQualityEvidence
from noetrium_platform.foundation.kernel.concurrency.api import ConcurrencyBudget, Deadline, ExecutionLaneKind, ExecutionSpec, TaskGroupPort
from noetrium_platform.composition.concurrency import build_execution_concurrency_runtime


_PARALLEL_FILE_THRESHOLD = 256
_MAX_QUALITY_WORKERS = 5


def _source_file_count_at_least(source_index: RepositorySourceIndexPort, threshold: int) -> bool:
    return len(tuple(source_index.documents(suffixes={".py"}))) >= threshold


def _architecture_lane(
    root_text: str, source_index: RepositorySourceIndexPort, git_executable: str | None = None
) -> tuple[str, bool]:
    from noetrium_platform.foundation.governance.architecture.composition import build_architecture_report

    architecture = build_architecture_report(
        Path(root_text), source_index=source_index, git_executable=git_executable
    )
    return architecture.report_sha256, architecture.clean


def _not_applicable_digest(system: str) -> str:
    return hashlib.sha256(f"{system}:not-applicable".encode("utf-8")).hexdigest()


def _quality_guard_lane(root_text: str, source_index: RepositorySourceIndexPort) -> tuple[int, int]:
    from noetrium_platform.foundation.governance.quality import scan_no_degradation, scan_silent_failures

    root = Path(root_text)
    silent = len(scan_silent_failures(
        root,
        source_index=source_index,
        path_prefixes=("noetrium_platform", "projects"),
    ))
    return len(scan_no_degradation(root, source_index=source_index)), silent


def _algorithm_lane(root_text: str, source_index: RepositorySourceIndexPort) -> tuple[str, bool, int]:
    from noetrium_platform.foundation.governance.algorithm.composition import build_algorithm_governance
    from noetrium_platform.foundation.governance.algorithm.runtime import AlgorithmBaselineMissing

    root = Path(root_text)
    if not (root / "noetrium_platform" / "foundation" / "governance" / "algorithm").exists():
        return _not_applicable_digest("algorithm-governance"), True, 0
    try:
        snapshot, report = build_algorithm_governance(
            root, exact=True, source_inventory=source_index, source_index=source_index
        ).gate()
        return snapshot.source_digest, report.passed, len(report.blockers)
    except AlgorithmBaselineMissing:
        return "", False, 1


def _concurrency_lane(root_text: str, source_index: RepositorySourceIndexPort) -> tuple[str, bool, int]:
    from noetrium_platform.foundation.governance.concurrency.composition import build_concurrency_governance
    from noetrium_platform.foundation.governance.concurrency.runtime import ConcurrencyBaselineMissing

    root = Path(root_text)
    if not (root / "noetrium_platform" / "foundation" / "governance" / "concurrency").exists():
        return _not_applicable_digest("concurrency-governance"), True, 0
    try:
        snapshot, report = build_concurrency_governance(
            root, exact=True, source_inventory=source_index, source_index=source_index
        ).gate()
        return snapshot.source_digest, report.passed, len(report.blockers)
    except ConcurrencyBaselineMissing:
        return "", False, 1


def _performance_lane(root_text: str, source_index: RepositorySourceIndexPort) -> tuple[str, bool, int]:
    from noetrium_platform.foundation.governance.performance.composition import build_performance_governance
    from noetrium_platform.foundation.governance.performance.runtime import PerformanceBaselineMissing

    root = Path(root_text)
    if not (root / "noetrium_platform" / "foundation" / "governance" / "performance").exists():
        return _not_applicable_digest("performance-governance"), True, 0
    try:
        snapshot, report = build_performance_governance(
            root, exact=True, source_inventory=source_index, source_index=source_index
        ).gate()
        return snapshot.source_digest, report.passed, len(report.blockers)
    except PerformanceBaselineMissing:
        return "", False, 1


def _static_quality_lane(
    root_text: str, source_index: RepositorySourceIndexPort
) -> dict[str, object]:
    no_degradation, silent = _quality_guard_lane(root_text, source_index)
    algorithm = _algorithm_lane(root_text, source_index)
    concurrency = _concurrency_lane(root_text, source_index)
    performance = _performance_lane(root_text, source_index)
    return {
        "no_degradation_findings": no_degradation,
        "silent_failure_findings": silent,
        "algorithm_source_digest": algorithm[0],
        "algorithm_clean": algorithm[1],
        "algorithm_blockers": algorithm[2],
        "concurrency_source_digest": concurrency[0],
        "concurrency_clean": concurrency[1],
        "concurrency_blockers": concurrency[2],
        "performance_source_digest": performance[0],
        "performance_clean": performance[1],
        "performance_blockers": performance[2],
    }

def _build_sequential(
    root: Path, source_index: RepositorySourceIndexPort, git_executable: str | None
) -> ReleaseQualityEvidence:
    architecture_sha, architecture_clean = _architecture_lane(
        str(root), source_index, git_executable
    )
    static = _static_quality_lane(str(root), source_index)
    return ReleaseQualityEvidence(
        architecture_report_sha256=architecture_sha,
        architecture_clean=architecture_clean,
        **static,
    )


def build_release_quality_evidence(
    root: Path,
    *,
    task_group: TaskGroupPort | None = None,
    source_index: RepositorySourceIndexPort | None = None,
    git_executable: str | Path | None = None,
) -> ReleaseQualityEvidence:
    """Build all static governance evidence from one immutable source tree.

    Algorithm, concurrency and performance governance are release authorities,
    not advisory reports. A missing reviewed baseline therefore fails closed.

    Algorithm-Complexity: O(1)
    Algorithm-Rationale: This coordinator always schedules and joins exactly five fixed governance lanes; repository-size traversal occurs inside those independently analyzed lane functions.
    """

    root = Path(root).resolve()
    if source_index is None:
        from noetrium_platform.foundation.governance.providers import GitRepositorySourceTree

        source_index = GitRepositorySourceTree(
            root,
            git_executable=git_executable,
        ).index()
    force_sequential = os.environ.get("RELEASE_QUALITY_SEQUENTIAL", "").strip().lower() in {"1", "true", "yes"}
    if force_sequential or not _source_file_count_at_least(source_index, _PARALLEL_FILE_THRESHOLD):
        return _build_sequential(
            root, source_index, str(git_executable) if git_executable is not None else None
        )

    owned_runtime = None
    resolved_group = task_group
    if resolved_group is None:
        owned_runtime = build_execution_concurrency_runtime(
            concurrency_budget=ConcurrencyBudget(
                max_blocking_io_workers=1,
                max_cpu_workers=_MAX_QUALITY_WORKERS,
                max_cpu_in_flight=_MAX_QUALITY_WORKERS,
                default_queue_capacity=16,
            ),
            admission_budget=AdmissionBudget(
                max_cpu_in_flight=_MAX_QUALITY_WORKERS,
            ),
            blocking_io_thread_name_prefix="release-quality-lane",
            timer_name="release-quality-timer",
        )
        resolved_group = owned_runtime.open_task_group("release-quality")

    try:
        architecture_task = resolved_group.submit(
            ExecutionSpec(task_id="release-quality-architecture", lane_kind=ExecutionLaneKind.CPU),
            _architecture_lane, str(root), source_index,
            str(git_executable) if git_executable is not None else None,
            deadline=Deadline.after(180.0),
        )
        quality_task = resolved_group.submit(
            ExecutionSpec(task_id="release-quality-quality-guards", lane_kind=ExecutionLaneKind.CPU),
            _quality_guard_lane, str(root), source_index, deadline=Deadline.after(180.0),
        )
        algorithm_task = resolved_group.submit(
            ExecutionSpec(task_id="release-quality-algorithm", lane_kind=ExecutionLaneKind.CPU),
            _algorithm_lane, str(root), source_index, deadline=Deadline.after(180.0),
        )
        concurrency_task = resolved_group.submit(
            ExecutionSpec(task_id="release-quality-concurrency", lane_kind=ExecutionLaneKind.CPU),
            _concurrency_lane, str(root), source_index, deadline=Deadline.after(180.0),
        )
        performance_task = resolved_group.submit(
            ExecutionSpec(task_id="release-quality-performance", lane_kind=ExecutionLaneKind.CPU),
            _performance_lane, str(root), source_index, deadline=Deadline.after(180.0),
        )
        results = {
            "architecture": architecture_task.result(timeout=180.0),
            "quality-guards": quality_task.result(timeout=180.0),
            "algorithm": algorithm_task.result(timeout=180.0),
            "concurrency": concurrency_task.result(timeout=180.0),
            "performance": performance_task.result(timeout=180.0),
        }
        architecture = results["architecture"]
        no_degradation, silent = results["quality-guards"]
        algorithm = results["algorithm"]
        concurrency = results["concurrency"]
        performance = results["performance"]
        static = {
            "no_degradation_findings": no_degradation,
            "silent_failure_findings": silent,
            "algorithm_source_digest": algorithm[0],
            "algorithm_clean": algorithm[1],
            "algorithm_blockers": algorithm[2],
            "concurrency_source_digest": concurrency[0],
            "concurrency_clean": concurrency[1],
            "concurrency_blockers": concurrency[2],
            "performance_source_digest": performance[0],
            "performance_clean": performance[1],
            "performance_blockers": performance[2],
        }
    finally:
        if owned_runtime is not None:
            owned_runtime.close()

    return ReleaseQualityEvidence(
        architecture_report_sha256=str(architecture[0]),
        architecture_clean=bool(architecture[1]),
        no_degradation_findings=int(static["no_degradation_findings"]),
        silent_failure_findings=int(static["silent_failure_findings"]),
        algorithm_source_digest=str(static["algorithm_source_digest"]),
        algorithm_clean=bool(static["algorithm_clean"]),
        algorithm_blockers=int(static["algorithm_blockers"]),
        concurrency_source_digest=str(static["concurrency_source_digest"]),
        concurrency_clean=bool(static["concurrency_clean"]),
        concurrency_blockers=int(static["concurrency_blockers"]),
        performance_source_digest=str(static["performance_source_digest"]),
        performance_clean=bool(static["performance_clean"]),
        performance_blockers=int(static["performance_blockers"]),
    )


class ReleaseQualityEvidenceProvider:
    """Composition-bound provider for the release API quality port."""

    def __init__(self, *, task_group: TaskGroupPort | None = None) -> None:
        self._task_group = task_group

    def build(self, root: Path) -> ReleaseQualityEvidence:
        return build_release_quality_evidence(root, task_group=self._task_group)


__all__ = ["ReleaseQualityEvidenceProvider", "build_release_quality_evidence"]
