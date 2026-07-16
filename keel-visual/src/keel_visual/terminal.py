"""Pure terminal renderer: draw a keel run as animatable ANSI frames.

The 2D/3D *flow* the user wants while a command runs, rendered right in the CLI —
no browser, no separate screen. This module is pure: ``(run_state, active) ->
frame string``. It holds no clock; the CLI loop advances ``active`` over time and
prints each frame. Two styles:

* ``flow`` — a single-line pipeline of step nodes with a playhead, gate colours,
  a regression bar, and a "where are we" pointer.
* ``wave`` — the same run drawn on a sine ribbon across several rows, with a
  light trail up to the active step (the closest a terminal gets to the 3D
  ribbon).

Colour is opt-out (``color=False``) so tests assert on plain glyphs and the CLI
can degrade for non-tty output. Pure — no I/O, clock, or random.
"""

from __future__ import annotations

import math
from typing import Any

from .runstate import REVIEW_STEP_ID

RESET = "\x1b[0m"
_CODES = {
    "dim": "\x1b[38;5;245m",
    "cyan": "\x1b[38;5;45m",
    "green": "\x1b[38;5;113m",
    "amber": "\x1b[38;5;214m",
    "yellow": "\x1b[38;5;221m",
    "red": "\x1b[38;5;203m",
    "white": "\x1b[38;5;231m",
    "bold": "\x1b[1m",
}

# Status / worst -> colour name.
_STATUS_COLOR = {
    "done": "green", "active": "cyan", "gate": "amber",
    "loop": "amber", "pending": "dim",
}
_WORST_COLOR = {"none": "green", "minor": "yellow", "major": "amber", "critical": "red"}

# Glyph per display status (filled = reached, hollow = pending).
_GLYPH = {"done": "●", "active": "◉", "gate": "◆", "loop": "↻", "pending": "○"}


def paint(text: str, color: str | None, *, enable: bool) -> str:
    """Wrap ``text`` in the ANSI code for ``color`` (no-op when disabled/unknown)."""
    if not enable or not color:
        return text
    code = _CODES.get(color)
    return f"{code}{text}{RESET}" if code else text


def display_status(step: dict[str, Any], idx: int, active: int) -> str:
    """Resolve the *display* status of ``step`` for a given playhead ``active``.

    Independent of the run's recorded position so the CLI can scrub/animate: a
    step before the playhead is ``done``, after it is ``pending``, and the
    playhead step keeps its intrinsic gate/loop/active status.
    """
    if idx < active:
        return "done"
    if idx > active:
        return "pending"
    kind = step.get("kind")
    if kind in ("gate", "merge"):
        return "gate"
    if kind == "loop":
        return "loop"
    return "active"


def _gate_word(step: dict[str, Any]) -> str:
    gate = step.get("gate")
    if not isinstance(gate, dict):
        return ""
    outcome = gate.get("outcome")
    if outcome == "fail":
        return "blocked"
    if outcome == "pass":
        return "passed"
    return "pending"


def _bar(fraction: float, width: int, color: str, *, enable: bool) -> str:
    filled = max(0, min(width, round(fraction * width)))
    head = paint("█" * filled, color, enable=enable)
    tail = paint("░" * (width - filled), "dim", enable=enable)
    return head + tail


def _header(run: dict[str, Any], *, enable: bool) -> str:
    bits = ["keel-visual"]
    if run.get("command"):
        bits.append(str(run["command"]))
    if run.get("issue"):
        bits.append(f"issue #{run['issue']}")
    if run.get("pr"):
        bits.append(f"PR #{run['pr']}")
    line = paint(" · ".join(bits), "white", enable=enable)
    if run.get("merged"):
        tag = paint("merged", "green", enable=enable)
    else:
        tag = paint("in progress", "dim", enable=enable)
    head = f"{line}   [{tag}]"
    tier = run.get("tier")
    if isinstance(tier, int):
        head += f"   [{paint(f'T{tier}', 'amber' if tier >= 3 else 'cyan', enable=enable)}]"
    window = run.get("window_open")
    if isinstance(window, bool):
        word = "win:open" if window else "win:closed"
        head += f"   [{paint(word, 'green' if window else 'dim', enable=enable)}]"
    if run.get("bypassed_window"):
        head += f"   [{paint('win:bypassed', 'amber', enable=enable)}]"
    jury = run.get("jury") or {}
    if jury.get("active"):
        head += f"   [{paint('jury', 'cyan', enable=enable)}]"
    return head


# Named-gate outcome -> (glyph, colour): ✓ ran clean, ✗ failed, – skipped.
_GATE_GLYPHS = {"ok": ("✓", "green"), "fail": ("✗", "red"), "skip": ("–", "dim")}


def _gate_strip(gates: list[dict[str, Any]], *, enable: bool) -> str:
    """Render the ledger's named gates as one compact strip line.

    e.g. ``gates: build✓ lint✓ evidence✗ jury–`` — the per-gate detail the flow
    line's generic gate phases cannot show. Pure — reads only its arguments.
    """
    bits = []
    for gate in gates:
        key = "skip" if gate.get("skipped") else ("ok" if gate.get("ok") else "fail")
        glyph, colr = _GATE_GLYPHS[key]
        bits.append(f"{gate.get('name', '?')}{paint(glyph, colr, enable=enable)}")
    return f"gates: {' '.join(bits)}"


def frame(run: dict[str, Any], active: int, *, style: str = "flow", color: bool = True) -> str:
    """Render one frame of the run at playhead ``active`` as a string.

    ``style`` is ``flow`` (linear pipeline) or ``wave`` (sine ribbon). Pure —
    reads only its arguments.
    """
    steps = run.get("steps") or []
    n = len(steps)
    active = max(0, min(active, n - 1)) if n else 0
    if style == "wave":
        body = _wave_body(steps, active, color=color)
    else:
        body = _flow_body(steps, active, run=run, color=color)
    return _header(run, enable=color) + "\n\n" + body


def _flow_body(
    steps: list[dict[str, Any]], active: int, *, run: dict[str, Any], color: bool
) -> str:
    merged_now = bool(run.get("merged")) and active >= 10
    # Cell width adapts to the longest phase id so word-length ids (config,
    # preflight, …) align under their nodes instead of colliding.
    cell_w = max(4, max((len(step.get("id", "")) for step in steps), default=4) + 1)
    cells, labels = [], []
    for idx, step in enumerate(steps):
        status = display_status(step, idx, active)
        if merged_now and idx <= active:
            status = "done"
        glyph = _GLYPH.get(status, "○")
        cglyph = _STATUS_COLOR.get(status, "dim")
        if status == "gate" and _gate_word(step) == "blocked":
            cglyph = "red"
        cells.append(paint(glyph, cglyph, enable=color))
        lab = step.get("id", "")
        labels.append(paint(lab.ljust(cell_w), "white" if idx == active else "dim", enable=color))
    connector = paint("─" * (cell_w - 1), "dim", enable=color)
    pipeline = connector.join(cells)
    label_line = "".join(labels)

    cur = steps[active] if steps else {"id": "s0", "name": "?"}
    pointer_pad = " " * (active * cell_w)
    word = ""
    if cur.get("kind") in ("gate", "merge"):
        gw = _gate_word(cur)
        word = f"  [gate: {paint(gw, 'red' if gw=='blocked' else 'green', enable=color)}]"
        if cur.get("kind") == "merge" and merged_now:
            word = f"  [{paint('merged', 'green', enable=color)}]"
    elif cur.get("kind") == "loop":
        word = f"  [{paint('fix loop ↩', 'amber', enable=color)}]"
    jury = run.get("jury") or {}
    if cur.get("id") == REVIEW_STEP_ID and jury.get("active"):
        word += f"  [{paint('jury: ' + str(jury.get('mode', 'on')), 'cyan', enable=color)}]"
    caret = paint("▲", "cyan", enable=color)
    pointer = f"{pointer_pad}{caret} {cur.get('id')} · {cur.get('name')}{word}"

    lines = [pipeline, label_line, "", pointer]
    gates = run.get("gates") or []
    if gates:
        lines.append(_gate_strip(gates, enable=color))
    reg = run.get("regression") or {}
    if reg.get("reached") and cur.get("kind") in ("gate", "merge"):
        worst = reg.get("worst", "none")
        wcolor = _WORST_COLOR.get(worst, "green")
        cov = round(reg.get("coverage", 0))
        bar = _bar(reg.get("coverage", 0) / 100, 22, wcolor, enable=color)
        worst_txt = paint(worst, wcolor, enable=color)
        lines.append(f"regression ▕{bar}▏ {cov}%  {worst_txt}")
    if merged_now:
        lines.append(paint("◆ merged — run is green", "green", enable=color))
    return "\n".join(lines)


def _wave_body(steps: list[dict[str, Any]], active: int, *, color: bool) -> str:
    n = len(steps)
    rows = 5
    grid = [[" "] * (n * 4 + 1) for _ in range(rows)]
    for idx, step in enumerate(steps):
        x = idx * 4
        y = round((rows - 1) / 2 * (1 - math.sin(idx / max(1, n - 1) * math.pi * 1.6)))
        y = max(0, min(rows - 1, y))
        status = display_status(step, idx, active)
        glyph = _GLYPH.get(status, "○")
        cglyph = _STATUS_COLOR.get(status, "dim")
        if status == "gate" and _gate_word(step) == "blocked":
            cglyph = "red"
        grid[y][x] = paint(glyph, cglyph, enable=color)
        if idx < active:
            grid[y][min(len(grid[y]) - 1, x + 2)] = paint("·", "green", enable=color)
    lines = ["".join(row).rstrip() for row in grid]
    cur = steps[active] if steps else {"id": "s0", "name": "?"}
    lines.append("")
    lines.append(f"{paint('▶', 'cyan', enable=color)} at {cur.get('id')} · {cur.get('name')}")
    return "\n".join(lines)


def exercised_indices(run: dict[str, Any]) -> list[int]:
    """Return the indices of steps the run's command actually exercises (for playback)."""
    steps = run.get("steps") or []
    return [i for i, s in enumerate(steps) if s.get("exercised", True)]
