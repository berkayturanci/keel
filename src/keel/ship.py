"""The deterministic ship decisions — keel's value-add as pure functions.

The agentic steps and the git/gh plumbing live in the adapter + I/O layer; the
*decisions* (how many reviewers, whether to merge / defer / block, whether to keep
fixing) are pure and live here, so they are reproducible and fully unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from .findings import Verdict

#: Hard cap on review→fix rounds (matches ship's budget).
MAX_FIX_ROUNDS = 3


def reviewer_count(tier: int) -> int:
    """Reviewers for a risk tier: TIER-3→3, TIER-2→2, TIER-1→1 (default 2)."""
    return {3: 3, 2: 2, 1: 1}.get(tier, 2)


@dataclass(frozen=True)
class MergeDecision:
    action: str  # "merge" | "defer" | "block"
    reason: str


def decide_merge(verdict: Verdict, *, window_open: bool, is_blocker: bool = False) -> MergeDecision:
    """Decide what to do with a green-or-not PR given the window.

    * blocking findings ⇒ **block** (never merges);
    * outside the merge window and not a blocker ⇒ **defer** to the morning queue;
    * otherwise ⇒ **merge**. A blocker bypasses the window (but never the findings).
    """
    if verdict.blocked:
        return MergeDecision("block", "blocking findings present")
    if not window_open and not is_blocker:
        return MergeDecision("defer", "outside merge window (night no-merge)")
    reason = "blocker bypass" if (is_blocker and not window_open) else "clear to merge"
    return MergeDecision("merge", reason)


def should_run_fixloop(verdict: Verdict, *, current_round: int, cap: int = MAX_FIX_ROUNDS) -> bool:
    """True if there are blocking findings and the fix budget is not exhausted."""
    return verdict.blocked and current_round < cap
