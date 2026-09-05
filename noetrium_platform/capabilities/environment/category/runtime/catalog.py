from __future__ import annotations

from noetrium_platform.capabilities.environment.category.api.contracts import (
    EnvironmentCategoryDescriptor,
    EnvironmentCategoryId,
    EnvironmentImplementationDescriptor,
)


def _category(
    category_id: EnvironmentCategoryId,
    package: str,
    description: str,
    modalities: tuple[str, ...],
    surfaces: tuple[str, ...],
    properties: tuple[str, ...],
    implementations: tuple[str, ...] = (),
    planned: tuple[str, ...] = (),
) -> EnvironmentCategoryDescriptor:
    return EnvironmentCategoryDescriptor(
        category_id=category_id,
        version="1",
        package=package,
        description=description,
        modalities=modalities,
        interaction_surfaces=surfaces,
        world_properties=properties,
        implementation_ids=implementations,
        planned_implementation_ids=planned,
    )


def canonical_environment_categories() -> tuple[EnvironmentCategoryDescriptor, ...]:
    return (
        _category(
            EnvironmentCategoryId.MINECRAFT,
            "environment.minecraft",
            "Persistent voxel open-world environments with spatial state and game actions.",
            ("text", "structured", "visual"),
            ("world_api", "visual", "command"),
            ("persistent_state", "spatial", "stochastic"),
            ("minecraft.mineflayer", "minecraft.rcon"),
        ),
        _category(
            EnvironmentCategoryId.EMBODIED,
            "environment.embodied",
            "Physical or simulated worlds where an agent acts through an embodiment.",
            ("visual", "sensor", "control"),
            ("sensor", "actuator", "trajectory"),
            ("continuous_time", "physical_constraints", "partial_observability"),
            ("embodied.adapter",),
            ("embodied.habitat", "embodied.maniskill", "embodied.real_robot"),
        ),
        _category(
            EnvironmentCategoryId.GUI,
            "environment.gui",
            "Desktop and mobile operating-system interfaces controlled through GUI actions.",
            ("visual", "structured", "accessibility"),
            ("pixels", "accessibility_tree", "keyboard_mouse", "touch"),
            ("persistent_state", "event_driven", "partially_observable"),
            planned=("gui.desktop_vm", "gui.mobile_emulator"),
        ),
        _category(
            EnvironmentCategoryId.WEB,
            "environment.web",
            "Stateful browser and web-application worlds exposed through web surfaces.",
            ("visual", "structured", "text"),
            ("dom", "pixels", "browser_navigation", "http"),
            ("persistent_state", "networked", "partially_observable"),
            planned=("web.browser", "web.live_application"),
        ),
        _category(
            EnvironmentCategoryId.SOFTWARE,
            "environment.software",
            "Repository and operating-system workspaces changed through software actions.",
            ("text", "code", "structured"),
            ("terminal", "filesystem", "repository", "test_runner"),
            ("persistent_state", "deterministic_or_stochastic", "artifact_producing"),
            planned=("software.repository", "software.terminal"),
        ),
        _category(
            EnvironmentCategoryId.TEXT_WORLD,
            "environment.text_world",
            "Text-mediated worlds whose state evolves in response to textual actions.",
            ("text", "structured"),
            ("text_command", "text_observation"),
            ("stateful", "turn_based", "partially_observable"),
            planned=("text_world.interactive_fiction", "text_world.simulation"),
        ),
    )


def canonical_environment_implementations() -> tuple[EnvironmentImplementationDescriptor, ...]:
    return (
        EnvironmentImplementationDescriptor(
            implementation_id="minecraft.mineflayer",
            category_id=EnvironmentCategoryId.MINECRAFT,
            version="1",
            provider_package="noetrium_platform.capabilities.environment.minecraft",
            backend_kind="game_server_bridge",
            capabilities=("actions", "observations", "raw_records"),
            resource_profile={"requires": ["node", "minecraft_server"]},
        ),
        EnvironmentImplementationDescriptor(
            implementation_id="minecraft.rcon",
            category_id=EnvironmentCategoryId.MINECRAFT,
            version="1",
            provider_package="noetrium_platform.capabilities.environment.minecraft",
            backend_kind="rcon_bridge",
            capabilities=("commands", "observations", "raw_records"),
            resource_profile={"requires": ["minecraft_server"]},
        ),
        EnvironmentImplementationDescriptor(
            implementation_id="embodied.adapter",
            category_id=EnvironmentCategoryId.EMBODIED,
            version="1",
            provider_package="noetrium_platform.capabilities.environment.embodied",
            backend_kind="provider_adapter",
            capabilities=("sensors", "actions", "raw_records"),
            resource_profile={"supports": ["simulator", "hardware"]},
        ),
    )


__all__ = [
    "canonical_environment_categories",
    "canonical_environment_implementations",
]
