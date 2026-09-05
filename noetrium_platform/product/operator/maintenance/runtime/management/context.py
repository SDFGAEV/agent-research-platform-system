from __future__ import annotations

from dataclasses import dataclass

from noetrium_platform.infrastructure.resources.directory.api import DirectoryManagementAuthorities
from noetrium_platform.capabilities.model.api import ModelAuthorities
from noetrium_platform.infrastructure.lifecycle.python.api import PythonEnvironmentAuthorities
from noetrium_platform.capabilities.environment.catalog.api import ExecutionEnvironmentCatalogPort
from noetrium_platform.foundation.scope.api import ScopeRegistryPort
from noetrium_platform.capabilities.model.qualification.composition import DeploymentQualificationAuthorities


@dataclass(frozen=True, slots=True)
class ManagementCommandContext:
    scopes: ScopeRegistryPort
    directories: DirectoryManagementAuthorities
    execution_environments: ExecutionEnvironmentCatalogPort
    environments: PythonEnvironmentAuthorities
    models: ModelAuthorities
    deployment_qualification: DeploymentQualificationAuthorities


__all__ = ["ManagementCommandContext"]
