"""Pure standard-library execution for common paper data workflows."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable
from statistics import median
from typing import Any

from noetrium_platform.foundation.kernel.kernel import canonical_digest, freeze_json
from ..api import (
    AggregationFunction, AggregationSpec, BaselineRegistryPort, BaselineSpec,
    DataColumn, DataTable, EvaluationContext, FigureOutputFormat, FigureSpec, GroupComparison,
    InferenceResult, MetricSummary, MissingValuePolicy, MultipleComparisonMethod,
    MultipleComparisonResult, PairedComparison,
    ResearchEvaluation, ResearchReport, SplitStrategy,
)


def _operation_sha(value: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("operation configuration_digest must be lowercase SHA-256")
    return value


def _numeric(value: object, column: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"column {column!r} contains a non-finite numeric value")
    return float(value)


def _derived_table(table: DataTable, operation_id: str, configuration_digest: str,
                   columns: tuple[DataColumn, ...], rows: tuple[tuple[Any, ...], ...],
                   additional_lineage: tuple[str, ...] = ()) -> DataTable:
    if type(operation_id) is not str or not operation_id.strip():
        raise ValueError("operation_id must be non-empty")
    digest = _operation_sha(configuration_digest)
    metadata = tuple(item for item in table.metadata if item[0] != "last_operation") + (("last_operation", operation_id),)
    return DataTable(
        table.table_id,
        columns,
        rows,
        source_digest=table.source_digest,
        lineage_digests=tuple(dict.fromkeys(table.lineage_digests + (table.table_digest, digest) + additional_lineage)),
        metadata=metadata,
    )


def _group_rows(
    rows: tuple[tuple[Any, ...], ...],
    group_indexes: tuple[int, ...],
) -> tuple[tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]], ...]:
    grouped: dict[tuple[str, ...], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(repr(freeze_json(row[index])) for index in group_indexes)].append(row)
    return tuple(sorted(grouped.items(), key=lambda item: item[0]))


def _aggregate_value(
    rows: tuple[tuple[Any, ...], ...],
    spec: AggregationSpec,
    source_index: int | None,
    missing: MissingValuePolicy,
) -> Any:
    if spec.function is AggregationFunction.COUNT:
        return len(rows)
    source_values = (
        (row[source_index] for row in rows)
        if source_index is not None
        else rows
    )
    numeric: list[float] = []
    for value in source_values:
        if value is None and missing is MissingValuePolicy.SKIP:
            continue
        numeric.append(_numeric(value, spec.source_column or spec.output_name))
    if not numeric:
        if missing is MissingValuePolicy.REJECT:
            raise ValueError(f"aggregate column {spec.source_column!r} has no usable values")
        return None
    mean_value = sum(numeric) / len(numeric)
    if spec.function is AggregationFunction.SUM:
        return sum(numeric)
    if spec.function is AggregationFunction.MEAN:
        return mean_value
    if spec.function is AggregationFunction.VARIANCE:
        return sum((value - mean_value) ** 2 for value in numeric) / (len(numeric) - 1) if len(numeric) > 1 else 0.0
    if spec.function is AggregationFunction.STANDARD_DEVIATION:
        return math.sqrt(sum((value - mean_value) ** 2 for value in numeric) / (len(numeric) - 1)) if len(numeric) > 1 else 0.0
    if spec.function is AggregationFunction.MINIMUM:
        return min(numeric)
    if spec.function is AggregationFunction.MEDIAN:
        return median(numeric)
    if spec.function is AggregationFunction.MAXIMUM:
        return max(numeric)
    raise ValueError(f"unsupported aggregation function: {spec.function}")


def _join_key(row: tuple[Any, ...], indexes: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(repr(freeze_json(row[index])) for index in indexes)


def _join_index(
    rows: tuple[tuple[Any, ...], ...],
    indexes: tuple[int, ...],
) -> dict[tuple[str, ...], list[tuple[Any, ...]]]:
    index: dict[tuple[str, ...], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        index[_join_key(row, indexes)].append(row)
    return index


def _join_rows(
    left_row: tuple[Any, ...],
    matches: list[tuple[Any, ...]],
    right_only: tuple[tuple[int, DataColumn], ...],
    how: str,
) -> tuple[tuple[Any, ...], ...]:
    if not matches and how == "left":
        return (left_row + tuple(None for _ in right_only),)
    return tuple(
        left_row + tuple(right_row[index] for index, _ in right_only)
        for right_row in matches
    )


class TablePipeline:
    """Composable transformations with explicit implementation/configuration lineage."""

    def project(self, table: DataTable, columns: tuple[str, ...], *,
                operation_id: str, configuration_digest: str) -> DataTable:
        if type(columns) is not tuple or not columns or len(set(columns)) != len(columns):
            raise ValueError("project columns must be a non-empty unique tuple")
        indexes = tuple(table.column_index(name) for name in columns)
        schema = tuple(table.columns[index] for index in indexes)
        rows = tuple(tuple(row[index] for index in indexes) for row in table.rows)
        return _derived_table(table, operation_id, configuration_digest, schema, rows)

    def filter(self, table: DataTable, predicate: Callable[[dict[str, Any]], bool], *,
               operation_id: str, configuration_digest: str) -> DataTable:
        if not callable(predicate):
            raise TypeError("filter predicate must be callable")
        names = table.column_names
        rows = tuple(row for row in table.rows if predicate(dict(zip(names, row, strict=True))))
        return _derived_table(table, operation_id, configuration_digest, table.columns, rows)

    def derive(self, table: DataTable, column: DataColumn, function: Callable[[dict[str, Any]], Any], *,
               operation_id: str, configuration_digest: str) -> DataTable:
        if type(column) is not DataColumn or not callable(function):
            raise TypeError("derive requires DataColumn and callable function")
        if column.name in table.column_names:
            raise ValueError(f"derived column already exists: {column.name}")
        names = table.column_names
        rows = tuple(row + (freeze_json(function(dict(zip(names, row, strict=True)))),) for row in table.rows)
        return _derived_table(table, operation_id, configuration_digest, table.columns + (column,), rows)

    def split(self, table: DataTable, *, seed: int, fractions: tuple[tuple[str, float], ...],
              operation_id: str, configuration_digest: str,
              strategy: SplitStrategy = SplitStrategy.RANDOM,
              stratify_by: tuple[str, ...] = (), group_by: tuple[str, ...] = (),
              order_by: tuple[str, ...] = ()) -> dict[str, DataTable]:
        if type(seed) is not int or isinstance(seed, bool):
            raise TypeError("split seed must be an integer")
        if not isinstance(strategy, SplitStrategy):
            raise TypeError("split strategy must be SplitStrategy")
        if type(fractions) is not tuple or not fractions or any(type(item) is not tuple or len(item) != 2 for item in fractions):
            raise TypeError("split fractions must be a tuple of name/fraction pairs")
        if len({item[0] for item in fractions}) != len(fractions) or any(type(name) is not str or not name.strip() for name, _ in fractions):
            raise ValueError("split names must be unique and non-empty")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0 for _, value in fractions):
            raise ValueError("split fractions must be positive finite numbers")
        if not math.isclose(sum(float(value) for _, value in fractions), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("split fractions must sum to 1")
        for label, names in (("stratify_by", stratify_by), ("group_by", group_by), ("order_by", order_by)):
            if type(names) is not tuple or any(type(name) is not str or not name.strip() for name in names):
                raise TypeError(f"split {label} must contain non-empty strings")
            if len(names) != len(set(names)):
                raise ValueError(f"split {label} must be unique")
            for name in names:
                table.column_index(name)
        if strategy is SplitStrategy.STRATIFIED and not stratify_by:
            raise ValueError("stratified split requires stratify_by")
        if strategy is SplitStrategy.GROUP and not group_by:
            raise ValueError("group split requires group_by")
        if strategy is SplitStrategy.TEMPORAL and not order_by:
            raise ValueError("temporal split requires order_by")
        rng = random.Random(seed)
        names = tuple(name for name, _ in fractions)
        weights = tuple(float(value) for _, value in fractions)

        def key(row: tuple[Any, ...], columns: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(repr(freeze_json(row[table.column_index(name)])) for name in columns)

        def allocate(rows: list[tuple[Any, ...]], *, preserve_groups: bool = False, shuffle_rows: bool = True) -> list[list[tuple[Any, ...]]]:
            buckets: list[list[tuple[Any, ...]]] = [[] for _ in names]
            if preserve_groups:
                groups: dict[tuple[str, ...], list[tuple[Any, ...]]] = defaultdict(list)
                for row in rows:
                    groups.setdefault(key(row, group_by), []).append(row)
                units = list(groups.values())
                rng.shuffle(units)
                targets = [len(rows) * sum(weights[:index + 1]) for index in range(len(weights))]
                bucket_index = 0
                for unit in units:
                    if bucket_index < len(names) - 1 and len(buckets[bucket_index]) + len(unit) > targets[bucket_index]:
                        bucket_index += 1
                    buckets[bucket_index].extend(unit)
                return buckets
            if shuffle_rows:
                rng.shuffle(rows)
            start = 0
            for index, weight in enumerate(weights):
                end = len(rows) if index == len(weights) - 1 else start + round(len(rows) * weight)
                buckets[index].extend(rows[start:end])
                start = end
            return buckets

        if strategy is SplitStrategy.TEMPORAL:
            order_indexes = tuple(table.column_index(name) for name in order_by)

            def temporal_key(row: tuple[Any, ...]) -> tuple[tuple[int, object], ...]:
                components: list[tuple[int, object]] = []
                for index in order_indexes:
                    value = row[index]
                    if value is None:
                        raise ValueError("temporal split order columns cannot contain null")
                    if type(value) is bool:
                        components.append((0, int(value)))
                    elif isinstance(value, (int, float)):
                        if not math.isfinite(float(value)):
                            raise ValueError("temporal split order columns must be finite")
                        components.append((1, float(value)))
                    elif type(value) is str:
                        components.append((2, value))
                    else:
                        components.append((3, repr(freeze_json(value))))
                return tuple(components)

            ordered = sorted(table.rows, key=temporal_key)
            buckets = allocate(ordered, shuffle_rows=False)
        elif strategy is SplitStrategy.STRATIFIED:
            strata: dict[tuple[str, ...], list[tuple[Any, ...]]] = defaultdict(list)
            for row in table.rows:
                strata.setdefault(key(row, stratify_by), []).append(row)
            buckets = [[] for _ in names]
            for stratum in sorted(strata):
                local = allocate(strata[stratum])
                for index, rows in enumerate(local):
                    buckets[index].extend(rows)
        elif strategy is SplitStrategy.GROUP:
            buckets = allocate(list(table.rows), preserve_groups=True)
        else:
            buckets = allocate(list(table.rows))

        result: dict[str, DataTable] = {}
        split_config = canonical_digest({
            "operation_id": operation_id, "seed": seed, "fractions": fractions,
            "strategy": strategy.value, "stratify_by": stratify_by, "group_by": group_by,
            "order_by": order_by,
        })
        for name, rows in zip(names, buckets, strict=True):
            derived = _derived_table(table, f"{operation_id}:{name}", configuration_digest,
                                     table.columns, tuple(rows))
            split_digest = canonical_digest({"config": split_config, "name": name})
            result[name] = DataTable(
                derived.table_id + ":" + name, derived.columns, derived.rows,
                source_digest=derived.source_digest,
                lineage_digests=derived.lineage_digests + (split_digest,),
                metadata=derived.metadata + (("split_seed", str(seed)), ("split_strategy", strategy.value)),
            )
        return result

    def aggregate(self, table: DataTable, group_by: tuple[str, ...],
                  aggregations: tuple[AggregationSpec, ...], *,
                  operation_id: str, configuration_digest: str,
                  missing: MissingValuePolicy = MissingValuePolicy.SKIP) -> DataTable:
        if type(group_by) is not tuple or any(type(name) is not str or not name.strip() for name in group_by):
            raise TypeError("aggregate group_by must contain non-empty strings")
        if len(group_by) != len(set(group_by)):
            raise ValueError("aggregate group_by must be unique")
        if type(aggregations) is not tuple or not aggregations or any(type(item) is not AggregationSpec for item in aggregations):
            raise TypeError("aggregate aggregations must contain AggregationSpec")
        if type(missing) is not MissingValuePolicy:
            raise TypeError("aggregate missing must be MissingValuePolicy")
        group_indexes = tuple(table.column_index(name) for name in group_by)
        source_indexes = tuple(
            None if spec.source_column is None else table.column_index(spec.source_column)
            for spec in aggregations
        )
        ordered_groups = _group_rows(table.rows, group_indexes)
        columns = tuple(table.columns[index] for index in group_indexes)
        output_columns = tuple(DataColumn(spec.output_name, spec.data_type, False) for spec in aggregations)
        output_rows: list[tuple[Any, ...]] = []
        for _, rows in ordered_groups:
            group_values = tuple(rows[0][index] for index in group_indexes)
            values = tuple(
                _aggregate_value(rows, spec, source_index, missing)
                for spec, source_index in zip(aggregations, source_indexes, strict=True)
            )
            output_rows.append(group_values + values)
        return _derived_table(
            table,
            operation_id,
            configuration_digest,
            columns + output_columns,
            tuple(output_rows),
        )

    def join(self, left: DataTable, right: DataTable, on: tuple[str, ...], *,
             operation_id: str, configuration_digest: str, how: str = "inner") -> DataTable:
        if type(on) is not tuple or not on:
            raise ValueError("join keys must be a non-empty tuple")
        if type(how) is not str or how not in {"inner", "left"}:
            raise ValueError("join how must be inner or left")
        left_indexes = tuple(left.column_index(name) for name in on)
        right_indexes = tuple(right.column_index(name) for name in on)
        right_only = tuple((index, column) for index, column in enumerate(right.columns) if column.name not in on)
        if any(column.name in left.column_names for _, column in right_only):
            raise ValueError("join output columns must be unique")
        index = _join_index(right.rows, right_indexes)
        columns = left.columns + tuple(column for _, column in right_only)
        rows: list[tuple[Any, ...]] = []
        for left_row in left.rows:
            rows.extend(
                _join_rows(
                    left_row,
                    index.get(_join_key(left_row, left_indexes), []),
                    right_only,
                    how,
                )
            )
        return _derived_table(left, operation_id, configuration_digest, columns, tuple(rows),
                              additional_lineage=(right.table_digest,))


class ScientificStatistics:
    """One shared numeric analysis authority for summaries and two-group effects."""

    def summarize(self, table: DataTable, value_column: str, *,
                  group_by: tuple[str, ...] = (),
                  missing: MissingValuePolicy = MissingValuePolicy.REJECT) -> tuple[MetricSummary, ...]:
        if type(missing) is not MissingValuePolicy:
            raise TypeError("missing must be MissingValuePolicy")
        value_index = table.column_index(value_column)
        group_indexes = tuple(table.column_index(name) for name in group_by)
        grouped: dict[str, tuple[tuple[str, Any], list[float]]] = {}
        for row in table.rows:
            value = row[value_index]
            if value is None and missing is MissingValuePolicy.SKIP:
                continue
            numeric = _numeric(value, value_column)
            group = tuple((name, freeze_json(row[index])) for name, index in zip(group_by, group_indexes, strict=True))
            key = canonical_digest(group)
            grouped.setdefault(key, (group, []))[1].append(numeric)
        summaries = []
        for group, values in sorted(grouped.values(), key=lambda item: repr(item[0])):
            ordered = sorted(values)
            count = len(ordered)
            mean = sum(ordered) / count
            variance = sum((value - mean) ** 2 for value in ordered) / (count - 1) if count > 1 else 0.0
            deviation = math.sqrt(variance)
            error = math.sqrt(variance / count)
            margin = 1.96 * error
            summaries.append(MetricSummary(value_column, group, count, mean, variance, deviation, error,
                                           ordered[0], median(ordered), ordered[-1], mean - margin, mean + margin))
        return tuple(summaries)

    def compare(self, table: DataTable, value_column: str, group_column: str, *,
                baseline: Any, candidate: Any,
                missing: MissingValuePolicy = MissingValuePolicy.REJECT) -> GroupComparison:
        group_index = table.column_index(group_column)
        value_index = table.column_index(value_column)
        base: list[float] = []
        cand: list[float] = []
        for row in table.rows:
            if row[group_index] == baseline:
                if row[value_index] is None and missing is MissingValuePolicy.SKIP:
                    continue
                base.append(_numeric(row[value_index], value_column))
            elif row[group_index] == candidate:
                if row[value_index] is None and missing is MissingValuePolicy.SKIP:
                    continue
                cand.append(_numeric(row[value_index], value_column))
        if not base or not cand:
            raise ValueError("comparison groups must each contain at least one numeric observation")
        base_mean = sum(base) / len(base)
        cand_mean = sum(cand) / len(cand)
        difference = cand_mean - base_mean
        base_var = sum((x - base_mean) ** 2 for x in base) / (len(base) - 1) if len(base) > 1 else 0.0
        cand_var = sum((x - cand_mean) ** 2 for x in cand) / (len(cand) - 1) if len(cand) > 1 else 0.0
        standard_error = math.sqrt(base_var / len(base) + cand_var / len(cand))
        pooled_n = len(base) + len(cand) - 2
        pooled = math.sqrt(((len(base) - 1) * base_var + (len(cand) - 1) * cand_var) / pooled_n) if pooled_n else 0.0
        return GroupComparison(value_column, group_column, freeze_json(baseline), freeze_json(candidate),
                               len(base), len(cand), difference,
                               difference / base_mean if base_mean else None,
                               difference / pooled if pooled else None,
                               difference - 1.96 * standard_error, difference + 1.96 * standard_error,
                               self._normal_p(difference, standard_error))

    def adjust_p_values(
        self,
        p_values: tuple[float, ...],
        *,
        method: MultipleComparisonMethod = MultipleComparisonMethod.HOLM,
        alpha: float = 0.05,
    ) -> MultipleComparisonResult:
        """Apply one declared family-wise/FDR policy in the shared statistics authority."""
        if type(p_values) is not tuple or not p_values:
            raise ValueError("p_values must be a non-empty tuple")
        if type(method) is not MultipleComparisonMethod:
            raise TypeError("method must be MultipleComparisonMethod")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0.0 < float(alpha) < 1.0:
            raise ValueError("alpha must be between zero and one")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0 for value in p_values):
            raise ValueError("p_values must contain values between zero and one")
        count = len(p_values)
        order = sorted(range(count), key=lambda index: (float(p_values[index]), index))
        adjusted = [0.0] * count
        if method is MultipleComparisonMethod.BONFERRONI:
            adjusted = [min(1.0, float(value) * count) for value in p_values]
        elif method is MultipleComparisonMethod.HOLM:
            running = 0.0
            for rank, index in enumerate(order):
                running = max(running, min(1.0, (count - rank) * float(p_values[index])))
                adjusted[index] = running
        else:
            harmonic = sum(1.0 / rank for rank in range(1, count + 1)) if method is MultipleComparisonMethod.BENJAMINI_YEKUTIELI else 1.0
            running = 1.0
            for rank, index in reversed(tuple(enumerate(order, start=1))):
                running = min(running, min(1.0, float(p_values[index]) * count * harmonic / rank))
                adjusted[index] = running
        adjusted_tuple = tuple(adjusted)
        return MultipleComparisonResult(
            method, float(alpha), tuple(float(value) for value in p_values), adjusted_tuple,
            tuple(value <= float(alpha) for value in adjusted_tuple),
        )

    def compare_many(
        self,
        table: DataTable,
        value_column: str,
        group_column: str,
        *,
        baseline: Any,
        candidates: tuple[Any, ...] | None = None,
        missing: MissingValuePolicy = MissingValuePolicy.REJECT,
    ) -> tuple[GroupComparison, ...]:
        """Compare one declared baseline with every candidate under one policy."""
        if candidates is None:
            group_index = table.column_index(group_column)
            candidates = tuple(sorted({
                freeze_json(row[group_index])
                for row in table.rows
                if row[group_index] != baseline
            }, key=repr))
        if type(candidates) is not tuple or not candidates:
            raise ValueError("compare_many requires at least one candidate")
        if baseline in candidates:
            raise ValueError("compare_many candidates cannot include the baseline")
        results = tuple(
            self.compare(
                table,
                value_column,
                group_column,
                baseline=baseline,
                candidate=candidate,
                missing=missing,
            )
            for candidate in candidates
        )
        p_values = tuple(result.p_value if result.p_value is not None else 1.0 for result in results)
        adjusted = self.adjust_p_values(p_values)
        return tuple(
            GroupComparison(
                result.metric, result.group_column, result.baseline, result.candidate,
                result.baseline_count, result.candidate_count, result.difference,
                result.relative_difference, result.standardized_effect,
                result.confidence95_low, result.confidence95_high,
                result.p_value, adjusted.adjusted_p_values[index],
            )
            for index, result in enumerate(results)
        )

    @staticmethod
    def _normal_p(value: float, standard_error: float) -> float | None:
        if standard_error == 0.0:
            return 0.0 if value != 0.0 else 1.0
        return math.erfc(abs(value / standard_error) / math.sqrt(2.0))

    @staticmethod
    def _quantile(values: list[float], probability: float) -> float:
        if not values:
            raise ValueError("quantile requires values")
        ordered = sorted(values)
        position = (len(ordered) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    def mean_inference(self, table: DataTable, value_column: str, *,
                       missing: MissingValuePolicy = MissingValuePolicy.REJECT) -> InferenceResult:
        values = [_numeric(value, value_column) for value in table.values(value_column)
                  if not (value is None and missing is MissingValuePolicy.SKIP)]
        if not values:
            raise ValueError("mean inference requires at least one numeric observation")
        estimate = sum(values) / len(values)
        variance = sum((value - estimate) ** 2 for value in values) / (len(values) - 1) if len(values) > 1 else 0.0
        standard_error = math.sqrt(variance / len(values))
        return InferenceResult(value_column, "normal_mean", len(values), estimate, standard_error,
                               estimate - 1.96 * standard_error, estimate + 1.96 * standard_error,
                               self._normal_p(estimate, standard_error), None, 0.0)

    def bootstrap_mean(self, table: DataTable, value_column: str, *,
                       replicates: int = 2000, seed: int = 0,
                       missing: MissingValuePolicy = MissingValuePolicy.REJECT) -> InferenceResult:
        if type(replicates) is not int or replicates < 100:
            raise ValueError("bootstrap replicates must be an integer of at least 100")
        values = [_numeric(value, value_column) for value in table.values(value_column)
                  if not (value is None and missing is MissingValuePolicy.SKIP)]
        if not values:
            raise ValueError("bootstrap requires at least one numeric observation")
        rng = random.Random(seed)
        estimates = [sum(rng.choice(values) for _ in values) / len(values) for _ in range(replicates)]
        estimate = sum(values) / len(values)
        mean_estimate = sum(estimates) / len(estimates)
        variance = sum((value - mean_estimate) ** 2 for value in estimates) / (len(estimates) - 1)
        return InferenceResult(value_column, f"bootstrap_mean:{replicates}:{seed}", len(values),
                               estimate, math.sqrt(variance),
                               self._quantile(estimates, 0.025), self._quantile(estimates, 0.975),
                               None, None, 0.0)

    def paired_compare(self, table: DataTable, value_column: str, group_column: str, *,
                       pair_column: str, baseline: Any, candidate: Any,
                       missing: MissingValuePolicy = MissingValuePolicy.REJECT) -> PairedComparison:
        value_index = table.column_index(value_column)
        group_index = table.column_index(group_column)
        pair_index = table.column_index(pair_column)
        pairs: dict[Any, dict[Any, float]] = defaultdict(dict)
        for row in table.rows:
            value = row[value_index]
            if value is None and missing is MissingValuePolicy.SKIP:
                continue
            if row[group_index] not in (baseline, candidate):
                continue
            pair_key = repr(freeze_json(row[pair_index]))
            if row[group_index] in pairs[pair_key]:
                raise ValueError("paired comparison contains duplicate group values for one pair")
            pairs[pair_key][row[group_index]] = _numeric(value, value_column)
        differences = [values[candidate] - values[baseline] for values in pairs.values()
                       if baseline in values and candidate in values]
        if not differences:
            raise ValueError("paired comparison requires complete baseline/candidate pairs")
        estimate = sum(differences) / len(differences)
        variance = sum((value - estimate) ** 2 for value in differences) / (len(differences) - 1) if len(differences) > 1 else 0.0
        deviation = math.sqrt(variance)
        error = math.sqrt(variance / len(differences))
        return PairedComparison(value_column, pair_column, len(differences), estimate, deviation, error,
                                estimate - 1.96 * error, estimate + 1.96 * error,
                                self._normal_p(estimate, error),
                                estimate / deviation if deviation else None)

    def permutation_compare(self, table: DataTable, value_column: str, group_column: str, *,
                            baseline: Any, candidate: Any, replicates: int = 2000,
                            seed: int = 0,
                            missing: MissingValuePolicy = MissingValuePolicy.REJECT) -> InferenceResult:
        if type(replicates) is not int or replicates < 100:
            raise ValueError("permutation replicates must be an integer of at least 100")
        group_index = table.column_index(group_column)
        value_index = table.column_index(value_column)
        base = [_numeric(row[value_index], value_column) for row in table.rows
                if row[group_index] == baseline and not (row[value_index] is None and missing is MissingValuePolicy.SKIP)]
        cand = [_numeric(row[value_index], value_column) for row in table.rows
                if row[group_index] == candidate and not (row[value_index] is None and missing is MissingValuePolicy.SKIP)]
        if not base or not cand:
            raise ValueError("permutation comparison groups must each contain numeric observations")
        observed = sum(cand) / len(cand) - sum(base) / len(base)
        pooled = base + cand
        rng = random.Random(seed)
        exceedances = 0
        for _ in range(replicates):
            rng.shuffle(pooled)
            candidate_sample = pooled[:len(cand)]
            baseline_sample = pooled[len(cand):]
            permuted = sum(candidate_sample) / len(candidate_sample) - sum(baseline_sample) / len(baseline_sample)
            exceedances += abs(permuted) >= abs(observed)
        p_value = (exceedances + 1) / (replicates + 1)
        return InferenceResult(value_column, f"permutation_difference:{replicates}:{seed}",
                               len(base) + len(cand), observed, 0.0, observed, observed,
                               p_value, None, 0.0)


class InMemoryBaselineRegistry(BaselineRegistryPort):
    """Single-process baseline authority; durable implementations can use the port."""

    def __init__(self) -> None:
        self._baselines: dict[str, BaselineSpec] = {}

    def register(self, baseline: BaselineSpec) -> BaselineSpec:
        if type(baseline) is not BaselineSpec:
            raise TypeError("baseline registry accepts BaselineSpec")
        existing = self._baselines.get(baseline.baseline_id)
        if existing is not None and existing.baseline_digest != baseline.baseline_digest:
            raise ValueError(f"baseline identity is already registered: {baseline.baseline_id}")
        self._baselines[baseline.baseline_id] = baseline
        return baseline

    def catalog(self) -> tuple[BaselineSpec, ...]:
        """Return a deterministic, auditable baseline catalog."""
        return tuple(self._baselines[key] for key in sorted(self._baselines))

    def resolve(self, baseline_id: str) -> BaselineSpec:
        if type(baseline_id) is not str or not baseline_id.strip():
            raise ValueError("baseline_id must be non-empty")
        try:
            return self._baselines[baseline_id]
        except KeyError as exc:
            raise KeyError(f"baseline is not registered: {baseline_id}") from exc

    def validate(self, context: EvaluationContext) -> None:
        if type(context) is not EvaluationContext:
            raise TypeError("baseline validation requires EvaluationContext")
        if context.baseline_id is None:
            return
        baseline = self.resolve(context.baseline_id)
        if baseline.dataset_digest != context.dataset_digest:
            raise ValueError("baseline dataset digest does not match evaluation context")
        if baseline.protocol_digest != context.protocol_digest:
            raise ValueError("baseline protocol digest does not match evaluation context")
        if baseline.baseline_id == context.candidate_id:
            raise ValueError("evaluation candidate cannot be its own baseline")


class ResearchLifecycle:
    """One downstream-facing seam for run outputs, analysis, comparison and reports."""

    def __init__(
        self,
        *,
        pipeline: TablePipeline | None = None,
        statistics: ScientificStatistics | None = None,
        baselines: BaselineRegistryPort | None = None,
    ) -> None:
        self._pipeline = pipeline or TablePipeline()
        self._statistics = statistics or ScientificStatistics()
        self._baselines = baselines or InMemoryBaselineRegistry()

    @property
    def pipeline(self) -> TablePipeline:
        return self._pipeline

    @property
    def statistics(self) -> ScientificStatistics:
        return self._statistics

    @property
    def baselines(self) -> BaselineRegistryPort:
        return self._baselines

    def evaluate(
        self,
        table: DataTable,
        context: EvaluationContext,
        *,
        metric: str,
        group_by: tuple[str, ...] = (),
        comparison_group: str | None = None,
        baseline_value: Any | None = None,
        candidate_value: Any | None = None,
        candidate_values: tuple[Any, ...] | None = None,
        figures: tuple[Any, ...] = (),
        report_id: str | None = None,
        missing: MissingValuePolicy = MissingValuePolicy.REJECT,
    ) -> ResearchEvaluation:
        if type(table) is not DataTable:
            raise TypeError("research lifecycle table must be DataTable")
        if type(context) is not EvaluationContext:
            raise TypeError("research lifecycle context must be EvaluationContext")
        table_metadata = dict(table.metadata)
        for metadata_key, expected, error in (
            ("dataset_digest", context.dataset_digest, "dataset identity"),
            ("split_digest", context.split_digest, "split identity"),
            ("protocol_digest", context.protocol_digest, "protocol identity"),
        ):
            pinned = table_metadata.get(metadata_key)
            if pinned is not None and pinned != expected:
                raise ValueError(f"evaluation table metadata does not match {error}")
        self._baselines.validate(context)
        summaries = self._statistics.summarize(
            table, metric, group_by=group_by, missing=missing
        )
        comparison = None
        comparisons = ()
        if comparison_group is not None:
            if baseline_value is None:
                raise ValueError("comparison requires baseline_value")
            if candidate_values is not None and candidate_value is not None:
                raise ValueError("provide candidate_value or candidate_values, not both")
            if candidate_values is None:
                if candidate_value is None:
                    raise ValueError("comparison requires candidate_value or candidate_values")
                candidate_values = (candidate_value,)
            if type(candidate_values) is not tuple or not candidate_values:
                raise ValueError("candidate_values must be a non-empty tuple")
            comparisons = self._statistics.compare_many(
                table, metric, comparison_group,
                baseline=baseline_value, candidates=candidate_values,
                missing=missing,
            )
            comparison = comparisons[0]
        elif candidate_values is not None or candidate_value is not None or baseline_value is not None:
            raise ValueError("comparison values require comparison_group")
        if type(figures) is not tuple:
            raise TypeError("research lifecycle figures must be a tuple")
        if any(type(figure) is not FigureSpec for figure in figures):
            raise TypeError("research lifecycle figures must contain FigureSpec")
        active_report_id = report_id or (
            f"{context.project_id}:{context.study_id}:{context.candidate_id}:{context.stage.value}"
        )
        metadata = (
            ("context_digest", context.context_digest),
            ("candidate_id", context.candidate_id),
            ("stage", context.stage.value),
            ("dataset_digest", context.dataset_digest),
            ("split_digest", context.split_digest),
            ("protocol_digest", context.protocol_digest),
            ("code_commit", context.code_commit),
            ("configuration_digest", context.configuration_digest),
            ("seed", context.seed),
        )
        report = ResearchReport(
            active_report_id,
            tables=(table,),
            figures=figures,
            metadata=metadata,
        )
        return ResearchEvaluation(
            context=context,
            table=table,
            summaries=summaries,
            comparison=comparison,
            report=report,
            comparisons=comparisons,
        )

    def run_and_evaluate(
        self,
        executor: Any,
        *,
        run_spec: Any,
        context: EvaluationContext,
        unit_adapter: Any,
        protocol: Any | None = None,
        plan: Any | None = None,
        **kwargs: Any,
    ) -> ResearchEvaluation:
        """Execute one frozen run, then route its observations through this facade."""
        if type(context) is not EvaluationContext:
            raise TypeError("research lifecycle context must be EvaluationContext")
        result = executor.execute(
            run_spec=run_spec,
            protocol=protocol,
            plan=plan,
            unit_adapter=unit_adapter,
        )
        report = getattr(result, "study_report", None)
        observations = getattr(report, "observations", None)
        if observations is None:
            raise TypeError("run executor returned no study observations")
        protocol_digest = getattr(result, "protocol_digest", None)
        if protocol_digest != context.protocol_digest:
            raise ValueError("run result protocol does not match evaluation context")
        return self.evaluate_study_observations(observations, context, **kwargs)

    def evaluate_study_observations(
        self,
        observations: tuple[Any, ...],
        context: EvaluationContext,
        **kwargs: Any,
    ) -> ResearchEvaluation:
        """Adapt generic Study observations once, then use the shared lifecycle."""
        from ..api import StudyObservationTableAdapter

        table = StudyObservationTableAdapter().to_table(observations)
        return self.evaluate(table, context, **kwargs)

    def evaluate_measurement_records(
        self,
        records: tuple[Any, ...],
        context: EvaluationContext,
        *,
        measurement_id: str,
        group_by: tuple[str, ...] = ("variant_id",),
        **kwargs: Any,
    ) -> ResearchEvaluation:
        """Project authoritative scalar records into the shared lifecycle once."""
        from ..api import MeasurementRecordTableAdapter

        if type(records) is not tuple or not records:
            raise ValueError("measurement records must be a non-empty tuple")
        from ...study.api import MeasurementRecord

        if any(type(record) is not MeasurementRecord for record in records):
            raise TypeError("measurement records must contain MeasurementRecord")
        if type(measurement_id) is not str or not measurement_id.strip():
            raise ValueError("measurement_id must be non-empty")
        selected = tuple(record for record in records if record.measurement_id == measurement_id)
        if not selected:
            raise ValueError("no measurement record matches measurement_id")
        for record in selected:
            if record.project_id != context.project_id:
                raise ValueError("measurement record project does not match evaluation context")
            if record.study_id != context.study_id:
                raise ValueError("measurement record study does not match evaluation context")
            if context.run_id is not None and record.run_id != context.run_id:
                raise ValueError("measurement record run does not match evaluation context")
        table = MeasurementRecordTableAdapter().to_table(
            selected,
            table_id=f"measurement:{measurement_id}",
        )
        return self.evaluate(
            table,
            context,
            metric="value",
            group_by=group_by,
            **kwargs,
        )

    def evaluate_trial_report(
        self,
        report: Any,
        context: EvaluationContext,
        *,
        measurement_id: str,
        group_by: tuple[str, ...] = ("variant_id",),
        **kwargs: Any,
    ) -> ResearchEvaluation:
        """Bridge trial receipts without bypassing Measurement authority."""
        from ...study.api import TrialMatrixExecutionReport

        if type(report) is not TrialMatrixExecutionReport:
            raise TypeError("trial report must be TrialMatrixExecutionReport")
        if report.project_id != context.project_id:
            raise ValueError("trial report project does not match evaluation context")
        if context.run_id is not None and report.run_id != context.run_id:
            raise ValueError("trial report run does not match evaluation context")
        return self.evaluate_measurement_records(
            report.records,
            context,
            measurement_id=measurement_id,
            group_by=group_by,
            **kwargs,
        )

    def render(
        self,
        evaluation: ResearchEvaluation,
        *,
        table_format: str = "markdown",
        output_format: FigureOutputFormat = FigureOutputFormat.PDF,
        table_renderer: Any | None = None,
        figure_renderer: Any | None = None,
    ) -> Any:
        """Render one evaluation through injected adapters; figures default to PDF."""
        from ..api import RenderedResearchPackage

        if type(evaluation) is not ResearchEvaluation:
            raise TypeError("research lifecycle evaluation must be ResearchEvaluation")
        if table_renderer is None or figure_renderer is None:
            raise ValueError("render requires explicit table_renderer and figure_renderer ports")
        if not callable(getattr(table_renderer, "render", None)):
            raise TypeError("table_renderer must provide render(table, format)")
        if not callable(getattr(figure_renderer, "render", None)):
            raise TypeError("figure_renderer must provide render(figure, output_format=...)")
        if type(output_format) is not FigureOutputFormat:
            raise TypeError("output_format must be FigureOutputFormat")
        table_text = table_renderer.render(evaluation.table, table_format)
        figures = tuple(
            (figure.figure_id, figure_renderer.render(figure, output_format=output_format))
            for figure in evaluation.report.figures
        )
        return RenderedResearchPackage(
            evaluation_digest=evaluation.evaluation_digest,
            table_format=table_format,
            table_text=table_text,
            figures=figures,
            figure_format=output_format,
        )


__all__ = [
    "InMemoryBaselineRegistry",
    "ResearchLifecycle",
    "ScientificStatistics",
    "TablePipeline",
]