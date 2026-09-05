import pytest

from noetrium_platform.research.experimentation.study.api import (
    BenchmarkSourceKind,
    BenchmarkSourceSpec,
    BenchmarkTaskSet,
    InMemoryBenchmarkSource,
    TaskDefinition,
)


SHA = "a" * 64


def source() -> BenchmarkSourceSpec:
    return BenchmarkSourceSpec(
        "benchmark-source",
        BenchmarkSourceKind.CUSTOM,
        "revision-1",
        "adapter://benchmark",
        SHA,
        license="Apache-2.0",
        metadata={"owner": "research"},
    )


def task_set() -> BenchmarkTaskSet:
    return BenchmarkTaskSet(
        "benchmark", "revision-1", SHA, "task.v1",
        (TaskDefinition("task-1", "revision-1", "generic", "task.v1", SHA),),
    )


def test_external_source_resolves_an_immutable_cut() -> None:
    registry = InMemoryBenchmarkSource()
    resolved = registry.register(source(), task_set())

    assert resolved.cut_digest == task_set().cut_digest
    assert registry.resolve(source()) == resolved
    assert resolved.resolution_digest


def test_source_revision_or_content_drift_is_rejected() -> None:
    registry = InMemoryBenchmarkSource()
    with pytest.raises(ValueError, match="content digest"):
        registry.register(BenchmarkSourceSpec(
            "benchmark-source", BenchmarkSourceKind.CUSTOM, "revision-1",
            "adapter://benchmark", "b" * 64,
        ), task_set())
    registry.register(source(), task_set())
    with pytest.raises(KeyError, match="not registered"):
        registry.resolve(BenchmarkSourceSpec(
            "benchmark-source", BenchmarkSourceKind.CUSTOM, "revision-2",
            "adapter://benchmark", SHA,
        ))
