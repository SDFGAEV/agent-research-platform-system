from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
from threading import Lock
import time

from noetrium_platform.foundation.kernel.concurrency.api import Deadline, SerialActorPort, TaskGroupPort
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, JsonObject
from noetrium_platform.foundation.kernel.kernel.errors import describe_exception

from ..api.contracts import RawObservationReceipt, RawObservationSchema
from .segment_codec import RawSegmentCodecError, canonical_record_bytes, decode_record_json, encode_record
from .segment_pool import RawSegmentPool
from .segment_recovery import scan_raw_segment
from .segment_writer import RawSegmentWriter


class FileRawObservationPersistence:
    """Filesystem-backed raw observation persistence with actor-owned writers."""

    def __init__(self, root: Path, *, task_group: TaskGroupPort) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._pool = RawSegmentPool(root)
        self._task_group = task_group
        self._actors_lock = Lock()
        self._close_lock = Lock()
        self._actors: dict[tuple[str, str], SerialActorPort] = {}
        self._close_pending: dict[tuple[str, str], RawSegmentWriter] = {}
        self._closed = False

    @staticmethod
    def _actor_suffix(run_id: str, family: str) -> str:
        return hashlib.sha256(f"{run_id}\x00{family}".encode("utf-8")).hexdigest()

    def _actor_for(self, run_id: str, family: str) -> SerialActorPort:
        key = (run_id, family)
        with self._actors_lock:
            if self._closed:
                raise RuntimeError("raw observation persistence is closed")
            actor = self._actors.get(key)
            if actor is None:
                suffix = self._actor_suffix(run_id, family)
                actor = self._task_group.open_serial_actor(
                    f"raw-segment:{suffix}",
                    lane_id=f"raw-segment:{suffix}",
                )
                self._actors[key] = actor
            return actor

    def append(
        self,
        context: ExecutionContext,
        schema: RawObservationSchema,
        payload: JsonObject,
        *,
        timestamp: float | None,
        idempotency_key: str | None,
    ) -> RawObservationReceipt:
        resolved_timestamp = time.time() if timestamp is None else float(timestamp)
        preflight: dict[str, object] = {
            "sequence": 0,
            "timestamp": resolved_timestamp,
            "family": schema.family,
            "schema_version": schema.schema_version,
            "retention": schema.retention.value,
            "context": asdict(context),
            "payload": dict(payload),
        }
        if idempotency_key is not None:
            preflight["idempotency_key"] = idempotency_key
        canonical_record_bytes(preflight)
        actor = self._actor_for(context.run_id, schema.family)

        def append_owned() -> RawObservationReceipt:
            segment = self._pool.get(context.run_id, schema.family, schema.schema_version)
            if idempotency_key is not None:
                previous = segment.previous(idempotency_key)
                if previous is not None:
                    return previous
            sequence = segment.sequence + 1
            record: dict[str, object] = {
                "sequence": sequence,
                "timestamp": resolved_timestamp,
                "family": schema.family,
                "schema_version": schema.schema_version,
                "retention": schema.retention.value,
                "context": asdict(context),
                "payload": dict(payload),
            }
            if idempotency_key is not None:
                record["idempotency_key"] = idempotency_key
            encoded, digest = encode_record(record)
            receipt = RawObservationReceipt(
                schema.family,
                schema.schema_version,
                context.run_id,
                str(segment.target),
                sequence,
                digest,
                len(encoded),
            )
            segment.append(encoded, receipt, idempotency_key)
            return receipt

        return actor.call("append", append_owned)

    def verify(self, run_id: str, family: str) -> tuple[str, ...]:
        target = RawSegmentPool.target(self.root, run_id, family)
        actor = self._actor_for(run_id, family)

        def freeze_prefix_owned() -> tuple[int, str] | None:
            if not target.exists():
                return None
            schema_version = self._schema_version_for(target, run_id, family)
            return target.stat().st_size, schema_version

        try:
            snapshot = actor.call("freeze-verify-prefix", freeze_prefix_owned)
            if snapshot is None:
                return (f"missing segment: {target}",)
            snapshot_size, schema_version = snapshot
            scan_raw_segment(
                target,
                family=family,
                schema_version=schema_version,
                run_id=run_id,
                limit_bytes=snapshot_size,
                repair_partial_tail=False,
            )
        except Exception as exc:
            descriptor = describe_exception(exc)
            return (
                f"{descriptor.error_type}: {descriptor.safe_message}; "
                f"error_digest={descriptor.error_digest}",
            )
        return ()

    def read(self, run_id: str, family: str, *, limit: int = 10000) -> tuple[JsonObject, ...]:
        if limit <= 0:
            return ()
        target = RawSegmentPool.target(self.root, run_id, family)
        actor = self._actor_for(run_id, family)

        def read_owned() -> tuple[JsonObject, ...]:
            if not target.exists():
                return ()
            schema_version = self._schema_version_for(target, run_id, family)
            snapshot_size = target.stat().st_size
            scan_raw_segment(
                target,
                family=family,
                schema_version=schema_version,
                run_id=run_id,
                limit_bytes=snapshot_size,
                repair_partial_tail=False,
            )
            rows: list[JsonObject] = []
            with target.open("rb") as handle:
                for raw in handle:
                    if len(rows) >= limit:
                        break
                    rows.append(decode_record_json(raw))
            return tuple(rows)

        return actor.call("read", read_owned)

    @staticmethod
    def _schema_version_for(target: Path, run_id: str, family: str) -> str:
        if not target.exists():
            raise FileNotFoundError(target)
        with target.open("rb") as handle:
            raw = handle.readline()
        if not raw.endswith(b"\n"):
            raise RuntimeError(f"{target}: incomplete first record")
        try:
            row = decode_record_json(raw)
        except RawSegmentCodecError as exc:
            raise RuntimeError(f"{target}: invalid first record") from exc
        if not isinstance(row, dict) or row.get("family") != family:
            raise RuntimeError(f"{target}: family identity mismatch for run {run_id!r}")
        schema_version = row.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise RuntimeError(f"{target}: invalid schema_version")
        return schema_version

    def close(self) -> None:
        """Seal writers once and retry only incomplete actor-owned cleanup."""

        with self._close_lock:
            with self._actors_lock:
                if self._closed and not self._close_pending:
                    return
                if not self._closed:
                    self._closed = True
                    self._close_pending = dict(self._pool.seal())
                actors = dict(self._actors)
                pending = dict(self._close_pending)

            errors: list[BaseException] = []
            failed: dict[tuple[str, str], RawSegmentWriter] = {}
            deadline = Deadline.after(30.0)
            for key, writer in pending.items():
                actor = actors.get(key)
                if actor is None:
                    errors.append(RuntimeError(f"raw segment writer has no actor owner: {key}"))
                    failed[key] = writer
                    continue
                try:
                    actor.call("close-writer", writer.close, deadline=deadline)
                except BaseException as exc:
                    errors.append(exc)
                    failed[key] = writer

            with self._actors_lock:
                self._close_pending = failed
            if errors:
                raise ExceptionGroup("raw observation persistence close failed", errors)
