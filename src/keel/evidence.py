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
TRUSTED_AUTHOR_TYPES = frozenset({"Bot"})

_FIELD_RE = re.compile(r"^\s*(?P<key>reviewer|head)\s*:\s*(?P<value>\S+)\s*$",
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
    dry_run: bool = False,
    enforced: bool = True,
    deferrals: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify required evidence artifacts and return a deterministic report."""
    del pr_body  # Explicitly not accepted as evidence.
    items = required_items(review_contract, dry_run=dry_run, enforced=enforced)
    deferred = set(deferrals)
    counts = _evidence_counts(
        pr_comments=pr_comments or [],
        issue_comments=issue_comments or [],
        pr_reviews=pr_reviews or [],
        head_sha=head_sha,
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
            "reason": None if ok else f"missing required evidence: {item.id}",
        })
    missing = [result["id"] for result in results if not result["ok"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not missing else "fail",
        "dry_run": dry_run,
        "enforced": enforced,
        "required_count": len(items),
        "missing": missing,
        "results": results,
        "counts": counts,
    }


def _evidence_counts(
    *,
    pr_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    pr_reviews: list[dict[str, Any]],
    head_sha: str | None = None,
) -> dict[str, int]:
    review_keys = _review_evidence_keys(
        [*pr_comments, *pr_reviews],
        head_sha=head_sha,
    )
    return {
        "closure_pr": sum(_is_closure_comment(comment) for comment in pr_comments),
        "closure_issue": sum(_is_closure_comment(comment) for comment in issue_comments),
        "review_verdict": len(review_keys),
        "jury_verdict": sum(_is_jury_verdict(comment, head_sha=head_sha)
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


def _is_closure_comment(item: dict[str, Any]) -> bool:
    return _is_trusted_source(item) and _has_closure_marker(_body(item))


def _is_trusted_source(item: dict[str, Any]) -> bool:
    """Return whether GitHub marks this evidence source as trusted.

    Live GitHub comment/review payloads include ``author_association``. Offline
    fixtures and older adapters may omit it, so unknown provenance remains
    accepted for backward-compatible local verification; untrusted explicit
    associations fail closed.
    """
    association = item.get("author_association")
    if association is None:
        return True
    if isinstance(association, str) and association.upper() in TRUSTED_AUTHOR_ASSOCIATIONS:
        return True
    return _author_type(item) in TRUSTED_AUTHOR_TYPES


def _author_type(item: dict[str, Any]) -> str | None:
    user = item.get("user")
    if isinstance(user, dict) and isinstance(user.get("type"), str):
        return user["type"]
    return None


def _is_ship_assessment(body: str) -> bool:
    return SHIP_ASSESSMENT_HEADING in body or "keel ship \u2014" in body


def _review_evidence_keys(
    items: list[dict[str, Any]],
    *,
    head_sha: str | None = None,
) -> set[str]:
    keys: set[str] = set()
    for item in items:
        if not _is_trusted_source(item):
            continue
        body = _body(item)
        if not _is_review_verdict_body(body):
            continue
        if not _matches_head(item, body, head_sha):
            continue
        keys.add(_reviewer_key(item, body))
    return keys


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
    return any(_is_trusted_source(item) and REVIEW_VERDICT_MARKER in _body(item) for item in items)


def _is_jury_verdict(item: dict[str, Any], *, head_sha: str | None = None) -> bool:
    if not _is_trusted_source(item):
        return False
    body = _body(item)
    if not body or _is_ship_assessment(body) or _has_closure_marker(body):
        return False
    return JURY_VERDICT_MARKER in body and _matches_head(item, body, head_sha)
