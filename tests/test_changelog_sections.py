"""A release section may appear at most once per version.

Written after `## [1.12.0]`, `## [1.10.0]` and `## [1.6.4]` were each found to
repeat a section — shipped that way across seven releases. Every entry was
correct and none was lost; they were simply inserted above the previous top
section each time, so those blocks grew alternating headings.

It matters at release time rather than while editing: `## [Unreleased]` becomes
`## [x.y.z]` verbatim, so a duplicated heading ships to PyPI and to the GitHub
Release notes, where a reader looking for "what changed" finds two lists and no
reason to think either is complete.

Cheap to detect, invisible to review — a reviewer sees the diff hunk, not the
shape of the whole section.
"""

from __future__ import annotations

import collections
import re
import unittest
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

#: Keep a Changelog's six, plus the four this project has actually used.
#:
#: Deliberately a fixed list rather than one derived from the file, which would
#: pass by construction. The point is to catch a *near-miss* — `### Fixes` is
#: silently a different bucket from `### Fixed`, and a reader scanning for one
#: heading will not find the other. Adding a genuinely new section is a
#: one-line edit here, and that edit is the review.
KNOWN = {
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
    "Performance",
    "Refactored",
    "Tests",
    "Companion",
}


#: Matches an unresolved merge-conflict marker line: seven `<`, `=`, or `>`
#: characters at the start of the line, followed by a space (the branch label
#: git appends, e.g. `HEAD` or a ref name) or end of line (the bare `=======`
#: separator carries no label). Anything shorter or longer than seven — a
#: quoted six-`<` example, say — is not a marker git itself would emit and is
#: left alone.
CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)")


def conflict_marker_lines(text: str) -> list[tuple[int, str]]:
    """1-based line numbers and content of unresolved merge-conflict markers.

    Written after #1010: a merge of `origin/main` into a PR branch was
    committed and pushed with the CHANGELOG conflict unresolved (commit
    eb7f2ec) — three marker lines bracketing both sides' entries. `versions()`
    above only recognises `## [` and `### ` lines, so the marker lines matched
    neither regex, created no duplicate heading, and the existing tests passed
    4/4 against that exact commit. Nothing else in CI was marker-aware for
    this file, so it would have shipped to `main` on a green squash-merge.
    """
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if CONFLICT_MARKER.match(line)
    ]


def versions() -> dict[str, list[str]]:
    """Every `## [version]` block mapped to the `### Section` names inside it."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        version = re.match(r"^## \[([^\]]+)\]", line)
        if version:
            current = version.group(1)
            blocks[current] = []
            continue
        section = re.match(r"^### (.+?)\s*$", line)
        if section and current is not None:
            blocks[current].append(section.group(1))
    return blocks


class EachSectionAppearsOnce(unittest.TestCase):
    def setUp(self):
        self.blocks = versions()

    def test_there_are_versions_to_check(self):
        """Vacuity: a parser that matched nothing would pass everything below."""
        self.assertGreater(len(self.blocks), 5, "the changelog was not parsed")
        self.assertIn("Unreleased", self.blocks)

    def test_no_version_repeats_a_section(self):
        offenders = {}
        for version, sections in self.blocks.items():
            repeated = [s for s, n in collections.Counter(sections).items() if n > 1]
            if repeated:
                offenders[version] = repeated
        self.assertEqual(
            offenders,
            {},
            "a release section appears more than once under one version, so the "
            f"released notes carry two lists of the same kind: {offenders}",
        )

    def test_every_section_name_is_a_known_one(self):
        """`### Fixes` is silently a different bucket from `### Fixed`."""
        unknown = {
            version: sorted(set(sections) - KNOWN)
            for version, sections in self.blocks.items()
            if set(sections) - KNOWN
        }
        self.assertEqual(unknown, {}, f"unrecognised changelog sections: {unknown}")

    def test_no_released_version_is_empty(self):
        """A published version with no sections is a release that says nothing.

        Deliberately *not* asserted on `Unreleased`. The first draft did, and it
        failed the moment a release was actually cut — `## [Unreleased]` is
        correctly empty from the cut until the next change lands, so the guard
        fired on the one behaviour it was meant to protect. The state worth
        refusing is a *released* block with nothing under it.
        """
        empty = sorted(
            v for v, sections in self.blocks.items() if v != "Unreleased" and not sections
        )
        self.assertEqual(
            empty,
            [],
            f"released versions carry no sections, so their notes are blank: {empty}",
        )


class NoConflictMarkers(unittest.TestCase):
    """See #1010: nothing else here parses far enough to notice one."""

    def test_changelog_has_no_conflict_markers(self):
        offenders = conflict_marker_lines(CHANGELOG.read_text(encoding="utf-8"))
        self.assertEqual(
            offenders,
            [],
            "CHANGELOG.md contains unresolved merge-conflict markers — a merge "
            f"landed without resolving a conflict in this file: {offenders}",
        )

    def test_detects_a_three_marker_body_like_eb7f2ec(self):
        """Reconstructs the shape of commit eb7f2ec: markers at lines 10, 12, 18.

        Built from parts (`"<" * 7`, not a literal run) so this fixture is not
        itself a conflicted file — it is a regular Python string describing one.
        """
        conflicted = "\n".join(
            [
                "## [Unreleased]",  # 1
                "",  # 2
                "### Added",  # 3
                "- existing entry one",  # 4
                "- existing entry two",  # 5
                "- existing entry three",  # 6
                "- existing entry four",  # 7
                "- existing entry five",  # 8
                "- existing entry six",  # 9
                "<" * 7 + " HEAD",  # 10
                "- entry from HEAD",  # 11
                "=" * 7,  # 12
                "- entry from origin/main line 1",  # 13
                "- entry from origin/main line 2",  # 14
                "- entry from origin/main line 3",  # 15
                "- entry from origin/main line 4",  # 16
                "- entry from origin/main line 5",  # 17
                ">" * 7 + " origin/main",  # 18
                "",  # 19
                "### Fixed",  # 20
            ]
        )
        offenders = conflict_marker_lines(conflicted)
        self.assertEqual(
            offenders,
            [
                (10, "<" * 7 + " HEAD"),
                (12, "=" * 7),
                (18, ">" * 7 + " origin/main"),
            ],
        )

    def test_a_clean_body_has_no_offenders(self):
        """No false positives on ordinary changelog prose."""
        self.assertEqual(conflict_marker_lines("## [Unreleased]\n\n### Added\n- a thing\n"), [])
