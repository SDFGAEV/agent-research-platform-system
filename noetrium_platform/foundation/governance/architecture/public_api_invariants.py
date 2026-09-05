from __future__ import annotations

from pathlib import Path

from noetrium_platform.foundation.governance.system_registry.api import system_catalog

from .import_graph import module_name, scan_imports
from .source_scan import SourceInvariantViolation, violation


_CONCRETE_CHILDREN = ("runtime", "providers", "composition")


def _is_concrete_target(module: str, prefix: str) -> bool:
    return any(
        module == f"{prefix}.{child}" or module.startswith(f"{prefix}.{child}.")
        for child in _CONCRETE_CHILDREN
    )


def audit_registered_public_facades(root: Path) -> list[SourceInvariantViolation]:
    """Registered roots and API facades may expose contracts, never implementations.

    The import graph is used instead of the lightweight source scanner so relative
    imports are resolved before comparing module boundaries. Both the registered
    package root and its nested api/__init__.py facade are audited.
    """

    root = Path(root).resolve()
    rows: list[SourceInvariantViolation] = []
    edges = scan_imports(root)
    for descriptor in system_catalog():
        package = root.joinpath(*descriptor.package_prefix.split("."))
        prefix = descriptor.package_prefix
        facade_paths = [package / "__init__.py"]
        api_facade = package / "api" / "__init__.py"
        if api_facade.is_file():
            facade_paths.append(api_facade)
        if not (package / "api").is_dir():
            continue
        for facade in facade_paths:
            if not facade.is_file():
                continue
            source_module = module_name(root, facade)
            for edge in edges:
                if edge.source_module != source_module:
                    continue
                if not _is_concrete_target(edge.target_module, source_module):
                    continue
                rows.append(
                    violation(
                        root,
                        facade,
                        "registered_public_api_facade",
                        edge.line,
                        (
                            f"registered boundary {descriptor.identity.key} re-exports concrete layer "
                            f"{edge.target_module}; public facades may expose API contracts only"
                        ),
                    )
                )
    return rows


__all__ = ["audit_registered_public_facades"]
