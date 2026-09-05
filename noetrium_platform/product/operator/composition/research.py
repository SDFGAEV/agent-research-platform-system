from __future__ import annotations

from noetrium_platform.product.operator.maintenance.composition.cli import main as manage_main
from noetrium_platform.product.operator.runtime.research_cli import run_research_cli

from .cli import main as diagnose_main
from .project_experience import build_project_facade


def main(argv: list[str] | None = None) -> int:
    return run_research_cli(
        argv,
        diagnose_main=diagnose_main,
        manage_main=manage_main,
        project_experience=build_project_facade(),
    )


__all__ = ["main"]
