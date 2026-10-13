"""``keel finish`` decision logic — pure.

``keel finish`` chains steps keel already has (``review``, the CI wait, ``ship`` and
``merge``); it adds no new refusal and removes none. This module holds only the part that is
a *decision* — which steps run, what a CI snapshot means for the wait, how the timeout is
accounted — so it can be tested without a clock, a network or a subprocess. The I/O (the
polling loop, the ``gh`` calls, the sub-command invocations) is in :mod:`keel.cli`.

Two properties are deliberate:

* **A timeout is a timeout.** :func:`ci_decision` answers ``timeout`` once the elapsed time
  reaches the budget while checks are still pending; nothing here turns it into ``proceed``.
* **No bypass.** The argv builders never emit ``--hotfix``, ``--no-checkpoint-gate`` or
  ``--operator-override``; the steps run with exactly the refusals they have on their own.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SCHEMA_VERSION = "keel.finish.v1"

STEP_REVIEW = "review"
STEP_CI = "ci"
STEP_WINDOW = "window"
STEP_SHIP = "ship"
STEP_MERGE = "merge"

ACTION_PROCEED = "proceed"
ACTION_WAIT = "wait"
ACTION_RERUN = "rerun"
ACTION_FAILED = "failed"
ACTION_TIMEOUT = "timeout"

#: Consecutive unreadable CI snapshots tolerated before the wait gives up. One dropped
#: request during a thirty-minute wait must not end it; a host that cannot be read at all
#: must not be waited on for the whole budget either.
MAX_READ_FAILURES = 3

_STEP_DESCRIPTIONS = {
    STEP_REVIEW: "post the supplied review verdicts (keel review --live)",
    STEP_CI: "wait for CI on the head, bounded by --ci-timeout",
    STEP_WINDOW: "stop if the merge window is closed (no bypass)",
    STEP_SHIP: "record the gates-pass ledger entry (keel ship --live --append-ledger)",
    STEP_MERGE: "merge through the evidence gate (keel merge)",
}


def plan_steps(*, reviews: bool, rerun: Sequence[str] = ()) -> list[dict[str, object]]:
    """The ordered steps of one finish, each ``{name, description}``.

    ``review`` is present only when reviews were supplied. Reviews go first so they are
    posted while CI is still running rather than after it.
    """
    names = [STEP_REVIEW] if reviews else []
    names += [STEP_CI, STEP_WINDOW, STEP_SHIP, STEP_MERGE]
    steps: list[dict[str, object]] = []
    for name in names:
        step: dict[str, object] = {"name": name, "description": _STEP_DESCRIPTIONS[name]}
        if name == STEP_CI and rerun:
            step["rerun_flaky"] = list(rerun)
        steps.append(step)
    return steps


def next_sleep(interval: float, timeout: float, elapsed: float) -> float:
    """How long to sleep before the next poll: the interval, never past the deadline."""
    return max(0.0, min(interval, timeout - elapsed))


def poll_cap(timeout: float, interval: float) -> int:
    """An upper bound on polls, independent of the clock.

    The wait loop is bounded by elapsed time, and *also* by this count, so a clock that
    does not advance (a stubbed one, a suspended machine) cannot make it spin forever.
    """
    return math.ceil(timeout / interval) + 3


def read_failures_exhausted(consecutive: int) -> bool:
    return consecutive >= MAX_READ_FAILURES


def _names(value: object) -> list[str]:
    return [str(n) for n in value] if isinstance(value, list) else []


def ci_decision(
    ci: object,
    *,
    elapsed: float,
    timeout: float,
    rerun: Sequence[str] = (),
    reran: Sequence[str] = (),
    settling: Sequence[str] = (),
) -> dict[str, object]:
    """What one CI snapshot means for the wait: ``{action, names}``.

    ``ci`` is the rollup state :func:`keel.cli._ci_rollup_state` returns. ``rerun`` names the
    checks the operator allowed to be rerun once, ``reran`` those already rerun, and
    ``settling`` those rerun on the previous poll: GitHub can keep showing the old failure
    for a moment after a rerun is requested, and that stale failure must be waited out once
    rather than read as the rerun failing.
    """
    ci = ci if isinstance(ci, dict) else {}
    state = ci.get("state")
    if state in ("pass", "no-checks"):
        # `no-checks` is passed on: whether an empty check set is acceptable is the merge
        # gate's call (docs-only), not the wait's.
        return {"action": ACTION_PROCEED, "names": []}
    if state == "fail":
        failing = _names(ci.get("failing_checks"))
        real = [n for n in failing if n not in settling]
        if not real:
            return _wait_or_timeout(failing, elapsed, timeout)
        allowed = [n for n in real if n in rerun and n not in reran]
        if len(allowed) == len(real):
            return {"action": ACTION_RERUN, "names": allowed}
        return {"action": ACTION_FAILED, "names": real}
    return _wait_or_timeout(_names(ci.get("pending_checks")), elapsed, timeout)


def _wait_or_timeout(names: list[str], elapsed: float, timeout: float) -> dict[str, object]:
    if elapsed >= timeout:
        return {"action": ACTION_TIMEOUT, "names": names}
    return {"action": ACTION_WAIT, "names": names}


def consent_argv(
    approve_scope: Sequence[str], operator: str | None, consent_mode: str | None
) -> list[str]:
    """The consent flags every step is given, so one approval covers the whole finish."""
    argv: list[str] = []
    for scope in approve_scope:
        argv += ["--approve-scope", scope]
    if operator:
        argv += ["--operator", operator]
    if consent_mode:
        argv += ["--consent-mode", consent_mode]
    return argv


def review_argv(
    *,
    path: str,
    root: str,
    pr: int,
    issue: int | None,
    reviews: str,
    closure: str | None,
    consent: Sequence[str],
) -> list[str]:
    argv = ["review", path, "--root", root, "--pr", str(pr), "--reviews", reviews, "--live"]
    if issue is not None:
        argv += ["--issue", str(issue)]
    if closure:
        argv += ["--closure", closure]
    return argv + list(consent)


def ship_argv(
    *,
    path: str,
    root: str,
    pr: int,
    issue: int,
    run_id: str | None,
    capture_status: str | None,
    capture_reason: str | None,
    capture_artifact: str | None,
    implementer: str | None,
    consent: Sequence[str],
) -> list[str]:
    argv = ["ship", path, "--root", root, "--pr", str(pr), "--issue", str(issue)]
    argv += ["--live", "--append-ledger"]
    for flag, value in (
        ("--run-id", run_id),
        ("--capture-status", capture_status),
        ("--capture-reason", capture_reason),
        ("--capture-artifact", capture_artifact),
        ("--implementer", implementer),
    ):
        if value:
            argv += [flag, value]
    return argv + list(consent)


def merge_argv(
    *,
    path: str,
    root: str,
    pr: int,
    issue: int,
    method: str,
    transport: str,
    run_id: str | None,
    consent: Sequence[str],
) -> list[str]:
    argv = ["merge", path, "--root", root, "--pr", str(pr), "--issue", str(issue)]
    argv += ["--method", method, "--transport", transport]
    if run_id:
        argv += ["--run-id", run_id]
    return argv + list(consent)
