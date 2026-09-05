from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from noetrium_platform.infrastructure.lifecycle.python.api import (
    EnvironmentCommandResult,
    PythonEnvironmentSpec,
)
from noetrium_platform.infrastructure.lifecycle.python.runtime import (
    CondaEnvironmentBackend,
    VenvEnvironmentBackend,
    build_python_environment_authorities,
)
from noetrium_platform.infrastructure.lifecycle.python.runtime.registry import PythonEnvironmentRegistry
from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayout, ManagedDirectoryKind
from noetrium_platform.infrastructure.resources.directory.runtime import build_local_directory_authorities
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE
from noetrium_platform.foundation.scope.api import scope_to_data


def _layout(root: Path) -> DirectoryLayout:
    return DirectoryLayout(
        releases=root / "releases",
        runtime=root / "runtime",
        state=root / "state",
        logs=root / "logs",
        model_artifacts=root / "models",
        python_environments=root / "envs",
        cache=root / "cache",
        temp=root / "tmp",
        locks=root / "locks",
        workspaces=root / "workspaces",
    )


class _Backend:
    backend_id = "fake"

    def create(self, root: Path, spec: PythonEnvironmentSpec) -> Path:
        path = root / "bin" / "python"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake", encoding="utf-8")
        return path

    def python_path(self, root: Path) -> Path:
        return root / "bin" / "python"

    def install(self, root: Path, requirements: Path, *, extra_args: tuple[str, ...] = ()) -> EnvironmentCommandResult:
        return EnvironmentCommandResult((str(self.python_path(root)), "-m", "pip"), 0, "", "")


class _Runner:
    def run(self, argv, *, cwd=None, environment=None):
        return EnvironmentCommandResult(tuple(argv), 0, "", "")


def test_python_spec_digest_is_canonical_and_instance_identity_is_stable() -> None:
    first = PythonEnvironmentSpec("sem", PLATFORM_SCOPE, backend="fake", tags=("gpu", "paper"))
    reordered = PythonEnvironmentSpec("sem", PLATFORM_SCOPE, backend="fake", tags=("paper", "gpu"))
    changed = PythonEnvironmentSpec("sem", PLATFORM_SCOPE, backend="fake", python_version="3.12", tags=("gpu", "paper"))
    assert first.specification_digest == reordered.specification_digest
    assert first.specification_digest != changed.specification_digest

    with TemporaryDirectory() as td:
        directories = build_local_directory_authorities(_layout(Path(td)))
        authorities = build_python_environment_authorities(directories.layout, (_Backend(),), _Runner())
        created = authorities.lifecycle.create(first)
        loaded = authorities.lifecycle.get("sem")
        assert created.specification_digest == first.specification_digest
        assert loaded.identity_digest == created.identity_digest


def test_registry_rejects_metadata_without_immutable_spec_digest() -> None:
    with TemporaryDirectory() as td:
        directories = build_local_directory_authorities(_layout(Path(td)))
        registry = PythonEnvironmentRegistry(directories.layout)
        entry = directories.layout.root(ManagedDirectoryKind.STATE) / "python-environments" / "broken.json"
        entry.write_text(
            '{"environment_id":"broken","scope":{"kind":"platform","value":"platform"},'
            '"backend":"fake","root":"/tmp/broken","python_path":"/tmp/broken/bin/python",'
            '"ownership":"external"}',
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="immutable specification digest"):
            registry.get("broken")


def test_explicit_legacy_migration_requires_operator_interpreter_and_freezes_identity() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        directories = build_local_directory_authorities(_layout(root))
        authorities = build_python_environment_authorities(directories.layout, (_Backend(),), _Runner())
        environment_root = root / "legacy-env"
        python_path = environment_root / "bin" / "python"
        python_path.parent.mkdir(parents=True)
        python_path.write_text("legacy", encoding="utf-8")
        entry = directories.layout.root(ManagedDirectoryKind.STATE) / "python-environments" / "legacy.json"
        entry.write_text(
            __import__("json").dumps(
                {
                    "environment_id": "legacy",
                    "scope": scope_to_data(PLATFORM_SCOPE),
                    "backend": "fake",
                    "root": str(environment_root),
                    "python_path": str(python_path),
                    "ownership": "external",
                    "description": "legacy",
                    "tags": ["paper"],
                }
            ),
            encoding="utf-8",
        )
        migrated = authorities.lifecycle.migrate_legacy(
            "legacy",
            python_executable=str(python_path),
            python_version="3.11.15",
        )
        assert migrated.specification_digest
        assert authorities.lifecycle.get("legacy").identity_digest == migrated.identity_digest


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics are required")
def test_legacy_migration_preserves_virtualenv_entrypoint_symlink() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        directories = build_local_directory_authorities(_layout(root))
        authorities = build_python_environment_authorities(directories.layout, (_Backend(),), _Runner())
        environment_root = root / "legacy-env"
        actual = root / "shared-python" / "python3.11"
        actual.parent.mkdir(parents=True)
        actual.write_text("shared", encoding="utf-8")
        python_path = environment_root / "bin" / "python"
        python_path.parent.mkdir(parents=True)
        python_path.symlink_to(actual)
        entry = directories.layout.root(ManagedDirectoryKind.STATE) / "python-environments" / "legacy-link.json"
        entry.write_text(
            __import__("json").dumps(
                {
                    "environment_id": "legacy-link",
                    "scope": scope_to_data(PLATFORM_SCOPE),
                    "backend": "fake",
                    "root": str(environment_root),
                    "python_path": str(python_path),
                    "ownership": "external",
                }
            ),
            encoding="utf-8",
        )
        migrated = authorities.lifecycle.migrate_legacy(
            "legacy-link",
            python_executable=str(python_path),
            python_version="3.11.15",
        )
        assert migrated.python_path == python_path.absolute()
        assert migrated.python_path.is_symlink()


def test_python_backends_route_interpreters_by_controller_os() -> None:
    runner = _Runner()
    venv_root = Path("C:/env")
    conda_root = Path("C:/env")
    venv_expected = venv_root / "Scripts/python.exe"
    conda_expected = conda_root / "python.exe"
    with patch("noetrium_platform.infrastructure.lifecycle.python.runtime.venv_backend.os.name", "nt"):
        assert VenvEnvironmentBackend(runner).python_path(venv_root) == venv_expected
    with patch("noetrium_platform.infrastructure.lifecycle.python.runtime.conda_backend.os.name", "nt"):
        assert CondaEnvironmentBackend(runner).python_path(conda_root) == conda_expected
