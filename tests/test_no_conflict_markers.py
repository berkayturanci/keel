"""No tracked text file may carry an unresolved merge-conflict marker.

Companion to `tests/test_changelog_sections.py`'s narrower guard: that file
only reads `CHANGELOG.md`, the file most likely to conflict on nearly every
PR (#1010). This one is the tree-wide backstop — any tracked source, doc, or
config file, not just the changelog, that gets committed mid-conflict with
`<<<<<<< HEAD` / `=======` / `>>>>>>> <ref>` left in place.

`git diff --check` catches this at merge time, but nothing in `make test` or
CI ran it, so a squash-merge with markers still inside would have gone green
(see #1010: commit eb7f2ec shipped exactly this in `CHANGELOG.md` before a
human caught it by eye). This test makes that failure visible in the same
offline suite every PR already runs.

Nothing is skipped. `tests/test_swarm_landing.py` used to embed marker lines as a
fixture for the swarm conflict healer; the healer went with the local landing it served
(#1287), and so did the skip list.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Same shape as `tests/test_changelog_sections.py`'s CONFLICT_MARKER: seven
#: `<`, `=`, or `>` characters at the start of a line, followed by a space
#: (git's branch-label suffix) or end of line (the bare `=======` separator).
CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)")

#: Files that deliberately contain marker-shaped lines as test data, not as an
#: unresolved conflict. Empty; an entry needs a comment naming the fixture, as a
#: statement that a human looked at the lines and confirmed they are not a real merge.
SKIP: set[str] = set()


def _is_binary(path: Path) -> bool:
    """Null-byte sniff on the first chunk — the same heuristic git itself uses."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
    except OSError:  # pragma: no cover - race with a file removed mid-scan
        return True
    return b"\0" in chunk


def _tracked_files() -> list[str]:
    """Every path `git ls-files` reports, relative to the repo root."""
    git = shutil.which("git")
    if git is None:  # pragma: no cover - env guard
        return []
    proc = subprocess.run(
        [git, "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:  # pragma: no cover - env guard
        return []
    return [rel for rel in proc.stdout.split("\0") if rel]


def conflict_markers_in_tree() -> list[tuple[str, int, str]]:
    """(relative path, 1-based line number, line content) for every marker found."""
    offenders: list[tuple[str, int, str]] = []
    for rel in _tracked_files():
        if rel in SKIP:
            continue
        path = REPO_ROOT / rel
        if not path.is_file() or _is_binary(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if CONFLICT_MARKER.match(line):
                offenders.append((rel, number, line))
    return offenders


class NoConflictMarkersInTree(unittest.TestCase):
    def _require_git(self) -> list[str]:
        files = _tracked_files()
        if not files:  # pragma: no cover - env guard
            self.skipTest("git is unavailable or this is not a checkout")
        return files

    def test_there_are_tracked_files_to_check(self):
        """Vacuity: an empty listing would pass the guard below by construction."""
        self.assertGreater(len(self._require_git()), 100, "the tree was not scanned")

    def test_no_tracked_file_has_a_conflict_marker(self):
        offenders = conflict_markers_in_tree()
        self.assertEqual(
            offenders,
            [],
            f"unresolved merge-conflict markers in the tree (path, line, content): {offenders}",
        )
