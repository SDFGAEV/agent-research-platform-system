from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock

from noetrium_platform.infrastructure.resources.compute.api import ComputeCluster, ComputeGPU, ComputeHost
from noetrium_platform.foundation.scope.api import ScopeIdentity, ScopeKind


class InMemoryComputeInventory:
    """Thread-safe observation/catalog projection for compute resources.

    This class is intentionally non-durable. It is suitable for a projection or
    test authority; durable fleet identity belongs in a persistent provider.
    """

    def __init__(self) -> None:
        self._hosts: dict[str, ComputeHost] = {}
        self._clusters: dict[str, ComputeCluster] = {}
        self._lock = RLock()

    def register_host(self, host: ComputeHost) -> None:
        with self._lock:
            current = self._hosts.get(host.host_id)
            if current is not None and current != host:
                raise ValueError(f"host identity already registered: {host.host_id}")
            self._hosts[host.host_id] = host

    def host(self, host_id: str) -> ComputeHost:
        with self._lock:
            try:
                return self._hosts[host_id]
            except KeyError as exc:
                raise KeyError(host_id) from exc

    def list_hosts(self, *, scope: ScopeIdentity | None = None) -> tuple[ComputeHost, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (host for host in self._hosts.values() if scope is None or host.scope == scope),
                    key=lambda host: host.host_id,
                )
            )

    def register_cluster(self, cluster: ComputeCluster) -> None:
        with self._lock:
            missing = [host_id for host_id in cluster.host_ids if host_id not in self._hosts]
            if missing:
                raise KeyError(missing[0])
            current = self._clusters.get(cluster.cluster_id)
            if current is not None and current != cluster:
                raise ValueError(f"cluster identity already registered: {cluster.cluster_id}")
            self._clusters[cluster.cluster_id] = cluster

    def cluster(self, cluster_id: str) -> ComputeCluster:
        with self._lock:
            try:
                return self._clusters[cluster_id]
            except KeyError as exc:
                raise KeyError(cluster_id) from exc


__all__ = ["InMemoryComputeInventory"]

class SQLiteComputeInventory:
    """Restart-safe compute host/cluster catalog with atomic identity writes."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path, *, timeout_seconds: float = 30.0) -> None:
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = float(timeout_seconds)
        with self._connection() as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS compute_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS compute_hosts(host_id TEXT PRIMARY KEY, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS compute_clusters(cluster_id TEXT PRIMARY KEY, payload TEXT NOT NULL);"
            )
            conn.execute(
                "INSERT OR IGNORE INTO compute_meta(key,value) VALUES('schema_version',?)",
                (str(self.SCHEMA_VERSION),),
            )
            row = conn.execute(
                "SELECT value FROM compute_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None or int(row[0]) != self.SCHEMA_VERSION:
                raise RuntimeError("unsupported SQLiteComputeInventory schema")

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
    def _host_payload(cls, host: ComputeHost) -> str:
        return json.dumps({
            "host_id": host.host_id,
            "scope": cls._scope(host.scope),
            "cpu_cores": host.cpu_cores,
            "memory_bytes": host.memory_bytes,
            "gpus": [
                {"gpu_id": gpu.gpu_id, "memory_bytes": gpu.memory_bytes,
                 "model": gpu.model, "labels": list(gpu.labels)}
                for gpu in host.gpus
            ],
            "labels": list(host.labels),
            "enabled": host.enabled,
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _decode_host(cls, payload: str) -> ComputeHost:
        value = json.loads(payload)
        gpus = tuple(
            ComputeGPU(
                row["gpu_id"], int(row["memory_bytes"]), row["model"],
                tuple(tuple(item) for item in row["labels"]),
            )
            for row in value["gpus"]
        )
        return ComputeHost(
            value["host_id"], cls._decode_scope(value["scope"]),
            int(value["cpu_cores"]), int(value["memory_bytes"]), gpus,
            tuple(tuple(item) for item in value["labels"]), bool(value["enabled"]),
        )

    @classmethod
    def _cluster_payload(cls, cluster: ComputeCluster) -> str:
        return json.dumps({
            "cluster_id": cluster.cluster_id, "scope": cls._scope(cluster.scope),
            "host_ids": list(cluster.host_ids), "labels": list(cluster.labels),
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _decode_cluster(cls, payload: str) -> ComputeCluster:
        value = json.loads(payload)
        return ComputeCluster(
            value["cluster_id"], cls._decode_scope(value["scope"]),
            tuple(value["host_ids"]), tuple(tuple(item) for item in value["labels"]),
        )

    def _put(self, table: str, key: str, payload: str) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"INSERT OR IGNORE INTO {table}({('host_id' if table == 'compute_hosts' else 'cluster_id')},payload) VALUES(?,?)",
                (key, payload),
            )
            conn.commit()

    def register_host(self, host: ComputeHost) -> None:
        payload = self._host_payload(host)
        self._put("compute_hosts", host.host_id, payload)
        if self.host(host.host_id) != host:
            raise ValueError(f"host identity already registered: {host.host_id}")

    def host(self, host_id: str) -> ComputeHost:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM compute_hosts WHERE host_id=?", (host_id,)
            ).fetchone()
        if row is None:
            raise KeyError(host_id)
        return self._decode_host(str(row[0]))

    def list_hosts(self, *, scope: ScopeIdentity | None = None) -> tuple[ComputeHost, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM compute_hosts ORDER BY host_id"
            ).fetchall()
        hosts = tuple(self._decode_host(str(row[0])) for row in rows)
        return tuple(host for host in hosts if scope is None or host.scope == scope)

    def register_cluster(self, cluster: ComputeCluster) -> None:
        missing = [host_id for host_id in cluster.host_ids
                   if not self._exists("compute_hosts", "host_id", host_id)]
        if missing:
            raise KeyError(missing[0])
        payload = self._cluster_payload(cluster)
        self._put("compute_clusters", cluster.cluster_id, payload)
        if self.cluster(cluster.cluster_id) != cluster:
            raise ValueError(f"cluster identity already registered: {cluster.cluster_id}")

    def _exists(self, table: str, key_column: str, key: str) -> bool:
        with self._connection() as conn:
            return conn.execute(
                f"SELECT 1 FROM {table} WHERE {key_column}=?", (key,)
            ).fetchone() is not None

    def cluster(self, cluster_id: str) -> ComputeCluster:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM compute_clusters WHERE cluster_id=?", (cluster_id,)
            ).fetchone()
        if row is None:
            raise KeyError(cluster_id)
        return self._decode_cluster(str(row[0]))


__all__ = ["InMemoryComputeInventory", "SQLiteComputeInventory"]
