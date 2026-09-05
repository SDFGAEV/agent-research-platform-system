from __future__ import annotations

from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayoutPort
from noetrium_platform.infrastructure.lifecycle.python.api import (
    EnvironmentCommandRunnerPort,
    PythonEnvironmentAuthorities,
    PythonEnvironmentBackend,
)

from .execution import PythonEnvironmentExecutor
from .lifecycle import PythonEnvironmentLifecycle
from .packages import PythonPackageManager
from .registry import PythonEnvironmentRegistry


def build_python_environment_authorities(
    directories: DirectoryLayoutPort,
    backends: tuple[PythonEnvironmentBackend, ...],
    runner: EnvironmentCommandRunnerPort,
) -> PythonEnvironmentAuthorities:
    registry = PythonEnvironmentRegistry(directories)
    lifecycle = PythonEnvironmentLifecycle(directories, registry, backends)
    execution = PythonEnvironmentExecutor(lifecycle, runner)
    packages = PythonPackageManager(directories, lifecycle, execution, lifecycle.backend)
    return PythonEnvironmentAuthorities(lifecycle, execution, packages)


__all__ = ["build_python_environment_authorities"]
