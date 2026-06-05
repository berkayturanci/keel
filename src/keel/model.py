"""The keel backbone: the fixed, ordered step machine and its extension slots.

This module is the single source of truth for the step IDs, the named slots an
extension may register into, and the invariants the backbone always preserves.
It is pure data — no I/O, no config — so consumers (config, extensions,
orchestrator) and tests can all agree on one definition.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """One backbone step. ``slot`` is the named extension point it exposes (if any)."""

    id: str
    name: str
    slot: str | None = None
    agentic: bool = False  # True if the step dispatches to an agent (implement/review/classify)


#: The fixed backbone, in execution order. Changing this is a keel-core change.
BACKBONE: tuple[Step, ...] = (
    Step("s0", "config"),
    Step("s1", "select"),
    Step("s2", "branch"),
    Step("s3", "guard"),
    Step("s4", "implement", slot="after-implement", agentic=True),
    Step("s5", "classify", agentic=True),
    Step("s6", "ci"),
    Step("s7", "review", slot="reviewers", agentic=True),
    Step("s8", "test", slot="tester"),
    Step("s9", "fixloop"),
    Step("s10", "merge", slot="pre-merge"),
    Step("s11", "capture", slot="post-merge"),
    Step("s12", "close"),
)

#: The named slots, in backbone order. Extensions are add-only into these.
SLOTS: tuple[str, ...] = tuple(s.slot for s in BACKBONE if s.slot)

#: Invariants the backbone always preserves — no config or extension can override.
INVARIANTS: tuple[str, ...] = (
    "merge_lock",               # every merge goes through the mkdir-based lock
    "window_gate",              # the night no-merge window is enforced
    "fail_soft",                # a soft failure degrades to a no-op, never aborts
    "orchestrator_only_writes",  # only the orchestrator writes to the PR
    "attribution",              # implementer/reviewer vendor+model is recorded
)

_BY_ID: dict[str, Step] = {s.id: s for s in BACKBONE}
_BY_SLOT: dict[str, Step] = {s.slot: s for s in BACKBONE if s.slot}


def step_ids() -> tuple[str, ...]:
    """All backbone step IDs in order."""
    return tuple(s.id for s in BACKBONE)


def get_step(step_id: str) -> Step:
    """Return the step with ``step_id`` (raises ``KeyError`` if unknown)."""
    return _BY_ID[step_id]


def step_for_slot(slot: str) -> Step:
    """Return the backbone step that exposes ``slot`` (raises ``KeyError``)."""
    return _BY_SLOT[slot]
