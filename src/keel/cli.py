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
from pathlib import Path

from . import __version__, gates, git, github, jury, scaffold, ship, window
from . import config as cfg
from . import findings as fnd
from . import orchestrator as orch
from .extensions import ExtensionError, load_extensions
from .gates import GateSpec
from .runner import command_gate_runner


def _gate_runner(root: str, diff_text: str):
    """A gate runner that handles command gates plus the ``jury`` built-in (on the diff)."""
    commands = command_gate_runner(root)

    def run(spec: GateSpec):
        if spec.kind == "builtin" and spec.id == "jury":
            return jury.run_gate(diff_text, cwd=root)
        return commands(spec)

    return run


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
    try:
        plan = orch.build_plan(config, loaded)
    except gates.GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(orch.render_plan(config, plan))
    for prob in problems:
        print(f"  ! extension not loaded: {prob}", file=sys.stderr)
    return 0


def _cmd_run_gates(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    loaded, problems = load_extensions(config, args.root, strict=False)
    for prob in problems:
        print(f"  ! extension not loaded: {prob}", file=sys.stderr)

    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    diff_text = git.diff(config.base_branch, "HEAD", cwd=args.root)
    outcomes = gates.run_gates(specs, _gate_runner(args.root, diff_text))
    for o in outcomes:
        status = "ok" if o.ok else "FAIL"
        print(f"  {status:>4}  {o.gate}")

    verdict = fnd.summarize(gates.collect_findings(outcomes))
    for f in verdict.findings:
        print(f"    [{f.severity}] {f.source}: {f.message.splitlines()[0]}")
    if verdict.blocked:
        print("BLOCKED — merge is gated by the findings above")
        return 1
    return 0


def _cmd_window(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not config.timezone or not config.merge_window:
        print("no merge window configured (needs timezone + merge_window)")
        return 0
    is_open = window.is_merge_open(config.timezone, config.merge_window)
    state = "OPEN" if is_open else "CLOSED (night no-merge)"
    print(f"merge window {state}  [{config.timezone} {config.merge_window}]")
    return 0


def _cmd_ship(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    loaded, problems = load_extensions(config, args.root, strict=False)
    for prob in problems:
        print(f"  ! extension not loaded: {prob}", file=sys.stderr)

    changed = git.changed_files(config.base_branch, "HEAD", cwd=args.root)
    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    diff_text = git.diff(config.base_branch, "HEAD", cwd=args.root)
    outcomes = gates.run_gates(specs, _gate_runner(args.root, diff_text))
    verdict = fnd.summarize(gates.collect_findings(outcomes))
    ci_conclusion = github.ci_conclusion(args.pr, cwd=args.root) if args.pr else None

    a = ship.assess(
        changed_files=changed,
        gate_verdict=verdict,
        tier3_globs=config.knobs.tier3_globs,
        docs_globs=config.knobs.docs_gate_paths,
        timezone=config.timezone,
        merge_window=config.merge_window,
        merge_window_mode=config.merge_window_mode,
        ci_conclusion=ci_conclusion,
        is_blocker=args.hotfix,
    )

    name = config.repo or config.extends
    print(f"keel ship — {name}  (base {config.base_branch})")
    print(f"  changed files : {len(changed)}")
    print(f"  risk tier     : TIER-{a.tier}  → {a.reviewers} reviewer(s)")
    window = "OPEN" if a.window_open else f"CLOSED ({config.merge_window_mode}, night no-merge)"
    print(f"  merge window  : {window}")
    ci_str = "unknown" if a.ci_ok is None else ("passing" if a.ci_ok else "FAILING")
    print(f"  ci            : {ci_str}")
    for o in outcomes:
        print(f"  gate {o.gate:<14} {'ok' if o.ok else 'FAIL'}")
    if a.halted:  # pragma: no cover - display only; logic covered in ship.assess tests
        print("  pipeline      : HALTED (merge window paused)")
    if a.bypassed_window:  # pragma: no cover - display only; logic covered in ship.assess
        print("  audit         : hotfix bypassed the merge window")
    print(f"  decision      : {a.merge.action.upper()} — {a.merge.reason}")
    print("  note: dry assessment; live merge (s10) needs a configured runner (git + gh auth).")
    return 0 if a.merge.action != "block" else 1


def _ask(prompt: str, default: str) -> str:  # pragma: no cover - interactive I/O
    raw = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    return raw or default


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    target = root / ".keel" / "project.yaml"
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    stack = scaffold.detect_stack(root)
    repo = root.resolve().name
    if args.wizard:
        print(f"keel init wizard — detected stack: {stack} (Enter accepts each default)")
        text = scaffold.wizard(stack, _ask, repo=repo)
    else:
        text = scaffold.default_config(stack, repo=repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}  (detected stack: {stack})")
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

    p_run = sub.add_parser("run-gates", help="run a project's command gates")
    p_run.add_argument("path", help="path to project.yaml")
    p_run.add_argument("--root", default=".", help="repo root for commands + extensions")
    p_run.set_defaults(func=_cmd_run_gates)

    p_window = sub.add_parser("window", help="is the merge window open now?")
    p_window.add_argument("path", help="path to project.yaml")
    p_window.set_defaults(func=_cmd_window)

    p_ship = sub.add_parser("ship", help="dry ship assessment (tier, window, gates, decision)")
    p_ship.add_argument("path", help="path to project.yaml")
    p_ship.add_argument("--root", default=".", help="repo root for git, gates + extensions")
    p_ship.add_argument("--pr", type=int, default=None, help="PR number for CI status (gh)")
    p_ship.add_argument("--hotfix", action="store_true", help="emergency: bypass the merge window")
    p_ship.set_defaults(func=_cmd_ship)

    p_init = sub.add_parser("init", help="scaffold a default .keel/project.yaml for this repo")
    p_init.add_argument("--root", default=".", help="repo root to scaffold into")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_init.add_argument("--wizard", action="store_true", help="prompt for values interactively")
    p_init.set_defaults(func=_cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return func(args)
