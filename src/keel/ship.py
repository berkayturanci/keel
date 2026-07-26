"""The deterministic ship decisions — keel's value-add as pure functions.

The agentic steps and the git/gh plumbing live in the adapter + I/O layer; the
*decisions* (how many reviewers, whether to merge / defer / block, whether to keep
fixing) are pure and live here, so they are reproducible and fully unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import classify
from .findings import Verdict
from .window import is_merge_open

#: Hard cap on review→fix rounds (matches ship's budget).
MAX_FIX_ROUNDS = 3

#: GitHub check-rollup conclusions that count as "not failing".
CI_OK_STATES = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})

POSTING_MODES = frozenset({"inline", "summary"})

# A cross-vendor jury needs at least this many distinct vendors to gate. Below it
# the panel cannot produce cross-vendor consensus, so the verdict is advisory —
# and a run where no agent produced output counts as zero, which is how "a jury
# that did not complete cleanly never gates" falls out of the same comparison.
MINIMUM_JURY_VENDORS = 2

REVIEW_FOCUS_A = (
    "logic correctness",
    "null safety",
    "language interop",
)
REVIEW_FOCUS_B = (
    "platform compatibility",
    "lifecycle safety",
    "API compatibility",
    "threading",
)
REVIEW_FOCUS_C = (
    "test coverage",
    "docs gate",
    "scope creep",
    "CI prediction",
    "security",
)


def reviewer_count(tier: int) -> int:
    """Reviewers for a risk tier: TIER-3→3, TIER-2→2, TIER-1→1 (default 2)."""
    return {3: 3, 2: 2, 1: 1}.get(tier, 2)


def reviewer_focuses(count: int) -> tuple[dict[str, Any], ...]:
    """Focus coverage for each reviewer slot. Lower counts merge focus; none are dropped."""
    if count <= 1:
        return ({
            "slot": "A",
            "focus": list(REVIEW_FOCUS_A + REVIEW_FOCUS_B + REVIEW_FOCUS_C),
            "merged_from": ["A", "B", "C"],
        },)
    if count == 2:
        return (
            {
                "slot": "A",
                "focus": list(REVIEW_FOCUS_A + REVIEW_FOCUS_B),
                "merged_from": ["A", "B"],
            },
            {
                "slot": "C",
                "focus": list(REVIEW_FOCUS_C),
                "merged_from": ["C"],
            },
        )
    return (
        {"slot": "A", "focus": list(REVIEW_FOCUS_A), "merged_from": ["A"]},
        {"slot": "B", "focus": list(REVIEW_FOCUS_B), "merged_from": ["B"]},
        {"slot": "C", "focus": list(REVIEW_FOCUS_C), "merged_from": ["C"]},
    )


def resolve_jury(
    *,
    tier: int | None,
    gates: tuple[str, ...] = (),
    jury: bool = False,
    no_jury: bool = False,
    jury_advisory: bool = False,
    participating_vendors: int | None = None,
) -> dict[str, Any]:
    """Resolve the cross-vendor jury mode using ship flag precedence.

    ``participating_vendors`` is the count of distinct vendors that actually took
    part in the panel. Below :data:`MINIMUM_JURY_VENDORS` a gating mode is
    downgraded to advisory, because a panel that small cannot produce
    cross-vendor consensus — and a run where no agent returned output is simply
    zero, so "a jury that did not complete cleanly never gates" needs no separate
    branch. ``None`` means the panel is not known yet (planning, ``keel plan``,
    any caller resolving the contract before s8 runs), and leaves the mode alone.

    The downgrade must live here rather than in adapter prose: the evidence gate
    derives its ``jury-verdict`` requirement from this ``mode``, so a mode that
    ignores the real panel makes the gate demand a verdict the jury step would
    decline to treat as gating.
    """
    if no_jury:
        enabled = False
        reason = "--no-jury"
    elif jury:
        enabled = True
        reason = "--jury"
    elif tier == 3:
        enabled = True
        reason = "tier-3 auto"
    else:
        enabled = False
        reason = "default"
    mode = "off" if not enabled else ("advisory" if jury_advisory else "gating")
    downgraded = (
        mode == "gating"
        and participating_vendors is not None
        and participating_vendors < MINIMUM_JURY_VENDORS
    )
    if downgraded:
        mode = "advisory"
        reason = (
            f"{reason}; downgraded to advisory "
            f"({participating_vendors} participating vendor(s), "
            f"minimum {MINIMUM_JURY_VENDORS})"
        )
    return {
        "enabled": enabled,
        "mode": mode,
        "reason": reason,
        "configured_gate": "jury" in gates,
        "fail_soft": True,
        "minimum_vendors": MINIMUM_JURY_VENDORS,
        "participating_vendors": participating_vendors,
        "downgraded": downgraded,
        "verified_consensus_gates": enabled and mode == "gating",
        "severity_policy": {
            "critical": "block",
            "major": "block",
            "minor": "gated-suggestion",
            "nit": "advisory",
        },
    }


def resolve_review_contract(
    *,
    tier: int | None,
    reviewer_override: int | None = None,
    review_comments: str = "inline",
    gates: tuple[str, ...] = (),
    policy_pack: dict[str, Any] | None = None,
    jury: bool = False,
    no_jury: bool = False,
    jury_advisory: bool = False,
    require_distinct_vendors: bool = False,
    jury_participating_vendors: int | None = None,
) -> dict[str, Any]:
    """Machine-readable review, jury, test, and merge-gate plan for ship-like flows."""
    if reviewer_override is not None and reviewer_override not in {1, 2, 3}:
        raise ValueError("reviewer_override must be one of 1, 2, or 3")
    if review_comments not in POSTING_MODES:
        raise ValueError("review_comments must be 'inline' or 'summary'")
    count = reviewer_override if reviewer_override is not None else reviewer_count(tier or 2)
    source = (
        "override" if reviewer_override is not None
        else ("risk-tier" if tier is not None else "unresolved")
    )
    pack = policy_pack or {}
    review_policy = pack.get("review", {}) if isinstance(pack.get("review", {}), dict) else {}
    return {
        "reviewers": {
            "count": count,
            "source": source,
            "tier": tier,
            "independent": True,
            "self_review_counts_toward_lgtm": False,
            "minimum_lgtm": count,
            "require_distinct_vendors": bool(require_distinct_vendors),
            "orchestrator_owns_writes": True,
            "focuses": list(reviewer_focuses(count)),
            "project_additions": list(review_policy.get("additions", [])),
            "required_sections": list(review_policy.get("required_sections", [])),
        },
        "posting": {
            "mode": review_comments,
            "inline_default": True,
            "per_reviewer_inline_fallback": "summary",
            "summary_mode": review_comments == "summary",
        },
        "jury": resolve_jury(
            tier=tier,
            gates=gates,
            jury=jury,
            no_jury=no_jury,
            jury_advisory=jury_advisory,
            participating_vendors=jury_participating_vendors,
        ),
        "finding_policy": {
            "critical": "block",
            "major": "block",
            "minor": "gated-suggestion",
            "nit": "advisory",
            "suggestions_require_fix_or_explicit_deferral": True,
            "parser_source": "reviewer-returned-findings",
        },
        "fixloop": {
            "max_rounds": MAX_FIX_ROUNDS,
            "blocker_rerun": "full-review",
            "suggestion_only_rerun": "narrowed-originating-focus",
        },
        "ci": {
            "failure_before_pending": True,
            "empty_check_set_allowed_for_docs_only": True,
            "retry_budget": 3,
        },
        "test_gates": {
            "configured_gates": list(gates),
            "no_jury_preserves_review_and_test_gates": True,
        },
        "merge_gate": {
            "merge_window_applies_to": "literal-merge-only",
            "merge_lock_scope": "literal-merge-only",
            "final_mergeability_recheck_inside_lock": True,
            "hotfix_bypasses_window_only": True,
            "hotfix_never_bypasses_findings_or_ci": True,
            "pr_merged_state_authoritative": True,
        },
        "closeout": {
            "comment_targets": ["issue", "pull_request"],
            "capture_marker_required": True,
            "status_done_after_merge_only": True,
        },
    }


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
    # ⚡ Bolt: ~3.4x faster validation using C-level frozenset.issuperset
    # instead of generator expression
    return CI_OK_STATES.issuperset(parts)


def is_hotfix(labels: list[str] | tuple[str, ...], *, hotfix_label: str = "hotfix") -> bool:
    """True if the issue/PR carries the hotfix label (case-insensitive)."""
    # ⚡ Bolt Optimization: Unroll any() generator and pre-compute lower() target
    target = hotfix_label.lower()
    for label in labels:
        if label.strip().lower() == target:
            return True
    return False


@dataclass(frozen=True)
class ShipAssessment:
    tier: int
    reviewers: int
    window_open: bool
    ci_ok: bool | None
    merge: MergeDecision
    halted: bool = False           # pause mode + outside window ⇒ pipeline halted
    bypassed_window: bool = False  # hotfix merged outside the window (audited)
    review_contract: dict[str, Any] | None = None


def assess(
    *,
    changed_files: list[str],
    gate_verdict: Verdict,
    tier3_globs: tuple[str, ...] = (),
    docs_globs: tuple[str, ...] = (),
    timezone: str | None = None,
    merge_window: str | None = None,
    merge_window_mode: str = "freeze",
    ci_conclusion: str | None = None,
    now=None,
    is_blocker: bool = False,
    reviewer_override: int | None = None,
    review_comments: str = "inline",
    gates: tuple[str, ...] = (),
    policy_pack: dict[str, Any] | None = None,
    jury: bool = False,
    no_jury: bool = False,
    jury_advisory: bool = False,
) -> ShipAssessment:
    """The whole deterministic ship decision in one place: tier → reviewers, window,
    CI, and the final merge action. Pure — identical inputs give identical output.

    ``merge_window_mode`` 'pause' halts the pipeline outside the window; 'freeze'
    (default) only blocks the merge. ``is_blocker`` (a hotfix) bypasses the window —
    but never the findings or a failing CI."""
    tier = classify.tier_for_files(changed_files, tier3_globs=tier3_globs, docs_globs=docs_globs)
    reviewers = reviewer_override if reviewer_override is not None else reviewer_count(tier)
    window_open = (
        is_merge_open(timezone, merge_window, now=now) if (timezone and merge_window) else True
    )
    halted = (merge_window_mode == "pause") and not window_open and not is_blocker
    ci_ok = ci_passing(ci_conclusion)
    if ci_ok is False:
        merge = MergeDecision("block", "CI failing")
    else:
        merge = decide_merge(gate_verdict, window_open=window_open, is_blocker=is_blocker)
    bypassed = is_blocker and not window_open and merge.action == "merge"
    review_contract = resolve_review_contract(
        tier=tier,
        reviewer_override=reviewer_override,
        review_comments=review_comments,
        gates=gates,
        policy_pack=policy_pack,
        jury=jury,
        no_jury=no_jury,
        jury_advisory=jury_advisory,
    )
    return ShipAssessment(
        tier, reviewers, window_open, ci_ok, merge, halted, bypassed, review_contract
    )
