"""The deterministic ship decisions — keel's value-add as pure functions.

The agentic steps and the git/gh plumbing live in the adapter + I/O layer; the
*decisions* (how many reviewers, whether to merge / defer / block, whether to keep
fixing) are pure and live here, so they are reproducible and fully unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import classify
from .findings import Verdict
from .window import is_merge_open

#: Hard cap on review→fix rounds (matches ship's budget).
MAX_FIX_ROUNDS = 3

#: GitHub check-rollup conclusions that count as "not failing".
CI_OK_STATES = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


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


def ci_passing(ci_conclusion: str | None) -> bool | None:
    """Interpret a check-rollup string (e.g. ``"SUCCESS,FAILURE"``). ``None`` == unknown."""
    if ci_conclusion is None:
        return None
    parts = [p.strip().upper() for p in ci_conclusion.split(",") if p.strip()]
    if not parts:
        return None
    return all(p in CI_OK_STATES for p in parts)


@dataclass(frozen=True)
class ShipAssessment:
    tier: int
    reviewers: int
    window_open: bool
    ci_ok: bool | None
    merge: MergeDecision


def assess(
    *,
    changed_files: list[str],
    gate_verdict: Verdict,
    tier3_globs: tuple[str, ...] = (),
    docs_globs: tuple[str, ...] = (),
    timezone: str | None = None,
    merge_window: str | None = None,
    ci_conclusion: str | None = None,
    now=None,
    is_blocker: bool = False,
) -> ShipAssessment:
    """The whole deterministic ship decision in one place: tier → reviewers, window,
    CI, and the final merge action. Pure — identical inputs give identical output."""
    tier = classify.tier_for_files(changed_files, tier3_globs=tier3_globs, docs_globs=docs_globs)
    reviewers = reviewer_count(tier)
    window_open = (
        is_merge_open(timezone, merge_window, now=now) if (timezone and merge_window) else True
    )
    ci_ok = ci_passing(ci_conclusion)
    if ci_ok is False:
        merge = MergeDecision("block", "CI failing")
    else:
        merge = decide_merge(gate_verdict, window_open=window_open, is_blocker=is_blocker)
    return ShipAssessment(tier, reviewers, window_open, ci_ok, merge)
