from __future__ import annotations

from collections.abc import Callable
import hashlib
from pathlib import Path
import sqlite3

from noetrium_platform.foundation.kernel.kernel import (
    CanonicalDecodingError,
    CanonicalEncodingError,
    canonical_digest,
    strict_finite_json_bytes,
    strict_json_loads,
)
from noetrium_platform.foundation.governance.evolution.api import (
    DiscoveryReport,
    EvolutionProposal,
    EvolutionStage,
    EvolutionStateStorePort,
    EvolutionTransition,
    ImprovementSignal,
    ObservationOutcome,
    SignalKind,
    TopologyObservation,
)
from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity


class EvolutionStoreConflict(RuntimeError):
    """The same immutable key was written with different content."""


class EvolutionStoreIntegrityError(RuntimeError):
    """A durable evolution record is corrupt or semantically invalid."""


_SCHEMA = "evolution-store.sqlite.v2"


def _default_connection(path: str | Path, *, timeout_seconds: float) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=timeout_seconds)


def _payload(value: object) -> tuple[bytes, str]:
    try:
        raw = strict_finite_json_bytes(value)
    except (CanonicalEncodingError, UnicodeEncodeError) as exc:
        raise EvolutionStoreIntegrityError("evolution record is not strict JSON") from exc
    return raw, hashlib.sha256(raw).hexdigest()


def _object(raw: bytes, *, field: str) -> dict[str, object]:
    try:
        value = strict_json_loads(raw)
    except CanonicalDecodingError as exc:
        raise EvolutionStoreIntegrityError(f"{field} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise EvolutionStoreIntegrityError(f"{field} must decode to an object")
    return value


def _expect(value: dict[str, object], fields: frozenset[str], *, field: str) -> None:
    if frozenset(value) != fields:
        raise EvolutionStoreIntegrityError(f"{field} fields are invalid")


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EvolutionStoreIntegrityError(f"{field} must be canonical text")
    return value


def _integer(value: object, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise EvolutionStoreIntegrityError(f"{field} must be a positive integer")
    return value


def _digest(value: object, *, field: str) -> str:
    result = _text(value, field=field)
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise EvolutionStoreIntegrityError(f"{field} must be a lowercase SHA-256 digest")
    return result


def _identity(value: SystemIdentity) -> dict[str, object]:
    return {"system_id": value.system_id, "subsystem_path": list(value.subsystem_path)}


def _decode_identity(value: object, *, field: str) -> SystemIdentity:
    if not isinstance(value, dict):
        raise EvolutionStoreIntegrityError(f"{field} must be an object")
    _expect(value, frozenset({"system_id", "subsystem_path"}), field=field)
    path = value["subsystem_path"]
    if not isinstance(path, list) or any(not isinstance(item, str) for item in path):
        raise EvolutionStoreIntegrityError(f"{field}.subsystem_path is invalid")
    try:
        return SystemIdentity(
            _text(value["system_id"], field=f"{field}.system_id"),
            tuple(path),
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreIntegrityError(f"{field} is invalid") from exc


def _encode_observation(value: TopologyObservation) -> dict[str, object]:
    return {
        "observation_id": value.observation_id,
        "system": _identity(value.system),
        "topology_generation": value.topology_generation,
        "topology_digest": value.topology_digest,
        "operation_id": value.operation_id,
        "duration_seconds": value.duration_seconds,
        "outcome": value.outcome.value,
        "evidence_refs": list(value.evidence_refs),
    }


def _decode_observation(raw: bytes) -> TopologyObservation:
    value = _object(raw, field="topology observation")
    _expect(value, frozenset({
        "observation_id", "system", "topology_generation", "topology_digest",
        "operation_id", "duration_seconds", "outcome", "evidence_refs",
    }), field="topology observation")
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
        raise EvolutionStoreIntegrityError("topology observation evidence_refs are invalid")
    try:
        return TopologyObservation(
            observation_id=_text(value["observation_id"], field="observation_id"),
            system=_decode_identity(value["system"], field="system"),
            topology_generation=_integer(value["topology_generation"], field="topology_generation"),
            topology_digest=_digest(value["topology_digest"], field="topology_digest"),
            operation_id=_text(value["operation_id"], field="operation_id"),
            duration_seconds=float(value["duration_seconds"]),
            outcome=ObservationOutcome(_text(value["outcome"], field="outcome")),
            evidence_refs=tuple(refs),
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreIntegrityError("topology observation is invalid") from exc


def _encode_discovery(value: DiscoveryReport) -> dict[str, object]:
    return {
        "source_id": value.source_id,
        "source_digest": value.source_digest,
        "registered": list(value.registered),
        "already_registered": list(value.already_registered),
        "rejected": list(value.rejected),
        "topology_generation": value.topology_generation,
        "topology_digest": value.topology_digest,
    }


def _decode_discovery(raw: bytes) -> DiscoveryReport:
    value = _object(raw, field="discovery report")
    _expect(value, frozenset({
        "source_id", "source_digest", "registered", "already_registered",
        "rejected", "topology_generation", "topology_digest",
    }), field="discovery report")
    fields = {}
    for name in ("registered", "already_registered", "rejected"):
        items = value[name]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise EvolutionStoreIntegrityError(f"discovery report {name} is invalid")
        fields[name] = tuple(items)
    try:
        return DiscoveryReport(
            source_id=_text(value["source_id"], field="source_id"),
            source_digest=_digest(value["source_digest"], field="source_digest"),
            registered=fields["registered"],
            already_registered=fields["already_registered"],
            rejected=fields["rejected"],
            topology_generation=_integer(value["topology_generation"], field="topology_generation"),
            topology_digest=_digest(value["topology_digest"], field="topology_digest"),
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreIntegrityError("discovery report is invalid") from exc


def _encode_signal(value: ImprovementSignal) -> dict[str, object]:
    return {
        "signal_id": value.signal_id,
        "target": _identity(value.target),
        "kind": value.kind.value,
        "topology_generation": value.topology_generation,
        "topology_digest": value.topology_digest,
        "severity": value.severity,
        "sample_size": value.sample_size,
        "evidence_refs": list(value.evidence_refs),
        "description": value.description,
    }


def _decode_signal(value: object) -> ImprovementSignal:
    if not isinstance(value, dict):
        raise EvolutionStoreIntegrityError("proposal signal must be an object")
    _expect(value, frozenset({
        "signal_id", "target", "kind", "topology_generation", "topology_digest",
        "severity", "sample_size", "evidence_refs", "description",
    }), field="proposal signal")
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
        raise EvolutionStoreIntegrityError("proposal signal evidence_refs are invalid")
    try:
        return ImprovementSignal(
            signal_id=_text(value["signal_id"], field="signal_id"),
            target=_decode_identity(value["target"], field="target"),
            kind=SignalKind(_text(value["kind"], field="kind")),
            topology_generation=_integer(value["topology_generation"], field="topology_generation"),
            topology_digest=_digest(value["topology_digest"], field="topology_digest"),
            severity=int(value["severity"]),
            sample_size=_integer(value["sample_size"], field="sample_size"),
            evidence_refs=tuple(refs),
            description=_text(value["description"], field="description"),
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreIntegrityError("proposal signal is invalid") from exc


def _encode_proposal(value: EvolutionProposal) -> dict[str, object]:
    return {
        "proposal_id": value.proposal_id,
        "signal": _encode_signal(value.signal),
        "predecessor_topology_digest": value.predecessor_topology_digest,
        "change_contract_id": value.change_contract_id,
        "implementation_digest": value.implementation_digest,
        "configuration_digest": value.configuration_digest,
        "validation_plan_digest": value.validation_plan_digest,
        "rollback_anchor_digest": value.rollback_anchor_digest,
        "stage": value.stage.value,
    }


def _decode_proposal(raw: bytes) -> EvolutionProposal:
    value = _object(raw, field="evolution proposal")
    _expect(value, frozenset({
        "proposal_id", "signal", "predecessor_topology_digest", "change_contract_id",
        "implementation_digest", "configuration_digest", "validation_plan_digest",
        "rollback_anchor_digest", "stage",
    }), field="evolution proposal")
    try:
        return EvolutionProposal(
            proposal_id=_text(value["proposal_id"], field="proposal_id"),
            signal=_decode_signal(value["signal"]),
            predecessor_topology_digest=_digest(
                value["predecessor_topology_digest"], field="predecessor_topology_digest"
            ),
            change_contract_id=_text(value["change_contract_id"], field="change_contract_id"),
            implementation_digest=_digest(value["implementation_digest"], field="implementation_digest"),
            configuration_digest=_digest(value["configuration_digest"], field="configuration_digest"),
            validation_plan_digest=_digest(value["validation_plan_digest"], field="validation_plan_digest"),
            rollback_anchor_digest=_digest(value["rollback_anchor_digest"], field="rollback_anchor_digest"),
            stage=EvolutionStage(_text(value["stage"], field="stage")),
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreIntegrityError("evolution proposal is invalid") from exc


def _encode_transition(value: EvolutionTransition) -> dict[str, object]:
    return {
        "transition_id": value.transition_id,
        "proposal_id": value.proposal_id,
        "proposal_digest": value.proposal_digest,
        "from_stage": value.from_stage.value,
        "to_stage": value.to_stage.value,
        "evidence_refs": list(value.evidence_refs),
        "reason_digest": value.reason_digest,
        "decision_contract_id": value.decision_contract_id,
        "decision_implementation_digest": value.decision_implementation_digest,
        "decision_configuration_digest": value.decision_configuration_digest,
        "transition_generation": value.transition_generation,
    }


def _decode_transition(raw: bytes) -> EvolutionTransition:
    value = _object(raw, field="evolution transition")
    _expect(value, frozenset({
        "transition_id", "proposal_id", "proposal_digest", "from_stage", "to_stage",
        "evidence_refs", "reason_digest", "decision_contract_id",
        "decision_implementation_digest", "decision_configuration_digest",
        "transition_generation",
    }), field="evolution transition")
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
        raise EvolutionStoreIntegrityError("evolution transition evidence_refs are invalid")
    try:
        return EvolutionTransition(
            transition_id=_text(value["transition_id"], field="transition_id"),
            proposal_id=_text(value["proposal_id"], field="proposal_id"),
            proposal_digest=_digest(value["proposal_digest"], field="proposal_digest"),
            from_stage=EvolutionStage(_text(value["from_stage"], field="from_stage")),
            to_stage=EvolutionStage(_text(value["to_stage"], field="to_stage")),
            evidence_refs=tuple(refs),
            reason_digest=_digest(value["reason_digest"], field="reason_digest"),
            decision_contract_id=_text(
                value["decision_contract_id"], field="decision_contract_id"
            ),
            decision_implementation_digest=_digest(
                value["decision_implementation_digest"],
                field="decision_implementation_digest",
            ),
            decision_configuration_digest=_digest(
                value["decision_configuration_digest"],
                field="decision_configuration_digest",
            ),
            transition_generation=_integer(
                value["transition_generation"], field="transition_generation"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise EvolutionStoreIntegrityError("evolution transition is invalid") from exc


class SQLiteEvolutionStore(EvolutionStateStorePort):
    """Crash-safe append/idempotent store for evolution evidence and proposals."""

    SCHEMA_VERSION = _SCHEMA

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 30.0,
        connection_factory: Callable[..., sqlite3.Connection] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = float(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._connection_factory = connection_factory or _default_connection
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS evolution_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS evolution_observations ("
                    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "observation_id TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, "
                    "payload_digest TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS evolution_discoveries ("
                    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "record_key TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, "
                    "payload_digest TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS evolution_proposals ("
                    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "proposal_id TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, "
                    "payload_digest TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS evolution_transitions ("
                    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "transition_id TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, "
                    "payload_digest TEXT NOT NULL)"
                )
                current = conn.execute(
                    "SELECT value FROM evolution_meta WHERE key='schema_version'"
                ).fetchone()
                if current is None:
                    conn.execute(
                        "INSERT INTO evolution_meta(key,value) VALUES('schema_version',?)",
                        (self.SCHEMA_VERSION,),
                    )
                elif current[0] != self.SCHEMA_VERSION:
                    raise EvolutionStoreIntegrityError("unsupported evolution store schema")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _connection(self):
        return self._connection_factory(
            self.path,
            timeout_seconds=self.timeout_seconds,
        )

    def _put(self, table: str, key_column: str, key: str, value: object) -> None:
        raw, digest = _payload(value)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT payload_digest FROM {table} WHERE {key_column}=?",
                (key,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    conn.rollback()
                    raise EvolutionStoreConflict(key)
                conn.commit()
                return
            conn.execute(
                f"INSERT INTO {table}({key_column},payload,payload_digest) VALUES(?,?,?)",
                (key, raw, digest),
            )
            conn.commit()

    def append_observation(self, observation: TopologyObservation) -> None:
        self._put(
            "evolution_observations",
            "observation_id",
            observation.observation_id,
            _encode_observation(observation),
        )

    def append_discovery(self, report: DiscoveryReport) -> None:
        self._put(
            "evolution_discoveries",
            "record_key",
            f"{report.source_id}:{report.source_digest}",
            _encode_discovery(report),
        )

    def put_proposal(self, proposal: EvolutionProposal) -> None:
        self._put(
            "evolution_proposals",
            "proposal_id",
            proposal.proposal_id,
            _encode_proposal(proposal),
        )

    def append_transition(self, transition: EvolutionTransition) -> None:
        self._put(
            "evolution_transitions",
            "transition_id",
            transition.transition_id,
            _encode_transition(transition),
        )

    def observations(self) -> tuple[TopologyObservation, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM evolution_observations ORDER BY sequence"
            ).fetchall()
        return tuple(_decode_observation(bytes(row[0])) for row in rows)

    def proposals(self) -> tuple[EvolutionProposal, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM evolution_proposals ORDER BY sequence"
            ).fetchall()
        return tuple(_decode_proposal(bytes(row[0])) for row in rows)

    def transitions(self) -> tuple[EvolutionTransition, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM evolution_transitions ORDER BY sequence"
            ).fetchall()
        return tuple(_decode_transition(bytes(row[0])) for row in rows)


__all__ = [
    "EvolutionStoreConflict",
    "EvolutionStoreIntegrityError",
    "SQLiteEvolutionStore",
]
