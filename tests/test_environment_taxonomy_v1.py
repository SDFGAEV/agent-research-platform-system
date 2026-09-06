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


def test_catalog_exposes_planned_backends_as_contract_only() -> None:
    categories = {item.category_id: item for item in canonical_environment_categories()}
    implementations = canonical_environment_implementations()
    by_id = {item.implementation_id: item for item in implementations}
    for category in categories.values():
        assert set(category.implementation_ids) <= set(by_id)
        assert set(category.planned_implementation_ids) <= set(by_id)
        assert all(
            by_id[item].status is EnvironmentCategoryStatus.CONTRACT_ONLY
            for item in category.planned_implementation_ids
        )
    assert all(
        by_id[item].status is EnvironmentCategoryStatus.AVAILABLE
        for category in categories.values()
        for item in category.implementation_ids
    )


def test_default_catalog_has_all_six_categories() -> None:
    catalog = default_environment_category_catalog()
    assert {item.category_id for item in catalog.categories()} == set(EnvironmentCategoryId)
    gui = catalog.implementations(EnvironmentCategoryId.GUI)
    assert {item.implementation_id for item in gui} == {
        "gui.desktop_vm", "gui.mobile_emulator",
    }
    assert all(item.status is EnvironmentCategoryStatus.CONTRACT_ONLY for item in gui)
    assert catalog.implementation("web.browser").status is EnvironmentCategoryStatus.CONTRACT_ONLY
    assert len(catalog.catalog_digest) == 64
    assert catalog.catalog_digest == default_environment_category_catalog().catalog_digest


def test_new_family_contracts_are_typed_and_digestable() -> None:
    gui = GuiEnvironmentSpec("gui-ref", "1", (1280, 720), True, (GuiActionKind.CLICK,))
    web = WebEnvironmentSpec("web-ref", "1", "https://example.test", True, (WebActionKind.CLICK,))
    software = SoftwareEnvironmentSpec("software-ref", "1", "C:\\workspace", supported_actions=(SoftwareActionKind.TEST,))
    text = TextWorldEnvironmentSpec("text-ref", "1", True, (TextWorldActionKind.COMMAND,))
    assert len(gui.spec_digest) == 64
    assert len(web.spec_digest) == 64
    assert len(software.spec_digest) == 64
    assert len(text.spec_digest) == 64
