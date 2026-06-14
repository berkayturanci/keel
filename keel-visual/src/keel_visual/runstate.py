"""Pure run-state adapter: turn keel records into a visualizer-ready model.

``keel-visual`` is an *optional* companion to keel core — it renders a run, it
never drives one. This module is the bridge: it reads the exact record shapes
keel core already produces (a ship_run ledger record, an optional checkpoint
step id) and projects them onto the fixed backbone (:data:`keel.model.BACKBONE`)
as a flat, JSON-serialisable ``RunState`` the front-end can animate.

It is pure by the same contract as keel core: data in, structured data out, no
network/subprocess/clock/random. The CLI does the I/O (reads the ledger and
checkpoint); this module only transforms dicts — so it is driven by the same
fixtures the keel test-suite builds.

Step status vocabulary (what the front-end paints):

* ``done``    — a step before the active one (completed)
* ``active``  — the step the run is currently on
* ``gate``    — the active step *and* it is a gate (test / merge)
* ``loop``    — the active step *and* it is the fix loop
* ``pending`` — a step the run has not reached yet
"""

from __future__ import annotations

from typing import Any

from keel.model import BACKBONE

SCHEMA_VERSION = "keel.visual.run-state.v1"

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_GATE = "gate"
STATUS_LOOP = "loop"

# Backbone steps that carry special visual semantics.
GATE_STEP_IDS = ("s8", "s10")
LOOP_STEP_ID = "s9"
MERGE_STEP_ID = "s10"
TEST_STEP_ID = "s8"

# Which backbone steps each command actually exercises (others render dimmed).
COMMAND_STEPS: dict[str, tuple[str, ...]] = {
    "ship": tuple(s.id for s in BACKBONE),
    "regression": ("s0", "s6", "s8", "s9", "s12"),
    "review": ("s0", "s1", "s7", "s8", "s12"),
}

# Human labels for the regression folder's worst-finding state -> colour band.
WORST_NONE = "none"
WORST_MINOR = "minor"
WORST_MAJOR = "major"
WORST_CRITICAL = "critical"


def _int_or_none(value: Any) -> int | None:
    """Coerce an issue/PR number to ``int`` (or ``None``) — never free text.

    Defense-in-depth: keel writes these as ints, but a hand-edited or corrupt
    ledger could carry a string. Forcing ``int`` here means no arbitrary string
    from a record ever reaches the HTML ``window.KEEL_RUN`` payload.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def current_step_from_checkpoint(record: dict[str, Any] | None) -> str | None:
    """Extract the live ``current_step`` id from a keel checkpoint record.

    keel writes the run's position to ``position.current_step`` at each step (see
    :func:`keel.checkpoint.build_checkpoint_record`). This is what ``--follow``
    reads to show where a run *actually* is, in real time. Returns ``None`` for a
    missing/malformed record so the caller degrades to inferring position from
    the ledger. Pure — reads only its argument.
    """
    if not isinstance(record, dict):
        return None
    position = record.get("position")
    step = position.get("current_step") if isinstance(position, dict) else None
    return step if isinstance(step, str) and step.strip() else None


def step_index(step_id: str | None) -> int | None:
    """Return the backbone index of ``step_id`` (``None`` when unknown/missing)."""
    if not isinstance(step_id, str):
        return None
    for idx, step in enumerate(BACKBONE):
        if step.id == step_id:
            return idx
    return None


def _merge_action(record: dict[str, Any]) -> str | None:
    assessment = record.get("assessment")
    if not isinstance(assessment, dict):
        return None
    merge = assessment.get("merge")
    if not isinstance(merge, dict):
        return None
    action = merge.get("action")
    return action if isinstance(action, str) else None


def _verdict_counts(record: dict[str, Any]) -> dict[str, int]:
    verdict = record.get("verdict")
    counts = verdict.get("counts") if isinstance(verdict, dict) else None
    out = {"critical": 0, "major": 0, "minor": 0, "nit": 0}
    if isinstance(counts, dict):
        for key in out:
            value = counts.get(key)
            if isinstance(value, int) and value >= 0:
                out[key] = value
    return out


def worst_finding(counts: dict[str, int]) -> str:
    """Return the worst severity present in ``counts`` (drives the folder colour)."""
    if counts.get("critical"):
        return WORST_CRITICAL
    if counts.get("major"):
        return WORST_MAJOR
    if counts.get("minor"):
        return WORST_MINOR
    return WORST_NONE


def _active_index(
    record: dict[str, Any] | None,
    checkpoint_step: str | None,
    *,
    merged: bool,
) -> int:
    """Resolve which backbone index the run is currently on.

    Priority: an explicit checkpoint step (authoritative — keel writes it at each
    step) wins; otherwise a merged run is shown at ``close`` (the final step) and
    an un-merged run with a record sits at ``merge``; with nothing to go on the
    run is at the start.
    """
    idx = step_index(checkpoint_step)
    if idx is not None:
        return idx
    if record is None:
        return 0
    if merged:
        return len(BACKBONE) - 1
    return step_index(MERGE_STEP_ID) or 0


def live_state_from_checkpoint(record: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the live ``state`` block from a keel checkpoint record.

    keel writes ``state.merge`` / ``state.capture`` / ``state.close`` /
    ``state.last_gate`` at each step (see :func:`keel.checkpoint.build_checkpoint_record`),
    so a run *in progress* — which has no ledger ship_run record yet — still
    exposes its merge/gate progress here. Returns only the recognised string
    fields; a missing/malformed block yields ``{}`` so position falls back to the
    ledger. Pure — reads only its argument.
    """
    if not isinstance(record, dict):
        return {}
    state = record.get("state")
    if not isinstance(state, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("merge", "capture", "close", "last_gate"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value
    return out


# Checkpoint merge_state -> merge-gate outcome shown by the visualizer.
_MERGE_STATE_OUTCOME = {
    "merged": "pass", "pending": "pending", "failed": "fail",
    "skipped": "pass", "not-started": "pending",
}


def _merge_outcome(*, merged: bool, live_merge: str | None) -> str:
    """Resolve the merge-gate outcome from the live checkpoint, else the ledger."""
    if live_merge in _MERGE_STATE_OUTCOME:
        return _MERGE_STATE_OUTCOME[live_merge]
    return "pass" if merged else "pending"


def _gate_for(
    step_id: str, *, counts: dict[str, int], merge_outcome: str,
) -> dict[str, Any] | None:
    """Build the gate block for a gate step, or ``None`` for a non-gate step."""
    if step_id not in GATE_STEP_IDS:
        return None
    if step_id == MERGE_STEP_ID:
        return {"kind": "merge", "outcome": merge_outcome}
    blocked = bool(counts.get("critical") or counts.get("major"))
    return {
        "kind": "test",
        "outcome": "fail" if blocked else "pass",
        "counts": dict(counts),
        "worst": worst_finding(counts),
    }


def _status(idx: int, active: int, step_id: str) -> str:
    if idx < active:
        return STATUS_DONE
    if idx > active:
        return STATUS_PENDING
    if step_id in GATE_STEP_IDS:
        return STATUS_GATE
    if step_id == LOOP_STEP_ID:
        return STATUS_LOOP
    return STATUS_ACTIVE


def _kind(step_id: str) -> str:
    if step_id == MERGE_STEP_ID:
        return "merge"
    if step_id in GATE_STEP_IDS:
        return "gate"
    if step_id == LOOP_STEP_ID:
        return "loop"
    return "normal"


def build_run_state(
    record: dict[str, Any] | None,
    *,
    checkpoint_step: str | None = None,
    checkpoint_state: dict[str, Any] | None = None,
    command: str = "ship",
) -> dict[str, Any]:
    """Project a keel ship_run ``record`` onto the backbone as a ``RunState``.

    ``record`` is a ship_run ledger record (or ``None`` for an empty/just-started
    run). ``checkpoint_step`` is the current step id from the keel checkpoint;
    ``checkpoint_state`` is its live ``state`` block (see
    :func:`live_state_from_checkpoint`). Both take priority over the ledger so a
    run *in progress* shows live position and merge progress before any ship_run
    record exists. ``command`` selects which steps are highlighted vs dimmed.

    Returns a flat JSON-serialisable dict the front-end animates. Pure — reads
    only its arguments.
    """
    rec = record if isinstance(record, dict) else None
    live = checkpoint_state if isinstance(checkpoint_state, dict) else {}
    live_merge = live.get("merge")
    ledger_merged = _merge_action(rec) == "merge" if rec else False
    merged = live_merge == "merged" or ledger_merged
    merge_outcome = _merge_outcome(merged=merged, live_merge=live_merge)
    counts = _verdict_counts(rec) if rec else {"critical": 0, "major": 0, "minor": 0, "nit": 0}
    active = _active_index(rec, checkpoint_step, merged=merged)
    active = max(0, min(active, len(BACKBONE) - 1))
    command = command if command in COMMAND_STEPS else "ship"
    exercised = set(COMMAND_STEPS[command])

    steps: list[dict[str, Any]] = []
    for idx, step in enumerate(BACKBONE):
        steps.append({
            "id": step.id,
            "name": step.name,
            "kind": _kind(step.id),
            "status": _status(idx, active, step.id),
            "exercised": step.id in exercised,
            "gate": _gate_for(step.id, counts=counts, merge_outcome=merge_outcome),
        })

    issue = rec.get("issue") if rec else None
    pr = rec.get("pull_request") if rec else None
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "issue": _int_or_none(issue.get("number")) if isinstance(issue, dict) else None,
        "pr": _int_or_none(pr.get("number")) if isinstance(pr, dict) else None,
        "active_index": active,
        "active_id": BACKBONE[active].id,
        "merged": merged,
        "merge_state": live_merge if isinstance(live_merge, str) else None,
        "steps": steps,
        "regression": _regression(active, counts),
    }


def _regression(active: int, counts: dict[str, int]) -> dict[str, Any]:
    """Build the regression-folder model shown while the test gate is active.

    Coverage is a presentation value: a passed/blocked test gate reports 100%
    observed coverage; before the run reaches the test step there is nothing to
    show yet (0%). The worst finding drives the folder colour band.
    """
    test_idx = step_index(TEST_STEP_ID) or 0
    reached = active >= test_idx
    coverage = 100 if reached else 0
    return {
        "reached": reached,
        "coverage": coverage,
        "counts": dict(counts),
        "worst": worst_finding(counts),
    }
