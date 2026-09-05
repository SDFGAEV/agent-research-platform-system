from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import tomllib

from noetrium_platform.foundation.governance.repository_boundary.api import RepositoryBoundaryAuditor
from noetrium_platform.product.operator.api import (
    ProjectTemplateProfile,
    project_template_revision,
    ProjectDoctorCheck,
    ProjectDoctorDisposition,
    ProjectDoctorReport,
)
from noetrium_platform.product.operator.runtime.project_layout import project_package_name
from noetrium_platform.product.operator.runtime.project_platform_identity import installed_platform_identity
from noetrium_platform.product.operator.runtime.project_subprocess import (
    isolated_environment,
    isolated_script_command,
)
from noetrium_platform.foundation.portfolio.api import (
    ProjectManifest,
    ProjectManifestDecodeError,
    decode_project_manifest_bytes,
)

_MANIFEST_PATH = "project.manifest.json"
_PACKAGE = re.compile(r"[a-z][a-z0-9_]*")
_PROVIDER_PROBE_TIMEOUT_S = 30
_AUTHOR_PROBE_TIMEOUT_S = 30
_AUTHOR_PROBE_SCRIPT = r'''
from noetrium.contracts.research import ResearchMethodHostPort
from __PACKAGE__.research import METHOD_HOST, compile_method

if not isinstance(METHOD_HOST, ResearchMethodHostPort):
    raise TypeError("author Method Host does not implement ResearchMethodHostPort")
if not callable(compile_method):
    raise TypeError("author research module must export compile_method")
print("ready")
'''
_PROVIDER_PROBE_SCRIPT = r'''
import json

from noetrium.contracts.environment import (
    EnvironmentCapability,
    EnvironmentDiagnosticsPort,
    EnvironmentProviderCapabilities,
    EnvironmentProviderPort,
    EnvironmentSession,
)
from noetrium.contracts.model import (
    ModelBindingDiagnostic,
    ModelBindingDiagnosticSeverity,
    ProjectModelProviderPort,
)
from noetrium.contracts.participant import (
    ParticipantBindingDiagnostic,
    ParticipantBindingDiagnosticSeverity,
    ProjectParticipantProviderPort,
)
from __PACKAGE__.environment_provider import ENVIRONMENT_PROVIDER, ENVIRONMENT_SERVICES
from __PACKAGE__.model_provider import MODEL_PROVIDER
from __PACKAGE__.participant_provider import PARTICIPANT_PROVIDER
from __PACKAGE__.requirements import MODEL_REQUIREMENT, PARTICIPANT_REQUIREMENT


def diagnostic_rows(rows, row_type, error_severity):
    if not isinstance(rows, tuple):
        raise TypeError("provider diagnostics must be a tuple")
    result = []
    for row in rows:
        if not isinstance(row, row_type):
            raise TypeError("provider returned an untyped diagnostic")
        result.append({
            "code": row.code.value,
            "severity": row.severity.value,
            "error": row.severity is error_severity,
        })
    return result


def environment_readiness():
    if not isinstance(ENVIRONMENT_PROVIDER, EnvironmentProviderPort):
        raise TypeError("environment provider does not implement EnvironmentProviderPort")
    capabilities = ENVIRONMENT_PROVIDER.capabilities
    if not isinstance(capabilities, EnvironmentProviderCapabilities):
        raise TypeError("environment provider capabilities are not typed")
    try:
        session = ENVIRONMENT_PROVIDER.open_session(
            session_id="research-project-doctor",
            services=ENVIRONMENT_SERVICES,
        )
    except Exception as exc:
        return {
            "ready": False,
            "code": "ENVIRONMENT_OPEN_" + type(exc).__name__.upper(),
        }
    try:
        if not isinstance(session, EnvironmentSession):
            raise TypeError("environment provider returned an invalid session")
        if capabilities.supports(EnvironmentCapability.DIAGNOSTICS):
            if not isinstance(session, EnvironmentDiagnosticsPort):
                raise TypeError("environment diagnostics capability lacks public diagnostics port")
            observation = session.diagnostics_snapshot()
            return {
                "ready": bool(observation.ready and not observation.closed),
                "code": "ENVIRONMENT_DIAGNOSTICS_READY"
                if observation.ready and not observation.closed
                else "ENVIRONMENT_DIAGNOSTICS_NOT_READY",
            }
        return {"ready": True, "code": "ENVIRONMENT_SESSION_OPEN"}
    finally:
        session.close()


if not isinstance(PARTICIPANT_PROVIDER, ProjectParticipantProviderPort):
    raise TypeError("participant provider does not implement ProjectParticipantProviderPort")
if not isinstance(MODEL_PROVIDER, ProjectModelProviderPort):
    raise TypeError("model provider does not implement ProjectModelProviderPort")

document = {
    "participant": diagnostic_rows(
        PARTICIPANT_PROVIDER.diagnose(PARTICIPANT_REQUIREMENT),
        ParticipantBindingDiagnostic,
        ParticipantBindingDiagnosticSeverity.ERROR,
    ),
    "model": diagnostic_rows(
        MODEL_PROVIDER.diagnose(MODEL_REQUIREMENT),
        ModelBindingDiagnostic,
        ModelBindingDiagnosticSeverity.ERROR,
    ),
    "environment": environment_readiness(),
}
print(json.dumps(document, sort_keys=True, separators=(",", ":")))
'''


def _check(check_id: str, ok: bool, summary: str, remediation: str) -> ProjectDoctorCheck:
    return ProjectDoctorCheck(
        check_id,
        ProjectDoctorDisposition.PASS if ok else ProjectDoctorDisposition.BLOCKED,
        summary,
        "" if ok else remediation,
    )


def _project_metadata(root: Path) -> tuple[str, str, tuple[str, ...]]:
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = document.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml is missing [project]")
    project_id = str(project.get("name", "")).strip()
    version = str(project.get("version", "")).strip()
    dependencies = project.get("dependencies", ())
    if not isinstance(dependencies, list):
        raise ValueError("project dependencies must be an array")
    return project_id, version, tuple(str(item) for item in dependencies)


def _manifest(root: Path) -> ProjectManifest | None:
    path = root / _MANIFEST_PATH
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return decode_project_manifest_bytes(path.read_bytes())
    except (OSError, ProjectManifestDecodeError):
        return None


def _contains_unimplemented_raise(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(target, ast.Name) and target.id == "NotImplementedError":
            return True
    return False


def _diagnostic_codes(rows: object) -> tuple[str, ...]:
    if not isinstance(rows, list):
        raise ValueError("provider diagnostic rows must be an array")
    codes: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"code", "severity", "error"}:
            raise ValueError("provider diagnostic row fields are not exact")
        code = row["code"]
        severity = row["severity"]
        error = row["error"]
        if not isinstance(code, str) or not code.strip():
            raise ValueError("provider diagnostic code must be non-empty text")
        if severity not in {"info", "warning", "error"} or type(error) is not bool:
            raise ValueError("provider diagnostic severity is invalid")
        if error != (severity == "error"):
            raise ValueError("provider diagnostic error flag contradicts severity")
        if error:
            codes.append(code)
    return tuple(codes)


def _author_readiness(root: Path, package: str) -> tuple[bool, str]:
    if not _PACKAGE.fullmatch(package):
        return False, "invalid project package identity"
    script = _AUTHOR_PROBE_SCRIPT.replace("__PACKAGE__", package)
    command = isolated_script_command(script, project_src=root / "src")
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=isolated_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=_AUTHOR_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "author Method Host probe could not complete"
    if completed.returncode != 0 or completed.stdout.strip() != "ready":
        return False, "author Method Host public contract probe failed closed"
    return True, "ready"


def _provider_readiness(root: Path, package: str) -> tuple[bool, str, bool, str, bool, str]:
    if not _PACKAGE.fullmatch(package):
        return (
            False,
            "invalid project package identity",
            False,
            "invalid project package identity",
            False,
            "invalid project package identity",
        )
    script = _PROVIDER_PROBE_SCRIPT.replace("__PACKAGE__", package)
    command = isolated_script_command(script, project_src=root / "src")
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=isolated_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=_PROVIDER_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (
            False,
            "provider readiness probe could not complete",
            False,
            "provider readiness probe could not complete",
            False,
            "provider readiness probe could not complete",
        )
    if completed.returncode != 0:
        return (
            False,
            "provider readiness probe failed closed",
            False,
            "provider readiness probe failed closed",
            False,
            "provider readiness probe failed closed",
        )
    try:
        document = json.loads(completed.stdout)
        if not isinstance(document, dict) or set(document) != {"participant", "model", "environment"}:
            raise ValueError("provider readiness root fields are not exact")
        participant_errors = _diagnostic_codes(document["participant"])
        model_errors = _diagnostic_codes(document["model"])
        environment_row = document["environment"]
        if not isinstance(environment_row, dict) or set(environment_row) != {"ready", "code"}:
            raise ValueError("environment readiness fields are not exact")
        environment_ready = environment_row["ready"]
        environment_code = environment_row["code"]
        if type(environment_ready) is not bool or not isinstance(environment_code, str) or not environment_code.strip():
            raise ValueError("environment readiness is invalid")
    except (json.JSONDecodeError, ValueError, TypeError):
        return (
            False,
            "provider readiness output is invalid",
            False,
            "provider readiness output is invalid",
            False,
            "provider readiness output is invalid",
        )
    participant_detail = ", ".join(participant_errors) or "ready"
    model_detail = ", ".join(model_errors) or "ready"
    return (
        not participant_errors,
        participant_detail,
        not model_errors,
        model_detail,
        environment_ready,
        environment_code,
    )


def _template_profile(revision: str | None) -> ProjectTemplateProfile | None:
    if revision is None:
        return None
    for profile in ProjectTemplateProfile:
        if revision == project_template_revision(profile):
            return profile
    return None


def doctor_project(project_root: Path, *, boundary_auditor: RepositoryBoundaryAuditor) -> ProjectDoctorReport:
    root = project_root.expanduser().absolute()
    checks: list[ProjectDoctorCheck] = []
    marker = root / ".noetrium-template"
    marker_value = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    profile = _template_profile(marker_value)
    checks.append(_check(
        "template_revision", profile is not None,
        "project template revision is supported",
        "regenerate the project with a supported author/provider template",
    ))

    try:
        project_id, project_version, dependencies = _project_metadata(root)
        metadata_ok = bool(project_id and project_version)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        project_id, project_version, dependencies, metadata_ok = "", "", (), False
    checks.append(_check(
        "project_metadata", metadata_ok,
        "pyproject project identity is readable",
        "restore the generated pyproject.toml project name/version",
    ))

    manifest = _manifest(root)
    checks.append(_check(
        "project_manifest", manifest is not None,
        "canonical digest-bound project manifest decodes successfully",
        "restore project.manifest.json from the canonical Portfolio codec",
    ))
    manifest_template_ok = bool(
        manifest is not None and profile is not None
        and manifest.template_revision == marker_value
    )
    checks.append(_check(
        "manifest_template_revision", manifest_template_ok,
        "manifest template revision matches the product template marker",
        "regenerate the project instead of editing template identity bytes",
    ))

    identity_ok = bool(
        manifest is not None and metadata_ok
        and manifest.project.identity.project_id == project_id
        and manifest.project.identity.version == project_version
    )
    checks.append(_check(
        "manifest_identity", identity_ok,
        "manifest identity matches pyproject identity",
        "regenerate the project; do not hand-edit manifest identity bytes",
    ))

    platform = installed_platform_identity()
    dependency_ok = dependencies == (f"noetrium=={platform.version}",)
    checks.append(_check(
        "platform_version", dependency_ok,
        f"project pins installed noetrium {platform.version}",
        "regenerate with the installed qualified noetrium artifact",
    ))
    provenance_ok = bool(
        manifest is not None
        and manifest.provenance.tool_version == platform.version
        and manifest.provenance.platform_artifact_sha256 == platform.artifact_sha256
    )
    checks.append(_check(
        "platform_provenance", provenance_ok,
        "manifest provenance matches the installed Platform artifact",
        "regenerate with the currently installed qualified Platform artifact",
    ))

    try:
        package = project_package_name(project_id) if project_id else ""
    except ValueError:
        package = ""
    common_files = () if not package else (
        _MANIFEST_PATH,
        f"src/{package}/project.py",
    )
    author_files = () if not package else (
        f"src/{package}/methods.py", f"src/{package}/tasks.py",
        f"src/{package}/measurements.py", f"src/{package}/studies.py",
        f"src/{package}/research.py",
        "tests/test_generated_author_project.py",
    )
    provider_files = () if not package else (
        f"src/{package}/requirements.py",
        f"src/{package}/participant_provider.py",
        f"src/{package}/model_provider.py",
        f"src/{package}/environment_provider.py",
        f"src/{package}/application.py",
        "tests/test_generated_provider_project.py",
    )
    required_files = common_files + (
        author_files if profile is ProjectTemplateProfile.AUTHOR else provider_files
        if profile is ProjectTemplateProfile.PROVIDER else ()
    )
    files_ok = bool(required_files) and all(
        (root / relative).is_file() and not (root / relative).is_symlink()
        for relative in required_files
    )
    checks.append(_check(
        "generated_files", files_ok,
        "generated files match the selected product template profile",
        "restore or regenerate the deterministic project scaffold",
    ))

    try:
        boundary_report = boundary_auditor(root)
        boundary_ok = boundary_report.passed
        violation_detail = "; ".join(
            f"{row.path}: {row.detail}" for row in boundary_report.violations
        )
    except (OSError, ValueError):
        boundary_ok, violation_detail = False, "downstream import audit could not complete"
    checks.append(_check(
        "public_import_boundary", boundary_ok,
        "downstream source imports public Platform boundaries only",
        violation_detail or "remove forbidden private Platform imports",
    ))

    if profile is ProjectTemplateProfile.AUTHOR:
        if files_ok:
            author_ready, author_detail = _author_readiness(root, package)
        else:
            author_ready, author_detail = False, "author template files are incomplete"
        checks.append(_check(
            "level0_standard_bindings", author_ready,
            "Level-0 Research Method Host and typed compiler/binding seam are available",
            "resolve author Method Host readiness: " + author_detail,
        ))
    elif profile is ProjectTemplateProfile.PROVIDER:
        if files_ok:
            (
                participant_ready, participant_detail,
                model_ready, model_detail,
                environment_ready, environment_detail,
            ) = _provider_readiness(root, package)
        else:
            participant_ready = model_ready = environment_ready = False
            participant_detail = model_detail = environment_detail = "provider template files are incomplete"
        checks.append(_check(
            "participant_provider_readiness", participant_ready,
            "Participant provider reports no blocking typed diagnostics",
            "resolve Participant diagnostics: " + participant_detail,
        ))
        checks.append(_check(
            "model_provider_readiness", model_ready,
            "Model provider reports no blocking typed diagnostics",
            "resolve Model diagnostics: " + model_detail,
        ))
        checks.append(_check(
            "environment_provider_readiness", environment_ready,
            "Environment provider opens a public ready session",
            "resolve Environment readiness: " + environment_detail,
        ))
        application = root / f"src/{package}/application.py" if package else root / "missing-application.py"
        application_ready = application.is_file() and not _contains_unimplemented_raise(application)
        checks.append(_check(
            "application_binding", application_ready,
            "provider template application contains an explicit RunControl binding",
            "implement build_application() using the public RunControlPort",
        ))

    return ProjectDoctorReport(
        project_root=str(root),
        template_profile=profile,
        template_revision=marker_value,
        checks=tuple(checks),
    )


__all__ = ["doctor_project"]
