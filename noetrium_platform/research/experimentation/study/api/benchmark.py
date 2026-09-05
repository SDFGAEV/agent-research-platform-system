"""Paper-general Benchmark/TaskSet identities; environment fixtures remain provider-owned."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Protocol

from noetrium_platform.evidence.artifact.catalog.api import ArtifactRegistryPort
from noetrium_platform.evidence.artifact.reference.api import ArtifactReference, ArtifactReferencePort
from noetrium_platform.foundation.kernel.kernel import canonical_digest, freeze_json, require_sha256

_HEX = frozenset("0123456789abcdef")


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _sha(value: object, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(ch not in _HEX for ch in text):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return text


def _ids(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if type(value) is not tuple or (not allow_empty and not value):
        raise TypeError(f"{field} must be a {'possibly empty ' if allow_empty else 'non-empty '}tuple")
    if any(type(item) is not str or not item.strip() for item in value):
        raise TypeError(f"{field} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must be unique")
    return value


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    task_id: str
    revision_id: str
    family: str
    schema_id: str
    content_digest: str
    content_reference: ArtifactReference | None = None
    lineage_refs: tuple[str, ...] = ()
    task_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (("task_id", self.task_id), ("revision_id", self.revision_id), ("family", self.family), ("schema_id", self.schema_id)):
            _text(value, f"task definition {name}")
        _sha(self.content_digest, "task definition content_digest")
        if self.content_reference is not None and type(self.content_reference) is not ArtifactReference:
            raise TypeError("task definition content_reference must be ArtifactReference or None")
        _ids(self.lineage_refs, "task definition lineage_refs", allow_empty=True)
        object.__setattr__(self, "task_digest", canonical_digest({"task_id": self.task_id, "revision_id": self.revision_id, "family": self.family, "schema_id": self.schema_id, "content_digest": self.content_digest, "content_reference": self.content_reference, "lineage_refs": self.lineage_refs}))

    def verify_content(self, references: ArtifactReferencePort, artifacts: ArtifactRegistryPort) -> None:
        if self.content_reference is None:
            return
        resolved = references.resolve(self.content_reference.reference_id, self.content_reference.scope)
        if resolved != self.content_reference:
            raise ValueError("task content reference generation drift")
        record = artifacts.get(self.content_reference.artifact_id)
        if record.scope != self.content_reference.scope or record.digest != self.content_digest:
            raise ValueError("task content reference does not match immutable content")


class TaskGraphRelation(StrEnum):
    PREREQUISITE = "prerequisite"
    RETRY_OF = "retry_of"
    LINEAGE = "lineage"


@dataclass(frozen=True, slots=True, order=True)
class TaskGraphEdge:
    source_task_id: str
    target_task_id: str
    relation: TaskGraphRelation

    def __post_init__(self) -> None:
        _text(self.source_task_id, "task graph source_task_id")
        _text(self.target_task_id, "task graph target_task_id")
        if self.source_task_id == self.target_task_id:
            raise ValueError("task graph edge cannot self-reference")
        if not isinstance(self.relation, TaskGraphRelation):
            raise TypeError("task graph relation must be TaskGraphRelation")


@dataclass(frozen=True, slots=True)
class TaskGraph:
    edges: tuple[TaskGraphEdge, ...] = ()
    graph_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.edges) is not tuple or any(type(row) is not TaskGraphEdge for row in self.edges):
            raise TypeError("task graph edges must be a tuple of TaskGraphEdge")
        previous = None
        for row in self.edges:
            if previous is not None and row <= previous:
                raise ValueError("task graph edges must be canonically ordered")
            previous = row
        if len(self.edges) != len(set(self.edges)):
            raise ValueError("task graph edges must be unique")
        object.__setattr__(self, "graph_digest", canonical_digest(self.edges))


@dataclass(frozen=True, slots=True)
class TrialBudget:
    budget_id: str
    max_steps: int | None = None
    max_seconds: float | None = None
    max_tokens: int | None = None
    resource_budget_digest: str | None = None
    budget_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.budget_id, "trial budget budget_id")
        for name, value in (("max_steps", self.max_steps), ("max_tokens", self.max_tokens)):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"trial budget {name} must be a positive integer or None")
        if self.max_seconds is not None:
            if isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float)) or not math.isfinite(float(self.max_seconds)) or self.max_seconds <= 0:
                raise ValueError("trial budget max_seconds must be finite and positive or None")
        if self.resource_budget_digest is not None:
            _sha(self.resource_budget_digest, "trial budget resource_budget_digest")
        object.__setattr__(self, "budget_digest", canonical_digest({"budget_id": self.budget_id, "max_steps": self.max_steps, "max_seconds": self.max_seconds, "max_tokens": self.max_tokens, "resource_budget_digest": self.resource_budget_digest}))


@dataclass(frozen=True, slots=True, order=True)
class TaskSetSplit:
    split_id: str
    task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.split_id, "task-set split split_id")
        _ids(self.task_ids, "task-set split task_ids")


@dataclass(frozen=True, slots=True)
class BenchmarkTaskSet:
    benchmark_id: str
    revision_id: str
    source_digest: str
    task_schema_id: str
    tasks: tuple[TaskDefinition, ...]
    source_reference: ArtifactReference | None = None
    task_graph: TaskGraph = field(default_factory=TaskGraph)
    splits: tuple[TaskSetSplit, ...] = ()
    selection_policy_digest: str = ""
    cut_digest: str = field(init=False)

    def __post_init__(self) -> None:
        """Algorithm-Complexity: O(N)
        Algorithm-Rationale: N is tasks plus graph edges plus split memberships; each declared element is validated a constant number of times.
        """
        for name, value in (("benchmark_id", self.benchmark_id), ("revision_id", self.revision_id), ("task_schema_id", self.task_schema_id)):
            _text(value, f"benchmark task set {name}")
        _sha(self.source_digest, "benchmark task set source_digest")
        if self.selection_policy_digest:
            _sha(self.selection_policy_digest, "benchmark task set selection_policy_digest")
        if self.source_reference is not None and type(self.source_reference) is not ArtifactReference:
            raise TypeError("benchmark source_reference must be ArtifactReference or None")
        if type(self.tasks) is not tuple or not self.tasks or any(type(row) is not TaskDefinition for row in self.tasks):
            raise TypeError("benchmark task set tasks must be a non-empty tuple of TaskDefinition")
        previous_task_id: str | None = None
        for row in self.tasks:
            if previous_task_id is not None and row.task_id <= previous_task_id:
                raise ValueError("benchmark tasks must be canonically ordered by task_id")
            previous_task_id = row.task_id
        task_ids = tuple(row.task_id for row in self.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("benchmark task ids must be unique")
        if type(self.task_graph) is not TaskGraph:
            raise TypeError("benchmark task_graph must be TaskGraph")
        known = set(task_ids)
        if any(edge.source_task_id not in known or edge.target_task_id not in known for edge in self.task_graph.edges):
            raise ValueError("benchmark task graph references an unknown task")
        if type(self.splits) is not tuple or any(type(row) is not TaskSetSplit for row in self.splits):
            raise TypeError("benchmark splits must be a tuple of TaskSetSplit")
        previous_split_id: str | None = None
        for row in self.splits:
            if previous_split_id is not None and row.split_id <= previous_split_id:
                raise ValueError("benchmark splits must be canonically ordered")
            previous_split_id = row.split_id
        split_ids = tuple(row.split_id for row in self.splits)
        if len(split_ids) != len(set(split_ids)):
            raise ValueError("benchmark split ids must be unique")
        if any(task_id not in known for row in self.splits for task_id in row.task_ids):
            raise ValueError("benchmark split references an unknown task")
        object.__setattr__(self, "cut_digest", canonical_digest({
            "benchmark_id": self.benchmark_id,
            "revision_id": self.revision_id,
            "source_digest": self.source_digest,
            "task_schema_id": self.task_schema_id,
            "tasks": tuple(row.task_digest for row in self.tasks),
            "source_reference": self.source_reference,
            "task_graph_digest": self.task_graph.graph_digest,
            "splits": self.splits,
            "selection_policy_digest": self.selection_policy_digest,
        }))

    def verify_source(self, references: ArtifactReferencePort, artifacts: ArtifactRegistryPort) -> None:
        if self.source_reference is None:
            return
        resolved = references.resolve(self.source_reference.reference_id, self.source_reference.scope)
        if resolved != self.source_reference:
            raise ValueError("benchmark source reference generation drift")
        record = artifacts.get(self.source_reference.artifact_id)
        if record.scope != self.source_reference.scope or record.digest != self.source_digest:
            raise ValueError("benchmark source reference does not match immutable content")

    def selected_tasks(self, split_id: str | None = None) -> tuple[TaskDefinition, ...]:
        if split_id is None:
            return self.tasks
        _text(split_id, "benchmark selected split_id")
        matches = tuple(row for row in self.splits if row.split_id == split_id)
        if len(matches) != 1:
            raise KeyError(f"benchmark has no unique split {split_id!r}")
        by_id = {row.task_id: row for row in self.tasks}
        return tuple(by_id[task_id] for task_id in matches[0].task_ids)


__all__ = [
    "BenchmarkTaskSet", "TaskDefinition", "TaskGraph", "TaskGraphEdge",
    "TaskGraphRelation", "TaskSetSplit", "TrialBudget", "BenchmarkSourceKind",
    "BenchmarkSourceSpec", "BenchmarkSourceResolution", "BenchmarkSourcePort",
    "InMemoryBenchmarkSource",
]
class BenchmarkSourceKind(StrEnum):
    LOCAL_FILE = "local_file"
    HTTP = "http"
    GIT = "git"
    HUGGINGFACE = "huggingface"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class BenchmarkSourceSpec:
    source_id: str
    kind: BenchmarkSourceKind
    revision_id: str
    locator: str
    content_digest: str
    license: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    source_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (("source_id", self.source_id), ("revision_id", self.revision_id),
                            ("locator", self.locator)):
            if type(value) is not str or not value.strip():
                raise ValueError(f"benchmark source {name} must be non-empty")
        if not isinstance(self.kind, BenchmarkSourceKind):
            raise TypeError("benchmark source kind must be BenchmarkSourceKind")
        require_sha256(self.content_digest, "benchmark source content_digest")
        if self.license is not None and (type(self.license) is not str or not self.license.strip()):
            raise ValueError("benchmark source license must be non-empty when provided")
        frozen = freeze_json(self.metadata)
        if not isinstance(frozen, Mapping) or any(type(k) is not str or type(v) is not str for k, v in frozen.items()):
            raise TypeError("benchmark source metadata must be a string mapping")
        object.__setattr__(self, "metadata", frozen)
        object.__setattr__(self, "source_digest", canonical_digest({
            "source_id": self.source_id, "kind": self.kind.value,
            "revision_id": self.revision_id, "locator": self.locator,
            "content_digest": self.content_digest, "license": self.license,
            "metadata": self.metadata,
        }))


@dataclass(frozen=True, slots=True)
class BenchmarkSourceResolution:
    source: BenchmarkSourceSpec
    task_set: BenchmarkTaskSet
    resolution_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.source) is not BenchmarkSourceSpec:
            raise TypeError("benchmark resolution source must be BenchmarkSourceSpec")
        if type(self.task_set) is not BenchmarkTaskSet:
            raise TypeError("benchmark resolution task_set must be BenchmarkTaskSet")
        if self.task_set.source_digest != self.source.content_digest:
            raise ValueError("resolved benchmark content digest does not match source")
        if self.task_set.revision_id != self.source.revision_id:
            raise ValueError("resolved benchmark revision does not match source")
        object.__setattr__(self, "resolution_digest", canonical_digest({
            "source": self.source.source_digest, "task_set": self.task_set.cut_digest,
        }))

    @property
    def cut_digest(self) -> str:
        return self.task_set.cut_digest


class BenchmarkSourcePort(Protocol):
    def resolve(self, source: BenchmarkSourceSpec) -> BenchmarkSourceResolution:
        ...


class InMemoryBenchmarkSource(BenchmarkSourcePort):
    """Deterministic reference adapter for tests and local composition."""

    def __init__(self) -> None:
        self._cuts: dict[str, BenchmarkSourceResolution] = {}

    def register(self, source: BenchmarkSourceSpec, task_set: BenchmarkTaskSet) -> BenchmarkSourceResolution:
        resolution = BenchmarkSourceResolution(source, task_set)
        key = f"{source.source_id}@{source.revision_id}"
        if key in self._cuts and self._cuts[key] != resolution:
            raise ValueError(f"benchmark source revision already registered: {key}")
        self._cuts[key] = resolution
        return resolution

    def resolve(self, source: BenchmarkSourceSpec) -> BenchmarkSourceResolution:
        key = f"{source.source_id}@{source.revision_id}"
        try:
            return self._cuts[key]
        except KeyError as exc:
            raise KeyError(f"benchmark source revision is not registered: {key}") from exc
