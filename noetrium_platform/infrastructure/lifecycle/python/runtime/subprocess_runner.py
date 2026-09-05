from __future__ import annotations

from pathlib import Path
from typing import Mapping

from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult
from noetrium_platform.foundation.kernel.kernel.process import LocalCommandRunnerPort


class SubprocessEnvironmentCommandRunner:
    """Environment adapter over the platform-wide local command port."""

    def __init__(self, runner: LocalCommandRunnerPort) -> None:
        self._runner = runner

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> EnvironmentCommandResult:
        completed = self._runner.run(argv, cwd=cwd, environment=environment)
        return EnvironmentCommandResult(
            argv=completed.argv,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


__all__ = ["SubprocessEnvironmentCommandRunner"]
