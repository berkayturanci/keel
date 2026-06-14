"""keel-visual CLI: render a keel run as an animated 2D/3D HTML page.

This is the thin I/O layer. It reads keel core's own artifacts — a ship_run
ledger record (live via the configured ledger under ``--root``, or an offline
``--ledger-jsonl`` fixture) and an optional checkpoint step — projects them onto
a RunState via :mod:`keel_visual.runstate` (pure), and writes the visualizer
HTML via :mod:`keel_visual.render` (pure). keel-visual never drives a run; it
only renders one, so it depends on keel core but core never depends on it.
"""

from __future__ import annotations

import argparse
import sys
import time
from importlib import resources
from pathlib import Path

from keel import config as cfg
from keel import ledger

from . import render, runstate, terminal


def load_template() -> str:
    """Load the packaged visualizer HTML template text."""
    return resources.files("keel_visual.templates").joinpath("runviz.html").read_text(
        encoding="utf-8"
    )


def _resolve_record(args: argparse.Namespace, config: cfg.ProjectConfig) -> dict | None:
    """Load the ship_run record for the run to render (offline fixture or live ledger)."""
    fixture = getattr(args, "ledger_jsonl", None)
    if fixture is not None:
        records = ledger.parse_records(Path(fixture).read_text(encoding="utf-8"))
    else:
        records = ledger.read_records(ledger.resolve_path(args.root, config))
    if args.pr is not None:
        return ledger.latest_ship_run_for_pr(records, args.pr)
    ship_runs = [r for r in records if r.get("record_type") == ledger.RECORD_TYPE_SHIP_RUN]
    return ship_runs[-1] if ship_runs else None


def cmd_render(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        record = _resolve_record(args, config)
    except ledger.LedgerError as exc:
        print(f"invalid run ledger: {exc}", file=sys.stderr)
        return 1

    state = runstate.build_run_state(
        record, checkpoint_step=args.checkpoint_step, command=args.command,
    )
    title = f"{args.command} · issue #{state['issue']}" if state["issue"] else args.command
    html = render.render_html(load_template(), state, title=title)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"keel-visual — wrote {args.out}  ({state['command']}, at {state['active_id']})")
    return 0


def _run_state_for(args: argparse.Namespace) -> dict | tuple[int, str]:
    """Resolve the RunState for a terminal command, or ``(code, message)`` on error."""
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        return 1, f"no such config: {args.path}"
    except cfg.ConfigError as exc:
        return 1, str(exc)
    try:
        record = _resolve_record(args, config)
    except ledger.LedgerError as exc:
        return 1, f"invalid run ledger: {exc}"
    return runstate.build_run_state(
        record, checkpoint_step=args.checkpoint_step, command=args.command,
    )


def cmd_play(args: argparse.Namespace, *, sleep=time.sleep, out=sys.stdout) -> int:
    state = _run_state_for(args)
    if isinstance(state, tuple):
        print(state[1], file=sys.stderr)
        return state[0]
    color = sys.stdout.isatty() if args.color == "auto" else (args.color == "always")
    order = terminal.exercised_indices(state) or [state["active_index"]]
    if args.step is not None:
        order = [max(0, min(args.step, len(state["steps"]) - 1))]
    elif args.once:
        order = [state["active_index"]]
    for i, active in enumerate(order):
        if not args.no_clear and (i > 0 or not args.once):
            out.write("\x1b[2J\x1b[H")
        out.write(terminal.frame(state, active, style=args.style, color=color) + "\n")
        out.flush()
        if len(order) > 1 and i < len(order) - 1:
            sleep(max(0.0, 1.0 / max(1, args.fps)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keel-visual", description="visualize a keel run")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render", help="render a ship_run as an animated HTML page")
    p.add_argument("path", help="path to project.yaml")
    p.add_argument("--root", default=".", help="repo root for the ledger path")
    p.add_argument("--pr", type=int, default=None, help="PR number (default: latest ship_run)")
    p.add_argument("--ledger-jsonl", default=None, help="offline run-ledger JSONL fixture")
    p.add_argument("--checkpoint-step", default=None, help="current step id (e.g. s8)")
    p.add_argument("--command", default="ship", choices=sorted(runstate.COMMAND_STEPS),
                   help="which command's steps to highlight")
    p.add_argument("--out", default="keel-run.html", help="output HTML path")
    p.set_defaults(func=cmd_render)

    pl = sub.add_parser("play", help="animate the run in the terminal (2D flow / wave ribbon)")
    pl.add_argument("path", help="path to project.yaml")
    pl.add_argument("--root", default=".", help="repo root for the ledger path")
    pl.add_argument("--pr", type=int, default=None, help="PR number (default: latest ship_run)")
    pl.add_argument("--ledger-jsonl", default=None, help="offline run-ledger JSONL fixture")
    pl.add_argument("--checkpoint-step", default=None, help="current step id (e.g. s8)")
    pl.add_argument("--command", default="ship", choices=sorted(runstate.COMMAND_STEPS),
                    help="which command's steps to play")
    pl.add_argument("--style", default="flow", choices=("flow", "wave"), help="render style")
    pl.add_argument("--fps", type=int, default=2, help="frames per second during playback")
    pl.add_argument("--step", type=int, default=None, help="render a single step frame and exit")
    pl.add_argument("--once", action="store_true", help="render current frame once (no animation)")
    pl.add_argument("--no-clear", action="store_true", help="keep frames (no screen clear)")
    pl.add_argument("--color", default="auto", choices=("auto", "always", "never"),
                    help="ANSI colour (auto = only on a tty)")
    pl.set_defaults(func=cmd_play)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
