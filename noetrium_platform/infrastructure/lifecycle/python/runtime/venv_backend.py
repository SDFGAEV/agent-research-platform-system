from __future__ import annotations

from pathlib import Path
import os

from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult, EnvironmentCommandRunnerPort, PythonEnvironmentSpec


class VenvEnvironmentBackend:
    backend_id = "venv"

    def __init__(self, runner: EnvironmentCommandRunnerPort, *, pip_cache: Path | None = None) -> None:
        self._runner = runner
        self._pip_cache = pip_cache
        if self._pip_cache is not None:
            self._pip_cache.mkdir(parents=True, exist_ok=True)

    def create(self, root: Path, spec: PythonEnvironmentSpec) -> Path:
        root.parent.mkdir(parents=True, exist_ok=True)
        result = self._runner.run((spec.python_executable, "-m", "venv", str(root)))
        if result.returncode != 0:
            raise RuntimeError(f"venv creation failed: {result.stderr.strip() or result.stdout.strip()}")
        return self.python_path(root)

    def python_path(self, root: Path) -> Path:
        return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def install(
        self,
        root: Path,
        requirements: Path,
        *,
        extra_args: tuple[str, ...] = (),
    ) -> EnvironmentCommandResult:
        argv = (
            str(self.python_path(root)),
            "-m",
            "pip",
            "install",
            *(("--cache-dir", str(self._pip_cache)) if self._pip_cache is not None else ()),
            "-r",
            str(requirements),
            *extra_args,
        )
        return self._runner.run(argv)


__all__ = ["VenvEnvironmentBackend"]
