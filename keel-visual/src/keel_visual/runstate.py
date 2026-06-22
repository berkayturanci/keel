"""Pure run-state adapter: turn keel records into a visualizer-ready model.

``keel-visual`` is an *optional* companion to keel core — it renders a run, it
never drives one. This module is the bridge: it reads the exact record shapes
keel core already produces (a ship_run ledger record, an optional checkpoint
step id) and projects them onto the run's **command flow**
(:func:`keel.flows.flow_for`) as a flat, JSON-serialisable ``RunState`` the
front-end can animate. ``ship`` is the s0–s12 backbone with rich merge/test-gate
detail; every other keel command renders its own phases.

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

from keel import flows

SCHEMA_VERSION = "keel.visual.run-state.v1"

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_GATE = "gate"
STATUS_LOOP = "loop"

# Ship backbone steps with ship-specific outcome data (merge gate, test gate).
SHIP_COMMAND = "ship"
MERGE_STEP_ID = "s10"
TEST_STEP_ID = "s8"

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


def _phase_index(flow: tuple[Any, ...], phase_id: str | None) -> int | None:
    """Return the index of the phase whose id is ``phase_id`` (``None`` if absent)."""
    if not isinstance(phase_id, str):
        return None
    for idx, phase in enumerate(flow):
        if phase.id == phase_id:
            return idx
    return None


def _active_index(
    flow: tuple[Any, ...],
    record: dict[str, Any] | None,
    checkpoint_step: str | None,
    *,
    merged: bool,
    is_ship: bool,
) -> int:
    """Resolve which phase index the run is currently on, for any command flow.

    Priority: a checkpoint step that names a phase in this flow wins (for ship the
    checkpoint's ``s0..s12`` ids are the flow ids; other commands' checkpoints —
    when they exist — are ship-backbone ids and won't match, so they fall back).
    For ship, a merged run shows ``close`` and an un-merged record sits at
    ``merge``. With nothing to go on, the run is at the start.
    """
    idx = _phase_index(flow, checkpoint_step)
    if idx is not None:
        return idx
    if is_ship and record is not None:
        if merged:
            return len(flow) - 1
        return _phase_index(flow, MERGE_STEP_ID) or 0
    return 0


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
    for key in ("merge", "capture", "close", "last_gate", "jury_mode"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value
    return out


# The cross-vendor jury vocabulary keel writes (see keel.ship.resolve_jury):
# "off" disabled; "advisory"/"gating" enabled. Recognised modes are the ONLY
# values ever surfaced — an unrecognised mode is normalised to a safe literal so
# no arbitrary record string reaches the DOM (mirrors how `command` falls back).
_JURY_INACTIVE = {"off", "none", "disabled", ""}
_JURY_KNOWN = {"off", "advisory", "gating", "none", "disabled"}

# The backbone step the jury attaches to (s7 review).
REVIEW_STEP_ID = "s7"


def _normalize_jury(raw: Any) -> dict[str, Any]:
    """Normalise a raw ``jury_mode`` token into ``{"mode", "active"}``.

    ``active`` is true unless the mode is in the inactive set; ``mode`` is always a
    recognised token (``on`` for an unknown-but-enabled value) so no untrusted
    record string is ever surfaced. A blank/missing value yields the inactive
    default. Pure — reads only its argument.
    """
    norm = raw.strip().lower() if isinstance(raw, str) and raw.strip() else None
    if norm is None:
        return {"mode": None, "active": False}
    active = norm not in _JURY_INACTIVE
    mode = norm if norm in _JURY_KNOWN else ("on" if active else "off")
    return {"mode": mode, "active": active}


def jury_from_record(record: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the cross-vendor jury state from a ship_run ``record`` (the ledger).

    keel records ``run_context.jury_mode`` (``off`` / ``advisory`` / ``gating``)
    when a ship run resolves the jury — tier-3 auto-enables it, ``--jury`` forces
    it; the actual jury (``ai-jury`` when installed) is fail-soft, so this is read
    from *keel's own ledger*, never from ai-jury. This is the **post-run** source;
    for the live mid-run view see :func:`jury_from_checkpoint`. Pure.
    """
    rec = record if isinstance(record, dict) else None
    run_context = rec.get("run_context") if rec else None
    raw = run_context.get("jury_mode") if isinstance(run_context, dict) else None
    return _normalize_jury(raw)


def jury_from_checkpoint(state: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the **live** jury state from a checkpoint ``state`` block.

    keel writes ``state.jury_mode`` at the review/test steps (see
    :func:`keel.checkpoint.build_checkpoint_record`), so a run *in progress* —
    which has no ledger ship_run record yet — exposes the jury here for
    ``--follow``. Same vocabulary/normalisation as :func:`jury_from_record`; a
    missing key yields the inactive default so callers fall back to the ledger.
    Pure — reads only its argument.
    """
    raw = state.get("jury_mode") if isinstance(state, dict) else None
    return _normalize_jury(raw)


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


def _gate_for_phase(
    phase: Any, *, counts: dict[str, int], merge_outcome: str, is_ship: bool,
) -> dict[str, Any] | None:
    """Build the gate block for a gate/merge phase, or ``None`` otherwise.

    Ship's merge and test gates carry rich outcome data (merge state, finding
    counts). A gate phase in any other command flow has no such data available,
    so it renders as a gate with a neutral ``pending`` outcome — the structure is
    shown without a fabricated pass/fail.
    """
    if phase.kind == "merge":
        return {"kind": "merge", "outcome": merge_outcome}
    if phase.kind != "gate":
        return None
    if is_ship and phase.id == TEST_STEP_ID:
        blocked = bool(counts.get("critical") or counts.get("major"))
        return {
            "kind": "test",
            "outcome": "fail" if blocked else "pass",
            "counts": dict(counts),
            "worst": worst_finding(counts),
        }
    return {"kind": "gate", "outcome": "pending"}


def _status(idx: int, active: int, kind: str) -> str:
    if idx < active:
        return STATUS_DONE
    if idx > active:
        return STATUS_PENDING
    if kind in ("gate", "merge"):
        return STATUS_GATE
    if kind == "loop":
        return STATUS_LOOP
    return STATUS_ACTIVE


def build_run_state(
    record: dict[str, Any] | None,
    *,
    checkpoint_step: str | None = None,
    checkpoint_state: dict[str, Any] | None = None,
    command: str = "ship",
) -> dict[str, Any]:
    """Project a keel ``record`` onto its command flow as a ``RunState``.

    ``record`` is a ship_run ledger record (or ``None`` for an empty/just-started
    run). ``checkpoint_step`` is the current step id from the keel checkpoint;
    ``checkpoint_state`` is its live ``state`` block (see
    :func:`live_state_from_checkpoint`). ``command`` selects the flow
    (:func:`keel.flows.flow_for`) — ``ship`` carries rich merge/test-gate and
    regression detail; every other command renders its own phases (position +
    kinds), since only ship-style runs expose that data.

    Returns a flat JSON-serialisable dict the front-end animates. Pure — reads
    only its arguments.
    """
    flow = flows.flow_for(command)
    command = command if flows.is_known(command) else SHIP_COMMAND
    is_ship = command == SHIP_COMMAND
    rec = record if isinstance(record, dict) else None
    live = checkpoint_state if isinstance(checkpoint_state, dict) else {}
    live_merge = live.get("merge")
    ledger_merged = (_merge_action(rec) == "merge" if rec else False) if is_ship else False
    merged = (live_merge == "merged" or ledger_merged) if is_ship else False
    merge_outcome = _merge_outcome(merged=merged, live_merge=live_merge if is_ship else None)
    counts = (
        _verdict_counts(rec) if (rec and is_ship)
        else {"critical": 0, "major": 0, "minor": 0, "nit": 0}
    )
    active = _active_index(flow, rec, checkpoint_step, merged=merged, is_ship=is_ship)
    active = max(0, min(active, len(flow) - 1))

    steps: list[dict[str, Any]] = []
    for idx, phase in enumerate(flow):
        steps.append({
            "id": phase.id,
            "name": phase.name,
            "kind": phase.kind,
            "status": _status(idx, active, phase.kind),
            "exercised": True,
            "gate": _gate_for_phase(
                phase, counts=counts, merge_outcome=merge_outcome, is_ship=is_ship,
            ),
        })

    issue = rec.get("issue") if rec else None
    pr = rec.get("pull_request") if rec else None
    # Branch / base / author come straight from the ledger ship_run record keel
    # already writes — fixed for the life of the run, so never stale, and no new
    # core field is needed. Activity-only runs (no ledger record) leave them None.
    git_block = rec.get("git") if rec else None
    actors = rec.get("actors") if rec else None
    branch = git_block.get("branch") if isinstance(git_block, dict) else None
    base = git_block.get("base_branch") if isinstance(git_block, dict) else None
    author = actors.get("implementer") if isinstance(actors, dict) else None
    # Jury: prefer the live checkpoint (mid-run, for --follow); fall back to the
    # ledger (post-run). Both read only keel's own records, never ai-jury.
    if not is_ship:
        jury = {"mode": None, "active": False}
    else:
        live_jury = jury_from_checkpoint(live)
        jury = live_jury if live_jury["mode"] is not None else jury_from_record(rec)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "issue": _int_or_none(issue.get("number")) if isinstance(issue, dict) else None,
        "pr": _int_or_none(pr.get("number")) if isinstance(pr, dict) else None,
        "branch": branch if isinstance(branch, str) and branch else None,
        "base": base if isinstance(base, str) and base else None,
        "author": author if isinstance(author, str) and author else None,
        "active_index": active,
        "active_id": flow[active].id,
        "merged": merged,
        "merge_state": live_merge if isinstance(live_merge, str) else None,
        "jury": jury,
        "steps": steps,
        "regression": _regression(flow, active, counts, is_ship=is_ship),
    }


def _regression(
    flow: tuple[Any, ...], active: int, counts: dict[str, int], *, is_ship: bool,
) -> dict[str, Any]:
    """Build the regression-folder model shown while the ship test gate is active.

    Ship-only: keyed off the test step's position. For any other command there is
    no regression folder, so it reports ``reached=False`` (the panel stays hidden).
    """
    test_idx = _phase_index(flow, TEST_STEP_ID)
    reached = is_ship and test_idx is not None and active >= test_idx
    return {
        "reached": reached,
        "coverage": 100 if reached else 0,
        "counts": dict(counts),
        "worst": worst_finding(counts),
    }
