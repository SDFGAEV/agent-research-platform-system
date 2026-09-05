from __future__ import annotations

from pathlib import Path
import os

from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult, EnvironmentCommandRunnerPort, PythonEnvironmentSpec


class CondaEnvironmentBackend:
    """Prefix-based conda/mamba backend; registry semantics stay backend-neutral."""

    def __init__(
        self,
        runner: EnvironmentCommandRunnerPort,
        *,
        executable: str = "conda",
        backend_id: str = "conda",
        conda_package_cache: Path | None = None,
        pip_cache: Path | None = None,
    ) -> None:
        if not backend_id:
            raise ValueError("backend_id is required")
        self._runner = runner
        self.executable = executable
        self.backend_id = backend_id
        self._conda_package_cache = conda_package_cache
        self._pip_cache = pip_cache
        for path in (self._conda_package_cache, self._pip_cache):
            if path is not None:
                path.mkdir(parents=True, exist_ok=True)

    def create(self, root: Path, spec: PythonEnvironmentSpec) -> Path:
        root.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.executable, "create", "-y", "-p", str(root)]
        if spec.python_version:
            argv.append(f"python={spec.python_version}")
        environment = (
            {"CONDA_PKGS_DIRS": str(self._conda_package_cache)}
            if self._conda_package_cache is not None else None
        )
        result = self._runner.run(tuple(argv), environment=environment)
        if result.returncode != 0:
            raise RuntimeError(f"{self.backend_id} creation failed: {result.stderr.strip() or result.stdout.strip()}")
        return self.python_path(root)

    def python_path(self, root: Path) -> Path:
        return root / ("python.exe" if os.name == "nt" else "bin/python")

    def install(
        self,
        root: Path,
        requirements: Path,
        *,
        extra_args: tuple[str, ...] = (),
    ) -> EnvironmentCommandResult:
        return self._runner.run(
            (
                str(self.python_path(root)),
                "-m",
                "pip",
                "install",
                *(("--cache-dir", str(self._pip_cache)) if self._pip_cache is not None else ()),
                "-r",
                str(requirements),
                *extra_args,
            )
        )


__all__ = ["CondaEnvironmentBackend"]
