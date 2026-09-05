from __future__ import annotations

from typing import Protocol, runtime_checkable

from noetrium_platform.capabilities.environment.api import (
    ActionRequest, ActionResult, Observation, ExecutionContext,
)
from .contracts import SoftwareEnvironmentSpec


@runtime_checkable
class SoftwareWorldPort(Protocol):
    @property
    def spec(self) -> SoftwareEnvironmentSpec: ...

    def observe(self, context: ExecutionContext) -> Observation: ...

    def act(self, request: ActionRequest) -> ActionResult: ...

    def close(self) -> None: ...


__all__ = ["SoftwareWorldPort"]
