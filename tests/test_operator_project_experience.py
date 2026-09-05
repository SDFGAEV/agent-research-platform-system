from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from noetrium_platform.foundation.governance.repository_boundary.runtime import audit_downstream_project_imports
from noetrium_platform.product.operator.api import (
    ProjectCreateRequest, ProjectDoctorDisposition, ProjectTemplateProfile, ProjectTestStage,
)
from noetrium_platform.product.operator.runtime import project_doctor, project_scaffold, project_testing
from noetrium_platform.product.operator.runtime.project_platform_identity import InstalledPlatformIdentity
from noetrium_platform.foundation.portfolio.api import (
    decode_project_manifest_bytes,
    encode_project_manifest,
    project_manifest_document,
)
_FIXED_PLATFORM = InstalledPlatformIdentity("0.1.0", "a" * 64)


def _bind_fixed_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        project_scaffold,
        "installed_platform_identity",
        lambda: _FIXED_PLATFORM,
    )
    monkeypatch.setattr(
        project_doctor,
        "installed_platform_identity",
        lambda: _FIXED_PLATFORM,
    )


def _checks(report) -> dict[str, ProjectDoctorDisposition]:
    return {row.check_id: row.disposition for row in report.checks}


def test_project_create_uses_canonical_manifest_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    request = ProjectCreateRequest("demo-project", "0.1.0", root, "standalone")

    first = project_scaffold.create_project(request)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    second = project_scaffold.create_project(request)
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    manifest = decode_project_manifest_bytes((root / "project.manifest.json").read_bytes())
    assert manifest.project.identity.project_id == "demo-project"
    assert manifest.project.identity.version == "0.1.0"
    assert manifest.provenance.platform_artifact_sha256 == "a" * 64
    assert first.manifest_semantic_digest == project_manifest_document(manifest)["semantic_digest"]
    assert first == second
    assert before == after


def test_project_create_rejects_drift_without_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    request = ProjectCreateRequest("demo-project", "0.1.0", root)
    project_scaffold.create_project(request)
    readme = root / "README.md"
    readme.write_text("user change\n", encoding="utf-8")

    with pytest.raises(ValueError, match="identical generated scaffold"):
        project_scaffold.create_project(request)

    assert readme.read_text(encoding="utf-8") == "user change\n"


def test_project_create_rejects_unexpected_files_without_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    request = ProjectCreateRequest("demo-project", "0.1.0", root)
    project_scaffold.create_project(request)
    unexpected = root / "extra.txt"
    unexpected.write_text("not generated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected=.*extra.txt"):
        project_scaffold.create_project(request)

    assert unexpected.read_text(encoding="utf-8") == "not generated\n"


def test_project_create_cleans_partial_publication_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    request = ProjectCreateRequest("demo-project", "0.1.0", root)
    original = project_scaffold.atomic_replace_bytes
    calls = 0

    def fail_publication(path: Path, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication failure")
        original(path, payload)

    monkeypatch.setattr(project_scaffold, "atomic_replace_bytes", fail_publication)
    with pytest.raises(OSError, match="injected publication failure"):
        project_scaffold.create_project(request)

    assert not root.exists()


def test_project_create_rejects_incomplete_crash_residue_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    root.mkdir()
    partial = root / "README.md"
    partial.write_text("partial crash residue\n", encoding="utf-8")

    with pytest.raises(ValueError, match="identical generated scaffold"):
        project_scaffold.create_project(ProjectCreateRequest("demo-project", "0.1.0", root))

    assert partial.read_text(encoding="utf-8") == "partial crash residue\n"
    assert not (root / ".noetrium-template").exists()


def test_project_doctor_rejects_manifest_and_private_import_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    project_scaffold.create_project(ProjectCreateRequest("demo-project", "0.1.0", root))

    initial = project_doctor.doctor_project(root, boundary_auditor=audit_downstream_project_imports)
    initial_checks = _checks(initial)
    assert initial.template_profile is ProjectTemplateProfile.AUTHOR
    assert initial_checks["project_manifest"] is ProjectDoctorDisposition.PASS
    assert initial_checks["manifest_identity"] is ProjectDoctorDisposition.PASS
    assert initial_checks["public_import_boundary"] is ProjectDoctorDisposition.PASS
    assert initial_checks["level0_standard_bindings"] is ProjectDoctorDisposition.PASS
    level0 = next(row for row in initial.checks if row.check_id == "level0_standard_bindings")
    assert level0.summary == "Level-0 Research Method Host and typed compiler/binding seam are available"
    assert "participant_provider_readiness" not in initial_checks

    manifest_path = root / "project.manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    private_source = root / "src" / "demo_project" / "private_import.py"
    private_source.write_text("from noetrium_platform.product.operator.runtime import research_cli\n", encoding="utf-8")

    drifted = project_doctor.doctor_project(root, boundary_auditor=audit_downstream_project_imports)
    drifted_checks = _checks(drifted)
    assert drifted_checks["project_manifest"] is ProjectDoctorDisposition.BLOCKED
    assert drifted_checks["manifest_identity"] is ProjectDoctorDisposition.BLOCKED
    assert drifted_checks["public_import_boundary"] is ProjectDoctorDisposition.BLOCKED

def test_project_doctor_rejects_unknown_manifest_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    project_scaffold.create_project(ProjectCreateRequest("demo-project", "0.1.0", root))
    manifest_path = root / "project.manifest.json"
    manifest = decode_project_manifest_bytes(manifest_path.read_bytes())
    future = replace(manifest, template_revision="noetrium.project-template.v999")
    manifest_path.write_bytes(encode_project_manifest(future))

    report = project_doctor.doctor_project(root, boundary_auditor=audit_downstream_project_imports)
    checks = _checks(report)
    assert checks["project_manifest"] is ProjectDoctorDisposition.PASS
    assert checks["manifest_template_revision"] is ProjectDoctorDisposition.BLOCKED
    assert not report.ready


def test_project_create_normalizes_python_package_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    receipt = project_scaffold.create_project(
        ProjectCreateRequest("demo.project-alpha", "0.1.0", root)
    )
    assert "src/demo_project_alpha/project.py" in receipt.generated_files


def test_project_doctor_projects_typed_provider_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    project_scaffold.create_project(ProjectCreateRequest(
        "demo-project", "0.1.0", root,
        template_profile=ProjectTemplateProfile.PROVIDER,
    ))

    report = project_doctor.doctor_project(root, boundary_auditor=audit_downstream_project_imports)
    rows = {row.check_id: row for row in report.checks}
    assert report.template_profile is ProjectTemplateProfile.PROVIDER
    assert "PARTICIPANT_RUNTIME_UNAVAILABLE" in rows["participant_provider_readiness"].remediation
    assert "MODEL_QUALIFIED_BINDING_UNAVAILABLE" in rows["model_provider_readiness"].remediation
    assert "ENVIRONMENT_OPEN_NOTIMPLEMENTEDERROR" in rows["environment_provider_readiness"].remediation
    assert rows["application_binding"].disposition is ProjectDoctorDisposition.BLOCKED

def test_root_product_api_exports_project_test_stage_types() -> None:
    from noetrium_platform.api import ProjectTestStage, ProjectTestStageReceipt

    receipt = ProjectTestStageReceipt(ProjectTestStage.BUILD_INSTALL, ("python",), 0)
    assert receipt.stage is ProjectTestStage.BUILD_INSTALL
    assert receipt.passed


def test_project_test_runs_generated_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    project_scaffold.create_project(ProjectCreateRequest("demo-project", "0.1.0", root))
    receipt = project_testing.test_project(root)
    assert receipt.passed
    assert tuple(stage.stage for stage in receipt.stages) == (
        ProjectTestStage.BUILD_INSTALL, ProjectTestStage.CONTRACT_TEST,
    )

def test_project_cli_test_emits_one_strict_json_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd
) -> None:
    import json
    from noetrium_platform.product.operator.composition.research import main

    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "strict-project-test"
    project_scaffold.create_project(ProjectCreateRequest("strict-project-test", "0.1.0", root))

    assert main(["project", "test", "--project", str(root)]) == 0
    captured = capfd.readouterr()
    assert captured.err == ""
    document = json.loads(captured.out)
    assert document["ok"] is True
    assert document["command"] == "project test"
    assert [row["stage"] for row in document["result"]["stages"]] == [
        "build_install", "contract_test",
    ]


def test_generated_project_has_no_private_platform_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    project_scaffold.create_project(ProjectCreateRequest("demo-project", "0.1.0", root))

    report = audit_downstream_project_imports(root)
    assert report.passed, report.violations


def test_project_test_timeout_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "demo-project"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text("[build-system]\nrequires=[]\nbuild-backend=\"missing\"\n", encoding="utf-8")

    def timeout(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired(("python", "-m", "unittest"), 120)

    monkeypatch.setattr(project_testing.subprocess, "run", timeout)
    receipt = project_testing.test_project(root)
    assert len(receipt.stages) == 1
    assert receipt.stages[0].stage is ProjectTestStage.BUILD_INSTALL
    assert receipt.stages[0].exit_code == 124
    assert not receipt.passed


def test_project_cli_create_and_doctor_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import json
    from noetrium_platform.product.operator.composition.research import main

    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "demo-project"
    assert main([
        "project", "create", "demo-project", str(root),
        "--version", "0.1.0", "--program-id", "standalone",
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["ok"] is True
    assert created["result"]["template_profile"] == "author"
    assert created["result"]["manifest_semantic_digest"]

    assert main(["project", "doctor", "--project", str(root)]) == 0
    diagnosed = json.loads(capsys.readouterr().out)
    checks = {row["check_id"]: row["disposition"] for row in diagnosed["result"]["checks"]}
    assert diagnosed["result"]["template_profile"] == "author"
    assert checks["project_manifest"] == "pass"
    assert checks["level0_standard_bindings"] == "pass"
    assert "participant_provider_readiness" not in checks

def test_project_cli_loads_explicit_project_application_and_defaults_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import json
    from noetrium_platform.product.operator.composition.research import main

    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "project-route"
    project_scaffold.create_project(ProjectCreateRequest(
        "project-route", "0.1.0", root,
        template_profile=ProjectTemplateProfile.PROVIDER,
    ))
    application = root / "src" / "project_route" / "application.py"
    application.write_text(
        "from noetrium_platform.api import ResearchResult\n\n"
        "class Application:\n"
        "    def execute(self, request):\n"
        "        return ResearchResult(request.action, request.target, 'accepted', {'route': 'project'})\n\n"
        "def build_application(config_path):\n"
        "    del config_path\n"
        "    return Application()\n",
        encoding="utf-8",
    )

    assert main(["run", "--project", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["result"]["target"] == "project-route"
    assert result["result"]["state"] == "accepted"
    assert result["result"]["payload"] == {"route": "project"}


def test_author_project_lifecycle_route_is_compiler_blocked_not_application_driven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import json
    from noetrium_platform.product.operator.composition.research import main

    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "author-route"
    project_scaffold.create_project(ProjectCreateRequest("author-route", "0.1.0", root))
    assert not (root / "src" / "author_route" / "application.py").exists()

    assert main(["run", "--project", str(root)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["ok"] is False
    assert "Research Method Host" in error["error"]
    assert "BindingContribution" in error["error"]


def test_project_cli_rejects_ambiguous_application_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from noetrium_platform.product.operator.composition.research import main

    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "ambiguous-route"
    project_scaffold.create_project(ProjectCreateRequest("ambiguous-route", "0.1.0", root))
    exit_code = main([
        "--application", "example.module:factory",
        "run", "--project", str(root),
    ])
    assert exit_code == 2
    assert "either --project or --application" in capsys.readouterr().err


def test_default_project_template_is_author_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "author-first"
    receipt = project_scaffold.create_project(
        ProjectCreateRequest("author-first", "0.1.0", root)
    )
    assert receipt.template_profile is ProjectTemplateProfile.AUTHOR
    generated = set(receipt.generated_files)
    for name in ("methods.py", "tasks.py", "measurements.py", "studies.py"):
        assert f"src/author_first/{name}" in generated
    assert "src/author_first/participant_provider.py" not in generated
    assert "src/author_first/model_provider.py" not in generated
    assert "src/author_first/environment_provider.py" not in generated
    assert "src/author_first/application.py" not in generated


def test_provider_template_is_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "provider-template"
    receipt = project_scaffold.create_project(ProjectCreateRequest(
        "provider-template", "0.1.0", root,
        template_profile=ProjectTemplateProfile.PROVIDER,
    ))
    assert receipt.template_profile is ProjectTemplateProfile.PROVIDER
    generated = set(receipt.generated_files)
    assert "src/provider_template/participant_provider.py" in generated
    assert "src/provider_template/model_provider.py" in generated
    assert "src/provider_template/environment_provider.py" in generated
    assert "src/provider_template/application.py" in generated
    assert "src/provider_template/methods.py" not in generated


def test_project_cli_provider_template_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import json
    from noetrium_platform.product.operator.composition.research import main

    _bind_fixed_platform(monkeypatch)
    root = tmp_path / "provider-cli"
    assert main([
        "project", "create", "provider-cli", str(root), "--version", "0.1.0",
        "--template", "provider",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"]["template_profile"] == "provider"
