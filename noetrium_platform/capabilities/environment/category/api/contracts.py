from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from noetrium_platform.foundation.kernel.kernel import (
    JsonObject,
    JsonValue,
    canonical_digest,
    freeze_json,
    thaw_json,
)


class EnvironmentCategoryId(StrEnum):
    MINECRAFT = "minecraft"
    EMBODIED = "embodied"
    GUI = "gui"
    WEB = "web"
    SOFTWARE = "software"
    TEXT_WORLD = "text_world"


class EnvironmentCategoryStatus(StrEnum):
    AVAILABLE = "available"
    CONTRACT_ONLY = "contract_only"


def _text(name: str, value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
@dataclass(frozen=True, slots=True)
class EnvironmentCategoryDescriptor:
    category_id: EnvironmentCategoryId
    version: str
    package: str
    description: str
    modalities: tuple[str, ...] = ()
    interaction_surfaces: tuple[str, ...] = ()
    world_properties: tuple[str, ...] = ()
    implementation_ids: tuple[str, ...] = ()
    planned_implementation_ids: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.category_id, EnvironmentCategoryId):
            raise TypeError("category_id must use EnvironmentCategoryId")
        for name, value in (
            ("version", self.version),
            ("package", self.package),
            ("description", self.description),
        ):
            _text(name, value)
        for name, values in (
            ("modalities", self.modalities),
            ("interaction_surfaces", self.interaction_surfaces),
            ("world_properties", self.world_properties),
            ("implementation_ids", self.implementation_ids),
            ("planned_implementation_ids", self.planned_implementation_ids),
        ):
            if any(type(value) is not str or not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        overlap = set(self.implementation_ids) & set(self.planned_implementation_ids)
        if overlap:
            raise ValueError("implemented and planned ids must be disjoint")
        object.__setattr__(self, "metadata", freeze_json(self.metadata))
    def record(self) -> JsonObject:
        return {
            "category_id": self.category_id.value,
            "version": self.version,
            "package": self.package,
            "description": self.description,
            "modalities": list(self.modalities),
            "interaction_surfaces": list(self.interaction_surfaces),
            "world_properties": list(self.world_properties),
            "implementation_ids": list(self.implementation_ids),
            "planned_implementation_ids": list(self.planned_implementation_ids),
            "metadata": thaw_json(self.metadata),
        }

    @property
    def category_digest(self) -> str:
        return canonical_digest(self.record())


@dataclass(frozen=True, slots=True)
class EnvironmentImplementationDescriptor:
    implementation_id: str
    category_id: EnvironmentCategoryId
    version: str
    provider_package: str
    backend_kind: str
    status: EnvironmentCategoryStatus = EnvironmentCategoryStatus.AVAILABLE
    capabilities: tuple[str, ...] = ()
    resource_profile: Mapping[str, JsonValue] = field(default_factory=dict)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("implementation_id", self.implementation_id),
            ("version", self.version),
            ("provider_package", self.provider_package),
            ("backend_kind", self.backend_kind),
        ):
            _text(name, value)
        if not isinstance(self.category_id, EnvironmentCategoryId):
            raise TypeError("category_id must use EnvironmentCategoryId")
        if not isinstance(self.status, EnvironmentCategoryStatus):
            raise TypeError("status must use EnvironmentCategoryStatus")
        if any(type(value) is not str or not value.strip() for value in self.capabilities):
            raise ValueError("implementation capabilities must be non-empty strings")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("implementation capabilities must be unique")
        object.__setattr__(self, "resource_profile", freeze_json(self.resource_profile))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))

    def record(self) -> JsonObject:
        return {
            "implementation_id": self.implementation_id,
            "category_id": self.category_id.value,
            "version": self.version,
            "provider_package": self.provider_package,
            "backend_kind": self.backend_kind,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
            "resource_profile": thaw_json(self.resource_profile),
            "metadata": thaw_json(self.metadata),
        }

    @property
    def implementation_digest(self) -> str:
        return canonical_digest(self.record())


__all__ = [
    "EnvironmentCategoryDescriptor",
    "EnvironmentCategoryId",
    "EnvironmentCategoryStatus",
    "EnvironmentImplementationDescriptor",
]
