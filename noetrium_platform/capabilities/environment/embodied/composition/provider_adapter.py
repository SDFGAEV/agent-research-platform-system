from __future__ import annotations

from collections.abc import Callable
import base64
import json
import time

from noetrium_platform.capabilities.environment.api import (
    ActionIdentityViolation,
    ActionRequest,
    ActionResult,
    EnvironmentActionPhase,
    EnvironmentCapability,
    EnvironmentCapabilityDescriptor,
    EnvironmentCapabilityUnsupported,
    EnvironmentDiagnosticsPort,
    EnvironmentIdentity,
    EnvironmentProviderCapabilities,
    EnvironmentProviderPort,
    EnvironmentQuery,
    EnvironmentQueryResult,
    EnvironmentSession,
    EnvironmentSessionDiagnostics,
    EnvironmentSessionServices,
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

from ..api import (
    EmbodiedActionCommand,
    EmbodiedCapabilityPort,
    EmbodiedCheckpointPort,
    EmbodiedEnvironmentPort,
    EmbodiedEvent,
    EmbodiedEventKind,
    EmbodiedQueryPort,
    EmbodiedTrajectorySinkPort,
    EmbodimentSpec,
    EpisodeSpec,
)


class EmbodiedEnvironmentProviderAdapter:
    """Expose an embodied port through the generic provider seam."""

    def __init__(
        self,
        environment: EmbodiedEnvironmentPort,
        *,
        environment_id: str,
        implementation_version: str,
        schema_version: str = "1",
        episode_factory: Callable[[str, EmbodimentSpec], EpisodeSpec] | None = None,
        trajectory_sink: EmbodiedTrajectorySinkPort | None = None,
        checkpoint: EmbodiedCheckpointPort | None = None,
        query: EmbodiedQueryPort | None = None,
    ) -> None:
        if not environment_id.strip() or not implementation_version.strip():
            raise ValueError("environment identity fields must be non-empty")
        self._environment = environment
        self._identity = EnvironmentIdentity(
            environment_id=environment_id,
            implementation_version=implementation_version,
            abi_version="environment.embodied.v2",
            schema_version=schema_version,
            artifact_digest=environment.spec.spec_digest,
        )
        capabilities = [
            EnvironmentCapability.DIAGNOSTICS,
            EnvironmentCapability.RECONCILE,
            EnvironmentCapability.QUERY,
        ]
        if checkpoint is not None:
            capabilities.extend((EnvironmentCapability.SNAPSHOT, EnvironmentCapability.RESTORE))
        if trajectory_sink is not None:
            capabilities.append(EnvironmentCapability.RAW_RECORDS)
        self._capabilities = EnvironmentProviderCapabilities(tuple(capabilities))
        self._episode_factory = episode_factory or self._default_episode
        self._trajectory_sink = trajectory_sink
        self._checkpoint = checkpoint
        self._query = query

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
        del services
        if not session_id.strip():
            raise ValueError("session_id must be non-empty")
        episode = self._episode_factory(session_id, self._environment.spec)
        return _EmbodiedSessionAdapter(
            self._environment,
            episode,
            session_id=session_id,
            trajectory_sink=self._trajectory_sink,
            checkpoint=self._checkpoint,
            query=self._query,
            capabilities=self._capabilities,
            identity=self._identity,
        )


class _EmbodiedSessionAdapter(EnvironmentDiagnosticsPort):
    def __init__(
        self,
        environment: EmbodiedEnvironmentPort,
        episode: EpisodeSpec,
        *,
        session_id: str,
        trajectory_sink: EmbodiedTrajectorySinkPort | None,
        checkpoint: EmbodiedCheckpointPort | None,
        query: EmbodiedQueryPort | None,
        capabilities: EnvironmentProviderCapabilities,
        identity: EnvironmentIdentity,
    ) -> None:
        self._environment = environment
        self._episode = episode
        self._session_id = session_id
        self._trajectory_sink = trajectory_sink
        self._checkpoint = checkpoint
        self._query_port = query
        self._capabilities = capabilities
        self._identity = identity
        self._started = False
        self._closed = False
        self._sequence = 1
        self._last_observation: Observation | None = None
        self._action_digests: dict[str, str] = {}
        self._action_results: dict[str, ActionResult] = {}

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
        digest = action_request_digest(request)
        prior_digest = self._action_digests.get(request.action_id)
        if prior_digest is not None:
            if prior_digest != digest:
                raise ActionIdentityViolation(
                    f"embodied action identity was reused with drift: {request.action_id}"
                )
            prior = self._action_results.get(request.action_id)
            if prior is not None:
                return prior
            raise ActionIdentityViolation(
                f"embodied action was already executed: {request.action_id}"
            )
        command = EmbodiedActionCommand(
            command_id=request.action_id,
            episode_id=self._episode.episode_id,
            action_id=request.action_id,
            sequence=self._sequence,
            raw_payload=canonical_bytes(request.payload),
            normalized_payload=request.payload,
            issued_at_ns=time.time_ns(),
        )
        self._sequence += 1
        self._action_digests[request.action_id] = digest
        events = self._environment.step(command, request.context)
        self._record(events, request.context)
        result = self._action_result(request, events)
        self._action_results[request.action_id] = result
        return result

    def _action_result(
        self,
        request: ActionRequest,
        events: tuple[EmbodiedEvent, ...],
    ) -> ActionResult:
        action_events = tuple(
            event for event in events
            if event.kind is EmbodiedEventKind.ACTION_RESULT
        )
        failed = any(
            event.kind is EmbodiedEventKind.ERROR
            or event.status.lower() in {"error", "rejected", "failed"}
            for event in action_events
        )
        verified = any(
            event.status.lower() in {"ok", "applied", "success", "completed"}
            or (
                isinstance(event.outcome, str)
                and event.outcome in {"ok", "applied", "success", "completed"}
            )
            for event in action_events
        )
        if not action_events:
            certainty = EffectCertainty.EFFECT_UNKNOWN
            accepted = False
        elif failed:
            certainty = EffectCertainty.EFFECT_REJECTED
            accepted = False
        elif verified:
            certainty = EffectCertainty.EFFECT_CONFIRMED
            accepted = True
        else:
            certainty = EffectCertainty.EFFECT_POSSIBLE
            accepted = True
        provider_receipt = action_events[-1].event_id if action_events else None
        effect = EffectReceipt(
            effect_id=f"embodied-action:{request.action_id}",
            request_digest=action_request_digest(request),
            effect_class=EffectClass.RECONCILABLE,
            certainty=certainty,
            provider_instance_id=f"{self._identity.environment_id}:{self._session_id}",
            verification_required=certainty in {
                EffectCertainty.EFFECT_UNKNOWN,
                EffectCertainty.EFFECT_POSSIBLE,
            },
            after_artifact=(
                action_events[-1].event_digest if action_events else None
            ),
            provider_receipt=provider_receipt,
        )
        lifecycle = EnvironmentActionPhase.SETTLED if verified else (
            EnvironmentActionPhase.REJECTED if failed or not action_events
            else EnvironmentActionPhase.UNKNOWN
        )
        return ActionResult(
            action_id=request.action_id,
            accepted=accepted,
            observation=self._last_observation,
            effect=effect,
            diagnostics={
                "episode_id": self._episode.episode_id,
                "event_ids": [event.event_id for event in events],
                "event_digests": [event.event_digest for event in events],
                "raw_payload_sha256": [event.raw_payload_sha256 for event in events],
                "lifecycle": {
                    "phase": lifecycle.value,
                    "request_digest": action_request_digest(request),
                    "terminal": lifecycle is not EnvironmentActionPhase.UNKNOWN,
                    "evidence_refs": [event.event_id for event in action_events],
                },
                "effect_evidence_missing": not bool(action_events),
            },
        )

    def query(self, request: EnvironmentQuery) -> EnvironmentQueryResult:
        self._ensure_open()
        if request.query_type == "capabilities":
            descriptors = self.capability_descriptors()
            return EnvironmentQueryResult(
                request.query_id,
                True,
                {"capabilities": [
                    {
                        "capability_id": descriptor.capability_id,
                        "version": descriptor.version,
                        "action_types": list(descriptor.action_types),
                        "query_types": list(descriptor.query_types),
                        "metadata": descriptor.metadata,
                    }
                    for descriptor in descriptors
                ]},
                self._last_observation,
            )
        if self._query_port is None:
            raise EnvironmentCapabilityUnsupported(EnvironmentCapability.QUERY.value)
        events = self._query_port.query(
            request.query_type,
            thaw_json(request.payload),
            request.context,
        )
        self._record(events, request.context)
        return EnvironmentQueryResult(
            request.query_id,
            bool(events),
            {"event_ids": [event.event_id for event in events]},
            self._last_observation,
            {"event_count": len(events)},
        )

    def capability_descriptors(self) -> tuple[EnvironmentCapabilityDescriptor, ...]:
        if isinstance(self._environment, EmbodiedCapabilityPort):
            values = self._environment.capability_descriptors()
            if all(isinstance(value, EnvironmentCapabilityDescriptor) for value in values):
                return tuple(values)
        return (
            EnvironmentCapabilityDescriptor(
                capability_id="embodied.interaction",
                version=self._identity.abi_version,
                action_types=tuple(item.action_id for item in self._environment.spec.actions),
                query_types=("capabilities",),
                metadata={"embodiment_id": self._environment.spec.embodiment_id},
            ),
        )

    def reconcile(self, effect: EffectReceipt, context: ExecutionContext) -> EffectReceipt:
        del context
        self._ensure_open()
        if not effect.provider_receipt:
            raise ActionIdentityViolation("embodied effect has no provider receipt")
        result = self._action_results.get(effect.provider_receipt.split(":")[-1])
        if result is None or result.effect is None:
            for candidate in self._action_results.values():
                if candidate.effect is not None and candidate.effect.provider_receipt == effect.provider_receipt:
                    result = candidate
                    break
        if result is None or result.effect is None:
            raise ActionIdentityViolation("embodied effect does not identify an applied action")
        if result.effect.request_digest != effect.request_digest:
            raise ActionIdentityViolation("embodied effect request digest drift")
        return result.effect

    def checkpoint(self) -> bytes:
        self._ensure_open()
        if self._checkpoint is None:
            raise EnvironmentCapabilityUnsupported(EnvironmentCapability.SNAPSHOT.value)
        payload = self._checkpoint.capture(episode=self._episode)
        document = {
            "schema": "environment.embodied.session.v2",
            "session_id": self._session_id,
            "episode": self._episode.record(),
            "provider_payload_b64": base64.b64encode(payload).decode("ascii"),
            "started": self._started,
            "sequence": self._sequence,
            "action_digests": self._action_digests,
            "last_observation": self._observation_document(),
        }
        return canonical_bytes(document)

    def restore(self, payload: bytes) -> None:
        self._ensure_open()
        if self._checkpoint is None:
            raise EnvironmentCapabilityUnsupported(EnvironmentCapability.RESTORE.value)
        try:
            document = json.loads(payload.decode("utf-8"))
            if set(document) != {
                "schema", "session_id", "episode", "provider_payload_b64",
                "started", "sequence", "action_digests", "last_observation",
            }:
                raise ValueError("embodied checkpoint schema mismatch")
            if document["schema"] != "environment.embodied.session.v2":
                raise ValueError("embodied checkpoint schema version mismatch")
            if document["session_id"] != self._session_id:
                raise ValueError("embodied checkpoint session identity mismatch")
            provider_payload = base64.b64decode(
                document["provider_payload_b64"], validate=True
            )
            if not isinstance(document["action_digests"], dict):
                raise ValueError("embodied checkpoint action ledger is invalid")
            if not isinstance(document["sequence"], int) or document["sequence"] < 1:
                raise ValueError("embodied checkpoint sequence is invalid")
            self._checkpoint.restore(provider_payload, episode=self._episode)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ValueError("invalid embodied checkpoint") from exc
        self._started = bool(document["started"])
        self._sequence = document["sequence"]
        self._action_digests = {
            str(key): str(value) for key, value in document["action_digests"].items()
        }
        self._action_results.clear()
        self._restore_observation(document["last_observation"])

    def diagnostics_snapshot(self) -> EnvironmentSessionDiagnostics:
        state_digest = (
            None
            if self._last_observation is None
            else canonical_digest(self._last_observation.payload)
        )
        return EnvironmentSessionDiagnostics(
            session_id=self._session_id,
            environment=self._identity,
            generation=self._identity.artifact_digest,
            ready=not self._closed,
            closed=self._closed,
            capabilities=self._capabilities,
            state_digest=state_digest,
        )

    def close(self) -> None:
        if not self._closed:
            self._environment.close()
            self._closed = True

    def _record(
        self,
        events: tuple[EmbodiedEvent, ...],
        context: ExecutionContext,
    ) -> None:
        if not events:
            raise RuntimeError("embodied provider returned no events")
        for event in events:
            if self._trajectory_sink is not None:
                self._trajectory_sink.capture(event, context)
            payload = dict(thaw_json(event.normalized_payload))
            payload.update({
                "event_id": event.event_id,
                "event_kind": event.kind.value,
                "episode_id": event.episode_id,
                "sequence": event.sequence,
                "status": event.status,
                "raw_payload_sha256": event.raw_payload_sha256,
            })
            self._last_observation = Observation(
                observation_id=event.event_id,
                generation=str(event.sequence),
                payload=payload,
            )

    def _observation_document(self) -> dict[str, object] | None:
        if self._last_observation is None:
            return None
        return {
            "observation_id": self._last_observation.observation_id,
            "generation": self._last_observation.generation,
            "payload": self._last_observation.payload,
            "artifact_refs": list(self._last_observation.artifact_refs),
        }

    def _restore_observation(self, document: object) -> None:
        if document is None:
            self._last_observation = None
            return
        if not isinstance(document, dict):
            raise ValueError("embodied checkpoint observation is invalid")
        required = {"observation_id", "generation", "payload", "artifact_refs"}
        if set(document) != required or not isinstance(document["payload"], dict):
            raise ValueError("embodied checkpoint observation schema is invalid")
        self._last_observation = Observation(
            str(document["observation_id"]),
            str(document["generation"]),
            document["payload"],
            tuple(str(item) for item in document["artifact_refs"]),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"environment session is closed: {self._session_id}")


__all__ = ["EmbodiedEnvironmentProviderAdapter"]
