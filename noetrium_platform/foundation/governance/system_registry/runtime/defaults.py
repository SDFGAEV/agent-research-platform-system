from __future__ import annotations

from pathlib import Path

from noetrium_platform.foundation.governance.system_registry.api import (
    audit_system_topology_source,
    system_catalog,
)

from .registry import InMemorySystemRegistry


def build_default_system_registry(root: str | Path | None = None) -> InMemorySystemRegistry:
    audit = audit_system_topology_source(root)
    if not audit.clean:
        details = "; ".join(
            (
                *(f"stale:{item}" for item in audit.stale_registered_packages),
                *(f"unregistered:{item}" for item in audit.unregistered_standard_packages),
            )
        )
        raise RuntimeError(f"system topology source audit failed: {details}")
    registry = InMemorySystemRegistry()
    registry.register_many(system_catalog())
    return registry


__all__ = ["build_default_system_registry"]
