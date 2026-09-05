from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from noetrium_platform.foundation.kernel.kernel import (
    JsonInput,
    JsonObject,
    JsonValue,
    canonical_digest,
    freeze_json,
    thaw_json,
)


def _text(name: str, value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    suite_id: str
    case_id: str
    task_id: str
    scenario_id: str
    seed: int | None = None
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)
    expected_artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("suite_id", self.suite_id),
            ("case_id", self.case_id),
            ("task_id", self.task_id),
            ("scenario_id", self.scenario_id),
        ):
            _text(name, value)
        if self.seed is not None and type(self.seed) is not int:
            raise TypeError("benchmark seed must be an integer or None")
        if any(not ref.strip() for ref in self.expected_artifacts):
            raise ValueError("benchmark artifact refs must be non-empty")
        object.__setattr__(self, "parameters", freeze_json(self.parameters))

    def record(self) -> JsonObject:
        return {
            "suite_id": self.suite_id,
            "case_id": self.case_id,
            "task_id": self.task_id,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "parameters": thaw_json(self.parameters),
            "expected_artifacts": list(self.expected_artifacts),
        }

    @property
    def case_digest(self) -> str:
        return canonical_digest(self.record())


@dataclass(frozen=True, slots=True)
class BenchmarkSuite:
    suite_id: str
    revision: str
    benchmark_family: str
    cases: tuple[BenchmarkCase, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("suite_id", self.suite_id),
            ("revision", self.revision),
            ("benchmark_family", self.benchmark_family),
        ):
            _text(name, value)
        if not self.cases:
            raise ValueError("benchmark suite must contain at least one case")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark case ids must be unique")
        if any(case.suite_id != self.suite_id for case in self.cases):
            raise ValueError("benchmark case suite identity mismatch")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "suite_id": self.suite_id,
            "revision": self.revision,
            "benchmark_family": self.benchmark_family,
            "cases": [case.record() for case in self.cases],
            "metadata": thaw_json(self.metadata),
        }

    @property
    def suite_digest(self) -> str:
        return canonical_digest(self.record())


__all__ = ["BenchmarkCase", "BenchmarkSuite"]
