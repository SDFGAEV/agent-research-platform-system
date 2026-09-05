from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for entry in (str(SCRIPT_DIR), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from check_readme_i18n import validate_root as validate_readme_i18n
from noetrium_platform.research.execution.admission.api import AdmissionBudget

from noetrium_platform.foundation.governance.release.api import ReleaseRegressionEvidence
from noetrium_platform.foundation.governance.release.runtime.evidence import RELEASE_EVIDENCE_FILENAME
from noetrium_platform.foundation.governance.release.runtime.authority import publish_release_authority
from noetrium_platform.foundation.governance.release.runtime.freeze_lock import ReleaseFreezeBusy, ReleaseFreezeLock
from noetrium_platform.foundation.governance.release.runtime.generation import ReleaseEvidenceCoordinator
from noetrium_platform.foundation.governance.release.runtime.regression_state import default_regression_state_path
from noetrium_platform.composition.release_quality import ReleaseQualityEvidenceProvider
from noetrium_platform.foundation.kernel.concurrency.api import ConcurrencyBudget, TaskGroupPort
from noetrium_platform.composition.concurrency import build_execution_concurrency_runtime
from release_regression import run_release_regression


class _PytestReleaseRegressionProvider:
    """Top-level composition adapter from pytest runner to release API."""

    def __init__(self, task_group: TaskGroupPort) -> None:
        self._task_group = task_group

    def run(self, root: Path, *, source_manifest_digest: str) -> ReleaseRegressionEvidence:
        raw = run_release_regression(
            root,
            source_manifest_digest=source_manifest_digest,
            state_path=default_regression_state_path(root),
            task_group=self._task_group,
        )
        return ReleaseRegressionEvidence(
            tests_collected=raw.collected,
            tests_passed=raw.passed,
            tests_skipped=raw.skipped,
            shard_count=raw.shard_count,
            test_inventory_sha256=raw.test_inventory_sha256,
            runtime_sha256=raw.runtime_sha256,
            plan_sha256=raw.plan_sha256,
        )


def _generate_locked(root: Path) -> int:
    workers = max(4, min(8, int(__import__("os").cpu_count() or 1)))
    runtime = build_execution_concurrency_runtime(
        concurrency_budget=ConcurrencyBudget(
            max_blocking_io_workers=workers,
            max_cpu_workers=min(5, workers),
            max_blocking_io_in_flight=workers,
            max_cpu_in_flight=min(5, workers),
            default_queue_capacity=128,
        ),
        admission_budget=AdmissionBudget(
            max_blocking_io_in_flight=workers,
            max_cpu_in_flight=min(5, workers),
        ),
        blocking_io_thread_name_prefix="release-structured",
        timer_name="release-structured-timer",
    )
    coordinator_group = runtime.open_task_group("release-evidence", tenant_id="release", resource_id="quality")
    regression_group = runtime.open_task_group("release-regression", tenant_id="release", resource_id="regression")
    coordinator = ReleaseEvidenceCoordinator(
        quality=ReleaseQualityEvidenceProvider(task_group=coordinator_group),
        regression=_PytestReleaseRegressionProvider(regression_group),
        task_group=coordinator_group,
    )
    try:
        result = coordinator.generate(root)
    except RuntimeError as exc:
        print(f"RELEASE_EVIDENCE_FAIL: {exc}")
        return 1
    finally:
        runtime.close()

    receipt = publish_release_authority(root, result.manifest, result.evidence)
    path = root / RELEASE_EVIDENCE_FILENAME
    print(f"RELEASE_MANIFEST={root / 'RELEASE_MANIFEST.json'}")
    print(f"RELEASE_MANIFEST_SHA256={result.manifest.digest()}")
    print(f"RELEASE_EVIDENCE={path}")
    print(f"EVIDENCE_SHA256={result.evidence.digest()}")
    print(f"RELEASE_AUTHORITY_SHA256={receipt.digest()}")
    print(f"TESTS_COLLECTED={result.regression.tests_collected}")
    print(f"TESTS_PASSED={result.regression.tests_passed}")
    print(f"TESTS_SKIPPED={result.regression.tests_skipped}")
    print(f"TEST_SHARDS={result.regression.shard_count}")
    print(f"TEST_PLAN_SHA256={result.regression.plan_sha256}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and publish fail-closed Noetrium release evidence."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root to verify (default: the project containing this script)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    root = _parse_args(argv).root.resolve()
    _readme_errors = validate_readme_i18n(root)
    if _readme_errors:
        print("RELEASE_EVIDENCE_FAIL: multilingual README gate failed")
        print(f"README_I18N: {_readme_errors!r}")
        return 1
    print("README_I18N_RELEASE_GATE_PASS")
    try:
        with ReleaseFreezeLock(root):
            return _generate_locked(root)
    except ReleaseFreezeBusy:
        print("RELEASE_EVIDENCE_FAIL: another release freeze operation is already active")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
