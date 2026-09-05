from __future__ import annotations

import json
from pathlib import Path

from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayoutPort, ManagedDirectoryKind
from noetrium_platform.infrastructure.lifecycle.python.api import (
    EnvironmentCommandResult,
    InstalledPythonPackage,
    PythonEnvironmentCloneResult,
    PythonEnvironmentExecutionPort,
    PythonEnvironmentLifecyclePort,
    PythonEnvironmentSpec,
    PythonEnvironmentState,
)
from noetrium_platform.foundation.kernel.kernel.durability.durable_file import atomic_replace_bytes


class PythonPackageManager:
    """Package/environment-content management, separate from environment lifecycle."""

    def __init__(
        self,
        directories: DirectoryLayoutPort,
        lifecycle: PythonEnvironmentLifecyclePort,
        execution: PythonEnvironmentExecutionPort,
        backend_resolver,
    ) -> None:
        self._directories = directories
        self._lifecycle = lifecycle
        self._execution = execution
        self._backend_resolver = backend_resolver
        self._pip_cache = directories.root(ManagedDirectoryKind.CACHE) / "pip"
        self._pip_cache.mkdir(parents=True, exist_ok=True)

    def install(
        self,
        environment_id: str,
        requirements: Path,
        *,
        extra_args: tuple[str, ...] = (),
    ) -> EnvironmentCommandResult:
        value = self._lifecycle.get(environment_id)
        if value.state is not PythonEnvironmentState.READY:
            raise FileNotFoundError(f"Python environment is not ready: {environment_id}")
        return self._backend_resolver(value.backend).install(value.root, requirements, extra_args=extra_args)

    def install_packages(
        self,
        environment_id: str,
        packages: tuple[str, ...],
        *,
        extra_args: tuple[str, ...] = (),
    ) -> EnvironmentCommandResult:
        if not packages:
            raise ValueError("at least one package is required")
        return self._execution.run(
            environment_id, "-m", "pip", "install", "--cache-dir", str(self._pip_cache),
            *packages, *extra_args,
        )

    def uninstall_packages(self, environment_id: str, packages: tuple[str, ...]) -> EnvironmentCommandResult:
        if not packages:
            raise ValueError("at least one package is required")
        return self._execution.run(environment_id, "-m", "pip", "uninstall", "-y", *packages)

    def packages(self, environment_id: str) -> tuple[InstalledPythonPackage, ...]:
        result = self._execution.run(environment_id, "-m", "pip", "list", "--format=json")
        if result.returncode != 0:
            raise RuntimeError("pip package listing failed")
        rows = json.loads(result.stdout or "[]")
        return tuple(InstalledPythonPackage(name=str(row["name"]), version=str(row["version"])) for row in rows)

    def freeze(self, environment_id: str) -> tuple[str, ...]:
        result = self._execution.run(environment_id, "-m", "pip", "freeze")
        if result.returncode != 0:
            raise RuntimeError("pip freeze failed")
        return tuple(line for line in result.stdout.splitlines() if line.strip())

    def export_requirements(self, environment_id: str, target: Path) -> Path:
        lines = self.freeze(environment_id)
        destination = target.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        atomic_replace_bytes(destination, payload)
        return destination

    def clone(self, source_environment_id: str, spec: PythonEnvironmentSpec) -> PythonEnvironmentCloneResult:
        requirements = self.freeze(source_environment_id)
        environment = self._lifecycle.create(spec)
        if not requirements:
            return PythonEnvironmentCloneResult(source_environment_id, environment, 0, None)
        clone_root = self._directories.root(ManagedDirectoryKind.TEMP) / "python-env-clone"
        clone_root.mkdir(parents=True, exist_ok=True)
        requirements_path = clone_root / f"{spec.environment_id}.requirements.txt"
        atomic_replace_bytes(requirements_path, ("\n".join(requirements) + "\n").encode("utf-8"))
        try:
            result = self.install(spec.environment_id, requirements_path)
        finally:
            requirements_path.unlink(missing_ok=True)
        return PythonEnvironmentCloneResult(source_environment_id, environment, len(requirements), result)

    def check(self, environment_id: str) -> EnvironmentCommandResult:
        return self._execution.run(environment_id, "-m", "pip", "check")


__all__ = ["PythonPackageManager"]
