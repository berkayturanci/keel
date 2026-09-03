"""Pure orchestration for ``keel review`` — the evidence-bundle orchestrator.

The host agent runs the actual reviewers and produces the review *content*. This
module takes that supplied content and deterministically decides what to render
and where to post it: one head-pinned reviewer verdict per supplied review, an
optional closure comment posted to both the PR and the linked issue, and the
run-id sub-keys that bind each post to a stable, idempotent comment.

Everything here is pure — no network, no subprocess, no clock, no randomness.
Rendering uses the already-pure artifact/closure renderers. The CLI handler owns
the head-SHA fetch, the actual posting, and the optional re-verify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import artifacts, closure, evidence

SCHEMA_VERSION = "keel.review.v1"


class ReviewError(ValueError):
    """Raised when the supplied review bundle is malformed or under-count."""


@dataclass(frozen=True)
class ReviewItem:
    """One parsed reviewer verdict supplied by the host agent."""

    reviewer: str
    verdict: str
    scope: str | None
    findings: tuple[dict[str, Any], ...]
    testing: str | None
    vendor: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class PostTarget:
    """A single planned post: a rendered body bound to a target and run-id sub-key."""

    artifact: str
    target_kind: str
    target_number: int
    run_id: str
    marker: str
    body: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "target": {"kind": self.target_kind, "number": self.target_number},
            "run_id": self.run_id,
            "marker": self.marker,
            "body": self.body,
        }


@dataclass(frozen=True)
class ReviewPlan:
    """The deterministic plan: rendered verdicts, optional closure, post targets."""

    pull_request: int
    issue: int | None
    head_sha: str | None
    run_id: str
    tier: int | None
    required_count: int
    supplied_count: int
    posts: tuple[PostTarget, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pull_request": self.pull_request,
            "issue": self.issue,
            "head_sha": self.head_sha,
            "run_id": self.run_id,
            "tier": self.tier,
            "required_count": self.required_count,
            "supplied_count": self.supplied_count,
            "posts": [post.as_dict() for post in self.posts],
        }


def parse_reviews(raw: object) -> tuple[ReviewItem, ...]:
    """Parse and validate a ``--reviews`` JSON payload into ``ReviewItem`` records."""
    if not isinstance(raw, list):
        raise ReviewError("reviews file must contain a JSON array of review objects")
    items: list[ReviewItem] = []
    for index, entry in enumerate(raw):
        items.append(_parse_review(entry, index))
    return tuple(items)


def _parse_review(entry: object, index: int) -> ReviewItem:
    if not isinstance(entry, dict):
        raise ReviewError(f"review #{index + 1} must be a JSON object")
    reviewer = entry.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ReviewError(f"review #{index + 1} requires a non-empty 'reviewer' string")
    verdict = entry.get("verdict")
    if not isinstance(verdict, str) or not verdict.strip():
        raise ReviewError(f"review #{index + 1} requires a non-empty 'verdict' string")
    scope = entry.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ReviewError(f"review #{index + 1} 'scope' must be a string when present")
    testing = entry.get("testing")
    if testing is not None and not isinstance(testing, str):
        raise ReviewError(f"review #{index + 1} 'testing' must be a string when present")
    vendor = _parse_provenance_field(entry.get("vendor"), index, "vendor")
    model = _parse_provenance_field(entry.get("model"), index, "model")
    findings = _parse_findings(entry.get("findings"), index)
    return ReviewItem(
        reviewer=reviewer.strip(),
        verdict=verdict.strip(),
        scope=scope,
        findings=findings,
        testing=testing,
        vendor=vendor,
        model=model,
    )


def _parse_provenance_field(value: object, index: int, name: str) -> str | None:
    """Parse an optional ``vendor``/``model`` provenance string from a review entry."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewError(f"review #{index + 1} '{name}' must be a string when present")
    cleaned = value.strip()
    return cleaned or None


def _parse_findings(raw: object, index: int) -> tuple[dict[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ReviewError(f"review #{index + 1} 'findings' must be a list when present")
    findings: list[dict[str, Any]] = []
    for finding_index, finding in enumerate(raw):
        if not isinstance(finding, dict):
            raise ReviewError(
                f"review #{index + 1} finding #{finding_index + 1} must be a JSON object"
            )
        findings.append(dict(finding))
    return tuple(findings)


def parse_cycle_reviewers(raw: object) -> tuple[dict[str, Any], ...]:
    """Parse a review-cycle findings payload into reviewer records for rendering.

    The payload is the host-supplied structured block each reviewer returns
    (codename · focus · verdict · findings · clean areas). Validation is shallow
    on purpose: the renderer applies per-field fallbacks, so this only enforces
    the outer shape and surfaces a clear error when it is malformed.
    """
    if not isinstance(raw, list):
        raise ReviewError("review-cycle findings must be a JSON array of reviewer objects")
    reviewers: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ReviewError(f"reviewer #{index + 1} must be a JSON object")
        reviewers.append(dict(entry))
    return tuple(reviewers)


def review_run_id(run_id: str, reviewer: str) -> str:
    """Stable per-reviewer run-id sub-key, e.g. ``<run-id>:rv-<reviewer-slug>``."""
    return f"{run_id}:rv-{artifacts.slug(reviewer)}"


def closure_run_id(run_id: str) -> str:
    """Stable run-id sub-key for the closure-comment artifact."""
    return f"{run_id}:closure"


def jury_run_id(run_id: str) -> str:
    """Stable run-id sub-key for the jury-verdict artifact."""
    return f"{run_id}:jury"


def build_review_plan(
    reviews: tuple[ReviewItem, ...],
    *,
    required_count: int,
    head_sha: str | None,
    pull_request: int,
    issue: int | None,
    run_id: str,
    tier: int | None,
    closure_record: dict[str, Any] | None = None,
    jury_record: dict[str, Any] | None = None,
) -> ReviewPlan:
    """Validate the bundle against the required count and build the post plan.

    Fewer supplied reviews than required fails; exact or more is allowed. Each
    review renders as a head-pinned verdict posted to the PR. A closure record,
    when supplied, renders once and posts to both the PR and the linked issue.

    ``jury_record`` is the panel's consensus record (:func:`keel.jury.jury_verdict`),
    supplied when the reviews *are* a jury panel's ballots (#1015). It renders the
    one ``keel.jury-verdict.v1`` comment alongside the per-ballot verdicts, so the
    panel posts its evidence in a single call and the two halves are pinned to the
    same head SHA by construction — a jury verdict posted separately from the
    ballots it summarises is the drift this whole path removes.
    """
    supplied = len(reviews)
    if supplied < required_count:
        raise ReviewError(
            f"supplied {supplied} review(s) but tier requires at least "
            f"{required_count}; refusing to under-post evidence"
        )
    posts: list[PostTarget] = []
    for item in reviews:
        body = artifacts.render_review_verdict(
            reviewer=item.reviewer,
            head_sha=head_sha,
            verdict=item.verdict,
            scope=item.scope,
            findings=list(item.findings),
            testing=item.testing,
            vendor=item.vendor,
            model=item.model,
        )
        posts.append(
            PostTarget(
                artifact="review-verdict",
                target_kind="pr",
                target_number=pull_request,
                run_id=review_run_id(run_id, item.reviewer),
                marker=evidence.REVIEW_VERDICT_MARKER,
                body=body,
            )
        )
    if jury_record is not None:
        posts.append(
            _jury_post(jury_record, head_sha=head_sha, pull_request=pull_request, run_id=run_id)
        )
    if closure_record is not None:
        posts.extend(
            _closure_posts(
                closure_record,
                pull_request=pull_request,
                issue=issue,
                run_id=run_id,
            )
        )
    return ReviewPlan(
        pull_request=pull_request,
        issue=issue,
        head_sha=head_sha,
        run_id=run_id,
        tier=tier,
        required_count=required_count,
        supplied_count=supplied,
        posts=tuple(posts),
    )


#: Fields :func:`keel.artifacts.render_jury_verdict` accepts from a jury record.
#: Named rather than splatted: the record comes from a parsed ai-jury report, and
#: an unexpected key there must be dropped, never forwarded into a renderer.
_JURY_FIELDS = (
    "verdict",
    "participants",
    "participating_vendors",
    "panelists",
    "findings_summary",
    "remaining_risks",
)


def _jury_post(
    jury_record: dict[str, Any],
    *,
    head_sha: str | None,
    pull_request: int,
    run_id: str,
) -> PostTarget:
    if not isinstance(jury_record, dict):
        raise ReviewError("jury record must be a JSON object")
    fields = {key: jury_record[key] for key in _JURY_FIELDS if key in jury_record}
    return PostTarget(
        artifact="jury-verdict",
        target_kind="pr",
        target_number=pull_request,
        run_id=jury_run_id(run_id),
        marker=evidence.JURY_VERDICT_MARKER,
        body=artifacts.render_jury_verdict(head_sha=head_sha, **fields),
    )


def _closure_posts(
    closure_record: dict[str, Any],
    *,
    pull_request: int,
    issue: int | None,
    run_id: str,
) -> list[PostTarget]:
    if not isinstance(closure_record, dict):
        raise ReviewError("closure file must contain a JSON object")
    body = closure.render_closure_comment(closure_record)
    sub_run_id = closure_run_id(run_id)
    targets: list[PostTarget] = [
        PostTarget(
            artifact="closure-comment",
            target_kind="pr",
            target_number=pull_request,
            run_id=sub_run_id,
            marker=closure.COMMENT_MARKER,
            body=body,
        )
    ]
    if issue is not None:
        targets.append(
            PostTarget(
                artifact="closure-comment",
                target_kind="issue",
                target_number=issue,
                run_id=sub_run_id,
                marker=closure.COMMENT_MARKER,
                body=body,
            )
        )
    return targets
