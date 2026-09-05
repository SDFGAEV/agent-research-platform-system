from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
from contextlib import suppress
import json
from collections.abc import Mapping
import ssl
import time
from threading import Lock
from urllib.parse import urlsplit

from noetrium_platform.capabilities.model.serving.api import (
    ModelAdmissionClosed,
    ModelAdmissionLeasePort,
    ModelAdmissionPort,
    ModelAdmissionTimeout,
)
from noetrium_platform.capabilities.model.serving.endpoint.api import (
    AsyncJsonHttpTransportPort,
    JsonHttpResponse,
    ModelEndpointError,
    ModelEndpointObserverPort,
    ModelEndpointPort,
    ModelEndpointRequest,
    ModelEndpointResponse,
    ModelEndpointRoute,
)
from noetrium_platform.foundation.kernel.concurrency.api import (
    Deadline,
    ExecutionLaneKind,
    ExecutionSpec,
    TaskCancelled,
    TaskDeadlineExceeded,
    TaskFailureScope,
    TaskGroupPort,
)
from noetrium_platform.foundation.kernel.kernel import canonical_bytes, canonical_digest


def _error_detail(body: object) -> str:
    if isinstance(body, Mapping):
        for key in ("message", "detail", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:512]
    return f"response_body_digest={canonical_digest(body)}"


class AsyncioJsonTransport(AsyncJsonHttpTransportPort):
    """Dependency-free async HTTP/1.1 JSON transport.

    Each request owns one connection and therefore needs no hidden connection
    pool lifecycle.  Concurrency, deadline and cancellation are provided by the
    platform ASYNC_IO lane; this provider only implements protocol I/O.
    """

    def __init__(
        self,
        *,
        headers: tuple[tuple[str, str], ...] = (),
        max_header_bytes: int = 64 * 1024,
        max_response_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        if max_header_bytes <= 0 or max_response_bytes <= 0:
            raise ValueError("HTTP transport limits must be positive")
        self._headers = (("Content-Type", "application/json"), ("Accept", "application/json"), *headers)
        self._max_header_bytes = int(max_header_bytes)
        self._max_response_bytes = int(max_response_bytes)
        self._ssl_context = ssl.create_default_context()

    async def _read_headers(self, reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
        consumed = 0
        status_line = await reader.readline()
        consumed += len(status_line)
        if not status_line or consumed > self._max_header_bytes:
            raise ModelEndpointError("model endpoint HTTP status line is missing or oversized")
        try:
            version, status_text, _reason = status_line.decode("iso-8859-1").rstrip("\r\n").split(" ", 2)
            if not version.startswith("HTTP/"):
                raise ValueError
            status = int(status_text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelEndpointError("model endpoint HTTP status line is malformed") from exc

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            consumed += len(line)
            if consumed > self._max_header_bytes:
                raise ModelEndpointError("model endpoint HTTP headers exceed configured limit")
            if line in {b"\r\n", b"\n", b""}:
                break
            try:
                name, value = line.decode("iso-8859-1").split(":", 1)
            except (UnicodeDecodeError, ValueError) as exc:
                raise ModelEndpointError("model endpoint HTTP header is malformed") from exc
            key = name.strip().lower()
            normalized = value.strip()
            headers[key] = f"{headers[key]},{normalized}" if key in headers else normalized
        return status, headers

    async def _read_chunked(self, reader: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            line = await reader.readline()
            if not line:
                raise ModelEndpointError("model endpoint chunked response ended before terminator")
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise ModelEndpointError("model endpoint chunk size is malformed") from exc
            if size < 0:
                raise ModelEndpointError("model endpoint chunk size is invalid")
            if size == 0:
                # Consume trailers.  They are not semantically relevant here.
                while True:
                    trailer = await reader.readline()
                    if trailer in {b"\r\n", b"\n", b""}:
                        return b"".join(chunks)
            total += size
            if total > self._max_response_bytes:
                raise ModelEndpointError("model endpoint HTTP response exceeds configured limit")
            chunk = await reader.readexactly(size)
            if await reader.readexactly(2) != b"\r\n":
                raise ModelEndpointError("model endpoint chunk delimiter is malformed")
            chunks.append(chunk)

    async def _read_body(self, reader: asyncio.StreamReader, headers: Mapping[str, str]) -> bytes:
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer_encoding:
            return await self._read_chunked(reader)
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ModelEndpointError("model endpoint Content-Length is malformed") from exc
            if length < 0 or length > self._max_response_bytes:
                raise ModelEndpointError("model endpoint HTTP response exceeds configured limit")
            return await reader.readexactly(length)
        data = await reader.read(self._max_response_bytes + 1)
        if len(data) > self._max_response_bytes:
            raise ModelEndpointError("model endpoint HTTP response exceeds configured limit")
        return data

    async def post_json(self, url: str, body: dict[str, object], *, timeout_s: float) -> JsonHttpResponse:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ModelEndpointError("model endpoint HTTP URL is invalid")
        if timeout_s <= 0:
            raise ValueError("model endpoint HTTP timeout must be positive")
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        host_header = host if parsed.port is None else f"{host}:{port}"
        headers = {
            "Host": host_header,
            "Content-Length": str(len(encoded)),
            "Connection": "close",
            **dict(self._headers),
        }
        request = (
            f"POST {target} HTTP/1.1\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in headers.items())
            + "\r\n"
        ).encode("iso-8859-1") + encoded

        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(timeout_s):
                ssl_context = self._ssl_context if parsed.scheme == "https" else None
                reader, writer = await asyncio.open_connection(
                    host,
                    port,
                    ssl=ssl_context,
                    server_hostname=host if ssl_context is not None else None,
                )
                writer.write(request)
                await writer.drain()
                status, response_headers = await self._read_headers(reader)
                raw = await self._read_body(reader, response_headers)
        except ModelEndpointError:
            raise
        except (TimeoutError, OSError, asyncio.IncompleteReadError) as exc:
            raise ModelEndpointError(
                f"model endpoint HTTP transport failed: {type(exc).__name__}",
                request_body=encoded,
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()
        try:
            parsed_body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelEndpointError(
                "model endpoint HTTP response is not valid JSON",
                request_body=encoded, response_body=raw,
            ) from exc
        return JsonHttpResponse(status, parsed_body, raw_body=raw, request_body=encoded)


class OpenAICompatibleModelEndpoint(ModelEndpointPort):
    """Strict response adapter whose network I/O is owned by ASYNC_IO."""

    def __init__(
        self,
        *,
        route: ModelEndpointRoute,
        transport: AsyncJsonHttpTransportPort,
        task_group: TaskGroupPort,
        admission: ModelAdmissionPort,
        observers: tuple[ModelEndpointObserverPort, ...] = (),
    ) -> None:
        self._route = route
        self.transport = transport
        self._task_group = task_group
        self._admission = admission
        self._observers = tuple(observers)
        self._sequence_lock = Lock()
        self._sequence = 0

    @property
    def route(self) -> ModelEndpointRoute:
        return self._route

    def _next_task_id(self, request_id: str) -> str:
        with self._sequence_lock:
            self._sequence += 1
            sequence = self._sequence
        return f"model-http:{request_id}:{sequence}"

    def _notify_exchange(
        self,
        request: ModelEndpointRequest,
        response: JsonHttpResponse,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
    ) -> None:
        for observer in self._observers:
            try:
                observer.on_exchange(
                    request, response, started_monotonic_ns, completed_monotonic_ns
                )
            except Exception as exc:
                observer_id = getattr(observer, "observer_id", type(observer).__qualname__)
                raise ModelEndpointError(
                    f"lossless model exchange capture failed: {observer_id}"
                ) from exc

    def _notify_failure(
        self,
        request: ModelEndpointRequest,
        exc: BaseException,
        started_monotonic_ns: int,
        completed_monotonic_ns: int,
    ) -> None:
        for observer in self._observers:
            callback = getattr(observer, "on_failure", None)
            if callable(callback):
                callback(
                    request, type(exc).__name__, str(exc)[:2048],
                    started_monotonic_ns, completed_monotonic_ns,
                    getattr(exc, "request_body", b""),
                    getattr(exc, "response_body", b""),
                )

    async def _post(
        self,
        context,
        request: ModelEndpointRequest,
        lease: ModelAdmissionLeasePort,
    ) -> JsonHttpResponse:
        try:
            context.checkpoint()
            remaining = context.remaining_seconds
            timeout_s = self.route.timeout_s if remaining is None else min(self.route.timeout_s, remaining)
            if timeout_s <= 0:
                context.checkpoint()
                raise TimeoutError("model endpoint deadline expired before transport")
            materialized_body = json.loads(canonical_bytes(request.body))
            if not isinstance(materialized_body, dict):
                raise ModelEndpointError("model endpoint request body materialization drift")
            response = await self.transport.post_json(
                self.route.completion_url,
                materialized_body,
                timeout_s=timeout_s,
            )
            context.checkpoint()
            return response
        finally:
            lease.release()

    def complete(self, request: ModelEndpointRequest) -> ModelEndpointResponse:
        started_monotonic_ns = time.perf_counter_ns()
        try:
            return self._complete(request, started_monotonic_ns)
        except ModelEndpointError as exc:
            self._notify_failure(
                request, exc, started_monotonic_ns, time.perf_counter_ns()
            )
            raise

    def _complete(
        self,
        request: ModelEndpointRequest,
        started_monotonic_ns: int,
    ) -> ModelEndpointResponse:
        if request.deployment_id != self.route.deployment_id:
            raise ModelEndpointError("endpoint request deployment does not match route")
        if request.deployment_generation != self.route.deployment_generation:
            raise ModelEndpointError("endpoint request deployment generation does not match route")
        deadline = Deadline.after(self.route.timeout_s)
        try:
            lease = self._admission.acquire(
                timeout_seconds=max(0.0, deadline.remaining_seconds)
            )
        except ModelAdmissionTimeout as exc:
            raise ModelEndpointError("model endpoint admission timed out") from exc
        except ModelAdmissionClosed as exc:
            raise ModelEndpointError("model endpoint admission is closed") from exc
        try:
            handle = self._task_group.submit(
                ExecutionSpec(
                    task_id=self._next_task_id(request.request.request_id),
                    lane_kind=ExecutionLaneKind.ASYNC_IO,
                    failure_scope=TaskFailureScope.CALLER,
                ),
                self._post,
                request,
                lease,
                deadline=deadline,
            )
        except BaseException:
            lease.release()
            raise
        try:
            response = handle.result(timeout=max(0.001, deadline.remaining_seconds))
        except (TimeoutError, TaskDeadlineExceeded) as exc:
            handle.cancel()
            raise ModelEndpointError("model endpoint HTTP transport failed: TimeoutError") from exc
        except (TaskCancelled, CancelledError) as exc:
            raise ModelEndpointError("model endpoint HTTP transport cancelled at deadline") from exc
        self._notify_exchange(
            request, response, started_monotonic_ns, time.perf_counter_ns()
        )
        if not 200 <= response.status_code < 300:
            raise ModelEndpointError(
                f"model endpoint returned HTTP {response.status_code}: {_error_detail(response.body)}"
            )
        if not isinstance(response.body, Mapping):
            raise ModelEndpointError("model endpoint response body must be an object")
        choices = response.body.get("choices")
        if not isinstance(choices, (tuple, list)) or len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise ModelEndpointError("model endpoint response must contain exactly one choice")
        choice = choices[0]
        message = choice.get("message")
        text = message.get("content") if isinstance(message, Mapping) else choice.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ModelEndpointError("model endpoint choice has no text content")
        usage = response.body.get("usage")
        input_tokens = usage.get("prompt_tokens") if isinstance(usage, Mapping) else None
        output_tokens = usage.get("completion_tokens") if isinstance(usage, Mapping) else None
        if input_tokens is not None and type(input_tokens) is not int:
            raise ModelEndpointError("model endpoint prompt_tokens must be an integer")
        if output_tokens is not None and type(output_tokens) is not int:
            raise ModelEndpointError("model endpoint completion_tokens must be an integer")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise ModelEndpointError("model endpoint finish_reason must be text")
        return ModelEndpointResponse(
            request_id=request.request.request_id,
            deployment_id=request.deployment_id,
            text=text,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage=usage if isinstance(usage, Mapping) else None,
        )


__all__ = ["AsyncioJsonTransport", "OpenAICompatibleModelEndpoint"]
