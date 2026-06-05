"""The ``keel`` command-line interface (thin; logic lives in the pure modules).

Subcommands
-----------
``keel version``                 print the version
``keel validate <project.yaml…>``  validate config(s) against the schema (CI gate)
``keel plan <project.yaml>``       render the backbone plan for a project (dry-run view)
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import config as cfg
from . import orchestrator as orch
from .extensions import ExtensionError, load_extensions


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"keel {__version__}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    rc = 0
    for path in args.paths:
        try:
            config = cfg.load_config(path)
        except FileNotFoundError:
            print(f"MISSING  {path}")
            rc = 1
            continue
        except cfg.ConfigError as exc:
            print(f"INVALID  {path}")
            print(f"         {exc}".replace("\n", "\n         "))
            rc = 1
            continue

        if args.root is not None:
            try:
                load_extensions(config, args.root, strict=True)
            except ExtensionError as exc:
                print(f"INVALID  {path} (extensions)")
                print(f"         {exc}".replace("\n", "\n         "))
                rc = 1
                continue
        print(f"OK       {path}  ({config.repo or '-'}, base {config.base_branch})")
    return rc


def _cmd_plan(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    loaded, problems = load_extensions(config, args.root, strict=False)
    print(orch.render_plan(config, orch.build_plan(config, loaded)))
    for prob in problems:
        print(f"  ! extension not loaded: {prob}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keel", description="keel — workflow core")
    parser.add_argument("--version", action="version", version=f"keel {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_version = sub.add_parser("version", help="print the keel version")
    p_version.set_defaults(func=_cmd_version)

    p_validate = sub.add_parser("validate", help="validate project config(s) against the schema")
    p_validate.add_argument("paths", nargs="+", help="path(s) to project.yaml")
    p_validate.add_argument("--root", default=None,
                            help="repo root; if set, also strict-validate extensions")
    p_validate.set_defaults(func=_cmd_validate)

    p_plan = sub.add_parser("plan", help="render the backbone plan for a project")
    p_plan.add_argument("path", help="path to project.yaml")
    p_plan.add_argument("--root", default=".", help="repo root for resolving extensions")
    p_plan.set_defaults(func=_cmd_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return func(args)
