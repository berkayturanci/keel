"""Did the merge apply what was reviewed? — the pure comparison (issue #561, #1511).

`keel merge` proves a merge *succeeded*. It does not prove the merge applied the
diff that was reviewed, and those came apart twice in one day while shipping
1.8.1/1.8.2: a `gh api …/update-branch` merge commit followed by a GitHub
squash-merge silently reverted unrelated already-merged work. Neither revert was
caught by CI, because the reverted state was internally consistent — old code with
no test for the removed behaviour — so the suite stayed green throughout.

**Why the obvious checks do not work.**
1. The first version of this module compared the PR's file set against the merge's
   file set, on the theory that a revert shows up as the merge touching files the PR
   never did. Run against the actual incident (#543's squash reverting #550) it reported
   **clean**, and the reason is the whole point: `update-branch` had already pulled
   the reverting state into the branch, so GitHub computed the PR's own diff — the
   thing a reviewer reads — as *including* those files. The revert was inside the
   reviewed diff. Scope comparison cannot see it.
2. Checking line survival via simple set membership reported false clean when generic
   lines (like `}`, `return None`, or boilerplate) were reverted while existing elsewhere
   in the file, or when deletions were silently re-added.

**What does work** is multiset line counting per overtaking PR (#1511):
The timing fingerprint the incident actually leaves:
* #550 merged at 13:02, touching ``src/keel/github.py`` and three others;
* #543 had branched on the 8th, **before** that;
* #543 merged at 15:19 and its commit **removed 41 lines** from ``github.py`` —
  a file a "label the search input" change has no reason to touch at all.

For every file shared between this merge and pull requests that merged in the window
(branch point, merge commit), compare the file content as of each overtaking PR's merge
(its landed version) against the merge commit's content:
- For every line the PR added, its count in the merge commit must be >= its count in
  the PR's landed version.
- For every line the PR deleted, its count in the merge commit must be <= its count in
  the PR's landed version (catching re-added deletions).
- Blank lines are ignored.
- A PR that deleted a whole file (`+++ /dev/null`): the file reappearing in the merge
  commit is drift; staying absent is clean.
- Unreadable content at either commit is distinguished from confirmed absent files
  via :data:`FILE_ABSENT`, ensuring failed reads are reported as drift, not stayed deletions.
- Any unreadable input (missing diff, malformed diff, unreadable file content) stays
  conservative drift.

Pure: lists in, report out. The CLI does every GitHub read.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class _FileAbsent:
    """Sentinel indicating a file is confirmed absent in a commit/tree (#1511)."""

    def __repr__(self) -> str:
        return "<FILE_ABSENT>"


#: Sentinel object representing a file confirmed absent (e.g. 404 or not in tree).
FILE_ABSENT = _FileAbsent()

#: Report shape version, so a consumer can tell an old report from a new one (#1511).
SCHEMA_VERSION = "keel.merge-verify.v2"

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _split_lines(text: str) -> list[str]:
    """Split text on newlines (\\r\\n, \\r, \\n) without splitting on form feeds/VT (#1511)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


@dataclass(frozen=True)
class FileDiff:
    """Parsed additions and deletions for a single file in a unified diff (#1511)."""

    path: str
    added_lines: tuple[str, ...]
    deleted_lines: tuple[str, ...]
    is_deleted: bool = False


def _clean_diff_path(raw: str, *, is_new: bool) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = ast.literal_eval(raw)
        except Exception:
            raw = raw[1:-1]
    if is_new and raw.startswith("b/"):
        return raw[2:]
    if not is_new and raw.startswith("a/"):
        return raw[2:]
    return raw


def parse_diff_by_file(diff_text: str | None) -> dict[str, FileDiff] | None:
    """Parse unified diff text into FileDiff objects keyed by target file path (#1511).

    Returns None if the diff is malformed or truncated (incomplete hunks).
    """
    if diff_text is None:
        return None
    if not diff_text.strip():
        return {}

    result: dict[str, FileDiff] = {}
    current_file: str | None = None
    old_file: str | None = None
    is_deleted = False
    in_hunk = False
    rem_old = 0
    rem_new = 0
    current_added: list[str] = []
    current_deleted: list[str] = []
    saw_hunk_for_file = False

    def _flush_current_file() -> bool:
        nonlocal \
            current_file, \
            old_file, \
            is_deleted, \
            current_added, \
            current_deleted, \
            saw_hunk_for_file
        if current_file is not None:
            if not saw_hunk_for_file:
                return False
            result[current_file] = FileDiff(
                path=current_file,
                added_lines=tuple(current_added),
                deleted_lines=tuple(current_deleted),
                is_deleted=is_deleted,
            )
        current_file = None
        old_file = None
        is_deleted = False
        current_added = []
        current_deleted = []
        saw_hunk_for_file = False
        return True

    for line in _split_lines(diff_text):
        if line.startswith("diff --git "):
            if in_hunk:
                return None
            if not _flush_current_file():
                return None
            continue

        if not in_hunk:
            if line.startswith(("+", "-")) and not line.startswith(("--- ", "+++ ")):
                return None
            if line.startswith("--- "):
                target = line[4:].strip()
                if target == "/dev/null":
                    old_file = None
                else:
                    old_file = _clean_diff_path(target, is_new=False)
                continue
            if line.startswith("+++ "):
                target = line[4:].strip()
                if target == "/dev/null":
                    if old_file is None:
                        return None
                    current_file = old_file
                    is_deleted = True
                else:
                    current_file = _clean_diff_path(target, is_new=True)
                    is_deleted = False
                continue

        m = _HUNK_HEADER_RE.match(line)
        if m:
            if current_file is None or in_hunk:
                return None
            saw_hunk_for_file = True
            rem_old = int(m.group(2)) if m.group(2) is not None else 1
            rem_new = int(m.group(4)) if m.group(4) is not None else 1
            in_hunk = True
            if rem_old == 0 and rem_new == 0:
                in_hunk = False
            continue

        if in_hunk:
            if line.startswith("\\"):
                continue
            if line.startswith("+"):
                current_added.append(line[1:])
                rem_new -= 1
            elif line.startswith("-"):
                current_deleted.append(line[1:])
                rem_old -= 1
            else:
                rem_old -= 1
                rem_new -= 1
            if rem_old < 0 or rem_new < 0:
                return None
            if rem_old == 0 and rem_new == 0:
                in_hunk = False

    if in_hunk:
        return None
    if not _flush_current_file():
        return None

    return result


def extract_added_lines_by_file(diff_text: str | None) -> dict[str, list[str]]:
    """Extract added lines from diff hunks, keyed by target file path (#1511)."""
    parsed = parse_diff_by_file(diff_text)
    if parsed is None:
        return {}
    return {path: list(fd.added_lines) for path, fd in parsed.items()}


def check_multiset_survival(
    added_lines: Sequence[str],
    deleted_lines: Sequence[str],
    landed_content: str | object | None,
    merge_content: str | object | None,
    *,
    is_deleted: bool = False,
    this_pr_added_lines: Sequence[str] | None = None,
) -> tuple[int, list[str]]:
    """Check line survival via multiset counting (#1511).

    Returns ``(lost_count, violations)``.
    Ignore blank lines only.
    """
    if is_deleted:
        if merge_content is FILE_ABSENT:
            return 0, []
        if merge_content is None:
            return 1, ["unreadable file content"]
        return 1, ["deleted file reappeared in merge commit"]

    if landed_content is None or merge_content is None:
        return 1, ["unreadable file content"]

    if landed_content is FILE_ABSENT or merge_content is FILE_ABSENT:
        return 1, ["file missing in merge commit"]

    assert isinstance(landed_content, str)
    assert isinstance(merge_content, str)

    landed_lines = [line for line in _split_lines(landed_content) if line.strip()]
    merge_lines = [line for line in _split_lines(merge_content) if line.strip()]

    landed_counts = Counter(landed_lines)
    merge_counts = Counter(merge_lines)

    violations: list[str] = []

    for line in added_lines:
        if not line.strip():
            continue
        if merge_counts[line] < landed_counts[line]:
            if line not in violations:
                violations.append(line)

    excluded_lines = (
        {line.strip() for line in this_pr_added_lines if line.strip()}
        if this_pr_added_lines is not None
        else set()
    )

    for line in deleted_lines:
        if not line.strip() or line.strip() in excluded_lines:
            continue
        if merge_counts[line] > landed_counts[line]:
            if line not in violations:
                violations.append(line)

    return len(violations), violations


def check_line_survival(
    added_lines: Sequence[str],
    landed_or_merge_content: str | object | None,
    merge_content: str | object | None = None,
    deleted_lines: Sequence[str] | None = None,
    *,
    is_deleted: bool = False,
    this_pr_added_lines: Sequence[str] | None = None,
) -> tuple[int, list[str]]:
    """Check how many added/deleted lines survive in the target file content (#1511).

    Supports both 2-arg legacy call and full multiset call.
    """
    if merge_content is None and deleted_lines is None:
        landed_content = "\n".join(added_lines) if added_lines else ""
        return check_multiset_survival(
            added_lines,
            (),
            landed_content,
            landed_or_merge_content,
            is_deleted=is_deleted,
            this_pr_added_lines=this_pr_added_lines,
        )
    return check_multiset_survival(
        added_lines,
        deleted_lines or (),
        landed_or_merge_content,
        merge_content,
        is_deleted=is_deleted,
        this_pr_added_lines=this_pr_added_lines,
    )


def verify_merge(
    landed: Sequence[str] | None,
    overtaken: Mapping[str, int | Sequence[int]] | None = None,
    intended: Sequence[str] | None = None,
    *,
    diffs: Mapping[int, str | None] | None = None,
    file_contents: Mapping[str, str | None] | None = None,
    pr_file_contents: Mapping[int, Mapping[str, str | None]]
    | Mapping[tuple[int, str], str | None]
    | None = None,
) -> dict:
    """Judge whether a merge may have silently reverted other merged work.

    ``landed`` is what the merge commit changed. ``overtaken`` maps each path to all
    pull request numbers that changed it **after this PR branched and before this PR
    merged** (#1511). ``intended`` is the PR's own file list.
    ``diffs`` maps an overtaking PR number to its unified diff string.
    ``file_contents`` maps a file path to its content at the merge commit.
    ``pr_file_contents`` maps an overtaking PR and file path to the file content as of
    that PR's merge commit (its landed version).

    ``None`` for ``landed`` means *not observed* and yields ``unknown`` rather than
    a clean bill: failing to look is not evidence that nothing drifted.

    ``status``:

    * ``drift``      — the merge wrote to files another PR changed after this one
      branched and lost added lines or re-added deleted lines (or inputs unreadable).
      The silent-revert shape; loud, and names the overtaking PR.
    * ``out-of-scope`` — no overtaking drift, but the merge changed files the PR's
      own diff did not list. A different (rarer) way for a merge to do more than it said.
    * ``clean``      — neither (including when shared files have 0 lost lines).
    * ``unknown``    — nothing could be read.
    """
    if landed is None:
        return _report("unknown", "could not read the merge commit's file list from GitHub")
    landed_set = {p.strip() for p in landed if p.strip()}

    norm_overtaken: dict[str, list[int]] = {}
    for path, prs in (overtaken or {}).items():
        p = path.strip()
        if not p:
            continue
        if isinstance(prs, int):
            norm_overtaken[p] = [prs]
        else:
            norm_overtaken[p] = list(dict.fromkeys(prs))

    collisions = {path: pr_list for path, pr_list in norm_overtaken.items() if path in landed_set}

    if collisions:
        drifted: dict[str, list[int]] = {}
        drift_details: dict[str, list[str]] = {}

        if diffs is None or file_contents is None:
            drifted = {p: list(prs) for p, prs in collisions.items()}
        else:
            parsed_diffs: dict[int, dict[str, FileDiff] | None] = {}
            for path, prs in sorted(collisions.items()):
                merge_content = file_contents.get(path)
                for pr in sorted(prs):
                    if pr not in parsed_diffs:
                        pr_diff = diffs.get(pr)
                        parsed_diffs[pr] = (
                            parse_diff_by_file(pr_diff) if pr_diff is not None else None
                        )

                    pr_parsed = parsed_diffs[pr]
                    if pr_parsed is None:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"unreadable or malformed diff for #{pr}"
                        )
                        continue

                    if path not in pr_parsed:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"{path} not found in parsed diff for #{pr}"
                        )
                        continue

                    fd = pr_parsed[path]
                    if fd.is_deleted:
                        if merge_content is FILE_ABSENT:
                            continue
                        if merge_content is None:
                            drifted.setdefault(path, []).append(pr)
                            drift_details.setdefault(path, []).append(
                                f"unreadable merge commit content for {path}"
                            )
                            continue
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"deleted file reappeared in merge commit (#{pr})"
                        )
                        continue

                    if merge_content is None:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"unreadable merge commit content for {path}"
                        )
                        continue

                    if merge_content is FILE_ABSENT:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"file missing in merge commit for {path}"
                        )
                        continue

                    pr_landed_content = None
                    if pr_file_contents is not None:
                        if isinstance(pr_file_contents.get(pr), Mapping):
                            pr_landed_content = pr_file_contents[pr].get(path)
                        elif (pr, path) in pr_file_contents:
                            pr_landed_content = pr_file_contents.get((pr, path))

                    if pr_landed_content is None:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"unreadable landed content for {path} (#{pr})"
                        )
                        continue

                    if pr_landed_content is FILE_ABSENT:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"landed content absent for {path} (#{pr})"
                        )
                        continue

                    lost_count, violations = check_multiset_survival(
                        fd.added_lines,
                        fd.deleted_lines,
                        pr_landed_content,
                        merge_content,
                        is_deleted=fd.is_deleted,
                    )
                    if lost_count > 0:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).extend(violations)

        if drifted:
            listed_parts = []
            for p, pr_list in sorted(drifted.items()):
                prs_str = ", ".join(f"#{pr}" for pr in sorted(pr_list))
                listed_parts.append(f"{p} ({prs_str})")
            listed = ", ".join(listed_parts)
            return _report(
                "drift",
                f"the merge wrote to {len(drifted)} file(s) that another pull request "
                f"changed after this one branched — the shape of a silent revert: {listed}",
                overtaken=dict(sorted(drifted.items())),
                landed_count=len(landed_set),
                shared_files=sorted(collisions.keys()),
                drift_details=drift_details,
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
                shared_files=sorted(collisions.keys()) if collisions else [],
            )

    if collisions:
        file_suffix = "s" if len(collisions) != 1 else ""
        return _report(
            "clean",
            f"clean ({len(collisions)} shared file{file_suffix}, 0 lost lines)",
            landed_count=len(landed_set),
            shared_files=sorted(collisions.keys()),
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
        "shared_files": [],
        "drift_details": {},
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
    drift_details = report.get("drift_details") or {}
    for path, prs in (report.get("overtaken") or {}).items():
        if isinstance(prs, int):
            prs_str = f"#{prs}"
        else:
            prs_str = ", ".join(f"#{pr}" for pr in sorted(prs))
        lines.append(f"    {path}  — also changed by {prs_str} after this PR branched")
        for detail in drift_details.get(path) or []:
            lines.append(f"      * {detail}")
    for path in report.get("unexpected") or []:
        lines.append(f"    {path}  — not in the PR's own diff")
    return "\n".join(lines)
