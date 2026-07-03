"""Pure post-hoc dry-run integrity reconciliation.

``--dry-run`` is a promise: a rehearsal redirects every state-changing
``git``/``gh`` write to a logged ``DRY-RUN:`` line and mutates nothing (see the
ship adapter and :func:`keel.cli`'s dry-run paths). But the promise is prose —
nothing *detects* a leak where a dry run actually created a ledger record, a
branch, or a PR. That is the audit gap (GAP-14) this module closes.

The check is a before/after snapshot diff. The operator captures a snapshot of
the relevant artifacts *before* the dry run, runs the dry run, then this module
diffs a fresh *after* snapshot against the before one and flags any new artifact
attributable to the rehearsed run:

* **new ledger record** — a ship_run ``run_id`` equal to the dry run's id that
  appears in the after snapshot but not the before one. A dry run must append no
  record, so any such record is a leak.
* **new branch** — a branch matching the run's issue (``<type>/issue-<N>`` in the
  ship-branch vocabulary) present after but not before.
* **new PR** — a pull request present after but not before. The CLI scopes the
  PR snapshot to the run's issue, so any new entry is attributable.

The verdict is **clean** when the after snapshot adds nothing, **violated** when
it adds at least one attributable artifact. The findings are an integrity
*detection* — the rehearsal already ran, so this cannot prevent a leak, only
surface it post-hoc for an operator (hence the audit-grade, advisory framing).

**Trust model — the before-snapshot is unauthenticated input.** This check is
only as honest as the baseline it is handed: an operator (or agent) that
captures the "before" snapshot *after* the leak, or hand-writes a baseline that
already lists the branch/PR it intends to leak, gets a clean verdict. It
therefore assumes the baseline was captured by an honest party *before* the
rehearsal, and provides no defense against an agent forging its own baseline. A
stronger future design would have keel itself emit the before-snapshot as a
side effect of starting the dry run rather than trusting a supplied file.

**Attribution scope (deliberately narrow, to avoid false positives).** The
ledger leg keys on an exact ``run_id`` match — a record mis-attributed to a
different run_id is out of scope. The branch leg recognises only keel's
ship-branch vocabulary (``<type>/issue-<N>``); a leak on a non-conforming branch
name with no accompanying PR is not detected. These mirror keel's own write
paths, so a leak that *follows keel's conventions* is caught.

**The after-snapshot is read fail-closed by the CLI** (a failed git/gh read
raises rather than degrading to empty), because an empty-on-error after snapshot
would compute ``after − before = ∅`` and wrongly report "clean" — the worst
fail direction for a detector. A verifier that cannot observe says "cannot
certify", never "clean".

Pure data in / structured report out: no network, subprocess, clock, or random.
The CLI does the I/O (reads the ledger, lists branches, lists the issue's PRs)
and feeds the snapshots here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "keel.dryrun-verify.v1"

VERDICT_CLEAN = "clean"
VERDICT_VIOLATED = "violated"

FINDING_NEW_LEDGER_RECORD = "new-ledger-record"
FINDING_NEW_BRANCH = "new-branch"
FINDING_NEW_PR = "new-pr"

# The ship-branch type prefixes (mirrors keel.evidence._SHIP_BRANCH_RE). A dry
# run for issue N must not create a branch named ``<type>/issue-N``.
_BRANCH_TYPES = ("feature", "fix", "chore", "docs", "test")


def issue_branch_pattern(issue: int) -> re.Pattern[str]:
    """Return the compiled branch pattern that a dry run for ``issue`` must not create.

    Matches ``<type>/issue-<N>`` optionally followed by ``-<slug>`` — the same
    shape keel's ship-branch detection recognises, pinned to the specific issue
    number so a branch for a *different* issue is never mis-attributed.
    """
    types = "|".join(_BRANCH_TYPES)
    return re.compile(rf"^({types})/issue-{issue}(?:-|$)")


@dataclass(frozen=True)
class ArtifactSnapshot:
    """A snapshot of the artifacts a dry run could leak.

    ``ledger_run_ids`` are the ship_run ``run_id`` values present in the run
    ledger; ``branches`` are local/remote branch names; ``pr_numbers`` are pull
    request numbers (CLI-scoped to the run's issue). All offline-supplyable so
    tests are deterministic.
    """

    ledger_run_ids: tuple[str, ...] = field(default_factory=tuple)
    branches: tuple[str, ...] = field(default_factory=tuple)
    pr_numbers: tuple[int, ...] = field(default_factory=tuple)


def reconcile(
    before: ArtifactSnapshot,
    after: ArtifactSnapshot,
    *,
    run_id: str,
    issue: int,
) -> dict[str, Any]:
    """Diff a before/after snapshot pair and flag dry-run integrity leaks.

    Flags a new ledger record whose ``run_id`` equals ``run_id``, any new branch
    matching ``issue``'s ship-branch pattern, and any new PR. ``before``/``after``
    are the artifact snapshots taken around the rehearsed run. Returns a
    structured report: the per-artifact additions, a flat findings list, the
    verdict, and a summary. Pure — reads only its arguments.
    """
    pattern = issue_branch_pattern(issue)
    new_run_ids = _added(after.ledger_run_ids, before.ledger_run_ids)
    new_branches = _added(after.branches, before.branches)
    new_prs = _added(after.pr_numbers, before.pr_numbers)

    findings: list[dict[str, Any]] = []
    leaked_records = [rid for rid in new_run_ids if rid == run_id]
    for rid in leaked_records:
        findings.append({
            "finding": FINDING_NEW_LEDGER_RECORD,
            "artifact": rid,
            "message": (
                f"dry run {run_id!r} left a new ledger record (run_id {rid!r}); "
                f"a rehearsal must append no record"
            ),
        })
    leaked_branches = [name for name in new_branches if pattern.search(name)]
    for name in leaked_branches:
        findings.append({
            "finding": FINDING_NEW_BRANCH,
            "artifact": name,
            "message": (
                f"dry run {run_id!r} left a new branch {name!r} for issue "
                f"#{issue}; a rehearsal must create no branch"
            ),
        })
    for number in new_prs:
        findings.append({
            "finding": FINDING_NEW_PR,
            "artifact": number,
            "message": (
                f"dry run {run_id!r} left a new PR #{number} for issue #{issue}; "
                f"a rehearsal must open no PR"
            ),
        })

    verdict = VERDICT_VIOLATED if findings else VERDICT_CLEAN
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": verdict,
        "ok": verdict == VERDICT_CLEAN,
        "run_id": run_id,
        "issue": issue,
        "added": {
            "ledger_run_ids": list(new_run_ids),
            "branches": list(new_branches),
            "pr_numbers": list(new_prs),
        },
        "findings": findings,
        "summary": {
            "new_ledger_records": len(leaked_records),
            "new_branches": len(leaked_branches),
            "new_prs": len(new_prs),
            "findings": len(findings),
        },
    }


def _added(after: tuple[Any, ...], before: tuple[Any, ...]) -> list[Any]:
    """Return items present in ``after`` but not ``before``, in ``after`` order.

    Order-preserving and de-duplicated so the report is deterministic regardless
    of how the CLI gathered the snapshots.
    """
    seen = frozenset(before)
    return list(dict.fromkeys(item for item in after if item not in seen))
