"""Deprecated implementation-path shim for service ABI ports.

The public contract lives in :mod:`service.api.ports`; this module remains
only so internal migrations cannot create duplicate ABI definitions.
"""

from .ports import (
    ExactServiceRuntimePort,
    ServiceReadyObservation,
    ServiceReconcileObservation,
    ServiceStartOutcome,
    ServiceStopOutcome,
)

__all__ = [
    "ExactServiceRuntimePort",
    "ServiceReadyObservation",
    "ServiceReconcileObservation",
    "ServiceStartOutcome",
    "ServiceStopOutcome",
]
