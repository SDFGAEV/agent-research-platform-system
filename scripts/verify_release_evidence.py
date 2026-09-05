import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from noetrium_platform.composition.release_quality import build_release_quality_evidence
from noetrium_platform.foundation.governance.release.runtime.evidence import RELEASE_EVIDENCE_FILENAME, verify_release_evidence
from noetrium_platform.foundation.governance.release.runtime.manifest import verify_release_manifest
from noetrium_platform.foundation.governance.release.runtime.freeze_lock import ReleaseFreezeBusy, ReleaseFreezeLock
from noetrium_platform.foundation.governance.release.runtime.authority import ReleaseAuthorityMismatch, load_verified_release_authority


def _verify_locked(root: Path) -> int:
    evidence_path = root / RELEASE_EVIDENCE_FILENAME
    manifest_path = root / "RELEASE_MANIFEST.json"
    if not evidence_path.exists():
        print("RELEASE_EVIDENCE_VERIFY_FAIL missing RELEASE_EVIDENCE.json")
        return 1
    if not manifest_path.exists():
        print("RELEASE_EVIDENCE_VERIFY_FAIL missing RELEASE_MANIFEST.json")
        return 1
    try:
        manifest, evidence, authority = load_verified_release_authority(root)
    except ReleaseAuthorityMismatch as exc:
        print(f"RELEASE_EVIDENCE_VERIFY_FAIL {exc}")
        return 1
    errors = list(verify_release_manifest(root, manifest))
    if evidence.release_manifest_digest != manifest.digest():
        errors.append("release evidence does not bind RELEASE_MANIFEST.json")
    errors.extend(verify_release_evidence(root, evidence, quality=build_release_quality_evidence(root)))
    for error in errors:
        print(f"RELEASE_EVIDENCE_VERIFY_FAIL {error}")
    if errors:
        return 1
    print(f"RELEASE_MANIFEST_VERIFY_PASS {manifest.digest()}")
    print(f"RELEASE_EVIDENCE_VERIFY_PASS {evidence.digest()}")
    print(f"RELEASE_AUTHORITY_VERIFY_PASS {authority.digest()}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the persisted Noetrium release authority and evidence."
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
    try:
        with ReleaseFreezeLock(root):
            return _verify_locked(root)
    except ReleaseFreezeBusy:
        print("RELEASE_EVIDENCE_VERIFY_FAIL another release freeze operation is already active")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
