from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

from noetrium_platform.infrastructure.lifecycle.python.api import (
    ManagedPythonEnvironment,
    PythonEnvironmentOwnership,
    PythonEnvironmentState,
)
from noetrium_platform.foundation.kernel.kernel import canonical_bytes, canonical_digest
from noetrium_platform.foundation.kernel.kernel.durability.durable_file import atomic_replace_bytes, durable_unlink, fsync_directory
from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayoutPort, ManagedDirectoryKind
from noetrium_platform.foundation.scope.api import ScopeIdentity, scope_from_data, scope_to_data


_SCHEMA = "python-environment-lifecycle-transaction.v1"
_CREATE_PHASES = frozenset({"prepared", "materialized", "committed"})
_REMOVE_PHASES = frozenset({"prepared", "unregistered", "committed"})


@dataclass(frozen=True, slots=True)
class PythonEnvironmentLifecycleTransaction:
    operation: str
    phase: str
    environment_id: str
    scope: ScopeIdentity
    backend: str
    root: Path
    python_path: Path
    ownership: PythonEnvironmentOwnership
    description: str
    tags: tuple[str, ...]
    specification_digest: str

    def __post_init__(self) -> None:
        phases = _CREATE_PHASES if self.operation == "create" else _REMOVE_PHASES if self.operation == "remove" else None
        if phases is None or self.phase not in phases:
            raise ValueError("invalid Python environment lifecycle transaction phase")
        if not self.environment_id or any(char in self.environment_id for char in "/\\\x00\r\n"):
            raise ValueError("invalid Python environment lifecycle transaction id")
        if not self.root.is_absolute() or not self.python_path.is_absolute():
            raise ValueError("Python environment lifecycle transaction paths must be absolute")
        if len(self.specification_digest) != 64:
            raise ValueError("Python environment lifecycle transaction specification digest is invalid")

    def with_phase(self, phase: str) -> "PythonEnvironmentLifecycleTransaction":
        return replace(self, phase=phase)

    def environment(self, *, state: PythonEnvironmentState = PythonEnvironmentState.READY) -> ManagedPythonEnvironment:
        return ManagedPythonEnvironment(
            self.environment_id,
            self.scope,
            self.backend,
            self.root,
            self.python_path,
            state,
            self.ownership,
            self.description,
            self.tags,
            self.specification_digest,
        )


class PythonEnvironmentLifecycleTransactionStore:
    """Crash-durable per-environment lifecycle intent/phase journal."""

    def __init__(self, directories: DirectoryLayoutPort) -> None:
        self._root = directories.root(ManagedDirectoryKind.STATE) / "python-environment-lifecycle"
        created = not self._root.exists()
        self._root.mkdir(parents=True, exist_ok=True)
        if created:
            fsync_directory(self._root.parent)

    def _path(self, environment_id: str) -> Path:
        if not environment_id or any(char in environment_id for char in "/\\\x00\r\n"):
            raise ValueError("invalid Python environment id")
        return self._root / f"{environment_id}.json"

    @staticmethod
    def _payload(value: PythonEnvironmentLifecycleTransaction) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA,
            "operation": value.operation,
            "phase": value.phase,
            "environment_id": value.environment_id,
            "scope": scope_to_data(value.scope),
            "backend": value.backend,
            "root": str(value.root),
            "python_path": str(value.python_path),
            "ownership": value.ownership.value,
            "description": value.description,
            "tags": list(value.tags),
            "specification_digest": value.specification_digest,
        }

    def put(self, value: PythonEnvironmentLifecycleTransaction) -> PythonEnvironmentLifecycleTransaction:
        payload = self._payload(value)
        document = dict(payload)
        document["record_digest"] = canonical_digest(payload)
        atomic_replace_bytes(self._path(value.environment_id), canonical_bytes(document))
        return value

    def get(self, environment_id: str) -> PythonEnvironmentLifecycleTransaction | None:
        path = self._path(environment_id)
        if not path.exists():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Python environment lifecycle transaction is unreadable: {environment_id}") from exc

        expected = {
            "schema_version", "operation", "phase", "environment_id", "scope", "backend",
            "root", "python_path", "ownership", "description", "tags", "specification_digest",
            "record_digest",
        }
        if not isinstance(document, dict) or set(document) != expected:
            raise RuntimeError(f"Python environment lifecycle transaction schema is invalid: {environment_id}")
        digest = document.pop("record_digest")
        if not isinstance(digest, str) or canonical_digest(document) != digest.lower():
            raise RuntimeError(f"Python environment lifecycle transaction digest mismatch: {environment_id}")
        if document.get("schema_version") != _SCHEMA or document.get("environment_id") != environment_id:
            raise RuntimeError(f"Python environment lifecycle transaction identity mismatch: {environment_id}")
        try:
            return PythonEnvironmentLifecycleTransaction(
                operation=str(document["operation"]),
                phase=str(document["phase"]),
                environment_id=environment_id,
                scope=scope_from_data(document["scope"]),
                backend=str(document["backend"]),
                root=Path(str(document["root"])),
                python_path=Path(str(document["python_path"])),
                ownership=PythonEnvironmentOwnership(str(document["ownership"])),
                description=str(document["description"]),
                tags=tuple(str(item) for item in document["tags"]),
                specification_digest=str(document["specification_digest"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Python environment lifecycle transaction fields are invalid: {environment_id}") from exc

    def all(self) -> tuple[PythonEnvironmentLifecycleTransaction, ...]:
        return tuple(value for path in sorted(self._root.glob("*.json")) if (value := self.get(path.stem)) is not None)

    def remove(self, environment_id: str) -> None:
        durable_unlink(self._path(environment_id))


__all__ = ["PythonEnvironmentLifecycleTransaction", "PythonEnvironmentLifecycleTransactionStore"]
