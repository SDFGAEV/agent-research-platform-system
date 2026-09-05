from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import TypeVar

from noetrium_platform.foundation.kernel.kernel import canonical_digest
from noetrium_platform.capabilities.environment.catalog.api import (
    EnvironmentAssignment,
    EnvironmentBinding,
    EnvironmentInstance,
    EnvironmentOverlay,
    EnvironmentSpec,
    EnvironmentTemplate,
    ExecutionEnvironmentKind,
    ResolvedEnvironmentSpec,
)
from noetrium_platform.infrastructure.resources.resolution import (
    HierarchicalResourceResolver,
    ResolutionPolicy,
    ScopedValue,
)
from noetrium_platform.foundation.scope.api import ScopeIdentity, ScopeKind, ScopeRegistryPort


_T = TypeVar("_T")


class EnvironmentCatalogConflict(RuntimeError):
    pass


class EnvironmentCatalogNotFound(KeyError):
    pass


class ExecutionEnvironmentCatalog:
    """Hierarchy-aware logical environment authority.

    Logical specifications inherit through Scope System.  Physical Python/Conda/etc.
    environments remain separate instances and can be reused by many scoped bindings.
    """

    def __init__(self, scopes: ScopeRegistryPort) -> None:
        self._scopes = scopes
        self._templates: dict[str, EnvironmentTemplate] = {}
        self._specs: dict[str, EnvironmentSpec] = {}
        self._overlays: dict[str, EnvironmentOverlay] = {}
        self._assignments = HierarchicalResourceResolver[str](ancestry=scopes.ancestry)
        self._instances: dict[str, EnvironmentInstance] = {}
        self._bindings = HierarchicalResourceResolver[EnvironmentBinding](ancestry=scopes.ancestry)

    @staticmethod
    def _put(store: dict[str, _T], key: str, value: _T) -> None:
        current = store.get(key)
        if current is not None and current != value:
            raise EnvironmentCatalogConflict(key)
        store[key] = value

    def register_template(self, template: EnvironmentTemplate) -> None:
        self._put(self._templates, template.template_id, template)

    def register_spec(self, spec: EnvironmentSpec) -> None:
        if spec.parent_spec_id is not None and spec.parent_spec_id not in self._specs:
            raise EnvironmentCatalogNotFound(spec.parent_spec_id)
        if spec.template_id is not None and spec.template_id not in self._templates:
            raise EnvironmentCatalogNotFound(spec.template_id)
        self._put(self._specs, spec.spec_id, spec)

    def register_overlay(self, overlay: EnvironmentOverlay) -> None:
        if overlay.target_spec_id not in self._specs:
            raise EnvironmentCatalogNotFound(overlay.target_spec_id)
        self._put(self._overlays, overlay.overlay_id, overlay)

    def assign(self, assignment: EnvironmentAssignment) -> None:
        if assignment.spec_id not in self._specs:
            raise EnvironmentCatalogNotFound(assignment.spec_id)
        self._assignments.bind(ScopedValue("execution-environment", assignment.name, assignment.scope, assignment.spec_id, assignment.policy))

    def _spec_chain(self, spec: EnvironmentSpec) -> tuple[EnvironmentSpec, ...]:
        chain = [spec]
        seen = {spec.spec_id}
        current = spec
        while current.parent_spec_id is not None:
            try:
                current = self._specs[current.parent_spec_id]
            except KeyError as exc:
                raise EnvironmentCatalogNotFound(current.parent_spec_id) from exc
            if current.spec_id in seen:
                raise EnvironmentCatalogConflict(f"environment spec cycle: {current.spec_id}")
            seen.add(current.spec_id)
            chain.append(current)
        chain.reverse()
        return tuple(chain)

    def resolve(self, name: str, scope: ScopeIdentity) -> ResolvedEnvironmentSpec:
        assigned = self._assignments.resolve(namespace="execution-environment", name=name, scope=scope)
        try:
            leaf = self._specs[assigned.value]
        except KeyError as exc:
            raise EnvironmentCatalogNotFound(assigned.value) from exc
        chain = self._spec_chain(leaf)
        requirements: dict[str, str] = {}
        environment: dict[str, str] = {}
        source_scopes: list[ScopeIdentity] = []
        for spec in chain:
            requirements.update(spec.requirements)
            environment.update(spec.environment)
            source_scopes.append(spec.scope)
        ancestry_path = self._scopes.ancestry(scope)
        ancestry_rank = {identity: index for index, identity in enumerate(ancestry_path)}
        chain_ids = {item.spec_id for item in chain}
        overlays = tuple(sorted(
            (
                row for row in self._overlays.values()
                if row.target_spec_id in chain_ids and row.scope in ancestry_rank
            ),
            key=lambda row: ancestry_rank[row.scope],
            reverse=True,
        ))
        for overlay in overlays:
            requirements.update(overlay.requirements)
            environment.update(overlay.environment)
            source_scopes.append(overlay.scope)
        return ResolvedEnvironmentSpec(
            spec_id=leaf.spec_id,
            kind=leaf.kind,
            requested_scope=scope,
            source_scopes=tuple(source_scopes),
            source_spec_ids=tuple(item.spec_id for item in chain),
            applied_overlay_ids=tuple(item.overlay_id for item in overlays),
            requirements=tuple(sorted(requirements.items())),
            environment=tuple(sorted(environment.items())),
        )

    def register_instance(self, instance: EnvironmentInstance) -> None:
        self._put(self._instances, instance.instance_id, instance)

    def bind(self, binding: EnvironmentBinding) -> None:
        if binding.instance_id not in self._instances:
            raise EnvironmentCatalogNotFound(binding.instance_id)
        self._bindings.bind(ScopedValue("execution-environment-instance", binding.role, binding.scope, binding))

    def binding(self, role: str, scope: ScopeIdentity) -> EnvironmentBinding:
        return self._bindings.resolve(namespace="execution-environment-instance", name=role, scope=scope).value

    @staticmethod
    def resolved_digest(value: ResolvedEnvironmentSpec) -> str:
        return canonical_digest(value)


__all__ = ["EnvironmentCatalogConflict", "EnvironmentCatalogNotFound", "ExecutionEnvironmentCatalog"]

class SQLiteExecutionEnvironmentCatalog(ExecutionEnvironmentCatalog):
    """Restart-safe environment hierarchy and binding authority."""

    SCHEMA_VERSION = 1

    def __init__(
        self, path: str | Path, scopes: ScopeRegistryPort, *,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(scopes)
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = float(timeout_seconds)
        self._assignment_rows: dict[tuple[str, str], EnvironmentAssignment] = {}
        self._binding_rows: dict[tuple[str, str], EnvironmentBinding] = {}
        self._state_generation = 0
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS environment_state("
                "state_id INTEGER PRIMARY KEY CHECK(state_id=1),"
                "schema_version INTEGER NOT NULL, generation INTEGER NOT NULL,"
                "payload TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO environment_state("
                "state_id,schema_version,generation,payload)"
                " VALUES(1,?,?,?)",
                (self.SCHEMA_VERSION, 0, "{}"),
            )
        self._load()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=self.timeout_seconds, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout={max(1, int(self.timeout_seconds * 1000))}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _scope(value: ScopeIdentity) -> dict[str, str]:
        return {"kind": value.kind.value, "scope_id": value.scope_id}

    @staticmethod
    def _decode_scope(value: dict[str, str]) -> ScopeIdentity:
        return ScopeIdentity(ScopeKind(value["kind"]), value["scope_id"])

    @classmethod
    def _pairs(cls, value: tuple[tuple[str, str], ...]) -> list[list[str]]:
        return [list(row) for row in value]

    @classmethod
    def _template(cls, value: EnvironmentTemplate) -> dict[str, object]:
        return {
            "template_id": value.template_id, "kind": value.kind.value,
            "scope": cls._scope(value.scope), "base_spec_id": value.base_spec_id,
            "description": value.description,
        }

    @classmethod
    def _spec(cls, value: EnvironmentSpec) -> dict[str, object]:
        return {
            "spec_id": value.spec_id, "kind": value.kind.value,
            "scope": cls._scope(value.scope), "parent_spec_id": value.parent_spec_id,
            "template_id": value.template_id, "requirements": cls._pairs(value.requirements),
            "environment": cls._pairs(value.environment), "tags": list(value.tags),
        }

    @classmethod
    def _overlay(cls, value: EnvironmentOverlay) -> dict[str, object]:
        return {
            "overlay_id": value.overlay_id, "target_spec_id": value.target_spec_id,
            "scope": cls._scope(value.scope), "requirements": cls._pairs(value.requirements),
            "environment": cls._pairs(value.environment),
        }

    @classmethod
    def _assignment(cls, value: EnvironmentAssignment) -> dict[str, object]:
        return {
            "name": value.name, "spec_id": value.spec_id,
            "scope": cls._scope(value.scope), "policy": value.policy.value,
        }

    @classmethod
    def _instance(cls, value: EnvironmentInstance) -> dict[str, object]:
        return {
            "instance_id": value.instance_id,
            "resolved_spec_digest": value.resolved_spec_digest,
            "backend": value.backend, "runtime_reference": value.runtime_reference,
            "scope": cls._scope(value.scope),
        }

    @classmethod
    def _binding(cls, value: EnvironmentBinding) -> dict[str, object]:
        return {
            "binding_id": value.binding_id, "scope": cls._scope(value.scope),
            "role": value.role, "instance_id": value.instance_id,
        }

    def _state(self) -> str:
        return json.dumps({
            "templates": [self._template(row) for row in self._templates.values()],
            "specs": [self._spec(row) for row in self._specs.values()],
            "overlays": [self._overlay(row) for row in self._overlays.values()],
            "assignments": [self._assignment(row) for row in self._assignment_rows.values()],
            "instances": [self._instance(row) for row in self._instances.values()],
            "bindings": [self._binding(row) for row in self._binding_rows.values()],
        }, sort_keys=True, separators=(",", ":"))

    def _persist(self) -> None:
        payload = self._state()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            next_generation = self._state_generation + 1
            updated = conn.execute(
                "UPDATE environment_state SET schema_version=?, generation=?, payload=? "
                "WHERE state_id=1 AND generation=?",
                (self.SCHEMA_VERSION, next_generation, payload, self._state_generation),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise RuntimeError("stale environment catalog revision; reload and retry")
            conn.commit()
            self._state_generation = next_generation

    @staticmethod
    def _pairs_decode(value: list[list[str]]) -> tuple[tuple[str, str], ...]:
        return tuple((str(row[0]), str(row[1])) for row in value)

    def _load(self) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT schema_version,generation,payload FROM environment_state WHERE state_id=1"
            ).fetchone()
        if row is None or int(row[0]) != self.SCHEMA_VERSION:
            raise RuntimeError("unsupported SQLiteExecutionEnvironmentCatalog schema")
        self._state_generation = int(row[1])
        value = json.loads(str(row[2]))
        self._templates = {
            row["template_id"]: EnvironmentTemplate(
                row["template_id"], ExecutionEnvironmentKind(row["kind"]),
                self._decode_scope(row["scope"]),
                row["base_spec_id"], row["description"],
            )
            for row in value.get("templates", [])
        }

        self._specs = {
            row["spec_id"]: EnvironmentSpec(
                row["spec_id"], ExecutionEnvironmentKind(row["kind"]),
                self._decode_scope(row["scope"]),
                row["parent_spec_id"], row["template_id"],
                self._pairs_decode(row["requirements"]),
                self._pairs_decode(row["environment"]), tuple(row["tags"]),
            )
            for row in value.get("specs", [])
        }
        self._overlays = {
            row["overlay_id"]: EnvironmentOverlay(
                row["overlay_id"], row["target_spec_id"], self._decode_scope(row["scope"]),
                self._pairs_decode(row["requirements"]),
                self._pairs_decode(row["environment"]),
            )
            for row in value.get("overlays", [])
        }
        self._instances = {
            row["instance_id"]: EnvironmentInstance(
                row["instance_id"], row["resolved_spec_digest"], row["backend"],
                row["runtime_reference"], self._decode_scope(row["scope"]),
            )
            for row in value.get("instances", [])
        }
        self._assignment_rows = {
            (row["name"], row["scope"]["kind"] + ":" + row["scope"]["scope_id"]):
            EnvironmentAssignment(
                row["name"], row["spec_id"], self._decode_scope(row["scope"]),
                ResolutionPolicy(row["policy"]),
            )
            for row in value.get("assignments", [])
        }
        self._binding_rows = {
            (row["role"], row["scope"]["kind"] + ":" + row["scope"]["scope_id"]):
            EnvironmentBinding(
                row["binding_id"], self._decode_scope(row["scope"]),
                row["role"], row["instance_id"],
            )
            for row in value.get("bindings", [])
        }
        self._assignments = HierarchicalResourceResolver(ancestry=self._scopes.ancestry)
        for row in self._assignment_rows.values():
            self._assignments.bind(
                ScopedValue("execution-environment", row.name, row.scope, row.spec_id, row.policy)
            )
        self._bindings = HierarchicalResourceResolver(ancestry=self._scopes.ancestry)
        for row in self._binding_rows.values():
            self._bindings.bind(
                ScopedValue("execution-environment-instance", row.role, row.scope, row)
            )

    def register_template(self, template: EnvironmentTemplate) -> None:
        self._load()
        super().register_template(template)
        self._persist()

    def register_spec(self, spec: EnvironmentSpec) -> None:
        self._load()
        super().register_spec(spec)
        self._persist()

    def register_overlay(self, overlay: EnvironmentOverlay) -> None:
        self._load()
        super().register_overlay(overlay)
        self._persist()

    def assign(self, assignment: EnvironmentAssignment) -> None:
        self._load()
        super().assign(assignment)
        self._assignment_rows[(assignment.name, assignment.scope.key)] = assignment
        self._persist()

    def resolve(self, name: str, scope: ScopeIdentity) -> ResolvedEnvironmentSpec:
        self._load()
        return super().resolve(name, scope)

    def register_instance(self, instance: EnvironmentInstance) -> None:
        self._load()
        super().register_instance(instance)
        self._persist()

    def bind(self, binding: EnvironmentBinding) -> None:
        self._load()
        super().bind(binding)
        self._binding_rows[(binding.role, binding.scope.key)] = binding
        self._persist()

    def binding(self, role: str, scope: ScopeIdentity) -> EnvironmentBinding:
        self._load()
        return super().binding(role, scope)


__all__ = [
    "EnvironmentCatalogConflict",
    "EnvironmentCatalogNotFound",
    "ExecutionEnvironmentCatalog",
    "SQLiteExecutionEnvironmentCatalog",
]
