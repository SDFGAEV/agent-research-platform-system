from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from noetrium_platform.foundation.kernel.kernel import canonical_digest
from noetrium_platform.foundation.scope.api import ScopeIdentity


class PythonEnvironmentState(StrEnum):
    REGISTERED = "registered"
    READY = "ready"
    MISSING = "missing"


class PythonEnvironmentOwnership(StrEnum):
    MANAGED = "managed"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class PythonEnvironmentSpec:
    environment_id: str
    scope: ScopeIdentity
    backend: str = "venv"
    python_executable: str = "python3"
    python_version: str | None = None
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.environment_id or any(char in self.environment_id for char in "/\\\x00\r\n"):
            raise ValueError("Python environment id must be a safe non-empty identifier")
        if not self.backend.strip():
            raise ValueError("Python environment backend must be non-empty")
        if not self.python_executable.strip():
            raise ValueError("Python environment executable must be non-empty")

    @property
    def specification_digest(self) -> str:
        """Identity of the requested logical environment, before materialization."""

        return canonical_digest(
            {
                "environment_id": self.environment_id,
                "scope": self.scope,
                "backend": self.backend,
                "python_executable": self.python_executable,
                "python_version": self.python_version,
                "description": self.description,
                "tags": tuple(sorted(set(self.tags))),
            }
        )


@dataclass(frozen=True, slots=True)
class ManagedPythonEnvironment:
    environment_id: str
    scope: ScopeIdentity
    backend: str
    root: Path
    python_path: Path
    state: PythonEnvironmentState
    ownership: PythonEnvironmentOwnership
    description: str = ""
    tags: tuple[str, ...] = ()
    specification_digest: str = ""

    @property
    def identity_digest(self) -> str:
        """Identity of one materialized environment instance and its location."""

        if len(self.specification_digest) != 64:
            raise ValueError("managed Python environment specification digest is missing")
        return canonical_digest(
            {
                "environment_id": self.environment_id,
                "scope": self.scope,
                "backend": self.backend,
                "root": str(self.root),
                "python_path": str(self.python_path),
                "ownership": self.ownership.value,
                "specification_digest": self.specification_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class EnvironmentCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class InstalledPythonPackage:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class PythonEnvironmentCloneResult:
    source_environment_id: str
    environment: ManagedPythonEnvironment
    requirements_count: int
    install_result: EnvironmentCommandResult | None = None


__all__ = [
    "EnvironmentCommandResult",
    "InstalledPythonPackage",
    "ManagedPythonEnvironment",
    "PythonEnvironmentCloneResult",
    "PythonEnvironmentOwnership",
    "PythonEnvironmentSpec",
    "PythonEnvironmentState",
]
