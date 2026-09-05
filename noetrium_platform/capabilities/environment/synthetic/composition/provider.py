from __future__ import annotations

from dataclasses import dataclass

from noetrium_platform.capabilities.environment.api import (
    EnvironmentCapability,
    EnvironmentProviderCapabilities,
)
from noetrium_platform.capabilities.environment.runtime.api import (
    StateMachineDynamicsPort,
    StateMachineEnvironmentSpec,
)
from noetrium_platform.capabilities.environment.runtime.composition import (
    StateMachineEnvironmentAssembly,
    compose_state_machine_environment,
)

from ..api import SyntheticEnvironmentSpec


@dataclass(frozen=True, slots=True)
class SyntheticEnvironmentAssembly:
    provider: StateMachineEnvironmentAssembly

    @property
    def identity(self):
        return self.provider.identity

    @property
    def capabilities(self) -> EnvironmentProviderCapabilities:
        return self.provider.capabilities

    def open_session(self, *, session_id: str, services: object):
        return self.provider.open_session(session_id=session_id, services=services)


def compose_synthetic_environment(
    spec: SyntheticEnvironmentSpec,
    *,
    dynamics: StateMachineDynamicsPort,
) -> SyntheticEnvironmentAssembly:
    machine_spec = StateMachineEnvironmentSpec(
        environment_id=spec.environment_id,
        dynamics=dynamics.identity,
        initial_state=spec.initial_state,
        action_types=spec.action_types,
        implementation_version=spec.revision,
        abi_version="environment.synthetic.v1",
        schema_version="1",
    )
    return SyntheticEnvironmentAssembly(
        compose_state_machine_environment(machine_spec, dynamics=dynamics)
    )


__all__ = ["SyntheticEnvironmentAssembly", "compose_synthetic_environment"]
