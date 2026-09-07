from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from noetrium.contracts.json import JsonValue, canonical_digest


_GRAPH_SCHEMA = "memory-graph.v1"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class MemoryNodeRecord:
    node_id: str
    kind: str
    label: str
    content: str
    generation: str
    active: bool = True
    evidence_ids: tuple[str, ...] = ()
    parent_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("node_id", "kind", "label", "content", "generation"):
            _text(getattr(self, name), name)
        if not isinstance(self.active, bool):
            raise TypeError("memory node active must be boolean")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_ids):
            raise ValueError("memory node evidence_ids must contain text")
        if any(not isinstance(value, str) or not value.strip() for value in self.parent_ids):
            raise ValueError("memory node parent_ids must contain text")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class MemoryEdgeRecord:
    source_id: str
    target_id: str
    relation: str
    active: bool = True

    def __post_init__(self) -> None:
        for name in ("source_id", "target_id", "relation"):
            _text(getattr(self, name), name)
        if self.source_id == self.target_id:
            raise ValueError("memory edge cannot self-reference")
        if not isinstance(self.active, bool):
            raise TypeError("memory edge active must be boolean")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class MemoryGraphSnapshot:
    generation: str
    nodes: tuple[MemoryNodeRecord, ...]
    edges: tuple[MemoryEdgeRecord, ...]
    schema_version: str = _GRAPH_SCHEMA

    def __post_init__(self) -> None:
        _text(self.generation, "generation")
        if self.schema_version != _GRAPH_SCHEMA:
            raise ValueError("unsupported memory graph schema")
        if tuple(sorted(node.node_id for node in self.nodes)) != tuple(node.node_id for node in self.nodes):
            raise ValueError("memory graph nodes must be canonically ordered")
        if tuple(sorted((edge.source_id, edge.target_id, edge.relation) for edge in self.edges)) != tuple(
            (edge.source_id, edge.target_id, edge.relation) for edge in self.edges
        ):
            raise ValueError("memory graph edges must be canonically ordered")
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("memory graph node ids must be unique")
        if any(
            parent_id not in node_ids
            for node in self.nodes
            for parent_id in node.parent_ids
        ):
            raise ValueError("memory graph node parent is missing")
        edge_keys = {(edge.source_id, edge.target_id, edge.relation) for edge in self.edges}
        if len(edge_keys) != len(self.edges):
            raise ValueError("memory graph edges must be unique")
        if any(edge.source_id not in node_ids or edge.target_id not in node_ids for edge in self.edges):
            raise ValueError("memory graph edge endpoint is missing")

    def digest(self) -> str:
        return canonical_digest(self)

    def node(self, node_id: str) -> MemoryNodeRecord | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)


@dataclass(frozen=True, slots=True)
class MemoryGraphOperation:
    operation: str
    target_id: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.operation, "operation")
        _text(self.target_id, "target_id")
        if self.operation not in {"create_node", "update_node", "retire_node", "create_edge", "retire_edge"}:
            raise ValueError("unsupported memory graph operation")
        if not isinstance(self.payload, Mapping):
            raise TypeError("memory graph operation payload must be a mapping")


@dataclass(frozen=True, slots=True)
class MemoryGraphTransaction:
    base_digest: str
    snapshot: MemoryGraphSnapshot
    evidence_ids: tuple[str, ...]
    rationale_digest: str
    operation_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.base_digest, "base_digest")
        _text(self.rationale_digest, "rationale_digest")
        if any(not isinstance(value, str) or not value.strip() for value in self.evidence_ids):
            raise ValueError("transaction evidence_ids must contain text")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class MemoryGraphLedgerEntry:
    event_id: str
    base_digest: str
    result_digest: str
    generation: str
    operation_digests: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    rationale_digest: str

    def digest(self) -> str:
        return canonical_digest(self)


class MemoryGraphPort(Protocol):
    def snapshot(self) -> MemoryGraphSnapshot: ...

    def stage(
        self,
        operations: tuple[MemoryGraphOperation, ...],
        *,
        evidence_ids: tuple[str, ...] = (),
        rationale_digest: str,
    ) -> MemoryGraphTransaction: ...

    def activate(self, transaction: MemoryGraphTransaction) -> MemoryGraphLedgerEntry: ...

    def restore(self, snapshot: MemoryGraphSnapshot) -> None: ...


__all__ = [
    "MemoryEdgeRecord",
    "MemoryGraphLedgerEntry",
    "MemoryGraphOperation",
    "MemoryGraphPort",
    "MemoryGraphSnapshot",
    "MemoryGraphTransaction",
    "MemoryNodeRecord",
]