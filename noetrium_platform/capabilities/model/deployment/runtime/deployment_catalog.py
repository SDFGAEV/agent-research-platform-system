from __future__ import annotations

from dataclasses import replace

from noetrium_platform.capabilities.model.deployment.api import (
    ModelDeploymentSelector,
    ModelDeploymentSpec,
    ModelDesiredState,
)
from noetrium_platform.infrastructure.lifecycle.python.api import PythonEnvironmentLookupPort

from noetrium_platform.capabilities.model.asset.api import ModelAssetLookupPort
from .deployment_registry import ModelDeploymentRegistry


class ModelDeploymentCatalog:
    """Mutable desired deployment configuration authority."""

    def __init__(
        self,
        asset_registry: ModelAssetLookupPort,
        deployment_registry: ModelDeploymentRegistry,
        python_environments: PythonEnvironmentLookupPort,
    ) -> None:
        self._asset_registry = asset_registry
        self._deployment_registry = deployment_registry
        self._python_environments = python_environments

    def put_deployment(self, spec: ModelDeploymentSpec) -> ModelDeploymentSpec:
        self._asset_registry.get(spec.model_id)
        if spec.python_environment_id is not None:
            self._python_environments.get(spec.python_environment_id)
        normalized = replace(spec, tags=tuple(sorted({tag.strip() for tag in spec.tags if tag.strip()})))
        return self._deployment_registry.put(normalized)

    def deployment(self, deployment_id: str) -> ModelDeploymentSpec:
        return self._deployment_registry.get(deployment_id)

    def deployments(self) -> tuple[ModelDeploymentSpec, ...]:
        return self._deployment_registry.all()

    def select(self, selector: ModelDeploymentSelector = ModelDeploymentSelector()) -> tuple[ModelDeploymentSpec, ...]:
        required_tags = set(selector.tags)
        values = []
        for spec in self.deployments():
            if required_tags and not required_tags.issubset(spec.tags):
                continue
            if selector.model_id is not None and spec.model_id != selector.model_id:
                continue
            if selector.engine is not None and spec.engine != selector.engine:
                continue
            if selector.python_environment_id is not None and spec.python_environment_id != selector.python_environment_id:
                continue
            values.append(spec)
        return tuple(values)

    def set_desired_state_selected(
        self, selector: ModelDeploymentSelector, state: ModelDesiredState
    ) -> tuple[ModelDeploymentSpec, ...]:
        return tuple(self.set_desired_state(spec.deployment_id, state) for spec in self.select(selector))

    def set_desired_state(self, deployment_id: str, state: ModelDesiredState) -> ModelDeploymentSpec:
        return self._deployment_registry.put(replace(self.deployment(deployment_id), desired_state=state))

    def set_gpu_devices(self, deployment_id: str, gpu_devices: tuple[str, ...]) -> ModelDeploymentSpec:
        normalized = tuple(dict.fromkeys(str(device).strip() for device in gpu_devices if str(device).strip()))
        return self.put_deployment(replace(self.deployment(deployment_id), gpu_devices=normalized))

    def set_python_environment(self, deployment_id: str, environment_id: str | None) -> ModelDeploymentSpec:
        return self.put_deployment(replace(self.deployment(deployment_id), python_environment_id=environment_id))

    def remove(self, deployment_id: str) -> bool:
        return self._deployment_registry.remove(deployment_id)


__all__ = ["ModelDeploymentCatalog"]
