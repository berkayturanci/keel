#!/usr/bin/env python3
"""Per-PR changelog fragments, collected into CHANGELOG.md at release time (#1515).

Every pull request used to append to ``## [Unreleased]``, so parallel PRs all edited
the same few lines and went DIRTY on it (four conflicts in one day). A fragment is a
file of its own, so two PRs never touch the same line:

    changelog.d/<issue>.<type>.md

``<issue>`` is the GitHub issue number (digits) and ``<type>`` is one of
:data:`TYPES`, which map to the ``### `` headings ``CHANGELOG.md`` uses. The file
holds one or more markdown bullet lines, written as they should read in the release.

``make release-bump`` collects them into the new ``## [x.y.z] - <date>`` section, under
the right ``###`` heading, ordered by issue number, merged with whatever is still under
``## [Unreleased]`` (so the transition is backward compatible), and deletes the
collected files. ``make changelog-preview`` prints that section without writing.
``make release-check`` refuses a malformed fragment name and a release whose section
carries no entries.

Stdlib-only and offline, like its siblings: ``release_check.py`` runs it before
anything is installed. Planning (:func:`plan_collection`) never writes; only
:func:`apply_collection` does, so a refusal leaves the tree untouched.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import NamedTuple

#: Repo root = parent of this script's directory (scripts/..).
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

FRAGMENT_DIR = "changelog.d"

#: Fragment type -> the ``###`` heading it lands under, in the order the section
#: renders them (Keep a Changelog's order). Headings outside this table (``Tests``,
#: ``Companion``...) can only arrive through a hand-edited ``[Unreleased]``; they
#: render after these, in the order they were written.
TYPES = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "fixed": "Fixed",
    "security": "Security",
}

#: Files that may sit in ``changelog.d`` without being fragments.
NON_FRAGMENTS = frozenset({"README.md", ".gitkeep"})

FRAGMENT_NAME = re.compile(r"^(\d+)\.([a-z]+)\.md$")

_VERSION_HEADING = re.compile(r"^## \[([^\]]+)\]")
_SECTION_HEADING = re.compile(r"^### (.+?)\s*$")
_BULLET = re.compile(r"^- \S")

UNRELEASED = "Unreleased"


class Fragment(NamedTuple):
    issue: int
    type: str
    path: Path
    text: str


class Collection(NamedTuple):
    """What a release would do to the tree: the new CHANGELOG and the files it eats."""

    changelog: str
    fragments: list[Fragment]


def scan(root: Path) -> tuple[list[Fragment], list[str]]:
    """Every fragment under ``root``, sorted by issue then type, plus every problem.

    A problem is a name that is not ``<digits>.<type>.md`` with a known type, a
    fragment that is empty, or one whose first line is not a ``- `` bullet. All are
    reported together rather than the first, so one run shows the whole list.
    """
    directory = root / FRAGMENT_DIR
    if not directory.is_dir():
        return [], []
    fragments: list[Fragment] = []
    problems: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name in NON_FRAGMENTS:
            continue
        shown = f"{FRAGMENT_DIR}/{path.name}"
        match = FRAGMENT_NAME.match(path.name)
        if not match:
            problems.append(f"{shown} is not named <issue>.<type>.md (digits, then a type)")
            continue
        if match.group(2) not in TYPES:
            problems.append(
                f"{shown} has unknown type {match.group(2)!r}; use one of {', '.join(TYPES)}"
            )
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not _BULLET.match(text):
            problems.append(f"{shown} must open with a `- ` bullet line (it is empty or prose)")
            continue
        fragments.append(Fragment(int(match.group(1)), match.group(2), path, text))
    fragments.sort(key=lambda fragment: (fragment.issue, fragment.type))
    return fragments, problems


def _split_blocks(lines: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split a section body into its preamble and ``(heading, lines)`` blocks."""
    preamble: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        heading = _SECTION_HEADING.match(line)
        if heading:
            blocks.append((heading.group(1), []))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, blocks


def _trim(lines: list[str]) -> list[str]:
    """Drop leading and trailing blank lines."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def render_section(
    version: str, date: str, unreleased: list[str], fragments: list[Fragment]
) -> str:
    """The ``## [version] - date`` block: Unreleased's own lines, then the fragments.

    ``unreleased`` is the body of the current ``## [Unreleased]`` section. Its preamble
    (the highlight lines) stays on top; its bullets stay ahead of the fragments in
    their heading, because they are older.
    """
    preamble, blocks = _split_blocks(unreleased)
    merged: dict[str, list[str]] = {}
    for heading, lines in blocks:
        merged.setdefault(heading, []).extend(_trim(lines))
    for fragment in fragments:
        merged.setdefault(TYPES[fragment.type], []).extend(fragment.text.splitlines())
    known = [heading for heading in TYPES.values() if heading in merged]
    extra = [heading for heading in merged if heading not in TYPES.values()]
    out = [f"## [{version}] - {date}", ""]
    preamble = _trim(preamble)
    if preamble:
        out += preamble + [""]
    for heading in known + extra:
        out += [f"### {heading}", *merged[heading], ""]
    return "\n".join(out)


def _unreleased_span(lines: list[str]) -> tuple[int, int]:
    """``(heading_index, end_index)`` of the ``## [Unreleased]`` section."""
    for index, line in enumerate(lines):
        heading = _VERSION_HEADING.match(line)
        if heading and heading.group(1) == UNRELEASED:
            for end in range(index + 1, len(lines)):
                if _VERSION_HEADING.match(lines[end]):
                    return index, end
            return index, len(lines)
    raise ValueError("CHANGELOG.md has no `## [Unreleased]` heading to collect into")


def has_release(changelog: str, version: str) -> bool:
    """Whether ``## [version]`` already exists."""
    return any(
        (heading := _VERSION_HEADING.match(line)) and heading.group(1) == version
        for line in changelog.splitlines()
    )


def plan_collection(root: Path, version: str, date: str) -> Collection | None:
    """What cutting ``version`` would do, or ``None`` when there is nothing to do.

    Raises ``ValueError`` for malformed fragments or a CHANGELOG with no
    ``[Unreleased]`` heading. ``None`` for: no CHANGELOG under ``root`` (a fixture
    tree), a section for ``version`` that already exists (re-running the bump to
    repair drift must not collect twice), or no fragment and nothing under
    ``[Unreleased]``.
    """
    path = root / "CHANGELOG.md"
    if not path.exists():
        return None
    fragments, problems = scan(root)
    if problems:
        raise ValueError("; ".join(problems))
    text = path.read_text(encoding="utf-8")
    if has_release(text, version):
        return None
    lines = text.split("\n")
    start, end = _unreleased_span(lines)
    body = lines[start + 1 : end]
    if not fragments and not any(line.strip() for line in body):
        return None
    section = render_section(version, date, body, fragments)
    rebuilt = lines[: start + 1] + ["", section] + lines[end:]
    return Collection("\n".join(rebuilt), fragments)


def apply_collection(root: Path, collection: Collection) -> list[str]:
    """Write the new CHANGELOG, delete the collected fragments; return changed paths."""
    (root / "CHANGELOG.md").write_text(collection.changelog, encoding="utf-8")
    changed = ["CHANGELOG.md"]
    for fragment in collection.fragments:
        fragment.path.unlink()
        changed.append(f"{FRAGMENT_DIR}/{fragment.path.name}")
    return changed


def section_entries(changelog: str, version: str) -> int | None:
    """Bullet lines under ``###`` headings of ``## [version]``; ``None`` if no section."""
    lines = iter(changelog.splitlines())
    for line in lines:
        heading = _VERSION_HEADING.match(line)
        if heading and heading.group(1) == version:
            break
    else:
        return None
    count = 0
    in_block = False
    for line in lines:
        if _VERSION_HEADING.match(line):
            break
        if _SECTION_HEADING.match(line):
            in_block = True
        elif in_block and _BULLET.match(line):
            count += 1
    return count


def preview(root: Path, version: str, date: str) -> str:
    """The section ``release-bump`` would write, as text."""
    collection = plan_collection(root, version, date)
    if collection is None:
        return "changelog-preview: nothing to release (no fragments, empty [Unreleased])\n"
    text = collection.changelog
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"## [{version}]"))
    end = next(
        (i for i in range(start + 1, len(lines)) if _VERSION_HEADING.match(lines[i])), len(lines)
    )
    return "\n".join(lines[start:end]).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview the next CHANGELOG section.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root")
    parser.add_argument("--version", default="next", help="heading to print (default: next)")
    parser.add_argument("--date", default=None, help="date to print (default: today)")
    args = parser.parse_args(argv)
    date = args.date or datetime.date.today().isoformat()
    try:
        sys.stdout.write(preview(Path(args.root), args.version, date))
    except (ValueError, OSError) as exc:
        print(f"changelog-preview failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
