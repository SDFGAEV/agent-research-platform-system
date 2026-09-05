"""Deprecated implementation-path shim for method ABI ports.

The public contract lives in :mod:`method.api.ports`; this module remains
only so internal migrations cannot create duplicate ABI definitions.
"""

from .ports import (
    MethodCompositionPorts,
    MethodEndpointFactoryPort,
    MethodEndpointPort,
    MethodImplementation,
    MethodRuntimeBinding,
    MethodRuntimeIdentity,
    MethodSessionRuntime,
)

__all__ = [
    "MethodCompositionPorts",
    "MethodEndpointFactoryPort",
    "MethodEndpointPort",
    "MethodImplementation",
    "MethodRuntimeBinding",
    "MethodRuntimeIdentity",
    "MethodSessionRuntime",
]
