from __future__ import annotations

from pathlib import Path
import base64
import tempfile

from noetrium_platform.capabilities.environment.api import (
    ActionRequest,
    EnvironmentProviderPort,
    ExecutionContext,
)
from noetrium_platform.capabilities.environment.embodied.api import (
    ActionKind,
    ActionSpec,
    EmbodiedActionCommand,
    EmbodiedEnvironmentPort,
    EmbodiedEvent,
    EmbodiedEventKind,
    EmbodimentKind,
    EmbodimentSpec,
    EpisodeSpec,
    SensorModality,
    SensorSpec,
)
from noetrium_platform.capabilities.environment.embodied.composition import (
    EmbodiedEnvironmentProviderAdapter,
    RegistryBoundEmbodiedTrajectorySink,
)
from noetrium_platform.evidence.observability.capture.runtime import (
    RegistryBoundRawObservationGateway,
)
from noetrium_platform.foundation.governance.system_registry.runtime import (
    build_default_system_registry,
)
from tests._concurrency_support import drain_test_concurrency_runtimes, raw_observation_lake


class FakeEmbodiedEnvironment:
    def __init__(self) -> None:
        self._spec = EmbodimentSpec(
            embodiment_id="arm-a",
            revision="v1",
            kind=EmbodimentKind.ROBOT_ARM,
            sensors=(SensorSpec("camera", SensorModality.RGB, "head", "uint8", (2, 2, 3), 10.0),),
            actions=(ActionSpec("joint", ActionKind.JOINT, 2, bounds=((-1.0, 1.0), (-2.0, 2.0))),),
        )
        self.closed = False

    @property
    def spec(self) -> EmbodimentSpec:
        return self._spec

    def reset(self, episode: EpisodeSpec, context: ExecutionContext) -> tuple[EmbodiedEvent, ...]:
        del context
        return (self._event(episode, 1, EmbodiedEventKind.OBSERVATION, b"raw-reset"),)

    def step(self, command: EmbodiedActionCommand, context: ExecutionContext) -> tuple[EmbodiedEvent, ...]:
        del context
        episode = EpisodeSpec(command.episode_id, "sim", "arm-a", "task")
        return (self._event(episode, command.sequence + 1, EmbodiedEventKind.ACTION_RESULT, command.raw_payload),)

    def close(self) -> None:
        self.closed = True

    def _event(self, episode: EpisodeSpec, sequence: int, kind: EmbodiedEventKind, raw: bytes) -> EmbodiedEvent:
        return EmbodiedEvent(
            event_id=f"{episode.episode_id}:{sequence}",
            episode_id=episode.episode_id,
            sequence=sequence,
            kind=kind,
            event_time_ns=sequence,
            raw_payload=raw,
            normalized_payload={"value": sequence},
            source_id="fake",
            embodiment_id=episode.embodiment_id,
            environment_id=episode.environment_id,
            task_id=episode.task_id,
        )


def test_embodied_spec_and_event_are_stable_and_lossless() -> None:
    env = FakeEmbodiedEnvironment()
    assert env.spec.spec_digest == env.spec.spec_digest
    event = env.reset(EpisodeSpec("e", "sim", "arm-a", "task"), ExecutionContext("r", "t", "s"))[0]
    assert event.raw_payload == b"raw-reset"
    assert len(event.raw_payload_sha256) == 64
    assert isinstance(env, EmbodiedEnvironmentPort)



def test_embodied_provider_is_generic_to_upstream_and_captures_raw_events() -> None:
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lake = raw_observation_lake(root)
            systems = build_default_system_registry()
            gateway = RegistryBoundRawObservationGateway(lake, systems)
            sink = RegistryBoundEmbodiedTrajectorySink(gateway, producer_id="fake.embodied")
            provider = EmbodiedEnvironmentProviderAdapter(
                FakeEmbodiedEnvironment(),
                environment_id="simulated-arm",
                implementation_version="1",
                trajectory_sink=sink,
            )
            assert isinstance(provider, EnvironmentProviderPort)
            assert systems.contains("environment/embodied")
            context = ExecutionContext("run-embodied", "trace", "span")
            session = provider.open_session(session_id="episode-1", services=object())
            observation = session.observe(context)
            result = session.act(
                ActionRequest(
                    action_id="a-1",
                    action_type="joint",
                    payload={"joint": [0.1, 0.2]},
                    context=context,
                )
            )
            assert observation.observation_id == "episode-1:1"
            assert result.accepted
            rows = lake.read("run-embodied", "embodied.trajectory.raw")
            assert len(rows) == 2
            capture = rows[0]["payload"]["__capture"]
            assert base64.b64decode(capture["raw_payload_b64"]) == b"raw-reset"
            assert capture["system"] == "environment/embodied"
            session.close()
            lake.close()
    finally:
        drain_test_concurrency_runtimes()


def test_embodied_raw_capture_uses_registered_topology() -> None:
    try:
        with tempfile.TemporaryDirectory() as td:
            lake = raw_observation_lake(Path(td))
            gateway = RegistryBoundRawObservationGateway(lake, build_default_system_registry())
            sink = RegistryBoundEmbodiedTrajectorySink(gateway, producer_id="fake.embodied")
            event = FakeEmbodiedEnvironment().reset(
                EpisodeSpec("e", "sim", "arm-a", "task"),
                ExecutionContext("r", "t", "s"),
            )[0]
            assert sink.capture(event, ExecutionContext("r", "t", "s")).family == "embodied.trajectory.raw"
            lake.close()
    finally:
        drain_test_concurrency_runtimes()
