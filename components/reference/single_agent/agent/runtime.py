"""Platform-facing runtime seams for reference agent methods.

The reference method remains dependency-injected; this module supplies
checkpoint, event, and capability adapters without importing LangGraph.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Protocol

from noetrium.contracts.json import (
    JsonValue, canonical_digest, canonical_text, freeze_json, strict_json_loads,
)
from noetrium_platform.capabilities.participant.capability.api import (
    CapabilityPort, CapabilityRequest,
)
from noetrium_platform.foundation.kernel.kernel import ExecutionContext

from .contracts import (
    ReferenceAgentAction, ReferenceAgentActionKind, ReferenceAgentObservation,
    ReferenceAgentState,
)


@dataclass(frozen=True, slots=True)
class ReferenceAgentEvent:
    event_type: str
    run_id: str
    step: int
    state_digest: str
    action_digest: str | None = None
    observation_digest: str | None = None
    payload: JsonValue = field(default_factory=dict)
    event_id: str = field(init=False)
    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() for value in (
            self.event_type, self.run_id, self.state_digest,
        )):
            raise ValueError("agent event identity fields must be non-empty")
        if type(self.step) is not int or isinstance(self.step, bool) or self.step < 0:
            raise ValueError("agent event step must be a non-negative integer")
        for name in ("action_digest", "observation_digest"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or len(value) != 64):
                raise ValueError(f"agent event {name} must be SHA-256 when present")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "event_id", canonical_digest({
            "event_type": self.event_type,
            "run_id": self.run_id,
            "step": self.step,
            "state_digest": self.state_digest,
            "action_digest": self.action_digest,
            "observation_digest": self.observation_digest,
            "payload": self.payload,
        }))


class ReferenceAgentProgressPort(Protocol):
    def checkpoint(self, state: ReferenceAgentState, *, context: ExecutionContext) -> str: ...

    def emit(self, event: ReferenceAgentEvent, *, context: ExecutionContext) -> None: ...


class NullReferenceAgentProgress:
    def checkpoint(self, state: ReferenceAgentState, *, context: ExecutionContext) -> str:
        return state.digest

    def emit(self, event: ReferenceAgentEvent, *, context: ExecutionContext) -> None:
        return None


class PlatformCapabilityToolPort:
    """Adapt a platform CapabilityPort to the reference tool protocol."""

    def __init__(
        self,
        capabilities: CapabilityPort,
        context: ExecutionContext,
        *,
        capability_ids: Mapping[str, str] | None = None,
        idempotency_prefix: str = "reference-agent",
    ) -> None:
        if not isinstance(capabilities, CapabilityPort):
            raise TypeError("capabilities must implement CapabilityPort")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not idempotency_prefix.strip():
            raise ValueError("idempotency_prefix must be non-empty")
        self._capabilities = capabilities
        self._context = context
        self._capability_ids = dict(capability_ids or {})
        self._idempotency_prefix = idempotency_prefix

    @staticmethod
    def _content(payload: JsonValue) -> str:
        return canonical_text(payload)

    def invoke_action(self, action: ReferenceAgentAction) -> ReferenceAgentObservation:
        if action.kind is not ReferenceAgentActionKind.TOOL:
            raise ValueError("platform capability tool port accepts tool actions only")
        capability_id = self._capability_ids.get(action.name, action.name)
        request = CapabilityRequest(
            capability_id,
            action.argument_values(),
            self._context,
            idempotency_key=canonical_digest({
                "prefix": self._idempotency_prefix,
                "run_id": self._context.run_id,
                "action_digest": action.action_digest,
            }),
        )
        try:
            descriptor = self._capabilities.describe(capability_id)
            if descriptor.capability_id != capability_id:
                raise TypeError("capability descriptor identity drifted")
            result = self._capabilities.invoke(request)
        except Exception as exc:
            return ReferenceAgentObservation(
                action.action_digest, f"{type(exc).__name__}: {exc}", False,
                capability_id=capability_id, result_digest=None,
            )
        return ReferenceAgentObservation(
            action.action_digest,
            self._content(result.payload),
            True,
            capability_id=capability_id,
            capability_result=result,
        )

    def invoke(self, name: str, arguments: Mapping[str, JsonValue]) -> ReferenceAgentObservation:
        action = ReferenceAgentAction(ReferenceAgentActionKind.TOOL, name, arguments)
        return self.invoke_action(action)
class JsonlReferenceAgentProgress(ReferenceAgentProgressPort):
    """Crash-durable append-only event/checkpoint sink."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _state_payload(state: ReferenceAgentState) -> dict[str, JsonValue]:
        def message(row: object) -> tuple[JsonValue, ...]:
            return (row.role, row.content, row.name)
        return {
            "task": state.task,
            "messages": tuple(message(row) for row in state.messages),
            "scratchpad": tuple(message(row) for row in state.scratchpad),
            "step": state.step,
        }

    def _append(self, event: ReferenceAgentEvent) -> None:
        document = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "run_id": event.run_id,
            "step": event.step,
            "state_digest": event.state_digest,
            "action_digest": event.action_digest,
            "observation_digest": event.observation_digest,
            "payload": event.payload,
        }
        line = (canonical_text(document) + "\n").encode("utf-8")
        descriptor = os.open(
            self._path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def checkpoint(self, state: ReferenceAgentState, *, context: ExecutionContext) -> str:
        event = ReferenceAgentEvent(
            "checkpoint", context.run_id, state.step, state.digest,
            payload=self._state_payload(state),
        )
        self._append(event)
        return event.event_id

    def emit(self, event: ReferenceAgentEvent, *, context: ExecutionContext) -> None:
        if event.run_id != context.run_id:
            raise ValueError("agent event run_id does not match execution context")
        self._append(event)

    def latest_state(self, run_id: str) -> ReferenceAgentState | None:
        if not run_id.strip() or not self._path.exists():
            return None
        latest: Mapping[str, JsonValue] | None = None
        latest_digest: str | None = None
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            document = strict_json_loads(line)
            if isinstance(document, Mapping) and document.get("run_id") == run_id and document.get("event_type") == "checkpoint":
                payload = document.get("payload")
                if isinstance(payload, Mapping):
                    latest = payload
                    digest = document.get("state_digest")
                    latest_digest = digest if isinstance(digest, str) else None
        if not isinstance(latest, Mapping):
            return None
        state = _state_from_payload(latest)
        if latest_digest != state.digest:
            raise ValueError("agent checkpoint state digest mismatch")
        return state


def _state_from_payload(payload: Mapping[str, JsonValue]) -> ReferenceAgentState:
    def messages(key: str) -> tuple[object, ...]:
        rows = payload.get(key)
        if not isinstance(rows, (list, tuple)):
            raise ValueError(f"checkpoint {key} must be a sequence")
        from .contracts import ReferenceAgentMessage
        result = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise ValueError(f"checkpoint {key} message is malformed")
            result.append(ReferenceAgentMessage(row[0], row[1], row[2]))
        return tuple(result)
    return ReferenceAgentState(
        payload["task"], messages=messages("messages"),
        scratchpad=messages("scratchpad"), step=payload["step"],
    )


__all__ = [
    "JsonlReferenceAgentProgress", "NullReferenceAgentProgress",
    "PlatformCapabilityToolPort", "ReferenceAgentEvent",
    "ReferenceAgentProgressPort",
]
