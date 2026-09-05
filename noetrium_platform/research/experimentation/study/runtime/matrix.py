from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import TypeVar
from uuid import uuid4

from noetrium_platform.foundation.kernel.concurrency.api import (
    Deadline,
    ExecutionLaneKind,
    ExecutionSpec,
    TaskFailureScope,
    TaskGroupPort,
)

from ..api import (
    BoundStudyUnitExecutionPort,
    ExperimentPlan,
    StudyAssignment,
    StudyExecutionUnit,
    StudyMatrixExecutionReport,
    StudyMetricAggregationPort,
    StudyMetricObservation,
    StudyProtocol,
    StudyUnitExecutionPort,
    VariantBinding,
)
from .protocol import DeterministicStudyAssignment


def _study_units(
    protocol: StudyProtocol,
    assignments: tuple[StudyAssignment, ...],
) -> tuple[StudyExecutionUnit, ...]:
    grouped: dict[int, list[StudyAssignment]] = defaultdict(list)
    for assignment in assignments:
        grouped[assignment.repetition].append(assignment)
    return tuple(
        StudyExecutionUnit(
            protocol.study_id,
            repetition,
            tuple(sorted(grouped[repetition], key=lambda item: (item.variant_id, item.seed))),
        )
        for repetition in sorted(grouped)
    )


def _binding_index(plan: ExperimentPlan) -> dict[str, VariantBinding]:
    return {item.variant.variant_id: item for item in plan.bindings}


_T = TypeVar("_T")
_R = TypeVar("_R")


def _pending_variant_assignments(
    units: tuple[StudyExecutionUnit, ...],
    offsets: dict[str, int],
    variant_limit: int,
) -> tuple[tuple[StudyExecutionUnit, StudyAssignment], ...]:
    pending: list[tuple[StudyExecutionUnit, StudyAssignment]] = []
    for unit in units:
        offset = offsets[unit.unit_digest]
        for assignment in unit.assignments[offset : offset + variant_limit]:
            pending.append((unit, assignment))
    return tuple(pending)


def _collect_variant_results(
    pending: tuple[tuple[StudyExecutionUnit, StudyAssignment], ...],
    results: tuple[StudyMetricObservation, ...],
    collected: dict[str, list[StudyMetricObservation]],
    offsets: dict[str, int],
    variant_limit: int,
) -> None:
    for (unit, _assignment), observation in zip(pending, results, strict=True):
        collected[unit.unit_digest].append(observation)
    for unit in {item[0] for item in pending}:
        offsets[unit.unit_digest] += variant_limit


class StudyMatrixExecutor:
    """Run every declared assignment through one injected environment adapter.

    Matrix completeness, repetition grouping and aggregate invocation are
    platform responsibilities. The adapter owns environment and branch
    mechanics only.
    """

    def __init__(
        self,
        aggregation: StudyMetricAggregationPort,
        assignment_expander: DeterministicStudyAssignment | None = None,
        task_group: TaskGroupPort | None = None,
    ) -> None:
        self._aggregation = aggregation
        self._assignment_expander = assignment_expander or DeterministicStudyAssignment()
        self._task_group = task_group

    def _execute_bounded(
        self,
        items: tuple[_T, ...],
        execute_one: Callable[[_T], _R],
        *,
        parallelism: int,
        timeout_seconds: float,
        task_id_prefix: str,
        failure_message: str,
    ) -> tuple[_R, ...]:
        """Run a bounded batch and merge results in submission order."""
        if not items:
            return ()
        effective_parallelism = min(parallelism, len(items))
        if effective_parallelism == 1:
            return tuple(execute_one(item) for item in items)
        if self._task_group is None:
            raise RuntimeError(
                "parallel study execution requires an injected structured task group"
            )

        invocation_id = uuid4().hex
        iterator = iter(items)
        results: list[_R] = []
        errors: list[BaseException] = []
        index = 0
        while True:
            handles = []
            while len(handles) < effective_parallelism:
                try:
                    item = next(iterator)
                except StopIteration:
                    break

                def run(_context, owned_item=item):
                    return execute_one(owned_item)

                handle = self._task_group.submit(
                    ExecutionSpec(
                        task_id=f"{task_id_prefix}:{invocation_id}:{index}",
                        lane_kind=ExecutionLaneKind.BLOCKING_IO,
                        failure_scope=TaskFailureScope.CALLER,
                    ),
                    run,
                    deadline=Deadline.after(timeout_seconds),
                )
                handles.append(handle)
                index += 1
            if not handles:
                break
            for handle in handles:
                try:
                    results.append(handle.result(timeout=timeout_seconds))
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise ExceptionGroup(failure_message, errors)
        return tuple(results)

    def _execute_repetitions(
        self,
        protocol: StudyProtocol,
        units: tuple[StudyExecutionUnit, ...],
        execute_one: Callable[[StudyExecutionUnit], tuple[StudyMetricObservation, ...]],
    ) -> tuple[tuple[StudyExecutionUnit, tuple[StudyMetricObservation, ...]], ...]:
        """Execute repetition groups with bounded, deterministic fanout."""
        if protocol.concurrency_policy.max_parallel_repetitions == 1:
            results = self._execute_bounded(
                units,
                execute_one,
                parallelism=1,
                timeout_seconds=protocol.concurrency_policy.repetition_timeout_seconds,
                task_id_prefix=f"study-repetition:{protocol.study_id}",
                failure_message=f"parallel study repetition batch failed: study={protocol.study_id}",
            )
            return tuple(
                (unit, observations)
                for unit, observations in zip(units, results, strict=True)
            )
        return self._execute_repetition_batches(protocol, units, execute_one)

    def _execute_repetition_batches(
        self,
        protocol: StudyProtocol,
        units: tuple[StudyExecutionUnit, ...],
        execute_one: Callable[[StudyExecutionUnit], tuple[StudyMetricObservation, ...]],
    ) -> tuple[tuple[StudyExecutionUnit, tuple[StudyMetricObservation, ...]], ...]:
        completed: list[tuple[StudyExecutionUnit, tuple[StudyMetricObservation, ...]]] = []
        parallelism = protocol.concurrency_policy.max_parallel_repetitions
        for start in range(0, len(units), parallelism):
            batch = units[start : start + parallelism]
            results = self._execute_bounded(
                batch,
                execute_one,
                parallelism=parallelism,
                timeout_seconds=protocol.concurrency_policy.repetition_timeout_seconds,
                task_id_prefix=f"study-repetition:{protocol.study_id}",
                failure_message=f"parallel study repetition batch failed: study={protocol.study_id}",
            )
            completed.extend(zip(batch, results, strict=True))
        return tuple(completed)

    def _execute_variant_units(
        self,
        protocol: StudyProtocol,
        units: tuple[StudyExecutionUnit, ...],
        execute_variant: Callable[[StudyAssignment], StudyMetricObservation],
    ) -> tuple[tuple[StudyExecutionUnit, tuple[StudyMetricObservation, ...]], ...]:
        """Fan out variants without nesting task groups or risking worker deadlock.

        Repetition groups are scheduled in bounded outer batches. Each round
        submits at most ``max_parallel_variants`` assignments per active group,
        so both scientific concurrency limits remain explicit and composable.
        """
        repetition_limit = protocol.concurrency_policy.max_parallel_repetitions
        variant_limit = protocol.concurrency_policy.max_parallel_variants
        completed: list[tuple[StudyExecutionUnit, tuple[StudyMetricObservation, ...]]] = []
        for start in range(0, len(units), repetition_limit):
            active_units = units[start : start + repetition_limit]
            offsets = {unit.unit_digest: 0 for unit in active_units}
            collected: dict[str, list[StudyMetricObservation]] = {
                unit.unit_digest: [] for unit in active_units
            }
            while True:
                pending = _pending_variant_assignments(
                    active_units,
                    offsets,
                    variant_limit,
                )
                if not pending:
                    break
                results = self._execute_bounded(
                    pending,
                    lambda item: execute_variant(item[1]),
                    parallelism=len(pending),
                    timeout_seconds=protocol.concurrency_policy.repetition_timeout_seconds,
                    task_id_prefix=f"study-variant:{protocol.study_id}",
                    failure_message=f"parallel study variant batch failed: study={protocol.study_id}",
                )
                _collect_variant_results(
                    pending,
                    results,
                    collected,
                    offsets,
                    variant_limit,
                )
            completed.extend(
                (unit, tuple(collected[unit.unit_digest])) for unit in active_units
            )
        return tuple(completed)

    def execute(
        self,
        protocol: StudyProtocol,
        assignments: tuple[StudyAssignment, ...],
        adapter: StudyUnitExecutionPort,
    ) -> StudyMatrixExecutionReport:
        expected = self._assignment_expander.assignments(protocol)
        self._require_exact_assignments(expected, assignments)
        units = _study_units(protocol, assignments)
        if protocol.concurrency_policy.parallel_variants:
            execute_variant = getattr(adapter, "execute_variant", None)
            if not callable(execute_variant):
                raise TypeError(
                    "parallel study variants require an adapter implementing execute_variant"
                )
            unit_results = self._execute_variant_units(protocol, units, execute_variant)
        else:
            unit_results = self._execute_repetitions(
                protocol, units, lambda owned: tuple(adapter.execute(owned))
            )
        observations: list[StudyMetricObservation] = []
        for unit, unit_observations in unit_results:
            self._require_exact_observations(unit, unit_observations, unit.repetition)
            observations.extend(sorted(unit_observations, key=lambda item: item.assignment.variant_id))

        frozen_observations = tuple(observations)
        aggregates = self._aggregation.aggregate(protocol, frozen_observations, expected)
        return StudyMatrixExecutionReport(protocol.protocol_digest, frozen_observations, aggregates)

    def execute_plan(
        self,
        plan: ExperimentPlan,
        assignments: tuple[StudyAssignment, ...],
        adapter: BoundStudyUnitExecutionPort,
    ) -> StudyMatrixExecutionReport:
        """Execute a compiled plan through its complete binding set.

        This is intentionally a distinct port from the legacy protocol-only
        path. A plan run must not silently downgrade to an adapter that can
        only interpret ``control`` and ``treatment`` by kind.
        """

        plan.assert_consistent()
        expected = plan.assignments
        self._require_exact_assignments(expected, assignments)
        units = _study_units(plan.protocol, assignments)
        binding_index = _binding_index(plan)

        if plan.protocol.concurrency_policy.parallel_variants:
            execute_bound_variant = getattr(adapter, "execute_bound_variant", None)
            if not callable(execute_bound_variant):
                raise TypeError(
                    "parallel compiled variants require an adapter implementing "
                    "execute_bound_variant"
                )

            def execute_variant(assignment: StudyAssignment) -> StudyMetricObservation:
                return execute_bound_variant(
                    assignment,
                    binding_index[assignment.variant_id],
                    plan.plan_digest,
                )

            unit_results = self._execute_variant_units(plan.protocol, units, execute_variant)
        else:
            execute_bound = getattr(adapter, "execute_bound", None)
            if not callable(execute_bound):
                raise TypeError(
                    "compiled experiment plans require an adapter implementing execute_bound"
                )

            def execute_unit(unit: StudyExecutionUnit) -> tuple[StudyMetricObservation, ...]:
                unit_bindings = tuple(binding_index[item.variant_id] for item in unit.assignments)
                return tuple(execute_bound(unit, unit_bindings, plan.plan_digest))

            unit_results = self._execute_repetitions(
                plan.protocol, units, execute_unit
            )
        observations: list[StudyMetricObservation] = []
        for unit, unit_observations in unit_results:
            self._require_exact_observations(unit, unit_observations, unit.repetition)
            observations.extend(sorted(unit_observations, key=lambda item: item.assignment.variant_id))

        frozen_observations = tuple(observations)
        aggregates = self._aggregation.aggregate(plan.protocol, frozen_observations, plan.assignments)
        return StudyMatrixExecutionReport(
            plan.protocol.protocol_digest,
            frozen_observations,
            aggregates,
            binding_digest=plan.binding_digest,
            plan_digest=plan.plan_digest,
        )

    @staticmethod
    def _require_exact_observations(
        unit: StudyExecutionUnit,
        observations: tuple[StudyMetricObservation, ...],
        repetition: int,
    ) -> None:
        expected_digests = {item.assignment_digest for item in unit.assignments}
        actual_digests = tuple(item.assignment.assignment_digest for item in observations)
        if len(actual_digests) != len(set(actual_digests)):
            raise ValueError(f"study unit returned duplicate observations: repetition={repetition}")
        if set(actual_digests) != expected_digests:
            raise ValueError(
                "study unit did not return exactly one observation per assignment: "
                f"repetition={repetition}"
            )

    @staticmethod
    def _require_exact_assignments(
        expected: tuple[StudyAssignment, ...],
        actual: tuple[StudyAssignment, ...],
    ) -> None:
        expected_digests = tuple(item.assignment_digest for item in expected)
        actual_digests = tuple(item.assignment_digest for item in actual)
        if len(actual_digests) != len(set(actual_digests)):
            raise ValueError("study assignment matrix contains duplicate assignments")
        if set(expected_digests) != set(actual_digests):
            raise ValueError("study assignment matrix is not exactly the declared protocol")


__all__ = ["StudyMatrixExecutor"]
