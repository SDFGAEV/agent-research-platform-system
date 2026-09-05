"""Environment-neutral workload API boundary.

Executors are runtime implementations and must be selected explicitly from
workload.runtime by composition code.
"""

from .api import (
    WorkloadActionAdapterPort,
    WorkloadBatchResult,
    WorkloadBatchBindingPort,
    WorkloadBoundaryPort,
    WorkloadCompletionPort,
    WorkloadCompletionReceipt,
    WorkloadEnvironmentPort,
    WorkloadEvidencePort,
    WorkloadFailurePolicyPort,
    WorkloadDiagnosticsPort,
    WorkloadExecutionCutObserverPort,
    WorkloadPlannerPort,
    WorkloadStatePort,
    WorkloadTaskResult,
    WorkloadTaskRunnerPort,
    WorkloadTaskRunError,
    WorkloadDecision,
)

__all__ = [
    "WorkloadBatchResult",
    "WorkloadActionAdapterPort",
    "WorkloadBatchBindingPort",
    "WorkloadBoundaryPort",
    "WorkloadCompletionPort",
    "WorkloadCompletionReceipt",
    "WorkloadDecision",
    "WorkloadDiagnosticsPort",
    "WorkloadExecutionCutObserverPort",
    "WorkloadEnvironmentPort",
    "WorkloadEvidencePort",
    "WorkloadFailurePolicyPort",
    "WorkloadPlannerPort",
    "WorkloadStatePort",
    "WorkloadTaskResult",
    "WorkloadTaskRunnerPort",
    "WorkloadTaskRunError",
]
