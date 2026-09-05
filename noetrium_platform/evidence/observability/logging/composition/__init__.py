"""Explicit composition of the logging system from its typed leaf seams."""

from __future__ import annotations

from dataclasses import dataclass

from noetrium_platform.foundation.governance.system_registry.api import SystemIdentity, SystemRegistryPort
from noetrium_platform.evidence.observability.logging.query.api import LogQueryPort
from noetrium_platform.evidence.observability.logging.record.api import (
    ExceptionDescriptorPort,
    LoggingSystemBinding,
    LoggingSystemPort,
)
from noetrium_platform.evidence.observability.logging.record.providers.exception_descriptor import (
    KernelExceptionDescriptor,
)
from noetrium_platform.evidence.observability.logging.record.runtime import (
    StructuredLoggingSystem,
    SystemObservationFactory,
)
from noetrium_platform.evidence.observability.logging.sink.api import LogSinkPort
from noetrium_platform.evidence.observability.logging.composition.raw_sink import RegistryBoundRawLogSink
from noetrium_platform.evidence.observability.capture.runtime import RegistryBoundRawObservationGateway
from noetrium_platform.evidence.observability.api import ContextMetricSink
from noetrium_platform.foundation.governance.architecture.api.capabilities import (
    EXCEPTION_DESCRIPTOR_V1,
    LOG_QUERY_V1,
    LOG_SINK_V1,
    LOGGING_SYSTEM_V1,
)
from noetrium_platform.foundation.governance.architecture.api.capability_composition import (
    BindingPlan,
    CapabilityOffer,
    CapabilityRequirement,
    CompositionContract,
    CompositionIdentity,
    CompositionSubject,
    RequirementAddress,
    interface_contract_digest,
)
from noetrium_platform.foundation.governance.architecture.runtime.capability_composition import (
    CapabilityCompositionPlanner,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest
from noetrium_platform.foundation.scope.api import PLATFORM_SCOPE, ScopeIdentity


_LOGGING_SYSTEM = SystemIdentity("observability", ("logging",))
_LOGGING_RECORD_SYSTEM = SystemIdentity("observability", ("logging", "record"))
_LOGGING_STORAGE_SYSTEM = SystemIdentity("observability", ("logging", "storage"))
_LOGGING_SUBJECT = CompositionSubject.system_subject(_LOGGING_SYSTEM)
_LOGGING_RECORD_SUBJECT = CompositionSubject.system_subject(_LOGGING_RECORD_SYSTEM)
_LOGGING_STORAGE_SUBJECT = CompositionSubject.system_subject(_LOGGING_STORAGE_SYSTEM)


@dataclass(frozen=True, slots=True)
class LogSinkBinding:
    """One concrete sink selected by a composition root, with evidence."""

    sink: LogSinkPort
    provider_identity: str
    configuration_digest: str


@dataclass(frozen=True, slots=True)
class LogQueryBinding:
    """One concrete query adapter selected by a composition root, with evidence."""

    query: LogQueryPort
    provider_identity: str
    configuration_digest: str


@dataclass(frozen=True, slots=True)
class ExceptionDescriptorBinding:
    """Optional record-policy provider selected by a composition root."""

    descriptor: ExceptionDescriptorPort
    provider_identity: str
    configuration_digest: str


def compose_logging_system(
    *,
    sink: LogSinkBinding,
    query: LogQueryBinding,
    planner: CapabilityCompositionPlanner,
    systems: SystemRegistryPort,
    scope: ScopeIdentity = PLATFORM_SCOPE,
    exception_descriptor: ExceptionDescriptorBinding | None = None,
    parent_plan_digest: str | None = None,
    metrics: ContextMetricSink | None = None,
    raw_gateway: RegistryBoundRawObservationGateway | None = None,
) -> LoggingSystemBinding:
    """Compose logging without a container or a hidden default runtime dependency.

    The storage and exception providers are selected here, recorded as offers,
    then injected directly into the structured logging implementation.
    """

    descriptor_binding = exception_descriptor or ExceptionDescriptorBinding(
        descriptor=KernelExceptionDescriptor(),
        provider_identity="platform.kernel.safe-exception-descriptor.v1",
        configuration_digest=canonical_digest({"policy": "platform.kernel.safe-exception.v1"}),
    )
    sink_offer = CapabilityOffer(
        offer_id="observability.logging.sink-provider",
        owner=_LOGGING_STORAGE_SUBJECT,
        scope=scope,
        capability=LOG_SINK_V1,
        interface_digest=interface_contract_digest(LogSinkPort),
        provider_identity=sink.provider_identity,
        configuration_digest=sink.configuration_digest,
    )
    query_offer = CapabilityOffer(
        offer_id="observability.logging.query-provider",
        owner=_LOGGING_STORAGE_SUBJECT,
        scope=scope,
        capability=LOG_QUERY_V1,
        interface_digest=interface_contract_digest(LogQueryPort),
        provider_identity=query.provider_identity,
        configuration_digest=query.configuration_digest,
    )
    descriptor_offer = CapabilityOffer(
        offer_id="observability.logging.exception-descriptor-provider",
        owner=_LOGGING_RECORD_SUBJECT,
        scope=scope,
        capability=EXCEPTION_DESCRIPTOR_V1,
        interface_digest=interface_contract_digest(ExceptionDescriptorPort),
        provider_identity=descriptor_binding.provider_identity,
        configuration_digest=descriptor_binding.configuration_digest,
    )
    sink_requirement = CapabilityRequirement(
        RequirementAddress(_LOGGING_SUBJECT, "sink"),
        scope,
        LOG_SINK_V1,
        interface_contract_digest(LogSinkPort),
    )
    query_requirement = CapabilityRequirement(
        RequirementAddress(_LOGGING_SUBJECT, "query"),
        scope,
        LOG_QUERY_V1,
        interface_contract_digest(LogQueryPort),
    )
    descriptor_requirement = CapabilityRequirement(
        RequirementAddress(_LOGGING_SUBJECT, "exception-descriptor"),
        scope,
        EXCEPTION_DESCRIPTOR_V1,
        interface_contract_digest(ExceptionDescriptorPort),
    )
    logging_offer = CapabilityOffer(
        offer_id="observability.logging.structured-logging-system",
        owner=_LOGGING_SUBJECT,
        scope=scope,
        capability=LOGGING_SYSTEM_V1,
        interface_digest=interface_contract_digest(LoggingSystemPort),
        provider_identity="observability.logging.structured-system.v1",
        configuration_digest=canonical_digest(
            {
                "sink_offer": sink_offer.offer_id,
                "query_offer": query_offer.offer_id,
                "descriptor_offer": descriptor_offer.offer_id,
            }
        ),
    )
    plan = planner.freeze(
        CompositionIdentity(
            "observability.logging",
            scope,
            owner=_LOGGING_SUBJECT,
            parent_plan_digest=parent_plan_digest,
        ),
        (
            CompositionContract(
                _LOGGING_SUBJECT,
                scope,
                offers=(logging_offer,),
                requirements=(sink_requirement, query_requirement, descriptor_requirement),
            ),
            CompositionContract(
                _LOGGING_RECORD_SUBJECT,
                scope,
                offers=(descriptor_offer,),
            ),
            CompositionContract(
                _LOGGING_STORAGE_SUBJECT,
                scope,
                offers=(sink_offer, query_offer),
            ),
        ),
    )
    logging_sink = sink.sink if raw_gateway is None else RegistryBoundRawLogSink(sink.sink, raw_gateway)
    logging_system = StructuredLoggingSystem(
        logging_sink,
        query.query,
        systems=systems,
        exception_descriptor=descriptor_binding.descriptor,
    )
    observations = None
    if metrics is not None:
        observations = SystemObservationFactory(systems, logging_system, metrics)
        observations.bind_all(scope=scope)
    return LoggingSystemBinding(
        logging=logging_system,
        plan=plan,
        offer=logging_offer,
        observations=observations,
    )


__all__ = [
    "ExceptionDescriptorBinding",
    "LogQueryBinding",
    "LogSinkBinding",
    "compose_logging_system",
    "RegistryBoundRawLogSink",
]
