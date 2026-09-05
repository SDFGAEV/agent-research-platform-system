from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from noetrium_platform.foundation.kernel.concurrency.api import TaskGroupPort
from noetrium_platform.infrastructure.lifecycle.process.supervision.composition import build_local_command_runner, build_process_command_runner

from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayout, DirectoryLayoutPort, DirectoryManagementAuthorities
from noetrium_platform.infrastructure.resources.directory.runtime import build_local_directory_authorities
from noetrium_platform.capabilities.model.api import ModelAuthorities
from noetrium_platform.capabilities.model.deployment.api import ModelDeploymentLogs
from noetrium_platform.capabilities.model.asset.providers import HuggingFaceCliModelSource
from noetrium_platform.capabilities.model.asset.runtime import LocalModelAssetStorage, ModelAssetManager, ModelAssetRegistry
from noetrium_platform.capabilities.model.composition import DeploymentModelAssetReferences
from noetrium_platform.capabilities.model.assignment.runtime import ModelAssignmentManager
from noetrium_platform.capabilities.model.deployment.runtime import (
    AppliedModelDeploymentStore,
    FileModelControllerStateStore,
    ModelDesiredStateController,
    ModelDeploymentCatalog,
    ModelDeploymentLogReader,
    ModelDeploymentRegistry,
    ModelDeploymentRuntime,
    ModelLaunchMaterializer,
    ModelFleetRuntime,
    ModelResourceView,
)
from noetrium_platform.capabilities.model.qualification.composition import (
    DeploymentQualificationAuthorities,
    build_local_deployment_qualification,
)
from noetrium_platform.infrastructure.resources.compute.providers import NvidiaSmiGpuRuntimeObserver
from noetrium_platform.infrastructure.lifecycle.python.api import PythonEnvironmentAuthorities
from noetrium_platform.capabilities.environment.catalog.api import ExecutionEnvironmentCatalogPort
from noetrium_platform.capabilities.environment.catalog.runtime import ExecutionEnvironmentCatalog
from noetrium_platform.foundation.scope.api import ScopeRegistryPort
from noetrium_platform.infrastructure.lifecycle.host.api import OperatingSystemRoute
from noetrium_platform.infrastructure.lifecycle.python.runtime import (
    CondaEnvironmentBackend,
    build_python_environment_authorities,
    SubprocessEnvironmentCommandRunner,
    VenvEnvironmentBackend,
)
from noetrium_platform.infrastructure.lifecycle.service.api import ServiceLaunchContract
from noetrium_platform.infrastructure.lifecycle.service.runtime import (
    DirectoryCapturePathProvider,
    ExactServiceRuntimeEndpoint,
    HttpEndpointReadinessProbe,
    LocalServiceProcessAdapter,
    MaterializedServiceEnvironment,
    ProcessAliveReadinessProbe,
    StaticServiceEnvironmentProvider,
)
from noetrium_platform.infrastructure.lifecycle.service.runtime.start_intent_store import DirectoryServiceStartIntentStore
from noetrium_platform.infrastructure.lifecycle.service.runtime.state_storage import FileServiceStateStore

from noetrium_platform.infrastructure.lifecycle.service.composition import compose_local_process_backend, build_service_supervisor
from noetrium_platform.infrastructure.lifecycle.host.composition import HostComposition, compose_local_host
from noetrium_platform.composition.platform_meta import build_in_memory_platform_meta


@dataclass(frozen=True, slots=True)
class ManagementPlaneAuthorities:
    scopes: ScopeRegistryPort
    directories: DirectoryManagementAuthorities
    execution_environments: ExecutionEnvironmentCatalogPort
    python_environments: PythonEnvironmentAuthorities
    models: ModelAuthorities
    host: HostComposition
    deployment_qualification: DeploymentQualificationAuthorities


class LocalModelServiceRuntimeFactory:
    """Composition-only factory for many independently managed local model services."""

    def __init__(
        self,
        directories: DirectoryLayoutPort,
        *,
        operating_system: OperatingSystemRoute,
        task_group: TaskGroupPort,
    ) -> None:
        self._directories = directories
        self._operating_system = operating_system
        self._task_group = task_group
        self._state_root = directories.layout.state / "model-services"
        self._intent_root = directories.layout.runtime / "model-service-start-intents"
        self._capture_root = directories.layout.logs / "model-services"

    def open(
        self,
        contract: ServiceLaunchContract,
        *,
        environment: tuple[tuple[str, str], ...],
        readiness_url: str | None,
    ) -> ExactServiceRuntimeEndpoint:
        service_key = self._safe(contract.service_id)
        materialized = MaterializedServiceEnvironment(
            variables=environment,
            evidence_ref=f"model-serving-env:{contract.environment_digest}",
        )
        provider = StaticServiceEnvironmentProvider((materialized,))
        backend = compose_local_process_backend(
            self._operating_system,
            task_group=self._task_group,
        )
        readiness = (
            HttpEndpointReadinessProbe(self._task_group, readiness_url)
            if readiness_url
            else ProcessAliveReadinessProbe(self._task_group)
        )
        adapter = LocalServiceProcessAdapter(
            provider,
            DirectoryCapturePathProvider(self._capture_root),
            backend,
            readiness,
        )
        contract_key = contract.digest()
        state = FileServiceStateStore(self._state_root / service_key / contract_key / "state.json")
        intents = DirectoryServiceStartIntentStore(self._intent_root / service_key / contract_key)
        return ExactServiceRuntimeEndpoint(build_service_supervisor(state, intents, adapter))


    def logs(self, contract: ServiceLaunchContract, *, deployment_id: str) -> ModelDeploymentLogs:
        paths = DirectoryCapturePathProvider(self._capture_root).paths(contract)
        return ModelDeploymentLogs(deployment_id, paths.stdout_path, paths.stderr_path)

    @staticmethod
    def _safe(value: str) -> str:
        return value.replace("/", "_").replace("\\", "_")


def build_local_management_plane(
    layout: DirectoryLayout,
    *,
    base_service_environment: tuple[tuple[str, str], ...] = (),
    model_source_environment: tuple[tuple[str, str], ...] = (),
    huggingface_cli: str = "hf",
    model_storage_pools: Mapping[str, Path] | None = None,
    task_group: TaskGroupPort,
) -> ManagementPlaneAuthorities:
    meta = build_in_memory_platform_meta()
    scopes = meta.scopes
    host = compose_local_host(planner=meta.capability_composition)
    directories = build_local_directory_authorities(layout)
    directory_layout = directories.layout
    process_runner = build_process_command_runner(task_group)
    local_commands = build_local_command_runner(task_group)
    runner = SubprocessEnvironmentCommandRunner(local_commands)
    pip_cache = directory_layout.layout.cache / "pip"
    conda_cache = directory_layout.layout.cache / "conda-packages"
    environments = build_python_environment_authorities(
        directory_layout,
        (
            VenvEnvironmentBackend(runner, pip_cache=pip_cache),
            CondaEnvironmentBackend(
                runner, executable="conda", backend_id="conda",
                conda_package_cache=conda_cache, pip_cache=pip_cache,
            ),
            CondaEnvironmentBackend(
                runner, executable="mamba", backend_id="mamba",
                conda_package_cache=conda_cache, pip_cache=pip_cache,
            ),
        ),
        runner,
    )
    execution_environments = ExecutionEnvironmentCatalog(scopes)
    asset_registry = ModelAssetRegistry(directory_layout)
    deployment_registry = ModelDeploymentRegistry(directory_layout)
    applied_store = AppliedModelDeploymentStore(directory_layout)
    asset_storage = LocalModelAssetStorage(directory_layout, additional_pools=model_storage_pools)
    deployment_catalog = ModelDeploymentCatalog(asset_registry, deployment_registry, environments.lifecycle)
    assets = ModelAssetManager(
        asset_registry,
        DeploymentModelAssetReferences(deployment_catalog),
        asset_storage,
        (HuggingFaceCliModelSource(
            asset_storage,
            executable=huggingface_cli,
            cache_root=directory_layout.layout.cache / "huggingface",
            environment=dict(model_source_environment),
            command_runner=local_commands,
        ),),
    )
    assignments = ModelAssignmentManager(scopes)
    service_factory = LocalModelServiceRuntimeFactory(
        directory_layout,
        operating_system=host.operating_system,
        task_group=task_group,
    )
    materializer = ModelLaunchMaterializer(
        assets, environments.lifecycle, base_environment=base_service_environment
    )
    deployment_runtime = ModelDeploymentRuntime(
        applied_store, deployment_catalog, materializer, service_factory
    )
    fleet = ModelFleetRuntime(deployment_catalog, deployment_runtime)
    deployment_logs = ModelDeploymentLogReader(
        applied_store, deployment_catalog, materializer, service_factory
    )
    resources = ModelResourceView(
        assets,
        deployment_catalog,
        fleet,
        NvidiaSmiGpuRuntimeObserver(local_commands),
    )
    controller = ModelDesiredStateController(
        fleet,
        FileModelControllerStateStore(directory_layout.layout.state / "model" / "deployments" / "controller.json"),
    )
    models = ModelAuthorities(
        assets, assignments, deployment_catalog, deployment_runtime, fleet, deployment_logs, resources, controller
    )
    return ManagementPlaneAuthorities(
        scopes,
        directories,
        execution_environments,
        environments,
        models,
        host,
        build_local_deployment_qualification(
            directory_layout.layout.state / "model" / "qualification",
            environments.packages,
            environments.execution,
            local_commands,
        ),
    )


__all__ = ["LocalModelServiceRuntimeFactory", "ManagementPlaneAuthorities", "build_local_management_plane"]
