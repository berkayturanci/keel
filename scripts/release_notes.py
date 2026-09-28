#!/usr/bin/env python3
"""Draft the top of a GitHub Release body from the CHANGELOG's highlights (#1342).

Until 1.24.x a release body was only GitHub's generated "What's Changed" list:
pull-request titles, which tell a reader arriving from a link which branches
merged, not what changed for them. So every released CHANGELOG section now opens
with a few plain-language highlight lines, and the publish job puts them at the
top of the release.

The convention, in ``CHANGELOG.md``::

    ## [1.25.0] - 2026-10-01

    - One line a user can act on: what they can now do, or what stopped breaking.
    - A second one.

    ### Fixed
    - ...

The highlights are the ``- `` bullets between the version heading and its first
``### `` section, :data:`MIN_HIGHLIGHTS` to :data:`MAX_HIGHLIGHTS` of them. A
bullet may wrap onto indented continuation lines. Anything else in that space is
refused rather than dropped: a paragraph written there would otherwise vanish
from the release without a word.

Two consumers read one parser, so they cannot disagree about what counts:

- ``release_check.py`` refuses a release whose section carries no valid
  highlights, locally (``make release-check``) and in ``publish.yml`` before
  anything is built or uploaded;
- this script's command line renders the body file ``publish.yml`` hands to the
  release action, which puts GitHub's generated notes underneath it.

Stdlib-only and offline, for the same reason as ``release_check.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: Repo root = parent of this script's directory (scripts/..).
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

#: Fewer than one is a release that says nothing; the floor is one rather than
#: two so a single-fix patch release is not pushed into writing filler.
MIN_HIGHLIGHTS = 1

#: More than three stops being a highlight and becomes a second changelog.
MAX_HIGHLIGHTS = 3

#: The last release cut before this convention. Past releases are not rewritten,
#: so only a version after this one is required to carry highlights — which also
#: keeps the tree between releases passing its own ``make release-check``.
LAST_WITHOUT_HIGHLIGHTS = (1, 24, 2)

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")

_VERSION_HEADING = re.compile(r"^## \[([^\]]+)\]")
_SECTION_HEADING = re.compile(r"^### ")
_BULLET = re.compile(r"^- (\S.*)$")
_CONTINUATION = re.compile(r"^\s+(\S.*)$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def requires_highlights(version: str) -> bool:
    """Whether ``version`` was released under the convention.

    Only an ``X.Y.Z`` after :data:`LAST_WITHOUT_HIGHLIGHTS` is; ``Unreleased``
    and anything unparsable are not a release.
    """
    match = _SEMVER.match(version)
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) > LAST_WITHOUT_HIGHLIGHTS


def preamble(changelog: str, version: str) -> list[str] | None:
    """The lines between ``## [version]`` and its first ``###``, or ``None``.

    ``None`` means the version has no section at all, which is a different
    failure from a section that has no highlights.
    """
    lines = iter(changelog.splitlines())
    for line in lines:
        heading = _VERSION_HEADING.match(line)
        if heading and heading.group(1) == version:
            break
    else:
        return None
    body = []
    for line in lines:
        if _VERSION_HEADING.match(line) or _SECTION_HEADING.match(line):
            break
        body.append(line)
    return body


def parse_highlights(changelog: str, version: str) -> tuple[list[str], list[str]]:
    """The version's highlights, and every problem that keeps them from shipping.

    Returns ``(highlights, problems)``; the highlights are only safe to publish
    when ``problems`` is empty.
    """
    lines = preamble(changelog, version)
    if lines is None:
        return [], [f"CHANGELOG.md has no `## [{version}]` section"]
    highlights: list[str] = []
    problems: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        bullet = _BULLET.match(line)
        continuation = _CONTINUATION.match(line)
        if bullet:
            highlights.append(bullet.group(1).strip())
        elif continuation and highlights:
            highlights[-1] = f"{highlights[-1]} {continuation.group(1).strip()}"
        else:
            problems.append(
                f"`## [{version}]` carries {line.strip()!r} above its first `###` section; "
                "only `- ` highlight bullets belong there, or the line never reaches "
                "the release"
            )
    if not MIN_HIGHLIGHTS <= len(highlights) <= MAX_HIGHLIGHTS:
        problems.append(
            f"`## [{version}]` opens with {len(highlights)} highlight line(s); it needs "
            f"{MIN_HIGHLIGHTS} to {MAX_HIGHLIGHTS} plain-language `- ` bullets between "
            "the heading and its first `###` section (docs/keel/release.md)"
        )
    return highlights, problems


def render(highlights: list[str], repo: str, tag: str) -> str:
    """The Markdown the release body opens with; GitHub's list follows it."""
    bullets = "\n".join(f"- {line}" for line in highlights)
    return (
        f"## Highlights\n\n{bullets}\n\n"
        f"Full notes: [CHANGELOG.md](https://github.com/{repo}/blob/{tag}/CHANGELOG.md)\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the highlights that open a GitHub Release body."
    )
    parser.add_argument("--tag", required=True, help="the tag being released (vX.Y.Z)")
    parser.add_argument("--repo", required=True, help="owner/name, for the changelog link")
    parser.add_argument("--out", required=True, help="where to write the Markdown body")
    parser.add_argument(
        "--root", default=str(DEFAULT_ROOT), help="repo root (defaults to the keel checkout)"
    )
    args = parser.parse_args(argv)

    match = TAG_RE.match(args.tag)
    if not match:
        print(f"release-notes: tag {args.tag!r} is not of the form vX.Y.Z", file=sys.stderr)
        return 1
    try:
        changelog = (Path(args.root) / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError as exc:
        print(f"release-notes: {exc}", file=sys.stderr)
        return 1
    highlights, problems = parse_highlights(changelog, match.group(1))
    if problems:
        for problem in problems:
            print(f"release-notes: {problem}", file=sys.stderr)
        return 1
    Path(args.out).write_text(render(highlights, args.repo, args.tag), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
