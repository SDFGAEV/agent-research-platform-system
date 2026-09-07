from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from noetrium.contracts.json import canonical_digest

from .memory_graph import (
    MemoryEdgeRecord,
    MemoryGraphLedgerEntry,
    MemoryGraphOperation,
    MemoryGraphSnapshot,
    MemoryGraphTransaction,
    MemoryNodeRecord,
)


class MemoryGraphConflict(RuntimeError):
    pass


class MemoryGraphIntegrityError(ValueError):
    pass


def _generation_number(generation: str) -> int:
    if not generation.startswith("g"):
        raise MemoryGraphIntegrityError("memory graph generation must start with g")
    try:
        return int(generation[1:])
    except ValueError as exc:
        raise MemoryGraphIntegrityError("memory graph generation is not numeric") from exc


@dataclass
class VersionedMemoryGraph:
    """Generic immutable-snapshot memory graph with atomic activation.

    It owns storage, ordering, cycle checks, and optimistic concurrency. It does
    not decide why a graph should change; downstream methods supply operations.
    """

    _snapshot: MemoryGraphSnapshot | None = None

    def __post_init__(self) -> None:
        if self._snapshot is None:
            self._snapshot = MemoryGraphSnapshot("g0", (), ())
        self._ledger: list[MemoryGraphLedgerEntry] = []

    def snapshot(self) -> MemoryGraphSnapshot:
        assert self._snapshot is not None
        return self._snapshot

    def ledger(self) -> tuple[MemoryGraphLedgerEntry, ...]:
        return tuple(self._ledger)

    def stage(
        self,
        operations: tuple[MemoryGraphOperation, ...],
        *,
        evidence_ids: tuple[str, ...] = (),
        rationale_digest: str,
    ) -> MemoryGraphTransaction:
        base = self.snapshot()
        if not operations:
            raise MemoryGraphIntegrityError("cannot stage an empty memory graph edit")
        if len(operations) > 8:
            raise MemoryGraphIntegrityError("memory graph edit exceeds bounded operation count")
        nodes = {node.node_id: node for node in base.nodes}
        edges = {(edge.source_id, edge.target_id, edge.relation): edge for edge in base.edges}
        operation_digests: list[str] = []
        for operation in operations:
            operation_digests.append(canonical_digest(operation))
            if operation.operation == "create_node":
                if operation.target_id in nodes:
                    raise MemoryGraphIntegrityError("cannot create an existing memory node")
                payload = operation.payload
                nodes[operation.target_id] = MemoryNodeRecord(
                    operation.target_id,
                    str(payload.get("kind", "semantic")),
                    str(payload.get("label", operation.target_id)),
                    str(payload.get("content", "")),
                    f"g{_generation_number(base.generation) + 1}",
                    bool(payload.get("active", True)),
                    tuple(str(value) for value in payload.get("evidence_ids", ())),
                    tuple(str(value) for value in payload.get("parent_ids", ())),
                )
            elif operation.operation == "update_node":
                current = nodes.get(operation.target_id)
                if current is None:
                    raise MemoryGraphIntegrityError("cannot update a missing memory node")
                payload = operation.payload
                nodes[operation.target_id] = MemoryNodeRecord(
                    current.node_id,
                    str(payload.get("kind", current.kind)),
                    str(payload.get("label", current.label)),
                    str(payload.get("content", current.content)),
                    f"g{_generation_number(base.generation) + 1}",
                    bool(payload.get("active", current.active)),
                    tuple(str(value) for value in payload.get("evidence_ids", current.evidence_ids)),
                    tuple(str(value) for value in payload.get("parent_ids", current.parent_ids)),
                )
            elif operation.operation == "retire_node":
                current = nodes.get(operation.target_id)
                if current is None:
                    raise MemoryGraphIntegrityError("cannot retire a missing memory node")
                nodes[operation.target_id] = MemoryNodeRecord(
                    current.node_id, current.kind, current.label, current.content,
                    f"g{_generation_number(base.generation) + 1}", False,
                    current.evidence_ids, current.parent_ids,
                )
            elif operation.operation == "create_edge":
                source_id = str(operation.payload.get("source_id", ""))
                target_id = str(operation.payload.get("target_id", ""))
                relation = str(operation.payload.get("relation", "supports"))
                key = (source_id, target_id, relation)
                if source_id not in nodes or target_id not in nodes:
                    raise MemoryGraphIntegrityError("cannot create an edge with missing endpoint")
                if key in edges:
                    raise MemoryGraphIntegrityError("cannot create an existing memory edge")
                edges[key] = MemoryEdgeRecord(source_id, target_id, relation, True)
            elif operation.operation == "retire_edge":
                source_id = str(operation.payload.get("source_id", ""))
                target_id = str(operation.payload.get("target_id", ""))
                relation = str(operation.payload.get("relation", "supports"))
                key = (source_id, target_id, relation)
                current = edges.get(key)
                if current is None:
                    raise MemoryGraphIntegrityError("cannot retire a missing memory edge")
                edges[key] = MemoryEdgeRecord(source_id, target_id, relation, False)
        active_edges = tuple(edge for edge in edges.values() if edge.active)
        self._validate_dag(nodes, active_edges)
        result = MemoryGraphSnapshot(
            f"g{_generation_number(base.generation) + 1}",
            tuple(sorted(nodes.values(), key=lambda node: node.node_id)),
            tuple(sorted(edges.values(), key=lambda edge: (edge.source_id, edge.target_id, edge.relation))),
        )
        if result.digest() == base.digest():
            raise MemoryGraphIntegrityError("memory graph edit is a no-op")
        return MemoryGraphTransaction(
            base.digest(), result, tuple(sorted(set(evidence_ids))), rationale_digest,
            tuple(operation_digests),
        )

    @staticmethod
    def _validate_dag(
        nodes: Mapping[str, MemoryNodeRecord],
        edges: tuple[MemoryEdgeRecord, ...],
    ) -> None:
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in edges:
            if edge.source_id not in nodes or edge.target_id not in nodes:
                raise MemoryGraphIntegrityError("memory graph has a dangling edge")
            if not nodes[edge.source_id].active or not nodes[edge.target_id].active:
                raise MemoryGraphIntegrityError("active edge must connect active nodes")
            adjacency[edge.source_id].append(edge.target_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise MemoryGraphIntegrityError("memory graph must remain acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for child in adjacency[node_id]:
                visit(child)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in adjacency:
            visit(node_id)

    def activate(self, transaction: MemoryGraphTransaction) -> MemoryGraphLedgerEntry:
        if transaction.base_digest != self.snapshot().digest():
            raise MemoryGraphConflict("memory graph changed after proposal staging")
        self._snapshot = transaction.snapshot
        entry = MemoryGraphLedgerEntry(
            event_id=canonical_digest(transaction)[:32],
            base_digest=transaction.base_digest,
            result_digest=transaction.snapshot.digest(),
            generation=transaction.snapshot.generation,
            operation_digests=transaction.operation_digests,
            evidence_ids=transaction.evidence_ids,
            rationale_digest=transaction.rationale_digest,
        )
        self._ledger.append(entry)
        return entry

    def restore(self, snapshot: MemoryGraphSnapshot) -> None:
        self._validate_dag(
            {node.node_id: node for node in snapshot.nodes},
            tuple(edge for edge in snapshot.edges if edge.active),
        )
        self._snapshot = snapshot
        self._ledger.clear()

    def diagnostics(self) -> Mapping[str, object]:
        snapshot = self.snapshot()
        return {
            "generation": snapshot.generation,
            "graph_digest": snapshot.digest(),
            "node_count": len(snapshot.nodes),
            "active_node_count": sum(node.active for node in snapshot.nodes),
            "edge_count": len(snapshot.edges),
            "active_edge_count": sum(edge.active for edge in snapshot.edges),
            "ledger_count": len(self._ledger),
        }


__all__ = [
    "MemoryGraphConflict",
    "MemoryGraphIntegrityError",
    "VersionedMemoryGraph",
]