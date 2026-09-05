from __future__ import annotations

from typing import Protocol, runtime_checkable

from noetrium_platform.capabilities.environment.api import EnvironmentProviderPort

from .contracts import BenchmarkCase


@runtime_checkable
class BenchmarkProviderFactoryPort(Protocol):
    """Adapt any external benchmark backend without leaking its SDK."""

    def create(self, case: BenchmarkCase) -> EnvironmentProviderPort: ...


__all__ = ["BenchmarkProviderFactoryPort"]
