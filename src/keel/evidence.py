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

from . import closure

SCHEMA_VERSION = "keel.evidence.v1"
REVIEW_VERDICT_MARKER = "keel.review-verdict.v1"
JURY_VERDICT_MARKER = "keel.jury-verdict.v1"
SHIP_ASSESSMENT_HEADING = "### \U0001f6a2 keel ship"
DEFAULT_WAIVER_LABEL = "keel:evidence-waived"
TRUSTED_AUTHOR_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
TRUSTED_SHIP_ASSESSMENT_BOTS = frozenset({"github-actions", "github-actions[bot]"})

_FIELD_RE = re.compile(r"^\s*(?P<key>reviewer|head|vendor|model)\s*:\s*(?P<value>\S+)\s*$",
                       re.IGNORECASE | re.MULTILINE)
_SHIP_BRANCH_RE = re.compile(r"^(feature|fix|chore|docs|test)/issue-\d+(?:-|$)")


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str
    required: bool
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "required": self.required,
            "description": self.description,
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
        _is_ship_assessment_source(item) and _is_ship_assessment(_body(item))
        for item in items
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
) -> dict[str, Any]:
    """Return the required evidence set derived from review/jury flags."""
    return {
        "schema_version": SCHEMA_VERSION,
        "enforced": enforced,
        "source": "review_merge_contract + closure_comment",
        "dry_run_disables_gating": True,
        "fail_closed": True,
        "require_distinct_vendors": _require_distinct_vendors(review_contract),
        "accepted_sources": {
            "closure": (
                "trusted issue/PR comments carrying keel.closure-comment.v1"
            ),
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
            for item in required_items(review_contract, dry_run=dry_run, enforced=enforced)
        ],
    }


def required_items(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
    enforced: bool = True,
) -> tuple[EvidenceItem, ...]:
    """Return the tier/flag-derived evidence requirements."""
    if dry_run or not enforced:
        return ()
    reviewers = review_contract.get("reviewers")
    reviewer_count = reviewers.get("count") if isinstance(reviewers, dict) else 0
    reviewer_count = reviewer_count if isinstance(reviewer_count, int) and reviewer_count > 0 else 0
    jury = review_contract.get("jury")
    jury_required = (
        isinstance(jury, dict)
        and bool(jury.get("enabled"))
        and jury.get("mode") == "gating"
    )
    items: list[EvidenceItem] = [
        EvidenceItem(
            "closure-comment-pr",
            "closure",
            True,
            "PR conversation comment with keel.closure-comment.v1 marker",
        ),
        EvidenceItem(
            "closure-comment-issue",
            "closure",
            True,
            "Linked issue comment with keel.closure-comment.v1 marker",
        ),
    ]
    for index in range(1, reviewer_count + 1):
        items.append(EvidenceItem(
            f"review-verdict-{index}",
            "review",
            True,
            "Distinct posted s7 reviewer verdict for the current PR",
        ))
    if jury_required:
        items.append(EvidenceItem(
            "jury-verdict",
            "jury",
            True,
            "Posted gating jury verdict comment for the current PR",
        ))
    return tuple(items)


def verify(
    review_contract: dict[str, Any],
    *,
    pr_comments: list[dict[str, Any]] | None = None,
    issue_comments: list[dict[str, Any]] | None = None,
    pr_reviews: list[dict[str, Any]] | None = None,
    pr_body: str | None = None,
    head_sha: str | None = None,
    ledger_record: dict[str, Any] | None = None,
    dry_run: bool = False,
    enforced: bool = True,
    deferrals: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify required evidence artifacts and return a deterministic report.

    When ``ledger_record`` is the ship_run record for this PR, a closure comment
    only counts when its content matches the canonical render of that record
    (closure-comment fidelity). Without a record the marker-only behavior holds.
    """
    del pr_body  # Explicitly not accepted as evidence.
    items = required_items(review_contract, dry_run=dry_run, enforced=enforced)
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
        results.append({
            "id": item.id,
            "kind": item.kind,
            "required": item.required,
            "present": present,
            "deferred": is_deferred,
            "ok": ok,
            "reason": None if ok else _result_reason(item, mismatch),
        })
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
    blocking_findings = [finding for finding in findings if finding["severity"] == "major"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not missing and not blocking_findings else "fail",
        "dry_run": dry_run,
        "enforced": enforced,
        "required_count": len(items),
        "missing": missing,
        "results": results,
        "counts": counts,
        "findings": findings,
    }


def _require_distinct_vendors(review_contract: dict[str, Any]) -> bool:
    reviewers = review_contract.get("reviewers")
    return bool(reviewers.get("require_distinct_vendors")) if isinstance(reviewers, dict) else False


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


_CLOSURE_MISMATCH_REASON = (
    "closure comment does not match the ship_run ledger record"
)


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
            comment for comment in comments
            if _is_trusted_source(comment, enforced=enforced)
            and _has_closure_marker(_body(comment))
        ]
        if markered and not any(
            closure_body_matches_record(_body(comment), ledger_record)
            for comment in markered
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
        "jury_verdict": sum(_is_jury_verdict(comment, head_sha=head_sha, enforced=enforced)
                            for comment in pr_comments),
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


def _normalize_closure_body(body: str) -> str:
    """Normalize a closure body for content comparison.

    Robust to harmless formatting drift but sensitive to real content changes:
    trailing whitespace is stripped per line, runs of blank lines collapse to a
    single blank line, and leading/trailing blank lines are dropped.
    """
    lines = [line.rstrip() for line in body.splitlines()]
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
            findings.append({
                "id": "run-context-empty",
                "severity": "major" if enforced else "minor",
                "kind": "closure",
                "message": "Closure comment Run context is fully degraded.",
            })
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
    )
    return len(keys)


def _review_evidence_keys(
    items: list[dict[str, Any]],
    *,
    head_sha: str | None = None,
    enforced: bool = True,
) -> set[str]:
    keys: set[str] = set()
    for item in items:
        if not _is_trusted_source(item, enforced=enforced):
            continue
        body = _body(item)
        if not _is_review_verdict_body(body):
            continue
        if not _matches_head(item, body, head_sha):
            continue
        keys.add(_reviewer_key(item, body))
    return keys


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
    return {match.group("key").lower(): match.group("value")
            for match in _FIELD_RE.finditer(body)}


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
