from __future__ import annotations

from noetrium_platform.foundation.governance.system_registry.api import system_catalog

from .registry import InMemorySystemRegistry


def build_default_system_registry() -> InMemorySystemRegistry:
    registry = InMemorySystemRegistry()
    registry.register_many(system_catalog())
    return registry


__all__ = ["build_default_system_registry"]
