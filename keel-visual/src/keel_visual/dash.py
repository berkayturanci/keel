"""Pure multi-run dashboard: one compact board line per active keel run.

When you run 2-3 ``keel ship`` (or other) commands in parallel, each runs in its
own git worktree with its own ``.keel/state/`` (checkpoint + ledger). This module
turns a set of those per-run states into a single board so you can see, at a
glance, where every run is.

It is pure: the CLI discovers the worktrees (``git worktree list``) and reads
each one's checkpoint/ledger; this module parses the worktree listing, extracts a
run's identity from its checkpoint, and renders the board. No I/O here.
"""

from __future__ import annotations

from typing import Any

from . import terminal

# Per-run status -> (label, colour) shown on the board.
_TONE = {
    "merged": ("merged", "green"),
    "blocked": ("blocked", "red"),
    "gate": ("gate", "amber"),
    "running": ("running", "cyan"),
}


def parse_worktrees(porcelain: str) -> list[str]:
    """Return the worktree paths from ``git worktree list --porcelain`` output.

    Each worktree block starts with a ``worktree <path>`` line. Pure — reads only
    its argument; order is preserved and duplicates are not expected from git.
    """
    paths: list[str] = []
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
            if path:
                paths.append(path)
    return paths


def identity_from_checkpoint(record: dict[str, Any] | None) -> dict[str, Any]:
    """Extract a run's display identity (issue, pr, command) from its checkpoint.

    A run in progress has no ledger record yet, but its checkpoint carries the
    issue (``queue.active_issue``), PR (``identifiers.pull_request``) and command.
    Returns ``{}``-filled fields for a missing/malformed record. Pure.
    """
    rec = record if isinstance(record, dict) else {}
    queue = rec.get("queue") if isinstance(rec.get("queue"), dict) else {}
    ident = rec.get("identifiers") if isinstance(rec.get("identifiers"), dict) else {}
    command = rec.get("command")
    return {
        "issue": _int_or_none(queue.get("active_issue")),
        "pr": _int_or_none(ident.get("pull_request")),
        "command": command if isinstance(command, str) and command.strip() else None,
    }


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _status_of(run_state: dict[str, Any]) -> str:
    """Reduce a run-state to a single board status key."""
    if run_state.get("merged"):
        return "merged"
    active = run_state.get("active_index", 0)
    steps = run_state.get("steps") or []
    if 0 <= active < len(steps):
        gate = steps[active].get("gate")
        if isinstance(gate, dict):
            return "blocked" if gate.get("outcome") == "fail" else "gate"
    return "running"


def board_row(run_state: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    """Build the display row for one run from its run-state + checkpoint identity."""
    steps = run_state.get("steps") or []
    total = len(steps) or 1
    active = run_state.get("active_index", 0)
    step = steps[active] if 0 <= active < len(steps) else {"id": "s?", "name": "?"}
    pr = run_state.get("pr") or identity.get("pr")
    issue = run_state.get("issue") or identity.get("issue")
    return {
        "label": f"#{pr}" if pr else (f"#{issue}" if issue else "?"),
        "active_index": active,
        "total": total,
        "active_id": step.get("id", "s?"),
        "active_name": step.get("name", "?"),
        "status": _status_of(run_state),
    }


def _row_cells(r: dict[str, Any], *, color: bool, width: int) -> tuple[str, str, str]:
    """Shared per-run cells: the progress ``bar``, ``step`` text, and ``status``. Pure."""
    frac = r["active_index"] / (r["total"] - 1) if r["total"] > 1 else 1.0
    tone_label, tone_color = _TONE.get(r["status"], _TONE["running"])
    filled = max(0, min(width, round(frac * width)))
    bar = terminal.paint("█" * filled, tone_color, enable=color) + terminal.paint(
        "░" * (width - filled), "dim", enable=color
    )
    step = f"{r['active_id']:<3} {r['active_name']}"
    status = terminal.paint(tone_label, tone_color, enable=color)
    return bar, step, status


def render_board(rows: list[dict[str, Any]], *, color: bool = True, width: int = 12) -> str:
    """Render the multi-run board as a string (one line per run). Pure."""
    n = len(rows)
    head = terminal.paint(f"keel · {n} active run{'' if n == 1 else 's'}", "white", enable=color)
    if not rows:
        return head + "\n  " + terminal.paint("no active runs found", "dim", enable=color)
    label_w = max(len(r["label"]) for r in rows)
    lines = [head]
    for r in rows:
        bar, step, status = _row_cells(r, color=color, width=width)
        label = terminal.paint(r["label"].ljust(label_w), "white", enable=color)
        lines.append(f"  {label}  {bar}  {step:<16}  {status}")
    return "\n".join(lines)


def _safe_label(text: str) -> str:
    """Drop non-printable characters so an attacker-named directory can't inject
    terminal control/ANSI sequences into the board (``dash --all`` scans a parent
    folder whose subdir names you may not fully control). Pure."""
    return "".join(c for c in str(text) if c.isprintable())


def render_project_board(rows: list[dict[str, Any]], *, color: bool = True, width: int = 12) -> str:
    """Render runs across multiple keel projects, each line prefixed by its project. Pure.

    Each row carries a ``project`` label (see the multi-repo ``dash --all`` path).
    Header counts both the runs and the distinct projects. Project names are
    sanitised (they come from directory names) before they reach the terminal.
    """
    nruns = len(rows)
    projects = [_safe_label(r.get("project", "")) for r in rows]
    nproj = len(set(projects))
    head = terminal.paint(
        f"keel · {nruns} run{'' if nruns == 1 else 's'} across "
        f"{nproj} project{'' if nproj == 1 else 's'}",
        "white", enable=color,
    )
    if not rows:
        return head + "\n  " + terminal.paint("no active runs found", "dim", enable=color)
    proj_w = max(len(p) for p in projects)
    label_w = max(len(r["label"]) for r in rows)
    lines = [head]
    for r, pname in zip(rows, projects, strict=True):
        bar, step, status = _row_cells(r, color=color, width=width)
        proj = terminal.paint(pname.ljust(proj_w), "white", enable=color)
        label = terminal.paint(r["label"].ljust(label_w), "dim", enable=color)
        lines.append(f"  {proj}  {label}  {bar}  {step:<16}  {status}")
    return "\n".join(lines)
