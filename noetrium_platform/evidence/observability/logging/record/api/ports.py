from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from noetrium_platform.evidence.observability.logging.context.api import DiagnosticAddress
from noetrium_platform.foundation.governance.system_registry.api import SystemDescriptor, SystemIdentity
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE, ScopeIdentity

from .contracts import LogLevel, LogRecord

from noetrium_platform.foundation.kernel.kernel.errors import SafeExceptionDescriptor
from noetrium_platform.foundation.kernel.kernel import JsonValue


class ExceptionDescriptorPort(Protocol):
    """Exception-policy seam owned by the structured-record leaf."""

    def describe(self, exc: BaseException) -> SafeExceptionDescriptor: ...


class LogWriterPort(Protocol):
    """Deep write interface used by architecture, projects and method internals.

    The writer owns record construction, safe exception description, stable
    identity and correlation references. It does not own storage or failure
    taxonomy truth.
    """

    @property
    def address(self) -> DiagnosticAddress: ...

    def child(
        self,
        *,
        address: DiagnosticAddress | None = None,
        component_id: str | None = None,
    ) -> "LogWriterPort": ...

    def log(
        self,
        level: LogLevel,
        *,
        event: str,
        message: str,
        attributes: Mapping[str, JsonValue] | None = None,
        correlation_refs: tuple[str, ...] = (),
        failure_refs: tuple[str, ...] = (),
        artifact_refs: tuple[str, ...] = (),
    ) -> str: ...

    def exception(
        self,
        *,
        event: str,
        message: str,
        exc: BaseException,
        level: LogLevel = LogLevel.ERROR,
        attributes: Mapping[str, JsonValue] | None = None,
        correlation_refs: tuple[str, ...] = (),
        failure_refs: tuple[str, ...] = (),
    ) -> str: ...

    def failure(
        self,
        *,
        event: str,
        message: str,
        failure_id: str,
        level: LogLevel = LogLevel.ERROR,
        attributes: Mapping[str, JsonValue] | None = None,
        correlation_refs: tuple[str, ...] = (),
    ) -> str: ...


class ObservationBindingPort(Protocol):
    """Minimal topology-bound observation surface exposed by composition."""

    descriptor: SystemDescriptor
    address: DiagnosticAddress
    logger: LogWriterPort
    metrics: object
    topology_generation: int
    topology_digest: str


class ObservationFactoryPort(Protocol):
    """Composition-owned observation factory without an API/runtime edge."""

    def bind_all(
        self,
        *,
        scope: ScopeIdentity = PLATFORM_SCOPE,
        trace_id: str | None = None,
    ) -> tuple[ObservationBindingPort, ...]: ...

    def bindings(self) -> tuple[ObservationBindingPort, ...]: ...


class LoggingSystemPort(Protocol):
    """Binding facade over record, sink and query leaf seams.

    This interface composes those leaves; it does not become their storage or
    query authority. Parent/project composition receives this one port so it
    can bind writers while the final logging view remains queryable.
    """

    def bind(
        self,
        *,
        logger: str,
        address: DiagnosticAddress,
        attributes: Mapping[str, JsonValue] | None = None,
    ) -> LogWriterPort: ...

    def query(
        self,
        *,
        scope: ScopeIdentity | None = None,
        system: SystemIdentity | None = None,
        component_id: str | None = None,
        trace_id: str | None = None,
        level_at_least: LogLevel | None = None,
        event: str | None = None,
        limit: int = 1000,
    ) -> tuple[LogRecord, ...]: ...


__all__ = ["ExceptionDescriptorPort", "LogWriterPort", "LoggingSystemPort"]
