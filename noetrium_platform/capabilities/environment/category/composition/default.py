from __future__ import annotations

from dataclasses import dataclass

from ..api.contracts import (
    EnvironmentCategoryDescriptor,
    EnvironmentCategoryId,
    EnvironmentImplementationDescriptor,
)
from ..runtime.catalog import (
    canonical_environment_categories,
    canonical_environment_implementations,
)


@dataclass(frozen=True, slots=True)
class DefaultEnvironmentCategoryCatalog:
    _categories: tuple[EnvironmentCategoryDescriptor, ...]
    _implementations: tuple[EnvironmentImplementationDescriptor, ...]

    def categories(self):
        return self._categories

    def category(self, category_id: EnvironmentCategoryId):
        for item in self._categories:
            if item.category_id == category_id:
                return item
        raise KeyError(category_id.value)

    def implementations(self, category_id: EnvironmentCategoryId):
        return tuple(item for item in self._implementations if item.category_id == category_id)


def default_environment_category_catalog():
    return DefaultEnvironmentCategoryCatalog(
        canonical_environment_categories(),
        canonical_environment_implementations(),
    )


__all__ = ["DefaultEnvironmentCategoryCatalog", "default_environment_category_catalog"]
