from __future__ import annotations

from pathlib import Path

from noetrium_platform.foundation.governance.repository_boundary.api import RepositoryBoundaryAuditor
from noetrium_platform.product.operator.api import (
    ProjectCreateReceipt,
    ProjectCreateRequest,
    ProjectDoctorReport,
    ProjectTestReceipt,
)
from noetrium_platform.product.operator.runtime.project_doctor import doctor_project
from noetrium_platform.product.operator.runtime.project_scaffold import create_project
from noetrium_platform.product.operator.runtime.project_testing import test_project


class LocalProjectExperience:
    """Filesystem product adapter; project/domain truth remains producer-owned."""

    def __init__(self, boundary_auditor: RepositoryBoundaryAuditor) -> None:
        if not callable(boundary_auditor):
            raise TypeError("boundary_auditor must be callable")
        self._boundary_auditor = boundary_auditor

    def create(self, request: ProjectCreateRequest) -> ProjectCreateReceipt:
        return create_project(request)

    def doctor(self, project_root: Path) -> ProjectDoctorReport:
        return doctor_project(project_root, boundary_auditor=self._boundary_auditor)

    def test(self, project_root: Path) -> ProjectTestReceipt:
        return test_project(project_root)


__all__ = ["LocalProjectExperience", "create_project", "doctor_project", "test_project"]
