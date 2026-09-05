from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE
from noetrium_platform.foundation.scope.runtime import InMemoryScopeRegistry
from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayout, ManagedDirectoryKind
from noetrium_platform.infrastructure.resources.directory.runtime import build_local_directory_authorities
from noetrium_platform.capabilities.model.asset.api import ModelAssetMode, ModelSourceSpec
from noetrium_platform.capabilities.model.api import ModelAuthorities
from noetrium_platform.capabilities.model.deployment.api import ModelDeploymentLogs, ModelDeploymentSelector, ModelDeploymentSpec, ModelDesiredState, ModelRuntimeState
from noetrium_platform.infrastructure.resources.compute.api import GpuDeviceStatus, GpuProcessStatus, GpuRuntimeSnapshot
from noetrium_platform.capabilities.model.asset.providers import HuggingFaceCliModelSource
from noetrium_platform.capabilities.model.asset.runtime import LocalModelAssetStorage, ModelAssetManager, ModelAssetRegistry
from noetrium_platform.capabilities.model.composition import DeploymentModelAssetReferences
from noetrium_platform.capabilities.model.assignment.runtime import ModelAssignmentManager
from noetrium_platform.capabilities.model.deployment.runtime import (
    AppliedModelDeploymentStore,
    FileModelControllerStateStore,
    ModelDesiredStateController,
    ModelDeploymentCatalog,
    ModelDeploymentRegistry,
    ModelDeploymentLogReader,
    ModelDeploymentRuntime,
    ModelLaunchMaterializer,
    ModelFleetRuntime,
    ModelResourceView,
    sglang_deployment,
    vllm_deployment,
)
from noetrium_platform.infrastructure.resources.compute.providers import NvidiaSmiGpuRuntimeObserver
from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult, PythonEnvironmentOwnership, PythonEnvironmentSpec
from noetrium_platform.infrastructure.lifecycle.python.runtime import CondaEnvironmentBackend, build_python_environment_authorities
from noetrium_platform.foundation.kernel.kernel.process import LocalCommandResult
from noetrium_platform.infrastructure.lifecycle.service.api import (
    ServiceProcessIdentity,
    ServiceReconcileObservation,
    ServiceStartOutcome,
    ServiceStopOutcome,
)


def layout(root: Path) -> DirectoryLayout:
    return DirectoryLayout(
        releases=root / "releases",
        runtime=root / "runtime",
        state=root / "state",
        logs=root / "logs",
        model_artifacts=root / "models",
        python_environments=root / "envs",
        cache=root / "cache",
        temp=root / "tmp",
        locks=root / "locks",
        workspaces=root / "workspaces",
    )


class FakeEnvBackend:
    backend_id = "fake"

    def create(self, root: Path, spec: PythonEnvironmentSpec) -> Path:
        python = root / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("fake", encoding="utf-8")
        return python

    def python_path(self, root: Path) -> Path:
        return root / "bin" / "python"

    def install(self, root: Path, requirements: Path, *, extra_args=()):
        return EnvironmentCommandResult((str(self.python_path(root)), "pip"), 0, "installed", "")


class FakeCommandRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, *, cwd=None, environment=None):
        self.calls.append((tuple(argv), cwd, environment))
        tail = tuple(argv)
        if tail[-4:] == ("-m", "pip", "list", "--format=json"):
            stdout = '[{"name":"pip","version":"25.0"}]'
        elif tail[-3:] == ("-m", "pip", "freeze"):
            stdout = "pip==25.0\npytest==9.0\n"
        else:
            stdout = "ran"
        return EnvironmentCommandResult(tuple(argv), 0, stdout, "")


class RecordingCommandRunner(FakeCommandRunner):
    pass


class FakeGpuObserver:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot or GpuRuntimeSnapshot(False, detail="test-no-gpu")

    def snapshot(self):
        return self._snapshot


class FakeRuntime:
    def __init__(self) -> None:
        self.live = False

    def reconcile_exact(self, contract):
        process = ServiceProcessIdentity(1234, "start") if self.live else None
        return ServiceReconcileObservation(True, process)

    def start_exact(self, contract):
        self.live = True
        return ServiceStartOutcome(contract.digest(), ServiceProcessIdentity(1234, "start"), "ready:test", 1234.5)

    def verify_ready_exact(self, contract):
        raise NotImplementedError

    def stop_exact(self, contract):
        self.live = False
        return ServiceStopOutcome(contract.digest(), True)


class FakeFactory:
    def __init__(self, log_root: Path | None = None) -> None:
        self.runtime = FakeRuntime()
        self.contracts = []
        self.environments = []
        self.log_root = log_root or Path("/tmp")

    def open(self, contract, *, environment, readiness_url):
        self.contracts.append(contract)
        self.environments.append(environment)
        return self.runtime

    def logs(self, contract, *, deployment_id):
        return ModelDeploymentLogs(deployment_id, self.log_root / "stdout.log", self.log_root / "stderr.log")


def build_environments(directories, runner=None):
    command_runner = runner or FakeCommandRunner()
    return build_python_environment_authorities(directories.layout, (FakeEnvBackend(),), command_runner)


def build_models(directories, environments, factory, *, source_backends=(), gpu_observer=None):
    asset_registry = ModelAssetRegistry(directories.layout)
    deployment_registry = ModelDeploymentRegistry(directories.layout)
    applied_store = AppliedModelDeploymentStore(directories.layout)
    storage = LocalModelAssetStorage(directories.layout)
    catalog = ModelDeploymentCatalog(asset_registry, deployment_registry, environments.lifecycle)
    assets = ModelAssetManager(asset_registry, DeploymentModelAssetReferences(catalog), storage, source_backends)
    materializer = ModelLaunchMaterializer(assets, environments.lifecycle)
    runtime = ModelDeploymentRuntime(applied_store, catalog, materializer, factory)
    fleet = ModelFleetRuntime(catalog, runtime)
    logs = ModelDeploymentLogReader(applied_store, catalog, materializer, factory)
    resources = ModelResourceView(assets, catalog, fleet, gpu_observer or FakeGpuObserver())
    controller = ModelDesiredStateController(
        fleet,
        FileModelControllerStateStore(directories.layout.layout.state / "model" / "deployments" / "controller.json"),
    )
    assignments = ModelAssignmentManager(InMemoryScopeRegistry())
    return ModelAuthorities(assets, assignments, catalog, runtime, fleet, logs, resources, controller)


class ManagementTests(unittest.TestCase):
    def test_directory_manager_allocates_and_removes_workspaces(self):
        with TemporaryDirectory() as td:
            manager = build_local_directory_authorities(layout(Path(td)))
            allocation = manager.workspaces.allocate_workspace("run-1", scope=PLATFORM_SCOPE, category="study", owner="paper-1")
            self.assertTrue(allocation.path.exists())
            self.assertEqual(manager.workspaces.list_workspaces(category="study")[0].owner, "paper-1")
            self.assertEqual(manager.layout.root(ManagedDirectoryKind.MODEL_ARTIFACTS), Path(td) / "models")
            (manager.layout.root(ManagedDirectoryKind.CACHE) / "large.bin").write_bytes(b"x" * 32)
            (manager.layout.root(ManagedDirectoryKind.CACHE) / "small.bin").write_bytes(b"x" * 4)
            overview = manager.inspection.overview(ManagedDirectoryKind.CACHE)
            self.assertEqual(overview.top_level_entries, 2)
            entries = manager.inspection.entries(ManagedDirectoryKind.CACHE, limit=1)
            self.assertEqual(entries[0].path.name, "large.bin")
            self.assertEqual(entries[0].bytes, 32)
            self.assertTrue(manager.workspaces.remove_workspace("run-1", scope=PLATFORM_SCOPE, category="study"))

    def test_python_environment_manager_is_backend_driven(self):
        with TemporaryDirectory() as td:
            directories = build_local_directory_authorities(layout(Path(td)))
            manager = build_environments(directories)
            value = manager.lifecycle.create(PythonEnvironmentSpec("agent", PLATFORM_SCOPE, backend="fake"))
            self.assertTrue(value.python_path.exists())
            self.assertEqual(value.ownership, PythonEnvironmentOwnership.MANAGED)
            self.assertEqual(manager.execution.command("agent", "-m", "pytest")[1:], ("-m", "pytest"))
            req = Path(td) / "requirements.txt"
            req.write_text("x==1", encoding="utf-8")
            self.assertEqual(manager.packages.install("agent", req).returncode, 0)
            self.assertEqual(manager.packages.install_packages("agent", ("pytest==9.0",)).returncode, 0)
            self.assertEqual(manager.packages.packages("agent")[0].name, "pip")
            self.assertEqual(manager.packages.freeze("agent"), ("pip==25.0", "pytest==9.0"))
            self.assertEqual(manager.packages.check("agent").returncode, 0)
            self.assertEqual(manager.packages.uninstall_packages("agent", ("pytest",)).returncode, 0)
            self.assertEqual(manager.execution.run("agent", "-c", "print(1)").returncode, 0)
            self.assertEqual(len(manager.lifecycle.list()), 1)

    def test_registered_external_environment_remove_only_drops_metadata(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            manager = build_environments(directories)
            external = root / "external-env"
            python = external / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("external", encoding="utf-8")
            value = manager.lifecycle.register_existing(PythonEnvironmentSpec("external", PLATFORM_SCOPE, backend="fake"), external)
            self.assertEqual(value.ownership, PythonEnvironmentOwnership.EXTERNAL)
            self.assertTrue(manager.lifecycle.remove("external"))
            self.assertTrue(external.exists())
            self.assertTrue(python.exists())

    def test_conda_and_mamba_backends_build_prefix_commands(self):
        with TemporaryDirectory() as td:
            root = Path(td) / "env"
            for executable, backend_id in (("conda", "conda"), ("mamba", "mamba")):
                runner = RecordingCommandRunner()
                backend = CondaEnvironmentBackend(runner, executable=executable, backend_id=backend_id)
                backend.create(root, PythonEnvironmentSpec("e", PLATFORM_SCOPE, backend=backend_id, python_version="3.11"))
                self.assertEqual(
                    runner.calls[-1][0],
                    (executable, "create", "-y", "-p", str(root), "python=3.11"),
                )

    def test_model_manager_handles_mutable_desired_state_without_qualification(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "external-model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(
                '{"model_type":"example_model","architectures":["ExampleForCausalLM"],"torch_dtype":"bfloat16","max_position_embeddings":32768,"quantization_config":{"quant_method":"awq","bits":4}}',
                encoding="utf-8",
            )
            factory = FakeFactory()
            models = build_models(directories, environments, factory)
            models.assets.register_model("example-model", PLATFORM_SCOPE, model_dir)
            spec = ModelDeploymentSpec(
                deployment_id="example-deployment",
                scope=PLATFORM_SCOPE,
                service_id="model:example-deployment",
                model_id="example-model",
                engine="custom",
                executable="{python}",
                argv=("{python}", "-m", "server", "--model", "{model_path}"),
                cwd=root,
                python_environment_id="serve",
                gpu_devices=("0", "1"),
            )
            models.deployment_catalog.put_deployment(spec)
            usage = models.assets.model_usage("example-model")
            self.assertEqual(usage.deployment_ids, ("example-deployment",))
            self.assertEqual(models.assets.model_stats("example-model").directories, 1)
            config = models.assets.model_config("example-model")
            self.assertEqual(config.model_type, "example_model")
            self.assertEqual(config.quantization_bits, 4)
            started = models.deployment_runtime.start("example-deployment")
            self.assertEqual(started.runtime_state, ModelRuntimeState.RUNNING)
            self.assertEqual(models.deployment_catalog.deployment("example-deployment").desired_state, ModelDesiredState.RUNNING)
            self.assertIn(("CUDA_VISIBLE_DEVICES", "0,1"), factory.environments[-1])
            self.assertIn(str(model_dir), factory.contracts[-1].argv)
            self.assertEqual(models.deployment_runtime.status("example-deployment").pid, 1234)
            stopped = models.deployment_runtime.stop("example-deployment")
            self.assertEqual(stopped.runtime_state, ModelRuntimeState.STOPPED)

    def test_running_deployment_can_be_reconfigured_then_reconciled(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "external-model"
            model_dir.mkdir()
            factory = FakeFactory()
            models = build_models(directories, environments, factory)
            models.assets.register_model("example-model", PLATFORM_SCOPE, model_dir)
            base = ModelDeploymentSpec(
                deployment_id="example-deployment",
                scope=PLATFORM_SCOPE,
                service_id="model:example-deployment",
                model_id="example-model",
                engine="custom",
                executable="{python}",
                argv=("{python}", "-m", "server", "--port", "8000"),
                cwd=root,
                python_environment_id="serve",
            )
            models.deployment_catalog.put_deployment(base)
            models.deployment_runtime.start("example-deployment")
            updated = ModelDeploymentSpec(
                deployment_id="example-deployment",
                scope=PLATFORM_SCOPE,
                service_id="model:example-deployment",
                model_id="example-model",
                engine="custom",
                executable="{python}",
                argv=("{python}", "-m", "server", "--port", "9000"),
                cwd=root,
                python_environment_id="serve",
                desired_state=ModelDesiredState.RUNNING,
            )
            models.deployment_catalog.put_deployment(updated)
            self.assertEqual(models.deployment_runtime.status("example-deployment").runtime_state, ModelRuntimeState.UPDATE_PENDING)
            reconciled = models.fleet.reconcile()[0]
            self.assertEqual(reconciled.runtime_state, ModelRuntimeState.RUNNING)
            self.assertIn("9000", factory.contracts[-1].argv)

    def test_applied_snapshot_survives_mutable_model_and_environment_registry_changes(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve", PLATFORM_SCOPE, backend="fake"))
            old_model = root / "old-model"; old_model.mkdir()
            new_model = root / "new-model"; new_model.mkdir()
            factory = FakeFactory()
            models = build_models(directories, environments, factory)
            models.assets.register_model("m", PLATFORM_SCOPE, old_model)
            models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                deployment_id="d", service_id="model:d", model_id="m", engine="custom",
                scope=PLATFORM_SCOPE,
                executable="{python}", argv=("{python}", "-m", "server", "{model_path}"), cwd=root,
                python_environment_id="serve",
            ))
            models.deployment_runtime.start("d")
            applied_contract = factory.contracts[-1]
            models.assets.register_model("m", PLATFORM_SCOPE, new_model)
            self.assertEqual(models.deployment_runtime.status("d").runtime_state, ModelRuntimeState.UPDATE_PENDING)
            environments.lifecycle.remove("serve")
            pending = models.deployment_runtime.status("d")
            self.assertEqual(pending.runtime_state, ModelRuntimeState.UPDATE_PENDING)
            self.assertTrue(pending.detail.startswith("desired-resource-missing:"))
            models.deployment_runtime.stop("d")
            self.assertEqual(factory.contracts[-1].digest(), applied_contract.digest())

    def test_large_model_asset_modes_cover_reference_copy_move_and_symlink(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            models = build_models(directories, environments, FakeFactory())
            ref = root / "ref"; ref.mkdir(); (ref / "w").write_text("x")
            copied = root / "copied-source"; copied.mkdir(); (copied / "w").write_text("y")
            moved = root / "moved-source"; moved.mkdir(); (moved / "w").write_text("z")
            linked = root / "linked-source"; linked.mkdir()
            self.assertEqual(models.assets.register_model("ref", PLATFORM_SCOPE, ref).path, ref.resolve())
            copy_asset = models.assets.register_model("copy", PLATFORM_SCOPE, copied, mode="copy")
            self.assertTrue((copy_asset.path / "w").exists())
            move_asset = models.assets.register_model("move", PLATFORM_SCOPE, moved, mode="move")
            self.assertFalse(moved.exists())
            self.assertTrue((move_asset.path / "w").exists())
            try:
                link_asset = models.assets.register_model("link", PLATFORM_SCOPE, linked, mode="symlink")
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise
            self.assertTrue(link_asset.path.is_symlink())
            models.assets.unregister_model("link", delete_managed_files=True)
            self.assertTrue(linked.exists())

    def test_gpu_conflicts_are_visible_but_do_not_block_management(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "model"; model_dir.mkdir()
            models = build_models(directories, environments, FakeFactory())
            models.assets.register_model("m", PLATFORM_SCOPE, model_dir)
            for name in ("a", "b"):
                models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                    deployment_id=name, service_id=f"model:{name}", model_id="m", engine="custom",
                scope=PLATFORM_SCOPE,
                    executable="{python}", argv=("{python}", "-m", "server"), cwd=root,
                    python_environment_id="serve", gpu_devices=("0",), desired_state=ModelDesiredState.RUNNING,
                ))
            conflicts = models.resources.gpu_conflicts()
            self.assertEqual(conflicts[0].gpu_device, "0")
            self.assertFalse(models.resources.gpu_runtime().available)
            self.assertEqual(conflicts[0].deployment_ids, ("a", "b"))
            self.assertEqual(models.deployment_logs.logs("a").stdout_path, Path("/tmp/stdout.log"))

    def test_model_config_view_is_best_effort_for_malformed_config(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            model_dir = root / "broken-model"; model_dir.mkdir()
            (model_dir / "config.json").write_text("{broken", encoding="utf-8")
            models = build_models(directories, environments, FakeFactory())
            models.assets.register_model("broken", PLATFORM_SCOPE, model_dir)
            summary = models.assets.model_config("broken")
            self.assertTrue(summary.detail.startswith("config-unreadable:"))

    def test_log_tail_and_gpu_process_binding_are_operator_read_models(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "model"; model_dir.mkdir()
            log_root = root / "captured"; log_root.mkdir()
            (log_root / "stdout.log").write_text("hello\nworld\n", encoding="utf-8")
            (log_root / "stderr.log").write_text("warning\n", encoding="utf-8")
            gpu = GpuRuntimeSnapshot(True, processes=(GpuProcessStatus(1234, "GPU-1", 2048, "python"),))
            models = build_models(directories, environments, FakeFactory(log_root), gpu_observer=FakeGpuObserver(gpu))
            models.assets.register_model("m", PLATFORM_SCOPE, model_dir)
            models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                deployment_id="d", service_id="model:d", model_id="m", engine="custom",
                scope=PLATFORM_SCOPE,
                executable="{python}", argv=("{python}", "-m", "server"), cwd=root, python_environment_id="serve",
            ))
            models.deployment_runtime.start("d")
            tail = models.deployment_logs.tail_logs("d", stream="stdout", max_bytes=6)
            self.assertEqual(tail.text, "world\n")
            bindings = models.resources.gpu_process_bindings()
            self.assertEqual(bindings[0].deployment_id, "d")
            self.assertEqual(bindings[0].used_memory_mb, 2048)

    def test_huggingface_source_backend_is_optional_management_acquisition(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            storage = LocalModelAssetStorage(directories.layout)
            seen = {}

            class FakeCommandRunner:
                def run(self, argv, **kwargs):
                    seen["argv"] = tuple(argv)
                    seen["env"] = kwargs.get("environment")
                    destination = Path(argv[argv.index("--local-dir") + 1])
                    destination.mkdir(parents=True)
                    (destination / "config.json").write_text("{}", encoding="utf-8")
                    return LocalCommandResult(tuple(argv), 0, "", "")

            source = HuggingFaceCliModelSource(
                storage,
                cache_root=directories.layout.layout.cache / "huggingface",
                environment={"HF_ENDPOINT": "https://hf-mirror.example"},
                command_runner=FakeCommandRunner(),
            )
            asset_registry = ModelAssetRegistry(directories.layout)
            deployment_registry = ModelDeploymentRegistry(directories.layout)
            applied_store = AppliedModelDeploymentStore(directories.layout)
            factory = FakeFactory()
            catalog = ModelDeploymentCatalog(asset_registry, deployment_registry, environments.lifecycle)
            assets = ModelAssetManager(asset_registry, DeploymentModelAssetReferences(catalog), storage, (source,))
            materializer = ModelLaunchMaterializer(assets, environments.lifecycle)
            runtime = ModelDeploymentRuntime(applied_store, catalog, materializer, factory)
            fleet = ModelFleetRuntime(catalog, runtime)
            logs = ModelDeploymentLogReader(applied_store, catalog, materializer, factory)
            resources = ModelResourceView(assets, catalog, fleet, FakeGpuObserver())
            controller = ModelDesiredStateController(
                fleet,
                FileModelControllerStateStore(directories.layout.layout.state / "model" / "deployments" / "controller.json"),
            )
            assignments = ModelAssignmentManager(InMemoryScopeRegistry())
            models = ModelAuthorities(assets, assignments, catalog, runtime, fleet, logs, resources, controller)
            with patch("noetrium_platform.capabilities.model.asset.providers.huggingface_cli.shutil.which", return_value="/usr/bin/hf"):
                asset = models.assets.fetch_model(
                    "example-model",
                    PLATFORM_SCOPE,
                    ModelSourceSpec("huggingface", "example-org/example-model", revision="main", max_workers=24),
                )
            self.assertEqual(asset.mode, ModelAssetMode.FETCHED)
            self.assertEqual(asset.origin.backend, "huggingface")
            self.assertEqual(asset.origin.revision, "main")
            self.assertNotIn("--cache-dir", seen["argv"])
            self.assertEqual(seen["argv"][seen["argv"].index("--max-workers") + 1], "24")
            self.assertEqual(seen["env"]["HF_HOME"], str(directories.layout.layout.cache / "huggingface"))
            self.assertEqual(seen["env"]["HF_ENDPOINT"], "https://hf-mirror.example")
            self.assertTrue((asset.path / "config.json").exists())
            models.assets.unregister_model("example-model", delete_managed_files=True)
            self.assertFalse(asset.path.exists())

    def test_gpu_runtime_observer_is_best_effort_and_parses_nvidia_smi(self):
        class FakeCommandRunner:
            def __init__(self): self.outputs = [
                "0, GPU-1, H100, 81920, 1024, 80896, 12\n",
                "123, GPU-1, 512, python\n",
            ]
            def run(self, argv, **kwargs):
                return LocalCommandResult(tuple(argv), 0, self.outputs.pop(0), "")

        observer = NvidiaSmiGpuRuntimeObserver(FakeCommandRunner())
        with patch("noetrium_platform.infrastructure.resources.compute.providers.nvidia_smi.shutil.which", return_value=None):
            self.assertFalse(observer.snapshot().available)
        observer = NvidiaSmiGpuRuntimeObserver(FakeCommandRunner())
        with patch("noetrium_platform.infrastructure.resources.compute.providers.nvidia_smi.shutil.which", return_value="/usr/bin/nvidia-smi"):
            snapshot = observer.snapshot()
        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.devices[0].memory_free_mb, 80896)
        self.assertEqual(snapshot.processes[0].pid, 123)

    def test_fleet_reconcile_isolates_missing_desired_resources(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("good", PLATFORM_SCOPE, backend="fake"))
            environments.lifecycle.create(PythonEnvironmentSpec("gone", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "model"; model_dir.mkdir()
            models = build_models(directories, environments, FakeFactory())
            models.assets.register_model("m", PLATFORM_SCOPE, model_dir)
            for deployment_id, env_id in (("good", "good"), ("bad", "gone")):
                models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                    deployment_id=deployment_id, service_id=f"model:{deployment_id}", model_id="m", engine="custom",
                scope=PLATFORM_SCOPE,
                    executable="{python}", argv=("{python}", "-m", "server"), cwd=root, python_environment_id=env_id,
                    desired_state=ModelDesiredState.RUNNING,
                ))
            environments.lifecycle.remove("gone")
            states = {value.deployment_id: value.runtime_state for value in models.fleet.reconcile()}
            self.assertEqual(states["good"], ModelRuntimeState.RUNNING)
            self.assertEqual(states["bad"], ModelRuntimeState.MISSING)

    def test_optional_engine_templates_do_not_constrain_generic_launch_contract(self):
        root = Path("/srv/research")
        s = sglang_deployment(
            deployment_id="sg",
            scope=PLATFORM_SCOPE,
            model_id="m",
            python_environment_id="e",
            cwd=root,
            tensor_parallel=4,
        )
        v = vllm_deployment(
            deployment_id="vl",
            scope=PLATFORM_SCOPE,
            model_id="m",
            python_environment_id="e",
            cwd=root,
            tensor_parallel=4,
        )
        self.assertEqual(s.engine, "sglang")
        self.assertEqual(v.engine, "vllm")
        self.assertIn("--tp-size", s.argv)
        self.assertIn("--tensor-parallel-size", v.argv)


    def test_gpu_advisory_and_resource_changes_are_desired_only(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("env-a", PLATFORM_SCOPE, backend="fake"))
            environments.lifecycle.create(PythonEnvironmentSpec("env-b", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "model"; model_dir.mkdir()
            gpu_snapshot = GpuRuntimeSnapshot(
                True,
                devices=(
                    GpuDeviceStatus("0", "GPU-0", "A100", 81920, 12000, 69920, 30),
                    GpuDeviceStatus("1", "GPU-1", "A100", 81920, 2000, 79920, 5),
                    GpuDeviceStatus("2", "GPU-2", "A100", 81920, 1000, 80920, 80),
                ),
            )
            factory = FakeFactory()
            models = build_models(directories, environments, factory, gpu_observer=FakeGpuObserver(gpu_snapshot))
            models.assets.register_model("m", PLATFORM_SCOPE, model_dir)
            models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                deployment_id="d", service_id="model:d", model_id="m", engine="custom",
                scope=PLATFORM_SCOPE,
                executable="{python}", argv=("{python}", "-V"), cwd=root,
                python_environment_id="env-a", desired_state=ModelDesiredState.RUNNING,
            ))
            candidates = models.resources.gpu_candidates(count=2, min_free_memory_mb=60000, max_utilization_percent=50)
            self.assertEqual(tuple(device.index for device in candidates), ("1", "0"))
            updated = models.deployment_catalog.set_gpu_devices("d", ("1", "0", "1"))
            self.assertEqual(updated.gpu_devices, ("1", "0"))
            updated = models.deployment_catalog.set_python_environment("d", "env-b")
            self.assertEqual(updated.python_environment_id, "env-b")
            self.assertFalse(factory.runtime.live)


    def test_named_model_storage_pools_keep_large_assets_on_selected_volume(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            storage = LocalModelAssetStorage(directories.layout, additional_pools={"nvme": root / "nvme-models"})
            environments = build_environments(directories)
            asset_registry = ModelAssetRegistry(directories.layout)
            deployment_registry = ModelDeploymentRegistry(directories.layout)
            catalog = ModelDeploymentCatalog(asset_registry, deployment_registry, environments.lifecycle)
            assets = ModelAssetManager(asset_registry, DeploymentModelAssetReferences(catalog), storage, ())
            source = root / "weights"; source.mkdir(); (source / "model.safetensors").write_bytes(b"weights")
            asset = assets.register_model("fast", PLATFORM_SCOPE, source, mode="copy", storage_pool="nvme")
            self.assertEqual(asset.storage_pool, "nvme")
            self.assertEqual(asset.path.parent, (root / "nvme-models").resolve())
            pools = {pool.pool_id: pool for pool in assets.storage_pools()}
            self.assertEqual(set(pools), {"default", "nvme"})
            self.assertEqual(pools["nvme"].path, (root / "nvme-models").resolve())


    def test_tags_and_selectors_support_group_management_without_starting_services(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve-a", PLATFORM_SCOPE, backend="fake", tags=("gpu", "online")))
            environments.lifecycle.create(PythonEnvironmentSpec("serve-b", PLATFORM_SCOPE, backend="fake", tags=("gpu", "batch")))
            self.assertEqual(tuple(value.environment_id for value in environments.lifecycle.list(tags=("online",))), ("serve-a",))

            model_a = root / "model-a"; model_a.mkdir()
            model_b = root / "model-b"; model_b.mkdir()
            factory = FakeFactory()
            models = build_models(directories, environments, factory)
            models.assets.register_model("a", PLATFORM_SCOPE, model_a, family="example-family", tags=("large", "chat"))
            models.assets.register_model("b", PLATFORM_SCOPE, model_b, family="example-family", tags=("small",))
            self.assertEqual(tuple(value.model_id for value in models.assets.models(tags=("large",))), ("a",))
            self.assertEqual(len(models.assets.models(family="example-family")), 2)

            for deployment_id, env_id, tags in (
                ("chat-a", "serve-a", ("online", "chat")),
                ("batch-b", "serve-b", ("batch",)),
            ):
                models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                    deployment_id=deployment_id, service_id=f"model:{deployment_id}",
                scope=PLATFORM_SCOPE,
                    model_id="a" if deployment_id == "chat-a" else "b", engine="custom",
                    executable="{python}", argv=("{python}", "-V"), cwd=root,
                    python_environment_id=env_id, tags=tags,
                ))
            selected = models.deployment_catalog.select(ModelDeploymentSelector(tags=("online",)))
            self.assertEqual(tuple(value.deployment_id for value in selected), ("chat-a",))
            desired = models.deployment_catalog.set_desired_state_selected(
                ModelDeploymentSelector(tags=("online",)), ModelDesiredState.RUNNING
            )
            self.assertEqual(desired[0].desired_state, ModelDesiredState.RUNNING)
            self.assertFalse(factory.runtime.live)


    def test_desired_state_controller_runs_multiple_cycles_and_persists_status(self):
        class Stop:
            def __init__(self):
                self.waits = 0
            def wait(self, timeout=None):
                self.waits += 1
                return False

        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            environments = build_environments(directories)
            environments.lifecycle.create(PythonEnvironmentSpec("serve", PLATFORM_SCOPE, backend="fake"))
            model_dir = root / "model"; model_dir.mkdir()
            models = build_models(directories, environments, FakeFactory())
            models.assets.register_model("m", PLATFORM_SCOPE, model_dir)
            models.deployment_catalog.put_deployment(ModelDeploymentSpec(
                deployment_id="d", service_id="model:d", model_id="m", engine="custom",
                scope=PLATFORM_SCOPE,
                executable="{python}", argv=("{python}", "-V"), cwd=root,
                python_environment_id="serve",
                desired_state=ModelDesiredState.RUNNING,
            ))
            state = models.controller.run(interval_seconds=0.01, stop=Stop(), max_cycles=2)
            self.assertEqual(state.phase.value, "stopped")
            self.assertEqual(state.cycle_count, 2)
            self.assertIsNone(state.pid)
            self.assertEqual(state.last_cycle.statuses[0].runtime_state, ModelRuntimeState.RUNNING)
            persisted = models.controller.snapshot()
            self.assertEqual(persisted.cycle_count, 2)
            self.assertEqual(persisted.last_cycle.cycle_index, 2)


    def test_python_environment_export_and_clone_are_management_operations(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            runner = FakeCommandRunner()
            environments = build_environments(directories, runner)
            environments.lifecycle.create(PythonEnvironmentSpec("source", PLATFORM_SCOPE, backend="fake"))
            exported = environments.packages.export_requirements("source", root / "exports" / "source.txt")
            self.assertIn("pytest==9.0", exported.read_text("utf-8"))
            cloned = environments.packages.clone(
                "source",
                PythonEnvironmentSpec("clone", PLATFORM_SCOPE, backend="fake", tags=("copy",)),
            )
            self.assertEqual(cloned.source_environment_id, "source")
            self.assertEqual(cloned.environment.environment_id, "clone")
            self.assertEqual(cloned.requirements_count, 2)
            self.assertEqual(cloned.install_result.returncode, 0)
            self.assertFalse((directories.layout.layout.temp / "python-env-clone" / "clone.requirements.txt").exists())

    def test_directory_cleanup_plan_is_non_destructive_until_clean_is_requested(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            directories = build_local_directory_authorities(layout(root))
            candidate = directories.layout.root(ManagedDirectoryKind.CACHE) / "download.partial"
            candidate.mkdir()
            (candidate / "chunk.bin").write_bytes(b"1234")
            plan = directories.cleanup.clean_plan(ManagedDirectoryKind.CACHE)
            self.assertEqual(plan[0].path, candidate)
            self.assertEqual(plan[0].bytes, 4)
            self.assertTrue(candidate.exists())
            removed = directories.cleanup.clean(ManagedDirectoryKind.CACHE)
            self.assertEqual(removed, (candidate,))
            self.assertFalse(candidate.exists())


if __name__ == "__main__":
    unittest.main()
