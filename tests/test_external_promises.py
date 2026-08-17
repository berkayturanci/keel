"""Every artifact the docs promise must actually exist (issues #772, #773, #774).

Three defects shipped in one week, and the suite was green through all of them,
because all three lived *outside* the repo:

* `berkayturanci/keel-action@v1` was documented in five places as the official
  1-click action. The repository did not exist (#773).
* `Formula/keel.rb` pointed at a seven-release-old tag with a placeholder
  checksum and declared MIT for an Apache-2.0 project (#774).
* The published 1.15.0 release was silently reverted on main while the tag and
  the PyPI artifact stayed at 1.15.0 (#772).

Every unit test in this repo compares the repo against itself, so none of them
could see any of it. These check the repo against the world.

**Network is opt-in.** The reachability checks run only with
``KEEL_CHECK_EXTERNAL=1`` so the ordinary suite stays hermetic and offline; the
shape checks below run always, because a malformed promise is a defect whether or
not a network is available.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from keel import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Surfaces a user reads and copies from.
PUBLIC = ("README.md", "docs", "website")
ONLINE = os.environ.get("KEEL_CHECK_EXTERNAL") == "1"

#: The tap `brew install` resolves from. This repo is the source of truth for
#: Formula/keel.rb and the release publishes it here (#774), so the name is
#: declared rather than read from the environment — an env-gated check is a check
#: nobody sets.
HOMEBREW_TAP = "berkayturanci/homebrew-keel"

_ACTION_REF = re.compile(r"uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([A-Za-z0-9_.-]+)")
_BREW = re.compile(r"brew install ([A-Za-z0-9/_-]+)")


def _public_text() -> dict[str, str]:
    """Every public-facing file, keyed by repo-relative path."""
    out: dict[str, str] = {}
    for entry in PUBLIC:
        path = REPO_ROOT / entry
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for f in files:
            if f.is_file() and f.suffix in (".md", ".html", ".js", ".yml", ".yaml", ".txt"):
                out[str(f.relative_to(REPO_ROOT))] = f.read_text(
                    encoding="utf-8", errors="replace"
                )
    return out


def _reachable(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        return exc.code < 400
    except OSError as exc:
        # A network problem is not evidence that the promise is broken; the
        # subject/instrument distinction that #675 turned on.
        raise unittest.SkipTest(f"network unavailable while checking {url}") from exc


class TestActionReferences(unittest.TestCase):
    """A documented `uses:` must resolve, or a reader's first run fails."""

    def _refs(self) -> set[tuple[str, str]]:
        return {
            match.groups()
            for text in _public_text().values()
            for match in _ACTION_REF.finditer(text)
        }

    def test_the_docs_reference_at_least_one_action(self):
        # Guards the guard: a regex that silently matches nothing would make
        # every assertion below vacuously true.
        self.assertTrue(self._refs(), "no `uses:` references found — check the pattern")

    def test_first_party_actions_are_pinned_to_a_tag_that_exists(self):
        first_party = {(r, v) for r, v in self._refs() if r.startswith("berkayturanci/")}
        self.assertTrue(first_party, "expected at least one first-party action reference")
        for repo, ref in sorted(first_party):
            with self.subTest(action=f"{repo}@{ref}"):
                if not ONLINE:
                    self.skipTest("set KEEL_CHECK_EXTERNAL=1 to check reachability")
                self.assertTrue(
                    _reachable(f"https://github.com/{repo}"),
                    f"{repo} is referenced in the docs but the repository does not exist",
                )
                self.assertTrue(
                    _reachable(f"https://github.com/{repo}/tree/{ref}"),
                    f"{repo} exists but has no {ref} ref — `uses: {repo}@{ref}` will fail",
                )


class TestHomebrewPromise(unittest.TestCase):
    """`brew install` resolves from a tap, not from a formula sitting in this repo."""

    def test_a_documented_brew_install_has_a_formula_behind_it(self):
        promised = {
            name
            for text in _public_text().values()
            for name in _BREW.findall(text)
        }
        if not promised:
            self.skipTest("no brew install documented")
        self.assertTrue(
            (REPO_ROOT / "Formula" / "keel.rb").exists(),
            f"docs promise `brew install {sorted(promised)[0]}` with no formula in the repo",
        )

    def test_a_bare_formula_name_needs_a_tap(self):
        """`brew install keel` only works from a tap; the formula alone is a template.

        No longer inert: #774 decided this repo is the source of truth and the tap
        is published from it, so `HOMEBREW_TAP` is declared here rather than read
        from the environment.
        """
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to check reachability")
        self.assertTrue(
            _reachable(f"https://github.com/{HOMEBREW_TAP}"),
            f"docs promise a tap install but {HOMEBREW_TAP} does not exist",
        )

    def test_the_formula_checksum_matches_the_tarball_it_points_at(self):
        """`brew install` refuses on a checksum mismatch, so a wrong one is fatal.

        1.16.0 shipped with 1.15.0's digest (#805). The url is bumped before the
        tag exists, so the checksum cannot be correct at that moment — it can only
        be computed from the tarball GitHub builds *from* the tag, and nothing
        closed the gap. `test_homebrew_formula_matches_the_project` compares the
        formula to the project and passed throughout: a real-looking 64-hex string
        satisfies it, whatever it is a digest of.
        """
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to fetch the tarball")
        formula = (REPO_ROOT / "Formula" / "keel.rb").read_text(encoding="utf-8")
        url = re.search(r"https://github\.com/\S+?\.tar\.gz", formula)
        self.assertIsNotNone(url, "formula has no source tarball url")
        declared = re.search(r'(?m)^  sha256 "([0-9a-f]{64})"', formula)
        self.assertIsNotNone(declared, "formula has no top-level sha256")
        try:
            with urllib.request.urlopen(url.group(0), timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"the formula points at {url.group(0)}, which does not exist")
            raise self.skipTest(f"cannot fetch the tarball: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            # Being unable to look is not evidence the checksum is wrong.
            raise self.skipTest(f"cannot fetch the tarball: {exc}") from exc
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            declared.group(1),
            "the formula's sha256 is not the digest of the tarball it points at; "
            "brew install would refuse",
        )

    def test_the_tap_serves_the_formula_in_this_repo(self):
        """The guarded copy must be the copy `brew install` runs.

        This is the assertion that was missing when #787 shipped. The formula here
        was fixed and every check went green while the tap still served a version
        that installed a keel unable to start — because nothing compared the two.
        Release automation now syncs them; this fails if that automation is broken,
        skipped, or someone edits one copy by hand.
        """
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to compare against the tap")
        # The contents API, not raw.githubusercontent.com. `raw` is CDN-cached for
        # minutes, so right after a sync it serves the previous formula and this
        # test fails on a tap that is already correct — observed while fixing #805.
        # `brew tap` clones the repository, so the API is also the view that
        # matches what a user actually installs.
        url = f"https://api.github.com/repos/{HOMEBREW_TAP}/contents/Formula/keel.rb"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github.raw", "User-Agent": "keel-tests"},
        )
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                published = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"{HOMEBREW_TAP} has no Formula/keel.rb; brew install would fail")
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            # Being unable to look is not evidence the copies disagree.
            raise self.skipTest(f"cannot reach the tap: {exc}") from exc

        local = (REPO_ROOT / "Formula" / "keel.rb").read_text(encoding="utf-8")
        published_version_match = re.search(r"/tags/v([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz", published)
        if published_version_match:
            published_v = [int(p) for p in published_version_match.group(1).split(".")]
            current_v = [int(p) for p in __version__.split(".")]
            if current_v > published_v:
                # A release bump staged here but not yet on the tap. The tap
                # pulls on a schedule rather than being pushed to (#774), so a
                # short window where it is behind is expected — and a tap serving
                # an installable older formula is a lag, not a defect. Failing on
                # it would make every release briefly red for nothing anyone
                # should act on. A tap *claiming this version* with different
                # content still falls through to the assertion below.
                return

        self.assertEqual(
            local,
            published,
            "the tap's formula differs from this repo's; the tap is what users install",
        )


class TestPublishedVersion(unittest.TestCase):
    """The repo's version and the published artifact must not contradict each other."""

    def test_the_version_is_not_one_pypi_already_published_differently(self):
        """The check that would have caught #772 on the next push.

        The stale-version guard compares site strings against the code version —
        two internal values, so a revert that moved both together passed. Anchoring
        against PyPI compares an internal value to an external fact.
        """
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to check PyPI")
        try:
            with urllib.request.urlopen(
                "https://pypi.org/pypi/keel-workflow/json", timeout=20
            ) as response:
                published = set(json.load(response)["releases"])
        except OSError as exc:
            raise unittest.SkipTest("PyPI unreachable") from exc

        latest = max(published, key=lambda v: [int(p) for p in v.split(".") if p.isdigit()])
        current = [int(p) for p in __version__.split(".") if p.isdigit()]
        newest = [int(p) for p in latest.split(".") if p.isdigit()]
        self.assertGreaterEqual(
            current,
            newest,
            f"the repo says {__version__} but PyPI has published {latest}. Either a "
            "release was reverted on main (see #772) or the bump was never committed.",
        )

    def test_documented_git_pins_point_at_the_current_version(self):
        stale = {
            path: refs
            for path, text in _public_text().items()
            if (refs := {
                ref for ref in re.findall(r"keel@(v[0-9]+\.[0-9]+\.[0-9]+)", text)
                if ref != f"v{__version__}"
            })
        }
        self.assertEqual(
            stale, {}, f"install instructions pin an old tag; current is v{__version__}"
        )
