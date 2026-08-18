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
import json
import os
import re
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any

from keel import checkpoint, flows, git, ledger, runner
from keel import config as cfg

try:  # the additive activity channel needs a core that ships keel.activity
    from keel import activity
except ImportError:  # pragma: no cover - exercised only against pre-activity cores
    activity = None

from . import dash, render, runstate, serve, terminal

_CLEAR = "\x1b[2J\x1b[H"


def load_template() -> str:
    """Load the packaged visualizer HTML template text."""
    return (
        resources.files("keel_visual.templates").joinpath("runviz.html").read_text(encoding="utf-8")
    )


def load_board_template() -> str:
    """Load the packaged multi-project board HTML template text."""
    return (
        resources.files("keel_visual.templates").joinpath("board.html").read_text(encoding="utf-8")
    )


def load_dashboard_template() -> str:
    """Load the packaged live-dashboard HTML template (polls ``/board.json``)."""
    return (
        resources.files("keel_visual.templates")
        .joinpath("dashboard.html")
        .read_text(encoding="utf-8")
    )


def load_swarm_template() -> str:
    """Load the packaged swarm visualizer HTML template text."""
    return (
        resources.files("keel_visual.templates").joinpath("swarm.html").read_text(encoding="utf-8")
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
    args: argparse.Namespace,
    config: cfg.ProjectConfig,
) -> tuple[str | None, dict, str | None]:
    """Resolve ``(current_step, live_state, run_id)`` from the run's checkpoint.

    An explicit ``--checkpoint-step`` pins the step and skips the file (no live
    state, no run id). Otherwise read the checkpoint under ``--root`` and take
    its ``position.current_step``, its ``state`` block — the latter is what lets
    ``--follow`` show live merge progress, not just position — and its
    ``run_id`` (what keys the run's saved jury outcome, see
    :func:`_load_jury_verdict`). Fail-soft: a missing/malformed checkpoint
    yields ``(None, {}, None)`` so position falls back to the ledger.
    """
    if args.checkpoint_step is not None:
        return args.checkpoint_step, {}, None
    try:
        record = checkpoint.read_checkpoint(checkpoint.resolve_path(args.root, config))
    except (checkpoint.CheckpointError, OSError):
        # CheckpointError = bad content; OSError = bad path (dir/symlink/perms).
        return None, {}, None
    run_id = record.get("run_id") if isinstance(record, dict) else None
    return (
        runstate.current_step_from_checkpoint(record),
        runstate.live_state_from_checkpoint(record),
        run_id if isinstance(run_id, str) else None,
    )


# Upper bound on a jury-outcome read — mirrors ai-jury's own replay/cache
# ceiling: a serialized outcome is a few KB, so a multi-MB file is either
# corrupt or hostile and is rejected without being held fully in memory.
_MAX_JURY_OUTCOME_BYTES = 8 * 1024 * 1024

# A run id is used as a filename component under .keel/state/jury/ — only plain
# tokens are accepted so a crafted checkpoint can never point the read outside
# that directory.
_RUN_ID_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def _load_jury_verdict(root: str, run_id: object) -> dict | None:
    """Read + summarise the run's saved ai-jury outcome, or ``None``.

    Discovery convention (issue #576): when keel's jury gate runs, the adapter
    saves ai-jury's serialized outcome JSON to ``.keel/state/jury/<run-id>.json``
    under the run's root. keel-visual only ever *reads* that file — it never
    imports ai-jury (same decoupling as the theater handoff) — and is fail-soft
    end to end: a missing/oversized/non-UTF-8/malformed file, or a run id that
    is not a plain filename token, yields ``None`` — exactly today's mode-only
    jury display. Never raises.
    """
    if not isinstance(run_id, str) or not _RUN_ID_SAFE.match(run_id):
        return None
    path = Path(root) / ".keel" / "state" / "jury" / f"{run_id}.json"
    try:
        # Cap the read itself (not stat-then-read, which is a TOCTOU): read at
        # most the ceiling + 1 so an oversized file is detected cheaply.
        with path.open("r", encoding="utf-8") as fh:
            raw = fh.read(_MAX_JURY_OUTCOME_BYTES + 1)
    except (OSError, ValueError):  # ValueError covers UnicodeDecodeError
        return None
    if len(raw) > _MAX_JURY_OUTCOME_BYTES:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        # RecursionError on hostile deeply-nested JSON is not a ValueError.
        return None
    return runstate.jury_verdict_from_outcome(data)


def cmd_render(args: argparse.Namespace) -> int:
    if getattr(args, "all", False):
        board = _aggregate_board(args.root, _run=subprocess.run)
        nproj = len({e["project"] for e in board})
        html = render.render_board_html(load_board_template(), board, title="keel board")
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"keel-visual — wrote {args.out}  ({len(board)} run(s) across {nproj} project(s))")
        return 0
    if not args.path:
        print("render: provide a path to project.yaml, or use --all", file=sys.stderr)
        return 1
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

    cp_step, cp_state, run_id = _resolve_checkpoint(args, config)
    state = runstate.build_run_state(
        record,
        checkpoint_step=cp_step,
        checkpoint_state=cp_state,
        command=args.command,
        jury_verdict=_load_jury_verdict(args.root, run_id),
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
    cp_step, cp_state, run_id = _resolve_checkpoint(args, config)
    return runstate.build_run_state(
        record,
        checkpoint_step=cp_step,
        checkpoint_state=cp_state,
        command=args.command,
        jury_verdict=_load_jury_verdict(args.root, run_id),
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
    args: argparse.Namespace,
    *,
    sleep=time.sleep,
    out=sys.stdout,
    max_cycles: int | None = None,
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
            _render_pass(
                args,
                state,
                order,
                color=color,
                out=out,
                sleep=sleep,
                clear_first=cycle > 0 or not args.once,
            )
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


def _live_pr(args, config) -> int | None:
    """The **live** run's PR from the checkpoint identifiers, or None.

    Theater is triggered by the live checkpoint (position + jury), so its PR must
    come from the *same* source — not the ledger's ``state["pr"]``, which can be a
    stale prior run's number in the exact window theater fires (a live run at the
    review step before its own ship_run record lands). Fail-soft: a
    missing/malformed checkpoint yields None so callers fall back to ledger/--pr.
    """
    try:
        record = checkpoint.read_checkpoint(checkpoint.resolve_path(args.root, config))
    except (checkpoint.CheckpointError, OSError):
        return None
    return dash.identity_from_checkpoint(record).get("pr")


def _theater_available(_which=None) -> bool:
    """True if ai-jury's ``jury`` CLI is on PATH. keel-visual never imports ai-jury."""
    which = _which or shutil.which
    return which("jury") is not None


def _spawn_theater(argv: list[str], cwd: str | None) -> bool:
    """Run ``jury --theater`` inheriting this terminal so theater owns the screen."""
    return subprocess.run(argv, cwd=cwd, check=False).returncode == 0


def _run_theater(pr: int, *, cwd: str | None, _spawn=None) -> bool:
    """Hand the terminal to ``jury --pr <pr> --theater``. Fail-soft: any spawn error
    (jury absent, crash) is swallowed and reported as ``False`` — the follow loop
    continues. Never raises. keel does not depend on ai-jury."""
    spawn = _spawn or _spawn_theater
    try:
        return bool(spawn(["jury", "--pr", str(pr), "--theater"], cwd))
    except (OSError, subprocess.SubprocessError):
        return False


def _is_interactive(out) -> bool:
    """True only on a real tty — theater is meaningless when output is piped/captured."""
    return bool(getattr(out, "isatty", lambda: False)())


def _theater_due(state: dict, *, enabled: bool, done: bool, interactive: bool) -> bool:
    """Pure: should we hand off to theater on this tick? True only when theater is
    enabled, the output is interactive, we haven't already done it this approach, and
    the live run sits on the review step with the jury active."""
    if not (enabled and interactive) or done:
        return False
    steps = state.get("steps") or []
    idx = state.get("active_index", 0)
    cur = steps[idx] if 0 <= idx < len(steps) else {}
    jury = state.get("jury") or {}
    return cur.get("id") == runstate.REVIEW_STEP_ID and bool(jury.get("active"))


def _review_index(state: dict) -> int | None:
    """Index of the review step in this run's flow, or None if it has none."""
    for i, step in enumerate(state.get("steps") or []):
        if step.get("id") == runstate.REVIEW_STEP_ID:
            return i
    return None


def _play_follow(
    args,
    *,
    sleep,
    out,
    max_cycles: int | None,
    theater_run=_run_theater,
    theater_available=_theater_available,
) -> int:
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
    theater = getattr(args, "theater", False)
    interactive = _is_interactive(out)
    last_frame: str | None = None
    state: dict | None = None
    theater_done = False
    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            try:
                state = _state_from_config(args, config)
                last_frame = terminal.frame(
                    state, state["active_index"], style=args.style, color=color
                )
            except (ledger.LedgerError, OSError):
                state = None  # transient (bad content or path): hold last frame, keep polling
            if not args.no_clear:
                out.write(_CLEAR)
            out.write((last_frame or "keel-visual — waiting for run state…") + "\n")
            out.flush()
            if theater and state is not None:
                # re-arm the one-shot when a fresh run is still before the review step
                rvi = _review_index(state)
                if rvi is not None and state.get("active_index", 0) < rvi:
                    theater_done = False
                if _theater_due(state, enabled=theater, done=theater_done, interactive=interactive):
                    # PR identity from the same live source that triggered the
                    # handoff (checkpoint), else the ledger/--pr fallback.
                    pr = _live_pr(args, config) or state.get("pr") or getattr(args, "pr", None)
                    if pr and theater_available():
                        theater_run(int(pr), cwd=args.root)  # hands off the screen, then resumes
                    theater_done = True  # one-shot per approach, even if jury was absent
            cycle += 1
            sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        out.write("\n")
    return 0


def _discover_runs(args: argparse.Namespace, config: cfg.ProjectConfig) -> list[dict]:
    """Board rows for every active run of the single repo at ``args.root``."""
    return _runs_in_repo(args.root, config)


def _run_records_in_repo(root: str, config: cfg.ProjectConfig) -> list[tuple[dict, dict]]:
    """Every worktree of the repo at ``root`` with a live checkpoint, as
    ``(run_state, identity)`` pairs.

    Worktrees come from ``git worktree list`` (keel's own run isolation — each
    parallel ship runs in its own worktree). A worktree with no readable
    checkpoint is skipped (not an active run). All per-worktree reads are
    fail-soft so one bad run never blanks the board.
    """
    result = git.worktree_list(cwd=root)
    paths = dash.parse_worktrees(result.output) if result.ok else [root]
    pairs: list[tuple[dict, dict]] = []
    checkpoint_run_ids: set[str] = set()
    for worktree in paths:
        try:
            checkpoint_path = checkpoint.resolve_path(worktree, config)
            record = checkpoint.read_checkpoint(checkpoint_path)
        except (checkpoint.CheckpointError, OSError):
            # One malformed worktree (bad content or bad path) must never blank
            # the whole board — skip it.
            record = None
        if record is None:
            continue
        run_id = record.get("run_id")
        if isinstance(run_id, str) and run_id:
            checkpoint_run_ids.add(run_id)
        identity = dash.identity_from_checkpoint(record)
        checkpoint_age = _record_age_seconds(checkpoint_path)
        ship_record = _latest_ship_record(worktree, config, identity.get("pr"))
        run_state = runstate.build_run_state(
            ship_record,
            checkpoint_step=runstate.current_step_from_checkpoint(record),
            checkpoint_state=runstate.live_state_from_checkpoint(record),
            command=identity.get("command") or "ship",
            # The jury outcome is saved under the run's own worktree root —
            # the same place its checkpoint lives.
            jury_verdict=_load_jury_verdict(worktree, record.get("run_id")),
        )
        run_state["record_age_seconds"] = checkpoint_age
        pairs.append((run_state, identity))
    # Activity records live per worktree too — an agent runs a command in its own
    # worktree and stamps .keel/activity/ there, not in the main checkout — so read
    # every worktree, exactly like checkpoints above. ship stamps activity too now
    # (for reliable board presence), so drop any activity record whose run already
    # appears as a richer ship checkpoint — same run, keyed by the shared run-id.
    for worktree in paths:
        for run_state, identity in _activity_records_in_repo(worktree, config):
            run_id = identity.get("run_id")
            if isinstance(run_id, str) and run_id in checkpoint_run_ids:
                continue
            pairs.append((run_state, identity))
    return pairs


def _activity_records_in_repo(root: str, config: cfg.ProjectConfig) -> list[tuple[dict, dict]]:
    """Non-ship command runs at ``root`` from the additive activity channel.

    Commands that don't write a ship checkpoint (triage, morning, pr-loop …)
    stamp ``.keel/activity/<run-id>.json`` as they move through their own
    :mod:`keel.flows` phases. Each record becomes a ``(run_state, identity)``
    pair projected onto its command flow. A ``done`` record is marked merged so
    the board fades / filters it like any finished run. Fail-soft (and a no-op
    against a core too old to ship ``keel.activity``)."""
    if activity is None:
        return []
    try:
        activity_dir = activity.resolve_dir(root, config)
        records = activity.read_all_activity(activity_dir)
    except activity.ActivityError:
        return []
    pairs: list[tuple[dict, dict]] = []
    for rec in records:
        run_state = runstate.build_run_state(
            None,
            checkpoint_step=rec.get("phase"),
            command=rec.get("command") or "ship",
            # An explicit "blocked" only. A missing verdict (an older record, or a
            # phase with nothing to pass) must not read as either outcome.
            blocked=rec.get("verdict") == "blocked",
        )
        status = rec.get("status")
        if status == "merged":
            # A real merge landed (stamped by `keel merge`). Distinct from "done":
            # this run actually merged, so it reads as a confirmed green "merged".
            run_state["merged"] = True
        elif status == "done":
            # "done" means the command finished/closed out — NOT that anything
            # merged. Most activity commands (morning, triage, …) never merge, and
            # a ship that defers its merge to the next window also closes out as
            # done. Mark it finished (fades/filters like a completed run) without
            # claiming a merge it can't prove.
            run_state["done"] = True
        run_state["record_age_seconds"] = _activity_record_age(activity_dir, rec.get("run_id"))
        identity = {
            "issue": rec.get("issue"),
            "pr": rec.get("pr"),
            "command": rec.get("command"),
            "run_id": rec.get("run_id"),
        }
        pairs.append((run_state, identity))
    return pairs


def _runs_in_repo(root: str, config: cfg.ProjectConfig) -> list[dict]:
    """Terminal board rows for every active run of the repo at ``root``."""
    return [dash.board_row(rs, identity) for rs, identity in _run_records_in_repo(root, config)]


def _latest_ship_record(worktree: str, config: cfg.ProjectConfig, pr: int | None) -> dict | None:
    """Best-effort: the worktree's latest ship_run record for ``pr`` (None if absent)."""
    try:
        records = ledger.read_records(ledger.resolve_path(worktree, config))
    except (ledger.LedgerError, OSError):
        return None
    if pr is not None:
        return ledger.latest_ship_run_for_pr(records, pr)
    return None


def _discover_projects(parent: str) -> list[tuple[str, str]]:
    """Immediate subdirectories of ``parent`` that are keel projects — a dir with
    **both** ``.git`` and ``.keel/project.yaml``. Returns ``(name, path)`` sorted by
    name. Fail-soft: an unreadable parent yields ``[]`` (one level deep only — no
    recursion into the projects themselves)."""
    try:
        entries = sorted(p for p in Path(parent).iterdir() if p.is_dir())
    except OSError:
        return []
    projects: list[tuple[str, str]] = []
    for directory in entries:
        if (directory / ".git").exists() and (directory / ".keel" / "project.yaml").is_file():
            projects.append((directory.name, str(directory)))
    return projects


def _each_project(parent: str):
    """Yield ``(name, project_dir, config)`` for each **loadable** keel project under
    ``parent``. Each project loads its **own** config; a project whose config is
    missing/malformed (bad schema, bad YAML, unreadable) is skipped so one bad
    project never blanks the board. Fail-soft."""
    for name, project_dir in _discover_projects(parent):
        try:
            config = cfg.load_config(str(Path(project_dir) / ".keel" / "project.yaml"))
        except Exception:  # noqa: BLE001
            continue
        yield name, project_dir, config


def _aggregate_runs(parent: str) -> list[dict]:
    """Terminal board rows across every keel project under ``parent``, each tagged
    with its ``project`` name. Fail-soft."""
    rows: list[dict] = []
    for name, project_dir, config in _each_project(parent):
        for row in _runs_in_repo(project_dir, config):
            row["project"] = name
            rows.append(row)
    return rows


_TITLE_CACHE: dict[tuple[str, int], str | None] = {}


#: A not-done record untouched for longer than this renders as **stale**, never
#: as "running". Sessions stamp their record every phase, so hours of silence
#: means the session is gone, not thinking. Chosen well above the longest
#: observed legitimate phase gap; the misread it prevents ran to 63 days.
STALE_AFTER_SECONDS = 6 * 3600


def _activity_record_age(activity_dir, run_id) -> float | None:
    """Age of an activity record, located the way the writer stored it.

    The writer saves every record under ``run_id_slug(run_id) + ".json"`` while
    the record *content* keeps the raw id — reading the raw name silently
    reintroduced the #823 misread for any id that is not already a valid slug.
    """
    try:
        name = activity.run_id_slug(run_id) + ".json"
    except activity.ActivityError:  # pragma: no cover - validate upstream
        return None
    return _record_age_seconds(Path(activity_dir) / name)


def _record_age_seconds(path) -> float | None:
    """Age of a record file in seconds, ``None`` when unreadable. The records
    themselves carry no timestamp field; the file's mtime is the honest
    last-seen signal — every stamp rewrites the file."""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def _fetch_title(kind: str, number: int, *, root: str, _run=subprocess.run) -> str | None:
    """Best-effort GitHub title for an issue/PR via ``gh``, cached for the process.

    Fail-soft: missing ``gh`` / no auth / offline / API error → ``None`` (the card
    shows its number only, never blanks). keel-visual takes **no hard dependency** on
    ``gh`` — the call is optional and guarded, exactly like its ``git worktree list``
    use. Cached by ``(kind, number)`` so ``serve``'s ~0.5s polling never re-hits the
    API for a run it has already resolved. The title is fetched **live**, so a renamed
    issue/PR is reflected on the next process start — nothing is stamped into a record."""
    key = (kind, number)
    if key in _TITLE_CACHE:
        return _TITLE_CACHE[key]
    result = runner.run_argv(
        ["gh", kind, "view", str(number), "--json", "title", "--jq", ".title"], cwd=root, _run=_run
    )
    title = result.output.strip() if result.ok else ""
    _TITLE_CACHE[key] = title or None
    return _TITLE_CACHE[key]


def _enrich_title(entry: dict, *, root: str, _run=subprocess.run) -> None:
    """Attach the live GitHub ``title`` to a board entry — issue title preferred, else PR."""
    if entry.get("issue"):
        entry["title"] = _fetch_title("issue", entry["issue"], root=root, _run=_run)
    elif entry.get("pr"):
        entry["title"] = _fetch_title("pr", entry["pr"], root=root, _run=_run)


def _board_entry(run_state: dict, identity: dict, project: str) -> dict:
    """A slim per-run entry the **web** board renders: project + label + the step
    strip + status/jury, plus branch/base/author (from the ledger record) and an
    optional live ``title``. Project/branch/author are sanitised before the page."""
    row = dash.board_row(run_state, identity)
    return {
        "project": dash._safe_label(project),
        "label": row["label"],
        "issue": row["issue"],
        "pr": row["pr"],
        "branch": dash._safe_label(row["branch"]) if row["branch"] else None,
        "base": dash._safe_label(row["base"]) if row["base"] else None,
        "author": dash._safe_label(row["author"]) if row["author"] else None,
        "title": None,
        "status": row["status"],
        "active_index": row["active_index"],
        "active_id": row["active_id"],
        "active_name": row["active_name"],
        "command": run_state.get("command", "ship"),
        "merged": bool(run_state.get("merged")),
        "done": bool(run_state.get("done")),
        # A finished run can be as old as it likes; only an unfinished record
        # that stopped being stamped goes stale.
        "stale": (
            not run_state.get("done")
            and not run_state.get("merged")
            and run_state.get("record_age_seconds") is not None
            and run_state["record_age_seconds"] > STALE_AFTER_SECONDS
        ),
        "age_hours": (
            round(run_state["record_age_seconds"] / 3600, 1)
            if run_state.get("record_age_seconds") is not None
            else None
        ),
        "jury": run_state.get("jury") or {"mode": None, "active": False},
        # Verdict summary from the run's saved ai-jury outcome (#576). Already
        # validated by runstate._jury_verdict_block — literals + int counts only.
        "jury_verdict": run_state.get("jury_verdict"),
        # Ledger-detail fields the drawer renders (#575). Free-text values are
        # sanitised like branch/author per this function's contract.
        "tier": run_state.get("tier"),
        "window_open": run_state.get("window_open"),
        "bypassed_window": run_state.get("bypassed_window"),
        "merge_reason": (
            dash._safe_label(run_state["merge_reason"]) if run_state.get("merge_reason") else None
        ),
        "file_count": run_state.get("file_count"),
        "reviewers": [dash._safe_label(r) for r in (run_state.get("reviewers") or [])],
        "tester": dash._safe_label(run_state["tester"]) if run_state.get("tester") else None,
        "host_agent": (
            dash._safe_label(run_state["host_agent"]) if run_state.get("host_agent") else None
        ),
        "gates": [
            {
                "name": dash._safe_label(g.get("name") or ""),
                "ok": g.get("ok"),
                "skipped": g.get("skipped"),
                "error": dash._safe_label(g["error"]) if g.get("error") else None,
                "finding_count": g.get("finding_count"),
            }
            for g in (run_state.get("gates") or [])
        ],
        "steps": [
            {"id": s.get("id"), "name": s.get("name"), "kind": s.get("kind"), "gate": s.get("gate")}
            for s in (run_state.get("steps") or [])
        ],
    }


def _aggregate_board(parent: str, *, _run=None) -> list[dict]:
    """Web board entries across every keel project under ``parent``. Same discovery +
    per-project config as :func:`_aggregate_runs`. Fail-soft.

    When ``_run`` is supplied (the live ``serve`` / ``render`` paths pass
    ``subprocess.run``), each entry is enriched with its live ``gh`` title, fetched in
    the entry's own project dir. Left ``None`` (tests, pure callers) → no ``gh`` calls."""
    board: list[dict] = []
    for name, project_dir, config in _each_project(parent):
        for run_state, identity in _run_records_in_repo(project_dir, config):
            entry = _board_entry(run_state, identity, name)
            if _run is not None:
                _enrich_title(entry, root=project_dir, _run=_run)
            board.append(entry)
    return board


def _single_repo_board(root: str, config: cfg.ProjectConfig, *, _run=None) -> list[dict]:
    """Web board entries for one repo (the non-``--all`` serve path). Fail-soft.

    ``_run`` enables live ``gh`` title enrichment exactly as in :func:`_aggregate_board`."""
    project = config.repo or Path(root).resolve().name
    board: list[dict] = []
    for run_state, identity in _run_records_in_repo(root, config):
        entry = _board_entry(run_state, identity, project)
        if _run is not None:
            _enrich_title(entry, root=root, _run=_run)
        board.append(entry)
    return board


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the **live** dashboard: the page polls ``/board.json``, which re-reads
    the records on every request. Localhost-only."""
    if getattr(args, "all", False):

        def provider() -> list[dict]:
            return _aggregate_board(args.root, _run=subprocess.run)
    else:
        if not args.path:
            print("serve: provide a path to project.yaml, or use --all", file=sys.stderr)
            return 1
        try:
            config = cfg.load_config(args.path)
        except FileNotFoundError:
            print(f"no such config: {args.path}", file=sys.stderr)
            return 1
        except cfg.ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        def provider() -> list[dict]:
            return _single_repo_board(args.root, config, _run=subprocess.run)

    page = load_dashboard_template()
    httpd = serve.make_server(provider, page, host=args.host, port=args.port)
    host, port = httpd.server_address[0], httpd.server_address[1]
    print(f"keel-visual — live dashboard on http://{host}:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nkeel-visual — dashboard stopped")
    finally:
        httpd.server_close()
    return 0


def _dash_all(args, *, sleep, out, color: bool, max_cycles: int | None) -> int:
    """Live board aggregating every keel project under ``--root`` (the ``--all`` path)."""
    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            rows = _aggregate_runs(args.root)
            if not args.no_clear:
                out.write(_CLEAR)
            out.write(dash.render_project_board(rows, color=color) + "\n")
            out.flush()
            cycle += 1
            if args.once:
                break
            sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        out.write("\n")
    return 0


def cmd_dash(
    args: argparse.Namespace,
    *,
    sleep=time.sleep,
    out=sys.stdout,
    max_cycles: int | None = None,
) -> int:
    """Live board of every active run across the project's worktrees.

    ``--all`` widens the board to **every keel project under ``--root``** (each with
    its own config), instead of a single project.yaml.
    """
    color = _resolve_color(args, out)
    if getattr(args, "all", False):
        return _dash_all(args, sleep=sleep, out=out, color=color, max_cycles=max_cycles)
    if not args.path:
        print("dash: provide a path to project.yaml, or use --all", file=sys.stderr)
        return 1
    config = _resolve_config(args)
    if isinstance(config, tuple):
        print(config[1], file=sys.stderr)
        return config[0]
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
    p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path to project.yaml (single run; omit when using --all)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="render one web board across every keel project under --root "
        "(a subdir with both .git and .keel/project.yaml)",
    )
    p.add_argument("--root", default=".", help="repo root (or parent folder, with --all)")
    p.add_argument("--pr", type=int, default=None, help="PR number (default: latest ship_run)")
    p.add_argument("--ledger-jsonl", default=None, help="offline run-ledger JSONL fixture")
    p.add_argument("--checkpoint-step", default=None, help="current step id (e.g. s8)")
    p.add_argument(
        "--command",
        default="ship",
        choices=flows.command_names(),
        help="which command's steps to highlight",
    )
    p.add_argument("--out", default="keel-run.html", help="output HTML path")
    p.set_defaults(func=cmd_render)

    pl = sub.add_parser("play", help="animate the run in the terminal (2D flow / wave ribbon)")
    pl.add_argument("path", help="path to project.yaml")
    pl.add_argument("--root", default=".", help="repo root for the ledger path")
    pl.add_argument("--pr", type=int, default=None, help="PR number (default: latest ship_run)")
    pl.add_argument("--ledger-jsonl", default=None, help="offline run-ledger JSONL fixture")
    pl.add_argument("--checkpoint-step", default=None, help="current step id (e.g. s8)")
    pl.add_argument(
        "--command",
        default="ship",
        choices=flows.command_names(),
        help="which command's steps to play",
    )
    pl.add_argument("--style", default="flow", choices=("flow", "wave"), help="render style")
    pl.add_argument("--fps", type=int, default=2, help="frames per second during playback")
    pl.add_argument("--step", type=int, default=None, help="render a single step frame and exit")
    pl.add_argument("--once", action="store_true", help="render current frame once (no animation)")
    pl.add_argument("--loop", action="store_true", help="replay continuously (Ctrl-C to stop)")
    pl.add_argument(
        "--follow",
        action="store_true",
        help="live: re-read ledger + checkpoint each interval, show where the run is",
    )
    pl.add_argument("--interval", type=float, default=1.0, help="--follow poll interval in seconds")
    pl.add_argument(
        "--theater",
        action="store_true",
        help="with --follow on a tty: hand off to ai-jury's `jury --theater` "
        "at the review step when the jury is active, then resume "
        "(silently skipped if the jury CLI is absent)",
    )
    pl.add_argument("--no-clear", action="store_true", help="keep frames (no screen clear)")
    pl.add_argument(
        "--color",
        default="auto",
        choices=("auto", "always", "never"),
        help="ANSI colour (auto = only on a tty)",
    )
    pl.set_defaults(func=cmd_play)

    pd = sub.add_parser("dash", help="live board of all active runs across the project's worktrees")
    pd.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path to project.yaml (single repo; omit when using --all)",
    )
    pd.add_argument(
        "--all",
        action="store_true",
        help="aggregate every keel project under --root into one board "
        "(a subdir with both .git and .keel/project.yaml)",
    )
    pd.add_argument("--root", default=".", help="repo root (or parent folder, with --all)")
    pd.add_argument("--interval", type=float, default=2.0, help="refresh interval in seconds")
    pd.add_argument("--once", action="store_true", help="render the board once and exit")
    pd.add_argument("--no-clear", action="store_true", help="keep frames (no screen clear)")
    pd.add_argument(
        "--color",
        default="auto",
        choices=("auto", "always", "never"),
        help="ANSI colour (auto = only on a tty)",
    )
    pd.set_defaults(func=cmd_dash)

    ps = sub.add_parser("serve", help="serve the live web dashboard (auto-refreshing, localhost)")
    ps.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path to project.yaml (single repo; omit when using --all)",
    )
    ps.add_argument(
        "--all",
        action="store_true",
        help="aggregate every keel project under --root into one board",
    )
    ps.add_argument("--root", default=".", help="repo root (or parent folder, with --all)")
    ps.add_argument("--host", default="127.0.0.1", help="bind host (localhost-only by default)")
    ps.add_argument("--port", type=int, default=8765, help="bind port")
    ps.set_defaults(func=cmd_serve)

    p_swarm = sub.add_parser(
        "swarm", help="render multi-wave swarm topology and 2D DAG partition graph"
    )
    p_swarm.add_argument("path", nargs="?", default=None, help="path to project.yaml")
    p_swarm.add_argument("--root", default=".", help="repo root for state directory")
    p_swarm.add_argument("--swarm-id", default=None, help="swarm execution ID (default: latest)")
    p_swarm.add_argument("--swarm-json", default=None, help="offline swarm run state JSON fixture")
    p_swarm.add_argument("--out", default="keel-swarm.html", help="output HTML path")
    p_swarm.add_argument(
        "--serve", action="store_true", help="serve the swarm visualizer on localhost"
    )
    p_swarm.add_argument("--host", default="127.0.0.1", help="bind host for server")
    p_swarm.add_argument("--port", type=int, default=8766, help="bind port for server")
    p_swarm.add_argument("--json", action="store_true", help="emit structured JSON to stdout")
    p_swarm.set_defaults(func=cmd_swarm)
    return parser


def _resolve_swarm_data(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve swarm plan and state dictionaries from state files or fixture."""
    fixture = getattr(args, "swarm_json", None)
    if fixture is not None:
        return json.loads(Path(fixture).read_text(encoding="utf-8"))

    root_path = Path(args.root).resolve()
    state_dir = root_path / ".keel" / "state" / "swarm"

    swarm_id = getattr(args, "swarm_id", None)
    selected_file: Path | None = None

    if state_dir.exists():
        if swarm_id:
            candidate = state_dir / f"{swarm_id}.json"
            if candidate.exists():
                selected_file = candidate
        else:
            files = sorted(
                state_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                selected_file = files[0]
                swarm_id = selected_file.stem

    state_dict: dict[str, Any] = {}
    if selected_file and selected_file.exists():
        try:
            state_dict = json.loads(selected_file.read_text(encoding="utf-8"))
        except Exception:
            state_dict = {}

    from keel import swarm as core_swarm

    scopes: list[core_swarm.IssueScope] = []
    if "workers" in state_dict:
        for w in state_dict["workers"]:
            scopes.append(
                core_swarm.IssueScope(
                    issue=w.get("issue", 1),
                    title=f"Worker {w.get('issue', 1)}",
                    role=w.get("role", "core"),
                )
            )

    plan = core_swarm.build_swarm_plan(scopes, swarm_id=swarm_id or "swarm-empty")

    return {
        "swarm_id": swarm_id or "swarm-empty",
        "plan": plan.to_dict(),
        "state": state_dict,
    }


def cmd_swarm(args: argparse.Namespace) -> int:
    """Render multi-wave swarm topology and 2D DAG partition graph."""
    if args.path:
        try:
            cfg.load_config(args.path)
        except FileNotFoundError:
            print(f"no such config: {args.path}", file=sys.stderr)
            return 1
        except cfg.ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    swarm_data = _resolve_swarm_data(args)

    if getattr(args, "json", False):
        print(json.dumps(swarm_data, indent=2))
        return 0

    tpl = load_swarm_template()
    title = f"keel swarm · {swarm_data.get('swarm_id', 'live')}"
    html = render.render_swarm_html(tpl, swarm_data, title=title)

    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"keel-visual — wrote {out_path.resolve()}  ({swarm_data.get('swarm_id')})")

    if getattr(args, "serve", False):

        def provider() -> dict[str, Any]:
            return _resolve_swarm_data(args)

        httpd = serve.make_server(provider, html, host=args.host, port=args.port)
        host, port = httpd.server_address[0], httpd.server_address[1]
        print(f"keel-visual — live swarm topology on http://{host}:{port}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nkeel-visual — server stopped")
        finally:
            httpd.server_close()

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
