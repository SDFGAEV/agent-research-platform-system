from __future__ import annotations

from dataclasses import dataclass
import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from .contracts import (
    STANDARD_SYSTEM_SHAPE,
    AuthorityDescriptor,
    SystemDescriptor,
    SystemIdentity,
    SystemLayer,
)


@dataclass(frozen=True, slots=True)
class _CatalogSemantics:
    authority: str
    must_not_own: str
    owns: str
    package_prefix: str
    parent: str | None
    shape: tuple[str, ...]
    requires: tuple[str, ...]
    provides: tuple[str, ...]
    components: tuple[str, ...]


def _string_tuple(value: object, *, field: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise RuntimeError(f"invalid packaged catalog {field} for {key!r}")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise RuntimeError(f"duplicate packaged catalog {field} for {key!r}")
    return result


def _package_prefix_exists(prefix: str) -> bool:
    """Check that catalog ownership points at a real source package/module."""

    root = Path(__file__).resolve().parents[5]
    candidate = root.joinpath(*prefix.split("."))
    return (
        candidate.is_dir()
        or candidate.with_suffix(".py").is_file()
        or (candidate / "__init__.py").is_file()
    )


def _parse_semantics(key: str, value: object) -> _CatalogSemantics:
    required = {
        "authority", "must_not_own", "owns", "package_prefix", "parent", "shape",
        "requires", "provides", "components",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError(f"invalid packaged catalog descriptor for {key!r}")
    text_fields = ("authority", "must_not_own", "owns", "package_prefix")
    if not all(isinstance(value[field], str) and value[field].strip() for field in text_fields):
        raise RuntimeError(f"invalid ownership semantics for {key!r}")
    shape = _string_tuple(value["shape"], field="shape", key=key)
    if shape != STANDARD_SYSTEM_SHAPE:
        raise RuntimeError(f"unsupported packaged system shape for {key!r}")
    parent = value["parent"]
    if parent is not None and not isinstance(parent, str):
        raise RuntimeError(f"invalid packaged catalog parent for {key!r}")
    normalized_parent = parent.replace(".", "/") if isinstance(parent, str) else None
    return _CatalogSemantics(
        authority=value["authority"],
        must_not_own=value["must_not_own"],
        owns=value["owns"],
        package_prefix=value["package_prefix"],
        parent=normalized_parent,
        shape=shape,
        requires=_string_tuple(value["requires"], field="requires", key=key),
        provides=_string_tuple(value["provides"], field="provides", key=key),
        components=_string_tuple(value["components"], field="components", key=key),
    )


@lru_cache(maxsize=1)
def _load_catalog_semantics() -> dict[str, _CatalogSemantics]:
    """Load and validate the single canonical recursive system catalog."""
    catalog_resource = files("noetrium_platform.foundation.governance.system_registry").joinpath("catalog.json")
    try:
        raw = json.loads(catalog_resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot load packaged canonical system catalog") from exc
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError("packaged canonical system catalog is not a non-empty object")

    result: dict[str, _CatalogSemantics] = {}
    seen: set[str] = set()
    for key, value in raw.items():
        if not isinstance(key, str):
            raise RuntimeError("packaged catalog keys must be strings")
        parts = tuple(part for part in key.split("/") if part)
        if not parts or "/".join(parts) != key:
            raise RuntimeError(f"invalid packaged catalog identity for {key!r}")
        semantics = _parse_semantics(key, value)
        if not _package_prefix_exists(semantics.package_prefix):
            raise RuntimeError(
                f"catalog package_prefix {semantics.package_prefix!r} for {key!r} "
                "does not resolve to a source package/module"
            )
        expected_parent = None if len(parts) == 1 else "/".join(parts[:-1])
        if semantics.parent != expected_parent:
            raise RuntimeError(f"parent drift for {key!r}")
        if expected_parent is not None and expected_parent not in seen:
            raise RuntimeError(f"catalog parent must precede child: {key!r}")
        result[key] = semantics
        seen.add(key)

    known = set(result)
    capability_owners: dict[str, str] = {}
    for key, semantics in result.items():
        for dependency in semantics.requires:
            if dependency not in known:
                raise RuntimeError(
                    f"catalog dependency {dependency!r} for {key!r} is not registered"
                )
            if dependency == key:
                raise RuntimeError(f"catalog node {key!r} cannot require itself")
        for component in semantics.components:
            if component not in known:
                raise RuntimeError(
                    f"catalog component {component!r} for {key!r} is not registered"
                )
            if component == key:
                raise RuntimeError(f"catalog node {key!r} cannot contain itself as a component")
        for capability in semantics.provides:
            previous = capability_owners.get(capability)
            if previous is not None:
                raise RuntimeError(
                    f"catalog capability {capability!r} is provided by both "
                    f"{previous!r} and {key!r}"
                )
            capability_owners[capability] = key
    return result


def _descriptor_from_catalog(key: str, semantics: _CatalogSemantics) -> SystemDescriptor:
    parts = key.split("/")
    return SystemDescriptor(
        identity=SystemIdentity(parts[0], tuple(parts[1:])),
        layer=SystemLayer(parts[0]),
        package_prefix=semantics.package_prefix,
        authorities=(AuthorityDescriptor(semantics.authority),),
        owns=semantics.owns,
        must_not_own=semantics.must_not_own,
        shape=semantics.shape,
        requires=semantics.requires,
        provides=semantics.provides,
        components=semantics.components,
    )


SYSTEM_CATALOG: tuple[SystemDescriptor, ...] = tuple(
    _descriptor_from_catalog(key, semantics)
    for key, semantics in _load_catalog_semantics().items()
)


def system_catalog() -> tuple[SystemDescriptor, ...]:
    """Return the canonical recursive platform system tree."""

    return SYSTEM_CATALOG


__all__ = ["SYSTEM_CATALOG", "system_catalog"]
