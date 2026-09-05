from __future__ import annotations

from dataclasses import replace
from typing import Callable

from noetrium_platform.capabilities.environment.api import (
    EnvironmentCapability,
    EnvironmentIdentity,
    EnvironmentProviderCapabilities,
    EnvironmentProviderPort,
    EnvironmentSession,
    EnvironmentSessionDiagnostics,
)
from noetrium_platform.foundation.kernel.kernel import EffectReceipt, ExecutionContext

from ..api import BenchmarkCase, BenchmarkProviderFactoryPort


class BenchmarkEnvironmentProviderAdapter:
    """One stable environment provider for any external benchmark case."""

    def __init__(
        self,
        factory: BenchmarkProviderFactoryPort,
        case: BenchmarkCase,
    ) -> None:
        self._case = case
        provider = factory.create(case)
        if not isinstance(provider, EnvironmentProviderPort):
            raise TypeError("benchmark factory returned an invalid environment provider")
        self._provider = provider
        self._identity = EnvironmentIdentity(
            environment_id=f"benchmark:{case.suite_id}:{case.case_id}",
            implementation_version=provider.identity.implementation_version,
            abi_version="environment.benchmark.v1",
            schema_version=provider.identity.schema_version,
            artifact_digest=case.case_digest,
        )

    @property
    def identity(self) -> EnvironmentIdentity:
        return self._identity

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities:
        return self._provider.capabilities

    @property
    def case(self) -> BenchmarkCase:
        return self._case

    def open_session(
        self,
        *,
        session_id: str,
        services: object,
    ) -> EnvironmentSession:
        session = self._provider.open_session(
            session_id=session_id,
            services=services,
        )
        return _BenchmarkSession(
            session,
            session_id=session_id,
            identity=self._identity,
        )


class _BenchmarkSession:
    def __init__(
        self,
        session: EnvironmentSession,
        *,
        session_id: str,
        identity: EnvironmentIdentity,
    ) -> None:
        self._session = session
        self._session_id = session_id
        self._identity = identity

    def observe(self, context: ExecutionContext):
        return self._session.observe(context)

    def act(self, request):
        return self._session.act(request)

    def reconcile(self, effect: EffectReceipt, context: ExecutionContext):
        return self._session.reconcile(effect, context)

    def checkpoint(self) -> bytes:
        return self._session.checkpoint()

    def restore(self, payload: bytes) -> None:
        self._session.restore(payload)

    def query(self, request):
        query = getattr(self._session, "query", None)
        if query is None:
            raise AttributeError("benchmark provider does not expose query")
        return query(request)

    def capability_descriptors(self):
        descriptors = getattr(self._session, "capability_descriptors", None)
        if descriptors is None:
            return ()
        return descriptors()

    def diagnostics_snapshot(self) -> EnvironmentSessionDiagnostics:
        diagnostics = getattr(self._session, "diagnostics_snapshot", None)
        if diagnostics is None:
            return EnvironmentSessionDiagnostics(
                session_id=self._session_id,
                environment=self._identity,
                generation=self._identity.artifact_digest,
                ready=True,
                closed=False,
                capabilities=self._identity_capabilities(),
            )
        value = diagnostics()
        if not isinstance(value, EnvironmentSessionDiagnostics):
            raise TypeError("benchmark diagnostics must be typed")
        return replace(value, environment=self._identity)

    def _identity_capabilities(self) -> EnvironmentProviderCapabilities:
        return EnvironmentProviderCapabilities((EnvironmentCapability.DIAGNOSTICS,))

    def close(self) -> None:
        self._session.close()


__all__ = ["BenchmarkEnvironmentProviderAdapter"]
