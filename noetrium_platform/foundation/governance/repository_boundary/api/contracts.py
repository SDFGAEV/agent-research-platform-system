from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RepositoryBoundaryViolation:
    code: str
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class RepositoryBoundaryReport:
    schema: str
    violations: tuple[RepositoryBoundaryViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


class RepositoryBoundaryAuditor(Protocol):
    """Injected read-only boundary audit authority for product tooling."""

    def __call__(self, root: Path) -> "DownstreamProjectImportReport": ...


class DownstreamImportKind(StrEnum):
    EXTERNAL = "external"
    COMMON_PLATFORM_API = "common_platform_api"
    PROVIDER_DEVELOPMENT_API = "provider_development_api"
    FORBIDDEN_PRIVATE_IMPLEMENTATION = "forbidden_private_implementation"


@dataclass(frozen=True, slots=True, order=True)
class DownstreamImportObservation:
    path: str
    line: int
    module: str
    kind: DownstreamImportKind


@dataclass(frozen=True, slots=True)
class DownstreamProjectImportReport:
    schema: str
    observations: tuple[DownstreamImportObservation, ...]
    violations: tuple[RepositoryBoundaryViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


__all__ = [
    "DownstreamImportKind",
    "DownstreamImportObservation",
    "DownstreamProjectImportReport",
    "RepositoryBoundaryReport",
    "RepositoryBoundaryViolation",
    "RepositoryBoundaryAuditor",
]
