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

from keel import checkpoint, git, ledger
from keel import config as cfg

from . import dash, render, runstate, terminal

_CLEAR = "\x1b[2J\x1b[H"


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


def _resolve_checkpoint(
    args: argparse.Namespace, config: cfg.ProjectConfig,
) -> tuple[str | None, dict]:
    """Resolve ``(current_step, live_state)`` from the run's checkpoint.

    An explicit ``--checkpoint-step`` pins the step and skips the file (no live
    state). Otherwise read the checkpoint under ``--root`` and take both its
    ``position.current_step`` *and* its ``state`` block — the latter is what lets
    ``--follow`` show live merge progress, not just position. Fail-soft: a
    missing/malformed checkpoint yields ``(None, {})`` so position falls back to
    the ledger.
    """
    if args.checkpoint_step is not None:
        return args.checkpoint_step, {}
    try:
        record = checkpoint.read_checkpoint(checkpoint.resolve_path(args.root, config))
    except (checkpoint.CheckpointError, OSError):
        # CheckpointError = bad content; OSError = bad path (dir/symlink/perms).
        return None, {}
    return (runstate.current_step_from_checkpoint(record),
            runstate.live_state_from_checkpoint(record))


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

    cp_step, cp_state = _resolve_checkpoint(args, config)
    state = runstate.build_run_state(
        record, checkpoint_step=cp_step, checkpoint_state=cp_state, command=args.command,
    )
    title = f"{args.command} · issue #{state['issue']}" if state["issue"] else args.command
    html = render.render_html(load_template(), state, title=title)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"keel-visual — wrote {args.out}  ({state['command']}, at {state['active_id']})")
    return 0


def _resolve_config(args: argparse.Namespace) -> cfg.ProjectConfig | tuple[int, str]:
    """Load the project config, or ``(code, message)`` for a *fatal* config error.

    A bad config path is permanent (it won't fix itself mid-run), so it is fatal
    for every caller — unlike a transient ledger read, which ``--follow`` tolerates.
    """
    try:
        return cfg.load_config(args.path)
    except FileNotFoundError:
        return 1, f"no such config: {args.path}"
    except cfg.ConfigError as exc:
        return 1, str(exc)


def _state_from_config(args: argparse.Namespace, config: cfg.ProjectConfig) -> dict:
    """Build the RunState from an already-loaded config; may raise ``LedgerError``."""
    record = _resolve_record(args, config)
    cp_step, cp_state = _resolve_checkpoint(args, config)
    return runstate.build_run_state(
        record, checkpoint_step=cp_step, checkpoint_state=cp_state, command=args.command,
    )


def _run_state_for(args: argparse.Namespace) -> dict | tuple[int, str]:
    """Resolve the RunState for a one-shot terminal command, or ``(code, message)``."""
    config = _resolve_config(args)
    if isinstance(config, tuple):
        return config
    try:
        return _state_from_config(args, config)
    except ledger.LedgerError as exc:
        return 1, f"invalid run ledger: {exc}"


def _resolve_color(args: argparse.Namespace, out) -> bool:
    if args.color == "auto":
        return hasattr(out, "isatty") and out.isatty()
    return args.color == "always"


def cmd_play(
    args: argparse.Namespace, *, sleep=time.sleep, out=sys.stdout, max_cycles: int | None = None,
) -> int:
    """Render the run in the terminal.

    Default: animate once through the run's steps. ``--loop`` replays it
    continuously; ``--follow`` re-reads the live ledger + checkpoint each
    ``--interval`` and redraws where the run actually is now. ``max_cycles``
    bounds the otherwise-unbounded loop/follow for tests; ``None`` means run
    until interrupted.
    """
    if args.follow:
        return _play_follow(args, sleep=sleep, out=out, max_cycles=max_cycles)

    state = _run_state_for(args)
    if isinstance(state, tuple):
        print(state[1], file=sys.stderr)
        return state[0]
    color = _resolve_color(args, out)
    order = terminal.exercised_indices(state) or [state["active_index"]]
    if args.step is not None:
        order = [max(0, min(args.step, len(state["steps"]) - 1))]
    elif args.once:
        order = [state["active_index"]]

    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            _render_pass(args, state, order, color=color, out=out, sleep=sleep,
                         clear_first=cycle > 0 or not args.once)
            cycle += 1
            if not args.loop:
                break
    except KeyboardInterrupt:
        out.write("\n")
    return 0


def _render_pass(args, state, order, *, color, out, sleep, clear_first) -> None:
    for i, active in enumerate(order):
        if not args.no_clear and (clear_first or i > 0):
            out.write(_CLEAR)
        out.write(terminal.frame(state, active, style=args.style, color=color) + "\n")
        out.flush()
        if len(order) > 1 and i < len(order) - 1:
            sleep(max(0.0, 1.0 / max(1, args.fps)))


def _play_follow(args, *, sleep, out, max_cycles: int | None) -> int:
    """Live mode: re-resolve the run-state each interval and redraw the current step.

    Fail-soft against *transient* read errors: a live run appends to the ledger
    concurrently, so an unlucky tick can read a half-written line
    (``LedgerError``). That must not kill the follower — the tick is skipped and
    the last good frame is held (or a "waiting" line shown until the first
    success). Only a *fatal* config error (resolved once up front) exits.
    """
    config = _resolve_config(args)
    if isinstance(config, tuple):
        print(config[1], file=sys.stderr)
        return config[0]
    color = _resolve_color(args, out)
    last_frame: str | None = None
    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            try:
                state = _state_from_config(args, config)
                last_frame = terminal.frame(
                    state, state["active_index"], style=args.style, color=color
                )
            except (ledger.LedgerError, OSError):
                pass  # transient (bad content or path): hold last frame, keep polling
            if not args.no_clear:
                out.write(_CLEAR)
            out.write((last_frame or "keel-visual — waiting for run state…") + "\n")
            out.flush()
            cycle += 1
            sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        out.write("\n")
    return 0


def _discover_runs(args: argparse.Namespace, config: cfg.ProjectConfig) -> list[dict]:
    """Find every worktree with a live checkpoint and build its board row.

    Worktrees come from ``git worktree list`` (keel's own run isolation — each
    parallel ship runs in its own worktree). A worktree with no readable
    checkpoint is skipped (not an active run). All per-worktree reads are
    fail-soft so one bad run never blanks the board.
    """
    result = git.worktree_list(cwd=args.root)
    paths = dash.parse_worktrees(result.output) if result.ok else [args.root]
    rows: list[dict] = []
    for worktree in paths:
        try:
            record = checkpoint.read_checkpoint(checkpoint.resolve_path(worktree, config))
        except (checkpoint.CheckpointError, OSError):
            # One malformed worktree (bad content or bad path) must never blank
            # the whole board — skip it.
            record = None
        if record is None:
            continue
        identity = dash.identity_from_checkpoint(record)
        ship_record = _latest_ship_record(worktree, config, identity.get("pr"))
        run_state = runstate.build_run_state(
            ship_record,
            checkpoint_step=runstate.current_step_from_checkpoint(record),
            checkpoint_state=runstate.live_state_from_checkpoint(record),
            command=identity.get("command") or "ship",
        )
        rows.append(dash.board_row(run_state, identity))
    return rows


def _latest_ship_record(worktree: str, config: cfg.ProjectConfig, pr: int | None) -> dict | None:
    """Best-effort: the worktree's latest ship_run record for ``pr`` (None if absent)."""
    try:
        records = ledger.read_records(ledger.resolve_path(worktree, config))
    except (ledger.LedgerError, OSError):
        return None
    if pr is not None:
        return ledger.latest_ship_run_for_pr(records, pr)
    return None


def cmd_dash(
    args: argparse.Namespace, *, sleep=time.sleep, out=sys.stdout, max_cycles: int | None = None,
) -> int:
    """Live board of every active run across the project's worktrees."""
    config = _resolve_config(args)
    if isinstance(config, tuple):
        print(config[1], file=sys.stderr)
        return config[0]
    color = _resolve_color(args, out)
    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            rows = _discover_runs(args, config)
            if not args.no_clear:
                out.write(_CLEAR)
            out.write(dash.render_board(rows, color=color) + "\n")
            out.flush()
            cycle += 1
            if args.once:
                break
            sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        out.write("\n")
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
    pl.add_argument("--loop", action="store_true", help="replay continuously (Ctrl-C to stop)")
    pl.add_argument("--follow", action="store_true",
                    help="live: re-read ledger + checkpoint each interval, show where the run is")
    pl.add_argument("--interval", type=float, default=1.0, help="--follow poll interval in seconds")
    pl.add_argument("--no-clear", action="store_true", help="keep frames (no screen clear)")
    pl.add_argument("--color", default="auto", choices=("auto", "always", "never"),
                    help="ANSI colour (auto = only on a tty)")
    pl.set_defaults(func=cmd_play)

    pd = sub.add_parser("dash", help="live board of all active runs across the project's worktrees")
    pd.add_argument("path", help="path to project.yaml")
    pd.add_argument("--root", default=".", help="repo root to discover worktrees from")
    pd.add_argument("--interval", type=float, default=2.0, help="refresh interval in seconds")
    pd.add_argument("--once", action="store_true", help="render the board once and exit")
    pd.add_argument("--no-clear", action="store_true", help="keep frames (no screen clear)")
    pd.add_argument("--color", default="auto", choices=("auto", "always", "never"),
                    help="ANSI colour (auto = only on a tty)")
    pd.set_defaults(func=cmd_dash)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
