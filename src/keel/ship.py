"""The deterministic ship decisions — keel's value-add as pure functions.

The agentic steps and the git/gh plumbing live in the adapter + I/O layer; the
*decisions* (how many reviewers, whether to merge / defer / block, whether to keep
fixing) are pure and live here, so they are reproducible and fully unit-tested.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import classify
from . import team as team_policy
from .findings import Verdict, decision_for
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
    """Focus coverage for each reviewer slot. Lower counts merge focus; none are dropped.

    Zero slots is not "one slot with everything merged in": it is a tier whose
    ``knobs.team`` policy made the jury the review panel (#1014), so there is no host
    reviewer to carry a focus. The panel's own coverage is the jury's business.
    """
    if count <= 0:
        return ()
    if count <= 1:
        return (
            {
                "slot": "A",
                "focus": list(REVIEW_FOCUS_A + REVIEW_FOCUS_B + REVIEW_FOCUS_C),
                "merged_from": ["A", "B", "C"],
            },
        )
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
    panel_is_jury: bool = False,
    policy_mode: str | None = None,
    minimum_vendors: int = MINIMUM_JURY_VENDORS,
) -> dict[str, Any]:
    """Resolve the cross-vendor jury mode using ship flag precedence.

    ``panel_is_jury`` is a ``knobs.team`` tier whose review policy is ``jury``: the panel
    *is* the review, so it enables the jury the way tier-3 auto does but for a reason the
    project stated rather than one keel inferred. ``policy_mode`` is ``team.jury.mode``,
    which can only ever make an enabled jury *advisory* — a project cannot promote a jury
    that ``--no-jury`` turned off. ``minimum_vendors`` is ``team.jury.min_vendors``, which
    may raise :data:`MINIMUM_JURY_VENDORS` but never lowers it (the schema's floor is 2).

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
    elif panel_is_jury:
        enabled = True
        reason = "team.review panel"
    elif tier == 3:
        enabled = True
        reason = "tier-3 auto"
    else:
        enabled = False
        reason = "default"
    advisory = jury_advisory or policy_mode == "advisory"
    mode = "off" if not enabled else ("advisory" if advisory else "gating")
    downgraded = (
        mode == "gating"
        and participating_vendors is not None
        and participating_vendors < minimum_vendors
    )
    if downgraded:
        mode = "advisory"
        reason = (
            f"{reason}; downgraded to advisory "
            f"({participating_vendors} participating vendor(s), "
            f"minimum {minimum_vendors})"
        )
    return {
        "enabled": enabled,
        "mode": mode,
        "reason": reason,
        "configured_gate": "jury" in gates,
        "fail_soft": True,
        "minimum_vendors": minimum_vendors,
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
    require_distinct_vendors: bool | None = None,
    jury_participating_vendors: int | None = None,
    assignment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-readable review, jury, test, and merge-gate plan for ship-like flows.

    ``assignment`` is the resolved ``knobs.team`` team (:func:`keel.team.resolve_assignment`).
    When one is supplied it owns the reviewer bench — how many slots there are, who sits in
    each, and whether the jury is the panel instead — so the contract a host executes and
    the assignment it renders cannot disagree. Without one the tier-derived counts stand,
    which is every pre-#1014 caller.

    ``require_distinct_vendors`` is tri-state: ``None`` takes the tier-derived default
    (on from tier-2 up), a bool is the project's explicit answer.
    """
    if reviewer_override is not None and reviewer_override not in {1, 2, 3}:
        raise ValueError("reviewer_override must be one of 1, 2, or 3")
    if review_comments not in POSTING_MODES:
        raise ValueError("review_comments must be 'inline' or 'summary'")
    if assignment is None:
        count = reviewer_override if reviewer_override is not None else reviewer_count(tier or 2)
        source = (
            "override"
            if reviewer_override is not None
            else ("risk-tier" if tier is not None else "unresolved")
        )
        panel, slots = "reviewers", []
        panel_is_jury = False
    else:
        count = assignment["reviewer_count"]
        source = assignment["reviewer_source"]
        panel = assignment["review_panel"]
        slots = list(assignment["reviewers"])
        panel_is_jury = bool(assignment["jury"]["panel_is_review"])
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
            "require_distinct_vendors": team_policy.require_distinct_vendors(
                require_distinct_vendors, tier
            ),
            "orchestrator_owns_writes": True,
            "panel": panel,
            # Per-slot provider/model/effort, so a host dispatches the configured vendor
            # for slot B instead of running one vendor N times (#1014). Empty for every
            # caller that resolves no team, which keeps the pre-#1014 contract intact.
            "slots": slots,
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
            panel_is_jury=panel_is_jury,
            policy_mode=None if assignment is None else assignment["jury"]["mode"],
            minimum_vendors=(
                MINIMUM_JURY_VENDORS if assignment is None else assignment["jury"]["min_vendors"]
            ),
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


#: The built-in jury gate writes ``jury:<reviewer>`` (and ``jury:consensus`` /
#: ``jury:incomplete-run`` / …) as a finding's source: one gate with several voices.
_JURY_SOURCE_PREFIX = "jury:"


def _gate_id(source: str) -> str:
    """The gate a finding's ``source`` belongs to.

    Command gates write their ``spec.id`` verbatim, and nothing forbids a colon in an
    extension's id, so only the jury's own ``jury:`` prefix is collapsed — splitting
    every source on ``:`` would turn an operator's ``sec:scan`` gate into ``sec``.
    """
    return "jury" if source.startswith(_JURY_SOURCE_PREFIX) else source


def blocking_sources(verdict: Verdict) -> tuple[str, ...]:
    """The distinct gates whose findings block this verdict, sorted.

    Ship's verdict is built from gate outcomes, so a finding's ``source`` is the gate's
    id, except for the jury's ``jury:<voice>`` sources, which :func:`_gate_id` folds
    back to the one gate they belong to.
    """
    return tuple(
        sorted(
            {
                _gate_id(finding.source)
                for finding in verdict.findings
                if finding.source and decision_for(finding.severity) == "block"
            }
        )
    )


def block_reason(verdict: Verdict) -> str:
    """The reason a blocked verdict gives, naming what blocked it when it can.

    "blocking findings present" is true and useless next to a reviewer verdict that says
    "none blocking": the findings it means are the ones a failed ``on_fail: block`` gate
    produced, and the line was the one place the operator looked that did not say which
    gate (#1007). A verdict blocked with no attributable source keeps the old wording.
    """
    sources = blocking_sources(verdict)
    if not sources:
        return "blocking findings present"
    return f"blocking findings from gate(s): {', '.join(sources)}"


def decide_merge(
    verdict: Verdict,
    *,
    window_open: bool,
    is_blocker: bool = False,
    unrun_blocking_gates: tuple[str, ...] = (),
) -> MergeDecision:
    """Decide what to do with a green-or-not PR given the window.

    * blocking findings ⇒ **block** (never merges);
    * a required gate that nobody ran ⇒ **block** (no verdict exists to clear it);
    * outside the merge window and not a blocker ⇒ **defer** to the morning queue;
    * otherwise ⇒ **merge**. A blocker bypasses the window (but never the findings).

    ``unrun_blocking_gates`` names ``on_fail: block`` gates this run did not execute —
    agentic gates reach the command-only runner, which does not dispatch them. The
    assessment must say so: :func:`keel.ledger.record_gates_passed` refuses to certify
    such a record, so reporting "clear to merge" would promise a merge that
    ``keel merge`` will then refuse, with the operator given no reason why.
    """
    if verdict.blocked:
        return MergeDecision("block", block_reason(verdict))
    if unrun_blocking_gates:
        listed = ", ".join(unrun_blocking_gates)
        return MergeDecision(
            "block",
            f"required gate(s) not run: {listed} — record a result with "
            "--gate-result <id>=pass|fail once the gate has been dispatched",
        )
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


def ci_ran(ci_conclusion: str | None) -> bool | None:
    """Did any check report for this head? ``None`` == we could not find out.

    Separate from :func:`ci_passing` on purpose. "Every check passed" and "no check
    ran" are not the same fact, and folding them together is the defect in #675: an
    empty rollup used to reach the merge decision as *unknown*, and unknown did not
    block, so a PR nothing had verified assessed identically to a green one — then
    that assessment was written into the run ledger as evidence.

    ``""`` is ``gh`` reporting an empty rollup (a fact about the PR) and returns
    **False**. ``None`` is ``gh`` never answered, or no PR was supplied at all (a
    fact about the runner) and stays **None** — keel does not block on what it
    could not observe, it blocks on having observed nothing.
    """
    if ci_conclusion is None:
        return None
    return bool(ci_conclusion.strip())


def missing_ci_workflows(
    workflow_names: Sequence[str] | None,
    ci_workflows: dict[str, str] | None,
) -> tuple[str, ...]:
    """Declared workflows in ``ci_workflows`` that reported nothing for this head.

    ``knobs.ci_workflows`` is the project stating which workflows gate a merge, so
    presence can be checked against a **declaration** instead of inferred from an
    empty set — the difference between "I saw no failures" and "I saw the things
    that were supposed to run".

    ``workflow_names`` must be *workflow* names (:func:`keel.github.ci_workflow_names`),
    not job names. The distinction is not cosmetic: `ci_workflows` is keyed ``CI``,
    while the rollup reports ``test (py3.13 / ubuntu-latest)``, so comparing against
    job names would report every declared workflow missing on any repo using a matrix.
    Matching is exact and case-insensitive — a prefix rule would let an unrelated
    ``testing-utils`` satisfy a declared ``test``.

    ``()`` when nothing is declared or the names could not be read — absence of a
    declaration is not evidence of a missing run.
    """
    if not ci_workflows or workflow_names is None:
        return ()
    reported = {name.strip().lower() for name in workflow_names if name.strip()}
    return tuple(
        sorted(declared for declared in ci_workflows if declared.strip().lower() not in reported)
    )


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
    halted: bool = False  # pause mode + outside window ⇒ pipeline halted
    bypassed_window: bool = False  # hotfix merged outside the window (audited)
    review_contract: dict[str, Any] | None = None
    #: Did any check report? False == the rollup was empty (nothing verified this
    #: head); None == keel could not find out. Distinct from ``ci_ok`` (#675).
    ci_ran: bool | None = None
    #: Declared ``knobs.ci_workflows`` that produced no check for this head.
    missing_workflows: tuple[str, ...] = ()
    #: The resolved ``knobs.team`` assignment: who implements, gates, reviews, juries.
    assignment: dict[str, Any] | None = None


def assess(
    *,
    changed_files: list[str] | None,
    gate_verdict: Verdict,
    tier3_globs: tuple[str, ...] = (),
    docs_globs: tuple[str, ...] = (),
    allowlist_globs: tuple[str, ...] = (),
    patches: dict[str, str] | None = None,
    timezone: str | None = None,
    merge_window: str | None = None,
    merge_window_mode: str = "freeze",
    ci_conclusion: str | None = None,
    ci_check_names: Sequence[str] | None = None,
    ci_workflow_names: Sequence[str] | None = None,
    ci_workflows: dict[str, str] | None = None,
    now=None,
    is_blocker: bool = False,
    unrun_blocking_gates: tuple[str, ...] = (),
    reviewer_override: int | None = None,
    review_comments: str = "inline",
    gates: tuple[str, ...] = (),
    policy_pack: dict[str, Any] | None = None,
    jury: bool = False,
    no_jury: bool = False,
    jury_advisory: bool = False,
    team: team_policy.TeamPolicy | None = None,
    legacy_agents: dict[str, team_policy.Seat] | None = None,
    role: str | None = None,
    delegate: str | None = None,
    review_delegates: Sequence[str] = (),
    host_agent: str = team_policy.HOST_DEFAULT,
    require_distinct_vendors: bool | None = None,
) -> ShipAssessment:
    """The whole deterministic ship decision in one place: tier → reviewers, window,
    CI, and the final merge action. Pure — identical inputs give identical output.

    ``merge_window_mode`` 'pause' halts the pipeline outside the window; 'freeze'
    (default) only blocks the merge. ``is_blocker`` (a hotfix) bypasses the window —
    but never the findings or a failing CI.

    ``patches`` is the per-file diff, keyed by path. Without it this classified
    from filenames alone and so could not apply the diff-based TIER-3 downgrade,
    which made the assessment a human reads disagree with the evidence gate that
    enforces it (#845). ``None`` keeps the old behaviour — no diff is no evidence,
    and the path decides.

    ``changed_files`` is ``None`` when git could not be read (as
    :func:`keel.git.changed_files` reports it), which is deliberately *not* the same
    as ``[]``. An empty list classifies as the default tier; an unreadable one
    classifies fail-closed at :data:`keel.classify.UNKNOWN_TIER`, so a change nobody
    could see never buys itself a lighter review contract."""
    tier = (
        classify.UNKNOWN_TIER
        if changed_files is None
        else classify.tier_for_files(
            changed_files,
            tier3_globs=tier3_globs,
            docs_globs=docs_globs,
            allowlist_globs=allowlist_globs,
            patches=patches,
        )
    )
    assignment = team_policy.resolve_assignment(
        team if team is not None else team_policy.TeamPolicy(),
        tier=tier,
        role=role,
        default_count=reviewer_count(tier),
        reviewer_override=reviewer_override,
        delegate=delegate,
        review_delegates=review_delegates,
        host_agent=host_agent,
        legacy=legacy_agents,
    )
    reviewers = assignment["reviewer_count"]
    window_open = (
        is_merge_open(timezone, merge_window, now=now) if (timezone and merge_window) else True
    )
    halted = (merge_window_mode == "pause") and not window_open and not is_blocker
    ci_ok = ci_passing(ci_conclusion)
    ran = ci_ran(ci_conclusion)
    docs_only = changed_files is not None and classify.is_docs_only(list(changed_files), docs_globs)
    missing = () if docs_only else missing_ci_workflows(ci_workflow_names, ci_workflows)
    if ci_ok is False:
        merge = MergeDecision("block", "CI failing")
    elif ran is False and not docs_only:
        # Fail closed, and say which it was: an operator needs "nothing verified
        # this commit" to read differently from "a check went red".
        #
        # The docs-only carve-out is not a softening — it is what `keel merge`
        # already applies to its own `no-checks` state (cli._ci_state), and this
        # assessment must not contradict the gate it is predicting. A docs-only
        # change legitimately matches no workflow's path filter; anything else
        # with an empty rollup was simply never verified.
        merge = MergeDecision("block", "no CI ran — nothing verified this commit")
    elif missing:
        merge = MergeDecision("block", f"declared CI workflow(s) never ran: {', '.join(missing)}")
    else:
        merge = decide_merge(
            gate_verdict,
            window_open=window_open,
            is_blocker=is_blocker,
            unrun_blocking_gates=unrun_blocking_gates,
        )
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
        require_distinct_vendors=require_distinct_vendors,
        assignment=assignment,
    )
    return ShipAssessment(
        tier,
        reviewers,
        window_open,
        ci_ok,
        merge,
        halted,
        bypassed,
        review_contract,
        ran,
        missing,
        assignment,
    )
