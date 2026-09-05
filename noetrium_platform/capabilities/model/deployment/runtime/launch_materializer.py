from __future__ import annotations

from noetrium_platform.foundation.kernel.kernel import canonical_digest
from noetrium_platform.capabilities.model.asset.api import ModelAssetManagementPort
from noetrium_platform.capabilities.model.deployment.api import ModelDeploymentSpec
from noetrium_platform.infrastructure.lifecycle.python.api import PythonEnvironmentLookupPort
from noetrium_platform.infrastructure.lifecycle.service.api import ServiceLaunchContract


class ModelLaunchMaterializer:
    """Materializes mutable deployment intent into one exact service launch contract."""

    def __init__(
        self,
        assets: ModelAssetManagementPort,
        python_environments: PythonEnvironmentLookupPort,
        *,
        base_environment: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._assets = assets
        self._python_environments = python_environments
        self._base_environment = tuple(base_environment)

    def materialize(self, spec: ModelDeploymentSpec) -> tuple[ServiceLaunchContract, tuple[tuple[str, str], ...]]:
        asset = self._assets.model(spec.model_id)
        environment = dict(self._base_environment)
        environment.update(spec.environment)
        if spec.gpu_devices:
            environment["CUDA_VISIBLE_DEVICES"] = ",".join(spec.gpu_devices)
        executable = spec.executable
        argv = list(spec.argv)
        if spec.python_environment_id is not None:
            python_path = self._python_environments.get(spec.python_environment_id).python_path
            executable = str(python_path) if spec.executable in {"python", "python3", "{python}"} else spec.executable
            argv = [str(python_path) if item == "{python}" else item for item in argv]
        replacements = {
            "{model_path}": str(asset.path),
            "{model_id}": asset.model_id,
            "{deployment_id}": spec.deployment_id,
        }
        argv = [self._replace_tokens(item, replacements) for item in argv]
        if not argv or argv[0] != executable:
            argv = [executable, *argv]
        environment_items = tuple(sorted((str(k), str(v)) for k, v in environment.items()))
        environment_digest = canonical_digest(environment_items)
        artifact_digest = canonical_digest(
            {
                "model_id": asset.model_id,
                "path": asset.path,
                "mode": asset.mode,
                "storage_pool": asset.storage_pool,
            }
        )
        runtime_identity_digest = canonical_digest(
            {
                "engine": spec.engine,
                "python_environment_id": spec.python_environment_id,
                "executable": executable,
            }
        )
        cwd = str(spec.cwd.expanduser().resolve())
        generation = canonical_digest(
            {
                "deployment_id": spec.deployment_id,
                "service_id": spec.service_id,
                "engine": spec.engine,
                "executable": executable,
                "argv": tuple(argv),
                "cwd": cwd,
                "environment_digest": environment_digest,
                "artifact_digest": artifact_digest,
                "runtime_identity_digest": runtime_identity_digest,
                "readiness_url": spec.readiness_url,
            }
        )[:24]
        return (
            ServiceLaunchContract(
                service_id=spec.service_id,
                generation=generation,
                executable=executable,
                argv=tuple(argv),
                cwd=cwd,
                environment_digest=environment_digest,
                artifact_digest=artifact_digest,
                runtime_identity_digest=runtime_identity_digest,
                readiness_timeout_s=spec.readiness_timeout_s,
                stop_timeout_s=spec.stop_timeout_s,
                heartbeat_interval_s=spec.heartbeat_interval_s,
            ),
            environment_items,
        )

    @staticmethod
    def _replace_tokens(value: str, replacements: dict[str, str]) -> str:
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        return value


__all__ = ["ModelLaunchMaterializer"]
