from __future__ import annotations

import math

from noetrium_platform.capabilities.model.serving.api import (
    ModelAdmissionRegistryPort,
    QualifiedDeploymentManifest,
)
from noetrium_platform.capabilities.model.serving.endpoint.api import (
    AsyncJsonHttpTransportPort,
    ModelEndpointPort,
    ModelEndpointRoute,
)
from noetrium_platform.capabilities.model.serving.endpoint.providers import (
    AsyncioJsonTransport,
    OpenAICompatibleModelEndpoint,
)
from noetrium_platform.foundation.kernel.concurrency.api import TaskGroupPort


def build_openai_compatible_runtime_canary_endpoint(
    deployment: QualifiedDeploymentManifest,
    route: ModelEndpointRoute,
    *,
    task_group: TaskGroupPort,
    admission_registry: ModelAdmissionRegistryPort,
    api_key: str = "",
    timeout_s: float | None = None,
    transport: AsyncJsonHttpTransportPort | None = None,
    observers: tuple[object, ...] = (),
) -> ModelEndpointPort:
    """Build a bounded endpoint for live canary execution before closure publication."""
    generation = deployment.digest()
    if route.deployment_id != deployment.deployment_id:
        raise ValueError("runtime canary route deployment identity drift")
    if route.deployment_generation != generation:
        raise ValueError("runtime canary route generation drift")
    if timeout_s is not None:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("runtime canary endpoint timeout_s must be numeric")
        if not math.isfinite(float(timeout_s)) or timeout_s <= 0:
            raise ValueError("runtime canary endpoint timeout_s must be positive and finite")

    if transport is None:
        headers: tuple[tuple[str, str], ...] = ()
        if api_key:
            headers = (("Authorization", f"Bearer {api_key}"),)
        transport = AsyncioJsonTransport(headers=headers)
    elif api_key:
        raise ValueError("api_key cannot be combined with an injected runtime canary transport")

    admission = admission_registry.controller_for(
        deployment_id=deployment.deployment_id,
        deployment_generation=generation,
        qualified_capacity=deployment.certificate.resource_envelope.max_qualified_concurrency,
    )
    return OpenAICompatibleModelEndpoint(
        route=ModelEndpointRoute(
            deployment_id=deployment.deployment_id,
            deployment_generation=generation,
            base_url=route.base_url,
            completion_path=route.completion_path,
            timeout_s=route.timeout_s if timeout_s is None else float(timeout_s),
        ),
        transport=transport,
        task_group=task_group,
        admission=admission,
        observers=observers,
    )


__all__ = ["build_openai_compatible_runtime_canary_endpoint"]
