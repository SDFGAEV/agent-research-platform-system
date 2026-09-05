from __future__ import annotations

from .contracts import SystemIdentity, SystemPort, SystemSpec
from noetrium_platform.foundation.kernel.kernel import ExecutionContext
from noetrium_platform.foundation.kernel.kernel.operation import EffectClass, EffectCertainty, EffectReceipt
from .conformance import (
    EnvironmentConformanceProbe,
    EnvironmentProviderConformanceReceipt,
    verify_environment_provider_conformance,
)
from .provider import (
    EnvironmentCapability,
    EnvironmentDiagnosticsPort,
    EnvironmentProviderCapabilities,
    EnvironmentProviderPort,
    EnvironmentSessionDiagnostics,
    EnvironmentSessionServices,
)
from .interaction import (
    EnvironmentActionLifecycle,
    EnvironmentActionPhase,
    EnvironmentCapabilityDescriptor,
    EnvironmentCoordinationPort,
    EnvironmentCoordinationReceipt,
    EnvironmentCoordinationRequest,
    EnvironmentQuery,
    EnvironmentQueryKind,
    EnvironmentQueryPort,
    EnvironmentQueryResult,
    EnvironmentRawEventReceipt,
    EnvironmentRawEventRecord,
    EnvironmentRawRecordSinkPort,
)

_LAZY_RUNTIME_EXPORTS = frozenset({
    "ActionIdentityViolation",
    "ActionNotApplied",
    "ActionRecoveryRequired",
    "ActionReconciliationDisposition",
    "ActionReconciliationResult",
    "ActionRequest",
    "ActionResult",
    "ActionSafetyCapabilityMissing",
    "ActionScientificCommitContradiction",
    "EnvironmentCapabilityUnsupported",
    "ActionSemanticIdentity",
    "DurablePreparedActionSession",
    "EnvironmentIdentity",
    "EnvironmentImplementation",
    "EnvironmentSession",
    "Observation",
    "action_request_digest",
    "require_action_recovery_handle_identity",
    "require_action_result_identity",
    "require_effect_receipt_digest",
    "require_reconciliation_identity",
    "require_recovery_handle_reconciliation_identity",
    "JsonScalar",
    "JsonInput",
    "JsonMutableValue",
    "JsonValue",
    "StateMachineDynamicsIdentity",
    "StateMachineDynamicsPort",
    "StateMachineEnvironmentSpec",
    "StateTransition",
    "freeze_json_mapping",
    "thaw_json",
    "thaw_json_mapping",
})


def __getattr__(name: str):
    if name not in _LAZY_RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import noetrium_platform.capabilities.environment.runtime.api as runtime_api

    value = getattr(runtime_api, name)
    globals()[name] = value
    return value


__all__ = [
    "SystemIdentity", "SystemSpec", "SystemPort", "ExecutionContext",
    "EffectClass", "EffectCertainty", "EffectReceipt",
    "ActionIdentityViolation", "ActionNotApplied", "ActionRecoveryRequired",
    "ActionReconciliationDisposition", "ActionReconciliationResult",
    "ActionRequest", "ActionResult", "ActionSafetyCapabilityMissing",
    "ActionScientificCommitContradiction", "ActionSemanticIdentity",
    "EnvironmentCapabilityUnsupported", "EnvironmentCapability",
    "EnvironmentConformanceProbe", "EnvironmentProviderConformanceReceipt",
    "verify_environment_provider_conformance",
    "EnvironmentDiagnosticsPort", "EnvironmentProviderCapabilities",
    "EnvironmentProviderPort", "EnvironmentSessionDiagnostics",
    "EnvironmentSessionServices",
    "EnvironmentActionLifecycle", "EnvironmentActionPhase",
    "EnvironmentCapabilityDescriptor",
    "EnvironmentCoordinationPort", "EnvironmentCoordinationReceipt",
    "EnvironmentCoordinationRequest",
    "EnvironmentQuery", "EnvironmentQueryKind", "EnvironmentQueryPort",
    "EnvironmentQueryResult",
    "EnvironmentRawEventReceipt", "EnvironmentRawEventRecord",
    "EnvironmentRawRecordSinkPort",
    "DurablePreparedActionSession", "EnvironmentIdentity",
    "EnvironmentImplementation", "EnvironmentSession", "Observation",
    "action_request_digest", "require_action_recovery_handle_identity",
    "require_action_result_identity", "require_effect_receipt_digest",
    "require_reconciliation_identity", "require_recovery_handle_reconciliation_identity",
    "JsonScalar", "JsonInput", "JsonMutableValue", "JsonValue",
    "StateMachineDynamicsIdentity", "StateMachineDynamicsPort",
    "StateMachineEnvironmentSpec", "StateTransition",
    "freeze_json_mapping", "thaw_json", "thaw_json_mapping",
]
