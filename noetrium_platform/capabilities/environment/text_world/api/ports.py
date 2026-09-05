from __future__ import annotations

from typing import Protocol, runtime_checkable

from noetrium_platform.capabilities.environment.api import (
    ActionRequest, ActionResult, Observation, ExecutionContext,
)
from .contracts import TextWorldEnvironmentSpec


@runtime_checkable
class TextWorldPort(Protocol):
    @property
    def spec(self) -> TextWorldEnvironmentSpec: ...

    def observe(self, context: ExecutionContext) -> Observation: ...

    def act(self, request: ActionRequest) -> ActionResult: ...

    def close(self) -> None: ...


__all__ = ["TextWorldPort"]
