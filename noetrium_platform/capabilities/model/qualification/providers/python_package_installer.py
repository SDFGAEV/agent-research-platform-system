"""Adapter from qualification package plans to the Python environment system."""

from __future__ import annotations

from collections import defaultdict

from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult, PythonPackageManagementPort
from noetrium_platform.capabilities.model.qualification.api import (
    InstallPackage,
    QualificationCommandReceipt,
    QualificationPackageInstallerPort,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest


class PythonEnvironmentQualificationPackageInstaller(QualificationPackageInstallerPort):
    """Apply exact package groups through the existing Python package port.

    Packages are grouped by their planned index and installed in separate pip
    invocations. This preserves the source binding of every package instead
    of combining multiple indexes into one ambiguous resolver operation.
    Qualification has already resolved the complete dependency closure, so
    pip is explicitly forbidden from discovering a new dependency graph or
    falling back to a source distribution.
    """

    def __init__(self, packages: PythonPackageManagementPort) -> None:
        self._packages = packages

    def install(
        self,
        environment_id: str,
        packages: tuple[InstallPackage, ...],
    ) -> tuple[QualificationCommandReceipt, ...]:
        grouped: dict[str, list[InstallPackage]] = defaultdict(list)
        for package in packages:
            grouped[package.index_url].append(package)
        receipts: list[QualificationCommandReceipt] = []
        for index_url, group in grouped.items():
            result = self._packages.install_packages(
                environment_id,
                tuple(f"{item.name}=={item.version}" for item in group),
                extra_args=(
                    "--no-deps",
                    "--only-binary=:all:",
                    "--index-url",
                    index_url,
                ),
            )
            receipts.append(self._receipt("pip-install", result))
            if result.returncode != 0:
                break
        return tuple(receipts)

    def check(self, environment_id: str) -> QualificationCommandReceipt:
        return self._receipt("pip-check", self._packages.check(environment_id))

    @staticmethod
    def _receipt(operation: str, result: EnvironmentCommandResult) -> QualificationCommandReceipt:
        return QualificationCommandReceipt(
            operation=operation,
            command_digest=canonical_digest(result.argv),
            return_code=result.returncode,
            stdout_digest=canonical_digest(result.stdout),
            stderr_digest=canonical_digest(result.stderr),
        )


__all__ = ["PythonEnvironmentQualificationPackageInstaller"]
