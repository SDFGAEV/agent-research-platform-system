from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .contracts import (
    EnvironmentCommandResult,
    InstalledPythonPackage,
    ManagedPythonEnvironment,
    PythonEnvironmentCloneResult,
    PythonEnvironmentSpec,
)


class EnvironmentCommandRunnerPort(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> EnvironmentCommandResult: ...


class PythonEnvironmentBackend(Protocol):
    backend_id: str

    def create(self, root: Path, spec: PythonEnvironmentSpec) -> Path: ...
    def python_path(self, root: Path) -> Path: ...
    def install(self, root: Path, requirements: Path, *, extra_args: tuple[str, ...] = ()) -> EnvironmentCommandResult: ...


class PythonEnvironmentLookupPort(Protocol):
    def get(self, environment_id: str) -> ManagedPythonEnvironment: ...
    def list(self, *, tags: tuple[str, ...] = ()) -> tuple[ManagedPythonEnvironment, ...]: ...


class PythonEnvironmentLifecyclePort(PythonEnvironmentLookupPort, Protocol):
    def create(self, spec: PythonEnvironmentSpec) -> ManagedPythonEnvironment: ...
    def register_existing(self, spec: PythonEnvironmentSpec, root: Path) -> ManagedPythonEnvironment: ...
    def migrate_legacy(
        self,
        environment_id: str,
        *,
        python_executable: str,
        python_version: str,
    ) -> ManagedPythonEnvironment: ...
    def backends(self) -> tuple[str, ...]: ...
    def remove(self, environment_id: str) -> bool: ...


class PythonEnvironmentExecutionPort(Protocol):
    def command(self, environment_id: str, *args: str) -> tuple[str, ...]: ...
    def run(
        self,
        environment_id: str,
        *args: str,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> EnvironmentCommandResult: ...


class PythonPackageManagementPort(Protocol):
    def install(self, environment_id: str, requirements: Path, *, extra_args: tuple[str, ...] = ()) -> EnvironmentCommandResult: ...
    def install_packages(self, environment_id: str, packages: tuple[str, ...], *, extra_args: tuple[str, ...] = ()) -> EnvironmentCommandResult: ...
    def uninstall_packages(self, environment_id: str, packages: tuple[str, ...]) -> EnvironmentCommandResult: ...
    def packages(self, environment_id: str) -> tuple[InstalledPythonPackage, ...]: ...
    def freeze(self, environment_id: str) -> tuple[str, ...]: ...
    def export_requirements(self, environment_id: str, target: Path) -> Path: ...
    def clone(self, source_environment_id: str, spec: PythonEnvironmentSpec) -> PythonEnvironmentCloneResult: ...
    def check(self, environment_id: str) -> EnvironmentCommandResult: ...


@dataclass(frozen=True, slots=True)
class PythonEnvironmentAuthorities:
    lifecycle: PythonEnvironmentLifecyclePort
    execution: PythonEnvironmentExecutionPort
    packages: PythonPackageManagementPort


__all__ = [
    "EnvironmentCommandRunnerPort",
    "PythonEnvironmentAuthorities",
    "PythonEnvironmentBackend",
    "PythonEnvironmentExecutionPort",
    "PythonEnvironmentLifecyclePort",
    "PythonEnvironmentLookupPort",
    "PythonPackageManagementPort",
]
