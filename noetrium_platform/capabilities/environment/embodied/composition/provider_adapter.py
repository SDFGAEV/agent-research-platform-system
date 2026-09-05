from __future__ import annotations

from collections.abc import Callable
import time

from noetrium_platform.capabilities.environment.api import (
    ActionRequest,
    ActionResult,
    EnvironmentCapabilityUnsupported,
    EnvironmentIdentity,
    EnvironmentProviderCapabilities,
    EnvironmentProviderPort,
    EnvironmentSession,
    EnvironmentSessionServices,
    Observation,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, canonical_bytes, thaw_json

from ..api import (
    EmbodiedActionCommand,
    EmbodiedEnvironmentPort,
    EmbodiedEvent,
    EmbodiedEventKind,
    EmbodiedTrajectorySinkPort,
    EmbodimentSpec,
    EpisodeSpec,
)


class EmbodiedEnvironmentProviderAdapter:
    """Expose any embodied port through the generic environment provider seam."""

    def __init__(
        self,
        environment: EmbodiedEnvironmentPort,
        *,
        environment_id: str,
        implementation_version: str,
        schema_version: str = "1",
        episode_factory: Callable[[str, EmbodimentSpec], EpisodeSpec] | None = None,
        trajectory_sink: EmbodiedTrajectorySinkPort | None = None,
    ) -> None:
        if not environment_id.strip() or not implementation_version.strip():
            raise ValueError("environment identity fields must be non-empty")
        self._environment = environment
        self._identity = EnvironmentIdentity(
            environment_id=environment_id,
            implementation_version=implementation_version,
            abi_version="environment.embodied.v1",
            schema_version=schema_version,
            artifact_digest=environment.spec.spec_digest,
        )
        self._capabilities = EnvironmentProviderCapabilities()
        self._episode_factory = episode_factory or self._default_episode
        self._trajectory_sink = trajectory_sink

    @staticmethod
    def _default_episode(session_id: str, spec: EmbodimentSpec) -> EpisodeSpec:
        return EpisodeSpec(
            episode_id=session_id,
            environment_id="embodied",
            embodiment_id=spec.embodiment_id,
            task_id="unspecified",
        )

    @property
    def identity(self) -> EnvironmentIdentity:
        return self._identity

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities:
        return self._capabilities

    def open_session(
        self,
        *,
        session_id: str,
        services: EnvironmentSessionServices,
    ) -> EnvironmentSession:
        if not session_id.strip():
            raise ValueError("session_id must be non-empty")
        episode = self._episode_factory(session_id, self._environment.spec)
        return _EmbodiedSessionAdapter(
            self._environment,
            episode,
            session_id=session_id,
            trajectory_sink=self._trajectory_sink,
        )


class _EmbodiedSessionAdapter:
    def __init__(
        self,
        environment: EmbodiedEnvironmentPort,
        episode: EpisodeSpec,
        *,
        session_id: str,
        trajectory_sink: EmbodiedTrajectorySinkPort | None,
    ) -> None:
        self._environment = environment
        self._episode = episode
        self._session_id = session_id
        self._trajectory_sink = trajectory_sink
        self._started = False
        self._closed = False
        self._sequence = 1
        self._last_observation: Observation | None = None

    def observe(self, context: ExecutionContext) -> Observation:
        self._ensure_open()
        if not self._started:
            self._record(self._environment.reset(self._episode, context), context)
            self._started = True
        if self._last_observation is None:
            raise RuntimeError("embodied environment returned no observable event")
        return self._last_observation

    def act(self, request: ActionRequest) -> ActionResult:
        self._ensure_open()
        command = EmbodiedActionCommand(
            command_id=request.action_id,
            episode_id=self._episode.episode_id,
            action_id=request.action_type,
            sequence=self._sequence,
            raw_payload=canonical_bytes(request.payload),
            normalized_payload=request.payload,
            issued_at_ns=time.time_ns(),
        )
        self._sequence += 1
        events = self._environment.step(command, request.context)
        self._record(events, request.context)
        failed = any(
            event.kind is EmbodiedEventKind.ERROR
            or event.status.lower() in {"error", "rejected", "failed"}
            for event in events
        )
        return ActionResult(
            action_id=request.action_id,
            accepted=not failed,
            observation=self._last_observation,
            effect=None,
            diagnostics={
                "episode_id": self._episode.episode_id,
                "event_ids": [event.event_id for event in events],
                "event_digests": [event.event_digest for event in events],
            },
        )

    def reconcile(self, effect: object, context: ExecutionContext) -> object:
        raise EnvironmentCapabilityUnsupported("reconcile")

    def checkpoint(self) -> bytes:
        raise EnvironmentCapabilityUnsupported("snapshot")

    def restore(self, payload: bytes) -> None:
        raise EnvironmentCapabilityUnsupported("restore")

    def close(self) -> None:
        if not self._closed:
            self._environment.close()
            self._closed = True

    def _record(self, events: tuple[EmbodiedEvent, ...], context: ExecutionContext) -> None:
        for event in events:
            if self._trajectory_sink is not None:
                self._trajectory_sink.capture(event, context)
            payload = dict(thaw_json(event.normalized_payload))
            payload.update(
                {
                    "event_id": event.event_id,
                    "event_kind": event.kind.value,
                    "episode_id": event.episode_id,
                    "sequence": event.sequence,
                    "status": event.status,
                    "raw_payload_sha256": event.raw_payload_sha256,
                }
            )
            self._last_observation = Observation(
                observation_id=event.event_id,
                generation=str(event.sequence),
                payload=payload,
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"environment session is closed: {self._session_id}")


__all__ = ["EmbodiedEnvironmentProviderAdapter"]
