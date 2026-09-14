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
FINDING_INVALID_MARKER = "invalid-marker"
FINDING_APPLIED_WITHOUT_ARTIFACT = "applied-without-artifact"
FINDING_REVIEWER_COUNT_MISMATCH = "reviewer-count-mismatch"
#: A **note**, never a finding: the capture was applied to a sink outside the checkout,
#: so the path in the committed ledger is host-specific and names nothing anywhere else.
#: That is the sink's design, not a gap — reporting it as `applied-without-artifact`
#: accused a run that did exactly what it was configured to do (#1185).
NOTE_APPLIED_ELSEWHERE = "applied-elsewhere"


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
    notes = [note for result in results for note in result.get("notes", ())]
    by_type = {
        FINDING_MISSING_MARKER: 0,
        FINDING_INVALID_MARKER: 0,
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
        "notes": notes,
        "summary": {
            "checked": len(results),
            "findings": len(findings),
            "notes": len(notes),
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
    notes: list[dict[str, Any]] = []

    if not verification["ok"]:
        if verification["status"] == "missing":
            findings.append(
                _finding(
                    FINDING_MISSING_MARKER,
                    pr_number,
                    "merged PR has no capture marker in the ledger",
                )
            )
        else:
            reason = verification.get("reason") or "invalid capture marker in the ledger"
            findings.append(
                _finding(
                    FINDING_INVALID_MARKER,
                    pr_number,
                    f"merged PR has an invalid capture marker: {reason}",
                    invalid_reason=reason,
                )
            )

    artifact = _capture_artifact(record)
    scope = _capture_artifact_scope(record, artifact)
    # Both branches are about an **applied** row, and the note has to say so as
    # plainly as the finding does: it asserts that a host wrote a file. Attached on
    # the scope alone it appeared beside `invalid-marker`, and on a `deferred` row —
    # claiming a write for a run that captured nothing.
    applied = verification.get("status") == "applied"
    if applied and not artifact and scope != capture.ARTIFACT_SCOPE_MACHINE:
        findings.append(
            _finding(
                FINDING_APPLIED_WITHOUT_ARTIFACT,
                pr_number,
                "capture status is applied but no capture artifact was recorded",
            )
        )
    elif applied and scope == capture.ARTIFACT_SCOPE_MACHINE:
        # Decided from the record, not from the filesystem: this module is pure, and
        # "can this host read it" is the wrong question anyway — the path is host-specific
        # wherever it is read, including on the machine that wrote it a month later.
        notes.append(
            _note(
                NOTE_APPLIED_ELSEWHERE,
                pr_number,
                "capture artifact is outside the checkout, so it is readable only on the "
                + (f"host that wrote it: {artifact}" if artifact else "host that wrote it"),
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
        "notes": notes,
    }


def _finding(finding_type: str, pr_number: int, reason: str, **extra: Any) -> dict[str, Any]:
    finding = {"type": finding_type, "pr": pr_number, "reason": reason}
    finding.update(extra)
    return finding


def _note(note_type: str, pr_number: int, message: str) -> dict[str, Any]:
    """A note has a finding's shape and none of its authority: it never fails a run."""
    return {"type": note_type, "pr": pr_number, "message": message}


def _capture_artifact_scope(record: dict[str, Any] | None, artifact: str | None) -> str | None:
    """The recorded scope, or the one the path's shape implies for an older record.

    Records written before `artifact_scope` existed carry only the path, and an anchored
    path was always an outside sink — so nothing already in a ledger changes meaning.
    """
    if not isinstance(record, dict):
        return None
    block = record.get("capture")
    if isinstance(block, dict):
        recorded = block.get("artifact_scope")
        if isinstance(recorded, str) and recorded.strip():
            return recorded.strip()
    return capture.artifact_scope(artifact)


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
