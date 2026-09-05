from __future__ import annotations

from noetrium_platform.capabilities.model.serving.api import ModelAdmissionRegistryPort
from noetrium_platform.capabilities.model.serving.endpoint.api import (
    ModelEndpointPort,
    ModelEndpointRoute,
    QualifiedModelEndpointBinding,
)
from noetrium_platform.foundation.kernel.concurrency.api import TaskGroupPort
from noetrium_platform.capabilities.model.serving.endpoint.providers import (
    OpenAICompatibleModelEndpoint,
    AsyncioJsonTransport,
)


def build_openai_compatible_qualified_endpoint(
    binding: QualifiedModelEndpointBinding,
    *,
    api_key: str = "",
    timeout_s: float | None = None,
    task_group: TaskGroupPort,
    admission_registry: ModelAdmissionRegistryPort,
    observers: tuple[object, ...] = (),
) -> ModelEndpointPort:
    """Materialize one endpoint from a platform-qualified binding."""

    headers: tuple[tuple[str, str], ...] = ()
    if api_key:
        headers = (("Authorization", f"Bearer {api_key}"),)
    admission = admission_registry.controller_for(
        deployment_id=binding.deployment_id,
        deployment_generation=binding.deployment_generation,
        qualified_capacity=binding.max_admitted_concurrency,
    )
    return OpenAICompatibleModelEndpoint(
        route=ModelEndpointRoute(
            deployment_id=binding.deployment_id,
            deployment_generation=binding.deployment_generation,
            base_url=binding.base_url,
            completion_path=binding.completion_path,
            timeout_s=timeout_s or binding.timeout_s,
        ),
        transport=AsyncioJsonTransport(headers=headers),
        task_group=task_group,
        admission=admission,
        observers=observers,
    )


__all__ = ["build_openai_compatible_qualified_endpoint"]
