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

**What does work** is the timing fingerprint the incident actually leaves:

* #550 merged at 13:02, touching ``src/keel/github.py`` and three others;
* #543 had branched on the 8th, **before** that;
* #543 merged at 15:19 and its commit **removed 41 lines** from ``github.py`` —
  a file a "label the search input" change has no reason to touch at all.

So the first question to ask is: *did this merge write to files that some other pull
request changed after this one branched?* That is the only way an update-branch
squash can undo merged work, and it is cheap to answer. Because two PRs editing one
file in sequence is ordinary, we refine file overlap with multiset line counting per
overtaking PR (#1511). It remains a **look at this** heuristic rather than a proof:
- False drift (conservative): if a later overtaking PR (or this PR) modifies a line an
  earlier overtaking PR added, or if an overtaking PR deleted a generic line that this
  PR legitimately adds, it is flagged so the report names the violating overtaking PR(s)
  for each drifted file and lets a human judge.
- False clean (same-line swap): because whole-file multiset counts are compared without
  subtracting this PR's own diff (which bakes in any `update-branch` + squash revert),
  reverting an overtaking PR's added line (such as `}`, `return None`, or `pass`) is
  masked if this PR also adds an identical line elsewhere in the same file (or deletes
  an identical line elsewhere while re-adding a deleted line), keeping the whole-file
  count `>=` (for additions) or `<=` (for deletions) the overtaking PR's landed count.

For every file shared between this merge and pull requests that merged in the window
(branch point, merge commit), compare the file content as of each overtaking PR's merge
(its landed version) against the merge commit's content:
- For every line the PR added, its count in the merge commit must be >= its count in
  the PR's landed version.
- For every line the PR deleted, its count in the merge commit must be <= its count in
  the PR's landed version (catching re-added deletions).
- Blank lines are ignored.
- A PR that deleted a whole file (`+++ /dev/null` or `rename from`): the file must be
  confirmed absent at both the overtaking PR's merge commit and this merge commit
  (`FILE_ABSENT`); the file reappearing in the merge commit is drift.
- Unreadable content at either commit is distinguished from confirmed absent files
  via :data:`FILE_ABSENT`, ensuring failed reads are reported as drift, not stayed deletions.
- Baseline mismatches at the overtaking PR's own merge commit (``deleted file still present
  in overtaking commit``, ``landed content absent``) and shared files whose diff record
  cannot be verified by text-line survival (multiple diff records for the same path such as
  file-to-symlink type replacements, file mode changes, non-deleted binary diffs, or hunkless
  non-deletion records) are classified as ``could not verify line survival (fail-closed)``,
  whereas a valid baseline at the overtaking PR's commit that disappears (``file missing in
  merge commit``) or reappears (``deleted file reappeared in merge commit``) at this merge
  commit is classified as ``the shape of a silent revert``.
- Any unreadable input (missing diff, malformed diff, unreadable file content) stays
  conservative drift.

Pure: lists in, report out. The CLI does every GitHub read.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .vocab import FILE_ABSENT as FILE_ABSENT
from .vocab import FileContent as FileContent

__all__ = [
    "FILE_ABSENT",
    "FileContent",
    "FileDiff",
    "SCHEMA_VERSION",
    "check_multiset_survival",
    "is_drift",
    "parse_diff_by_file",
    "render",
    "verify_merge",
]

#: Report shape version, so a consumer can tell an old report from a new one (#1511).
SCHEMA_VERSION = "keel.merge-verify.v2"

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDEX_RE = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)(?: \d+)?$")
_SIMILARITY_RE = re.compile(r"^similarity index (\d+)%$")
_EMPTY_BLOB_SHAS = (
    "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813",
)


def _is_empty_blob_sha(sha: str) -> bool:
    return len(sha) >= 7 and any(full.startswith(sha) for full in _EMPTY_BLOB_SHAS)


def _split_lines(text: str) -> list[str]:
    """Split text on LF or CRLF line endings without splitting on CR, FF, or VT (#1511)."""
    text = text.replace("\r\n", "\n")
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
    has_hunks: bool = True
    has_mode_change: bool = False
    is_binary: bool = False
    has_multiple_records: bool = False


@dataclass
class _FileParseState:
    """Mutable per-file accumulator used while parsing a unified diff."""

    in_file: bool = False
    has_ab_prefix: bool = True
    header_file: str | None = None
    current_file: str | None = None
    old_file: str | None = None
    rename_old: str | None = None
    is_deleted: bool = False
    current_added: list[str] = field(default_factory=list)
    current_deleted: list[str] = field(default_factory=list)
    saw_hunk_for_file: bool = False
    saw_minus_header: bool = False
    saw_plus_header: bool = False
    saw_old_mode: bool = False
    saw_new_mode: bool = False
    saw_new_file: bool = False
    saw_deleted_file: bool = False
    saw_rename_from: bool = False
    saw_rename_to: bool = False
    saw_copy_from: bool = False
    saw_copy_to: bool = False
    saw_binary: bool = False
    similarity_index: int | None = None
    index_shas: tuple[str, str] | None = None


def _unquote_diff_path(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = ast.literal_eval(raw).encode("latin-1").decode("utf-8")
        except Exception:
            raw = raw[1:-1]
    return raw


def _clean_diff_path(raw: str, *, is_new: bool, strip_prefix: bool = True) -> str:
    raw = _unquote_diff_path(raw)
    if strip_prefix:
        if is_new and raw.startswith("b/"):
            return raw[2:]
        if not is_new and raw.startswith("a/"):
            return raw[2:]
    return raw


def _parse_git_header(line: str) -> tuple[str, bool] | None:
    """Extract ``(target_path, has_ab_prefix)`` from a ``diff --git`` header line."""
    rest = line[len("diff --git ") :].strip()
    mid = len(rest) // 2
    if '" "' in rest:
        idx = rest.find('" "')
        left = _unquote_diff_path(rest[: idx + 1])
        right = _unquote_diff_path(rest[idx + 2 :])
    elif (
        len(rest) % 2 == 1
        and rest[mid] == " "
        and (rest[:mid] == rest[mid + 1 :] or rest[2:mid] == rest[mid + 3 :])
    ):
        left, right = rest[:mid], rest[mid + 1 :]
    elif rest.startswith("a/") and " b/" in rest:
        left_raw, right_raw = rest.split(" b/", 1)
        left, right = left_raw, "b/" + right_raw
    else:
        parts = rest.split(None, 1)
        if len(parts) < 2:
            return None
        left, right = parts[0], parts[1]
    if left == right:
        return right, False
    has_ab = left.startswith("a/") and right.startswith("b/")
    return (right[2:] if has_ab else right), has_ab


def parse_diff_by_file(diff_text: str | None) -> dict[str, FileDiff] | None:
    """Parse unified diff text into FileDiff objects keyed by target file path (#1511).

    Returns None if the diff is malformed or truncated (incomplete hunks).
    """
    if diff_text is None:
        return None
    if not diff_text.strip():
        return {}

    result: dict[str, FileDiff] = {}
    st = _FileParseState()
    in_hunk = False
    rem_old = 0
    rem_new = 0

    def _flush_current_file() -> bool:
        nonlocal st
        if st.in_file or st.saw_minus_header or st.saw_plus_header:
            if (
                st.current_file is None
                or st.saw_minus_header != st.saw_plus_header
                or st.saw_old_mode != st.saw_new_mode
                or st.saw_rename_from != st.saw_rename_to
                or st.saw_copy_from != st.saw_copy_to
                or (st.saw_plus_header and not st.saw_hunk_for_file)
            ):
                return False
            if not st.saw_plus_header and not st.saw_binary:
                if st.saw_new_file or st.saw_deleted_file:
                    if st.index_shas is None:
                        return False
                    expected_empty = st.index_shas[1] if st.saw_new_file else st.index_shas[0]
                    expected_zero = st.index_shas[0] if st.saw_new_file else st.index_shas[1]
                    if set(expected_zero) != {"0"} or not _is_empty_blob_sha(expected_empty):
                        return False
                elif st.saw_rename_to or st.saw_copy_to:
                    if st.similarity_index != 100 or (
                        st.index_shas is not None and st.index_shas[0] != st.index_shas[1]
                    ):
                        return False
                elif st.index_shas is not None and st.index_shas[0] != st.index_shas[1]:
                    return False
            prev = result.get(st.current_file)
            result[st.current_file] = FileDiff(
                path=st.current_file,
                added_lines=(prev.added_lines if prev else ()) + tuple(st.current_added),
                deleted_lines=(prev.deleted_lines if prev else ()) + tuple(st.current_deleted),
                is_deleted=st.is_deleted,
                has_hunks=(prev.has_hunks if prev else False) or st.saw_hunk_for_file,
                has_mode_change=(prev.has_mode_change if prev else False) or st.saw_old_mode,
                is_binary=(prev.is_binary if prev else False) or st.saw_binary,
                has_multiple_records=prev is not None,
            )
            if st.rename_old:
                prev_old = result.get(st.rename_old)
                result[st.rename_old] = FileDiff(
                    path=st.rename_old,
                    added_lines=prev_old.added_lines if prev_old else (),
                    deleted_lines=prev_old.deleted_lines if prev_old else (),
                    is_deleted=True if prev_old is None else prev_old.is_deleted,
                    has_hunks=prev_old.has_hunks if prev_old else False,
                    has_mode_change=prev_old.has_mode_change if prev_old else False,
                    is_binary=prev_old.is_binary if prev_old else False,
                    has_multiple_records=prev_old is not None,
                )
        st = _FileParseState()
        return True

    for line in _split_lines(diff_text):
        if line.startswith("diff --git "):
            if in_hunk or not _flush_current_file():
                return None
            header = _parse_git_header(line)
            if header is None:
                return None
            st.header_file, st.has_ab_prefix = header
            st.in_file = True
            continue

        if line.startswith("\\") and (in_hunk or st.saw_hunk_for_file):
            continue

        if not in_hunk and not st.saw_hunk_for_file:
            if line.startswith("--- "):
                st.saw_minus_header = True
                target = line[4:].strip()
                st.old_file = (
                    None
                    if target == "/dev/null"
                    else _clean_diff_path(target, is_new=False, strip_prefix=st.has_ab_prefix)
                )
                continue
            if line.startswith("+++ "):
                st.saw_plus_header = True
                target = line[4:].strip()
                if target == "/dev/null":
                    st.current_file = st.old_file
                    st.is_deleted = True
                else:
                    st.current_file = _clean_diff_path(
                        target, is_new=True, strip_prefix=st.has_ab_prefix
                    )
                    st.is_deleted = False
                continue
            if line.startswith("old mode "):
                st.saw_old_mode = True
                continue
            if line.startswith("new mode "):
                st.current_file = st.current_file or st.header_file
                st.saw_new_mode = True
                continue
            if line.startswith("new file mode "):
                st.current_file = st.current_file or st.header_file
                st.saw_new_file = True
                continue
            if line.startswith("deleted file mode "):
                st.current_file = st.current_file or st.header_file
                st.saw_deleted_file = True
                st.is_deleted = True
                continue
            if line.startswith("similarity index "):
                m_sim = _SIMILARITY_RE.match(line)
                if not m_sim:
                    return None
                st.similarity_index = int(m_sim.group(1))
                continue
            if line.startswith(("rename from ", "copy from ")):
                is_rename = line.startswith("rename from ")
                prefix = "rename from " if is_rename else "copy from "
                st.old_file = _unquote_diff_path(line[len(prefix) :])
                if is_rename:
                    st.rename_old = st.old_file
                    st.saw_rename_from = True
                else:
                    st.saw_copy_from = True
                continue
            if line.startswith(("rename to ", "copy to ")):
                is_rename = line.startswith("rename to ")
                prefix = "rename to " if is_rename else "copy to "
                st.current_file = _unquote_diff_path(line[len(prefix) :])
                if is_rename:
                    st.saw_rename_to = True
                else:
                    st.saw_copy_to = True
                continue
            if line.startswith("index "):
                m_idx = _INDEX_RE.match(line)
                if not m_idx:
                    return None
                st.index_shas = (m_idx.group(1), m_idx.group(2))
                continue
            if line.startswith("Binary files ") and line.endswith(" differ"):
                st.current_file = st.current_file or st.header_file
                st.saw_binary = True
                if line.endswith(" and /dev/null differ"):
                    st.is_deleted = True
                continue

        m = _HUNK_HEADER_RE.match(line)
        if m:
            if st.current_file is None or in_hunk or not st.saw_plus_header:
                return None
            st.saw_hunk_for_file = True
            rem_old = int(m.group(2)) if m.group(2) is not None else 1
            rem_new = int(m.group(4)) if m.group(4) is not None else 1
            in_hunk = True
            if rem_old == 0 and rem_new == 0:
                in_hunk = False
            continue

        if not in_hunk:
            return None

        if line.startswith("+"):
            st.current_added.append(line[1:])
            rem_new -= 1
        elif line.startswith("-"):
            st.current_deleted.append(line[1:])
            rem_old -= 1
        elif line.startswith(" "):
            rem_old -= 1
            rem_new -= 1
        else:
            return None
        if rem_old < 0 or rem_new < 0:
            return None
        if rem_old == 0 and rem_new == 0:
            in_hunk = False

    if in_hunk or not _flush_current_file():
        return None

    return result


_UNVERIFIED_VIOLATIONS = frozenset(
    {
        "unreadable landed content",
        "unreadable merge commit content",
        "deleted file still present in overtaking commit",
        "landed content absent",
    }
)
_MERGE_COMMIT_VIOLATIONS = frozenset(
    {
        "unreadable merge commit content",
        "file missing in merge commit",
    }
)
_PR_PATH_VIOLATIONS = frozenset(
    {
        "unreadable landed content",
        "deleted file still present in overtaking commit",
        "landed content absent",
    }
)


def _unverifiable_reason(fd: FileDiff) -> str | None:
    """Return a fail-closed violation string if ``fd`` cannot be verified by line survival."""
    if fd.has_multiple_records:
        return "multiple diff records cannot be verified by line survival"
    if fd.has_mode_change:
        return "file mode change cannot be verified by line survival"
    if not fd.is_deleted:
        if fd.is_binary:
            return "binary diff cannot be verified by line survival"
        if not fd.has_hunks:
            return "hunkless diff cannot be verified by line survival"
    return None


def check_multiset_survival(
    added_lines: Sequence[str],
    deleted_lines: Sequence[str],
    landed_content: FileContent,
    merge_content: FileContent,
    *,
    is_deleted: bool = False,
) -> tuple[int, list[str]]:
    """Check line survival via multiset counting (#1511).

    Returns ``(lost_count, violations)``, where ``lost_count`` is the total number of
    lost or re-added line occurrences across all distinct violating lines.
    Ignore blank lines only.
    """
    if is_deleted:
        if landed_content is None:
            return 1, ["unreadable landed content"]
        if landed_content is not FILE_ABSENT:
            return 1, ["deleted file still present in overtaking commit"]
        if merge_content is FILE_ABSENT:
            return 0, []
        if merge_content is None:
            return 1, ["unreadable merge commit content"]
        return 1, ["deleted file reappeared in merge commit"]

    if merge_content is None:
        return 1, ["unreadable merge commit content"]
    if not isinstance(merge_content, str):
        return 1, ["file missing in merge commit"]
    if landed_content is None:
        return 1, ["unreadable landed content"]
    if not isinstance(landed_content, str):
        return 1, ["landed content absent"]

    landed_lines = [line for line in _split_lines(landed_content) if line.strip()]
    merge_lines = [line for line in _split_lines(merge_content) if line.strip()]

    landed_counts = Counter(landed_lines)
    merge_counts = Counter(merge_lines)

    violations: list[str] = []
    seen_violations: set[str] = set()
    lost_count = 0

    for line in added_lines:
        if not line.strip():
            continue
        if merge_counts[line] < landed_counts[line]:
            if line not in seen_violations:
                seen_violations.add(line)
                violations.append(line)
                lost_count += landed_counts[line] - merge_counts[line]

    for line in deleted_lines:
        if not line.strip():
            continue
        if merge_counts[line] > landed_counts[line]:
            if line not in seen_violations:
                seen_violations.add(line)
                violations.append(line)
                lost_count += merge_counts[line] - landed_counts[line]

    return lost_count, violations


def verify_merge(
    landed: Sequence[str] | None,
    overtaken: Mapping[str, int | Sequence[int]] | None = None,
    intended: Sequence[str] | None = None,
    *,
    diffs: Mapping[int, str | None] | None = None,
    file_contents: Mapping[str, FileContent] | None = None,
    pr_file_contents: Mapping[int, Mapping[str, FileContent]]
    | Mapping[tuple[int, str], FileContent]
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

    In the returned report, ``overtaken`` maps each drifted path to the list of
    overtaking PRs whose changes did not survive (or could not be verified), and is
    ``{}`` when ``status`` is ``clean``; ``shared_files`` always lists all paths shared
    between the merge commit and overtaking PRs in the window.

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
        saw_line_loss = False

        if diffs is None or file_contents is None:
            drifted = {p: list(prs) for p, prs in collisions.items()}
        else:
            parsed_diffs: dict[int, dict[str, FileDiff] | None] = {}
            prs_in_collisions = {pr for prs in collisions.values() for pr in prs}
            for pr in sorted(prs_in_collisions):
                pr_diff = diffs.get(pr)
                parsed = parse_diff_by_file(pr_diff) if pr_diff is not None else None
                parsed_diffs[pr] = parsed
                if parsed is not None:
                    for p in parsed:
                        if p in landed_set:
                            pr_list = collisions.setdefault(p, [])
                            if pr not in pr_list:
                                pr_list.append(pr)

            for path, prs in sorted(collisions.items()):
                merge_content = file_contents.get(path)
                for pr in sorted(prs):
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
                    unverifiable = _unverifiable_reason(fd)
                    if unverifiable is not None:
                        drifted.setdefault(path, []).append(pr)
                        drift_details.setdefault(path, []).append(
                            f"{unverifiable} for {path} (#{pr})"
                        )
                        continue

                    pr_landed_content = None
                    if pr_file_contents is not None:
                        if isinstance(pr_file_contents.get(pr), Mapping):
                            pr_landed_content = pr_file_contents[pr].get(path)
                        elif (pr, path) in pr_file_contents:
                            pr_landed_content = pr_file_contents.get((pr, path))

                    lost_count, violations = check_multiset_survival(
                        fd.added_lines,
                        fd.deleted_lines,
                        pr_landed_content,
                        merge_content,
                        is_deleted=fd.is_deleted,
                    )
                    if lost_count > 0:
                        drifted.setdefault(path, []).append(pr)
                        details = drift_details.setdefault(path, [])
                        for v in violations:
                            if v not in _UNVERIFIED_VIOLATIONS:
                                saw_line_loss = True
                            if v in _MERGE_COMMIT_VIOLATIONS:
                                detail = f"{v} for {path}"
                            elif v in _PR_PATH_VIOLATIONS:
                                detail = f"{v} for {path} (#{pr})"
                            else:
                                detail = f"{v} (#{pr})"
                            if detail not in details:
                                details.append(detail)

        if drifted:
            listed_parts = []
            for p, pr_list in sorted(drifted.items()):
                prs_str = ", ".join(f"#{pr}" for pr in sorted(pr_list))
                listed_parts.append(f"{p} ({prs_str})")
            listed = ", ".join(listed_parts)
            summary = (
                "the shape of a silent revert"
                if saw_line_loss
                else "could not verify line survival (fail-closed)"
            )
            return _report(
                "drift",
                f"the merge wrote to {len(drifted)} file(s) that another pull request "
                f"changed after this one branched — {summary}: {listed}",
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
