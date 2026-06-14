"""Pure close-ordering reconciliation: issue lifecycle state vs the ledger.

keel's lifecycle contract (see :mod:`keel.ship`) is explicit that an issue is
only *done* after a real merge: ``status_done_after_merge_only`` and
``pr_merged_state_authoritative``. But nothing deterministic checks that an issue
which is **closed** or already carries the **status-done** label actually has a
ledger record attesting a merge. An issue closed at s4, or a ``status:done``
label applied before the PR merged, is invisible — exactly the audit gap
(GAP-16) this module closes.

This is the lowest-friction core-pure form: a post-hoc reconcile for the
morning/wrap readers. Given each issue's observed lifecycle state (closed?
status-done label present?) and the most recent ship_run ledger record for that
issue, it flags any issue that is closed or status-done while its ledger record
does **not** attest a merge.

What counts as "the ledger attests a merge": a ship_run record whose
``assessment.merge.action`` is ``"merge"`` — the *clear-to-merge* decision the
ship step records after all gates passed and the window was open (``defer`` and
``block`` never lead to a merge). A missing record, a malformed record, or a
``defer``/``block`` decision all mean "no clear-to-merge decision witnessed", so
a close or status-done at that point is premature.

This is a proxy, and an honest reader should know its limit: the ship_run record
captures the *decision* made at ship time, not a confirmed post-merge fact — the
actual ``gh pr merge`` runs in a separate step and writes nothing back onto the
record. So a narrow blind spot remains: if the assessment said ``"merge"`` but
the subsequent merge failed or was never run, a later close/status-done is *not*
flagged. It is nonetheless the strongest merge signal available to a pure ledger
reader, and the gate is advisory, so this trade-off is acceptable. (Contrast
:mod:`keel.consentverify`, which reads the live-observed merged state — close
reconciliation runs ledger-only by design, for the morning/wrap readers.)

Two verdicts:

* **ok** — every observed issue is consistent (open and not status-done, or
  closed/status-done *with* a merge-attesting record).
* **flagged** — at least one issue is closed or status-done without a
  merge-attesting record. The findings are advisory: a wrap/morning reader
  surfaces them for an operator, they never block a run.

Pure data in / structured report out: no network, subprocess, clock, or random.
The CLI does the I/O (live issue state + labels, ledger records) and feeds the
booleans/records here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ledger import RECORD_TYPE_SHIP_RUN

SCHEMA_VERSION = "keel.close-reconcile.v1"

VERDICT_OK = "ok"
VERDICT_FLAGGED = "flagged"

FINDING_PREMATURE_CLOSE = "premature-close"
FINDING_PREMATURE_STATUS_DONE = "premature-status-done"

# The default label that marks an issue as done. Resolved from
# ``policy_pack.status_transitions.done`` by the CLI; this constant is the
# fallback when a project does not configure one.
DEFAULT_DONE_LABEL = "status:done"


@dataclass(frozen=True)
class ObservedIssue:
    """The observed lifecycle state of one issue plus its ledger record.

    ``closed`` and ``labels`` come from the live host (``gh issue view``).
    ``record`` is the most recent ship_run ledger record for the issue's PR (or
    ``None`` when the issue has no recorded ship run). All offline-supplyable so
    tests are deterministic.
    """

    number: int
    closed: bool = False
    labels: tuple[str, ...] = field(default_factory=tuple)
    record: dict[str, Any] | None = None

    def has_label(self, label: str) -> bool:
        return label in self.labels


def record_attests_merge(record: dict[str, Any] | None) -> bool:
    """Return whether a ledger ``record`` attests a clear-to-merge decision.

    ``True`` only for a ship_run record whose ``assessment.merge.action`` is
    exactly ``"merge"`` — the strongest merge signal a pure ledger reader has
    (it is the *decision*, not a confirmed post-merge fact; see the module
    docstring for that blind spot). A non-dict record, a record of another type,
    a missing or malformed ``assessment``/``merge`` block, or a ``defer``/
    ``block`` decision all degrade to ``False`` — a corrupt or absent record can
    never vouch for a merge the ledger did not witness. Pure — reads only its
    argument.
    """
    if not isinstance(record, dict):
        return False
    if record.get("record_type") != RECORD_TYPE_SHIP_RUN:
        return False
    assessment = record.get("assessment")
    if not isinstance(assessment, dict):
        return False
    merge = assessment.get("merge")
    if not isinstance(merge, dict):
        return False
    return merge.get("action") == "merge"


def latest_record_for_issue(
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    issue_number: int,
) -> dict[str, Any] | None:
    """Return the most recent ship_run record for ``issue_number``.

    Records are appended chronologically, so the last match is the latest ship
    run for that issue. Non-dict entries and records of another type are skipped;
    a missing or malformed ``issue`` block never matches. Returns ``None`` when no
    record matches. Pure — reads only its arguments.
    """
    match: dict[str, Any] | None = None
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("record_type") != RECORD_TYPE_SHIP_RUN:
            continue
        issue = record.get("issue")
        number = issue.get("number") if isinstance(issue, dict) else None
        if number == issue_number:
            match = record
    return match


def reconcile(
    observed: list[ObservedIssue] | tuple[ObservedIssue, ...],
    *,
    done_label: str = DEFAULT_DONE_LABEL,
) -> dict[str, Any]:
    """Reconcile each issue's lifecycle state against its ledger record.

    For every issue in ``observed``: if it is closed without a merge-attesting
    record, flag ``premature-close``; if it carries ``done_label`` without a
    merge-attesting record, flag ``premature-status-done``. An issue that is open
    and not status-done is never flagged regardless of its record; a
    closed/status-done issue *with* a merge-attesting record is consistent.

    Returns a structured report: a per-issue assessment, a flat findings list,
    the verdict, and a summary. Pure — reads only its arguments.
    """
    issues: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for issue in observed:
        attested = record_attests_merge(issue.record)
        status_done = issue.has_label(done_label)
        issue_findings: list[str] = []
        if issue.closed and not attested:
            issue_findings.append(FINDING_PREMATURE_CLOSE)
            findings.append({
                "issue": issue.number,
                "finding": FINDING_PREMATURE_CLOSE,
                "message": (
                    f"issue #{issue.number} is closed but no ship_run ledger "
                    f"record attests a merge"
                ),
            })
        if status_done and not attested:
            issue_findings.append(FINDING_PREMATURE_STATUS_DONE)
            findings.append({
                "issue": issue.number,
                "finding": FINDING_PREMATURE_STATUS_DONE,
                "message": (
                    f"issue #{issue.number} carries {done_label!r} but no "
                    f"ship_run ledger record attests a merge"
                ),
            })
        issues.append({
            "issue": issue.number,
            "closed": issue.closed,
            "status_done": status_done,
            "merge_attested": attested,
            "findings": issue_findings,
            "consistent": not issue_findings,
        })
    verdict = VERDICT_FLAGGED if findings else VERDICT_OK
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": verdict,
        "ok": verdict == VERDICT_OK,
        "done_label": done_label,
        "issues": issues,
        "findings": findings,
        "summary": {
            "observed": len(issues),
            "flagged": sum(1 for item in issues if not item["consistent"]),
            "findings": len(findings),
        },
    }
