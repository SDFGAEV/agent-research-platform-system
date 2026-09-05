from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib

from noetrium_platform.foundation.kernel.kernel import (
    ExecutionContext,
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


class EmbodimentKind(StrEnum):
    ROBOT_ARM = "robot_arm"
    MOBILE_MANIPULATOR = "mobile_manipulator"
    HUMANOID = "humanoid"
    QUADRUPED = "quadruped"
    SIMULATED = "simulated"
    OTHER = "other"


class SensorModality(StrEnum):
    RGB = "rgb"
    DEPTH = "depth"
    RGBD = "rgbd"
    TACTILE = "tactile"
    PROPRIOCEPTION = "proprioception"
    AUDIO = "audio"
    FORCE_TORQUE = "force_torque"
    EVENT = "event"
    OTHER = "other"


class ActionKind(StrEnum):
    JOINT = "joint"
    CARTESIAN = "cartesian"
    GRIPPER = "gripper"
    BASE = "base"
    WAYPOINT = "waypoint"
    OTHER = "other"


class EmbodiedEventKind(StrEnum):
    EPISODE_START = "episode_start"
    OBSERVATION = "observation"
    ACTION_COMMAND = "action_command"
    ACTION_RESULT = "action_result"
    REWARD = "reward"
    CHECKPOINT = "checkpoint"
    TERMINATION = "termination"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SensorSpec:
    sensor_id: str
    modality: SensorModality
    frame_id: str
    dtype: str
    shape: tuple[int, ...] = ()
    rate_hz: float = 0.0
    unit: str = ""
    calibration_digest: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (("sensor_id", self.sensor_id), ("frame_id", self.frame_id), ("dtype", self.dtype)):
            _text(name, value)
        if any(type(item) is not int or item < 0 for item in self.shape):
            raise ValueError("sensor shape must contain non-negative integers")
        if type(self.rate_hz) not in (int, float) or self.rate_hz < 0:
            raise ValueError("sensor rate_hz must be non-negative")
        if not isinstance(self.modality, SensorModality):
            raise TypeError("sensor modality must use SensorModality")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "sensor_id": self.sensor_id,
            "modality": self.modality.value,
            "frame_id": self.frame_id,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "rate_hz": self.rate_hz,
            "unit": self.unit,
            "calibration_digest": self.calibration_digest,
            "metadata": thaw_json(self.metadata),
        }



@dataclass(frozen=True, slots=True)
class ActionSpec:
    action_id: str
    kind: ActionKind
    dimensions: int
    dtype: str = "float32"
    rate_hz: float = 0.0
    unit: str = ""
    bounds: tuple[tuple[float, float], ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("action_id", self.action_id)
        _text("dtype", self.dtype)
        if not isinstance(self.kind, ActionKind):
            raise TypeError("action kind must use ActionKind")
        if type(self.dimensions) is not int or self.dimensions < 0:
            raise ValueError("action dimensions must be a non-negative integer")
        if type(self.rate_hz) not in (int, float) or self.rate_hz < 0:
            raise ValueError("action rate_hz must be non-negative")
        if self.bounds and len(self.bounds) != self.dimensions:
            raise ValueError("action bounds must match dimensions")
        for lower, upper in self.bounds:
            if lower > upper:
                raise ValueError("action bounds must be ordered")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "dimensions": self.dimensions,
            "dtype": self.dtype,
            "rate_hz": self.rate_hz,
            "unit": self.unit,
            "bounds": [list(item) for item in self.bounds],
            "metadata": thaw_json(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EmbodimentSpec:
    embodiment_id: str
    revision: str
    kind: EmbodimentKind
    sensors: tuple[SensorSpec, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    root_frame: str = "world"
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("embodiment_id", self.embodiment_id)
        _text("revision", self.revision)
        _text("root_frame", self.root_frame)
        if not isinstance(self.kind, EmbodimentKind):
            raise TypeError("embodiment kind must use EmbodimentKind")
        if len({item.sensor_id for item in self.sensors}) != len(self.sensors):
            raise ValueError("embodiment sensor ids must be unique")
        if len({item.action_id for item in self.actions}) != len(self.actions):
            raise ValueError("embodiment action ids must be unique")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "embodiment_id": self.embodiment_id,
            "revision": self.revision,
            "kind": self.kind.value,
            "sensors": [item.record() for item in self.sensors],
            "actions": [item.record() for item in self.actions],
            "root_frame": self.root_frame,
            "metadata": thaw_json(self.metadata),
        }

    @property
    def spec_digest(self) -> str:
        return canonical_digest(self.record())



@dataclass(frozen=True, slots=True)
class EpisodeSpec:
    episode_id: str
    environment_id: str
    embodiment_id: str
    task_id: str
    seed: int | None = None
    scenario_id: str = ""
    policy_id: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("episode_id", self.episode_id),
            ("environment_id", self.environment_id),
            ("embodiment_id", self.embodiment_id),
            ("task_id", self.task_id),
        ):
            _text(name, value)
        if self.seed is not None and type(self.seed) is not int:
            raise TypeError("episode seed must be an integer or None")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "episode_id": self.episode_id,
            "environment_id": self.environment_id,
            "embodiment_id": self.embodiment_id,
            "task_id": self.task_id,
            "seed": self.seed,
            "scenario_id": self.scenario_id,
            "policy_id": self.policy_id,
            "metadata": thaw_json(self.metadata),
        }

    @property
    def spec_digest(self) -> str:
        return canonical_digest(self.record())


@dataclass(frozen=True, slots=True)
class EmbodiedActionCommand:
    command_id: str
    episode_id: str
    action_id: str
    sequence: int
    raw_payload: bytes
    normalized_payload: JsonInput
    issued_at_ns: int
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (("command_id", self.command_id), ("episode_id", self.episode_id), ("action_id", self.action_id)):
            _text(name, value)
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("action sequence must be positive")
        if type(self.raw_payload) is not bytes:
            raise TypeError("action raw_payload must be exact bytes")
        if type(self.issued_at_ns) is not int or self.issued_at_ns <= 0:
            raise ValueError("action issued_at_ns must be positive")
        object.__setattr__(self, "normalized_payload", freeze_json(self.normalized_payload))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    @property
    def raw_payload_sha256(self) -> str:
        return hashlib.sha256(self.raw_payload).hexdigest()



@dataclass(frozen=True, slots=True)
class EmbodiedEvent:
    event_id: str
    episode_id: str
    sequence: int
    kind: EmbodiedEventKind
    event_time_ns: int
    raw_payload: bytes
    normalized_payload: JsonObject
    source_id: str
    embodiment_id: str
    environment_id: str
    task_id: str
    step_index: int | None = None
    sensor_id: str = ""
    action_id: str = ""
    status: str = "ok"
    outcome: str | None = None
    terminated: bool = False
    truncated: bool = False
    dimensions: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("event_id", self.event_id),
            ("episode_id", self.episode_id),
            ("source_id", self.source_id),
            ("embodiment_id", self.embodiment_id),
            ("environment_id", self.environment_id),
            ("task_id", self.task_id),
            ("status", self.status),
        ):
            _text(name, value)
        if not isinstance(self.kind, EmbodiedEventKind):
            raise TypeError("event kind must use EmbodiedEventKind")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("event sequence must be positive")
        if type(self.event_time_ns) is not int or self.event_time_ns <= 0:
            raise ValueError("event_time_ns must be positive")
        if type(self.raw_payload) is not bytes:
            raise TypeError("event raw_payload must be exact bytes")
        if self.step_index is not None and (type(self.step_index) is not int or self.step_index < 0):
            raise ValueError("event step_index must be non-negative or None")
        if type(self.terminated) is not bool or type(self.truncated) is not bool:
            raise TypeError("event terminated/truncated must be booleans")
        object.__setattr__(self, "normalized_payload", freeze_json(self.normalized_payload))
        object.__setattr__(self, "dimensions", freeze_json(self.dimensions))

    @property
    def raw_payload_sha256(self) -> str:
        return hashlib.sha256(self.raw_payload).hexdigest()

    @property
    def event_digest(self) -> str:
        return canonical_digest({
            "event_id": self.event_id,
            "episode_id": self.episode_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "event_time_ns": self.event_time_ns,
            "raw_payload_sha256": self.raw_payload_sha256,
            "source_id": self.source_id,
            "embodiment_id": self.embodiment_id,
            "environment_id": self.environment_id,
            "task_id": self.task_id,
            "step_index": self.step_index,
        })


@dataclass(frozen=True, slots=True)
class EmbodiedCaptureReceipt:
    event_id: str
    episode_id: str
    sequence: int
    family: str
    record_sha256: str
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        for name, value in (("event_id", self.event_id), ("episode_id", self.episode_id), ("family", self.family)):
            _text(name, value)
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("capture sequence must be positive")
        for name, value in (("record_sha256", self.record_sha256), ("raw_payload_sha256", self.raw_payload_sha256)):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be lowercase SHA-256")


__all__ = [
    "ActionKind", "ActionSpec", "EmbodiedActionCommand", "EmbodiedCaptureReceipt",
    "EmbodiedEvent", "EmbodiedEventKind", "EmbodimentKind", "EmbodimentSpec",
    "EpisodeSpec", "SensorModality", "SensorSpec",
]
