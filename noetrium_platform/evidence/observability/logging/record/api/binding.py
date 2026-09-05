from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.system import SystemObservationFactory

from noetrium_platform.foundation.governance.architecture.api.capability_composition import (
    BindingPlan,
    CapabilityOffer,
)

from .ports import LoggingSystemPort


@dataclass(frozen=True, slots=True)
class LoggingSystemBinding:
    """Public logging port paired with immutable composition provenance."""

    logging: LoggingSystemPort
    plan: BindingPlan
    offer: CapabilityOffer
    observations: "SystemObservationFactory | None" = None


__all__ = ["LoggingSystemBinding"]
