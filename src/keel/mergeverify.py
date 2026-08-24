"""Did the merge apply what was reviewed? — the pure comparison (issue #561).

`keel merge` proves a merge *succeeded*. It does not prove the merge applied the
diff that was reviewed, and those came apart twice in one day while shipping
1.8.1/1.8.2: a `gh api …/update-branch` merge commit followed by a GitHub
squash-merge silently reverted unrelated already-merged work. Neither revert was
caught by CI, because the reverted state was internally consistent — old code with
no test for the removed behaviour — so the suite stayed green throughout.

**Why the obvious check does not work.** The first version of this module compared
the PR's file set against the merge's file set, on the theory that a revert shows up
as the merge touching files the PR never did. Run against the actual incident
(#543's squash reverting #550) it reported **clean**, and the reason is the whole
point: `update-branch` had already pulled the reverting state into the branch, so
GitHub computed the PR's own diff — the thing a reviewer reads — as *including*
those files. The revert was inside the reviewed diff. Scope comparison cannot see it.

**What does work** is the timing fingerprint the incident actually leaves:

* #550 merged at 13:02, touching ``src/keel/github.py`` and three others;
* #543 had branched on the 8th, **before** that;
* #543 merged at 15:19 and its commit **removed 41 lines** from ``github.py`` —
  a file a "label the search input" change has no reason to touch at all.

So the question to ask is: *did this merge write to files that some other pull
request changed after this one branched?* That is the only way an update-branch
squash can undo merged work, and it is cheap to answer. It is a **look at this**
signal rather than a proof — two PRs editing one file in sequence is ordinary — so
the report names the overtaking PR for each file and lets a human judge.

Pure: lists in, report out. The CLI does every GitHub read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: Report shape version, so a consumer can tell an old report from a new one.
SCHEMA_VERSION = "keel.merge-verify.v1"


def verify_merge(
    landed: Sequence[str] | None,
    overtaken: Mapping[str, int] | None = None,
    intended: Sequence[str] | None = None,
) -> dict:
    """Judge whether a merge may have silently reverted other merged work.

    ``landed`` is what the merge commit changed. ``overtaken`` maps a path to the
    pull request that changed it **after this PR branched and before this PR
    merged** — the window in which a stale branch can carry a revert. ``intended``
    is the PR's own file list, used only for the weaker secondary signal.

    ``None`` for ``landed`` means *not observed* and yields ``unknown`` rather than
    a clean bill: failing to look is not evidence that nothing drifted.

    ``status``:

    * ``drift``      — the merge wrote to files another PR changed after this one
      branched. The silent-revert shape; loud, and names the overtaking PR.
    * ``out-of-scope`` — no overtaking, but the merge changed files the PR's own
      diff did not list. A different (rarer) way for a merge to do more than it said.
    * ``clean``      — neither.
    * ``unknown``    — nothing could be read.
    """
    if landed is None:
        return _report("unknown", "could not read the merge commit's file list from GitHub")
    landed_set = {p.strip() for p in landed if p.strip()}
    collisions = {
        path: pr for path, pr in (overtaken or {}).items()
        if path.strip() and path.strip() in landed_set
    }
    if collisions:
        listed = ", ".join(f"{p} (#{pr})" for p, pr in sorted(collisions.items()))
        return _report(
            "drift",
            f"the merge wrote to {len(collisions)} file(s) that another pull request "
            f"changed after this one branched — the shape of a silent revert: {listed}",
            overtaken=dict(sorted(collisions.items())),
            landed_count=len(landed_set),
        )
    if intended is not None:
        unexpected = sorted(landed_set - {p.strip() for p in intended if p.strip()})
        if unexpected:
            return _report(
                "out-of-scope",
                f"the merge changed {len(unexpected)} file(s) the pull request's own "
                "diff did not list",
                unexpected=unexpected,
                landed_count=len(landed_set),
            )
    return _report(
        "clean",
        "no file in this merge was changed by another pull request after this one "
        "branched",
        landed_count=len(landed_set),
    )


def _report(status: str, reason: str, **extra) -> dict:
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "overtaken": {},
        "unexpected": [],
        "landed_count": 0,
        # Present on every report, so a consumer never has to distinguish
        # "complete" from "this key was added later". Set by the caller when a
        # finding is kept despite an input it could not read.
        "incomplete": False,
    }
    report.update(extra)
    return report


def is_drift(report: dict) -> bool:
    """True when the report is the loud case a human must look at."""
    return isinstance(report, dict) and report.get("status") == "drift"


def render(report: dict) -> str:
    """Human-readable one-block summary."""
    lines = [
        f"keel verify-merge — {report.get('status', 'unknown')}",
        f"  {report.get('reason', '')}",
    ]
    for path, pr in (report.get("overtaken") or {}).items():
        lines.append(f"    {path}  — also changed by #{pr} after this PR branched")
    for path in report.get("unexpected") or []:
        lines.append(f"    {path}  — not in the PR's own diff")
    return "\n".join(lines)
