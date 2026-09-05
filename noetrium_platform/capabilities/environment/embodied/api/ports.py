from __future__ import annotations

from typing import Protocol, runtime_checkable

from noetrium_platform.foundation.kernel.kernel import ExecutionContext

from .contracts import (
    EmbodiedActionCommand,
    EmbodiedCaptureReceipt,
    EmbodiedEvent,
    EmbodimentSpec,
    EpisodeSpec,
)


@runtime_checkable
class EmbodiedEnvironmentPort(Protocol):
    @property
    def spec(self) -> EmbodimentSpec: ...

    def reset(
        self, episode: EpisodeSpec, context: ExecutionContext
    ) -> tuple[EmbodiedEvent, ...]: ...

    def step(
        self, command: EmbodiedActionCommand, context: ExecutionContext
    ) -> tuple[EmbodiedEvent, ...]: ...

    def close(self) -> None: ...


@runtime_checkable
class EmbodiedTrajectorySinkPort(Protocol):
    def capture(
        self, event: EmbodiedEvent, context: ExecutionContext
    ) -> EmbodiedCaptureReceipt: ...


__all__ = ["EmbodiedEnvironmentPort", "EmbodiedTrajectorySinkPort"]
