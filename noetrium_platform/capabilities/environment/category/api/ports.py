from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import (
    EnvironmentCategoryDescriptor,
    EnvironmentCategoryId,
    EnvironmentImplementationDescriptor,
)


@runtime_checkable
class EnvironmentCategoryCatalogPort(Protocol):
    def categories(self) -> tuple[EnvironmentCategoryDescriptor, ...]: ...

    def category(self, category_id: EnvironmentCategoryId) -> EnvironmentCategoryDescriptor: ...

    def implementations(
        self, category_id: EnvironmentCategoryId
    ) -> tuple[EnvironmentImplementationDescriptor, ...]: ...

    def implementation(self, implementation_id: str) -> EnvironmentImplementationDescriptor: ...

    @property
    def catalog_digest(self) -> str: ...


__all__ = ["EnvironmentCategoryCatalogPort"]
