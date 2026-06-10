"""Deterministic pre-merge evidence verification.

The ship adapter is agentic, but the artifacts it must leave behind are not:
reviewer verdict comments/reviews, the optional jury verdict, and the stable
closure comment marker. This module keeps the check pure so CI can enforce it
without trusting prose in an agent prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import closure

SCHEMA_VERSION = "keel.evidence.v1"
REVIEW_VERDICT_MARKER = "keel.review-verdict.v1"
JURY_VERDICT_MARKER = "keel.jury-verdict.v1"
SHIP_ASSESSMENT_HEADING = "### \U0001f6a2 keel ship"

_LGTM_RE = re.compile(r"\bLGTM\b", re.IGNORECASE)
_REVIEW_RE = re.compile(r"\b(review|reviewer|verdict)\b", re.IGNORECASE)
_JURY_RE = re.compile(r"\b(jury|ai jury)\b", re.IGNORECASE)


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


def contract_as_dict(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
    deferrals: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return the required evidence set derived from review/jury flags."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "review_merge_contract + closure_comment",
        "dry_run_disables_gating": True,
        "fail_closed": True,
        "accepted_sources": {
            "closure": "issue/PR comments carrying keel.closure-comment.v1",
            "review": "PR review or PR comment carrying a reviewer verdict",
            "jury": "PR comment carrying the jury verdict",
        },
        "not_accepted": [
            "pull_request_body",
            "chat_summary",
            "keel_ship_assessment_comment",
        ],
        "deferrals": list(deferrals),
        "required": [item.as_dict() for item in required_items(review_contract, dry_run=False)],
        "active_required": [
            item.as_dict() for item in required_items(review_contract, dry_run=dry_run)
        ],
    }


def required_items(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
) -> tuple[EvidenceItem, ...]:
    """Return the tier/flag-derived evidence requirements."""
    if dry_run:
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
    dry_run: bool = False,
    deferrals: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify required evidence artifacts and return a deterministic report."""
    del pr_body  # Explicitly not accepted as evidence.
    items = required_items(review_contract, dry_run=dry_run)
    deferred = set(deferrals)
    counts = _evidence_counts(
        pr_comments=pr_comments or [],
        issue_comments=issue_comments or [],
        pr_reviews=pr_reviews or [],
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
) -> dict[str, int]:
    pr_comment_bodies = [_body(comment) for comment in pr_comments]
    issue_comment_bodies = [_body(comment) for comment in issue_comments]
    review_bodies = [_body(review) for review in pr_reviews]
    return {
        "closure_pr": sum(_has_closure_marker(body) for body in pr_comment_bodies),
        "closure_issue": sum(_has_closure_marker(body) for body in issue_comment_bodies),
        "review_verdict": (
            sum(_is_review_verdict(body) for body in pr_comment_bodies)
            + sum(_is_review_verdict(body) for body in review_bodies)
        ),
        "jury_verdict": sum(_is_jury_verdict(body) for body in pr_comment_bodies),
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


def _is_ship_assessment(body: str) -> bool:
    return SHIP_ASSESSMENT_HEADING in body or "keel ship \u2014" in body


def _is_review_verdict(body: str) -> bool:
    if not body or _is_ship_assessment(body) or _has_closure_marker(body):
        return False
    if JURY_VERDICT_MARKER in body:
        return False
    if REVIEW_VERDICT_MARKER in body:
        return True
    if _JURY_RE.search(body) is not None:
        return False
    if "chat summary" in body.lower():
        return False
    return (
        ("LGTM" in body and _REVIEW_RE.search(body) is not None)
        or (_LGTM_RE.search(body) is not None and "review" in body.lower())
    )


def _is_jury_verdict(body: str) -> bool:
    if not body or _is_ship_assessment(body) or _has_closure_marker(body):
        return False
    return JURY_VERDICT_MARKER in body or (
        _JURY_RE.search(body) is not None and _LGTM_RE.search(body) is not None
    )
