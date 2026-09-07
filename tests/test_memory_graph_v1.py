from __future__ import annotations

import pytest

from components.reference.single_agent.memory import (
    MemoryGraphConflict,
    MemoryGraphIntegrityError,
    MemoryGraphOperation,
    MemoryNodeRecord,
    VersionedMemoryGraph,
)


def test_memory_graph_stages_and_activates_atomically() -> None:
    graph = VersionedMemoryGraph()
    transaction = graph.stage(
        (
            MemoryGraphOperation(
                "create_node",
                "node:a",
                {
                    "kind": "semantic",
                    "label": "a",
                    "content": "node a",
                },
            ),
        ),
        evidence_ids=("e1",),
        rationale_digest="rationale",
    )
    entry = graph.activate(transaction)
    assert entry.result_digest == graph.snapshot().digest()
    assert graph.snapshot().node("node:a") is not None
    assert graph.diagnostics()["ledger_count"] == 1


def test_memory_graph_rejects_stale_activation() -> None:
    graph = VersionedMemoryGraph()
    first = graph.stage(
        (
            MemoryGraphOperation(
                "create_node",
                "node:a",
                {"kind": "semantic", "label": "a", "content": "a"},
            ),
        ),
        rationale_digest="one",
    )
    second = graph.stage(
        (
            MemoryGraphOperation(
                "create_node",
                "node:b",
                {"kind": "semantic", "label": "b", "content": "b"},
            ),
        ),
        rationale_digest="two",
    )
    graph.activate(first)
    with pytest.raises(MemoryGraphConflict):
        graph.activate(second)


def test_memory_graph_rejects_cycles() -> None:
    graph = VersionedMemoryGraph(
        MemoryGraphSnapshotForTest.snapshot()
    )
    with pytest.raises(MemoryGraphIntegrityError):
        graph.stage(
            (
                MemoryGraphOperation(
                    "create_edge",
                    "edge:cycle",
                    {
                        "source_id": "node:b",
                        "target_id": "node:a",
                        "relation": "cycle",
                    },
                ),
            ),
            rationale_digest="cycle",
        )


def test_memory_graph_restore_revalidates_snapshot() -> None:
    graph = VersionedMemoryGraph()
    tx = graph.stage(
        (
            MemoryGraphOperation(
                "create_node",
                "node:a",
                {"kind": "semantic", "label": "a", "content": "a"},
            ),
        ),
        rationale_digest="restore",
    )
    graph.activate(tx)
    restored = VersionedMemoryGraph()
    restored.restore(graph.snapshot())
    assert restored.snapshot().digest() == graph.snapshot().digest()


class MemoryGraphSnapshotForTest:
    @staticmethod
    def snapshot():
        from components.reference.single_agent.memory import (
            MemoryEdgeRecord,
            MemoryGraphSnapshot,
        )

        return MemoryGraphSnapshot(
            "g0",
            (
                MemoryNodeRecord("node:a", "semantic", "a", "a", "g0"),
                MemoryNodeRecord("node:b", "semantic", "b", "b", "g0"),
            ),
            (MemoryEdgeRecord("node:a", "node:b", "forward"),),
        )
