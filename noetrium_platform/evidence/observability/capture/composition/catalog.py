from __future__ import annotations

from ..api.contracts import RawObservationSchema, RetentionClass
from ..runtime.registry import RawObservationRegistry


def build_default_raw_registry() -> RawObservationRegistry:
    registry = RawObservationRegistry()
    R = RetentionClass
    schemas = (
        RawObservationSchema("operation.raw", "1", ("operation_type", "status"), R.RUN_DURABLE, "Cross-component operation envelope including high-cardinality execution identity."),
        RawObservationSchema("llm.request.raw", "1", ("role", "model", "request_digest", "status"), R.RUN_DURABLE, "Logical LLM request/response metadata, token accounting, queueing and contract results."),
        RawObservationSchema("llm.attempt.raw", "1", ("role", "model", "endpoint", "attempt", "status"), R.HOT_DEBUG, "Physical provider attempt, transport details and timing."),
        RawObservationSchema("prompt.compile.raw", "1", ("role", "bundle_digest", "compiled_digest", "status"), R.RUN_DURABLE, "Exact prompt generation, typed-block identities, budget and compile outcome."),
        RawObservationSchema("prompt.trace.raw", "1", ("request_id", "role", "model", "request_digest", "stage"), R.RUN_DURABLE, "Per-request Prompt/LLM stage trace with exact high-cardinality request identity."),
        RawObservationSchema("model.runtime.raw", "1", ("model", "engine", "replica", "sample_type"), R.HOT_DEBUG, "Serving scheduler, cache, batch and process telemetry sample."),
        RawObservationSchema("host.sample.raw", "1", ("host", "sample_type"), R.HOT_DEBUG, "Host/GPU/NUMA/network/storage sample with raw device identifiers in payload."),
        RawObservationSchema("forensics.raw", "1", ("kind", "object_id"), R.SCIENTIFIC_DURABLE, "Operator/forensic linkage record that must remain joinable to authoritative evidence."),
        RawObservationSchema("method.raw", "1", ("method", "kind"), R.SCIENTIFIC_DURABLE, "Method-side observation, recall/evolution/materialization/adoption evidence metadata."),
        RawObservationSchema("environment.raw", "1", ("environment", "kind"), R.RUN_DURABLE, "Environment action/observation/effect metadata including exact object IDs."),
        RawObservationSchema("embodied.trajectory.raw", "1", ("kind", "episode_id", "status"), R.SCIENTIFIC_DURABLE, "Lossless embodied episode, sensor, action and outcome records."),
        RawObservationSchema("study.raw", "1", ("kind", "status"), R.SCIENTIFIC_DURABLE, "Study/run/task lifecycle and comparability metadata."),
    )
    for schema in schemas:
        registry.register(schema)
    return registry


__all__ = ["build_default_raw_registry"]
