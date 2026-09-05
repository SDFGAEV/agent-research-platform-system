import pytest

from noetrium_platform.research.experimentation.experiment.api import (
    MetricAggregation,
    MetricDefinition,
    MetricMissingPolicy,
    MetricPredicate,
    RawRecord,
)
from noetrium_platform.research.experimentation.study.runtime import (
    InMemoryObservationLedger,
    MetricEngine,
)

SHA = "a" * 64


def record(sequence: int, payload: dict, raw_payload: str, record_type: str = "llm.usage") -> RawRecord:
    return RawRecord(
        "project", "run", "unit", sequence, "t0", "t1", "model-adapter",
        "llm.response.v1", record_type, raw_payload.encode("utf-8"), payload,
    )


def test_raw_payload_is_verbatim_and_digest_bound() -> None:
    first = record(0, {"usage": {"prompt_tokens": 3}}, "{ \"usage\": {\"prompt_tokens\": 3} }")
    second = record(0, {"usage": {"prompt_tokens": 3}}, "{\"usage\":{\"prompt_tokens\":3}}")
    assert first.raw_payload != second.raw_payload
    assert first.record_digest != second.record_digest
    assert first.payload["usage"]["prompt_tokens"] == 3


def test_raw_ledger_is_ordered_and_rejects_duplicate_digest() -> None:
    ledger = InMemoryObservationLedger()
    first = record(0, {"value": 1}, "{\"value\":1}")
    duplicate = record(0, {"value": 1}, "{\"value\":1}")
    ledger.append_raw_record(first)
    with pytest.raises(ValueError, match="duplicate"):
        ledger.append_raw_record(duplicate)
    with pytest.raises(ValueError, match="sequence gap"):
        ledger.append_raw_record(record(2, {"value": 2}, "{\"value\":2}"))
    assert ledger.raw_snapshot() == (first,)


def test_custom_metrics_aggregate_tokens_latency_and_grouping() -> None:
    rows = (
        record(0, {"model": "a", "usage": {"prompt_tokens": 10, "completion_tokens": 4}, "latency_ms": 100}, "raw-a"),
        record(1, {"model": "a", "usage": {"prompt_tokens": 7, "completion_tokens": 3}, "latency_ms": 200}, "raw-b"),
        record(2, {"model": "b", "usage": {"prompt_tokens": 5, "completion_tokens": 2}, "latency_ms": 50}, "raw-c"),
    )
    definitions = (
        MetricDefinition("prompt_tokens", MetricAggregation.SUM, ("llm.usage",), value_path=("usage", "prompt_tokens"), group_by=(("model",),), unit="tokens"),
        MetricDefinition("completion_tokens", MetricAggregation.SUM, ("llm.usage",), value_path=("usage", "completion_tokens"), group_by=(("model",),), unit="tokens"),
        MetricDefinition("latency_mean", MetricAggregation.MEAN, ("llm.usage",), value_path=("latency_ms",), group_by=(("model",),), unit="ms"),
        MetricDefinition("call_count", MetricAggregation.COUNT, ("llm.usage",), group_by=(("model",),)),
    )
    report = MetricEngine.evaluate(rows, definitions)
    by_id = {(item.metric_id, item.group_key): item.value for item in report.values}
    assert by_id[("prompt_tokens", ("a",))] == 17.0
    assert by_id[("completion_tokens", ("b",))] == 2.0
    assert by_id[("latency_mean", ("a",))] == 150.0
    assert by_id[("call_count", ("b",))] == 1
    assert report.raw_cut_digest
def test_predicates_and_missing_policy_are_declarative() -> None:
    rows = (
        record(0, {"provider": "openai", "usage": {"total_tokens": 11}}, "raw-a"),
        record(1, {"provider": "local"}, "raw-b"),
        record(2, {"provider": "openai", "usage": {"total_tokens": 9}}, "raw-c"),
    )
    definition = MetricDefinition(
        "openai_total_tokens",
        MetricAggregation.SUM,
        ("llm.usage",),
        value_path=("usage", "total_tokens"),
        predicates=(MetricPredicate(("provider",), "openai"),),
        missing=MetricMissingPolicy.FAIL,
    )
    report = MetricEngine.evaluate(rows, (definition,))
    assert report.values[0].value == 20.0
    assert report.values[0].sample_size == 2


def test_missing_value_can_be_zero_filled() -> None:
    rows = (
        record(0, {"usage": {"total_tokens": 4}}, "raw-a"),
        record(1, {}, "raw-b"),
    )
    definition = MetricDefinition(
        "total_tokens",
        MetricAggregation.SUM,
        ("llm.usage",),
        value_path=("usage", "total_tokens"),
        missing=MetricMissingPolicy.ZERO,
    )
    report = MetricEngine.evaluate(rows, (definition,))
    assert report.values[0].value == 4.0
    assert report.values[0].sample_size == 2

def test_lossless_binary_and_extensible_dimensions_are_queryable() -> None:
    raw = b"\\x00\\xffprovider-response\\x00"
    item = RawRecord(
        "project", "run", "unit", 0, "2026-09-05T00:00:00Z",
        "2026-09-05T00:00:01Z", "model-adapter", "llm.response.v2",
        "llm.response", raw, {"text": "ok"},
        stream_id="model-stream", attempt_id="attempt-3",
        causation_id="parent", correlation_id="corr-1",
        trace_id="trace-1", span_id="span-1", event_name="response.received",
        operation_id="op-1", status="success", outcome="completed",
        monotonic_ns=100, clock_source="perf_counter",
        clock_uncertainty_ns=5, producer_version="adapter-2",
        producer_instance_id="worker-7",
        dimensions={
            "model": {"provider": "openai", "deployment": "gpt-test"},
            "usage": {"input_tokens": 11, "output_tokens": 7, "cached_tokens": 2},
            "timing": {"queue_ms": 3, "network_ms": 8, "compute_ms": 21},
            "resource": {"gpu_memory_bytes": 4096},
            "cost": {"amount": 0.002, "currency": "USD"},
            "artifacts": {"request_ref": "artifact://request/1"},
            "environment": {"os": "windows", "python": "3.12"},
            "quality": {"valid": True},
            "custom": {"future_dimension": "kept"},
        },
        source_location={"host": "lab-1", "process_id": 42},
        privacy={"classification": "internal", "redacted": False},
        content_type="application/octet-stream", content_encoding="identity",
    )
    assert item.raw_payload == raw
    assert item.raw_payload_digest
    assert item.dimensions["usage"]["cached_tokens"] == 2
    metric = MetricDefinition(
        "gpu_bytes", MetricAggregation.SUM,
        value_path=("dimensions", "resource", "gpu_memory_bytes"),
    )
    assert MetricEngine.evaluate((item,), (metric,)).values[0].value == 4096.0
