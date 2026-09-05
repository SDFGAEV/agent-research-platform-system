from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from noetrium_platform.composition.platform_meta import build_durable_platform_meta
from noetrium_platform.infrastructure.resources.allocation.api import EndpointAllocationRequest, EndpointProbeResult, NetworkEndpoint
from noetrium_platform.infrastructure.resources.compute.api import ComputeHost, ComputeRequirement
from noetrium_platform.capabilities.environment.catalog.api import (
    EnvironmentAssignment,
    EnvironmentBinding,
    EnvironmentInstance,
    EnvironmentSpec,
    ExecutionEnvironmentKind,
)
from noetrium_platform.infrastructure.resources.providers import SQLiteEndpointAllocationStore
from noetrium_platform.infrastructure.resources.allocation.runtime import AtomicEndpointAllocator
from noetrium_platform.infrastructure.resources.lease.api import (
    ResourceIdentity,
    ResourceKind,
    ResourceLease,
    ResourceOwner,
    ResourceOwnership,
)
from noetrium_platform.infrastructure.resources.providers import SQLiteResourceLeaseRegistry
from noetrium_platform.infrastructure.resources.lease.runtime import ResourceLeaseConflict
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE, ScopeIdentity, ScopeKind
from noetrium_platform.foundation.scope.providers import SQLiteScopeRegistry
from noetrium_platform.foundation.portfolio.api import (
    ProgramSpec,
    ProjectIdentity,
    ProjectManifest,
    ProjectSpec,
    ProjectToolProvenance,
    WorkspaceSpec,
)
from noetrium_platform.research.experimentation.run.identity.api import RunIdentity
from noetrium_platform.research.experimentation.study import StudySpec
from noetrium_platform.research.experimentation.experiment.api import ExperimentSpec


class _AvailableProbe:
    def probe(self, endpoint: NetworkEndpoint) -> EndpointProbeResult:
        return EndpointProbeResult(endpoint, True, "test probe")


class DurableResourceAuthoritiesTests(TestCase):
    def test_scope_and_lease_survive_rebuild_and_fence_conflicts(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "platform.sqlite"
            workspace = ScopeIdentity(ScopeKind.WORKSPACE, "workspace")
            resource = ResourceIdentity(ResourceKind.COMPUTE, "host-1")
            scopes = SQLiteScopeRegistry(database)
            scopes.register(workspace, PLATFORM_SCOPE)
            owners = SQLiteResourceLeaseRegistry(database)
            owner = ResourceOwner(resource, PLATFORM_SCOPE, ResourceOwnership.PLATFORM_MANAGED)
            owners.register_owner(owner)
            lease = ResourceLease("lease-1", resource, workspace, "test allocation")
            owners.acquire(lease)

            restored_scopes = SQLiteScopeRegistry(database)
            restored_owners = SQLiteResourceLeaseRegistry(database)
            self.assertEqual(restored_scopes.ancestry(workspace), (workspace, PLATFORM_SCOPE))
            self.assertEqual(restored_owners.get("lease-1"), lease)
            with self.assertRaises(ResourceLeaseConflict):
                restored_owners.acquire(ResourceLease("lease-2", resource, workspace, "competing allocation"))

    def test_endpoint_allocation_survives_rebuild_and_release_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "platform.sqlite"
            workspace = ScopeIdentity(ScopeKind.WORKSPACE, "workspace")
            scopes = SQLiteScopeRegistry(database)
            scopes.register(workspace, PLATFORM_SCOPE)
            leases = SQLiteResourceLeaseRegistry(database)
            store = SQLiteEndpointAllocationStore(database)
            allocator = AtomicEndpointAllocator(
                reservations=store,
                probe=_AvailableProbe(),
            )
            request = EndpointAllocationRequest(
                allocation_id="allocation-1",
                holder_scope=workspace,
                purpose="minecraft test server",
                host="127.0.0.1",
                candidate_ports=(25565, 25566),
            )
            allocation = allocator.allocate(request)
            restored = AtomicEndpointAllocator(
                reservations=SQLiteEndpointAllocationStore(database),
                probe=_AvailableProbe(),
            )
            self.assertEqual(restored.allocate(request), allocation)
            released = restored.release(request.allocation_id)
            self.assertEqual(restored.release(request.allocation_id), released)
            self.assertEqual(restored.active(), ())

    def test_durable_platform_meta_uses_one_authority_database(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_durable_platform_meta(root)
            workspace = ScopeIdentity(ScopeKind.WORKSPACE, "workspace")
            first.scopes.register(workspace, PLATFORM_SCOPE)
            second = build_durable_platform_meta(root)
            self.assertTrue(second.scopes.contains(workspace))
            self.assertEqual((root / "platform-meta.sqlite").is_file(), True)

    def test_durable_portfolio_survives_rebuild_with_canonical_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_durable_platform_meta(root)
            first.portfolio.register_workspace(WorkspaceSpec("workspace", "Workspace"))
            first.portfolio.register_program(ProgramSpec("program", "workspace", "Program"))
            manifest = ProjectManifest(
                ProjectSpec(ProjectIdentity("project", "1.0.0"), "program", "Project"),
                "template-1",
                ProjectToolProvenance("tool", "1.0.0", "0" * 64),
            )
            first.portfolio.register_project(manifest)

            second = build_durable_platform_meta(root)
            self.assertEqual(second.portfolio.workspace("workspace").name, "Workspace")
            self.assertEqual(second.portfolio.program("program").workspace_id, "workspace")
            self.assertEqual(second.portfolio.project("project"), manifest)
            self.assertEqual(second.portfolio.projects(program_id="program"), (manifest,))

    def test_durable_experimentation_survives_rebuild(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_durable_platform_meta(root)
            first.portfolio.register_workspace(WorkspaceSpec("workspace", "Workspace"))
            first.portfolio.register_program(ProgramSpec("program", "workspace", "Program"))
            manifest = ProjectManifest(
                ProjectSpec(ProjectIdentity("project", "1.0.0"), "program", "Project"),
                "template-1",
                ProjectToolProvenance("tool", "1.0.0", "0" * 64),
            )
            first.portfolio.register_project(manifest)
            first.experimentation.register_study(
                StudySpec("study", "project", "Study", ("experiment",))
            )
            experiment = ExperimentSpec(
                "experiment", "study", "project", (), None, None,
                "1" * 64, "2" * 64, 1, "protocol", "3" * 64
            )
            first.experimentation.register_experiment(experiment)
            run = RunIdentity("run", "session", "trace")
            first.experimentation.register_run("experiment", run)
            second = build_durable_platform_meta(root)
            self.assertEqual(second.experimentation.study("study").name, "Study")
            self.assertEqual(second.experimentation.experiment("experiment"), experiment)
            self.assertEqual(second.experimentation.experiments(study_id="study"), (experiment,))

    def test_durable_compute_inventory_and_allocations_survive_rebuild(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            scope = ScopeIdentity(ScopeKind.WORKSPACE, "workspace")
            first = build_durable_platform_meta(root)
            first.scopes.register(scope, PLATFORM_SCOPE)
            first.compute_inventory.register_host(
                ComputeHost("host-1", scope, 8, 1024)
            )
            requirement = ComputeRequirement(cpu_cores=2, memory_bytes=256)
            allocation = first.compute_scheduler.allocate("compute-1", scope, requirement)
            second = build_durable_platform_meta(root)
            self.assertEqual(second.compute_inventory.host("host-1").cpu_cores, 8)
            self.assertEqual(second.compute_scheduler.allocations(), (allocation,))
            second.compute_scheduler.release("compute-1")
            self.assertEqual(second.compute_scheduler.allocations(), ())

    def test_durable_environment_hierarchy_survives_rebuild(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            scope = ScopeIdentity(ScopeKind.WORKSPACE, "workspace")
            first = build_durable_platform_meta(root)
            first.scopes.register(scope, PLATFORM_SCOPE)
            spec = EnvironmentSpec(
                "python-base", ExecutionEnvironmentKind.PYTHON, scope,
                requirements=(("python", "3.12"),),
                environment=(("PYTHONUNBUFFERED", "1"),),
            )
            first.environments.register_spec(spec)
            first.environments.assign(EnvironmentAssignment("default", "python-base", scope))
            instance = EnvironmentInstance("env-1", "1" * 64, "local", "python.exe", scope)
            first.environments.register_instance(instance)
            binding = EnvironmentBinding("binding-1", scope, "runner", "env-1")
            first.environments.bind(binding)
            second = build_durable_platform_meta(root)
            resolved = second.environments.resolve("default", scope)
            self.assertEqual(resolved.requirements, (("python", "3.12"),))
            self.assertEqual(second.environments.binding("runner", scope), binding)
