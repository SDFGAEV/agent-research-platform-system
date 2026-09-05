from __future__ import annotations

from pathlib import Path
from typing import Mapping

from noetrium_platform.infrastructure.lifecycle.python.api import (
    EnvironmentCommandResult,
    EnvironmentCommandRunnerPort,
    PythonEnvironmentLookupPort,
    PythonEnvironmentState,
)


class PythonEnvironmentExecutor:
    """Command execution authority for an already-registered environment."""

    def __init__(self, environments: PythonEnvironmentLookupPort, runner: EnvironmentCommandRunnerPort) -> None:
        self._environments = environments
        self._runner = runner

    def command(self, environment_id: str, *args: str) -> tuple[str, ...]:
        value = self._environments.get(environment_id)
        if value.state is not PythonEnvironmentState.READY:
            raise FileNotFoundError(f"Python environment is not ready: {environment_id}")
        return (str(value.python_path), *args)

    def run(
        self,
        environment_id: str,
        *args: str,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> EnvironmentCommandResult:
        return self._runner.run(
            self.command(environment_id, *args),
            cwd=cwd,
            environment=environment,
        )


__all__ = ["PythonEnvironmentExecutor"]
