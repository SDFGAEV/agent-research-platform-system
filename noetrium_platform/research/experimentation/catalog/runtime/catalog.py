from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from noetrium_platform.research.experimentation.experiment.api.contracts import (
    ExperimentParticipantSpec,
    ExperimentSpec,
    ParticipantImplementationIdentity,
    ParticipantSessionRuntimeIdentity,
)
from noetrium_platform.research.experimentation.run.identity.api import RunIdentity
from noetrium_platform.research.experimentation.study import StudySpec
from noetrium_platform.foundation.scope.api import ScopeIdentity, ScopeKind, ScopeRegistryPort


class ExperimentationCatalogConflict(RuntimeError):
    pass


class ExperimentationCatalogNotFound(KeyError):
    pass


class InMemoryExperimentationCatalog:
    """Study/Experiment/Run hierarchy authority backed by Scope System."""

    def __init__(self, scopes: ScopeRegistryPort) -> None:
        self._scopes = scopes
        self._studies: dict[str, StudySpec] = {}
        self._experiments: dict[str, ExperimentSpec] = {}
        self._runs: dict[str, tuple[str, RunIdentity]] = {}

    @staticmethod
    def _put(store: dict[str, object], key: str, value: object) -> None:
        current = store.get(key)
        if current is not None and current != value:
            raise ExperimentationCatalogConflict(key)
        store[key] = value

    def register_study(self, spec: StudySpec) -> None:
        parent = ScopeIdentity(ScopeKind.PROJECT, spec.project_id)
        if not self._scopes.contains(parent):
            raise ExperimentationCatalogNotFound(parent.key)
        self._put(self._studies, spec.study_id, spec)
        self._scopes.register(spec.scope, parent)

    def register_experiment(self, spec: ExperimentSpec) -> None:
        try:
            study = self._studies[spec.study_id]
        except KeyError as exc:
            raise ExperimentationCatalogNotFound(spec.study_id) from exc
        if study.project_id != spec.project_id:
            raise ExperimentationCatalogConflict("experiment project_id does not match its Study")
        if spec.experiment_id not in study.experiment_ids:
            raise ExperimentationCatalogConflict("experiment is not declared by its Study")
        self._put(self._experiments, spec.experiment_id, spec)
        self._scopes.register(spec.scope, study.scope)

    def register_run(self, experiment_id: str, identity: RunIdentity) -> None:
        try:
            experiment = self._experiments[experiment_id]
        except KeyError as exc:
            raise ExperimentationCatalogNotFound(experiment_id) from exc
        value = (experiment_id, identity)
        self._put(self._runs, identity.run_id, value)
        self._scopes.register(identity.scope, experiment.scope)

    def study(self, study_id: str) -> StudySpec:
        try:
            return self._studies[study_id]
        except KeyError as exc:
            raise ExperimentationCatalogNotFound(study_id) from exc

    def experiment(self, experiment_id: str) -> ExperimentSpec:
        try:
            return self._experiments[experiment_id]
        except KeyError as exc:
            raise ExperimentationCatalogNotFound(experiment_id) from exc

    def experiments(self, *, study_id: str | None = None) -> tuple[ExperimentSpec, ...]:
        rows = self._experiments.values()
        if study_id is not None:
            rows = (row for row in rows if row.study_id == study_id)
        return tuple(sorted(rows, key=lambda row: row.experiment_id))


class SQLiteExperimentationCatalog:
    """Crash-durable Study/Experiment/Run hierarchy authority."""

    SCHEMA_VERSION = 1

    def __init__(
        self, path: str | Path, scopes: ScopeRegistryPort, *, timeout_seconds: float = 30.0
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("experimentation SQLite timeout must be positive")
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._scopes = scopes
        self.timeout_seconds = float(timeout_seconds)
        with self._connection() as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS experimentation_meta("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS studies("
                "study_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS experiments("
                "experiment_id TEXT PRIMARY KEY, study_id TEXT NOT NULL,"
                "project_id TEXT NOT NULL, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS runs("
                "run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, payload TEXT NOT NULL);"
            )
            conn.execute(
                "INSERT OR IGNORE INTO experimentation_meta(key,value) VALUES('schema_version',?)",
                (str(self.SCHEMA_VERSION),),
            )
            row = conn.execute(
                "SELECT value FROM experimentation_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None or int(row[0]) != self.SCHEMA_VERSION:
                raise RuntimeError("unsupported SQLiteExperimentationCatalog schema")

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

    @staticmethod
    def _payload(value: object) -> str:
        return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

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

    def register_study(self, spec: StudySpec) -> None:
        parent = ScopeIdentity(ScopeKind.PROJECT, spec.project_id)
        if not self._scopes.contains(parent):
            raise ExperimentationCatalogNotFound(parent.key)
        payload = self._payload(spec)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM studies WHERE study_id=?", (spec.study_id,)
            ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ExperimentationCatalogConflict(spec.study_id)
            return
        self._scopes.register(spec.scope, parent)
        self._insert(
            "INSERT OR IGNORE INTO studies(study_id,project_id,payload) VALUES(?,?,?)",
            (spec.study_id, spec.project_id, payload),
        )
        if self.study(spec.study_id) != spec:
            raise ExperimentationCatalogConflict(spec.study_id)

    def register_experiment(self, spec: ExperimentSpec) -> None:
        study = self.study(spec.study_id)
        if study.project_id != spec.project_id:
            raise ExperimentationCatalogConflict("experiment project_id does not match its Study")
        if spec.experiment_id not in study.experiment_ids:
            raise ExperimentationCatalogConflict("experiment is not declared by its Study")
        payload = self._payload(spec)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM experiments WHERE experiment_id=?", (spec.experiment_id,)
            ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ExperimentationCatalogConflict(spec.experiment_id)
            return
        self._scopes.register(spec.scope, study.scope)
        self._insert(
            "INSERT OR IGNORE INTO experiments(experiment_id,study_id,project_id,payload) VALUES(?,?,?,?)",
            (spec.experiment_id, spec.study_id, spec.project_id, payload),
        )
        if self.experiment(spec.experiment_id) != spec:
            raise ExperimentationCatalogConflict(spec.experiment_id)

    @staticmethod
    def _decode_experiment(payload: str) -> ExperimentSpec:
        value = json.loads(payload)
        participants = tuple(
            ExperimentParticipantSpec(
                row["role"],
                ParticipantImplementationIdentity(**row["implementation"]),
                ParticipantSessionRuntimeIdentity(**row["runtime"]),
                row["configuration_digest"],
                tuple(row["depends_on_roles"]),
            )
            for row in value["participants"]
        )
        return ExperimentSpec(
            value["experiment_id"], value["study_id"], value["project_id"], participants,
            value["model_stack_digest"], value["prompt_generation"], value["workload_digest"],
            value["seed_digest"], value["repetitions"], value["trial_protocol_id"],
            value["trial_protocol_configuration_digest"],
        )

    @staticmethod
    def _decode_study(payload: str) -> StudySpec:
        value = json.loads(payload)
        return StudySpec(
            value["study_id"], value["project_id"], value["name"],
            tuple(value["experiment_ids"]), value["description"], tuple(value["tags"])
        )

    def register_run(self, experiment_id: str, identity: RunIdentity) -> None:
        experiment = self.experiment(experiment_id)
        payload = self._payload(identity)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT experiment_id,payload FROM runs WHERE run_id=?", (identity.run_id,)
            ).fetchone()
        if row is not None:
            if str(row[0]) != experiment_id or str(row[1]) != payload:
                raise ExperimentationCatalogConflict(identity.run_id)
            return
        self._scopes.register(identity.scope, experiment.scope)
        self._insert(
            "INSERT OR IGNORE INTO runs(run_id,experiment_id,payload) VALUES(?,?,?)",
            (identity.run_id, experiment_id, payload),
        )
        with self._connection() as conn:
            stored = conn.execute(
                "SELECT experiment_id,payload FROM runs WHERE run_id=?", (identity.run_id,)
            ).fetchone()
        if stored is None or str(stored[0]) != experiment_id or str(stored[1]) != payload:
            raise ExperimentationCatalogConflict(identity.run_id)

    def study(self, study_id: str) -> StudySpec:
        with self._connection() as conn:
            row = conn.execute("SELECT payload FROM studies WHERE study_id=?", (study_id,)).fetchone()
        if row is None:
            raise ExperimentationCatalogNotFound(study_id)
        return self._decode_study(str(row[0]))

    def experiment(self, experiment_id: str) -> ExperimentSpec:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM experiments WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise ExperimentationCatalogNotFound(experiment_id)
        return self._decode_experiment(str(row[0]))

    def experiments(self, *, study_id: str | None = None) -> tuple[ExperimentSpec, ...]:
        query = "SELECT payload FROM experiments"
        values: tuple[object, ...] = ()
        if study_id is not None:
            query += " WHERE study_id=?"
            values = (study_id,)
        with self._connection() as conn:
            rows = conn.execute(query + " ORDER BY experiment_id", values).fetchall()
        return tuple(self._decode_experiment(str(row[0])) for row in rows)


__all__ = [
    "ExperimentationCatalogConflict",
    "ExperimentationCatalogNotFound",
    "InMemoryExperimentationCatalog",
    "SQLiteExperimentationCatalog",
]
