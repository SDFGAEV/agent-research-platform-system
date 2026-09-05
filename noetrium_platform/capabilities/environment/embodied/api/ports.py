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


@runtime_checkable
class EmbodiedCheckpointPort(Protocol):
    def capture(self, *, episode: EpisodeSpec) -> bytes: ...

    def restore(self, payload: bytes, *, episode: EpisodeSpec) -> None: ...


@runtime_checkable
class EmbodiedQueryPort(Protocol):
    def query(
        self,
        query_type: str,
        payload: object,
        context: ExecutionContext,
    ) -> tuple[EmbodiedEvent, ...]: ...


@runtime_checkable
class EmbodiedCapabilityPort(Protocol):
    def capability_descriptors(self) -> tuple[object, ...]: ...


__all__ = [
    "EmbodiedCapabilityPort",
    "EmbodiedCheckpointPort",
    "EmbodiedEnvironmentPort",
    "EmbodiedQueryPort",
    "EmbodiedTrajectorySinkPort",
]
