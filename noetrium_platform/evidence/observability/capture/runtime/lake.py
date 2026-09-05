from __future__ import annotations

from typing import Mapping

from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonObject

from ..api.contracts import RawObservationReceipt, RetentionClass
from ..api.ports import RawObservationPersistencePort
from .registry import RawObservationRegistry


class RawObservationLake:
    """Validated raw-observation runtime over an injected persistence authority."""

    def __init__(self, registry: RawObservationRegistry, persistence: RawObservationPersistencePort) -> None:
        self.registry = registry
        self._persistence = persistence

    def _append(
        self,
        context: ExecutionContext,
        family: str,
        payload: JsonObject,
        *,
        timestamp: float | None,
        idempotency_key: str | None,
    ) -> RawObservationReceipt:
        schema = self.registry.validate(family, payload)
        if schema.retention is RetentionClass.SCIENTIFIC_DURABLE and idempotency_key is None:
            raise ValueError(
                f"scientific-durable raw observation requires idempotency key: {family}"
            )
        return self._persistence.append(
            context,
            schema,
            payload,
            timestamp=timestamp,
            idempotency_key=idempotency_key,
        )

    def append(
        self,
        context: ExecutionContext,
        family: str,
        payload: JsonObject,
        *,
        timestamp: float | None = None,
    ) -> RawObservationReceipt:
        return self._append(context, family, payload, timestamp=timestamp, idempotency_key=None)

    def append_once(
        self,
        context: ExecutionContext,
        family: str,
        payload: JsonObject,
        *,
        idempotency_key: str,
        timestamp: float | None = None,
    ) -> RawObservationReceipt:
        if not idempotency_key:
            raise ValueError("raw observation idempotency key must be non-empty")
        return self._append(
            context,
            family,
            payload,
            timestamp=timestamp,
            idempotency_key=idempotency_key,
        )

    def verify(self, run_id: str, family: str) -> tuple[str, ...]:
        return self._persistence.verify(run_id, family)

    def read(self, run_id: str, family: str, *, limit: int = 10000) -> tuple[JsonObject, ...]:
        if limit <= 0:
            return ()
        return self._persistence.read(run_id, family, limit=limit)

    def close(self) -> None:
        self._persistence.close()

    def __enter__(self) -> "RawObservationLake":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["RawObservationLake"]
