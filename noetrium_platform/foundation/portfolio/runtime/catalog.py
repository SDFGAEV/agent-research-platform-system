from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import RLock

from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE, ScopeIdentity, ScopeRegistryPort
from noetrium_platform.foundation.portfolio.api import (
    ProgramSpec,
    ProjectManifest,
    WorkspaceSpec,
    decode_project_manifest_bytes,
    encode_project_manifest,
)


class PortfolioConflict(RuntimeError):
    pass


class PortfolioNotFound(KeyError):
    pass


class InMemoryPortfolioCatalog:
    """Thread-safe portfolio metadata authority; Scope owns hierarchy truth."""

    def __init__(self, scopes: ScopeRegistryPort) -> None:
        self._scopes = scopes
        self._workspaces: dict[str, WorkspaceSpec] = {}
        self._programs: dict[str, ProgramSpec] = {}
        self._projects: dict[str, ProjectManifest] = {}
        self._lock = RLock()

    @staticmethod
    def _validate_put(store: dict[str, object], key: str, value: object) -> None:
        current = store.get(key)
        if current is not None and current != value:
            raise PortfolioConflict(f"identity already registered with different content: {key}")

    def register_workspace(self, spec: WorkspaceSpec) -> None:
        with self._lock:
            self._validate_put(self._workspaces, spec.workspace_id, spec)
            self._scopes.register(spec.scope, PLATFORM_SCOPE)
            self._workspaces[spec.workspace_id] = spec

    def register_program(self, spec: ProgramSpec) -> None:
        with self._lock:
            workspace = self._workspaces.get(spec.workspace_id)
            if workspace is None:
                raise PortfolioNotFound(f"workspace not registered: {spec.workspace_id}")
            self._validate_put(self._programs, spec.program_id, spec)
            self._scopes.register(spec.scope, workspace.scope)
            self._programs[spec.program_id] = spec

    def register_project(self, manifest: ProjectManifest) -> None:
        spec = manifest.project
        with self._lock:
            program = self._programs.get(spec.program_id)
            if program is None:
                raise PortfolioNotFound(f"program not registered: {spec.program_id}")
            self._validate_put(self._projects, spec.project_id, manifest)
            self._scopes.register(spec.scope, program.scope)
            self._projects[spec.project_id] = manifest

    def workspace(self, workspace_id: str) -> WorkspaceSpec:
        with self._lock:
            try:
                return self._workspaces[workspace_id]
            except KeyError as exc:
                raise PortfolioNotFound(workspace_id) from exc

    def program(self, program_id: str) -> ProgramSpec:
        with self._lock:
            try:
                return self._programs[program_id]
            except KeyError as exc:
                raise PortfolioNotFound(program_id) from exc

    def project(self, project_id: str) -> ProjectManifest:
        with self._lock:
            try:
                return self._projects[project_id]
            except KeyError as exc:
                raise PortfolioNotFound(project_id) from exc

    def projects(self, *, program_id: str | None = None) -> tuple[ProjectManifest, ...]:
        with self._lock:
            rows = tuple(self._projects.values())
        if program_id is not None:
            rows = tuple(row for row in rows if row.project.program_id == program_id)
        return tuple(sorted(rows, key=lambda row: row.project.project_id))


class SQLitePortfolioCatalog:
    """Crash-durable, process-safe authority for portfolio metadata."""

    SCHEMA_VERSION = 1

    def __init__(
        self, path: str | Path, scopes: ScopeRegistryPort, *, timeout_seconds: float = 30.0
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("portfolio SQLite timeout must be positive")
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._scopes = scopes
        self.timeout_seconds = float(timeout_seconds)
        with self._connection() as conn:
            self._ensure_schema(conn)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=self.timeout_seconds, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout={max(1, int(self.timeout_seconds * 1000))}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS portfolio_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspaces("
            "workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS programs("
            "program_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),"
            "name TEXT NOT NULL, description TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects("
            "project_id TEXT PRIMARY KEY, version TEXT NOT NULL,"
            "program_id TEXT NOT NULL REFERENCES programs(program_id), manifest BLOB NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO portfolio_meta(key,value) VALUES('schema_version',?)",
            (str(self.SCHEMA_VERSION),),
        )
        row = conn.execute("SELECT value FROM portfolio_meta WHERE key='schema_version'").fetchone()
        if row is None or int(row[0]) != self.SCHEMA_VERSION:
            raise RuntimeError("unsupported SQLitePortfolioCatalog schema")

    @staticmethod
    def _same_workspace(row: tuple[object, ...], spec: WorkspaceSpec) -> bool:
        return (str(row[0]), str(row[1])) == (spec.name, spec.description)

    @staticmethod
    def _same_program(row: tuple[object, ...], spec: ProgramSpec) -> bool:
        return (str(row[0]), str(row[1]), str(row[2])) == (
            spec.workspace_id, spec.name, spec.description
        )

    def _insert(self, statement: str, values: tuple[object, ...]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(statement, values)
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def register_workspace(self, spec: WorkspaceSpec) -> None:
        self._scopes.register(spec.scope, PLATFORM_SCOPE)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT name,description FROM workspaces WHERE workspace_id=?",
                (spec.workspace_id,),
            ).fetchone()
        if row is not None:
            if not self._same_workspace(row, spec):
                raise PortfolioConflict(
                    f"identity already registered with different content: {spec.workspace_id}"
                )
            return
        self._insert(
            "INSERT OR IGNORE INTO workspaces(workspace_id,name,description) VALUES(?,?,?)",
            (spec.workspace_id, spec.name, spec.description),
        )
        stored = self.workspace(spec.workspace_id)
        if stored != spec:
            raise PortfolioConflict(
                f"identity already registered with different content: {spec.workspace_id}"
            )

    def register_program(self, spec: ProgramSpec) -> None:
        workspace = self.workspace(spec.workspace_id)
        self._scopes.register(spec.scope, workspace.scope)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT workspace_id,name,description FROM programs WHERE program_id=?",
                (spec.program_id,),
            ).fetchone()
        if row is not None:
            if not self._same_program(row, spec):
                raise PortfolioConflict(
                    f"identity already registered with different content: {spec.program_id}"
                )
            return
        try:
            self._insert(
                "INSERT OR IGNORE INTO programs(program_id,workspace_id,name,description) VALUES(?,?,?,?)",
                (spec.program_id, spec.workspace_id, spec.name, spec.description),
            )
        except sqlite3.IntegrityError as exc:
            raise PortfolioNotFound(f"workspace not registered: {spec.workspace_id}") from exc
        stored = self.program(spec.program_id)
        if stored != spec:
            raise PortfolioConflict(
                f"identity already registered with different content: {spec.program_id}"
            )

    def register_project(self, manifest: ProjectManifest) -> None:
        spec = manifest.project
        program = self.program(spec.program_id)
        raw = encode_project_manifest(manifest)
        self._scopes.register(spec.scope, program.scope)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT manifest FROM projects WHERE project_id=?", (spec.project_id,)
            ).fetchone()
        if row is not None:
            if bytes(row[0]) != raw:
                raise PortfolioConflict(
                    f"identity already registered with different content: {spec.project_id}"
                )
            return
        try:
            self._insert(
                "INSERT OR IGNORE INTO projects(project_id,version,program_id,manifest) VALUES(?,?,?,?)",
                (spec.project_id, spec.identity.version, spec.program_id, raw),
            )
        except sqlite3.IntegrityError as exc:
            raise PortfolioNotFound(f"program not registered: {spec.program_id}") from exc
        if self.project(spec.project_id) != manifest:
            raise PortfolioConflict(
                f"identity already registered with different content: {spec.project_id}"
            )

    def workspace(self, workspace_id: str) -> WorkspaceSpec:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT workspace_id,name,description FROM workspaces WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise PortfolioNotFound(workspace_id)
        return WorkspaceSpec(str(row[0]), str(row[1]), str(row[2]))

    def program(self, program_id: str) -> ProgramSpec:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT program_id,workspace_id,name,description FROM programs WHERE program_id=?",
                (program_id,),
            ).fetchone()
        if row is None:
            raise PortfolioNotFound(program_id)
        return ProgramSpec(str(row[0]), str(row[1]), str(row[2]), str(row[3]))

    def project(self, project_id: str) -> ProjectManifest:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT manifest FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
        if row is None:
            raise PortfolioNotFound(project_id)
        return decode_project_manifest_bytes(bytes(row[0]))

    def projects(self, *, program_id: str | None = None) -> tuple[ProjectManifest, ...]:
        query = "SELECT manifest FROM projects"
        values: tuple[object, ...] = ()
        if program_id is not None:
            query += " WHERE program_id=?"
            values = (program_id,)
        with self._connection() as conn:
            rows = conn.execute(query + " ORDER BY project_id", values).fetchall()
        return tuple(decode_project_manifest_bytes(bytes(row[0])) for row in rows)


__all__ = ["InMemoryPortfolioCatalog", "PortfolioConflict", "PortfolioNotFound", "SQLitePortfolioCatalog"]
