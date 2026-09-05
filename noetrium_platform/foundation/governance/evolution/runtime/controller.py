from __future__ import annotations

from collections import defaultdict, deque
from statistics import median

from noetrium_platform.foundation.governance.system_registry.api import (
    SystemDescriptor,
    SystemRegistryPort,
)
from ..api.contracts import (
    DiscoveryReport,
    DriftKind,
    EvolutionAssessment,
    EvolutionProposal,
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
        self._minimum_samples = minimum_samples
        self._failure_ratio = failure_ratio
        self._latency_ratio = latency_ratio
        self._observations: deque[TopologyObservation] = deque(maxlen=observation_capacity)
        self._drifts: list[TopologyDrift] = []

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
                self._systems.contains(parent) or parent in incoming
            ):
                rejected.append(f"{key}:unknown-parent:{parent}")
                continue
            self._systems.register(descriptor)
            registered.append(key)

        return DiscoveryReport(
            source_id=source_id,
            source_digest=source_digest,
            registered=tuple(registered),
            already_registered=tuple(already),
            rejected=tuple(rejected),
            topology_generation=self._systems.generation,
            topology_digest=self._systems.topology_digest,
        )

    def observe(self, observation: TopologyObservation) -> None:
        """Record an observation and turn topology inconsistency into evidence."""

        try:
            self._systems.validate(observation.system)
        except (KeyError, RuntimeError):
            self._append_drift(
                TopologyDrift(
                    kind=DriftKind.UNKNOWN_NODE,
                    system=observation.system,
                    expected_generation=self._systems.generation,
                    expected_digest=self._systems.topology_digest,
                    observed_generation=observation.topology_generation,
                    observed_digest=observation.topology_digest,
                    reason="runtime observation references an unregistered node",
                )
            )
        else:
            if observation.topology_generation != self._systems.generation:
                self._append_drift(
                    TopologyDrift(
                        kind=DriftKind.STALE_GENERATION,
                        system=observation.system,
                        expected_generation=self._systems.generation,
                        expected_digest=self._systems.topology_digest,
                        observed_generation=observation.topology_generation,
                        observed_digest=observation.topology_digest,
                        reason="observation was emitted against an older topology generation",
                    )
                )
            elif observation.topology_digest != self._systems.topology_digest:
                self._append_drift(
                    TopologyDrift(
                        kind=DriftKind.DIGEST_MISMATCH,
                        system=observation.system,
                        expected_generation=self._systems.generation,
                        expected_digest=self._systems.topology_digest,
                        observed_generation=observation.topology_generation,
                        observed_digest=observation.topology_digest,
                        reason="observation topology digest differs from the registry",
                    )
                )
        self._observations.append(observation)

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
        return EvolutionProposal(
            proposal_id=proposal_id,
            signal=signal,
            predecessor_topology_digest=self._systems.topology_digest,
            change_contract_id=change_contract_id,
            implementation_digest=implementation_digest,
            configuration_digest=configuration_digest,
            validation_plan_digest=validation_plan_digest,
            rollback_anchor_digest=rollback_anchor_digest,
        )

    def _append_drift(self, drift: TopologyDrift) -> None:
        if drift.digest() not in {item.digest() for item in self._drifts}:
            self._drifts.append(drift)


__all__ = ["RegistryDrivenEvolutionController"]
