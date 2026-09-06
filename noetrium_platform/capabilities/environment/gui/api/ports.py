from __future__ import annotations

from typing import Protocol, runtime_checkable

from noetrium_platform.capabilities.environment.api import (
    ActionRequest, ActionResult, EnvironmentSession, Observation, ExecutionContext,
)
from .contracts import GuiEnvironmentSpec


@runtime_checkable
class GuiWorldPort(EnvironmentSession, Protocol):
    @property
    def spec(self) -> GuiEnvironmentSpec: ...

    def observe(self, context: ExecutionContext) -> Observation: ...

    def act(self, request: ActionRequest) -> ActionResult: ...

    def close(self) -> None: ...


__all__ = ["GuiWorldPort"]
