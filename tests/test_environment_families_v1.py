from __future__ import annotations

from noetrium_platform.capabilities.environment.api import (
    ActionRequest,
    EnvironmentCapability,
    EnvironmentQuery,
    ExecutionContext,
)
from noetrium_platform.capabilities.environment.benchmark.api import (
    BenchmarkCase,
)
from noetrium_platform.capabilities.environment.benchmark.composition import (
    BenchmarkEnvironmentProviderAdapter,
)
from noetrium_platform.capabilities.environment.replay.api import ReplayEvent, ReplayTrace
from noetrium_platform.capabilities.environment.replay.composition import ReplayEnvironmentProvider
from noetrium_platform.capabilities.environment.synthetic.api import SyntheticEnvironmentSpec
from noetrium_platform.capabilities.environment.synthetic.composition import compose_synthetic_environment
from noetrium_platform.capabilities.environment.runtime.api import (
    StateMachineDynamicsIdentity,
    StateTransition,
)
from noetrium_platform.capabilities.environment.composition import reference_counter_environment
from noetrium_platform.foundation.kernel.kernel import canonical_digest


def context() -> ExecutionContext:
    return ExecutionContext("run-family", "trace-family", "span-family", task_id="task-family")


def test_replay_is_lossless_read_only_and_checkpointable() -> None:
    trace = ReplayTrace(
        "trace-1",
        "recorded-world",
        (
            ReplayEvent("event-1", 1, "observation", b"raw-1", {"value": 1}),
            ReplayEvent("event-2", 2, "observation", b"raw-2", {"value": 2}),
        ),
    )
    provider = ReplayEnvironmentProvider(trace)
    assert provider.capabilities.supports(EnvironmentCapability.REPLAY)
    session = provider.open_session(session_id="replay-1", services=object())
    first = session.observe(context())
    checkpoint = session.checkpoint()
    second = session.observe(context())
    session.restore(checkpoint)
    assert session.observe(context()).observation_id == second.observation_id
    assert first.payload["raw_payload_sha256"] == ReplayEvent(
        "event-1", 1, "observation", b"raw-1", {"value": 1}
    ).raw_payload_sha256
    result = session.act(ActionRequest("replay-action", "ignored", {}, context()))
    assert result.accepted is False
    assert result.effect is not None
    assert result.effect.certainty.value == "no_effect"
    session.close()


class IncrementDynamics:
    identity = StateMachineDynamicsIdentity(
        "increment-dynamics",
        "1",
        canonical_digest({"dynamics": "increment"}),
    )

    def transition(self, state, request, context):
        del context
        amount = int(request.payload.get("amount", 1))
        current = int(state.get("value", 0))
        return StateTransition(
            {"value": current + amount},
            True,
            {"amount": amount},
        )


def test_synthetic_is_deterministic_and_exposes_generic_queries() -> None:
    spec = SyntheticEnvironmentSpec(
        "synthetic-counter",
        "1",
        {"value": 0},
        ("increment",),
    )
    assembly = compose_synthetic_environment(spec, dynamics=IncrementDynamics())
    session = assembly.open_session(session_id="synthetic-1", services=object())
    session.act(ActionRequest("a-1", "increment", {"amount": 3}, context()))
    state = session.query(EnvironmentQuery("q-1", "state", {}, context()))
    assert state.supported is True
    assert state.payload["state"]["value"] == 3
    assert assembly.capabilities.supports(EnvironmentCapability.QUERY)
    session.close()


def test_benchmark_adapter_seals_case_identity_around_external_provider() -> None:
    case = BenchmarkCase("suite-1", "case-1", "task-1", "scenario-1", seed=7)
    class Factory:
        def create(self, value):
            del value
            return reference_counter_environment()

    provider = BenchmarkEnvironmentProviderAdapter(Factory(), case)
    assert provider.identity.environment_id == "benchmark:suite-1:case-1"
    assert provider.identity.artifact_digest == case.case_digest
    session = provider.open_session(session_id="episode-1", services=object())
    result = session.act(
        ActionRequest(
            "action-1",
            "increment",
            {"amount": 2},
            context(),
        )
    )
    assert result.action_id == "action-1"
    diagnostics = session.diagnostics_snapshot()
    assert diagnostics.environment == provider.identity
    session.close()
