from __future__ import annotations

import json
from pathlib import Path

from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayoutPort, ManagedDirectoryKind
from noetrium_platform.infrastructure.lifecycle.python.api import (
    ManagedPythonEnvironment,
    PythonEnvironmentOwnership,
    PythonEnvironmentSpec,
    PythonEnvironmentState,
)
from noetrium_platform.foundation.kernel.kernel.durability.durable_file import atomic_replace_bytes, durable_unlink, fsync_directory
from noetrium_platform.foundation.scope.api import scope_from_data, scope_to_data


class PythonEnvironmentRegistry:
    """Authoritative operator metadata for registered Python environments."""

    def __init__(self, directories: DirectoryLayoutPort) -> None:
        self._root = directories.root(ManagedDirectoryKind.STATE) / "python-environments"
        created = not self._root.exists()
        self._root.mkdir(parents=True, exist_ok=True)
        if created:
            fsync_directory(self._root.parent)

    def put(self, value: ManagedPythonEnvironment) -> ManagedPythonEnvironment:
        self._validate_id(value.environment_id)
        payload = json.dumps(
            {
                "environment_id": value.environment_id,
                "scope": scope_to_data(value.scope),
                "backend": value.backend,
                "root": str(value.root),
                "python_path": str(value.python_path),
                "ownership": value.ownership.value,
                "description": value.description,
                "tags": list(value.tags),
                "specification_digest": value.specification_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        atomic_replace_bytes(self._root / f"{value.environment_id}.json", payload)
        return value

    def get(self, environment_id: str) -> ManagedPythonEnvironment:
        self._validate_id(environment_id)
        path = self._root / f"{environment_id}.json"
        if not path.exists():
            raise KeyError(environment_id)
        data = json.loads(path.read_text("utf-8"))
        specification_digest = str(data.get("specification_digest", ""))
        if len(specification_digest) != 64:
            raise RuntimeError(
                f"Python environment registry entry lacks an immutable specification digest: {environment_id}"
            )
        value = ManagedPythonEnvironment(
            environment_id=str(data["environment_id"]),
            scope=scope_from_data(data["scope"]),
            backend=str(data["backend"]),
            root=Path(str(data["root"])),
            python_path=Path(str(data["python_path"])),
            state=PythonEnvironmentState.REGISTERED,
            ownership=PythonEnvironmentOwnership(str(data["ownership"])),
            description=str(data.get("description", "")),
            tags=tuple(str(item) for item in data.get("tags", ())),
            specification_digest=specification_digest,
        )
        state = PythonEnvironmentState.READY if value.python_path.exists() else PythonEnvironmentState.MISSING
        return ManagedPythonEnvironment(
            value.environment_id,
            value.scope,
            value.backend,
            value.root,
            value.python_path,
            state,
            value.ownership,
            value.description,
            value.tags,
            value.specification_digest,
        )

    def migrate_legacy(
        self,
        environment_id: str,
        *,
        python_executable: str,
        python_version: str,
    ) -> ManagedPythonEnvironment:
        """Explicitly convert one old metadata record into the current schema.

        The old record never contained the base interpreter or Python version,
        so those values must be supplied by the operator. The method derives
        all other fields from the old record and refuses to guess if the
        declared interpreter does not equal the materialized interpreter path.
        """

        self._validate_id(environment_id)
        path = self._root / f"{environment_id}.json"
        if not path.exists():
            raise KeyError(environment_id)
        data = json.loads(path.read_text("utf-8"))
        existing = str(data.get("specification_digest", ""))
        if existing:
            return self.get(environment_id)
        # Keep the operator-declared lexical path.  ``Path.resolve()`` follows
        # symlinks and can turn a virtual environment's own ``bin/python``
        # entry into another environment's interpreter.  That is a different
        # runtime identity: Python uses the lexical entry point to discover
        # the venv prefix, while the registry must preserve the path callers
        # actually execute.  We normalize to an absolute path without
        # dereferencing links and compare the two declarations exactly.
        python_path = self._absolute_lexical_path(str(data["python_path"]))
        declared = self._absolute_lexical_path(python_executable)
        if declared != python_path:
            raise ValueError(
                "legacy migration python_executable must equal the registered interpreter path"
            )
        spec = PythonEnvironmentSpec(
            environment_id=environment_id,
            scope=scope_from_data(data["scope"]),
            backend=str(data["backend"]),
            python_executable=str(declared),
            python_version=python_version,
            description=str(data.get("description", "")),
            tags=tuple(str(item) for item in data.get("tags", ())),
        )
        value = ManagedPythonEnvironment(
            environment_id=environment_id,
            scope=spec.scope,
            backend=spec.backend,
            root=self._absolute_lexical_path(str(data["root"])),
            python_path=python_path,
            state=PythonEnvironmentState.READY if python_path.exists() else PythonEnvironmentState.MISSING,
            ownership=PythonEnvironmentOwnership(str(data["ownership"])),
            description=spec.description,
            tags=tuple(sorted(set(spec.tags))),
            specification_digest=spec.specification_digest,
        )
        return self.put(value)

    def all(self) -> tuple[ManagedPythonEnvironment, ...]:
        return tuple(self.get(path.stem) for path in sorted(self._root.glob("*.json")))

    def remove(self, environment_id: str) -> bool:
        self._validate_id(environment_id)
        path = self._root / f"{environment_id}.json"
        if not path.exists():
            return False
        durable_unlink(path)
        return True

    @staticmethod
    def _validate_id(value: str) -> None:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("invalid Python environment id")

    @staticmethod
    def _absolute_lexical_path(value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else path.absolute()


__all__ = ["PythonEnvironmentRegistry"]
