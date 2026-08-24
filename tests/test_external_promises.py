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
import unittest.mock
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


def _declared_version() -> str | None:
    """The version this tree declares, read off disk.

    This module deliberately reads files rather than importing keel — it checks
    what the repository *promises*, not what the package computes — so the
    version is parsed from pyproject.toml in the same spirit.
    """
    match = re.search(r'(?m)^version = "([^"]+)"',
                      (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _could_not_look(case, what: str, exc: Exception):
    """An I/O failure in a check the operator asked for is a failure, not a skip.

    These guards used to raise ``SkipTest`` here, reasoning that "being unable to
    look is not evidence the promise is broken". True of the *verdict* and
    irrelevant to the *signal*: a skipped test does not fail CI, so nothing
    downstream can tell a skip from a pass, and a genuinely broken formula could
    ship during any transient network failure (#933).

    These classes only run under ``KEEL_CHECK_EXTERNAL=1``. The operator asked
    for the online check; "I could not ask" is a failure of the check.
    """
    case.fail(
        f"could not check {what}: {exc}. Reported as a failure rather than skipped, "
        "because a skipped test does not fail CI and so reads as a pass. Re-run when "
        "the network is available."
    )


def _reachable(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        return exc.code < 400
    except OSError as exc:
        # The subject/instrument distinction #675 turned on is real, but skipping
        # is the wrong way to record it: a skipped test does not fail CI, so every
        # caller of this helper fail-opened on any network blip (#933). Raising an
        # AssertionError keeps the distinction in the message and out of the
        # verdict — these callers only run under KEEL_CHECK_EXTERNAL=1, so the
        # operator asked for the check.
        raise AssertionError(
            f"could not check whether {url} resolves: {exc}. Reported as a failure "
            "rather than skipped, because a skipped test does not fail CI and so "
            "reads as a pass. Re-run when the network is available."
        ) from exc


class TestNotLookingIsNotAPass(unittest.TestCase):
    """The rule itself, asserted hermetically.

    The checks below fail on an I/O error instead of skipping, because a skipped
    test does not fail CI and so reads as a pass — a broken formula could ship
    during any network blip (#933). That rule lived only in the call sites, so
    reverting all of them to ``SkipTest`` left the suite identically green. It
    is pinned here instead, offline.
    """

    def test_an_unreachable_url_fails_rather_than_skipping(self):
        # The network is stubbed rather than relied on. Reaching for a
        # guaranteed-unresolvable host would still be a live DNS call in a suite
        # that claims to be hermetic, and a stalled resolver would spend the
        # 20-second timeout to prove something that is a pure branch.
        with unittest.mock.patch.object(
            urllib.request, "urlopen", side_effect=OSError("simulated outage")
        ):
            with self.assertRaises(AssertionError) as caught:
                _reachable("https://example.test/nope")
        self.assertIn("reads as a pass", str(caught.exception))

    def test_the_shared_helper_fails_rather_than_skipping(self):
        with self.assertRaises(AssertionError) as caught:
            _could_not_look(self, "the formula's tarball", OSError("boom"))
        self.assertIn("the formula's tarball", str(caught.exception))
        self.assertIn("reads as a pass", str(caught.exception))

    def test_neither_raises_a_skip(self):
        """`SkipTest` is not an `AssertionError`, so the guards above would not

        catch a revert that swapped one for the other if they only asserted
        "something was raised".
        """
        stub = unittest.mock.patch.object(
            urllib.request, "urlopen", side_effect=OSError("simulated outage")
        )
        for call in (
            lambda: _reachable("https://example.test/nope"),
            lambda: _could_not_look(self, "x", OSError("boom")),
        ):
            with self.subTest(call=call), stub:
                try:
                    call()
                except unittest.SkipTest:  # pragma: no cover - the regression
                    self.fail("the guard skips instead of failing")
                except AssertionError:
                    # The expected outcome. Written as an explicit `except`
                    # rather than `assertRaises`, because a `SkipTest` escaping
                    # that context manager would *skip* this test instead of
                    # failing it — which is precisely the regression under
                    # guard, so the guard would hide it.
                    continue
                self.fail("the guard raised nothing at all")


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
                # A release PR bumps the declared version and, because
                # test_homebrew_formula_matches_the_project pins the url to it,
                # the formula points at a tag that is created *from the commit
                # this PR produces*. It cannot exist yet, and no value of the
                # formula satisfies both tests at once (#839). Only that window
                # is exempt: a url naming any other version must still resolve.
                if _declared_version() and f"/tags/v{_declared_version()}." in url.group(0):
                    self.skipTest(
                        f"v{_declared_version()} is not tagged yet; the checksum "
                        "cannot be verified until the release is tagged"
                    )
                self.fail(f"the formula points at {url.group(0)}, which does not exist")
            _could_not_look(self, "the formula's tarball", exc)
        except (urllib.error.URLError, OSError) as exc:
            _could_not_look(self, "the formula's tarball", exc)
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
            _could_not_look(self, "the published tap formula", exc)
        except (urllib.error.URLError, OSError) as exc:
            _could_not_look(self, "the published tap formula", exc)

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
            _could_not_look(self, "the versions published on PyPI", exc)

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
