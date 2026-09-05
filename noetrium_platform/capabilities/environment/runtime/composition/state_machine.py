from __future__ import annotations

from dataclasses import dataclass

from noetrium_platform.capabilities.environment.api.provider import (
    EnvironmentProviderCapabilities,
    EnvironmentSessionServices,
)

from ..api import StateMachineDynamicsPort, StateMachineEnvironmentSpec
from ..runtime.state_machine import (
    StateMachineEnvironmentImplementation,
    StateMachineEnvironmentRuntime,
)


@dataclass(frozen=True, slots=True)
class StateMachineEnvironmentAssembly:
    implementation: StateMachineEnvironmentImplementation
    runtime: StateMachineEnvironmentRuntime

    @property
    def identity(self):
        return self.implementation.identity

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities:
        from noetrium_platform.capabilities.environment.api import EnvironmentCapability
        return EnvironmentProviderCapabilities((
            EnvironmentCapability.SNAPSHOT,
            EnvironmentCapability.RESTORE,
            EnvironmentCapability.RECONCILE,
            EnvironmentCapability.DIAGNOSTICS,
            EnvironmentCapability.QUERY,
        ))

    def open_session(
        self,
        *,
        session_id: str,
        services: EnvironmentSessionServices,
    ):
        return self.runtime.open_session(
            self.implementation,
            session_id=session_id,
            services=services,
        )


def compose_state_machine_environment(
    spec: StateMachineEnvironmentSpec,
    *,
    dynamics: StateMachineDynamicsPort,
) -> StateMachineEnvironmentAssembly:
    implementation = StateMachineEnvironmentImplementation(spec, dynamics)
    return StateMachineEnvironmentAssembly(
        implementation=implementation,
        runtime=StateMachineEnvironmentRuntime(),
    )


__all__ = ["StateMachineEnvironmentAssembly", "compose_state_machine_environment"]
