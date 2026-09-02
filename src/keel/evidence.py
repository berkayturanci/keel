"""Deterministic pre-merge evidence verification.

The ship adapter is agentic, but the artifacts it must leave behind are not:
reviewer verdict comments/reviews, the optional jury verdict, and the stable
closure comment marker. This module keeps the check pure so CI can enforce it
without trusting prose in an agent prompt.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import agents, closure

SCHEMA_VERSION = "keel.evidence.v1"
AGENT_LABEL_PREFIX = "agent:"
REVIEW_VERDICT_MARKER = "keel.review-verdict.v1"
JURY_VERDICT_MARKER = "keel.jury-verdict.v1"
SHIP_ASSESSMENT_HEADING = "### \U0001f6a2 keel ship"
DEFAULT_WAIVER_LABEL = "keel:evidence-waived"
TRUSTED_AUTHOR_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
TRUSTED_SHIP_ASSESSMENT_BOTS = frozenset({"github-actions", "github-actions[bot]"})

_FIELD_RE = re.compile(
    r"^\s*(?P<key>reviewer|head|vendor|model|vendors)\s*:\s*(?P<value>\S+)\s*$",
    re.IGNORECASE,
)
_HEADER_LINE_RE = re.compile(r"^[A-Za-z0-9_-]+\s*:")
_SHIP_BRANCH_RE = re.compile(r"^(feature|fix|chore|docs|test)/issue-\d+(?:-|$)")

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
    """
    label_set = set(labels or ())
    if waiver_label and waiver_label in label_set:
        return _gate_decision(False, "operator-waiver-label", waiver_label, waived=True)
    if gate_active(labels, gate_label):
        return _gate_decision(True, "gate-label", gate_label)
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
    for index in range(1, reviewer_count + 1):
        items.append(
            EvidenceItem(
                f"review-verdict-{index}",
                "review",
                True,
                "Distinct posted s7 reviewer verdict for the current PR",
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

    When the gate is active, ``pr_labels`` are additionally checked for the
    mandatory ``agent:<vendor>`` attribution label (and cross-checked against the
    ledger implementer vendor when a record is present); see
    :func:`attribution_check`.
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
    attribution = _attribution_finding(
        pr_labels=pr_labels,
        enforced=enforced and not dry_run,
        ledger_record=ledger_record,
    )
    if attribution is not None:
        findings = [*findings, attribution]
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


def _has_closure_marker(body: str) -> bool:
    return closure.COMMENT_MARKER in body


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
    return SHIP_ASSESSMENT_HEADING in body or "keel ship \u2014" in body


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


def agent_label_vendors(labels: Sequence[str] | None) -> list[str]:
    """Return the lower-cased vendor slugs from every ``agent:<vendor>`` label.

    A blank vendor (a bare ``agent:`` label) is ignored. Order is preserved and
    duplicates are kept so callers can reason about the raw label set; this is a
    pure helper with no I/O.
    """
    vendors: list[str] = []
    for label in labels or ():
        if not isinstance(label, str) or not label.startswith(AGENT_LABEL_PREFIX):
            continue
        vendor = label[len(AGENT_LABEL_PREFIX) :].strip().lower()
        if vendor:
            vendors.append(vendor)
    return vendors


def ledger_implementer_vendor(ledger_record: dict[str, Any] | None) -> str | None:
    """Return the implementer's vendor slug from a ship_run ``ledger_record``.

    The ledger stores the effective implementer as a codename or ``vendor:model``
    string under ``actors.implementer``; the vendor is the part before the first
    ``:``. Returns ``None`` when no record, no implementer, or a blank implementer
    is recorded so the cross-check can degrade to presence-only. Pure — no I/O.
    """
    if not isinstance(ledger_record, dict):
        return None
    actors = ledger_record.get("actors")
    implementer = actors.get("implementer") if isinstance(actors, dict) else None
    if not isinstance(implementer, str) or not implementer.strip():
        return None
    vendor, _ = agents.split_delegate(implementer.strip())
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
            "provenance (ship branch, posted review verdict, ship-run ledger, or the gate "
            "label), or disarm deliberately with the operator waiver label."
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
        if (
            (line.startswith("<!--") and line.endswith("-->"))
            or REVIEW_VERDICT_MARKER in line
            or JURY_VERDICT_MARKER in line
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
    if not body or _is_ship_assessment(body) or _has_closure_marker(body):
        return False
    if JURY_VERDICT_MARKER in body:
        return False
    return REVIEW_VERDICT_MARKER in body


def _has_trusted_review_marker(items: list[dict[str, Any]]) -> bool:
    return any(
        _is_trusted_source(item, enforced=True) and REVIEW_VERDICT_MARKER in _body(item)
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
    if not body or _is_ship_assessment(body) or _has_closure_marker(body):
        return False
    return JURY_VERDICT_MARKER in body and _matches_head(item, body, head_sha)
