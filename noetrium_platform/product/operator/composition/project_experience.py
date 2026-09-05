from __future__ import annotations

from noetrium_platform.foundation.governance.repository_boundary.runtime import audit_downstream_project_imports
from noetrium_platform.product.operator.api import ProjectFacade
from noetrium_platform.product.operator.runtime.project_experience import LocalProjectExperience


def build_project_facade() -> ProjectFacade:
    """Build the local downstream-project product facade without a service locator."""

    return ProjectFacade(LocalProjectExperience(audit_downstream_project_imports))


__all__ = ["build_project_facade"]
