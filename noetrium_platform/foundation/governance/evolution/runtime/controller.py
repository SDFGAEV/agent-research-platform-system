from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from statistics import median
from time import monotonic
from uuid import uuid4

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemIdentity,
    SystemRegistryPort,
)
from ..api import EvolutionStateStorePort
from ..api.contracts import (
    DiscoveryReport,
    DriftKind,
    EvolutionAssessment,
    EvolutionProposal,
    EvolutionStage,
    EvolutionTransition,
    ImprovementSignal,
    ObservationOutcome,
    SignalKind,
    TopologyDrift,
    TopologyObservation,
    _stable_id,
)


class RegistryDrivenEvolutionController:
    """Automatic topology observation and proposal generation.

    This controller owns transient assessment state and immutable proposals. The
    system registry remains the sole owner of topology; domain revisions and
    runtime effects remain owned by their respective authorities.
    """

    def __init__(
        self,
        systems: SystemRegistryPort,
        *,
        store: EvolutionStateStorePort | None = None,
        minimum_samples: int = 3,
        failure_ratio: float = 0.5,
        latency_ratio: float = 1.5,
        observation_capacity: int = 10_000,
    ) -> None:
        if minimum_samples <= 0:
            raise ValueError("minimum_samples must be positive")
        if not 0 < failure_ratio <= 1:
            raise ValueError("failure_ratio must be in (0, 1]")
        if latency_ratio <= 1:
            raise ValueError("latency_ratio must be greater than one")
        if observation_capacity <= 0:
            raise ValueError("observation_capacity must be positive")
        self._systems = systems
        self._store = store
        self._minimum_samples = minimum_samples
        self._failure_ratio = failure_ratio
        self._latency_ratio = latency_ratio
        self._observations: deque[TopologyObservation] = deque(maxlen=observation_capacity)
        if store is not None:
            self._observations.extend(store.observations())
        self._drifts: list[TopologyDrift] = []
        for observation in tuple(self._observations):
            drift = self._drift_for_observation(observation)
            if drift is not None:
                self._append_drift(drift)
        self._proposals: dict[str, EvolutionProposal] = {}
        self._stages: dict[str, EvolutionStage] = {}
        if store is not None:
            for proposal in store.proposals():
                self._proposals[proposal.proposal_id] = proposal
                self._stages[proposal.proposal_id] = proposal.stage
            for transition in store.transitions():
                proposal = self._proposals.get(transition.proposal_id)
                if proposal is None or proposal.digest() != transition.proposal_digest:
                    raise ValueError("evolution transition references an unknown proposal")
                current = self._stages[transition.proposal_id]
                if current is not transition.from_stage:
                    raise ValueError("evolution transition sequence is not contiguous")
                self._stages[transition.proposal_id] = transition.to_stage

    @property
    def systems(self) -> SystemRegistryPort:
        return self._systems

    def discover(
        self,
        source_id: str,
        descriptors: tuple[SystemDescriptor, ...],
        *,
        source_digest: str,
    ) -> DiscoveryReport:
        """Validate and automatically enroll typed descriptors.

        Discovery is additive and idempotent. A conflicting identity or an
        unknown parent is reported as rejected; it is never silently guessed.
        """

        if not isinstance(descriptors, tuple):
            raise TypeError("descriptors must be a tuple")
        if not source_id.strip():
            raise ValueError("source_id must be non-empty")
        registered: list[str] = []
        already: list[str] = []
        rejected: list[str] = []
        incoming: dict[str, SystemDescriptor] = {}
        to_register: list[SystemDescriptor] = []
        pending_keys: set[str] = set()

        for descriptor in sorted(descriptors, key=lambda item: item.identity.depth):
            key = descriptor.identity.key
            if key in incoming:
                rejected.append(f"{key}:duplicate-discovery")
                continue
            incoming[key] = descriptor
            if self._systems.contains(key):
                if self._systems.get(key) == descriptor:
                    already.append(key)
                else:
                    rejected.append(f"{key}:identity-conflict")
                continue
            parent = descriptor.parent_key
            if parent is not None and not (
                self._systems.contains(parent) or parent in pending_keys
            ):
                rejected.append(f"{key}:unknown-parent:{parent}")
                continue
            to_register.append(descriptor)
            pending_keys.add(key)

        if to_register:
            registered.extend(
                descriptor.identity.key
                for descriptor in self._systems.register_many(tuple(to_register))
            )

        report = DiscoveryReport(
            source_id=source_id,
            source_digest=source_digest,
            registered=tuple(registered),
            already_registered=tuple(already),
            rejected=tuple(rejected),
            topology_generation=self._systems.generation,
            topology_digest=self._systems.topology_digest,
        )
        if self._store is not None:
            self._store.append_discovery(report)
        return report

    def _drift_for_observation(
        self,
        observation: TopologyObservation,
    ) -> TopologyDrift | None:
        try:
            self._systems.validate(observation.system)
        except (KeyError, RuntimeError):
            return TopologyDrift(
                kind=DriftKind.UNKNOWN_NODE,
                system=observation.system,
                expected_generation=self._systems.generation,
                expected_digest=self._systems.topology_digest,
                observed_generation=observation.topology_generation,
                observed_digest=observation.topology_digest,
                reason="runtime observation references an unregistered node",
            )
        if observation.topology_generation != self._systems.generation:
            return TopologyDrift(
                kind=DriftKind.STALE_GENERATION,
                system=observation.system,
                expected_generation=self._systems.generation,
                expected_digest=self._systems.topology_digest,
                observed_generation=observation.topology_generation,
                observed_digest=observation.topology_digest,
                reason="observation was emitted against an older topology generation",
            )
        if observation.topology_digest != self._systems.topology_digest:
            return TopologyDrift(
                kind=DriftKind.DIGEST_MISMATCH,
                system=observation.system,
                expected_generation=self._systems.generation,
                expected_digest=self._systems.topology_digest,
                observed_generation=observation.topology_generation,
                observed_digest=observation.topology_digest,
                reason="observation topology digest differs from the registry",
            )
        return None

    def observe(self, observation: TopologyObservation) -> None:
        """Record an observation and turn topology inconsistency into evidence."""

        if self._store is not None:
            self._store.append_observation(observation)
        drift = self._drift_for_observation(observation)
        if drift is not None:
            self._append_drift(drift)
        self._observations.append(observation)

    @contextmanager
    def operation(
        self,
        system: SystemIdentity,
        operation_id: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ):
        """Capture one operation while preserving the caller's failure semantics."""

        started = monotonic()
        topology_generation = self._systems.generation
        topology_digest = self._systems.topology_digest
        try:
            yield
        except TimeoutError as exc:
            self._emit_operation_observation(
                system,
                operation_id,
                topology_generation,
                topology_digest,
                started,
                ObservationOutcome.TIMEOUT,
                evidence_refs,
                exc,
            )
            raise
        except BaseException as exc:
            outcome = (
                ObservationOutcome.CANCELLED
                if isinstance(exc, (KeyboardInterrupt, SystemExit))
                or exc.__class__.__name__ == "CancelledError"
                else ObservationOutcome.FAILURE
            )
            self._emit_operation_observation(
                system,
                operation_id,
                topology_generation,
                topology_digest,
                started,
                outcome,
                evidence_refs,
                exc,
            )
            raise
        else:
            self._emit_operation_observation(
                system,
                operation_id,
                topology_generation,
                topology_digest,
                started,
                ObservationOutcome.SUCCESS,
                evidence_refs,
                None,
            )

    def _emit_operation_observation(
        self,
        system: SystemIdentity,
        operation_id: str,
        topology_generation: int,
        topology_digest: str,
        started: float,
        outcome: ObservationOutcome,
        evidence_refs: tuple[str, ...],
        operation_error: BaseException | None,
    ) -> None:
        observation = TopologyObservation(
            observation_id=f"runtime:{uuid4().hex}",
            system=system,
            topology_generation=topology_generation,
            topology_digest=topology_digest,
            operation_id=operation_id,
            duration_seconds=max(0.0, monotonic() - started),
            outcome=outcome,
            evidence_refs=evidence_refs,
        )
        try:
            self.observe(observation)
        except BaseException as observation_error:
            if operation_error is not None:
                raise BaseExceptionGroup(
                    "operation and observation recording failed",
                    (operation_error, observation_error),
                ) from observation_error
            raise

    def assess(self) -> EvolutionAssessment:
        """Produce deterministic signals from the current observation window."""

        grouped: dict[str, list[TopologyObservation]] = defaultdict(list)
        for observation in self._observations:
            grouped[observation.system.key].append(observation)

        signals: list[ImprovementSignal] = []
        for key, observations in sorted(grouped.items()):
            target = observations[-1].system
            sample_size = len(observations)
            evidence = tuple(item.digest() for item in observations[-10:])
            failures = sum(
                item.outcome is not ObservationOutcome.SUCCESS for item in observations
            )
            if sample_size >= self._minimum_samples and failures / sample_size >= self._failure_ratio:
                signals.append(
                    ImprovementSignal(
                        signal_id=_stable_id(
                            SignalKind.FAILURE_CLUSTER.value,
                            key,
                            self._systems.topology_digest,
                        ),
                        target=target,
                        kind=SignalKind.FAILURE_CLUSTER,
                        topology_generation=self._systems.generation,
                        topology_digest=self._systems.topology_digest,
                        severity=5 if failures == sample_size else 4,
                        sample_size=sample_size,
                        evidence_refs=evidence,
                        description=(
                            f"{key} has {failures}/{sample_size} non-success "
                            "observations in the current window"
                        ),
                    )
                )

            durations = [item.duration_seconds for item in observations]
            if sample_size >= self._minimum_samples and durations:
                baseline = median(durations)
                peak = max(durations)
                if baseline > 0 and peak >= baseline * self._latency_ratio:
                    signals.append(
                        ImprovementSignal(
                            signal_id=_stable_id(
                                SignalKind.LATENCY_ANOMALY.value,
                                key,
                                self._systems.topology_digest,
                            ),
                            target=target,
                            kind=SignalKind.LATENCY_ANOMALY,
                            topology_generation=self._systems.generation,
                            topology_digest=self._systems.topology_digest,
                            severity=3,
                            sample_size=sample_size,
                            evidence_refs=evidence,
                            description=(
                                f"{key} peak latency {peak:.6f}s exceeds "
                                f"median {baseline:.6f}s by the configured ratio"
                            ),
                        )
                    )

        for drift in self._drifts:
            signals.append(
                ImprovementSignal(
                    signal_id=_stable_id(
                        SignalKind.TOPOLOGY_DRIFT.value,
                        drift.digest(),
                    ),
                    target=drift.system,
                    kind=SignalKind.TOPOLOGY_DRIFT,
                    topology_generation=self._systems.generation,
                    topology_digest=self._systems.topology_digest,
                    severity=5 if drift.kind is DriftKind.UNKNOWN_NODE else 4,
                    sample_size=1,
                    evidence_refs=(drift.digest(),),
                    description=drift.reason,
                )
            )

        observed = tuple(sorted(grouped))
        registered = tuple(item.identity.key for item in self._systems.list())
        unobserved = tuple(key for key in registered if key not in observed)
        return EvolutionAssessment(
            topology_generation=self._systems.generation,
            topology_digest=self._systems.topology_digest,
            signals=tuple(signals),
            drifts=tuple(self._drifts),
            observed_systems=observed,
            unobserved_systems=unobserved,
        )

    def propose(
        self,
        signal: ImprovementSignal,
        *,
        change_contract_id: str,
        implementation_digest: str,
        configuration_digest: str,
        validation_plan_digest: str,
        rollback_anchor_digest: str,
    ) -> EvolutionProposal:
        if signal.topology_generation != self._systems.generation:
            raise ValueError("cannot propose against a stale topology generation")
        if signal.topology_digest != self._systems.topology_digest:
            raise ValueError("cannot propose against a stale topology digest")
        proposal_id = _stable_id(
            signal.digest(),
            change_contract_id,
            implementation_digest,
            configuration_digest,
        )
        proposal = EvolutionProposal(
            proposal_id=proposal_id,
            signal=signal,
            predecessor_topology_digest=self._systems.topology_digest,
            change_contract_id=change_contract_id,
            implementation_digest=implementation_digest,
            configuration_digest=configuration_digest,
            validation_plan_digest=validation_plan_digest,
            rollback_anchor_digest=rollback_anchor_digest,
        )
        if self._store is not None:
            self._store.put_proposal(proposal)
        self._proposals[proposal.proposal_id] = proposal
        self._stages[proposal.proposal_id] = proposal.stage
        return proposal

    def current_stage(self, proposal_id: str) -> EvolutionStage:
        try:
            return self._stages[proposal_id]
        except KeyError as exc:
            raise KeyError(f"unknown evolution proposal: {proposal_id}") from exc

    def advance(
        self,
        proposal_id: str,
        to_stage: EvolutionStage,
        *,
        evidence_refs: tuple[str, ...],
        reason_digest: str,
        decision_contract_id: str,
        decision_implementation_digest: str,
        decision_configuration_digest: str,
    ) -> EvolutionTransition:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"unknown evolution proposal: {proposal_id}")
        current = self.current_stage(proposal_id)
        allowed = {
            EvolutionStage.PROPOSED: frozenset({
                EvolutionStage.VALIDATED,
                EvolutionStage.QUARANTINED,
            }),
            EvolutionStage.VALIDATED: frozenset({
                EvolutionStage.PROMOTED,
                EvolutionStage.QUARANTINED,
            }),
            EvolutionStage.PROMOTED: frozenset({EvolutionStage.ROLLED_BACK}),
            EvolutionStage.QUARANTINED: frozenset(),
            EvolutionStage.ROLLED_BACK: frozenset(),
        }
        if to_stage not in allowed[current]:
            raise ValueError(
                f"illegal evolution transition: {current.value} -> {to_stage.value}"
            )
        if (
            to_stage is EvolutionStage.PROMOTED
            and proposal.predecessor_topology_digest != self._systems.topology_digest
        ):
            raise ValueError("cannot promote against a changed topology")
        transition = EvolutionTransition(
            transition_id=_stable_id(
                proposal.digest(),
                current.value,
                to_stage.value,
                reason_digest,
            ),
            proposal_id=proposal.proposal_id,
            proposal_digest=proposal.digest(),
            from_stage=current,
            to_stage=to_stage,
            evidence_refs=evidence_refs,
            reason_digest=reason_digest,
            decision_contract_id=decision_contract_id,
            decision_implementation_digest=decision_implementation_digest,
            decision_configuration_digest=decision_configuration_digest,
            transition_generation=self._systems.generation,
        )
        if self._store is not None:
            self._store.append_transition(transition)
        self._stages[proposal_id] = to_stage
        return transition

    def _append_drift(self, drift: TopologyDrift) -> None:
        if drift.digest() not in {item.digest() for item in self._drifts}:
            self._drifts.append(drift)


__all__ = ["RegistryDrivenEvolutionController"]
