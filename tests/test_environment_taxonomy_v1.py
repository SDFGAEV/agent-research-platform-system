from __future__ import annotations

from noetrium_platform.capabilities.environment.category.api import (
    EnvironmentCategoryId,
    EnvironmentCategoryStatus,
)
from noetrium_platform.capabilities.environment.category.composition import (
    default_environment_category_catalog,
)
from noetrium_platform.capabilities.environment.category.runtime.catalog import (
    canonical_environment_categories,
    canonical_environment_implementations,
)
from noetrium_platform.capabilities.environment.gui.api import GuiActionKind, GuiEnvironmentSpec
from noetrium_platform.capabilities.environment.web.api import WebActionKind, WebEnvironmentSpec
from noetrium_platform.capabilities.environment.software.api import SoftwareActionKind, SoftwareEnvironmentSpec
from noetrium_platform.capabilities.environment.text_world.api import (
    TextWorldActionKind,
    TextWorldEnvironmentSpec,
)


def test_taxonomy_contains_exactly_six_environment_families() -> None:
    categories = canonical_environment_categories()
    assert {item.category_id for item in categories} == {
        EnvironmentCategoryId.MINECRAFT,
        EnvironmentCategoryId.EMBODIED,
        EnvironmentCategoryId.GUI,
        EnvironmentCategoryId.WEB,
        EnvironmentCategoryId.SOFTWARE,
        EnvironmentCategoryId.TEXT_WORLD,
    }
    assert len(categories) == 6


def test_non_environment_concepts_are_not_categories() -> None:
    values = {item.value for item in EnvironmentCategoryId}
    assert values.isdisjoint({
        "benchmark", "replay", "synthetic", "tool_world",
        "multi_agent", "reinforcement_learning", "python",
    })


def test_catalog_does_not_claim_planned_backends_as_implemented() -> None:
    categories = {item.category_id: item for item in canonical_environment_categories()}
    implementations = canonical_environment_implementations()
    implemented_ids = {item.implementation_id for item in implementations}
    for category in categories.values():
        assert implemented_ids.isdisjoint(category.planned_implementation_ids)
        assert set(category.implementation_ids) <= implemented_ids
    assert all(item.status is EnvironmentCategoryStatus.AVAILABLE for item in implementations)


def test_default_catalog_has_all_six_categories() -> None:
    catalog = default_environment_category_catalog()
    assert {item.category_id for item in catalog.categories()} == set(EnvironmentCategoryId)
    assert catalog.implementations(EnvironmentCategoryId.GUI) == ()


def test_new_family_contracts_are_typed_and_digestable() -> None:
    gui = GuiEnvironmentSpec("gui-ref", "1", (1280, 720), True, (GuiActionKind.CLICK,))
    web = WebEnvironmentSpec("web-ref", "1", "https://example.test", True, (WebActionKind.CLICK,))
    software = SoftwareEnvironmentSpec("software-ref", "1", "C:\\workspace", supported_actions=(SoftwareActionKind.TEST,))
    text = TextWorldEnvironmentSpec("text-ref", "1", True, (TextWorldActionKind.COMMAND,))
    assert len(gui.spec_digest) == 64
    assert len(web.spec_digest) == 64
    assert len(software.spec_digest) == 64
    assert len(text.spec_digest) == 64
