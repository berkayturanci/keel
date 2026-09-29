#!/usr/bin/env python3
"""Refuse a release that does not agree with itself — before anything is uploaded.

Every check here already existed as a sentence in ``docs/keel/release.md`` or as a
unit test that runs on pull requests. Neither ran in the one phase where the
mistake becomes permanent: PyPI files are immutable, so a tag that carries a
CHANGELOG still headed ``## [Unreleased]``, a plugin manifest naming the previous
version, or a ``keel-visual`` marker two releases behind (#796) cannot be fixed —
only yanked and re-released.

So the same assertions run again in ``publish.yml``'s build job, before the build
step, and locally as ``make release-check``. Five guards:

``declared version``
    ``pyproject.toml`` and ``src/keel/__init__.py`` agree.

``changelog lockstep``
    The top *released* ``## [x.y.z]`` section names the declared version. This is
    the one that catches a tag whose CHANGELOG was never renamed from
    ``## [Unreleased]``: the top released section is then still the *previous*
    release, which no longer equals what the tree declares.

``release highlights``
    The declared version's CHANGELOG section opens with one to three
    plain-language ``- `` highlight lines, which ``publish.yml`` puts at the top
    of the GitHub Release (#1342). Required of every version after
    ``release_notes.LAST_WITHOUT_HIGHLIGHTS``; past releases are not rewritten.
    Parsed by ``release_notes.py``, the script that renders them, so the check
    and the release cannot disagree about what counts.

``release surfaces``
    Every surface in :data:`release_surfaces.RELEASE_SURFACES` — the plugin
    manifests, the pinned-install references, the Homebrew formula url, the four
    site fallbacks — declares the declared version. Read from the same table
    ``release_bump.py`` writes through, so the bump cannot reach a surface this
    check does not, or the reverse.

``keel-visual markers``
    ``keel-visual``'s two version markers agree with each other. It is a second
    distribution on its own version line, so it is *not* compared to core's
    version; what drifted, twice, was the pair (#796). The rule is read from
    ``release_bump.VISUAL_EDITS`` — the list the bumper rewrites — rather than
    restated here.

With ``--tag v<x.y.z>`` a sixth guard runs: the tag names the declared version.
The publish workflow passes it on a tag push, so a tag pushed at the wrong commit
fails before the build.

``--package keel-visual`` checks the other distribution instead: its two version
markers agree, and with ``--tag keel-visual-v<x.y.z>`` the tag names the version
``keel-visual/pyproject.toml`` declares. ``publish-visual.yml`` runs it before its
build, because with ``skip-existing: true`` a tag that disagrees with the tree
would otherwise publish, or silently skip, the wrong version (#1371). Core's
guards are not run for it: core is on its own version line and its own tags.

Deliberately stdlib-only and offline. It runs before the build job installs
anything, and a guard that needs its own dependencies is a guard that can fail to
run for reasons unrelated to the release.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# `scripts/` is a directory of maintenance tools, not an installed package, so a
# sibling import needs the directory on the path. Already true when this file is
# run directly (`python scripts/release_check.py`); stated here so importing it
# from a test works the same way.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from release_bump import VISUAL_EDITS, current_version, visual_version  # noqa: E402
from release_notes import parse_highlights, requires_highlights  # noqa: E402
from release_surfaces import RELEASE_SURFACES, versions_in  # noqa: E402

#: Repo root = parent of this script's directory (scripts/..).
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

#: `## [1.19.3] - 2026-09-02` and `## [Unreleased]` alike.
CHANGELOG_HEADING = re.compile(r"(?m)^## \[([^\]]+)\]")

#: The heading a release renames. Every heading that is not this one names a
#: released version.
UNRELEASED = "Unreleased"

PACKAGE_VERSION = re.compile(r'(?m)^__version__ = "([^"]+)"')

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")

#: keel-visual's tag namespace, which ``publish-visual.yml`` triggers on.
VISUAL_TAG_RE = re.compile(r"^keel-visual-v(\d+\.\d+\.\d+)$")

#: The distributions this script can check, as ``release_bump.py --package`` names them.
PACKAGES = ("core", "keel-visual")


class Check(NamedTuple):
    """One guard's verdict. ``problems`` empty means it passed."""

    name: str
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def package_version(root: Path) -> str:
    """Read ``__version__`` from ``src/keel/__init__.py``.

    Read off disk rather than imported: this script runs in the publish workflow
    before anything is installed, and importing the package under test to check
    the package under test is how a check ends up asserting on the wrong tree.
    """
    text = (root / "src" / "keel" / "__init__.py").read_text(encoding="utf-8")
    match = PACKAGE_VERSION.search(text)
    if not match:
        raise ValueError('could not find `__version__ = "..."` in src/keel/__init__.py')
    return match.group(1)


def changelog_versions(root: Path) -> list[str]:
    """Every ``## [...]`` heading in ``CHANGELOG.md``, in file order."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return CHANGELOG_HEADING.findall(text)


def check_declared_version(root: Path) -> Check:
    """``pyproject.toml`` and ``src/keel/__init__.py`` must name one version."""
    declared = current_version(root)
    package = package_version(root)
    problems = []
    if declared != package:
        problems.append(
            f"pyproject.toml declares {declared} but src/keel/__init__.py declares {package}; "
            f"`make release-bump VERSION={declared}` re-syncs them"
        )
    return Check("declared version", problems)


def check_changelog(root: Path) -> Check:
    """The top released section must name the version being released."""
    declared = current_version(root)
    headings = changelog_versions(root)
    problems = []
    if not headings:
        return Check("changelog lockstep", ["CHANGELOG.md has no `## [...]` section headings"])
    released = [heading for heading in headings if heading != UNRELEASED]
    if not released:
        problems.append(
            "CHANGELOG.md has no released section; rename "
            f"`## [{UNRELEASED}]` to `## [{declared}]` before tagging"
        )
    elif released[0] != declared:
        problems.append(
            f"CHANGELOG.md's top released section is `## [{released[0]}]` but the tree "
            f"declares {declared}; rename `## [{UNRELEASED}]` to `## [{declared}]` "
            "before tagging"
        )
    return Check("changelog lockstep", problems)


def check_highlights(root: Path) -> Check:
    """The declared version's section must open with highlights the release can carry.

    Checked against the *declared* version rather than the top released section:
    when the two differ, ``changelog lockstep`` already refuses, and this guard
    then says the one further thing it knows — that the section it would publish
    does not exist.
    """
    declared = current_version(root)
    if not requires_highlights(declared):
        return Check("release highlights", [])
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    _highlights, problems = parse_highlights(changelog, declared)
    return Check("release highlights", problems)


def check_surfaces(root: Path) -> Check:
    """Every version-bearing surface must already name the declared version."""
    declared = current_version(root)
    problems = []
    for surface in RELEASE_SURFACES:
        path = root / surface.path
        if not path.exists():
            problems.append(f"{surface.path} is a release surface but does not exist")
            continue
        found = versions_in(surface, path.read_text(encoding="utf-8"))
        expected = surface.token.format(version=declared)
        if not found:
            # A surface whose token has been renamed or deleted is a silent hole:
            # the bump rewrites nothing and every version check passes vacuously.
            problems.append(
                f"{surface.path} carries no `{expected}`; the surface moved or was removed"
            )
            continue
        stale = sorted({version for version in found if version != declared})
        if stale:
            problems.append(
                f"{surface.path} declares {', '.join(stale)} where the tree declares "
                f"{declared} (expected `{expected}`)"
            )
    return Check("release surfaces", problems)


#: ``release_bump.VISUAL_EDITS`` writes its replacements as ``\g<1>{new}\g<3>``,
#: so every pattern in it is a three-group sandwich — prefix, version, suffix —
#: and the version is group 2. That convention is what lets this module read the
#: same list the bumper writes through instead of restating it, and it is checked
#: rather than assumed: a two-group pattern added there would otherwise make this
#: guard compare the wrong substring, or raise inside a release.
VISUAL_VERSION_GROUP = 2
VISUAL_PATTERN_GROUPS = 3


def check_visual_markers(root: Path) -> Check:
    """``keel-visual``'s two version markers must agree with each other.

    keel-visual is a second distribution on its own version line and its own tag
    namespace, so it is deliberately *not* compared to core's version. What
    shipped wrong was the pair: ``__version__`` stayed at 0.6.0 through the 0.7.0
    and 0.8.0 releases (#796), on a value that is public API — it is what a bug
    report quotes.

    The files and the patterns come from ``release_bump.VISUAL_EDITS``, the list
    the bumper rewrites, so a marker added there is guarded here without a second
    edit. See :data:`VISUAL_PATTERN_GROUPS` for the shape that list must keep.
    """
    # Keyed by (path, pattern): two markers in one file are two markers. Keying by
    # path alone silently collapsed them, so a file carrying both a correct and a
    # stale version would have reported whichever came last and agreed with itself.
    seen: dict[tuple[str, str], str] = {}
    problems = []
    for relative, pattern in VISUAL_EDITS:
        if pattern.groups != VISUAL_PATTERN_GROUPS:
            problems.append(
                f"{relative}'s pattern has {pattern.groups} groups, not "
                f"{VISUAL_PATTERN_GROUPS}; release_bump.VISUAL_EDITS must keep the "
                "(prefix)(version)(suffix) shape its own substitution relies on"
            )
            continue
        path = root / relative
        if not path.exists():
            problems.append(f"{relative} is a keel-visual version marker but does not exist")
            continue
        match = pattern.search(path.read_text(encoding="utf-8"))
        if not match:
            problems.append(f"{relative} carries no version marker")
            continue
        seen[(relative, pattern.pattern)] = match.group(VISUAL_VERSION_GROUP)
    if len(set(seen.values())) > 1:
        detail = ", ".join(
            f"{relative}={version}" for (relative, _pattern), version in sorted(seen.items())
        )
        problems.append(
            f"keel-visual's version markers disagree ({detail}); "
            "`python scripts/release_bump.py <version> --package keel-visual` repairs it"
        )
    return Check("keel-visual markers", problems)


def check_tag(root: Path, tag: str) -> Check:
    """The tag being published must name the version the tree declares."""
    declared = current_version(root)
    match = TAG_RE.match(tag)
    if not match:
        return Check("tag", [f"tag {tag!r} is not of the form vX.Y.Z"])
    problems = []
    if match.group(1) != declared:
        problems.append(
            f"tag {tag} does not name the declared version {declared}; "
            "the tag was cut at the wrong commit, or the bump never landed"
        )
    return Check("tag", problems)


def check_visual_tag(root: Path, tag: str) -> Check:
    """A ``keel-visual-v*`` tag must name the version keel-visual declares (#1371)."""
    declared = visual_version(root)
    match = VISUAL_TAG_RE.match(tag)
    if not match:
        return Check("tag", [f"tag {tag!r} is not of the form keel-visual-vX.Y.Z"])
    problems = []
    if match.group(1) != declared:
        problems.append(
            f"tag {tag} does not name keel-visual's declared version {declared}; "
            "the tag was cut at the wrong commit, or the bump never landed"
        )
    return Check("tag", problems)


def run_checks(root: Path, tag: str | None = None, package: str = "core") -> list[Check]:
    """Every guard, in a fixed order. Deterministic — no clock, no network."""
    if package == "keel-visual":
        visual = [check_visual_markers(root)]
        if tag is not None:
            visual.append(check_visual_tag(root, tag))
        return visual
    checks = [
        check_declared_version(root),
        check_changelog(root),
        check_highlights(root),
        check_surfaces(root),
        check_visual_markers(root),
    ]
    if tag is not None:
        checks.append(check_tag(root, tag))
    return checks


def report(checks: list[Check], declared: str, out) -> None:
    """Print one line per guard, then every problem under the guard that found it."""
    print(f"release-check: the tree declares {declared}", file=out)
    for check in checks:
        print(f"  {'PASS' if check.ok else 'FAIL'}  {check.name}", file=out)
        for problem in check.problems:
            print(f"          {problem}", file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check keel's release surfaces agree.")
    parser.add_argument(
        "--root", default=str(DEFAULT_ROOT), help="repo root (defaults to the keel checkout)"
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="also require this tag (vX.Y.Z, or keel-visual-vX.Y.Z) to name the declared version",
    )
    parser.add_argument(
        "--package",
        choices=PACKAGES,
        default="core",
        help="which distribution to check; keel-visual is on its own version line",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        if args.package == "keel-visual":
            declared = f"keel-visual {visual_version(root)}"
        else:
            declared = current_version(root)
        checks = run_checks(root, args.tag, args.package)
    except (ValueError, OSError) as exc:
        print(f"release-check failed: {exc}", file=sys.stderr)
        return 1

    report(checks, declared, sys.stdout)
    failed = [check.name for check in checks if not check.ok]
    if failed:
        # Flush first: the report is the evidence for the refusal, and interleaved
        # streams in a CI log put the verdict above the reason that produced it.
        sys.stdout.flush()
        print(
            "release-check: refusing the release — " + ", ".join(failed),
            file=sys.stderr,
        )
        return 1
    print("release-check: every release surface agrees")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
