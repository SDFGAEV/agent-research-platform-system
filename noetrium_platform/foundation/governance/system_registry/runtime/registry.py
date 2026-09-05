from __future__ import annotations

from collections import deque
from collections.abc import Callable
import hashlib
import json

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
    SystemRegistryChange,
    SystemRegistryObserver,
)


def _topology_digest(descriptors: tuple[SystemDescriptor, ...]) -> str:
    """Fingerprint a canonical, language-independent descriptor snapshot."""

    payload = [
        {
            "identity": {
                "system_id": descriptor.identity.system_id,
                "subsystem_path": list(descriptor.identity.subsystem_path),
            },
            "layer": descriptor.layer.value,
            "package_prefix": descriptor.package_prefix,
            "provides": list(descriptor.provides),
            "requires": list(descriptor.requires),
            "authorities": [
                {
                    "authority_id": authority.authority_id,
                    "state_kinds": list(authority.state_kinds),
                    "effect_kinds": list(authority.effect_kinds),
                    "artifact_kinds": list(authority.artifact_kinds),
                }
                for authority in descriptor.authorities
            ],
            "components": list(descriptor.components),
            "owns": descriptor.owns,
            "must_not_own": descriptor.must_not_own,
            "shape": list(descriptor.shape),
        }
        for descriptor in descriptors
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        self.register_many((descriptor,))

    def register_many(
        self,
        descriptors: tuple[SystemDescriptor, ...],
    ) -> tuple[SystemDescriptor, ...]:
        if not isinstance(descriptors, tuple):
            raise TypeError("descriptors must be a tuple")

        pending: dict[str, SystemDescriptor] = {}
        for descriptor in descriptors:
            key = descriptor.identity.key
            current = self._items.get(key) or pending.get(key)
            if current is not None:
                if current != descriptor:
                    raise SystemRegistryConflict(key)
                continue
            pending[key] = descriptor

        ordered = tuple(
            sorted(
                pending.values(),
                key=lambda item: (item.identity.depth, item.identity.key),
            )
        )
        for descriptor in ordered:
            parent = descriptor.parent_key
            if parent is not None and parent not in self._items and parent not in pending:
                raise SystemRegistryNotFound(parent)

        for descriptor in ordered:
            key = descriptor.identity.key
            parent = descriptor.parent_key
            self._items[key] = descriptor
            if parent is not None:
                self._children.setdefault(parent, set()).add(key)
            self._children.setdefault(key, set())
            self._generation += 1

        if not ordered:
            return ()

        self._topology_digest = None
        digest = self.topology_digest
        change = SystemRegistryChange(
            registered=ordered,
            generation=self._generation,
            topology_digest=digest,
        )
        failures: list[BaseException] = []
        for observer in tuple(self._observers):
            try:
                observer(change)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup(
                "system registry observer notification failed",
                tuple(failures),
            )
        return ordered

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
