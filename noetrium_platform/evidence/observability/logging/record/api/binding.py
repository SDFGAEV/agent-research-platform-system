from __future__ import annotations

from dataclasses import dataclass
from noetrium_platform.foundation.governance.architecture.api.capability_composition import (
    BindingPlan,
    CapabilityOffer,
)

from .ports import LoggingSystemPort, ObservationFactoryPort


@dataclass(frozen=True, slots=True)
class LoggingSystemBinding:
    """Public logging port paired with immutable composition provenance."""

    logging: LoggingSystemPort
    plan: BindingPlan
    offer: CapabilityOffer
    observations: ObservationFactoryPort | None = None


__all__ = ["LoggingSystemBinding"]
