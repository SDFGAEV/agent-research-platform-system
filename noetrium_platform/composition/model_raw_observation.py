from __future__ import annotations

import time
from collections.abc import Mapping
from threading import RLock

from noetrium_platform.capabilities.model.serving.endpoint.api import (
    JsonHttpResponse,
    ModelEndpointObserverPort,
    ModelEndpointRequest,
)
from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity
from noetrium_platform.foundation.kernel.kernel import JsonObject, canonical_bytes
from noetrium_platform.evidence.observability.capture.api import RawObservationEnvelope
from noetrium_platform.evidence.observability.capture.runtime import (
    RegistryBoundRawObservationGateway,
)


class RawLakeModelEndpointObserver(ModelEndpointObserverPort):
    """Fail-closed model wire observer backed by the registry-bound raw lake."""

    observer_id = "observability.capture.model-endpoint.v1"

    def __init__(
        self,
        gateway: RegistryBoundRawObservationGateway,
        *,
        system: SystemIdentity = SystemIdentity("model", ("serving", "endpoint")),
        producer_version: str = "model-endpoint.v1",
    ) -> None:
        self._gateway = gateway
        self._system = system
        self._producer_version = producer_version
        self._attempts: dict[str, int] = {}
        self._attempts_lock = RLock()
        self._gateway.register_producer(self.observer_id, self._system)

    def _identity(self, request: ModelEndpointRequest, *, advance: bool = True) -> tuple[str, int]:
        request_id = request.request.request_id
        with self._attempts_lock:
            attempt = self._attempts.get(request_id, 0) + (1 if advance else 0)
            attempt = max(1, attempt)
            self._attempts[request_id] = attempt
        return request_id, attempt

    def _emit(
        self,
        request: ModelEndpointRequest,
        *,
        family: str,
        event_id: str,
        attempt: int,
        raw_payload: bytes,
        payload: JsonObject,
        status: str,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
        dimensions: JsonObject | None = None,
    ) -> None:
        context = request.request.context
        self._gateway.capture(
            RawObservationEnvelope(
                event_id=event_id,
                family=family,
                context=context,
                system=self._system,
                producer_id=self.observer_id,
                payload=payload,
                raw_payload=raw_payload,
                occurred_at=time.time(),
                recorded_at=time.time(),
                status=status,
                outcome=status,
                producer_version=self._producer_version,
                producer_instance_id=self.observer_id,
                stream_id=request.request.request_id,
                attempt_id=f"attempt-{attempt}",
                correlation_id=context.trace_id,
                dimensions={
                    "deployment_id": request.deployment_id,
                    "started_monotonic_ns": started_monotonic_ns,
                    "completed_monotonic_ns": completed_monotonic_ns,
                    "duration_ns": max(0, completed_monotonic_ns - started_monotonic_ns),
                    **dict(dimensions or {}),
                },
            )
        )
    def on_exchange(
        self,
        request: ModelEndpointRequest,
        response: JsonHttpResponse,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
    ) -> None:
        request_id, attempt = self._identity(request)
        request_body = response.request_body or canonical_bytes(request.body)
        response_body = response.raw_body
        status = f"http_{response.status_code}"
        model = request.request.model.logical_name
        base = {
            "role": request.request.role,
            "model": model,
            "request_digest": request.digest(),
            "status": status,
            "request_id": request_id,
            "deployment_id": request.deployment_id,
            "response_status_code": response.status_code,
        }
        usage = response.body.get("usage") if isinstance(response.body, Mapping) else None
        self._emit(
            request,
            family="llm.request.raw",
            event_id=f"{request_id}:attempt:{attempt}:request",
            attempt=attempt,
            raw_payload=request_body,
            payload=base,
            status=status,
            started_monotonic_ns=started_monotonic_ns,
            completed_monotonic_ns=completed_monotonic_ns,
            dimensions={"usage": usage} if usage is not None else None,
        )
        self._emit(
            request,
            family="llm.attempt.raw",
            event_id=f"{request_id}:attempt:{attempt}:response",
            attempt=attempt,
            raw_payload=response_body,
            payload={
                "role": request.request.role,
                "model": model,
                "endpoint": request.deployment_id,
                "attempt": attempt,
                "status": status,
                "request_id": request_id,
                "finish_reason": (
                    response.body.get("choices", [{}])[0].get("finish_reason")
                    if isinstance(response.body, Mapping)
                    and isinstance(response.body.get("choices"), (list, tuple))
                    and response.body.get("choices")
                    and isinstance(response.body["choices"][0], Mapping)
                    else None
                ),
            },
            status=status,
            started_monotonic_ns=started_monotonic_ns,
            completed_monotonic_ns=completed_monotonic_ns,
            dimensions={"usage": usage} if usage is not None else None,
        )
    def on_failure(
        self,
        request: ModelEndpointRequest,
        error_type: str,
        error_message: str,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
        request_body: bytes,
        response_body: bytes,
    ) -> None:
        request_id, attempt = self._identity(request, advance=False)
        model = request.request.model.logical_name
        status = "failed"
        common = {
            "role": request.request.role,
            "model": model,
            "request_digest": request.digest(),
            "status": status,
            "request_id": request_id,
            "deployment_id": request.deployment_id,
            "error_type": error_type,
            "error_message": error_message,
        }
        self._emit(
            request,
            family="llm.request.raw",
            event_id=f"{request_id}:failure:request:{attempt}",
            attempt=attempt,
            raw_payload=request_body or canonical_bytes(request.body),
            payload=common,
            status=status,
            started_monotonic_ns=started_monotonic_ns,
            completed_monotonic_ns=completed_monotonic_ns,
            dimensions={"failure": {"type": error_type, "message": error_message}},
        )
        self._emit(
            request,
            family="llm.attempt.raw",
            event_id=f"{request_id}:failure:attempt:{attempt}",
            attempt=attempt,
            raw_payload=response_body,
            payload={
                "role": request.request.role,
                "model": model,
                "endpoint": request.deployment_id,
                "attempt": attempt,
                "status": status,
                "request_id": request_id,
                "error_type": error_type,
                "error_message": error_message,
            },
            status=status,
            started_monotonic_ns=started_monotonic_ns,
            completed_monotonic_ns=completed_monotonic_ns,
            dimensions={"failure": {"type": error_type, "message": error_message}},
        )


__all__ = ["RawLakeModelEndpointObserver"]
