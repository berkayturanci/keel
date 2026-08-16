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

        Recorded rather than enforced: whether to publish `homebrew-keel` is an
        owner decision (#774). This fails only once a tap is declared and missing,
        so it cannot block while the question is open.
        """
        tap = os.environ.get("KEEL_HOMEBREW_TAP")
        if not tap:
            self.skipTest("no tap declared; see #774 for the open decision")
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to check reachability")
        self.assertTrue(
            _reachable(f"https://github.com/{tap}"),
            f"KEEL_HOMEBREW_TAP={tap} is declared but the tap repository does not exist",
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
