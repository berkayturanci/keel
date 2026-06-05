"""Merge-window logic — the night no-merge invariant, as a pure function.

A project's ``merge_window`` (e.g. ``07:00-01:30``) is the **open** window; its
complement is the night no-merge window. The window may wrap midnight. This module
answers "is the merge window open at time T in the project's timezone?" with no I/O,
so it is fully unit-testable with a fixed clock.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


def parse_window(window: str) -> tuple[time, time]:
    """Parse ``HH:MM-HH:MM`` into ``(open, close)`` times."""
    open_s, _, close_s = window.partition("-")
    return _parse_time(open_s), _parse_time(close_s)


def _parse_time(s: str) -> time:
    hh, _, mm = s.strip().partition(":")
    return time(int(hh), int(mm))


def is_merge_open(timezone: str, merge_window: str, *, now: datetime | None = None) -> bool:
    """True if the merge window is open at ``now`` (default: real time) in ``timezone``."""
    tz = ZoneInfo(timezone)
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    current = now.astimezone(tz).time()

    opens, closes = parse_window(merge_window)
    if opens <= closes:
        return opens <= current < closes
    # window wraps midnight (e.g. 07:00-01:30): open late or early
    return current >= opens or current < closes
