"""Pure capture reconciliation: cross-check merged PRs against the ledger.

``keel capture-verify`` historically trusted the agent to pass every merged PR
via ``--merged-pr`` and to self-report ``--capture-status applied`` with no
proof. This module hardens that accounting with three additive checks, all
pure data in / pure findings out (no network, subprocess, clock, or random):

1. **missing-marker** — every PR in the derived merged set must have a valid
   capture marker in the ledger. A merged PR with no marker is a finding, so a
   merged PR can no longer silently vanish from capture accounting by being
   omitted from the args.
2. **applied-without-artifact** — an ``applied`` capture must carry a durable
   capture artifact reference (path/hash) in its ledger record. ``applied``
   with no artifact is a finding. ``deferred``/``skipped`` need no artifact.
3. **reviewer-count-mismatch** — the ledger record's ``actors.reviewers`` count
   for a PR is cross-checked against the evidence-side review-verdict count for
   that PR. Recording more reviewers than verdicts posted is a finding.

The CLI does the I/O (transport query for merged PRs, marker/verdict fetch) and
feeds the results here. The base pass/fail semantics of ``verify_session`` are
preserved; these are strictly additional findings.
"""

from __future__ import annotations

from typing import Any

from . import capture, ledger

RECONCILE_SCHEMA_VERSION = "keel.capture-verify-reconcile.v1"

FINDING_MISSING_MARKER = "missing-marker"
FINDING_APPLIED_WITHOUT_ARTIFACT = "applied-without-artifact"
FINDING_REVIEWER_COUNT_MISMATCH = "reviewer-count-mismatch"


def reconcile(
    records: list[dict[str, Any]],
    merged_prs: list[int] | tuple[int, ...],
    *,
    verdict_counts: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Cross-check the derived merged-PR set against the ledger.

    ``records`` is the run ledger. ``merged_prs`` is the authoritative merged-PR
    set (derived from the transport, not the agent's args). ``verdict_counts``
    maps a PR number to the evidence-side review-verdict count; a PR omitted from
    the mapping skips the reviewer cross-check (the count is unknown offline, so
    it degrades to advisory rather than failing).

    Returns a structured report: per-PR results plus a flat findings list and a
    summary. ``ok`` is true only when no findings were raised.
    """
    counts = verdict_counts or {}
    results = [_reconcile_pr(records, pr, counts) for pr in merged_prs]
    findings = [finding for result in results for finding in result["findings"]]
    by_type = {
        FINDING_MISSING_MARKER: 0,
        FINDING_APPLIED_WITHOUT_ARTIFACT: 0,
        FINDING_REVIEWER_COUNT_MISMATCH: 0,
    }
    for finding in findings:
        by_type[finding["type"]] += 1
    return {
        "schema_version": RECONCILE_SCHEMA_VERSION,
        "ok": not findings,
        "merged_prs": list(merged_prs),
        "results": results,
        "findings": findings,
        "summary": {
            "checked": len(results),
            "findings": len(findings),
            **by_type,
        },
    }


def _reconcile_pr(
    records: list[dict[str, Any]],
    pr_number: int,
    verdict_counts: dict[int, int],
) -> dict[str, Any]:
    verification = capture._verify_pr(records, pr_number)
    record = ledger.latest_ship_run_for_pr(records, pr_number)
    findings: list[dict[str, Any]] = []

    if not verification["ok"] and verification["status"] == "missing":
        findings.append(
            _finding(
                FINDING_MISSING_MARKER,
                pr_number,
                "merged PR has no capture marker in the ledger",
            )
        )

    artifact = _capture_artifact(record)
    if verification.get("status") == "applied" and not artifact:
        findings.append(
            _finding(
                FINDING_APPLIED_WITHOUT_ARTIFACT,
                pr_number,
                "capture status is applied but no capture artifact was recorded",
            )
        )

    recorded_reviewers = _recorded_reviewer_count(record)
    verdicts = verdict_counts.get(pr_number)
    if verdicts is not None and recorded_reviewers > verdicts:
        findings.append(
            _finding(
                FINDING_REVIEWER_COUNT_MISMATCH,
                pr_number,
                f"ledger records {recorded_reviewers} reviewer(s) but only "
                f"{verdicts} review verdict(s) were posted",
                recorded_reviewers=recorded_reviewers,
                posted_verdicts=verdicts,
            )
        )

    return {
        "pr": pr_number,
        "ok": not findings,
        "marker_status": verification["status"],
        "marker": verification.get("marker"),
        "artifact": artifact,
        "recorded_reviewers": recorded_reviewers,
        "posted_verdicts": verdicts,
        "findings": findings,
    }


def _finding(finding_type: str, pr_number: int, reason: str, **extra: Any) -> dict[str, Any]:
    finding = {"type": finding_type, "pr": pr_number, "reason": reason}
    finding.update(extra)
    return finding


def _capture_artifact(record: dict[str, Any] | None) -> str | None:
    if not isinstance(record, dict):
        return None
    block = record.get("capture")
    if not isinstance(block, dict):
        return None
    artifact = block.get("artifact")
    return artifact if isinstance(artifact, str) and artifact.strip() else None


def _recorded_reviewer_count(record: dict[str, Any] | None) -> int:
    if not isinstance(record, dict):
        return 0
    actors = record.get("actors")
    if not isinstance(actors, dict):
        return 0
    reviewers = actors.get("reviewers")
    if not isinstance(reviewers, list):
        return 0
    return sum(1 for reviewer in reviewers if isinstance(reviewer, str) and reviewer.strip())
