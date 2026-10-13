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

import re
from collections.abc import Mapping, Sequence

#: Report shape version, so a consumer can tell an old report from a new one.
SCHEMA_VERSION = "keel.merge-verify.v1"

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def extract_added_lines_by_file(diff_text: str) -> dict[str, list[str]]:
    """Extract added lines from diff hunks, keyed by target file path (#1511)."""
    if not diff_text:
        return {}
    result: dict[str, list[str]] = {}
    current_file: str | None = None
    in_hunk = False
    rem_old = 0
    rem_new = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            current_file = None
            continue
        if not in_hunk:
            if line.startswith("--- "):
                continue
            if line.startswith("+++ "):
                target = line[4:].strip()
                if target == "/dev/null":
                    current_file = None
                else:
                    if target.startswith('"') and target.endswith('"'):
                        target = target[1:-1]
                    if target.startswith(("b/", "a/")):
                        target = target[2:]
                    current_file = target
                    result.setdefault(current_file, [])
                continue
        m = _HUNK_HEADER_RE.match(line)
        if m:
            rem_old = int(m.group(2)) if m.group(2) is not None else 1
            rem_new = int(m.group(4)) if m.group(4) is not None else 1
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith("\\"):
                continue
            if line.startswith("+"):
                result[current_file].append(line[1:])  # type: ignore[index]
                rem_new -= 1
            elif line.startswith("-"):
                rem_old -= 1
            else:
                rem_old -= 1
                rem_new -= 1
            if rem_old <= 0 and rem_new <= 0:
                in_hunk = False
    return result


def check_line_survival(added_lines: Sequence[str], file_content: str) -> tuple[int, list[str]]:
    """Check how many added lines survive in the target file content (#1511).

    Returns ``(lost_count, lost_lines)``. Blank lines are ignored as formatting/spacing
    rather than reverted logic.
    """
    if not file_content:
        lost = [line for line in added_lines if line.strip()]
        return len(lost), lost

    target_lines = set(file_content.splitlines())
    target_stripped = {line.strip() for line in target_lines if line.strip()}
    lost = []
    for line in added_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if line not in target_lines and stripped not in target_stripped:
            lost.append(line)
    return len(lost), lost


def verify_merge(
    landed: Sequence[str] | None,
    overtaken: Mapping[str, int] | None = None,
    intended: Sequence[str] | None = None,
    *,
    diffs: Mapping[int, str | None] | None = None,
    file_contents: Mapping[str, str | None] | None = None,
) -> dict:
    """Judge whether a merge may have silently reverted other merged work.

    ``landed`` is what the merge commit changed. ``overtaken`` maps a path to the
    pull request that changed it **after this PR branched and before this PR
    merged** — the window in which a stale branch can carry a revert. ``intended``
    is the PR's own file list, used only for the weaker secondary signal.
    ``diffs`` maps an overtaking PR number to its unified diff string.
    ``file_contents`` maps a file path to its content at the merge commit.

    ``None`` for ``landed`` means *not observed* and yields ``unknown`` rather than
    a clean bill: failing to look is not evidence that nothing drifted.

    ``status``:

    * ``drift``      — the merge wrote to files another PR changed after this one
      branched and lost added lines from that PR (or diffs could not be read).
      The silent-revert shape; loud, and names the overtaking PR.
    * ``out-of-scope`` — no overtaking drift, but the merge changed files the PR's
      own diff did not list. A different (rarer) way for a merge to do more than it said.
    * ``clean``      — neither (including when shared files have 0 lost lines).
    * ``unknown``    — nothing could be read.
    """
    if landed is None:
        return _report("unknown", "could not read the merge commit's file list from GitHub")
    landed_set = {p.strip() for p in landed if p.strip()}
    collisions = {
        path: pr
        for path, pr in (overtaken or {}).items()
        if path.strip() and path.strip() in landed_set
    }
    if collisions:
        drifted: dict[str, int] = {}
        if diffs is None or file_contents is None:
            drifted = dict(collisions)
        else:
            parsed_diffs: dict[int, dict[str, list[str]]] = {}
            for path, pr in collisions.items():
                pr_diff = diffs.get(pr)
                content = file_contents.get(path)
                if pr_diff is None or content is None:
                    drifted[path] = pr
                    continue
                if pr not in parsed_diffs:
                    parsed_diffs[pr] = extract_added_lines_by_file(pr_diff)
                if path not in parsed_diffs[pr]:
                    drifted[path] = pr
                    continue
                added_lines = parsed_diffs[pr][path]
                lost_count, _ = check_line_survival(added_lines, content)
                if lost_count > 0:
                    drifted[path] = pr

        if drifted:
            listed = ", ".join(f"{p} (#{pr})" for p, pr in sorted(drifted.items()))
            return _report(
                "drift",
                f"the merge wrote to {len(drifted)} file(s) that another pull request "
                f"changed after this one branched — the shape of a silent revert: {listed}",
                overtaken=dict(sorted(drifted.items())),
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

    if collisions:
        file_suffix = "s" if len(collisions) != 1 else ""
        return _report(
            "clean",
            f"clean ({len(collisions)} shared file{file_suffix}, 0 lost lines)",
            landed_count=len(landed_set),
        )

    return _report(
        "clean",
        "no file in this merge was changed by another pull request after this one branched",
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
