from __future__ import annotations

import argparse
from pathlib import Path

from noetrium_platform.infrastructure.lifecycle.python.api import PythonEnvironmentSpec

from .context import ManagementCommandContext
from .scope_args import add_scope_arguments, scope_from_args

GROUP = "env"


def register(groups) -> None:
    parser = groups.add_parser(GROUP)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("environment_id")
    create.add_argument("--backend", default="venv", choices=("venv", "conda", "mamba"))
    create.add_argument("--python", default="python3")
    create.add_argument("--python-version")
    create.add_argument("--description", default="")
    create.add_argument("--tag", action="append", default=[])
    add_scope_arguments(create)
    register_existing = sub.add_parser("register")
    register_existing.add_argument("environment_id")
    register_existing.add_argument("root", type=Path)
    register_existing.add_argument("--backend", default="venv", choices=("venv", "conda", "mamba"))
    register_existing.add_argument("--description", default="")
    register_existing.add_argument("--tag", action="append", default=[])
    add_scope_arguments(register_existing)
    migrate = sub.add_parser("migrate-legacy")
    migrate.add_argument("environment_id")
    migrate.add_argument("--python", required=True)
    migrate.add_argument("--python-version", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--tag", action="append", default=[])
    sub.add_parser("backends")
    show = sub.add_parser("show")
    show.add_argument("environment_id")
    install = sub.add_parser("install")
    install.add_argument("environment_id")
    install.add_argument("requirements", type=Path)
    install.add_argument("--extra-arg", action="append", default=[])
    pip_install = sub.add_parser("pip-install")
    pip_install.add_argument("environment_id")
    pip_install.add_argument("packages", nargs="+")
    pip_install.add_argument("--extra-arg", action="append", default=[])
    packages = sub.add_parser("packages")
    packages.add_argument("environment_id")
    uninstall = sub.add_parser("pip-uninstall")
    uninstall.add_argument("environment_id")
    uninstall.add_argument("packages", nargs="+")
    freeze = sub.add_parser("freeze")
    freeze.add_argument("environment_id")
    export = sub.add_parser("export")
    export.add_argument("environment_id")
    export.add_argument("target", type=Path)
    clone = sub.add_parser("clone")
    clone.add_argument("source_environment_id")
    clone.add_argument("environment_id")
    clone.add_argument("--backend", default="venv", choices=("venv", "conda", "mamba"))
    clone.add_argument("--python", default="python3")
    clone.add_argument("--python-version")
    clone.add_argument("--description", default="")
    clone.add_argument("--tag", action="append", default=[])
    add_scope_arguments(clone)
    check = sub.add_parser("check")
    check.add_argument("environment_id")
    run = sub.add_parser("run")
    run.add_argument("environment_id")
    run.add_argument("args", nargs=argparse.REMAINDER)
    remove = sub.add_parser("remove")
    remove.add_argument("environment_id")
    command = sub.add_parser("command")
    command.add_argument("environment_id")
    command.add_argument("args", nargs=argparse.REMAINDER)


def dispatch(args, context: ManagementCommandContext):
    lifecycle = context.environments.lifecycle
    execution = context.environments.execution
    packages = context.environments.packages
    if args.action == "create":
        return lifecycle.create(
            PythonEnvironmentSpec(
                args.environment_id,
                scope_from_args(args),
                backend=args.backend,
                python_executable=args.python,
                python_version=args.python_version,
                description=args.description,
                tags=tuple(args.tag),
            )
        )
    if args.action == "register":
        return lifecycle.register_existing(
            PythonEnvironmentSpec(
                args.environment_id,
                scope_from_args(args),
                backend=args.backend,
                description=args.description,
                tags=tuple(args.tag),
            ),
            args.root,
        )
    if args.action == "migrate-legacy":
        return lifecycle.migrate_legacy(
            args.environment_id,
            python_executable=args.python,
            python_version=args.python_version,
        )
    if args.action == "list":
        return lifecycle.list(tags=tuple(args.tag))
    if args.action == "backends":
        return lifecycle.backends()
    if args.action == "show":
        return lifecycle.get(args.environment_id)
    if args.action == "install":
        return packages.install(args.environment_id, args.requirements, extra_args=tuple(args.extra_arg))
    if args.action == "pip-install":
        return packages.install_packages(args.environment_id, tuple(args.packages), extra_args=tuple(args.extra_arg))
    if args.action == "packages":
        return packages.packages(args.environment_id)
    if args.action == "pip-uninstall":
        return packages.uninstall_packages(args.environment_id, tuple(args.packages))
    if args.action == "freeze":
        return packages.freeze(args.environment_id)
    if args.action == "export":
        return {"path": packages.export_requirements(args.environment_id, args.target)}
    if args.action == "clone":
        return packages.clone(
            args.source_environment_id,
            PythonEnvironmentSpec(
                args.environment_id,
                scope_from_args(args),
                backend=args.backend,
                python_executable=args.python,
                python_version=args.python_version,
                description=args.description,
                tags=tuple(args.tag),
            ),
        )
    if args.action == "check":
        return packages.check(args.environment_id)
    if args.action == "run":
        return execution.run(args.environment_id, *args.args)
    if args.action == "remove":
        return {"removed": lifecycle.remove(args.environment_id)}
    if args.action == "command":
        return execution.command(args.environment_id, *args.args)
    raise ValueError(f"unsupported environment management action: {args.action}")


__all__ = ["GROUP", "dispatch", "register"]
