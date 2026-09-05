from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from noetrium_platform.evidence.artifact.catalog.api import ArtifactRegistryPort
from noetrium_platform.evidence.artifact.catalog.runtime import InMemoryArtifactRegistry
from noetrium_platform.evidence.data.dataset.api import DatasetRegistryPort
from noetrium_platform.evidence.data.dataset.runtime import InMemoryDatasetRegistry
from noetrium_platform.research.experimentation.catalog.api import ExperimentationCatalogPort
from noetrium_platform.research.experimentation.catalog.runtime import InMemoryExperimentationCatalog
from noetrium_platform.foundation.governance.evolution.api import SystemEvolutionPort
from noetrium_platform.foundation.governance.evolution.providers import SQLiteEvolutionStore
from noetrium_platform.foundation.governance.evolution.runtime import RegistryDrivenEvolutionController
from noetrium_platform.foundation.governance.system_registry.api import SystemRegistryPort
from noetrium_platform.foundation.governance.system_registry.runtime import build_default_system_registry
from noetrium_platform.foundation.portfolio.api import PortfolioCatalogPort
from noetrium_platform.foundation.portfolio.runtime import (
    InMemoryPortfolioCatalog,
    SQLitePortfolioCatalog,
)
from noetrium_platform.infrastructure.resources.compute.api import ComputeInventoryPort, ComputeSchedulerPort
from noetrium_platform.infrastructure.resources.allocation.api import EndpointAllocationPort
from noetrium_platform.infrastructure.resources.allocation.providers import SocketEndpointProbe
from noetrium_platform.infrastructure.resources.providers import (
    SQLiteEndpointAllocationStore,
    SQLiteResourceLeaseRegistry,
)
from noetrium_platform.infrastructure.resources.providers.sqlite_connection import (
    durable_sqlite_connection,
)
from noetrium_platform.infrastructure.resources.allocation.runtime import AtomicEndpointAllocator, InMemoryEndpointAllocator
from noetrium_platform.infrastructure.resources.compute.runtime import InMemoryComputeInventory, InMemoryComputeScheduler
from noetrium_platform.infrastructure.resources.lease.api import ResourceLeasePort, ResourceOwnershipPort
from noetrium_platform.infrastructure.resources.lease.runtime import InMemoryResourceLeaseRegistry
from noetrium_platform.capabilities.environment.catalog.api import ExecutionEnvironmentCatalogPort
from noetrium_platform.capabilities.environment.catalog.runtime import ExecutionEnvironmentCatalog
from noetrium_platform.foundation.scope.api import ScopeRegistryPort
from noetrium_platform.foundation.scope.runtime import InMemoryScopeRegistry
from noetrium_platform.foundation.scope.providers import SQLiteScopeRegistry
from noetrium_platform.foundation.governance.architecture.runtime.capability_composition import (
    CapabilityCompositionPlanner,
)


@dataclass(frozen=True, slots=True)
class PlatformMetaAuthorities:
    """Behavior-free bundle used only by composition roots and top-level management surfaces."""

    systems: SystemRegistryPort
    evolution: SystemEvolutionPort
    scopes: ScopeRegistryPort
    capability_composition: CapabilityCompositionPlanner
    portfolio: PortfolioCatalogPort
    experimentation: ExperimentationCatalogPort
    environments: ExecutionEnvironmentCatalogPort
    artifacts: ArtifactRegistryPort
    datasets: DatasetRegistryPort
    resource_ownership: ResourceOwnershipPort
    resource_leases: ResourceLeasePort
    endpoint_allocations: EndpointAllocationPort
    compute_inventory: ComputeInventoryPort
    compute_scheduler: ComputeSchedulerPort


def build_in_memory_platform_meta() -> PlatformMetaAuthorities:
    scopes = InMemoryScopeRegistry()
    systems = build_default_system_registry()
    evolution = RegistryDrivenEvolutionController(systems)
    resources = InMemoryResourceLeaseRegistry()
    compute_inventory = InMemoryComputeInventory()
    endpoint_allocations = InMemoryEndpointAllocator(
        ownership=resources,
        leases=resources,
        probe=SocketEndpointProbe(),
    )
    return PlatformMetaAuthorities(
        systems=systems,
        evolution=evolution,
        scopes=scopes,
        capability_composition=CapabilityCompositionPlanner(systems=systems, scopes=scopes),
        portfolio=InMemoryPortfolioCatalog(scopes),
        experimentation=InMemoryExperimentationCatalog(scopes),
        environments=ExecutionEnvironmentCatalog(scopes),
        artifacts=InMemoryArtifactRegistry(),
        datasets=InMemoryDatasetRegistry(),
        resource_ownership=resources,
        resource_leases=resources,
        endpoint_allocations=endpoint_allocations,
        compute_inventory=compute_inventory,
        compute_scheduler=InMemoryComputeScheduler(compute_inventory),
    )


def build_durable_platform_meta(root: str | Path) -> PlatformMetaAuthorities:
    """Build the production authority bundle over one durable SQLite root.

    Catalogs that are immutable project inputs remain lightweight registries;
    scope hierarchy, resource ownership/leases, and endpoint allocations share
    one SQLite authority and therefore survive process restart and coordinate
    competing workers.
    """

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    database = root / "platform-meta.sqlite"
    scopes = SQLiteScopeRegistry(database)
    systems = build_default_system_registry()
    evolution_store = SQLiteEvolutionStore(
        root / "platform-evolution.sqlite",
        connection_factory=durable_sqlite_connection,
    )
    evolution = RegistryDrivenEvolutionController(systems, store=evolution_store)
    resources = SQLiteResourceLeaseRegistry(database)
    endpoint_allocations = AtomicEndpointAllocator(
        reservations=SQLiteEndpointAllocationStore(database),
        probe=SocketEndpointProbe(),
    )
    compute_inventory = InMemoryComputeInventory()
    return PlatformMetaAuthorities(
        systems=systems,
        evolution=evolution,
        scopes=scopes,
        capability_composition=CapabilityCompositionPlanner(systems=systems, scopes=scopes),
        portfolio=SQLitePortfolioCatalog(database, scopes),
        experimentation=InMemoryExperimentationCatalog(scopes),
        environments=ExecutionEnvironmentCatalog(scopes),
        artifacts=InMemoryArtifactRegistry(),
        datasets=InMemoryDatasetRegistry(),
        resource_ownership=resources,
        resource_leases=resources,
        endpoint_allocations=endpoint_allocations,
        compute_inventory=compute_inventory,
        compute_scheduler=InMemoryComputeScheduler(compute_inventory),
    )


__all__ = ["PlatformMetaAuthorities", "build_durable_platform_meta", "build_in_memory_platform_meta"]
