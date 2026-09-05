"""Small typed state-graph runtime inspired by durable agent graphs."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Protocol

from noetrium.contracts.json import (
    JsonValue, canonical_digest, canonical_text, freeze_json, require_sha256,
    strict_json_loads,
)


StateUpdate = Mapping[str, JsonValue]
GraphReducer = Callable[[JsonValue | None, JsonValue], JsonValue]
GraphNode = Callable[[Mapping[str, JsonValue]], object]
GraphRouter = Callable[[Mapping[str, JsonValue]], object]


@dataclass(frozen=True, slots=True)
class GraphCommand:
    update: StateUpdate = field(default_factory=dict)
    goto: str | None = None
    resume: JsonValue | None = None
    interrupt: JsonValue | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.update, Mapping):
            raise TypeError("graph command update must be a mapping")
        object.__setattr__(self, "update", freeze_json(self.update))
        if self.goto is not None and (not isinstance(self.goto, str) or not self.goto.strip()):
            raise ValueError("graph command goto must be non-empty when present")


@dataclass(frozen=True, slots=True)
class GraphSend:
    target: str
    update: StateUpdate = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("graph send target must be non-empty")
        object.__setattr__(self, "update", freeze_json(self.update))
@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    thread_id: str
    step: int
    values: StateUpdate
    next_nodes: tuple[str, ...]
    parent_checkpoint_id: str | None = None
    interrupts: tuple[JsonValue, ...] = ()
    checkpoint_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.thread_id, str) or not self.thread_id.strip():
            raise ValueError("graph snapshot thread_id is required")
        if type(self.step) is not int or isinstance(self.step, bool) or self.step < 0:
            raise ValueError("graph snapshot step must be non-negative")
        if type(self.next_nodes) is not tuple or any(
            type(node) is not str or not node.strip() for node in self.next_nodes
        ) or len(self.next_nodes) != len(set(self.next_nodes)):
            raise ValueError("graph snapshot next_nodes must be unique non-empty strings")
        if self.parent_checkpoint_id is not None:
            require_sha256(self.parent_checkpoint_id, "graph snapshot parent_checkpoint_id")
        object.__setattr__(self, "values", freeze_json(self.values))
        object.__setattr__(self, "interrupts", tuple(freeze_json(row) for row in self.interrupts))
        object.__setattr__(self, "checkpoint_id", canonical_digest({
            "thread_id": self.thread_id, "step": self.step,
            "values": self.values, "next_nodes": self.next_nodes,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "interrupts": self.interrupts,
        }))


@dataclass(frozen=True, slots=True)
class GraphEvent:
    event_type: str
    thread_id: str
    step: int
    node: str | None
    payload: JsonValue
    event_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.event_type.strip() or not self.thread_id.strip():
            raise ValueError("graph event identity is required")
        if type(self.step) is not int or isinstance(self.step, bool) or self.step < 0:
            raise ValueError("graph event step must be non-negative")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        object.__setattr__(self, "event_id", canonical_digest({
            "event_type": self.event_type, "thread_id": self.thread_id,
            "step": self.step, "node": self.node, "payload": self.payload,
        }))


class GraphCheckpointerPort(Protocol):
    def save(self, snapshot: GraphSnapshot) -> None: ...
    def load(self, thread_id: str) -> GraphSnapshot | None: ...
    def history(self, thread_id: str) -> tuple[GraphSnapshot, ...]: ...
    def load_checkpoint(self, checkpoint_id: str) -> GraphSnapshot | None: ...


class GraphInterrupted(RuntimeError):
    def __init__(self, snapshot: GraphSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(f"graph interrupted at checkpoint {snapshot.checkpoint_id}")


class MemoryGraphCheckpointer(GraphCheckpointerPort):
    def __init__(self) -> None:
        self._snapshots: dict[str, list[GraphSnapshot]] = {}
        self._lock = RLock()

    def save(self, snapshot: GraphSnapshot) -> None:
        if type(snapshot) is not GraphSnapshot:
            raise TypeError("graph checkpointer accepts GraphSnapshot")
        with self._lock:
            history = self._snapshots.setdefault(snapshot.thread_id, [])
            if history and history[-1].checkpoint_id == snapshot.checkpoint_id:
                return
            history.append(snapshot)

    def load(self, thread_id: str) -> GraphSnapshot | None:
        history = self.history(thread_id)
        return history[-1] if history else None

    def history(self, thread_id: str) -> tuple[GraphSnapshot, ...]:
        if type(thread_id) is not str or not thread_id.strip():
            raise ValueError("graph checkpoint thread_id must be non-empty")
        with self._lock:
            return tuple(self._snapshots.get(thread_id, ()))

    def load_checkpoint(self, checkpoint_id: str) -> GraphSnapshot | None:
        require_sha256(checkpoint_id, "graph checkpoint_id")
        with self._lock:
            for history in self._snapshots.values():
                for snapshot in history:
                    if snapshot.checkpoint_id == checkpoint_id:
                        return snapshot
        return None


class SQLiteGraphCheckpointer(GraphCheckpointerPort):
    """Crash-durable graph checkpoint history with thread-local latest pointers."""

    durability = "crash_durable"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_threads (
                    thread_id TEXT PRIMARY KEY,
                    latest_checkpoint_id TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(
            str(self.path), timeout=30.0, isolation_level=None
        )
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _document(snapshot: GraphSnapshot) -> dict[str, JsonValue]:
        return {
            "thread_id": snapshot.thread_id,
            "step": snapshot.step,
            "values": snapshot.values,
            "next_nodes": snapshot.next_nodes,
            "parent_checkpoint_id": snapshot.parent_checkpoint_id,
            "interrupts": snapshot.interrupts,
        }

    @staticmethod
    def _decode(
        thread_id: str, checkpoint_id: str, raw: str
    ) -> GraphSnapshot:
        document = strict_json_loads(raw)
        if not isinstance(document, Mapping):
            raise ValueError("graph checkpoint document must be a mapping")
        values = document.get("values")
        next_nodes = document.get("next_nodes")
        interrupts = document.get("interrupts", ())
        if (
            not isinstance(values, Mapping)
            or not isinstance(next_nodes, (list, tuple))
            or not isinstance(interrupts, (list, tuple))
        ):
            raise ValueError("graph checkpoint document is malformed")
        snapshot = GraphSnapshot(
            thread_id=document.get("thread_id"),
            step=document.get("step"),
            values=values,
            next_nodes=tuple(next_nodes),
            parent_checkpoint_id=document.get("parent_checkpoint_id"),
            interrupts=tuple(interrupts),
        )
        if snapshot.thread_id != thread_id or snapshot.checkpoint_id != checkpoint_id:
            raise ValueError("graph checkpoint digest mismatch")
        return snapshot

    def save(self, snapshot: GraphSnapshot) -> None:
        if type(snapshot) is not GraphSnapshot:
            raise TypeError("graph checkpointer accepts GraphSnapshot")
        document = canonical_text(self._document(snapshot))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT snapshot_json FROM graph_checkpoints "
                    "WHERE checkpoint_id = ?",
                    (snapshot.checkpoint_id,),
                ).fetchone()
                if row is not None and row[0] != document:
                    raise ValueError("graph checkpoint identity collision")
                connection.execute(
                    "INSERT OR IGNORE INTO graph_checkpoints "
                    "(checkpoint_id, thread_id, snapshot_json) VALUES (?, ?, ?)",
                    (snapshot.checkpoint_id, snapshot.thread_id, document),
                )
                connection.execute(
                    "INSERT INTO graph_threads (thread_id, latest_checkpoint_id) "
                    "VALUES (?, ?) ON CONFLICT(thread_id) DO UPDATE SET "
                    "latest_checkpoint_id = excluded.latest_checkpoint_id",
                    (snapshot.thread_id, snapshot.checkpoint_id),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def load(self, thread_id: str) -> GraphSnapshot | None:
        history = self.history(thread_id)
        return history[-1] if history else None

    def history(self, thread_id: str) -> tuple[GraphSnapshot, ...]:
        if type(thread_id) is not str or not thread_id.strip():
            raise ValueError("graph checkpoint thread_id must be non-empty")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT checkpoint_id, snapshot_json FROM graph_checkpoints "
                "WHERE thread_id = ? ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return tuple(self._decode(thread_id, row[0], row[1]) for row in rows)

    def load_checkpoint(self, checkpoint_id: str) -> GraphSnapshot | None:
        require_sha256(checkpoint_id, "graph checkpoint_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT thread_id, snapshot_json FROM graph_checkpoints "
                "WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode(row[0], checkpoint_id, row[1])

    def close(self) -> None:
        """Retained as a lifecycle no-op; connections are scoped per operation."""
        return None


class StateGraph:
    """Compile-time validated graph builder with optional state reducers."""

    def __init__(self, *, reducers: Mapping[str, GraphReducer] | None = None) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, list[str]] = {}
        self._routers: dict[str, GraphRouter] = {}
        self._reducers = dict(reducers or {})
        self._entry: str | None = None

    def add_node(self, name: str, node: GraphNode) -> "StateGraph":
        if not isinstance(name, str) or not name.strip() or not callable(node):
            raise TypeError("graph node requires a non-empty name and callable")
        if name in self._nodes:
            raise ValueError(f"graph node already exists: {name}")
        self._nodes[name] = node
        self._edges.setdefault(name, [])
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self._entry = name
        return self

    def add_edge(self, source: str, target: str) -> "StateGraph":
        self._edges.setdefault(source, []).append(target)
        return self

    def add_conditional_edges(self, source: str, router: GraphRouter) -> "StateGraph":
        if source not in self._nodes or not callable(router):
            raise ValueError("conditional edge source must be a declared node")
        self._routers[source] = router
        return self

    def compile(self, *, checkpointer: GraphCheckpointerPort | None = None) -> "CompiledStateGraph":
        if self._entry not in self._nodes:
            raise ValueError("graph entry point must be a declared node")
        for source, targets in self._edges.items():
            if source not in self._nodes or any(target not in self._nodes for target in targets):
                raise ValueError("graph edges must reference declared nodes")
        return CompiledStateGraph(
            dict(self._nodes), {key: tuple(value) for key, value in self._edges.items()},
            dict(self._routers), dict(self._reducers), self._entry, checkpointer,
        )


class CompiledStateGraph:
    def __init__(
        self,
        nodes: dict[str, GraphNode],
        edges: dict[str, tuple[str, ...]],
        routers: dict[str, GraphRouter],
        reducers: dict[str, GraphReducer],
        entry: str,
        checkpointer: GraphCheckpointerPort | None,
    ) -> None:
        self._nodes, self._edges, self._routers = nodes, edges, routers
        self._reducers, self._entry, self._checkpointer = reducers, entry, checkpointer

    def invoke(
        self,
        initial: StateUpdate | None = None,
        *,
        thread_id: str = "default",
        max_steps: int = 100,
        resume: JsonValue | None = None,
        checkpoint_id: str | None = None,
    ) -> StateUpdate:
        final: StateUpdate | None = None
        for event in self.stream(
            initial,
            thread_id=thread_id,
            max_steps=max_steps,
            resume=resume,
            checkpoint_id=checkpoint_id,
        ):
            if event.event_type == "completed":
                final = event.payload
        if final is None:
            raise RuntimeError("graph ended without a completed state")
        return final

    def _merge(self, state: dict[str, JsonValue], update: StateUpdate) -> None:
        for key, value in update.items():
            frozen = freeze_json(value)
            reducer = self._reducers.get(key)
            state[key] = reducer(state.get(key), frozen) if reducer else frozen

    @staticmethod
    def _targets(value: object) -> tuple[GraphSend, ...] | tuple[str, ...]:
        if isinstance(value, GraphCommand):
            return () if value.goto is None else (value.goto,)
        if isinstance(value, str):
            return (value,)
        if isinstance(value, GraphSend):
            return (value,)
        if isinstance(value, tuple) and all(isinstance(row, GraphSend) for row in value):
            return value
        raise TypeError("graph route must return GraphCommand, node name, or GraphSend tuple")
    def history(self, thread_id: str) -> tuple[GraphSnapshot, ...]:
        if self._checkpointer is None:
            return ()
        return self._checkpointer.history(thread_id)

    def stream(
        self,
        initial: StateUpdate | None = None,
        *,
        thread_id: str = "default",
        max_steps: int = 100,
        resume: JsonValue | None = None,
        checkpoint_id: str | None = None,
    ) -> Iterator[GraphEvent]:
        if not thread_id.strip() or type(max_steps) is not int or max_steps <= 0:
            raise ValueError("graph thread_id and max_steps are invalid")
        if checkpoint_id is not None:
            require_sha256(checkpoint_id, "graph checkpoint_id")
            if self._checkpointer is None:
                raise RuntimeError("checkpoint_id requires a configured graph checkpointer")
            snapshot = self._checkpointer.load_checkpoint(checkpoint_id)
            if snapshot is None:
                raise KeyError(f"graph checkpoint not found: {checkpoint_id}")
            if snapshot.thread_id != thread_id:
                raise ValueError("graph checkpoint belongs to another thread")
        else:
            snapshot = None if self._checkpointer is None else self._checkpointer.load(thread_id)
        if initial is not None and checkpoint_id is not None:
            raise ValueError("graph initial state and checkpoint_id are mutually exclusive")
        if initial is None:
            if snapshot is None:
                raise ValueError("graph initial state is required for a new thread")
            state = dict(snapshot.values)
            next_nodes = snapshot.next_nodes
            parent = snapshot.checkpoint_id
            step = snapshot.step
            if snapshot.interrupts and resume is None:
                raise GraphInterrupted(snapshot)
            if resume is not None:
                state["__resume__"] = freeze_json(resume)
        else:
            state = dict(freeze_json(initial))
            next_nodes = (self._entry,)
            step = 0
            initial_snapshot = GraphSnapshot(thread_id, step, state, next_nodes)
            parent = initial_snapshot.checkpoint_id
            if self._checkpointer is not None:
                self._checkpointer.save(initial_snapshot)
        for _ in range(max_steps):
            if not next_nodes:
                yield GraphEvent("completed", thread_id, step, None, state)
                return
            node_name = next_nodes[0]
            result = self._nodes[node_name](dict(state))
            update = result.update if isinstance(result, GraphCommand) else {}
            if isinstance(result, Mapping):
                update = result
            self._merge(state, update)
            step += 1
            yield GraphEvent("node", thread_id, step, node_name, update)
            if isinstance(result, GraphCommand) and result.interrupt is not None:
                snapshot = GraphSnapshot(thread_id, step, state, (node_name,), parent, (result.interrupt,))
                if self._checkpointer is not None:
                    self._checkpointer.save(snapshot)
                yield GraphEvent("interrupt", thread_id, step, node_name, result.interrupt)
                raise GraphInterrupted(snapshot)
            route = result
            if node_name in self._routers:
                route = self._routers[node_name](dict(state))
            if isinstance(result, GraphCommand) and result.goto is not None:
                route = result
            targets = self._targets(route) if not isinstance(route, Mapping) else self._edges[node_name]
            if not targets:
                targets = self._edges[node_name]
            if targets and isinstance(targets[0], GraphSend):
                sends = targets
                next_nodes = tuple(row.target for row in sends)
                for send in sends:
                    self._merge(state, send.update)
            else:
                next_nodes = tuple(targets)
            snapshot = GraphSnapshot(thread_id, step, state, next_nodes, parent)
            parent = snapshot.checkpoint_id
            if self._checkpointer is not None:
                self._checkpointer.save(snapshot)
            yield GraphEvent("checkpoint", thread_id, step, node_name, snapshot.values)
        raise RuntimeError("graph exceeded max_steps")


__all__ = [
    "CompiledStateGraph", "GraphCheckpointerPort", "GraphCommand",
    "GraphEvent", "GraphInterrupted", "GraphSend", "GraphSnapshot",
    "MemoryGraphCheckpointer", "SQLiteGraphCheckpointer", "StateGraph",
]
