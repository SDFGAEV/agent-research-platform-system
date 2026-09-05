from pathlib import Path

from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult
from noetrium_platform.capabilities.model.qualification.api import (
    CudaFacts,
    DeploymentCapabilityFacts,
    DeploymentQualificationApplicationRequest,
    DeploymentQualificationEvidenceRecord,
    DeploymentQualificationPlan,
    DeploymentQualificationRequest,
    GpuCapabilityFacts,
    GpuFabricFacts,
    HostExecutionFacts,
    InstallPackage,
    ModelArtifactFacts,
    OperatingSystemFacts,
    PackageIndexFacts,
    PythonRuntimeFacts,
    StorageCapabilityFacts,
    QualificationCommandReceipt,
    QualificationMaterializationStatus,
)
from noetrium_platform.capabilities.model.qualification.providers.qualification_application import (
    FileDeploymentQualificationApplicationStore,
)
from noetrium_platform.capabilities.model.qualification.providers.qualification_evidence import (
    FileDeploymentQualificationEvidenceStore,
)
from noetrium_platform.capabilities.model.qualification.providers.python_package_installer import (
    PythonEnvironmentQualificationPackageInstaller,
)
from noetrium_platform.capabilities.model.qualification.runtime.application import DeploymentQualificationPlanApplier
from noetrium_platform.capabilities.model.qualification.runtime.qualification import DeploymentQualificationResolver


class _Installer:
    def __init__(self, *, return_code: int = 0) -> None:
        self.return_code = return_code
        self.installs: list[tuple[str, tuple[InstallPackage, ...]]] = []
        self.checks: list[str] = []

    def install(self, environment_id: str, packages: tuple[InstallPackage, ...]):
        self.installs.append((environment_id, packages))
        return (
            QualificationCommandReceipt(
                "pip-install",
                "a" * 64,
                self.return_code,
                "b" * 64,
                "c" * 64,
            ),
        )

    def check(self, environment_id: str):
        self.checks.append(environment_id)
        return QualificationCommandReceipt(
            "pip-check",
            "d" * 64,
            0,
            "e" * 64,
            "f" * 64,
        )


class _RaisingInstaller:
    def install(self, environment_id: str, packages: tuple[InstallPackage, ...]):
        raise FileNotFoundError(environment_id)

    def check(self, environment_id: str):
        raise AssertionError("check must not run after install setup failure")


def _facts(*, with_gpu: bool = True) -> DeploymentCapabilityFacts:
    return DeploymentCapabilityFacts(
        captured_at_unix=1.0,
        operating_system=OperatingSystemFacts("Linux", "Ubuntu", "22.04", "6.8", "x86_64"),
        cuda=CudaFacts("580.173.02", "13.0", "12.4", ("12",)),
        gpus=(GpuCapabilityFacts("0", "GPU-0", "RTX 3090", 24576, 24000, "8.6"),) if with_gpu else (),
        python=PythonRuntimeFacts(
            "/opt/env/bin/python",
            "3.11.0",
            "pip 26.0",
            True,
            True,
            "/opt/env/lib/python3.11/site-packages",
            "2.11.0",
            "13.0",
            ("sm86",),
            native_library_names=("libcudart.so.13",),
        ),
        model=ModelArtifactFacts(
            "example-model",
            "/models/example-model",
            "example_decoder",
            ("ExampleForConditionalGeneration",),
            "bfloat16",
            262144,
            True,
        ),
        package_indexes=(PackageIndexFacts(
            "vllm", "https://pypi.org/simple", ("0.27.1",),
            selected_version="0.27.1", dependency_closure_complete=True,
        ),),
        host=HostExecutionFacts("test-host", "x86_64", 16, 128 << 30, 96 << 30),
        fabric=GpuFabricFacts(("GPU0 GPU1 NV1",), "2.18", "/usr/lib/libnccl.so.2"),
        storage=StorageCapabilityFacts("/models/example-model", 1 << 40, 512 << 30, 1_000_000, "xfs", "dev0", True, True),
    )


def _request() -> DeploymentQualificationRequest:
    return DeploymentQualificationRequest(
        "example-model",
        Path("/models/example-model"),
        Path("/opt/env/bin/python"),
        backends=("vllm",),
    )


def _publish_plan(root: Path, *, with_gpu: bool = True) -> str:
    request = _request()
    facts = _facts(with_gpu=with_gpu)
    plan = DeploymentQualificationResolver().resolve(request, facts)
    FileDeploymentQualificationEvidenceStore(root).publish(
        DeploymentQualificationEvidenceRecord(1.0, request, facts, plan)
    )
    return plan.plan_digest


def test_applier_consumes_frozen_plan_and_persists_receipt(tmp_path: Path) -> None:
    plan_digest = _publish_plan(tmp_path / "evidence")
    installer = _Installer()
    applications = FileDeploymentQualificationApplicationStore(tmp_path / "applications")
    receipt = DeploymentQualificationPlanApplier(
        FileDeploymentQualificationEvidenceStore(tmp_path / "evidence"),
        installer,
        applications,
    ).apply(DeploymentQualificationApplicationRequest(plan_digest, "example-serving"))

    assert receipt.status is QualificationMaterializationStatus.SUCCEEDED
    assert receipt.backend == "vllm"
    assert installer.installs[0][0] == "example-serving"
    assert installer.checks == ["example-serving"]
    assert applications.get(receipt.application_digest) == receipt


def test_applier_rejects_plan_without_accepted_backend_without_installing(tmp_path: Path) -> None:
    plan_digest = _publish_plan(tmp_path / "evidence", with_gpu=False)
    installer = _Installer()
    applications = FileDeploymentQualificationApplicationStore(tmp_path / "applications")
    receipt = DeploymentQualificationPlanApplier(
        FileDeploymentQualificationEvidenceStore(tmp_path / "evidence"),
        installer,
        applications,
    ).apply(DeploymentQualificationApplicationRequest(plan_digest, "example-serving"))

    assert receipt.status is QualificationMaterializationStatus.REJECTED
    assert installer.installs == []
    assert installer.checks == []


def test_applier_persists_failure_before_reraising_installer_root_cause(tmp_path: Path) -> None:
    plan_digest = _publish_plan(tmp_path / "evidence")
    applications = FileDeploymentQualificationApplicationStore(tmp_path / "applications")
    try:
        DeploymentQualificationPlanApplier(
            FileDeploymentQualificationEvidenceStore(tmp_path / "evidence"),
            _RaisingInstaller(),
            applications,
        ).apply(DeploymentQualificationApplicationRequest(plan_digest, "missing-env"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("installer root cause must be re-raised")

    paths = tuple((tmp_path / "applications").glob("*.json"))
    assert len(paths) == 1
    receipt = applications.get(paths[0].stem)
    assert receipt.status is QualificationMaterializationStatus.FAILED
    assert receipt.reasons == ("package installer raised FileNotFoundError",)


def test_python_package_installer_does_not_re_resolve_dependency_graph() -> None:
    class Packages:
        def __init__(self) -> None:
            self.install_calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

        def install_packages(self, environment_id: str, packages: tuple[str, ...], *, extra_args=()):
            self.install_calls.append((environment_id, packages, tuple(extra_args)))
            return EnvironmentCommandResult(("python", "-m", "pip", "install"), 0, "", "")

        def check(self, environment_id: str):
            return EnvironmentCommandResult(("python", "-m", "pip", "check"), 0, "", "")

    packages = Packages()
    installer = PythonEnvironmentQualificationPackageInstaller(packages)
    installer.install(
        "example-serving",
        (
            InstallPackage("vllm", "0.27.1", "https://pypi.org/simple"),
            InstallPackage("torch", "2.11.0", "https://pypi.org/simple"),
        ),
    )

    assert packages.install_calls == [
        (
            "example-serving",
            ("vllm==0.27.1", "torch==2.11.0"),
            ("--no-deps", "--only-binary=:all:", "--index-url", "https://pypi.org/simple"),
        )
    ]
