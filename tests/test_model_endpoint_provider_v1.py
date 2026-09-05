from __future__ import annotations

from uuid import uuid4

import pytest

from noetrium_platform.capabilities.model.request.api import ContentRef, ModelRequestEnvelope
from noetrium_platform.capabilities.model.serving.endpoint import (
    JsonHttpResponse,
    ModelEndpointError,
    ModelEndpointRequest,
    ModelEndpointRoute,
)
from noetrium_platform.capabilities.model.serving.endpoint.providers import OpenAICompatibleModelEndpoint
from noetrium_platform.capabilities.model.serving.runtime import ModelAdmissionController
from noetrium_platform.foundation.kernel.concurrency.api import ExecutionLaneKind
from noetrium_platform.foundation.kernel.kernel import ExecutionContext, ImmutableModelIdentity
from noetrium_platform.foundation.kernel.concurrency.composition import build_concurrency_runtime


class Transport:
    def __init__(self, response: JsonHttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object], float]] = []

    async def post_json(self, url: str, body: dict[str, object], *, timeout_s: float) -> JsonHttpResponse:
        self.calls.append((url, body, timeout_s))
        return self.response


def _envelope(request_id: str = "rq-1") -> ModelRequestEnvelope:
    return ModelRequestEnvelope(
        schema_version="model-request.v1", request_id=request_id,
        context=ExecutionContext("run", "trace", "span"), role="planner",
        model=ImmutableModelIdentity("planner", "qwen", "rev", "sglang", "1", "bfloat16", None, 8192),
        prompt_generation_id="prompt-gen", prompt_id="planner.prompt", prompt_digest="d" * 64,
        request_body=ContentRef("f" * 64, 2, "application/json"),
    )


def _request(*, deployment_id: str = "dep-1", generation: str = "a" * 64) -> ModelEndpointRequest:
    return ModelEndpointRequest(
        request=_envelope(), deployment_id=deployment_id, deployment_generation=generation,
        body={"model": "qwen", "messages": []},
    )


@pytest.fixture
def endpoint_group():
    runtime = build_concurrency_runtime()
    group = runtime.open_task_group(f"test-model-endpoint:{uuid4().hex}")
    try:
        yield runtime, group
    finally:
        runtime.close()


def test_openai_compatible_endpoint_is_bound_to_exact_deployment_route(endpoint_group) -> None:
    runtime, group = endpoint_group
    transport = Transport(JsonHttpResponse(200, {
        "choices": [{"message": {"content": '{"action_type":"wait"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
    }))
    route = ModelEndpointRoute("dep-1", "a" * 64, "http://127.0.0.1:30000")
    endpoint = OpenAICompatibleModelEndpoint(route=route, transport=transport, task_group=group, admission=ModelAdmissionController(1))

    result = endpoint.complete(_request())

    assert result.request_id == "rq-1"
    assert result.deployment_id == "dep-1"
    assert result.output_tokens == 4
    assert result.usage["prompt_tokens"] == 12
    assert len(transport.calls) == 1
    url, body, timeout_s = transport.calls[0]
    assert url == "http://127.0.0.1:30000/v1/chat/completions"
    assert body == {"model": "qwen", "messages": []}
    assert 0.0 < timeout_s <= route.timeout_s
    tasks = runtime.topology_snapshot().groups[0].tasks
    assert len(tasks) == 1
    assert tasks[0].lane_kind is ExecutionLaneKind.ASYNC_IO
    assert tasks[0].execution_done


def test_openai_compatible_endpoint_rejects_route_identity_drift_before_transport(endpoint_group) -> None:
    _runtime, group = endpoint_group
    transport = Transport(JsonHttpResponse(200, {"choices": [{"text": "ok"}]}))
    endpoint = OpenAICompatibleModelEndpoint(
        route=ModelEndpointRoute("dep-1", "a" * 64, "https://model.example"),
        transport=transport,
        task_group=group,
        admission=ModelAdmissionController(1),
    )
    with pytest.raises(ModelEndpointError, match="deployment"):
        endpoint.complete(_request(deployment_id="dep-2"))
    assert transport.calls == []


def test_openai_compatible_endpoint_rejects_ambiguous_response_shape(endpoint_group) -> None:
    _runtime, group = endpoint_group
    transport = Transport(JsonHttpResponse(200, {"choices": []}))
    endpoint = OpenAICompatibleModelEndpoint(
        route=ModelEndpointRoute("dep-1", "a" * 64, "https://model.example"),
        transport=transport,
        task_group=group,
        admission=ModelAdmissionController(1),
    )
    with pytest.raises(ModelEndpointError, match="exactly one choice"):
        endpoint.complete(_request())


def test_openai_compatible_endpoint_preserves_structured_http_error_detail(endpoint_group) -> None:
    _runtime, group = endpoint_group
    transport = Transport(JsonHttpResponse(400, {
        "message": "No user query found in messages.",
        "code": 400,
    }))
    endpoint = OpenAICompatibleModelEndpoint(
        route=ModelEndpointRoute("dep-1", "a" * 64, "https://model.example"),
        transport=transport,
        task_group=group,
        admission=ModelAdmissionController(1),
    )
    with pytest.raises(ModelEndpointError, match="No user query found in messages"):
        endpoint.complete(_request())


def test_endpoint_contract_rejects_opaque_request_and_freezes_http_response() -> None:
    with pytest.raises(TypeError, match="ModelRequestEnvelope"):
        ModelEndpointRequest(
            request=object(), deployment_id="dep-1", deployment_generation="a" * 64,
            body={"model": "qwen", "messages": []},
        )
    body = {"choices": [{"text": "ok"}]}
    response = JsonHttpResponse(200, body)
    body["choices"][0]["text"] = "caller-mutated"
    assert response.body["choices"][0]["text"] == "ok"
    with pytest.raises(TypeError):
        response.body["choices"][0]["text"] = "tampered"
    with pytest.raises(ValueError, match="HTTP status"):
        JsonHttpResponse(600, {"error": "bad"})

class ExchangeObserver:
    observer_id = "raw-ledger"

    def __init__(self) -> None:
        self.exchanges = []

    def on_exchange(self, request, response, started_monotonic_ns, completed_monotonic_ns) -> None:
        self.exchanges.append(
            (request, response, started_monotonic_ns, completed_monotonic_ns)
        )

    def on_failure(
        self, request, error_type, error_message, started_monotonic_ns,
        completed_monotonic_ns, request_body, response_body,
    ) -> None:
        self.exchanges.append(
            (request, error_type, error_message, started_monotonic_ns,
             completed_monotonic_ns, request_body, response_body)
        )


def test_model_endpoint_observer_receives_exact_wire_bodies_and_timing(endpoint_group) -> None:
    runtime, group = endpoint_group
    wire_request = b'{"messages":[],"model":"qwen"}'
    wire_response = b'{"choices":[{"text":"ok"}]}'
    observer = ExchangeObserver()
    transport = Transport(JsonHttpResponse(
        200, {"choices": [{"text": "ok"}]},
        raw_body=wire_response, request_body=wire_request,
    ))
    endpoint = OpenAICompatibleModelEndpoint(
        route=ModelEndpointRoute("dep-1", "a" * 64, "https://model.example"),
        transport=transport, task_group=group, admission=ModelAdmissionController(1),
        observers=(observer,),
    )
    assert endpoint.complete(_request()).text == "ok"
    assert len(observer.exchanges) == 1
    captured_request, captured_response, started, completed = observer.exchanges[0]
    assert captured_request.request.request_id == "rq-1"
    assert captured_response.request_body == wire_request
    assert captured_response.raw_body == wire_response
    assert completed >= started
