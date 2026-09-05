from __future__ import annotations

import base64
import json

from noetrium_platform.capabilities.environment.api import (
    ActionRequest,
    ActionResult,
    EnvironmentCapability,
    EnvironmentCapabilityDescriptor,
    EnvironmentCapabilityUnsupported,
    EnvironmentDiagnosticsPort,
    EnvironmentIdentity,
    EnvironmentProviderCapabilities,
    EnvironmentQuery,
    EnvironmentQueryResult,
    EnvironmentSession,
    EnvironmentSessionDiagnostics,
    Observation,
    action_request_digest,
)
from noetrium_platform.foundation.kernel.kernel import (
    EffectCertainty,
    EffectClass,
    EffectReceipt,
    ExecutionContext,
    canonical_bytes,
    canonical_digest,
    thaw_json,
)

from ..api import ReplayEvent, ReplayTrace


class ReplayEnvironmentProvider:
    """Deterministic, read-only environment over an immutable raw trace."""

    def __init__(self, trace: ReplayTrace) -> None:
        self._trace = trace
        self._identity = EnvironmentIdentity(
            environment_id=f"replay:{trace.environment_id}:{trace.trace_id}",
            implementation_version="1",
            abi_version="environment.replay.v1",
            schema_version="1",
            artifact_digest=trace.trace_digest,
        )
        self._capabilities = EnvironmentProviderCapabilities((
            EnvironmentCapability.DIAGNOSTICS,
            EnvironmentCapability.QUERY,
            EnvironmentCapability.SNAPSHOT,
            EnvironmentCapability.RESTORE,
            EnvironmentCapability.REPLAY,
        ))

    @property
    def identity(self) -> EnvironmentIdentity:
        return self._identity

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities:
        return self._capabilities

    def open_session(self, *, session_id: str, services: object) -> EnvironmentSession:
        del services
        return _ReplaySession(self._trace, self._identity, session_id)


class _ReplaySession(EnvironmentDiagnosticsPort):
    def __init__(
        self,
        trace: ReplayTrace,
        identity: EnvironmentIdentity,
        session_id: str,
    ) -> None:
        if not session_id.strip():
            raise ValueError("replay session_id must be non-empty")
        self._trace = trace
        self._identity = identity
        self._session_id = session_id
        self._cursor = 0
        self._closed = False

    def observe(self, context: ExecutionContext) -> Observation:
        del context
        self._ensure_open()
        index = min(self._cursor, len(self._trace.events) - 1)
        event = self._trace.events[index]
        self._cursor = min(self._cursor + 1, len(self._trace.events))
        return Observation(
            event.event_id,
            f"{self._trace.trace_digest}:{event.sequence}",
            {
                "kind": event.kind,
                "payload": thaw_json(event.payload),
                "raw_payload_sha256": event.raw_payload_sha256,
                "event_digest": event.event_digest,
                "replay_cursor": self._cursor,
                "replay_complete": self._cursor >= len(self._trace.events),
            },
        )

    def act(self, request: ActionRequest) -> ActionResult:
        self._ensure_open()
        digest = action_request_digest(request)
        effect = EffectReceipt(
            effect_id=f"replay-action:{request.action_id}",
            request_digest=digest,
            effect_class=EffectClass.PURE,
            certainty=EffectCertainty.NO_EFFECT,
            provider_instance_id=f"{self._identity.environment_id}:{self._session_id}",
            verification_required=False,
            provider_receipt=request.action_id,
        )
        return ActionResult(
            action_id=request.action_id,
            accepted=False,
            observation=None,
            effect=effect,
            diagnostics={
                "replay_read_only": True,
                "lifecycle": {
                    "phase": "rejected",
                    "terminal": True,
                    "request_digest": digest,
                },
            },
        )

    def reconcile(self, effect: EffectReceipt, context: ExecutionContext) -> EffectReceipt:
        del context
        self._ensure_open()
        if effect.certainty is not EffectCertainty.NO_EFFECT:
            raise ValueError("replay only reconciles no-effect actions")
        return effect

    def query(self, request: EnvironmentQuery) -> EnvironmentQueryResult:
        self._ensure_open()
        if request.query_type == "trace":
            return EnvironmentQueryResult(
                request.query_id,
                True,
                self._trace.record(),
                None,
            )
        if request.query_type == "state":
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {
                    "cursor": self._cursor,
                    "remaining": len(self._trace.events) - self._cursor,
                    "trace_digest": self._trace.trace_digest,
                },
                None,
            )
        raise EnvironmentCapabilityUnsupported(f"query:{request.query_type}")

    def capability_descriptors(self) -> tuple[EnvironmentCapabilityDescriptor, ...]:
        return (
            EnvironmentCapabilityDescriptor(
                "replay.trace",
                "1",
                action_types=(),
                query_types=("trace", "state"),
                metadata={"trace_digest": self._trace.trace_digest},
            ),
        )

    def checkpoint(self) -> bytes:
        self._ensure_open()
        return canonical_bytes({
            "schema": "environment.replay.session.v1",
            "session_id": self._session_id,
            "trace_digest": self._trace.trace_digest,
            "cursor": self._cursor,
        })

    def restore(self, payload: bytes) -> None:
        self._ensure_open()
        try:
            document = json.loads(payload.decode("utf-8"))
            if set(document) != {"schema", "session_id", "trace_digest", "cursor"}:
                raise ValueError("replay checkpoint schema mismatch")
            if document["schema"] != "environment.replay.session.v1":
                raise ValueError("replay checkpoint version mismatch")
            if document["session_id"] != self._session_id:
                raise ValueError("replay checkpoint session mismatch")
            if document["trace_digest"] != self._trace.trace_digest:
                raise ValueError("replay checkpoint trace mismatch")
            cursor = document["cursor"]
            if not isinstance(cursor, int) or not 0 <= cursor <= len(self._trace.events):
                raise ValueError("replay checkpoint cursor invalid")
        except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("invalid replay checkpoint") from exc
        self._cursor = cursor

    def diagnostics_snapshot(self) -> EnvironmentSessionDiagnostics:
        return EnvironmentSessionDiagnostics(
            session_id=self._session_id,
            environment=self._identity,
            generation=self._trace.trace_digest,
            ready=not self._closed,
            closed=self._closed,
            capabilities=self._capabilities(),
            state_digest=canonical_digest({"cursor": self._cursor}),
        )

    def _capabilities(self) -> EnvironmentProviderCapabilities:
        return EnvironmentProviderCapabilities((
            EnvironmentCapability.DIAGNOSTICS,
            EnvironmentCapability.QUERY,
            EnvironmentCapability.SNAPSHOT,
            EnvironmentCapability.RESTORE,
            EnvironmentCapability.REPLAY,
        ))

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"replay session is closed: {self._session_id}")


__all__ = ["ReplayEnvironmentProvider"]
