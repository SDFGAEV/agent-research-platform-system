from __future__ import annotations

from collections import deque
from collections.abc import Callable
import hashlib

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
    SystemRegistryObserver,
)


def _topology_digest(descriptors: tuple[SystemDescriptor, ...]) -> str:
    """Fingerprint the registry's ordered descriptor snapshot without kernel coupling."""

    payload = "\n".join(repr(descriptor) for descriptor in descriptors)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SystemRegistryConflict(RuntimeError):
    pass


class SystemRegistryNotFound(KeyError):
    pass


class InMemorySystemRegistry:
    """Recursive system-tree authority. It owns topology, not system behavior."""

    def __init__(self) -> None:
        self._items: dict[str, SystemDescriptor] = {}
        self._children: dict[str, set[str]] = {}
        self._observers: list[SystemRegistryObserver] = []
        self._generation = 0
        self._topology_digest: str | None = _topology_digest(())

    @property
    def generation(self) -> int:
        """Monotonic topology generation for runtime evidence correlation."""

        return self._generation

    @property
    def topology_digest(self) -> str:
        """Lazily compute the canonical descriptor digest once per generation."""

        if self._topology_digest is None:
            self._topology_digest = _topology_digest(self.list())
        return self._topology_digest

    def register(self, descriptor: SystemDescriptor) -> None:
        key = descriptor.identity.key
        current = self._items.get(key)
        if current is not None:
            if current != descriptor:
                raise SystemRegistryConflict(key)
            return

        parent = descriptor.parent_key
        if parent is not None and parent not in self._items:
            raise SystemRegistryNotFound(parent)

        self._items[key] = descriptor
        if parent is not None:
            self._children.setdefault(parent, set()).add(key)
        self._children.setdefault(key, set())
        self._generation += 1
        self._topology_digest = None
        digest = self.topology_digest
        failures: list[BaseException] = []
        for observer in tuple(self._observers):
            try:
                observer(descriptor, self._generation, digest)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup(
                "system registry observer notification failed",
                tuple(failures),
            )

    def subscribe(self, observer: SystemRegistryObserver) -> Callable[[], None]:
        if observer not in self._observers:
            self._observers.append(observer)

        def unsubscribe() -> None:
            try:
                self._observers.remove(observer)
            except ValueError:
                pass

        return unsubscribe

    def contains(self, key: str) -> bool:
        return key in self._items

    def get(self, key: str) -> SystemDescriptor:
        try:
            return self._items[key]
        except KeyError as exc:
            raise SystemRegistryNotFound(key) from exc

    def validate(self, identity: SystemIdentity) -> SystemDescriptor:
        """Resolve a diagnostic/runtime identity or fail closed."""

        descriptor = self.get(identity.key)
        if descriptor.identity != identity:
            raise SystemRegistryConflict(identity.key)
        return descriptor

    def list(self) -> tuple[SystemDescriptor, ...]:
        return tuple(self._items[k] for k in sorted(self._items))

    def children(self, key: str) -> tuple[SystemDescriptor, ...]:
        self.get(key)
        return tuple(self._items[child_key] for child_key in sorted(self._children[key]))

    def descendants(self, key: str) -> tuple[SystemDescriptor, ...]:
        self.get(key)
        result: list[SystemDescriptor] = []
        frontier = deque([key])
        while frontier:
            parent = frontier.popleft()
            child_keys = sorted(self._children[parent])
            result.extend(self._items[child_key] for child_key in child_keys)
            frontier.extend(child_keys)
        return tuple(result)

    def ancestors(self, key: str) -> tuple[SystemDescriptor, ...]:
        current = self.get(key)
        result: list[SystemDescriptor] = []
        while current.parent_key is not None:
            current = self.get(current.parent_key)
            result.append(current)
        return tuple(result)

    def owner_for_module(self, module: str) -> SystemDescriptor | None:
        candidates = [
            row
            for row in self._items.values()
            if module == row.package_prefix or module.startswith(row.package_prefix + ".")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: len(row.package_prefix))


__all__ = ["InMemorySystemRegistry", "SystemRegistryConflict", "SystemRegistryNotFound"]
