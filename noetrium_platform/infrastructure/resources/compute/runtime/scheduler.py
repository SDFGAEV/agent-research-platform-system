from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from threading import RLock

from noetrium_platform.infrastructure.resources.compute.api import ComputeAllocation, ComputeHost, ComputeRequirement
from noetrium_platform.foundation.scope.api import ScopeIdentity, ScopeKind

from .inventory import InMemoryComputeInventory


@dataclass(slots=True)
class _HostUsage:
    cpu_cores: int = 0
    memory_bytes: int = 0
    gpu_ids: set[str] = field(default_factory=set)


class InMemoryComputeScheduler:
    """Linearizable in-process capacity scheduler with O(hosts + GPUs) selection.

    It is deliberately an in-memory authority. Cross-process durable allocation
    is provided separately; callers must not treat this class as restart-safe.
    """

    def __init__(self, inventory: InMemoryComputeInventory) -> None:
        self._inventory = inventory
        self._allocations: dict[str, ComputeAllocation] = {}
        self._usage_by_host: dict[str, _HostUsage] = {}
        self._lock = RLock()

    def _usage(self, host_id: str) -> _HostUsage:
        return self._usage_by_host.get(host_id, _HostUsage())

    def _candidates_locked(
        self,
        requirement: ComputeRequirement,
        *,
        scope: ScopeIdentity | None,
    ):
        result = []
        required_labels = dict(requirement.required_labels)
        for host in self._inventory.list_hosts(scope=scope):
            if not host.enabled:
                continue
            if any(dict(host.labels).get(key) != value for key, value in required_labels.items()):
                continue
            usage = self._usage(host.host_id)
            available_gpus = tuple(
                gpu.gpu_id
                for gpu in host.gpus
                if gpu.gpu_id not in usage.gpu_ids
                and gpu.memory_bytes >= requirement.minimum_gpu_memory_bytes
            )
            if host.cpu_cores - usage.cpu_cores < requirement.cpu_cores:
                continue
            if host.memory_bytes - usage.memory_bytes < requirement.memory_bytes:
                continue
            if len(available_gpus) < requirement.gpu_count:
                continue
            result.append(host)
        return tuple(result)

    def candidates(
        self,
        requirement: ComputeRequirement,
        *,
        scope: ScopeIdentity | None = None,
    ):
        with self._lock:
            return self._candidates_locked(requirement, scope=scope)

    def allocate(
        self,
        allocation_id: str,
        scope: ScopeIdentity,
        requirement: ComputeRequirement,
    ) -> ComputeAllocation:
        with self._lock:
            if allocation_id in self._allocations:
                raise ValueError(f"allocation already exists: {allocation_id}")
            hosts = self._candidates_locked(requirement, scope=scope)
            if not hosts:
                raise RuntimeError("no compute host satisfies requirement")
            host = hosts[0]
            usage = self._usage(host.host_id)
            gpu_ids = tuple(
                gpu.gpu_id
                for gpu in host.gpus
                if gpu.gpu_id not in usage.gpu_ids
                and gpu.memory_bytes >= requirement.minimum_gpu_memory_bytes
            )[: requirement.gpu_count]
            row = ComputeAllocation(
                allocation_id,
                scope,
                host.host_id,
                requirement.cpu_cores,
                requirement.memory_bytes,
                gpu_ids,
            )
            self._allocations[allocation_id] = row
            updated = _HostUsage(
                cpu_cores=usage.cpu_cores + row.cpu_cores,
                memory_bytes=usage.memory_bytes + row.memory_bytes,
                gpu_ids=set(usage.gpu_ids).union(row.gpu_ids),
            )
            self._usage_by_host[host.host_id] = updated
            return row

    def release(self, allocation_id: str) -> None:
        with self._lock:
            row = self._allocations.pop(allocation_id, None)
            if row is None:
                return
            usage = self._usage_by_host.get(row.host_id)
            if usage is None:
                raise RuntimeError(f"compute usage index missing for allocation: {allocation_id}")
            remaining_gpus = set(usage.gpu_ids)
            remaining_gpus.difference_update(row.gpu_ids)
            next_usage = _HostUsage(
                cpu_cores=usage.cpu_cores - row.cpu_cores,
                memory_bytes=usage.memory_bytes - row.memory_bytes,
                gpu_ids=remaining_gpus,
            )
            if next_usage.cpu_cores < 0 or next_usage.memory_bytes < 0:
                raise RuntimeError(f"compute usage index underflow: {allocation_id}")
            if next_usage.cpu_cores or next_usage.memory_bytes or next_usage.gpu_ids:
                self._usage_by_host[row.host_id] = next_usage
            else:
                self._usage_by_host.pop(row.host_id, None)

    def allocations(
        self,
        *,
        scope: ScopeIdentity | None = None,
    ) -> tuple[ComputeAllocation, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        allocation
                        for allocation in self._allocations.values()
                        if scope is None or allocation.scope == scope
                    ),
                    key=lambda allocation: allocation.allocation_id,
                )
            )


__all__ = ["InMemoryComputeScheduler"]

class SQLiteComputeScheduler:
    """Crash-safe, cross-process compute allocation authority."""

    SCHEMA_VERSION = 1

    def __init__(
        self, path: str | Path, inventory: InMemoryComputeInventory, *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._inventory = inventory
        self.timeout_seconds = float(timeout_seconds)
        with self._connection() as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS compute_scheduler_meta("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS compute_allocations("
                "allocation_id TEXT PRIMARY KEY, payload TEXT NOT NULL);"
            )
            conn.execute(
                "INSERT OR IGNORE INTO compute_scheduler_meta(key,value) VALUES('schema_version',?)",
                (str(self.SCHEMA_VERSION),),
            )
            row = conn.execute(
                "SELECT value FROM compute_scheduler_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None or int(row[0]) != self.SCHEMA_VERSION:
                raise RuntimeError("unsupported SQLiteComputeScheduler schema")

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=self.timeout_seconds, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout={max(1, int(self.timeout_seconds * 1000))}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _scope(value: ScopeIdentity) -> dict[str, str]:
        return {"kind": value.kind.value, "scope_id": value.scope_id}

    @staticmethod
    def _decode_scope(value: dict[str, str]) -> ScopeIdentity:
        return ScopeIdentity(ScopeKind(value["kind"]), value["scope_id"])

    @classmethod
    def _payload(cls, value: ComputeAllocation) -> str:
        return json.dumps({
            "allocation_id": value.allocation_id, "scope": cls._scope(value.scope),
            "host_id": value.host_id, "cpu_cores": value.cpu_cores,
            "memory_bytes": value.memory_bytes, "gpu_ids": list(value.gpu_ids),
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _decode(cls, payload: str) -> ComputeAllocation:
        value = json.loads(payload)
        return ComputeAllocation(
            value["allocation_id"], cls._decode_scope(value["scope"]),
            value["host_id"], int(value["cpu_cores"]), int(value["memory_bytes"]),
            tuple(value["gpu_ids"]),
        )

    @staticmethod
    def _usage(rows: tuple[ComputeAllocation, ...], host_id: str) -> _HostUsage:
        usage = _HostUsage()
        for row in rows:
            if row.host_id != host_id:
                continue
            usage.cpu_cores += row.cpu_cores
            usage.memory_bytes += row.memory_bytes
            usage.gpu_ids.update(row.gpu_ids)
        return usage

    def _rows(self, conn: sqlite3.Connection) -> tuple[ComputeAllocation, ...]:
        return tuple(
            self._decode(str(row[0]))
            for row in conn.execute(
                "SELECT payload FROM compute_allocations ORDER BY allocation_id"
            ).fetchall()
        )

    def _candidates(
        self, rows: tuple[ComputeAllocation, ...],
        requirement: ComputeRequirement, scope: ScopeIdentity | None,
    ) -> tuple[ComputeHost, ...]:
        required_labels = dict(requirement.required_labels)
        result: list[ComputeHost] = []
        for host in self._inventory.list_hosts(scope=scope):
            if not host.enabled:
                continue
            if any(dict(host.labels).get(key) != value for key, value in required_labels.items()):
                continue
            usage = self._usage(rows, host.host_id)
            available = tuple(
                gpu for gpu in host.gpus
                if gpu.gpu_id not in usage.gpu_ids
                and gpu.memory_bytes >= requirement.minimum_gpu_memory_bytes
            )
            if host.cpu_cores - usage.cpu_cores < requirement.cpu_cores:
                continue
            if host.memory_bytes - usage.memory_bytes < requirement.memory_bytes:
                continue
            if len(available) < requirement.gpu_count:
                continue
            result.append(host)
        return tuple(result)

    def candidates(
        self, requirement: ComputeRequirement, *,
        scope: ScopeIdentity | None = None,
    ) -> tuple[ComputeHost, ...]:
        with self._connection() as conn:
            return self._candidates(self._rows(conn), requirement, scope)

    def allocate(
        self, allocation_id: str, scope: ScopeIdentity,
        requirement: ComputeRequirement,
    ) -> ComputeAllocation:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = self._rows(conn)
            if any(row.allocation_id == allocation_id for row in rows):
                raise ValueError(f"allocation already exists: {allocation_id}")
            hosts = self._candidates(rows, requirement, scope)
            if not hosts:
                raise RuntimeError("no compute host satisfies requirement")
            host = hosts[0]
            usage = self._usage(rows, host.host_id)
            gpu_ids = tuple(
                gpu.gpu_id for gpu in host.gpus
                if gpu.gpu_id not in usage.gpu_ids
                and gpu.memory_bytes >= requirement.minimum_gpu_memory_bytes
            )[: requirement.gpu_count]
            allocation = ComputeAllocation(
                allocation_id, scope, host.host_id, requirement.cpu_cores,
                requirement.memory_bytes, gpu_ids,
            )
            conn.execute(
                "INSERT INTO compute_allocations(allocation_id,payload) VALUES(?,?)",
                (allocation_id, self._payload(allocation)),
            )
            conn.commit()
            return allocation

    def release(self, allocation_id: str) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM compute_allocations WHERE allocation_id=?", (allocation_id,)
            )
            conn.commit()

    def allocations(
        self, *, scope: ScopeIdentity | None = None,
    ) -> tuple[ComputeAllocation, ...]:
        with self._connection() as conn:
            rows = self._rows(conn)
        return tuple(row for row in rows if scope is None or row.scope == scope)


__all__ = ["InMemoryComputeScheduler", "SQLiteComputeScheduler"]
