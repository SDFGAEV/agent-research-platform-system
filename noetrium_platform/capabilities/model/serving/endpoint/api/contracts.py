from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping, Protocol
from urllib.parse import urlparse

from noetrium_platform.capabilities.model.request.api import ModelRequestEnvelope
from noetrium_platform.foundation.kernel.kernel import canonical_digest, freeze_json, JsonInput, JsonValue


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class ModelEndpointRequest:
    """One request sent to an already-bound, qualified model endpoint."""

    request: ModelRequestEnvelope
    deployment_id: str
    deployment_generation: str
    body: Mapping[str, JsonInput]

    def __post_init__(self) -> None:
        if not isinstance(self.request, ModelRequestEnvelope):
            raise TypeError("model endpoint request must carry a ModelRequestEnvelope")
        if not isinstance(self.deployment_id, str) or not self.deployment_id.strip():
            raise ValueError("model endpoint deployment_id is required")
        _require_sha256(self.deployment_generation, "model endpoint deployment_generation")
        if not isinstance(self.body, Mapping):
            raise TypeError("model endpoint request body must be a mapping")
        object.__setattr__(
            self, "body", freeze_json(self.body)
        )

    def digest(self) -> str:
        return canonical_digest({
            "request_envelope": self.request.envelope_digest,
            "deployment_id": self.deployment_id,
            "deployment_generation": self.deployment_generation,
            "body": dict(self.body),
        })


@dataclass(frozen=True, slots=True)
class ModelEndpointRoute:
    """Operational route bound to one qualified deployment identity."""

    deployment_id: str
    deployment_generation: str
    base_url: str
    completion_path: str = "/v1/chat/completions"
    timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if not isinstance(self.deployment_id, str) or not self.deployment_id.strip():
            raise ValueError("model endpoint route requires exact deployment identity")
        try:
            _require_sha256(self.deployment_generation, "model endpoint route deployment_generation")
        except ValueError as exc:
            raise ValueError("model endpoint route requires exact deployment identity") from exc
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model endpoint route base_url must be an absolute HTTP(S) URL")
        if not self.completion_path.startswith("/"):
            raise ValueError("model endpoint completion_path must be absolute")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(float(self.timeout_s))
            or self.timeout_s <= 0
        ):
            raise ValueError("model endpoint timeout_s must be finite and positive")

    @property
    def completion_url(self) -> str:
        return self.base_url.rstrip("/") + self.completion_path


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    """Parsed response with the exact wire bytes retained for observation."""

    status_code: int
    body: JsonValue
    raw_body: bytes = b""
    request_body: bytes = b""

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP status code is invalid")
        if type(self.raw_body) is not bytes or type(self.request_body) is not bytes:
            raise TypeError("HTTP wire bodies must be exact bytes")
        object.__setattr__(self, "body", freeze_json(self.body))


class ModelEndpointObserverPort(Protocol):
    """Non-authoritative hook for lossless model request/response capture."""

    observer_id: str

    def on_exchange(
        self,
        request: ModelEndpointRequest,
        response: JsonHttpResponse,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
    ) -> None:
        ...

    def on_failure(
        self,
        request: ModelEndpointRequest,
        error_type: str,
        error_message: str,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
        request_body: bytes,
        response_body: bytes,
    ) -> None:
        ...


@dataclass(frozen=True, slots=True)
class ModelEndpointResponse:
    """Transport result; scientific meaning is owned by the consuming project."""

    request_id: str
    deployment_id: str
    text: str
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage: JsonValue | None = None
    response_digest: str = ""

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.deployment_id.strip():
            raise ValueError("model endpoint response identity is required")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("model endpoint response text must be non-empty")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be non-negative")
        if self.usage is not None:
            object.__setattr__(self, "usage", freeze_json(self.usage))
            if not isinstance(self.usage, Mapping):
                raise TypeError("model endpoint response usage must be a mapping")
        expected = canonical_digest({
            "request_id": self.request_id,
            "deployment_id": self.deployment_id,
            "text": self.text,
            "finish_reason": self.finish_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usage": self.usage,
        })
        if self.response_digest and self.response_digest != expected:
            raise ValueError("model endpoint response digest mismatch")
        object.__setattr__(self, "response_digest", expected)


class ModelEndpointError(RuntimeError):
    """Transport failure with any wire bytes that were actually received."""

    def __init__(
        self,
        message: str,
        *,
        request_body: bytes = b"",
        response_body: bytes = b"",
    ) -> None:
        super().__init__(message)
        if type(request_body) is not bytes or type(response_body) is not bytes:
            raise TypeError("model endpoint error wire bodies must be exact bytes")
        self.request_body = request_body
        self.response_body = response_body


__all__ = [
    "JsonHttpResponse",
    "ModelEndpointError", "ModelEndpointObserverPort",
    "ModelEndpointRequest",
    "ModelEndpointResponse",
    "ModelEndpointRoute",
]
