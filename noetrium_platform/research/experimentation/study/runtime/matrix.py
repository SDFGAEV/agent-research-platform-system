from __future__ import annotations

from noetrium_platform.research.experimentation.experiment.api.contracts import (
    canonical_digest, require_sha256,
    ExperimentDefinition as UniversalExperimentDefinition,
    ExperimentDoctorPort,
    ExperimentLifecycleState as UniversalExperimentLifecycleState,
    ExperimentPlan as UniversalExperimentPlan,
    ExperimentRunReport as UniversalExperimentRunReport,
    ExperimentTransition as UniversalExperimentTransition,
    ExperimentUnit as UniversalExperimentUnit,
    ExperimentUnitExecutorPort as UniversalExperimentUnitExecutorPort,
    ExperimentUnitKind as UniversalExperimentUnitKind,
    FindingSeverity as UniversalFindingSeverity,
    ObservationEnvelope as UniversalObservationEnvelope,
    ObservationKind as UniversalObservationKind,
    ObservationSinkPort as UniversalObservationSinkPort,
    RawRecord as UniversalRawRecord,
    RawRecordStorePort as UniversalRawRecordStorePort,
    MetricAggregation as UniversalMetricAggregation,
    MetricDefinition as UniversalMetricDefinition,
    MetricMissingPolicy as UniversalMetricMissingPolicy,
    MetricPredicate as UniversalMetricPredicate,
    MetricReport as UniversalMetricReport,
    MetricValue as UniversalMetricValue,
    DoctorFinding as UniversalDoctorFinding,
    UnitOutcome as UniversalUnitOutcome,
    UnitOutcomeState as UniversalUnitOutcomeState,
)
from dataclasses import dataclass

from collections import defaultdict
from collections.abc import Callable, Mapping
import math
from threading import RLock
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

class StudyMatrixUniversalProjection:
    """Project matrix assignments into family-neutral experiment units.

    The projection deliberately preserves assignment identity and does not
    expose benchmark/task assumptions to the universal planner.
    """

    @staticmethod
    def plan(
        experiment_id: str,
        definition_digest: str,
        assignments: tuple[StudyAssignment, ...],
    ) -> UniversalExperimentPlan:
        if type(experiment_id) is not str or not experiment_id.strip():
            raise ValueError("experiment_id must be non-empty")
        require_sha256(definition_digest, "definition_digest")
        if type(assignments) is not tuple or not assignments:
            raise ValueError("assignments must be a non-empty tuple")
        if any(not isinstance(item, StudyAssignment) for item in assignments):
            raise TypeError("assignments must contain StudyAssignment")
        units = tuple(
            UniversalExperimentUnit(
                unit_id=f"assignment:{item.assignment_digest}",
                kind=UniversalExperimentUnitKind.TASK if item.task_id is not None else UniversalExperimentUnitKind.GENERIC,
                input_digest=canonical_digest({
                    "task_id": item.task_id,
                    "study_id": item.study_id,
                }),
                condition_digest=canonical_digest({
                    "variant_id": item.variant_id,
                    "repetition": item.repetition,
                }),
                seed=item.seed,
                ordinal=ordinal,
            )
            for ordinal, item in enumerate(assignments)
        )
        return UniversalExperimentPlan(
            experiment_id=experiment_id,
            definition_digest=definition_digest,
            units=units,
            planner_id="study-matrix-universal-projection-v1",
        )


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


def _binding_index(plan: UniversalExperimentPlan) -> dict[str, VariantBinding]:
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
        plan: UniversalExperimentPlan,
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


class StaticUnitPlanner:
    """Reference planner; family-specific planners can implement the same port."""

    def plan(self, definition: UniversalExperimentDefinition, units: tuple[UniversalExperimentUnit, ...]) -> UniversalExperimentPlan:
        if any(unit.kind is not definition.unit_kind for unit in units):
            raise ValueError("unit kind does not match experiment definition")
        return UniversalExperimentPlan(definition.experiment_id, definition.definition_digest, units)


class InMemoryObservationLedger(UniversalObservationSinkPort):
    """Volatile observation view; production raw facts use an injected store."""

    def __init__(self, raw_store: UniversalRawRecordStorePort | None = None) -> None:
        self._lock = RLock()
        self._raw_store = raw_store
        self._observations: list[UniversalObservationEnvelope] = []
        self._last_sequence: dict[tuple[str, str], int] = {}
        self._raw_records: list[UniversalRawRecord] = []
        self._last_raw_sequence: dict[tuple[str, str], int] = {}
        self._raw_digests: set[str] = set()

    def append(self, observation: UniversalObservationEnvelope) -> None:
        with self._lock:
            key = (observation.run_id, observation.unit_id)
            last = self._last_sequence.get(key, -1)
            if observation.sequence != last + 1:
                raise ValueError(f"observation sequence gap or duplicate for {key!r}")
            self._last_sequence[key] = observation.sequence
            self._observations.append(observation)

    def snapshot(self) -> tuple[UniversalObservationEnvelope, ...]:
        with self._lock:
            return tuple(self._observations)

    def append_raw_record(self, record: UniversalRawRecord) -> None:
        if self._raw_store is not None:
            self._raw_store.append_raw_record(record)
            return
        if type(record) is not UniversalRawRecord:
            raise TypeError("raw ledger accepts only RawRecord")
        with self._lock:
            key = (record.run_id, record.unit_id)
            last = self._last_raw_sequence.get(key, -1)
            if record.sequence != last + 1:
                raise ValueError(f"raw record sequence gap or duplicate for {key!r}")
            if record.record_digest in self._raw_digests:
                raise ValueError("raw ledger rejects duplicate record digest")
            self._last_raw_sequence[key] = record.sequence
            self._raw_digests.add(record.record_digest)
            self._raw_records.append(record)

    def raw_snapshot(self) -> tuple[UniversalRawRecord, ...]:
        if self._raw_store is not None:
            return self._raw_store.raw_snapshot()
        with self._lock:
            return tuple(self._raw_records)


def _metric_path(value: object, path: tuple[str, ...]) -> tuple[bool, object]:
    current = value
    for part in path:
        if isinstance(current, Mapping):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, (tuple, list)) and part.isdecimal():
            index = int(part)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _metric_record_path(record: UniversalRawRecord, path: tuple[str, ...]) -> tuple[bool, object]:
    """Resolve payload paths and explicit envelope/dimension namespaces.

    Existing payload paths remain concise; envelope and extensible dimensions
    are addressed as envelope.* and dimensions.* so every captured fact can
    become a metric without changing the raw ledger.
    """
    if path and path[0] == "envelope":
        envelope = {
            "experiment_id": record.experiment_id, "run_id": record.run_id,
            "unit_id": record.unit_id, "sequence": record.sequence,
            "stream_id": record.stream_id, "attempt_id": record.attempt_id,
            "causation_id": record.causation_id, "correlation_id": record.correlation_id,
            "trace_id": record.trace_id, "span_id": record.span_id,
            "occurred_at": record.occurred_at, "recorded_at": record.recorded_at,
            "monotonic_ns": record.monotonic_ns, "clock_source": record.clock_source,
            "clock_uncertainty_ns": record.clock_uncertainty_ns,
            "producer_id": record.producer_id, "producer_version": record.producer_version,
            "schema_id": record.schema_id, "record_type": record.record_type,
            "event_name": record.event_name, "operation_id": record.operation_id,
            "status": record.status, "outcome": record.outcome,
            "raw_payload_digest": record.raw_payload_digest,
            "record_digest": record.record_digest,
            "content_type": record.content_type, "content_encoding": record.content_encoding,
            "sampled": record.sampled, "sampling_rate": record.sampling_rate,
        }
        return _metric_path(envelope, path[1:])
    if path and path[0] == "dimensions":
        return _metric_path(record.dimensions, path[1:])
    present, value = _metric_path(record.payload, path)
    if present:
        return present, value
    return _metric_path(record.dimensions, path)


def _metric_number(value: object, metric_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"metric {metric_id} requires numeric values")
    if not math.isfinite(float(value)):
        raise ValueError(f"metric {metric_id} requires finite numeric values")
    return float(value)


def _metric_aggregate(
    aggregation: UniversalMetricAggregation,
    values: list[object],
    metric_id: str,
) -> object:
    if aggregation is UniversalMetricAggregation.COUNT:
        return len(values)
    if aggregation is UniversalMetricAggregation.FIRST:
        return values[0] if values else None
    if aggregation is UniversalMetricAggregation.LAST:
        return values[-1] if values else None
    if aggregation is UniversalMetricAggregation.DISTINCT_COUNT:
        return len({canonical_digest(item) for item in values})
    numbers = [_metric_number(item, metric_id) for item in values]
    if aggregation is UniversalMetricAggregation.SUM:
        return sum(numbers)
    if not numbers:
        return 0.0
    if aggregation is UniversalMetricAggregation.MEAN:
        return sum(numbers) / len(numbers)
    if aggregation is UniversalMetricAggregation.MIN:
        return min(numbers)
    if aggregation is UniversalMetricAggregation.MAX:
        return max(numbers)
    if aggregation is UniversalMetricAggregation.STDDEV:
        mean = sum(numbers) / len(numbers)
        return math.sqrt(sum((item - mean) ** 2 for item in numbers) / len(numbers))
    ordered = sorted(numbers)
    rank = (len(ordered) - 1) * (0.50 if aggregation is UniversalMetricAggregation.P50 else 0.95)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class MetricEngine:
    """Compile no code; evaluate typed metric declarations over an immutable cut."""

    @staticmethod
    def evaluate(
        records: tuple[UniversalRawRecord, ...],
        definitions: tuple[UniversalMetricDefinition, ...],
    ) -> UniversalMetricReport:
        if type(records) is not tuple or any(type(item) is not UniversalRawRecord for item in records):
            raise TypeError("metric engine records must contain RawRecord")
        if type(definitions) is not tuple or not definitions:
            raise ValueError("metric engine definitions must be a non-empty tuple")
        ids = tuple(item.metric_id for item in definitions)
        if len(ids) != len(set(ids)):
            raise ValueError("metric definitions must have unique metric ids")
        record_ids = tuple(item.record_digest for item in records)
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("metric engine raw cut contains duplicate records")
        values: list[UniversalMetricValue] = []
        for definition in definitions:
            groups: dict[str, tuple[tuple[object, ...], list[object], list[UniversalRawRecord]]] = {}
            for record in records:
                if definition.record_types and record.record_type not in definition.record_types:
                    continue
                if definition.schema_ids and record.schema_id not in definition.schema_ids:
                    continue
                if any(
                    not _metric_record_path(record, predicate.path)[0]
                    or _metric_record_path(record, predicate.path)[1] != predicate.equals
                    for predicate in definition.predicates
                ):
                    continue
                group_key_values: list[object] = []
                group_missing = False
                for path in definition.group_by:
                    present, group_value = _metric_record_path(record, path)
                    if not present:
                        group_missing = True
                        break
                    group_key_values.append(group_value)
                if group_missing:
                    if definition.missing is UniversalMetricMissingPolicy.FAIL:
                        raise ValueError(f"metric {definition.metric_id} has a missing group field")
                    continue
                if definition.aggregation is UniversalMetricAggregation.COUNT:
                    present, value = True, 1
                else:
                    present, value = _metric_record_path(record, definition.value_path)
                    if not present:
                        if definition.missing is UniversalMetricMissingPolicy.FAIL:
                            raise ValueError(f"metric {definition.metric_id} has a missing value field")
                        if definition.missing is UniversalMetricMissingPolicy.ZERO:
                            value = 0
                            present = True
                if not present:
                    continue
                group_key = canonical_digest(tuple(group_key_values))
                row = groups.setdefault(group_key, (tuple(group_key_values), [], []))
                row[1].append(value)
                row[2].append(record)
            if not groups:
                empty_value = 0 if definition.aggregation in (
                    UniversalMetricAggregation.COUNT,
                    UniversalMetricAggregation.DISTINCT_COUNT,
                ) else _metric_aggregate(definition.aggregation, [], definition.metric_id)
                values.append(UniversalMetricValue(
                    definition.metric_id, (), empty_value, 0, (),
                ))
                continue
            for group_key, (key_values, group_values, group_records) in groups.items():
                del group_key
                values.append(UniversalMetricValue(
                    definition.metric_id,
                    tuple(key_values),
                    _metric_aggregate(definition.aggregation, group_values, definition.metric_id),
                    len(group_values),
                    tuple(sorted(record.record_digest for record in group_records)),
                ))
        values.sort(key=lambda item: (item.metric_id, canonical_digest(item.group_key)))
        return UniversalMetricReport(
            canonical_digest(record_ids),
            canonical_digest(tuple(item.definition_digest for item in definitions)),
            tuple(values),
        )


@dataclass(frozen=True, slots=True)
class DoctorReport:
    findings: tuple[UniversalDoctorFinding, ...]

    @property
    def healthy(self) -> bool:
        return not any(item.blocking for item in self.findings)


class ExperimentDoctor(ExperimentDoctorPort):
    def inspect(self, plan: UniversalExperimentPlan, observations: tuple[UniversalObservationEnvelope, ...]) -> tuple[UniversalDoctorFinding, ...]:
        expected = {unit.unit_id for unit in plan.units}
        seen = {item.unit_id for item in observations}
        findings: list[UniversalDoctorFinding] = []
        for unit_id in sorted(expected - seen):
            findings.append(UniversalDoctorFinding(
                "unit.missing", UniversalFindingSeverity.ERROR, unit_id,
                "planned experiment unit produced no observation",
                blocking=True, recovery_action="retry_unit",
            ))
        unknown = sorted(seen - expected)
        for unit_id in unknown:
            findings.append(UniversalDoctorFinding(
                "unit.unknown", UniversalFindingSeverity.CRITICAL, unit_id,
                "observation references a unit absent from the frozen plan",
                blocking=True,
            ))
        by_stream: dict[tuple[str, str], list[UniversalObservationEnvelope]] = {}
        for item in observations:
            if item.experiment_id != plan.experiment_id:
                findings.append(UniversalDoctorFinding(
                    "observation.experiment_mismatch", UniversalFindingSeverity.CRITICAL,
                    item.unit_id, "observation belongs to another experiment",
                    blocking=True,
                ))
            by_stream.setdefault((item.run_id, item.unit_id), []).append(item)
        seen_digests: set[str] = set()
        for stream, items in by_stream.items():
            sequences = sorted(item.sequence for item in items)
            if len(sequences) != len(set(sequences)):
                findings.append(UniversalDoctorFinding(
                    "observation.duplicate_sequence", UniversalFindingSeverity.ERROR,
                    stream[1], "observation stream contains duplicate sequence numbers",
                    blocking=True, recovery_action="deduplicate_stream",
                ))
            expected_sequences = list(range(len(sequences)))
            if sequences != expected_sequences:
                findings.append(UniversalDoctorFinding(
                    "observation.sequence_gap", UniversalFindingSeverity.ERROR,
                    stream[1], "observation stream is not contiguous from sequence zero",
                    blocking=True, recovery_action="replay_missing_events",
                ))
            for item in items:
                if item.observation_digest in seen_digests:
                    findings.append(UniversalDoctorFinding(
                        "observation.duplicate", UniversalFindingSeverity.ERROR,
                        item.unit_id, "duplicate observation digest detected",
                        blocking=True, recovery_action="deduplicate_stream",
                    ))
                seen_digests.add(item.observation_digest)
        return tuple(findings)

    def report(self, plan: UniversalExperimentPlan, observations: tuple[UniversalObservationEnvelope, ...]) -> DoctorReport:
        return DoctorReport(self.inspect(plan, observations))


class ExperimentLifecycle:
    _ALLOWED = {
        UniversalExperimentLifecycleState.DECLARED: {UniversalExperimentLifecycleState.RESOLVED, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.RESOLVED: {UniversalExperimentLifecycleState.FROZEN, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.FROZEN: {UniversalExperimentLifecycleState.PLANNED, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.PLANNED: {UniversalExperimentLifecycleState.RUNNING, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.RUNNING: {UniversalExperimentLifecycleState.PAUSED, UniversalExperimentLifecycleState.COMPLETED, UniversalExperimentLifecycleState.PARTIAL, UniversalExperimentLifecycleState.FAILED, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.PAUSED: {UniversalExperimentLifecycleState.RESUMING, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.RESUMING: {UniversalExperimentLifecycleState.RUNNING, UniversalExperimentLifecycleState.FAILED},
        UniversalExperimentLifecycleState.PARTIAL: {UniversalExperimentLifecycleState.RESUMING, UniversalExperimentLifecycleState.COMPLETED, UniversalExperimentLifecycleState.FAILED},
        UniversalExperimentLifecycleState.FAILED: {UniversalExperimentLifecycleState.RESUMING, UniversalExperimentLifecycleState.CANCELLED},
        UniversalExperimentLifecycleState.COMPLETED: set(),
        UniversalExperimentLifecycleState.CANCELLED: set(),
    }

    def __init__(self, experiment_id: str) -> None:
        self.experiment_id = experiment_id
        self.state = UniversalExperimentLifecycleState.DECLARED
        self.transitions: list[UniversalExperimentTransition] = []

    def transition(self, target: UniversalExperimentLifecycleState, logical_time: str, reason: str) -> UniversalExperimentTransition:
        if target not in self._ALLOWED[self.state]:
            raise ValueError(f"illegal experiment transition {self.state.value}->{target.value}")
        transition = UniversalExperimentTransition(self.experiment_id, self.state, target, logical_time, reason)
        self.transitions.append(transition)
        self.state = target
        return transition


class UniversalExperimentKernel:
    def __init__(self, planner: StaticUnitPlanner | None = None, doctor: UniversalExperimentDoctor | None = None) -> None:
        self.planner = planner or StaticUnitPlanner()
        self.doctor = doctor or ExperimentDoctor()

    def compile(self, definition: UniversalExperimentDefinition, units: tuple[UniversalExperimentUnit, ...]) -> UniversalExperimentPlan:
        return self.planner.plan(definition, units)

    def inspect(self, plan: UniversalExperimentPlan, observations: tuple[UniversalObservationEnvelope, ...]) -> UniversalDoctorReport:
        return self.doctor.report(plan, observations)


class UniversalExperimentRunner:
    """Run arbitrary experiment units through one observable control loop."""

    def __init__(self, doctor: UniversalExperimentDoctor | None = None) -> None:
        self.doctor = doctor or ExperimentDoctor()

    def run(
        self,
        plan: UniversalExperimentPlan,
        run_id: str,
        executor: UniversalExperimentUnitExecutorPort,
        ledger: InMemoryObservationLedger | None = None,
    ) -> UniversalExperimentRunReport:
        sink = ledger or InMemoryObservationLedger()
        lifecycle = ExperimentLifecycle(plan.experiment_id)
        lifecycle.transition(UniversalExperimentLifecycleState.RESOLVED, "run:resolved", "execution dependencies resolved")
        lifecycle.transition(UniversalExperimentLifecycleState.FROZEN, "run:frozen", "plan identity frozen")
        lifecycle.transition(UniversalExperimentLifecycleState.PLANNED, "run:planned", "unit plan accepted")
        lifecycle.transition(UniversalExperimentLifecycleState.RUNNING, "run:running", "execution started")
        outcomes: list[UniversalUnitOutcome] = []
        for unit in plan.units:
            try:
                outcome = executor.execute_unit(unit, run_id, sink)
                if outcome.unit_id != unit.unit_id:
                    raise ValueError("executor returned an outcome for another unit")
            except Exception as exc:
                outcome = UniversalUnitOutcome(
                    unit.unit_id, UniversalUnitOutcomeState.FAILED, 1,
                    error_code=f"executor.{type(exc).__name__}",
                )
            outcomes.append(outcome)
        observations = sink.snapshot()
        findings = self.doctor.inspect(plan, observations)
        failed = any(item.state is not UniversalUnitOutcomeState.SUCCEEDED for item in outcomes)
        blocked = any(item.blocking for item in findings)
        terminal = UniversalExperimentLifecycleState.COMPLETED
        if failed or blocked:
            terminal = (
                UniversalExperimentLifecycleState.FAILED
                if not any(item.state is UniversalUnitOutcomeState.SUCCEEDED for item in outcomes)
                else UniversalExperimentLifecycleState.PARTIAL
            )
        lifecycle.transition(terminal, "run:terminal", "execution and automatic diagnosis completed")
        return UniversalExperimentRunReport(
            plan.experiment_id, run_id, plan.plan_digest, terminal,
            tuple(outcomes), findings,
        )


UniversalExperimentDoctor = ExperimentDoctor
UniversalDoctorReport = DoctorReport

__all__ = [
    "StudyMatrixExecutor", "StudyMatrixUniversalProjection", "StaticUnitPlanner",
    "InMemoryObservationLedger", "DoctorReport", "ExperimentDoctor", "ExperimentLifecycle",
    "UniversalExperimentKernel", "UniversalExperimentRunner", "MetricEngine",
]
