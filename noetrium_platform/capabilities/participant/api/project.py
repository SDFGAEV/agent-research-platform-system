from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from noetrium_platform.capabilities.participant.agent.api import AgentIdentity
from noetrium_platform.capabilities.participant.method.api.contracts import (
    MethodIdentity,
    MethodProgramIdentity,
    MethodProgramIdentityMismatch,
)
from noetrium_platform.capabilities.participant.core.api.contracts import (
    ParticipantImplementationIdentity,
    ParticipantRuntimeBinding,
    ParticipantSessionRuntimeIdentity,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest, require_sha256


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value


def _optional_sha256(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text or None")
    return require_sha256(value, field_name)


def _tokens(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    normalized = tuple(sorted(_text(value, field_name) for value in values))
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _method_program_identity(
    implementation: ParticipantImplementationIdentity,
    configuration_digest: str | None,
) -> MethodProgramIdentity:
    if implementation.kind != "method":
        raise MethodProgramIdentityMismatch("participant implementation kind is not method")
    return MethodProgramIdentity(
        MethodIdentity(
            implementation.participant_id,
            implementation.implementation_version,
            implementation.abi_version,
            implementation.schema_version,
            implementation.artifact_digest,
        ),
        configuration_digest,
    )


@dataclass(frozen=True, slots=True)
class ParticipantRequirement:
    role: str
    implementation: ParticipantImplementationIdentity
    configuration_digest: str | None = None
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.role, "participant requirement role")
        if not isinstance(self.implementation, ParticipantImplementationIdentity):
            raise TypeError("participant requirement implementation must be typed")
        _optional_sha256(self.configuration_digest, "participant requirement configuration_digest")
        _optional_sha256(self.implementation.artifact_digest, "participant requirement artifact_digest")
        object.__setattr__(
            self,
            "required_capabilities",
            _tokens(self.required_capabilities, "participant requirement capabilities"),
        )

    def digest(self) -> str:
        return canonical_digest(self)


def method_program_identity_for_requirement(requirement: ParticipantRequirement) -> MethodProgramIdentity:
    if not isinstance(requirement, ParticipantRequirement):
        raise TypeError("method program requirement must be ParticipantRequirement")
    return _method_program_identity(requirement.implementation, requirement.configuration_digest)


def method_program_identity_for_runtime_binding(binding: ParticipantRuntimeBinding) -> MethodProgramIdentity:
    if not isinstance(binding, ParticipantRuntimeBinding):
        raise TypeError("method program runtime binding must be ParticipantRuntimeBinding")
    return _method_program_identity(binding.implementation, binding.configuration_digest)


def require_method_program_runtime_binding(
    program_identity: MethodProgramIdentity,
    binding: ParticipantRuntimeBinding,
) -> None:
    if not isinstance(program_identity, MethodProgramIdentity):
        raise TypeError("method program identity must be MethodProgramIdentity")
    frozen = method_program_identity_for_runtime_binding(binding)
    if program_identity != frozen:
        raise MethodProgramIdentityMismatch(
            f"method program identity does not match frozen Participant binding: expected={frozen!r} actual={program_identity!r}"
        )


@dataclass(frozen=True, slots=True)
class AgentProjectDefinition:
    """Minimal public project declaration for one Agent participant."""

    role: str
    identity: AgentIdentity
    configuration_digest: str | None = None
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.role, "agent project role")
        if not isinstance(self.identity, AgentIdentity):
            raise TypeError("agent project identity must be AgentIdentity")
        _optional_sha256(self.configuration_digest, "agent project configuration_digest")
        _optional_sha256(self.identity.artifact_digest, "agent project artifact_digest")
        object.__setattr__(
            self,
            "required_capabilities",
            _tokens(self.required_capabilities, "agent project capabilities"),
        )

    def requirement(self) -> ParticipantRequirement:
        return ParticipantRequirement(
            role=self.role,
            implementation=ParticipantImplementationIdentity(
                kind="agent",
                participant_id=self.identity.agent_id,
                implementation_version=self.identity.implementation_version,
                abi_version=self.identity.abi_version,
                schema_version=self.identity.schema_version,
                artifact_digest=self.identity.artifact_digest,
            ),
            configuration_digest=self.configuration_digest,
            required_capabilities=self.required_capabilities,
        )

    def digest(self) -> str:
        return canonical_digest(self)

    def contribution(self) -> "ParticipantRequirementContribution":
        return ParticipantRequirementContribution(self.digest(), self.requirement())


@dataclass(frozen=True, slots=True)
class MethodProjectDefinition:
    """Level-0 declaration compiled onto the canonical Participant requirement."""

    role: str
    identity: MethodIdentity
    configuration_digest: str | None = None
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.role, "method project role")
        if not isinstance(self.identity, MethodIdentity):
            raise TypeError("method project identity must be MethodIdentity")
        for field_name in ("method_id", "implementation_version", "abi_version", "schema_version"):
            _text(getattr(self.identity, field_name), f"method project {field_name}")
        _optional_sha256(self.identity.artifact_digest, "method project artifact_digest")
        _optional_sha256(self.configuration_digest, "method project configuration_digest")
        object.__setattr__(
            self, "required_capabilities",
            _tokens(self.required_capabilities, "method project capabilities"),
        )

    def requirement(self) -> ParticipantRequirement:
        return ParticipantRequirement(
            role=self.role,
            implementation=ParticipantImplementationIdentity(
                kind="method", participant_id=self.identity.method_id,
                implementation_version=self.identity.implementation_version,
                abi_version=self.identity.abi_version,
                schema_version=self.identity.schema_version,
                artifact_digest=self.identity.artifact_digest,
            ),
            configuration_digest=self.configuration_digest,
            required_capabilities=self.required_capabilities,
        )

    def digest(self) -> str:
        return canonical_digest(self)

    def contribution(self) -> "ParticipantRequirementContribution":
        return ParticipantRequirementContribution(self.digest(), self.requirement())


@dataclass(frozen=True, slots=True)
class ParticipantRequirementContribution:
    author_definition_digest: str
    requirement: ParticipantRequirement

    def __post_init__(self) -> None:
        require_sha256(self.author_definition_digest, "participant contribution author_definition_digest")
        if not isinstance(self.requirement, ParticipantRequirement):
            raise TypeError("participant contribution requirement must be typed")

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class ParticipantProviderProfile:
    provider_id: str
    supported_kinds: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    schema_version: str = "project-participant-provider.v1"

    def __post_init__(self) -> None:
        _text(self.provider_id, "participant provider_id")
        if self.schema_version != "project-participant-provider.v1":
            raise ValueError("unsupported project participant provider schema")
        object.__setattr__(
            self,
            "supported_kinds",
            _tokens(self.supported_kinds, "participant provider supported_kinds"),
        )
        object.__setattr__(
            self,
            "capabilities",
            _tokens(self.capabilities, "participant provider capabilities"),
        )

    def digest(self) -> str:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class ProjectParticipantBinding:
    requirement_digest: str
    provider_id: str
    provider_profile_digest: str
    binding: ParticipantRuntimeBinding

    def __post_init__(self) -> None:
        require_sha256(self.requirement_digest, "project participant requirement_digest")
        _text(self.provider_id, "project participant provider_id")
        require_sha256(self.provider_profile_digest, "project participant provider_profile_digest")
        if not isinstance(self.binding, ParticipantRuntimeBinding):
            raise TypeError("project participant binding must carry ParticipantRuntimeBinding")

    @classmethod
    def from_runtime(
        cls,
        requirement: ParticipantRequirement,
        profile: ParticipantProviderProfile,
        binding: ParticipantRuntimeBinding,
    ) -> "ProjectParticipantBinding":
        if binding.role != requirement.role:
            raise ValueError("participant provider changed requested role")
        if binding.implementation != requirement.implementation:
            raise ValueError("participant provider changed requested implementation")
        if binding.configuration_digest != requirement.configuration_digest:
            raise ValueError("participant provider changed requested configuration")
        return cls(requirement.digest(), profile.provider_id, profile.digest(), binding)

    def digest(self) -> str:
        return canonical_digest(self)


class ParticipantBindingDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ParticipantBindingDiagnosticCode(StrEnum):
    KIND_UNSUPPORTED = "PARTICIPANT_KIND_UNSUPPORTED"
    CAPABILITY_MISSING = "PARTICIPANT_CAPABILITY_MISSING"
    RUNTIME_UNAVAILABLE = "PARTICIPANT_RUNTIME_UNAVAILABLE"
    BINDING_PROVENANCE_DRIFT = "PARTICIPANT_BINDING_PROVENANCE_DRIFT"


@dataclass(frozen=True, slots=True)
class ParticipantBindingDiagnostic:
    code: ParticipantBindingDiagnosticCode
    severity: ParticipantBindingDiagnosticSeverity
    message: str
    requirement_digest: str
    provider_id: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, ParticipantBindingDiagnosticCode):
            raise TypeError("participant diagnostic code must be typed")
        if not isinstance(self.severity, ParticipantBindingDiagnosticSeverity):
            raise TypeError("participant diagnostic severity must be typed")
        _text(self.message, "participant diagnostic message")
        require_sha256(self.requirement_digest, "participant diagnostic requirement_digest")
        _text(self.provider_id, "participant diagnostic provider_id")
        if not isinstance(self.evidence_refs, tuple) or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs
        ):
            raise TypeError("participant diagnostic evidence_refs must be non-empty strings")


class ParticipantProjectBindingError(RuntimeError):
    def __init__(self, diagnostics: tuple[ParticipantBindingDiagnostic, ...]) -> None:
        if not diagnostics:
            raise ValueError("participant binding error requires typed diagnostics")
        self.diagnostics = diagnostics
        super().__init__("; ".join(row.message for row in diagnostics))


@runtime_checkable
class ProjectParticipantProviderPort(Protocol):
    @property
    def profile(self) -> ParticipantProviderProfile: ...

    def bind(self, requirement: ParticipantRequirement) -> ProjectParticipantBinding: ...

    def diagnose(
        self, requirement: ParticipantRequirement
    ) -> tuple[ParticipantBindingDiagnostic, ...]: ...


__all__ = [
    "AgentProjectDefinition",
    "MethodProjectDefinition",
    "method_program_identity_for_requirement",
    "method_program_identity_for_runtime_binding",
    "require_method_program_runtime_binding",
    "ParticipantBindingDiagnostic",
    "ParticipantBindingDiagnosticCode",
    "ParticipantBindingDiagnosticSeverity",
    "ParticipantProjectBindingError",
    "ParticipantProviderProfile",
    "ParticipantRequirementContribution",
    "ParticipantRequirement",
    "ProjectParticipantBinding",
    "ProjectParticipantProviderPort",
]
