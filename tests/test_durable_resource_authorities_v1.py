from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from noetrium_platform.composition.platform_meta import build_durable_platform_meta
from noetrium_platform.infrastructure.resources.allocation.api import EndpointAllocationRequest, EndpointProbeResult, NetworkEndpoint
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
