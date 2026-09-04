"""Deterministic pre-merge evidence verification.

The ship adapter is agentic, but the artifacts it must leave behind are not:
reviewer verdict comments/reviews, the optional jury verdict, and the stable
closure comment marker. This module keeps the check pure so CI can enforce it
without trusting prose in an agent prompt.

Classification is **header-anchored**: :func:`marker_in_header` decides what a
comment is from its first non-empty line and nothing else, so a marker a reviewer
quotes in prose is content rather than a classification signal (#1026). The ship
assessment heading is anchored the same way, from its own header line (#1035).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import agents, closure
from . import team as team_policy

SCHEMA_VERSION = "keel.evidence.v1"
#: ``reviewers.panel`` when the cross-vendor jury *is* the review for this tier
#: (``knobs.team``'s ``review.by_tier.<n>: jury``), and the minimum distinct
#: vendors such a panel must span. Both come from :mod:`keel.team`, the leaf
#: module that owns the team vocabulary, so the gate and the policy cannot drift.
JURY_PANEL = team_policy.JURY_PANEL
DEFAULT_MINIMUM_JURY_VENDORS = team_policy.DEFAULT_MIN_VENDORS
AGENT_LABEL_PREFIX = "agent:"
MODEL_LABEL_PREFIX = "model:"
REVIEW_VERDICT_MARKER = "keel.review-verdict.v1"
JURY_VERDICT_MARKER = "keel.jury-verdict.v1"
#: The comment a live ship run posts on its PR right after creating it (#1013). It is
#: the *primary* arming signal for the evidence gate: unlike the branch name it is
#: written by the run itself, so a run that named its branch something else — or whose
#: ledger lives in a per-run worktree CI cannot read — still identifies as a keel ship
#: run. See :func:`gate_decision` for the full arming order.
SHIP_PROVENANCE_MARKER = "keel.ship-provenance.v1"
#: The marker the ship adapter tells a host to post when a finding is deferred rather
#: than fixed. keel core never counts a deferral as evidence; it is listed among the
#: classification markers so a deferral comment reads as *one* artifact instead of
#: being classified by whichever marker its prose happens to name.
DEFERRAL_MARKER = "keel.deferral.v1"
SHIP_ASSESSMENT_HEADING = "### \U0001f6a2 keel ship"
#: The banner the ``keel ship`` CLI prints above its own summary. An assessment pasted
#: raw — without the workflow's Markdown heading — leads with this line instead, so both
#: forms identify the comment. See :func:`_is_ship_assessment`.
SHIP_ASSESSMENT_BANNER = "keel ship \u2014"
DEFAULT_WAIVER_LABEL = "keel:evidence-waived"
TRUSTED_AUTHOR_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
TRUSTED_SHIP_ASSESSMENT_BOTS = frozenset({"github-actions", "github-actions[bot]"})

#: Every marker that *classifies* an evidence comment. Order is the order findings
#: report them in, so a malformed-header message is byte-stable.
CLASSIFICATION_MARKERS: tuple[str, ...] = (
    REVIEW_VERDICT_MARKER,
    JURY_VERDICT_MARKER,
    SHIP_PROVENANCE_MARKER,
    closure.CLOSURE_SCHEMA_VERSION,
    DEFERRAL_MARKER,
)

#: The finding raised for a comment whose header names more than one marker.
MALFORMED_MARKER_FINDING = "malformed-evidence-comment"

#: The header fields keel reads off an evidence comment. Closed by design: an
#: unlisted ``key: value`` line is prose, and prose ends the header block (#932).
#: ``panelists`` joins ``vendors`` as a jury-verdict field, because the size of a
#: panel that *is* the review sets the required verdict count (#1015).
_FIELD_RE = re.compile(
    r"^\s*(?P<key>reviewer|head|vendor|model|vendors|panelists)\s*:\s*(?P<value>\S+)\s*$",
    re.IGNORECASE,
)
_HEADER_LINE_RE = re.compile(r"^[A-Za-z0-9_-]+\s*:")
_SHIP_BRANCH_RE = re.compile(r"^(feature|fix|chore|docs|test)/issue-\d+(?:-|$)")
#: The exact wrapper a marker line may wear. Every keel renderer emits its marker as
#: the whole first line, in one of exactly two shapes: bare
#: (``artifacts.render_review_verdict`` / ``render_jury_verdict`` /
#: ``render_ship_provenance``) or wrapped in an HTML comment so it renders invisibly
#: (``closure.render_closure_comment``).
#:
#: These are matched literally, never with a regex. A pattern that treats ``-->`` as
#: *the* comment terminator is wrong about HTML — a browser also ends a comment at
#: ``--!>`` — and a classifier that disagrees with the renderer about where a comment
#: ends is exactly the confusion this module exists to remove (CodeQL
#: ``py/bad-tag-filter``). keel does not need to parse HTML: it needs to recognise the
#: one shape it writes, and refuse everything else.
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"

STATUS_PASS = "pass"
STATUS_WAITING = "waiting"
STATUS_FAIL = "fail"
STATUSES = (STATUS_PASS, STATUS_WAITING, STATUS_FAIL)

# Evidence phases. An artifact is required in the phase that produces it, mirroring
# the step mapping stepverifier already applies (review -> s7, jury -> s8, closure ->
# s12). The merge gate at s10 asks for PHASE_PRE_MERGE, because the closure comment
# is a post-merge record and requiring it at s10 makes the backbone unsatisfiable.
PHASE_PRE_MERGE = "pre-merge"
PHASE_POST_MERGE = "post-merge"
PHASE_ALL = "all"
PHASES = (PHASE_PRE_MERGE, PHASE_POST_MERGE, PHASE_ALL)


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str
    required: bool
    description: str
    phase: str = PHASE_PRE_MERGE

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "required": self.required,
            "description": self.description,
            "phase": self.phase,
        }


def gate_active(labels: Sequence[str] | None, gate_label: str) -> bool:
    """Return whether ``gate_label`` is present in ``labels`` (None/empty -> False).

    An empty ``gate_label`` is never active, so a misconfigured (blank) label can
    never silently match — the schema also forbids an empty ``evidence_gate_label``.
    """
    if not gate_label:
        return False
    return gate_label in set(labels or ())


def gate_decision(
    labels: Sequence[str] | None,
    gate_label: str,
    *,
    waiver_label: str = DEFAULT_WAIVER_LABEL,
    head_ref: str | None = None,
    pr_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    ledger_records: Sequence[object] | None = None,
) -> dict[str, Any]:
    """Return the fail-closed evidence-gate arming decision.

    Ship provenance arms the gate by default. The only disarm path is an explicit
    waiver label applied by an operator; the legacy gate label remains an
    additional arming signal for already-installed workflows.

    The signals are consulted in this order, and the order is part of the contract
    (documented in ``docs/keel/evidence.md``):

    1. ``operator-waiver-label`` — the one sanctioned disarm, checked first so an
       explicit waiver is never shadowed by an arming signal.
    2. ``gate-label`` — the legacy opt-in label.
    3. ``ship-provenance-comment`` — a trusted PR comment carrying
       :data:`SHIP_PROVENANCE_MARKER`, which a live ship run posts as soon as the PR
       exists. **Ahead of the branch regex on purpose** (#1013): the marker is written
       by the run, the branch name is written by whoever typed it, and a ship run that
       named its branch ``fix/2467-slug`` used to read as a non-keel PR.
    4. ``ship-branch`` — the legacy branch-name fallback for runs that predate the
       marker. Kept, but it is no longer the signal keel relies on.
    5. ``ship-assessment-comment`` / ``review-verdict-marker`` / ``ship-run-ledger`` —
       the remaining after-the-fact traces, unchanged.

    Nothing was removed: every path that armed the gate before still arms it.
    """
    label_set = set(labels or ())
    if waiver_label and waiver_label in label_set:
        return _gate_decision(False, "operator-waiver-label", waiver_label, waived=True)
    if gate_active(labels, gate_label):
        return _gate_decision(True, "gate-label", gate_label)
    if _has_trusted_ship_provenance(pr_comments or []):
        return _gate_decision(True, "ship-provenance-comment", SHIP_PROVENANCE_MARKER)
    if head_ref and _SHIP_BRANCH_RE.search(head_ref):
        return _gate_decision(True, "ship-branch", head_ref)
    if _has_trusted_ship_assessment(pr_comments or []):
        return _gate_decision(True, "ship-assessment-comment", SHIP_ASSESSMENT_HEADING)
    if _has_trusted_review_marker([*(pr_comments or []), *(pr_reviews or [])]):
        return _gate_decision(True, "review-verdict-marker", REVIEW_VERDICT_MARKER)
    if ledger_records:
        return _gate_decision(True, "ship-run-ledger", "ship_run")
    return _gate_decision(False, "no-ship-provenance", None)


def _gate_decision(
    enforced: bool,
    reason: str,
    source: str | None,
    *,
    waived: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enforced": enforced,
        "waived": waived,
        "reason": reason,
        "source": source,
    }


def _has_trusted_ship_provenance(items: list[dict[str, Any]]) -> bool:
    """True when a trusted PR comment carries the ship-provenance marker.

    Trust is the same fail-closed ``author_association`` check every other evidence
    source uses: an anonymous drive-by comment must not be able to arm — or, more to
    the point, to *look* like it armed — the gate.
    """
    return any(
        _is_trusted_source(item, enforced=True)
        and marker_in_header(_body(item)) == SHIP_PROVENANCE_MARKER
        for item in items
    )


def _has_trusted_ship_assessment(items: list[dict[str, Any]]) -> bool:
    return any(
        _is_ship_assessment_source(item) and _is_ship_assessment(_body(item)) for item in items
    )


def _is_ship_assessment_source(item: dict[str, Any]) -> bool:
    if _is_trusted_source(item, enforced=True):
        return True
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    login = user.get("login") if isinstance(user.get("login"), str) else None
    return bool(login and login.lower() in TRUSTED_SHIP_ASSESSMENT_BOTS)


def contract_as_dict(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
    enforced: bool = True,
    deferrals: tuple[str, ...] = (),
    phase: str = PHASE_ALL,
) -> dict[str, Any]:
    """Return the required evidence set derived from review/jury flags."""
    return {
        "schema_version": SCHEMA_VERSION,
        "enforced": enforced,
        "phase": phase,
        "source": "review_merge_contract + closure_comment",
        "dry_run_disables_gating": True,
        "fail_closed": True,
        "require_distinct_vendors": _require_distinct_vendors(review_contract),
        "accepted_sources": {
            "closure": ("trusted issue/PR comments carrying keel.closure-comment.v1"),
            "review": (
                "trusted PR review/comment carrying keel.review-verdict.v1 and current head"
            ),
            "jury": "trusted PR comment carrying keel.jury-verdict.v1 and current head",
        },
        "not_accepted": [
            "pull_request_body",
            "chat_summary",
            "untrusted_public_comment",
            "keel_ship_assessment_comment",
        ],
        "deferrals": list(deferrals),
        "required": [
            item.as_dict()
            for item in required_items(review_contract, dry_run=False, enforced=enforced)
        ],
        "active_required": [
            item.as_dict()
            for item in required_items(
                review_contract, dry_run=dry_run, enforced=enforced, phase=phase
            )
        ],
    }


def required_items(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
    enforced: bool = True,
    phase: str = PHASE_ALL,
) -> tuple[EvidenceItem, ...]:
    """Return the tier/flag-derived evidence requirements for ``phase``.

    ``phase`` selects which artifacts are in scope: ``pre-merge`` covers the
    review verdicts and a gating jury verdict (everything that must exist before
    s10 authorizes a merge), ``post-merge`` covers the closure comments s11
    posts, and ``all`` — the default, so existing callers are unchanged — covers
    both. An unknown phase raises, so a typo cannot silently drop requirements.
    """
    if phase not in PHASES:
        raise ValueError(f"unknown evidence phase {phase!r}; expected one of {', '.join(PHASES)}")
    if dry_run or not enforced:
        return ()
    reviewers = review_contract.get("reviewers")
    reviewer_count = reviewers.get("count") if isinstance(reviewers, dict) else 0
    reviewer_count = reviewer_count if isinstance(reviewer_count, int) and reviewer_count > 0 else 0
    jury = review_contract.get("jury")
    jury_required = (
        isinstance(jury, dict) and bool(jury.get("enabled")) and jury.get("mode") == "gating"
    )
    items: list[EvidenceItem] = [
        EvidenceItem(
            "closure-comment-pr",
            "closure",
            True,
            "PR conversation comment with keel.closure-comment.v1 marker",
            PHASE_POST_MERGE,
        ),
        EvidenceItem(
            "closure-comment-issue",
            "closure",
            True,
            "Linked issue comment with keel.closure-comment.v1 marker",
            PHASE_POST_MERGE,
        ),
    ]
    # A jury panel's verdicts are panelist ballots mapped onto s7 evidence by
    # `keel review --from-jury` (#1015): same marker, same head binding, same
    # requirement. The description names the panel that produced them so a
    # missing one sends the operator to the jury run rather than to a host
    # reviewer that was never dispatched.
    review_description = (
        "Distinct posted ai-jury panelist verdict for the current PR"
        if review_panel(review_contract) == JURY_PANEL
        else "Distinct posted s7 reviewer verdict for the current PR"
    )
    for index in range(1, reviewer_count + 1):
        items.append(
            EvidenceItem(
                f"review-verdict-{index}",
                "review",
                True,
                review_description,
                PHASE_PRE_MERGE,
            )
        )
    if jury_required:
        items.append(
            EvidenceItem(
                "jury-verdict",
                "jury",
                True,
                "Posted gating jury verdict comment for the current PR",
                PHASE_PRE_MERGE,
            )
        )
    if phase == PHASE_ALL:
        return tuple(items)
    return tuple(item for item in items if item.phase == phase)


def verify(
    review_contract: dict[str, Any],
    *,
    pr_comments: list[dict[str, Any]] | None = None,
    issue_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    pr_body: str | None = None,
    pr_title: str = "",
    pr_labels: Sequence[str] | None = None,
    head_sha: str | None = None,
    ledger_record: dict[str, Any] | None = None,
    dry_run: bool = False,
    enforced: bool = True,
    deferrals: tuple[str, ...] = (),
    phase: str = PHASE_ALL,
    require_armed: bool = False,
    waived: bool = False,
) -> dict[str, Any]:
    """Verify required evidence artifacts and return a deterministic report.

    ``phase`` narrows the requirement set to the artifacts that phase produces;
    see :func:`required_items`. The s10 merge gate passes ``pre-merge`` so it
    does not demand the closure comments s11 has not written yet.

    ``require_armed`` closes the vacuous-pass hole: with the gate unarmed there
    are no requirements, so the report would otherwise pass without having
    checked anything, and a green result could not be told apart from "could not
    tell whether this was a ship run". When set, an unarmed gate is a blocking
    finding instead. A deliberately non-ship PR still goes green through the
    operator waiver label, which disarms explicitly rather than by accident.

    When ``ledger_record`` is the ship_run record for this PR, a closure comment
    only counts when its content matches the canonical render of that record
    (closure-comment fidelity). Without a record the marker-only behavior holds.

    Every comment is classified by :func:`marker_in_header` — the marker on its
    header line, never one quoted in its prose. A header naming two markers is
    malformed: it is excluded from every count and reported as an advisory
    ``malformed-evidence-comment`` finding.

    When the gate is active, ``pr_labels`` are additionally checked for the
    mandatory ``agent:<vendor>`` attribution label (and cross-checked against the
    ledger implementer vendor when a record is present); see
    :func:`attribution_check`. The labels are separately checked against keel's own
    vocabulary — what :func:`keel.agents.attribution` produces from the ledger's
    ``actors.implementer`` — by :func:`attribution_vocabulary_check`, which catches a
    hand-composed label that happens to agree with a hand-written ledger value.
    """
    del pr_body  # Explicitly not accepted as evidence.
    items = required_items(review_contract, dry_run=dry_run, enforced=enforced, phase=phase)
    deferred = set(deferrals)
    counts = _evidence_counts(
        pr_comments=pr_comments or [],
        issue_comments=issue_comments or [],
        pr_reviews=pr_reviews or [],
        head_sha=head_sha,
        enforced=enforced,
        ledger_record=ledger_record,
    )
    findings = _run_context_findings(
        pr_comments=pr_comments or [],
        issue_comments=issue_comments or [],
        enforced=enforced,
        ledger_record=ledger_record,
    )
    mismatch = _closure_mismatch_scopes(
        pr_comments=pr_comments or [],
        issue_comments=issue_comments or [],
        enforced=enforced,
        ledger_record=ledger_record,
    )
    results = []
    for item in items:
        present = _is_present(item, counts)
        is_deferred = item.id in deferred or item.kind in deferred or "all" in deferred
        ok = present or is_deferred
        results.append(
            {
                "id": item.id,
                "kind": item.kind,
                "required": item.required,
                "present": present,
                "deferred": is_deferred,
                "ok": ok,
                "reason": None if ok else _result_reason(item, mismatch),
            }
        )
    missing = [result["id"] for result in results if not result["ok"]]
    distinct = _distinct_vendor_finding(
        review_contract,
        items=items,
        deferred=deferred,
        pr_comments=pr_comments or [],
        pr_reviews=pr_reviews or [],
        head_sha=head_sha,
        enforced=enforced,
    )
    if distinct is not None:
        findings = [*findings, distinct]
    substance = _verdict_substance_findings(
        [*(pr_comments or []), *(pr_reviews or [])],
        head_sha=head_sha,
        enforced=enforced,
        pr_title=pr_title,
    )
    findings = [*findings, *substance]
    findings = [
        *findings,
        *_malformed_marker_findings(
            [*(pr_comments or []), *(issue_comments or []), *(pr_reviews or [])],
            enforced=enforced,
        ),
    ]
    attribution = _attribution_finding(
        pr_labels=pr_labels,
        enforced=enforced and not dry_run,
        ledger_record=ledger_record,
    )
    if attribution is not None:
        findings = [*findings, attribution]
    vocabulary = _attribution_vocabulary_finding(
        pr_labels=pr_labels,
        enforced=enforced and not dry_run,
        ledger_record=ledger_record,
    )
    if vocabulary is not None:
        findings = [*findings, vocabulary]
    unarmed = _unarmed_finding(
        enforced=enforced,
        dry_run=dry_run,
        require_armed=require_armed,
        waived=waived,
    )
    if unarmed is not None:
        findings = [*findings, unarmed]
    blocking_findings = [finding for finding in findings if finding["severity"] == "major"]
    has_mismatch = bool(mismatch)
    if not missing and not blocking_findings:
        status = STATUS_PASS
    elif blocking_findings or has_mismatch:
        status = STATUS_FAIL
    else:
        status = STATUS_WAITING
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "dry_run": dry_run,
        "enforced": enforced,
        "phase": phase,
        "required_count": len(items),
        "missing": missing,
        "results": results,
        "counts": counts,
        "findings": findings,
    }


def _require_distinct_vendors(review_contract: dict[str, Any]) -> bool:
    reviewers = review_contract.get("reviewers")
    return bool(reviewers.get("require_distinct_vendors")) if isinstance(reviewers, dict) else False


def review_panel(review_contract: dict[str, Any]) -> str:
    """Who the reviewers are on this contract: ``reviewers`` or ``jury`` (#1015).

    A missing or malformed ``reviewers`` block reads as the host bench, which is
    the stricter of the two answers everywhere this is asked.
    """
    reviewers = review_contract.get("reviewers")
    panel = reviewers.get("panel") if isinstance(reviewers, dict) else None
    return panel if isinstance(panel, str) and panel else "reviewers"


def _minimum_jury_vendors(review_contract: dict[str, Any]) -> int:
    """``jury.minimum_vendors`` from the contract, with the schema floor as fallback."""
    jury = review_contract.get("jury")
    minimum = jury.get("minimum_vendors") if isinstance(jury, dict) else None
    if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > 0:
        return minimum
    return DEFAULT_MINIMUM_JURY_VENDORS


def _verdict_substance_findings(
    items: list[dict[str, Any]],
    *,
    head_sha: str | None,
    enforced: bool,
    pr_title: str,
) -> list[dict[str, Any]]:
    """One finding per verdict refused for naming nothing concrete (#926).

    Reported rather than dropped. A verdict silently not counted surfaces as
    "missing required evidence: review-verdict-2", which sends the operator
    looking for a comment that is sitting right there — the reason has to say
    the verdict was read and found to be a receipt.

    ``minor`` when the gate is not enforced, mirroring the other content
    findings: an advisory run should say what it saw without failing.
    """
    _, rejected = _review_evidence_keys_and_rejections(
        items, head_sha=head_sha, enforced=enforced, pr_title=pr_title
    )
    return [
        {
            "id": "review-verdict-insubstantial",
            "severity": "major" if enforced else "minor",
            "kind": "review",
            "message": f"{key}: {reason}.",
        }
        for key, reason in sorted(rejected)
    ]


def _malformed_marker_findings(
    items: list[dict[str, Any]],
    *,
    enforced: bool,
) -> list[dict[str, Any]]:
    """One finding per trusted comment whose header names more than one marker.

    Such a header does not say which artifact the comment is, so
    :func:`marker_in_header` refuses to classify it and the comment counts toward
    nothing. Excluding it silently would reproduce the failure #926 named — a
    comment sitting right there on the PR, reported as missing evidence — so the
    exclusion is stated instead of inferred.

    ``minor``, never blocking: the comment is malformed, not fraudulent, and the
    requirement it failed to satisfy already fails on its own.
    """
    findings: list[dict[str, Any]] = []
    for item in items:
        if not _is_trusted_source(item, enforced=enforced):
            continue
        markers = header_markers(_body(item))
        if len(markers) < 2:
            continue
        findings.append(
            {
                "id": MALFORMED_MARKER_FINDING,
                "severity": "minor",
                "kind": "evidence",
                "message": (
                    "Comment header carries more than one keel marker "
                    f"({', '.join(markers)}); it is excluded from evidence."
                ),
            }
        )
    return findings


def _distinct_vendor_finding(
    review_contract: dict[str, Any],
    *,
    items: tuple[EvidenceItem, ...],
    deferred: set[str],
    pr_comments: list[dict[str, Any]],
    pr_reviews: list[dict[str, Any]],
    head_sha: str | None,
    enforced: bool,
) -> dict[str, Any] | None:
    """Return a blocking finding when the optional vendor-distinctness check fails.

    Off by default: ``None`` unless ``reviewers.require_distinct_vendors`` is set
    on the contract. Skipped when review evidence is deferred so the knob never
    overrides an explicit deferral.
    """
    if not _require_distinct_vendors(review_contract):
        return None
    if "review" in deferred or "all" in deferred:
        return None
    required = sum(1 for item in items if item.kind == "review" and item.id not in deferred)
    if required <= 0:
        return None
    provenance = _review_vendor_provenance(
        [*pr_comments, *pr_reviews],
        head_sha=head_sha,
        enforced=enforced,
    )
    if review_panel(review_contract) == JURY_PANEL:
        result = panel_vendor_check(
            list(provenance.values()),
            required_count=required,
            minimum_vendors=_minimum_jury_vendors(review_contract),
        )
    else:
        result = distinct_vendor_check(list(provenance.values()), required_count=required)
    if result["ok"]:
        return None
    return {
        "id": "review-vendor-distinctness",
        "severity": "major",
        "kind": "review",
        "message": f"require_distinct_vendors: {result['reason']}.",
    }


_CLOSURE_MISMATCH_REASON = "closure comment does not match the ship_run ledger record"


def _result_reason(item: EvidenceItem, mismatch: set[str]) -> str:
    if item.id == "closure-comment-pr" and "pr" in mismatch:
        return _CLOSURE_MISMATCH_REASON
    if item.id == "closure-comment-issue" and "issue" in mismatch:
        return _CLOSURE_MISMATCH_REASON
    return f"missing required evidence: {item.id}"


def _closure_mismatch_scopes(
    *,
    pr_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    enforced: bool,
    ledger_record: dict[str, Any] | None,
) -> set[str]:
    """Return scopes ({"pr"}/{"issue"}) where a marker closure mismatched the ledger.

    A scope is reported only when a trusted marker-bearing closure exists but none
    of them match the record — so a stale comment alongside a correct re-post does
    not produce a misleading mismatch reason.
    """
    if ledger_record is None:
        return set()
    scopes: set[str] = set()
    for scope, comments in (("pr", pr_comments), ("issue", issue_comments)):
        markered = [
            comment
            for comment in comments
            if _is_trusted_source(comment, enforced=enforced)
            and _has_closure_marker(_body(comment))
        ]
        if markered and not any(
            closure_body_matches_record(_body(comment), ledger_record) for comment in markered
        ):
            scopes.add(scope)
    return scopes


def _evidence_counts(
    *,
    pr_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    pr_reviews: list[dict[str, Any]],
    head_sha: str | None = None,
    enforced: bool = True,
    ledger_record: dict[str, Any] | None = None,
) -> dict[str, int]:
    review_keys = _review_evidence_keys(
        [*pr_comments, *pr_reviews],
        head_sha=head_sha,
        enforced=enforced,
    )
    return {
        "closure_pr": sum(
            _is_closure_comment(comment, enforced=enforced, record=ledger_record)
            for comment in pr_comments
        ),
        "closure_issue": sum(
            _is_closure_comment(comment, enforced=enforced, record=ledger_record)
            for comment in issue_comments
        ),
        "review_verdict": len(review_keys),
        "jury_verdict": sum(
            _is_jury_verdict(comment, head_sha=head_sha, enforced=enforced)
            for comment in pr_comments
        ),
    }


def _is_present(item: EvidenceItem, counts: dict[str, int]) -> bool:
    if item.id == "closure-comment-pr":
        return counts["closure_pr"] >= 1
    if item.id == "closure-comment-issue":
        return counts["closure_issue"] >= 1
    if item.kind == "review":
        index = int(item.id.rsplit("-", 1)[1])
        return counts["review_verdict"] >= index
    if item.id == "jury-verdict":
        return counts["jury_verdict"] >= 1
    return False


def _body(item: dict[str, Any]) -> str:
    body = item.get("body")
    return body if isinstance(body, str) else ""


def _header_line(body: str) -> str:
    """``body``'s header line: its first *non-empty* line, stripped.

    Leading blank lines are skipped rather than treated as the end, for the same
    reason :func:`_fields` skips them — a GitHub comment body routinely begins
    with a newline. Everything after that line is prose.
    """
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        return line
    return ""


def _unwrap_html_comment(line: str) -> str:
    """Strip one literal ``<!-- … -->`` wrapper from ``line``, or return it unchanged.

    Deliberately not a regex and deliberately not an HTML parser: the only
    wrapper keel has to recognise is the one
    :func:`keel.closure.render_closure_comment` writes. Anything else — an
    unterminated ``<!--``, a ``--!>`` close, a second wrapper on the same line —
    is left intact, so the marker check below sees the delimiters as tokens and
    refuses to classify the comment. Failing to recognise a hand-rolled wrapper
    costs a comment its classification; guessing at one would let a body render
    as an invisible comment while counting as evidence.
    """
    if (
        line.startswith(_HTML_COMMENT_OPEN)
        and line.endswith(_HTML_COMMENT_CLOSE)
        and len(line) >= len(_HTML_COMMENT_OPEN) + len(_HTML_COMMENT_CLOSE)
    ):
        return line[len(_HTML_COMMENT_OPEN) : -len(_HTML_COMMENT_CLOSE)].strip()
    return line


def header_markers(body: str) -> tuple[str, ...]:
    """Return the distinct :data:`CLASSIFICATION_MARKERS` ``body``'s header carries.

    The header line, once unwrapped, must consist of markers and nothing else —
    that is exactly what every renderer emits, and it is what separates a marker
    line from a sentence that happens to name one. So this is empty for an
    ordinary comment (including one whose first line *mentions* a marker in
    prose, and one wearing a wrapper keel does not write), one entry for a
    well-formed artifact, and two or more for a malformed one, which
    :func:`marker_in_header` refuses to classify and
    :func:`_malformed_marker_findings` reports.
    """
    tokens = _unwrap_html_comment(_header_line(body)).split()
    if not tokens or any(token not in CLASSIFICATION_MARKERS for token in tokens):
        return ()
    return tuple(marker for marker in CLASSIFICATION_MARKERS if marker in tokens)


def marker_in_header(body: str) -> str | None:
    """Return the single keel marker ``body`` is anchored to, or ``None`` (#1026).

    **The header block is the only place a marker classifies a comment.** A marker
    further down is prose — a reviewer writing "I checked the jury-verdict marker
    handling" is quoting a string, not filing a jury verdict. Testing
    ``MARKER in body`` could not tell the two apart: two `keel.review-verdict.v1`
    comments whose scope mentioned ``keel.jury-verdict.v1`` were counted as
    ``jury_verdict: 2, review_verdict: 0``, and the review that happened was
    invisible to the gate.

    ``None`` for a comment that carries no marker *and* for one whose header
    carries several: a header naming two artifacts does not say which one it is,
    so it is excluded rather than counted for either.
    """
    markers = header_markers(body)
    return markers[0] if len(markers) == 1 else None


def _has_closure_marker(body: str) -> bool:
    return marker_in_header(body) == closure.CLOSURE_SCHEMA_VERSION


#: The idempotency marker ``keel post-comment`` appends to a posted body so a re-post
#: can find and edit its own comment. It is transport bookkeeping, not content, so it is
#: stripped before a closure body is compared to its canonical render.
#:
#: Matched in the *exact* form the transport emits — a run id, then the close, then end
#: of line. A permissive ``.*?`` would let a trusted author smuggle arbitrary text past
#: the verbatim comparison: an HTML comment ends at its first ``-->``, so anything after
#: that renders visibly on the page while the whole line still normalizes away.
RUN_ID_MARKER_RE = re.compile(r"^\s*<!--\s*keel\.run-id:\s*[\w.:@/+-]+\s*-->\s*$")


def _normalize_closure_body(body: str) -> str:
    """Normalize a closure body for content comparison.

    Robust to harmless formatting drift but sensitive to real content changes:
    trailing whitespace is stripped per line, runs of blank lines collapse to a
    single blank line, and leading/trailing blank lines are dropped.

    The transport's ``keel.run-id`` marker line is dropped too. Without that, closure
    fidelity and post-comment idempotency were mutually exclusive: the marker is what
    lets a re-post edit its own comment instead of duplicating, and its presence made
    the body differ from the canonical render.
    """
    lines = [line.rstrip() for line in body.splitlines() if not RUN_ID_MARKER_RE.match(line)]
    normalized: list[str] = []
    for line in lines:
        if not line and (not normalized or not normalized[-1]):
            continue
        normalized.append(line)
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized)


def closure_body_matches_record(body: str, record: dict[str, Any]) -> bool:
    """Return whether ``body`` matches the canonical render of ``record``."""
    expected = closure.render_closure_comment(record)
    return _normalize_closure_body(body) == _normalize_closure_body(expected)


def _is_closure_comment(
    item: dict[str, Any],
    *,
    enforced: bool = True,
    record: dict[str, Any] | None = None,
) -> bool:
    if not _is_trusted_source(item, enforced=enforced):
        return False
    if not _has_closure_marker(_body(item)):
        return False
    if record is None:
        return True
    return closure_body_matches_record(_body(item), record)


def _run_context_findings(
    *,
    pr_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    enforced: bool,
    ledger_record: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    comments = [*pr_comments, *issue_comments]
    findings: list[dict[str, Any]] = []
    for item in comments:
        if not _is_closure_comment(item, enforced=enforced, record=ledger_record):
            continue
        body = _body(item)
        if _has_empty_run_context(body):
            findings.append(
                {
                    "id": "run-context-empty",
                    "severity": "major" if enforced else "minor",
                    "kind": "closure",
                    "message": "Closure comment Run context is fully degraded.",
                }
            )
    return findings


def _has_empty_run_context(body: str) -> bool:
    if "### Run context" not in body:
        return False
    fields = _run_context_fields(body)
    return fields == {
        "host agent": "unknown",
        "transport": "unknown",
        "profile": "unknown",
        "jury": "off",
        "consent": "unknown (scopes: none)",
    }


def _run_context_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_block = False
    for line in body.splitlines():
        if line.strip() == "### Run context":
            in_block = True
            continue
        if in_block and line.startswith("### "):
            break
        if not in_block:
            continue
        match = re.match(r"^-\s+\*\*(?P<key>[^*]+):\*\*\s+(?P<value>.+?)\s*$", line)
        if match:
            fields[match.group("key").strip().lower()] = match.group("value").strip().lower()
    return fields


def _is_trusted_source(item: dict[str, Any], *, enforced: bool = True) -> bool:
    """Return whether GitHub marks this evidence source as trusted.

    Live GitHub comment/review payloads include ``author_association``. Enforced
    evidence fails closed when that field is absent because offline fixtures are
    agent-writable and must not manufacture trust. Untrusted explicit
    associations fail closed even if the author type is ``Bot``.
    """
    association = item.get("author_association")
    if association is None:
        return not enforced
    if isinstance(association, str) and association.upper() in TRUSTED_AUTHOR_ASSOCIATIONS:
        return True
    return False


def _is_ship_assessment(body: str) -> bool:
    """Whether ``body`` is a ship assessment comment, decided by its header (#1035).

    Header-anchored for the same reason markers are (#1026): this is consulted as an
    *exclusion* by :func:`_is_review_verdict_body` and :func:`_is_jury_verdict`, so a
    whole-body substring test let a reviewer disarm their own verdict by quoting the
    heading while describing what they reviewed ("the ``### \U0001f6a2 keel ship``
    comment claims the gates passed, but…"). The verdict was then silently uncounted
    and ``evidence-verify`` reported it missing from a PR it was sitting on.

    The heading is a Markdown heading rather than a versioned ``keel.*.v1`` marker, so
    it cannot join :data:`CLASSIFICATION_MARKERS`; it gets the same anchoring instead.
    A real assessment leads with the heading (the workflow writes it first) or with the
    CLI's own banner, so nothing that armed the gate through a genuine assessment
    comment stops arming it.
    """
    header = _header_line(body)
    return header.startswith(SHIP_ASSESSMENT_HEADING) or header.startswith(SHIP_ASSESSMENT_BANNER)


def count_review_verdicts(
    pr_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    *,
    head_sha: str | None = None,
    enforced: bool = True,
    pr_title: str = "",
) -> int:
    """Count distinct trusted review-verdict reviewers for a PR.

    This is the same evidence-side counting the verify report uses for the
    ``review`` items: it collapses idempotent re-posts by the same reviewer to
    one verdict and only counts trusted, head-bound verdicts. Reused by capture
    reconcile to cross-check the ledger's recorded reviewer count.
    """
    keys = _review_evidence_keys(
        [*(pr_comments or []), *(pr_reviews or [])],
        head_sha=head_sha,
        enforced=enforced,
        pr_title=pr_title,
    )
    return len(keys)


def _review_evidence_keys(
    items: list[dict[str, Any]],
    *,
    head_sha: str | None = None,
    enforced: bool = True,
    pr_title: str = "",
) -> set[str]:
    keys, _ = _review_evidence_keys_and_rejections(
        items, head_sha=head_sha, enforced=enforced, pr_title=pr_title
    )
    return keys


def _review_evidence_keys_and_rejections(
    items: list[dict[str, Any]],
    *,
    head_sha: str | None = None,
    enforced: bool = True,
    pr_title: str = "",
) -> tuple[set[str], list[tuple[str, str]]]:
    """Accepted reviewer keys, and the (key, reason) pairs refused for substance.

    Rejections are returned rather than dropped so the gate can *hold with a
    reason* — a verdict silently not counted would surface as "missing
    review-verdict-2", which sends the operator looking for a comment that is
    right there (#926).
    """
    keys: set[str] = set()
    rejected: list[tuple[str, str]] = []
    for item in items:
        if not _is_trusted_source(item, enforced=enforced):
            continue
        body = _body(item)
        if not _is_review_verdict_body(body):
            continue
        if not _matches_head(item, body, head_sha):
            continue
        key = _reviewer_key(item, body)
        ok, reason = verdict_substance(body, pr_title=pr_title)
        if not ok:
            rejected.append((key, reason))
            continue
        keys.add(key)
    # A reviewer who posted a thin verdict and then a real one is accepted: the
    # later comment is the review, and holding on the earlier one would make
    # correcting yourself impossible.
    return keys, [(key, why) for key, why in rejected if key not in keys]


def _review_vendor_provenance(
    items: list[dict[str, Any]],
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> dict[str, str | None]:
    """Map each accepted review-verdict reviewer-key to its declared vendor.

    The value is the lower-cased ``vendor:`` provenance for that verdict, or
    ``None`` when the verdict carries no vendor field. Keys mirror
    :func:`_review_evidence_keys`, so duplicate reviewer-keys collapse to one
    entry (idempotent re-posts do not inflate the vendor set).
    """
    provenance: dict[str, str | None] = {}
    for item in items:
        if not _is_trusted_source(item, enforced=enforced):
            continue
        body = _body(item)
        if not _is_review_verdict_body(body):
            continue
        if not _matches_head(item, body, head_sha):
            continue
        key = _reviewer_key(item, body)
        if key in provenance:
            continue
        vendor = _fields(body).get("vendor")
        provenance[key] = vendor.lower() if vendor else None
    return provenance


def distinct_vendor_check(
    vendors: Sequence[str | None],
    *,
    required_count: int,
) -> dict[str, Any]:
    """Pure vendor-distinctness check over review-verdict provenance.

    ``vendors`` is one entry per accepted review verdict: the declared vendor, or
    ``None`` when the verdict carries no vendor provenance. The check passes only
    when at least ``required_count`` verdicts each declare a vendor and those
    vendors are all distinct. It fails when a required verdict is missing vendor
    provenance, or when two required verdicts share a vendor.

    Returns ``{ok, reason, duplicated, missing_provenance}``. No I/O — fully
    unit-testable. A non-positive ``required_count`` always passes (nothing to
    require).
    """
    if required_count <= 0:
        return {"ok": True, "reason": None, "duplicated": [], "missing_provenance": 0}
    present = [vendor for vendor in vendors if vendor]
    missing = len(vendors) - len(present)
    seen: set[str] = set()
    duplicated: list[str] = []
    for vendor in present:
        if vendor in seen and vendor not in duplicated:
            duplicated.append(vendor)
        seen.add(vendor)
    if len(present) < required_count:
        return {
            "ok": False,
            "reason": "missing vendor provenance on required review verdict(s)",
            "duplicated": duplicated,
            "missing_provenance": missing,
        }
    if duplicated:
        return {
            "ok": False,
            "reason": f"review verdicts share a vendor: {', '.join(sorted(duplicated))}",
            "duplicated": sorted(duplicated),
            "missing_provenance": missing,
        }
    return {"ok": True, "reason": None, "duplicated": [], "missing_provenance": missing}


def panel_vendor_check(
    vendors: Sequence[str | None],
    *,
    required_count: int,
    minimum_vendors: int,
) -> dict[str, Any]:
    """Cross-vendor check for a **jury panel**, whose size the panel sets (#1015).

    :func:`distinct_vendor_check` asks for one distinct vendor per required
    verdict, which is the right question for a bench keel staffs: keel chose the
    seats, so keel can insist each one is a different vendor. It is the wrong
    question for a panel, where the *panel* chose the seats and three ballots from
    two vendors is a legitimate cross-vendor review — the same shape
    :data:`keel.ship.MINIMUM_JURY_VENDORS` already accepts as a gating jury.

    So the panel is held to the jury's own rule instead: every required ballot
    must declare a vendor, and the ballots together must span at least
    ``minimum_vendors`` distinct ones. A whole panel from one vendor is one
    opinion N times and fails, which is precisely what the strict check would
    have caught — the relaxation is only in *how many* distinct vendors are
    demanded, never in whether provenance is required at all.

    Returns the same ``{ok, reason, duplicated, missing_provenance}`` shape as
    :func:`distinct_vendor_check`, so a caller renders one finding either way.
    """
    if required_count <= 0:
        return {"ok": True, "reason": None, "duplicated": [], "missing_provenance": 0}
    present = [vendor for vendor in vendors if vendor]
    missing = len(vendors) - len(present)
    distinct = sorted(set(present))
    duplicated = sorted({vendor for vendor in present if present.count(vendor) > 1})
    if len(present) < required_count:
        return {
            "ok": False,
            "reason": "missing vendor provenance on required review verdict(s)",
            "duplicated": duplicated,
            "missing_provenance": missing,
        }
    if len(distinct) < minimum_vendors:
        return {
            "ok": False,
            "reason": (
                f"jury panel of {len(present)} ballot(s) spans "
                f"{len(distinct)} distinct vendor(s), below the minimum of {minimum_vendors}"
            ),
            "duplicated": duplicated,
            "missing_provenance": missing,
        }
    return {"ok": True, "reason": None, "duplicated": duplicated, "missing_provenance": missing}


def _label_values(labels: Sequence[str] | None, prefix: str) -> list[str]:
    """Lower-cased values of every ``<prefix><value>`` label, blanks dropped."""
    values: list[str] = []
    for label in labels or ():
        if not isinstance(label, str) or not label.startswith(prefix):
            continue
        value = label[len(prefix) :].strip().lower()
        if value:
            values.append(value)
    return values


def agent_label_vendors(labels: Sequence[str] | None) -> list[str]:
    """Return the lower-cased vendor slugs from every ``agent:<vendor>`` label.

    A blank vendor (a bare ``agent:`` label) is ignored. Order is preserved and
    duplicates are kept so callers can reason about the raw label set; this is a
    pure helper with no I/O.
    """
    return _label_values(labels, AGENT_LABEL_PREFIX)


def model_label_bases(labels: Sequence[str] | None) -> list[str]:
    """Return the lower-cased base slugs from every ``model:<base>`` label.

    The mirror of :func:`agent_label_vendors` for the second half of keel's
    attribution vocabulary. Same conventions: blanks dropped, order and duplicates
    preserved, no I/O.
    """
    return _label_values(labels, MODEL_LABEL_PREFIX)


def ledger_implementer(ledger_record: dict[str, Any] | None) -> str | None:
    """Return the raw ``actors.implementer`` string from a ship_run record, or ``None``.

    The full ``vendor`` / ``vendor:model`` value, not just the vendor half — the
    vocabulary check needs the model too. Blank/absent reads as ``None``. Pure.
    """
    if not isinstance(ledger_record, dict):
        return None
    actors = ledger_record.get("actors")
    implementer = actors.get("implementer") if isinstance(actors, dict) else None
    if not isinstance(implementer, str) or not implementer.strip():
        return None
    return implementer.strip()


def ledger_implementer_vendor(ledger_record: dict[str, Any] | None) -> str | None:
    """Return the implementer's vendor slug from a ship_run ``ledger_record``.

    The ledger stores the effective implementer as a codename or ``vendor:model``
    string under ``actors.implementer``; the vendor is the part before the first
    ``:``. Returns ``None`` when no record, no implementer, or a blank implementer
    is recorded so the cross-check can degrade to presence-only. Pure — no I/O.
    """
    implementer = ledger_implementer(ledger_record)
    if implementer is None:
        return None
    vendor, _ = agents.split_delegate(implementer)
    vendor = vendor.strip().lower()
    return vendor or None


def attribution_check(
    labels: Sequence[str] | None,
    *,
    implementer_vendor: str | None = None,
) -> dict[str, Any]:
    """Pure attribution-label check over a PR's labels and the ledger implementer.

    Two layers, both fail-closed only on a real contradiction:

    * **Presence** — at least one non-blank ``agent:<vendor>`` label must exist.
      Missing one is a ``missing-label`` finding.
    * **Cross-check** — when ``implementer_vendor`` is known (a ship_run record
      recorded an implementer), one of the PR's ``agent:*`` vendors must match it.
      A mismatch is a ``vendor-mismatch`` finding. When ``implementer_vendor`` is
      ``None`` (no record / no implementer) only the presence layer runs, so PRs
      that predate attribution recording are not broken.

    Returns ``{ok, reason, label_vendors, implementer_vendor}``. No I/O.
    """
    label_vendors = agent_label_vendors(labels)
    implementer = implementer_vendor.strip().lower() if implementer_vendor else None
    if not label_vendors:
        return {
            "ok": False,
            "reason": "missing-label",
            "label_vendors": label_vendors,
            "implementer_vendor": implementer,
        }
    if implementer is not None and implementer not in label_vendors:
        return {
            "ok": False,
            "reason": "vendor-mismatch",
            "label_vendors": label_vendors,
            "implementer_vendor": implementer,
        }
    return {
        "ok": True,
        "reason": None,
        "label_vendors": label_vendors,
        "implementer_vendor": implementer,
    }


def attribution_vocabulary_check(
    labels: Sequence[str] | None,
    *,
    implementer: str | None,
) -> dict[str, Any]:
    """Check a PR's attribution labels against keel's own vocabulary (#1013).

    :func:`attribution_check` compares the label's *vendor* with the ledger's
    *vendor*. That is a comparison of two hand-written strings: when the host wrote
    ``agent:gemini`` on the PR **and** ``gemini:gemini-3.8-flash-high`` into the
    ledger, the two agreed and the gate passed — while keel's own vocabulary for that
    run is ``agent:agy`` / ``model:gemini-3``. This check closes that hole by deriving
    the expected labels from :func:`keel.agents.attribution` instead of comparing the
    prose to itself.

    Only labels the PR actually carries are judged: a missing ``agent:`` label is
    :func:`attribution_check`'s ``missing-label`` finding and is not repeated here,
    and a ledger implementer with no model (``claude``) yields no expected
    ``model:`` label, so ``model:`` labels are left alone in that case.

    Returns ``{ok, checked, reason, expected, actual, implementer}``. ``checked`` is
    ``False`` when there was nothing to compare against — no ledger record, no
    recorded implementer — so a caller can tell "agrees" from "could not tell".
    Pure — no I/O.
    """
    expected = agents.attribution_from_implementer(implementer)
    actual = {
        "agent_labels": agent_label_vendors(labels),
        "model_labels": model_label_bases(labels),
    }
    if expected is None:
        return {
            "ok": True,
            "checked": False,
            "reason": "no-implementer",
            "expected": None,
            "actual": actual,
            "implementer": None,
        }
    recorded = implementer.strip() if isinstance(implementer, str) else None
    result = {
        "ok": True,
        "checked": True,
        "reason": None,
        "expected": dict(expected),
        "actual": actual,
        "implementer": recorded,
    }
    expected_agent = expected["agent_label"][len(AGENT_LABEL_PREFIX) :]
    expected_model = expected["model_label"]
    if actual["agent_labels"] and expected_agent not in actual["agent_labels"]:
        result["ok"] = False
        result["reason"] = "agent-label"
        return result
    if expected_model is not None:
        base = expected_model[len(MODEL_LABEL_PREFIX) :]
        if actual["model_labels"] and base not in actual["model_labels"]:
            result["ok"] = False
            result["reason"] = "model-label"
    return result


def _attribution_vocabulary_finding(
    *,
    pr_labels: Sequence[str] | None,
    enforced: bool,
    ledger_record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the blocking ``attribution-vocabulary`` finding, or ``None``.

    Skips — never fails — when the gate is inactive, when labels were not fetched, or
    when no ledger record named an implementer: the check needs a recorded implementer
    to derive the expected labels from, and refusing a PR because keel could not read
    its own ledger would be a fail-closed rule with nothing behind it.
    """
    if not enforced or pr_labels is None:
        return None
    result = attribution_vocabulary_check(
        pr_labels,
        implementer=ledger_implementer(ledger_record),
    )
    if result["ok"]:
        return None
    expected = result["expected"]
    labels = ", ".join(
        label for label in (expected["agent_label"], expected["model_label"]) if label
    )
    observed = ", ".join(
        [
            *(f"{AGENT_LABEL_PREFIX}{value}" for value in result["actual"]["agent_labels"]),
            *(f"{MODEL_LABEL_PREFIX}{value}" for value in result["actual"]["model_labels"]),
        ]
    )
    return {
        "id": "attribution-vocabulary",
        "severity": "major",
        "kind": "attribution",
        "message": (
            f"PR attribution labels ({observed}) are not keel's vocabulary for ledger "
            f"implementer {result['implementer']!r}. Expected: {labels}. "
            "Obtain labels from `keel attribution` instead of composing them by hand."
        ),
    }


def _unarmed_finding(
    *,
    enforced: bool,
    dry_run: bool,
    require_armed: bool,
    waived: bool,
) -> dict[str, Any] | None:
    """Return a blocking finding when the gate was never armed, else ``None``.

    Opt-in via ``require_armed`` so existing callers keep today's behavior. An
    unarmed gate derives no requirements, so without this the report passes
    having verified nothing — indistinguishable from a genuine pass. Skipped
    under ``dry_run``, where producing no evidence is the expected outcome, and
    when ``waived``: an operator disarming the gate on purpose is the sanctioned
    way out, and the whole point is to separate that from arming by accident.
    """
    if not require_armed or dry_run or enforced or waived:
        return None
    return {
        "id": "gate-unarmed",
        "severity": "major",
        "kind": "arming",
        "message": (
            "Evidence gate is not armed, so no requirements were checked. Arm it via ship "
            "provenance (the keel.ship-provenance.v1 comment a live run posts on its PR, a "
            "ship branch, a posted review verdict, the ship-run ledger, or the gate label), "
            "or disarm deliberately with the operator waiver label."
        ),
    }


def _attribution_finding(
    *,
    pr_labels: Sequence[str] | None,
    enforced: bool,
    ledger_record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a blocking attribution finding when the gate is active, else ``None``.

    Only runs when the evidence gate is active (``enforced``) *and* PR labels were
    actually fetched (``pr_labels is not None``): the presence check is cheap and
    default-on, while the vendor cross-check engages only when the ledger recorded
    an implementer vendor. Degrades gracefully — labels not available skips the
    check entirely, no record means presence-only, and a gate-inactive run skips
    the check (back-compat with callers that never pass labels).
    """
    if not enforced or pr_labels is None:
        return None
    implementer_vendor = ledger_implementer_vendor(ledger_record)
    result = attribution_check(pr_labels, implementer_vendor=implementer_vendor)
    if result["ok"]:
        return None
    if result["reason"] == "missing-label":
        message = "PR is missing a mandatory agent:<vendor> attribution label."
    else:
        message = (
            "PR agent:<vendor> attribution "
            f"({', '.join(result['label_vendors'])}) does not match the ship_run "
            f"ledger implementer vendor ({result['implementer_vendor']})."
        )
    return {
        "id": "attribution-label",
        "severity": "major",
        "kind": "attribution",
        "message": message,
    }


#: A concrete thing a review can point at: a path, a ``path:line``, a backticked
#: symbol, or a dotted/called identifier. Presence of *structure*, never a
#: judgement about whether the review was good — the same line ai-jury's
#: ``emitted_findings_block()`` draws.
_VERDICT_ANCHORS = (
    re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}:\d+"),  # path/to/file.py:42
    re.compile(r"[\w-]+/[\w./-]+\.[A-Za-z0-9]{1,5}\b"),  # src/keel/thing.py
    re.compile(r"`[^`\n]{2,}`"),  # `a_symbol`, `--a-flag`
    re.compile(r"\b\w+\.\w+\(\)"),  # module.function()
)

#: The escape hatch the issue insists on: a genuinely clean review must stay
#: expressible. "Checked X, Y and Z; found nothing" is a real review outcome and
#: must not be forced to invent an anchor.
_VERDICT_CHECKED_CLAUSE = re.compile(r"\bchecked\b[^.\n]{8,}", re.IGNORECASE)

#: Below this share of novel words, the prose is the PR title said again. The
#: observed shape was `Reviewed <title>: <generic affirmation>` — 75 of 75
#: verdicts across 25 PRs (#926).
_VERDICT_NOVELTY_FLOOR = 0.35

_WORD = re.compile(r"[a-z0-9]+")


def _verdict_prose(body: str) -> str:
    """The verdict's own words: header block, markers and HTML comments removed."""
    lines = body.splitlines()
    start = 0
    for index, raw in enumerate(lines):
        if not raw.strip():
            start = index + 1
            break
    kept = [
        line
        for line in lines[start:]
        if line.strip()
        and not line.strip().startswith("<!--")
        and REVIEW_VERDICT_MARKER not in line
    ]
    return "\n".join(kept)


def verdict_substance(body: str, *, pr_title: str = "") -> tuple[bool, str]:
    """Whether a verdict engages with the diff at all. ``(ok, reason)``.

    The evidence gate verified that verdicts *exist* with the right marker, head
    SHA and distinct reviewer ids — never that any of them looked at anything. A
    verdict engaging with nothing was indistinguishable from one that caught a
    blocker, and the record showed what that permits: 75 of 75 verdicts `pass`,
    all opening `Reviewed <PR title>: <affirmation>`, across 25 PRs that produced
    no review-driven commit between them (#926).

    Two mechanical requirements, both content-agnostic beyond structure:

    * **An anchor.** A path, a ``path:line``, a backticked symbol, or a called
      identifier — or an explicit "checked …" clause, because a genuinely clean
      review must stay expressible and forcing it to invent a file reference
      would make the check worse than nothing.
    * **Novelty against the title.** Prose that is substantially the PR title
      restated is the observed shape, and it survives the anchor test whenever
      the title happens to contain a path.

    This says nothing about whether a review was *good*. It cannot, and trying
    would make the gate a critic. It distinguishes a review from a receipt.
    """
    prose = _verdict_prose(body)
    if not prose.strip():
        return False, "verdict has no prose beyond its header"

    anchored = any(pattern.search(prose) for pattern in _VERDICT_ANCHORS)
    if not anchored and not _VERDICT_CHECKED_CLAUSE.search(prose):
        return False, (
            "verdict names nothing concrete — no file, line, symbol, or "
            "'checked …' clause, so it cannot be told apart from a receipt"
        )

    title_words = set(_WORD.findall(pr_title.lower()))
    prose_words = _WORD.findall(prose.lower())
    if title_words and prose_words:
        novel = [word for word in prose_words if word not in title_words]
        if len(novel) / len(prose_words) < _VERDICT_NOVELTY_FLOOR:
            return False, "verdict is substantially the pull request title restated"
    return True, ""


def _reviewer_key(item: dict[str, Any], body: str) -> str:
    fields = _fields(body)
    reviewer = fields.get("reviewer")
    if reviewer:
        return f"reviewer:{reviewer.lower()}"
    user = item.get("user")
    if isinstance(user, dict) and isinstance(user.get("login"), str) and user["login"]:
        return f"user:{user['login'].lower()}"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"body:{digest}"


def _matches_head(item: dict[str, Any], body: str, head_sha: str | None) -> bool:
    """Does this comment answer for ``head_sha``? A blank head means *do not filter*.

    **Deliberately not :func:`keel.juryavail.is_pinnable_head`'s rule, and the difference
    is worth stating** (#1068). This one filters *evidence items* inside a gate that, with
    no head resolved, is head-agnostic from end to end — every review verdict counts, so
    holding jury verdicts alone to a head nobody knows would refuse a gate the rest of
    which is already unfiltered. Nothing reached through here removes a requirement:
    :func:`_review_evidence_keys` and :func:`_review_vendor_provenance` count verdicts
    towards one, and :func:`jury_panel_size` feeds ``max(declared, minimum_vendors)``, so a
    stale ``panelists:`` can only ever raise the bar.

    The exception is :func:`jury_participating_vendors`, whose count can downgrade a gating
    jury to advisory (#1015) — a stale ``vendors:`` relaxes there, on a blank head and on a
    matching one alike, so it is that function's own head-independence and not this rule's.

    A *pin* is the case that cannot use this reading, because it does remove requirements —
    it takes ``review-verdict-1..3`` off the required set entirely. So the pin
    (:func:`keel.juryavail.pin`, read by :func:`keel.cli._shipped_jury_availability`)
    refuses a blank head before :func:`panel_verdict_posted` is asked at all, rather than
    this predicate changing under the surfaces that need the permissive one.
    """
    if not head_sha:
        return True
    fields = _fields(body)
    recorded = fields.get("head")
    if recorded:
        return recorded == head_sha
    commit_id = item.get("commit_id")
    return isinstance(commit_id, str) and commit_id == head_sha


def _fields(body: str) -> dict[str, str]:
    """Parse the header block at the top of a verdict comment, and only that.

    Scanning stops at the first line that is not part of a header block —
    whether or not a header has been seen yet. #868's second requirement said so
    and only the first shipped: the earlier version `continue`d past prose and
    blank lines until it found something header-shaped, so fields could be
    harvested from anywhere in a comment (#932):

        "Some prose line here.\\n\\nhead: 0000000\\nvendor: spoofed\\n"
        -> {'head': '0000000', 'vendor': 'spoofed'}

    Reachable through :func:`_reviewer_key`, which calls this with no marker
    requirement, so a comment whose *prose* contains ``reviewer: someone`` was
    keyed to that reviewer. Narrow — it needs a trusted author — and not a live
    hole, but it is the residual of the class #868 named, and stopping costs the
    real comment format nothing: a verdict's header is its first block.
    """
    fields: dict[str, str] = {}
    started = False

    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line:
            # Leading blank lines are skipped, not treated as the end: a comment
            # body routinely begins with a newline, and breaking there would
            # reject legitimate verdicts. Once the block has started, a blank
            # line ends it — that is the boundary #868 asked for.
            if started:
                break
            continue
        started = True
        # A marker-only line is the artifact's own header, not a field: skip it and
        # keep reading. A line that merely *mentions* a marker is prose, and prose
        # ends the block — the #932 boundary this parser exists to hold.
        if (line.startswith("<!--") and line.endswith("-->")) or all(
            token in CLASSIFICATION_MARKERS for token in line.split()
        ):
            continue
        match = _FIELD_RE.match(line)
        if match:
            key = match.group("key").lower()
            if key not in fields:
                fields[key] = match.group("value")
        elif not _HEADER_LINE_RE.match(line):
            break
    return fields


def _is_review_verdict_body(body: str) -> bool:
    """Whether ``body`` is a review verdict, decided by its header alone (#1026).

    The jury and closure exclusions are no longer separate substring tests:
    :func:`marker_in_header` yields at most one marker, so a body anchored to the
    jury or closure marker simply is not a review verdict, and a review verdict
    that *mentions* either one in its prose still is.
    """
    if _is_ship_assessment(body):
        return False
    return marker_in_header(body) == REVIEW_VERDICT_MARKER


def _has_trusted_review_marker(items: list[dict[str, Any]]) -> bool:
    return any(
        _is_trusted_source(item, enforced=True)
        and marker_in_header(_body(item)) == REVIEW_VERDICT_MARKER
        for item in items
    )


def jury_participating_vendors(
    pr_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> int | None:
    """Return the panel size declared by a posted jury verdict, or ``None``.

    Reads the ``vendors: <N>`` field from a trusted, head-bound
    ``keel.jury-verdict.v1`` comment. This is how the participating-vendor count
    reaches a CI evidence check: the run ledger and the jury artifact both live
    under the gitignored ``.keel/state/``, so a hosted runner cannot read either,
    but PR comments are always visible.

    ``None`` means "not declared" — no jury verdict posted, or one that predates
    the field — and leaves the jury mode untouched rather than assuming a short
    panel. Only a verdict that actually states the count may relax the gate.

    When several verdicts qualify, the largest declared count wins: a re-post
    correcting an earlier partial run should not be capped by the stale one.
    """
    counts = [
        parsed
        for item in [*(pr_comments or []), *(pr_reviews or [])]
        if _is_jury_verdict(item, head_sha=head_sha, enforced=enforced)
        if (parsed := _parse_vendor_count(_fields(_body(item)).get("vendors"))) is not None
    ]
    return max(counts) if counts else None


def jury_panel_size(
    pr_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> int | None:
    """Return the panel size declared by a posted jury verdict, or ``None`` (#1015).

    Reads the ``panelists: <N>`` field the same way
    :func:`jury_participating_vendors` reads ``vendors:``, and for the same
    reason: when the jury **is** the review panel, the number of ballots is the
    reviewer count the evidence gate must require, and a hosted runner can read
    it from nowhere else.

    ``None`` means "not declared", which leaves the gate on the contract's floor
    rather than requiring nothing. The largest declared count wins, so a re-post
    that completes a partial panel raises the requirement instead of being capped
    by the stale verdict — the direction that fails closed.
    """
    counts = [
        parsed
        for item in [*(pr_comments or []), *(pr_reviews or [])]
        if _is_jury_verdict(item, head_sha=head_sha, enforced=enforced)
        if (parsed := _parse_vendor_count(_fields(_body(item)).get("panelists"))) is not None
    ]
    return max(counts) if counts else None


def panel_verdict_posted(
    pr_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> bool:
    """Is a head-pinned jury verdict already on this pull request? (#1066)

    Proof that the panel *sat*, from the one place a bare CI runner can read it: the run
    ledger and the jury artifact both live under the gitignored ``.keel/state/``, while PR
    comments are always visible. A verification surface uses it to pin the contract to what
    the ship measured rather than re-measuring the panel on its own machine. It is the
    *weaker* of the two pins and speaks only when the run left no ledger record for this
    head; :func:`keel.juryavail.pin` owns that order and is the one place it is written.

    Distinct from :func:`jury_panel_size`, which answers *how many* ballots and is ``None``
    for a verdict predating the ``panelists:`` field. Presence is the weaker question, and
    the one that must not depend on an optional field.

    **Call this only with a head you actually resolved.** Like every reader here it goes
    through :func:`_matches_head`, which reads a blank ``head_sha`` as "do not filter" —
    right for counting evidence, wrong for a pin, which is why the caller refuses a blank
    head first (:func:`keel.juryavail.is_pinnable_head`) rather than this function carrying
    a second rule its siblings do not share.
    """
    return any(
        _is_jury_verdict(item, head_sha=head_sha, enforced=enforced)
        for item in [*(pr_comments or []), *(pr_reviews or [])]
    )


def shipped_panel_decision(
    pr_comments: list[dict[str, Any]] | None = None,
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> str | None:
    """The panel decision **this run** recorded, read back off its closure comment (#1068).

    The middle pin, and the one that makes the strongest pin work anywhere. The run's own
    ``ship_run`` ledger record outranks a posted jury verdict — a comment records what
    somebody put on the pull request, the ledger records what the run *did* — but the
    ledger lives under the gitignored ``.keel/state/``, so on a hosted ``evidence-verify``
    or ``merge`` there is no record to read and that precedence held on the shipping
    workstation and nowhere else. A leftover or collaborator-posted ``keel.jury-verdict.v1``
    then answered for a run that had fallen back, and took ``review-verdict-1..3`` off the
    required set.

    The run's decision is already on the pull request: s11 posts the closure comment keel
    renders from that same ledger record, and since #1068 round 6 it carries
    :data:`keel.closure.JURY_PANEL_MARKER` beside the human ``Jury panel:`` line. So this
    reads the run's own statement from the one place that travels with the pull request.

    Three conditions, and each is the same rule its siblings hold to:

    * **Trusted author only** (:func:`_is_trusted_source`). keel posts the closure comment
      on the operator's behalf, which is exactly the authority a posted jury verdict has —
      no more. An untrusted author must not be able to relax the contract *in either
      direction*: neither to claim a fallback that drops the panel item, nor to claim the
      panel sat.
    * **An actual closure comment** (:func:`_has_closure_marker`), so the marker counts only
      inside the artifact that renders it — a reviewer quoting the marker while describing
      this change is prose, the #1026 rule every marker here is read under.
    * **Pinned to this head.** The marker names the head its record was written for and it
      must be the head under verification, because a pull request outlives its heads and a
      pin removes requirements.

    **The latest such comment is the answer, not the first** (#1068 round 7). One head can
    be shipped more than once — a re-run, a force-push back onto the same commit, a second
    ship on a different machine — and each ship posts its own closure comment. ``pr_comments``
    arrives in GitHub's order, oldest first, so this walks the whole list and keeps the last
    match: the newest statement wins, which is the same direction
    :func:`keel.ledger.latest_ship_run_for_pr` selects the ledger record in, and
    :func:`keel.juryavail.pin` ranks the two sources on the premise that they agree about it.
    Returning the first match meant an older ``decision=fallback`` outranked the panel-sat
    ship that followed it — and round 6 emitted no marker at all for a panel that sat, so the
    later run had nothing to outrank the older one *with*. :func:`keel.closure._jury_panel`
    now renders ``decision=available`` too, which is what makes last-wins well defined here.

    ``None`` for everything else — no comment, an older head, a marker keel did not write —
    and ``None`` means *this source is silent*, never a waiver: :func:`keel.juryavail.pin`
    then goes on to the posted verdict exactly as it did before.
    """
    if not head_sha:
        return None
    latest: str | None = None
    for item in pr_comments or []:
        if not _is_trusted_source(item, enforced=enforced):
            continue
        body = _body(item)
        if not _has_closure_marker(body):
            continue
        decision = _jury_panel_decision(body, head_sha)
        if decision is not None:
            latest = decision
    return latest


def _jury_panel_decision(body: str, head_sha: str) -> str | None:
    """The decision ``body``'s panel marker records for ``head_sha``, or ``None``.

    A token parser over one HTML-comment line, not a regex over Markdown: the line is
    :func:`keel.closure._jury_panel_marker`'s exact render, so it unwraps with the same
    :func:`_unwrap_html_comment` a header marker does and splits into
    ``<marker> head=<sha> decision=<value>``. A line that is not that shape is prose and is
    skipped, which is why the human sentence above it — which *names* neither field — can
    never be mistaken for the record.
    """
    for raw_line in (body or "").splitlines():
        tokens = _unwrap_html_comment(raw_line.strip()).split()
        if not tokens or tokens[0] != closure.JURY_PANEL_MARKER:
            continue
        fields: dict[str, str] = {}
        for token in tokens[1:]:
            key, _, value = token.partition("=")
            # First wins, the convention `_fields` already reads headers under, and a
            # token carrying no `=` becomes a valueless key that matches neither field.
            fields.setdefault(key, value)
        if fields.get("head") == head_sha:
            return fields.get("decision")
    return None


def _parse_vendor_count(raw: str | None) -> int | None:
    """Parse a declared vendor count, rejecting anything not a plain non-negative int."""
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _is_jury_verdict(
    item: dict[str, Any],
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> bool:
    if not _is_trusted_source(item, enforced=enforced):
        return False
    body = _body(item)
    if _is_ship_assessment(body):
        return False
    return marker_in_header(body) == JURY_VERDICT_MARKER and _matches_head(item, body, head_sha)
