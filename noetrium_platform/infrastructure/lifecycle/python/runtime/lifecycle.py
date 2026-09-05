from __future__ import annotations

from functools import wraps
from pathlib import Path
import shutil
from threading import Lock, RLock

from noetrium_platform.infrastructure.lifecycle.python.api import (
    ManagedPythonEnvironment,
    PythonEnvironmentBackend,
    PythonEnvironmentOwnership,
    PythonEnvironmentSpec,
    PythonEnvironmentState,
)
from noetrium_platform.foundation.kernel.kernel.durability.durable_file import fsync_directory
from noetrium_platform.foundation.kernel.kernel.durability.file_lock import InterprocessFileLock
from noetrium_platform.infrastructure.resources.directory.api import DirectoryLayoutPort, ManagedDirectoryKind

from .lifecycle_transaction import (
    PythonEnvironmentLifecycleTransaction,
    PythonEnvironmentLifecycleTransactionStore,
)
from .registry import PythonEnvironmentRegistry


_PROCESS_LIFECYCLE_LOCKS: dict[str, RLock] = {}
_PROCESS_LIFECYCLE_LOCKS_GUARD = Lock()


def _process_lock_for(path: Path) -> RLock:
    key = str(path.resolve(strict=False)).casefold()
    with _PROCESS_LIFECYCLE_LOCKS_GUARD:
        return _PROCESS_LIFECYCLE_LOCKS.setdefault(key, RLock())


def _serialized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            with InterprocessFileLock(self._interprocess_lock_path):
                return method(self, *args, **kwargs)
    return wrapper


class PythonEnvironmentLifecycle:
    """Crash-recoverable create/register/remove authority plus lookup."""

    def __init__(
        self,
        directories: DirectoryLayoutPort,
        registry: PythonEnvironmentRegistry,
        backends: tuple[PythonEnvironmentBackend, ...],
    ) -> None:
        self._root = directories.root(ManagedDirectoryKind.PYTHON_ENVIRONMENTS)
        self._interprocess_lock_path = (
            directories.root(ManagedDirectoryKind.LOCKS) / "python-environment-lifecycle.lock"
        )
        self._lock = _process_lock_for(self._interprocess_lock_path)
        self._registry = registry
        self._transactions = PythonEnvironmentLifecycleTransactionStore(directories)
        self._backends = {backend.backend_id: backend for backend in backends}
        if len(self._backends) != len(backends):
            raise ValueError("duplicate Python environment backend")
        with self._lock:
            with InterprocessFileLock(self._interprocess_lock_path):
                self._recover_all_transactions()

    @_serialized
    def create(self, spec: PythonEnvironmentSpec) -> ManagedPythonEnvironment:
        self._validate_id(spec.environment_id)
        self._recover_transaction(spec.environment_id)
        backend = self._backend(spec.backend)
        root = self._root / spec.environment_id
        expected_python = backend.python_path(root)
        existing = self._registry_optional(spec.environment_id)
        if existing is not None:
            if (
                existing.state is PythonEnvironmentState.READY
                and existing.ownership is PythonEnvironmentOwnership.MANAGED
                and existing.root == root
                and existing.backend == spec.backend
                and existing.specification_digest == spec.specification_digest
            ):
                return existing
            raise FileExistsError(f"Python environment already registered: {spec.environment_id}")
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"Python environment already exists: {spec.environment_id}")
        transaction = PythonEnvironmentLifecycleTransaction(
            "create",
            "prepared",
            spec.environment_id,
            spec.scope,
            spec.backend,
            root,
            expected_python,
            PythonEnvironmentOwnership.MANAGED,
            spec.description,
            self._normalize_tags(spec.tags),
            spec.specification_digest,
        )
        self._transactions.put(transaction)
        try:
            python_path = backend.create(root, spec)
            if python_path != expected_python or not python_path.exists():
                raise RuntimeError(
                    f"Python environment backend returned an invalid interpreter: {python_path}"
                )
            transaction = self._transactions.put(transaction.with_phase("materialized"))
            value = transaction.environment()
            self._registry.put(value)
            self._transactions.put(transaction.with_phase("committed"))
            self._transactions.remove(spec.environment_id)
            return value
        except BaseException as primary:
            try:
                self._recover_transaction(spec.environment_id)
            except BaseException as recovery_exc:
                raise RuntimeError(
                    "Python environment creation failed and recovery was incomplete: "
                    f"primary={type(primary).__name__}: {primary}; "
                    f"recovery={type(recovery_exc).__name__}: {recovery_exc}"
                ) from primary
            raise

    @_serialized
    def register_existing(self, spec: PythonEnvironmentSpec, root: Path) -> ManagedPythonEnvironment:
        self._validate_id(spec.environment_id)
        self._recover_transaction(spec.environment_id)
        backend = self._backend(spec.backend)
        resolved = root.expanduser().resolve()
        python_path = backend.python_path(resolved)
        state = PythonEnvironmentState.READY if python_path.exists() else PythonEnvironmentState.MISSING
        return self._registry.put(
            ManagedPythonEnvironment(
                spec.environment_id,
                spec.scope,
                spec.backend,
                resolved,
                python_path,
                state,
                PythonEnvironmentOwnership.EXTERNAL,
                spec.description,
                self._normalize_tags(spec.tags),
                spec.specification_digest,
            )
        )

    @_serialized
    def get(self, environment_id: str) -> ManagedPythonEnvironment:
        self._recover_transaction(environment_id)
        return self._registry.get(environment_id)

    @_serialized
    def migrate_legacy(
        self,
        environment_id: str,
        *,
        python_executable: str,
        python_version: str,
    ) -> ManagedPythonEnvironment:
        self._recover_transaction(environment_id)
        return self._registry.migrate_legacy(
            environment_id,
            python_executable=python_executable,
            python_version=python_version,
        )

    @_serialized
    def list(self, *, tags: tuple[str, ...] = ()) -> tuple[ManagedPythonEnvironment, ...]:
        self._recover_all_transactions()
        required = set(self._normalize_tags(tags))
        values = self._registry.all()
        if not required:
            return values
        return tuple(value for value in values if required.issubset(value.tags))

    def backends(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    @_serialized
    def remove(self, environment_id: str) -> bool:
        self._validate_id(environment_id)
        self._recover_transaction(environment_id)
        value = self._registry_optional(environment_id)
        if value is None:
            return False
        if value.ownership is PythonEnvironmentOwnership.EXTERNAL:
            return self._registry.remove(environment_id)
        transaction = PythonEnvironmentLifecycleTransaction(
            "remove",
            "prepared",
            value.environment_id,
            value.scope,
            value.backend,
            value.root,
            value.python_path,
            value.ownership,
            value.description,
            value.tags,
            value.specification_digest,
        )
        self._transactions.put(transaction)
        self._recover_remove(transaction)
        return True

    def backend(self, backend_id: str) -> PythonEnvironmentBackend:
        return self._backend(backend_id)

    def _recover_all_transactions(self) -> None:
        for transaction in self._transactions.all():
            self._recover_transaction(transaction.environment_id)

    def _recover_transaction(self, environment_id: str) -> None:
        transaction = self._transactions.get(environment_id)
        if transaction is None:
            return
        expected_root = self._root / environment_id
        if transaction.root != expected_root:
            raise RuntimeError(
                f"Python environment lifecycle transaction root drift: {environment_id}"
            )
        if transaction.operation == "create":
            self._recover_create(transaction)
        else:
            self._recover_remove(transaction)

    def _recover_create(self, transaction: PythonEnvironmentLifecycleTransaction) -> None:
        backend = self._backend(transaction.backend)
        expected_python = backend.python_path(transaction.root)
        if transaction.python_path != expected_python:
            raise RuntimeError(
                f"Python environment lifecycle transaction interpreter drift: {transaction.environment_id}"
            )
        if transaction.phase == "prepared":
            self._remove_managed_root(transaction.root)
            self._transactions.remove(transaction.environment_id)
            return
        if not transaction.root.is_dir() or not transaction.python_path.exists():
            if transaction.phase == "committed":
                raise RuntimeError(
                    f"Committed Python environment is missing materialized state: {transaction.environment_id}"
                )
            existing = self._registry_optional(transaction.environment_id)
            if existing is not None:
                self._require_same_environment(existing, transaction.environment())
                self._registry.remove(transaction.environment_id)
            self._remove_managed_root(transaction.root)
            self._transactions.remove(transaction.environment_id)
            return
        expected = transaction.environment()
        existing = self._registry_optional(transaction.environment_id)
        if existing is None:
            self._registry.put(expected)
        else:
            self._require_same_environment(existing, expected)
        if transaction.phase != "committed":
            transaction = self._transactions.put(transaction.with_phase("committed"))
        self._transactions.remove(transaction.environment_id)

    def _recover_remove(self, transaction: PythonEnvironmentLifecycleTransaction) -> None:
        if transaction.ownership is not PythonEnvironmentOwnership.MANAGED:
            raise RuntimeError(
                f"Managed removal transaction has external ownership: {transaction.environment_id}"
            )
        existing = self._registry_optional(transaction.environment_id)
        if existing is not None:
            self._require_same_environment(existing, transaction.environment(state=existing.state))
            self._registry.remove(transaction.environment_id)
        if transaction.phase == "prepared":
            transaction = self._transactions.put(transaction.with_phase("unregistered"))
        self._remove_managed_root(transaction.root)
        if transaction.phase != "committed":
            transaction = self._transactions.put(transaction.with_phase("committed"))
        self._transactions.remove(transaction.environment_id)

    def _remove_managed_root(self, root: Path) -> None:
        if root != self._root / root.name:
            raise RuntimeError(f"Python managed environment root drift: {root}")
        if not root.exists():
            return
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError(f"Python managed environment root is not a directory: {root}")
        shutil.rmtree(root)
        fsync_directory(self._root)

    def _registry_optional(self, environment_id: str) -> ManagedPythonEnvironment | None:
        try:
            return self._registry.get(environment_id)
        except KeyError:
            return None

    @staticmethod
    def _require_same_environment(
        actual: ManagedPythonEnvironment,
        expected: ManagedPythonEnvironment,
    ) -> None:
        if actual.identity_digest != expected.identity_digest:
            raise RuntimeError(
                f"Python environment registry identity drift: {expected.environment_id}"
            )

    def _backend(self, backend_id: str) -> PythonEnvironmentBackend:
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            raise KeyError(f"unknown Python environment backend: {backend_id}") from exc

    @staticmethod
    def _normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
        values = tuple(sorted({str(tag).strip() for tag in tags if str(tag).strip()}))
        if any("/" in tag or "\\" in tag for tag in values):
            raise ValueError("Python environment tags cannot contain path separators")
        return values

    @staticmethod
    def _validate_id(value: str) -> None:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("invalid Python environment id")


__all__ = ["PythonEnvironmentLifecycle"]
