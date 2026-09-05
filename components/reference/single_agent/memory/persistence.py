"""Crash-durable persistence for reusable memory components."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Protocol

from noetrium.contracts.json import canonical_text, strict_json_loads

from .stores import MemoryItem


class MemoryPersistencePort(Protocol):
    durability: str

    def load(self, plane: str) -> tuple[MemoryItem, ...]: ...

    def upsert(self, plane: str, item: MemoryItem) -> None: ...

    def replace(self, plane: str, items: tuple[MemoryItem, ...]) -> None: ...

    def close(self) -> None: ...


class SQLiteMemoryPersistence(MemoryPersistencePort):
    """Atomic SQLite persistence shared by working/episodic/vector planes."""

    durability = "crash_durable"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None,
        )
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    plane TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    item_digest TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    embedding_json TEXT,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (plane, namespace, memory_id)
                )
                """
            )

    @staticmethod
    def _row(item: MemoryItem) -> tuple[object, ...]:
        return (
            item.namespace, item.memory_id, item.item_digest, item.content,
            canonical_text(item.tags),
            None if item.embedding is None else canonical_text(item.embedding),
            canonical_text(item.metadata),
        )

    def load(self, plane: str) -> tuple[MemoryItem, ...]:
        if type(plane) is not str or not plane.strip():
            raise ValueError("memory persistence plane must be non-empty")
        with self._lock:
            rows = self._connection.execute(
                "SELECT namespace, memory_id, item_digest, content, tags_json, embedding_json, metadata_json "
                "FROM memory_items WHERE plane = ? ORDER BY namespace, memory_id",
                (plane,),
            ).fetchall()
        items = []
        for row in rows:
            tags = strict_json_loads(row[4])
            embedding = None if row[5] is None else strict_json_loads(row[5])
            metadata = strict_json_loads(row[6])
            item = MemoryItem(
                row[1], row[3],
                tags=tuple(tags),
                embedding=None if embedding is None else tuple(embedding),
                metadata=tuple(tuple(pair) for pair in metadata),
                namespace=row[0],
            )
            if item.item_digest != row[2]:
                raise ValueError(f"memory persistence digest mismatch: {item.memory_id}")
            items.append(item)
        return tuple(items)

    def upsert(self, plane: str, item: MemoryItem) -> None:
        if type(plane) is not str or not plane.strip():
            raise ValueError("memory persistence plane must be non-empty")
        if type(item) is not MemoryItem:
            raise TypeError("memory persistence item must be MemoryItem")
        with self._lock:
            self._connection.execute(
                "INSERT INTO memory_items "
                "(plane, namespace, memory_id, item_digest, content, tags_json, embedding_json, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(plane, namespace, memory_id) DO UPDATE SET "
                "item_digest=excluded.item_digest, content=excluded.content, "
                "tags_json=excluded.tags_json, embedding_json=excluded.embedding_json, "
                "metadata_json=excluded.metadata_json",
                (plane, *self._row(item)),
            )

    def replace(self, plane: str, items: tuple[MemoryItem, ...]) -> None:
        if type(plane) is not str or not plane.strip():
            raise ValueError("memory persistence plane must be non-empty")
        if type(items) is not tuple or any(type(item) is not MemoryItem for item in items):
            raise TypeError("memory persistence items must contain MemoryItem")
        keys = [(item.namespace, item.memory_id) for item in items]
        if len(keys) != len(set(keys)):
            raise ValueError("memory persistence contains duplicate identities")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute("DELETE FROM memory_items WHERE plane = ?", (plane,))
                self._connection.executemany(
                    "INSERT INTO memory_items "
                    "(plane, namespace, memory_id, item_digest, content, tags_json, embedding_json, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple((plane, *self._row(item)) for item in items),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()


__all__ = ["MemoryPersistencePort", "SQLiteMemoryPersistence"]
