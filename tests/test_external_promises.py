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

import email.message
import hashlib
import json
import os
import re
import sys
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

from keel import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Surfaces a user reads and copies from.
PUBLIC = ("README.md", "docs", "website")
ONLINE = os.environ.get("KEEL_CHECK_EXTERNAL") == "1"

#: The tap `brew install` resolves from, and — since #1023 — the only place a
#: keel formula exists at all: the release renders one and attaches it, and the
#: tap pulls that asset (#774 for why the direction is a pull). Declared rather
#: than read from the environment: an env-gated check is a check nobody sets.
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
                out[str(f.relative_to(REPO_ROOT))] = f.read_text(encoding="utf-8", errors="replace")
    return out


#: A url `brew` may be pointed at. The guard #990 named as the load-bearing one:
#: not "does this digest match" — a digest is a correct description of whatever
#: it was taken over — but "is this an archive of *this* project, at a tag".
#: Written as a pure function so it can be exercised offline in both directions;
#: the same rule is applied to the rendered formula in `publish.yml` and to the
#: incoming formula in the tap's own workflow.
_OUR_TAG_ARCHIVE = re.compile(
    r"^https://github\.com/berkayturanci/keel/archive/refs/tags/v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$"
)


def _is_our_tag_archive(url: str) -> bool:
    return _OUR_TAG_ARCHIVE.match(url) is not None


def _tap_file(path: str, what: str, case) -> str:
    """Read one file from the tap, as a user's `brew tap` would see it.

    The contents API, not raw.githubusercontent.com: `raw` is CDN-cached for
    minutes, so right after a sync it serves the previous formula and a check
    against it fails on a tap that is already correct — observed while fixing
    #805. `brew tap` clones the repository, so the API is also the closer view.
    """
    request = urllib.request.Request(
        f"https://api.github.com/repos/{HOMEBREW_TAP}/contents/{path}",
        headers={"Accept": "application/vnd.github.raw", "User-Agent": "keel-tests"},
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(50 * 1024 * 1024).decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            case.fail(f"{HOMEBREW_TAP} has no {path}; brew install would fail")
        _could_not_look(case, what, exc)
    except (urllib.error.URLError, OSError) as exc:
        _could_not_look(case, what, exc)
    # Every branch above either returned the body or failed the test; this line is
    # the explicit end of the function so no path falls through implicitly.
    raise AssertionError(f"unreachable: could not read {path} and the failure was not reported")


def _could_not_look(case, what: str, exc: Exception) -> NoReturn:
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


#: The tarball the stub formula points at, and the url the PyPI check reads, so
#: a simulated HTTP status names the artifact the real check would have fetched.
_STUB_TARBALL = "https://github.com/berkayturanci/keel/archive/refs/tags/v1.0.0.tar.gz"
_PYPI_JSON = "https://pypi.org/pypi/keel-workflow/json"

#: A tap formula body shaped like the real one: the assertions that run before
#: the tarball fetch read a keel tag archive and a top-level sha256 out of it and
#: nothing else. The digest is deliberately wrong, so a run that reached the
#: comparison would fail loudly instead of passing by accident.
_STUB_FORMULA = (f'  url "{_STUB_TARBALL}"\n  sha256 "{"0" * 64}"\n').encode()


def _stub_fetch(body: bytes) -> unittest.mock.MagicMock:
    """A stand-in for one `urlopen` call: a context manager whose body is `body`."""
    response = unittest.mock.MagicMock()
    response.__enter__.return_value.read.return_value = body
    return response


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    """A real `HTTPError`, because the handlers under guard branch on `.code`."""
    return urllib.error.HTTPError(url, code, "simulated", email.message.Message(), None)


def _failure_message(failure: str) -> str:
    """The message a failure was raised with, without the traceback's own source.

    A fragment asserted against the whole formatted traceback can be satisfied by
    the *source line* of the handler that did not run — `self.fail("...")`'s
    literal text is printed as a stack frame whenever a later frame fails — which
    would make the per-handler fragments below insensitive to precisely the
    regression they exist to catch.
    """
    return failure.rsplit("AssertionError: ", 1)[-1]


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

    def test_a_fetch_that_could_not_happen_fails_at_the_call_sites_too(self):
        """Every handler the two restructured `else` clauses depend on, driven.

        The two online checks below read a name bound inside a `try` and used in
        that `try`'s `else`. The `else` is what makes the binding provable to a
        reader and to CodeQL (#1063), and it is load-bearing in one direction
        only: it is reached solely on the path that bound the name, so if a
        handler ever stopped ending in a call that never returns, the read would
        be *skipped* and the check would pass without having looked — the
        fail-open #933 is about, arriving quietly instead of as a loud
        unbound-name error the way it would have before the restructuring.

        **What this pins.** One row per handler each `else` depends on. Each
        drives its check with `urlopen` stubbed to raise the error that reaches
        that handler, and asserts the check ends in a failure *from that
        handler* — identified by a fragment of the handler's own message, not
        merely "a failure happened". The handlers are enumerated from the source
        rather than assumed to match each other:

        * `test_the_tap_serves_an_installable_formula` has two `except` clauses
          and three paths through them: an `HTTPError` whose `.code` is 404 (the
          missing artifact, reported by a direct `self.fail`), an `HTTPError`
          with any other code (which falls *through* that `if` to the shared
          helper), and a `URLError`/`OSError` (the unreachable host, the shared
          helper again).
        * `test_the_version_is_not_one_pypi_already_published_differently` has
          exactly one, `except OSError`, and no direct-call branch. Because
          `HTTPError` subclasses `OSError`, an HTTP answer reaches that same
          clause rather than a clause of its own; it is driven here too, so the
          shape is shown rather than inferred from the check above.

        Sensitivity is established by mutation, one handler at a time:
        substituting a returning stub for a handler's failure call makes the
        corresponding row fail. The runs are recorded in the pull request.

        **What this does not pin.** Not the `else` bodies: a wrong digest and a
        repo version behind PyPI are the online checks' own subject. Not
        anything about the world — every response here is a stub, so no row says
        the tap or PyPI is reachable, current, or in sync. Not *why* a handler
        never returns: `_could_not_look` is pinned by the tests above and
        `self.fail` is unittest's contract; this shows the property as observed
        at the call site. Not `_tap_file`'s own handlers — it ends in an explicit
        `raise AssertionError` instead of an `else`, so no binding of its depends
        on them, and it is reached here only through its success path. Not the
        other online classes, which bind nothing inside a `try`.

        Both online classes are gated behind `KEEL_CHECK_EXTERNAL=1` and that job
        is not a required check, so no blocking signal would catch the
        regression. This test is not gated: it drives those checks itself with
        the network stubbed to raise.
        """
        for handler, case_class, name, responses, expected in (
            (
                "unreachable host: `except (URLError, OSError)` -> `_could_not_look`",
                TestHomebrewPromise,
                "test_the_tap_serves_an_installable_formula",
                # The tap read succeeds so the tarball fetch is reached at all;
                # that second call — the one the restructured `try` wraps — is
                # the outage.
                [_stub_fetch(_STUB_FORMULA), OSError("simulated outage")],
                ("could not check the tap formula's tarball", "reads as a pass"),
            ),
            (
                "missing artifact: `except HTTPError` with .code 404 -> `self.fail` directly",
                TestHomebrewPromise,
                "test_the_tap_serves_an_installable_formula",
                [_stub_fetch(_STUB_FORMULA), _http_error(_STUB_TARBALL, 404)],
                (_STUB_TARBALL, "which does not exist"),
            ),
            (
                "other status: `except HTTPError` past the 404 `if` -> `_could_not_look`",
                TestHomebrewPromise,
                "test_the_tap_serves_an_installable_formula",
                [_stub_fetch(_STUB_FORMULA), _http_error(_STUB_TARBALL, 503)],
                ("could not check the tap formula's tarball", "reads as a pass"),
            ),
            (
                "unreachable host: `except OSError` -> `_could_not_look`",
                TestPublishedVersion,
                "test_the_version_is_not_one_pypi_already_published_differently",
                [OSError("simulated outage")],
                ("could not check the versions published on PyPI", "reads as a pass"),
            ),
            (
                "HTTP status: `HTTPError` subclasses `OSError`, so the same one clause",
                TestPublishedVersion,
                "test_the_version_is_not_one_pypi_already_published_differently",
                [_http_error(_PYPI_JSON, 404)],
                ("could not check the versions published on PyPI", "reads as a pass"),
            ),
        ):
            with self.subTest(check=name, handler=handler):
                online = unittest.mock.patch.object(sys.modules[__name__], "ONLINE", True)
                outage = unittest.mock.patch.object(
                    urllib.request, "urlopen", side_effect=responses
                )
                outcome = unittest.TestResult()
                with online, outage:
                    case_class(name).run(outcome)
                self.assertEqual(
                    outcome.errors,
                    [],
                    f"{name} raised instead of reporting a failure ({handler}): {outcome.errors}",
                )
                self.assertEqual(
                    outcome.skipped,
                    [],
                    f"{name} skipped rather than failing when it could not look "
                    f"({handler}): {outcome.skipped}",
                )
                self.assertEqual(
                    len(outcome.failures),
                    1,
                    f"{name} passed on a fetch that never happened ({handler}): the `else` "
                    "was skipped and no handler failed the test, so 'could not look' read "
                    "as a pass",
                )
                message = _failure_message(outcome.failures[0][1])
                for fragment in expected:
                    self.assertIn(
                        fragment,
                        message,
                        f"{name} failed, but not from the handler under test ({handler}): "
                        "the handler stopped reporting the outcome it exists to report",
                    )


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
    """`brew install` resolves from a tap, and the tap is now the only copy.

    Until #1023 this repository committed `Formula/keel.rb` and the tap mirrored
    it, so the guards here could examine a local file. They cannot any more, and
    that is the point: the url/sha256 pair is unknowable until the tag exists
    (#990), so it is rendered during the release and attached to it. The subject
    of every check below is therefore the **tap** — the copy `brew` downloads —
    and never this branch's version, because the tap pulls on a schedule and is
    legitimately behind between a release and its next sync. A check that failed
    on that lag would block the only sequence able to satisfy it.
    """

    def test_a_documented_brew_install_has_a_formula_behind_it(self):
        promised = {name for text in _public_text().values() for name in _BREW.findall(text)}
        if not promised:
            self.skipTest("no brew install documented")
        self.assertTrue(
            (REPO_ROOT / "packaging" / "homebrew" / "keel.rb.template").exists(),
            f"docs promise `brew install {sorted(promised)[0]}` with no formula template "
            "for the release to render",
        )

    def test_a_bare_formula_name_needs_a_tap(self):
        """`brew install keel` only works from a tap."""
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to check reachability")
        self.assertTrue(
            _reachable(f"https://github.com/{HOMEBREW_TAP}"),
            f"docs promise a tap install but {HOMEBREW_TAP} does not exist",
        )

    def test_a_url_that_is_not_our_tag_archive_is_rejected(self):
        """#990's load-bearing guard, offline: a digest is a poor proxy for it.

        A formula whose url points somewhere else is the real defect, and it is
        one a checksum comparison passes happily — the digest of the wrong
        tarball is a perfectly correct description of the wrong tarball.
        """
        good = "https://github.com/berkayturanci/keel/archive/refs/tags/v1.19.3.tar.gz"
        self.assertTrue(_is_our_tag_archive(good))
        for bad in (
            "https://github.com/someone-else/keel/archive/refs/tags/v1.19.3.tar.gz",
            "https://example.com/keel/archive/refs/tags/v1.19.3.tar.gz",
            "https://github.com/berkayturanci/keel/archive/refs/heads/main.tar.gz",
            "https://github.com/berkayturanci/keel-visual/archive/refs/tags/v1.19.3.tar.gz",
        ):
            with self.subTest(url=bad):
                self.assertFalse(_is_our_tag_archive(bad))

    def test_the_tap_serves_an_installable_formula(self):
        """`brew install` refuses on a checksum mismatch, so a wrong one is fatal.

        1.16.0 shipped with 1.15.0's digest (#805) and the tap refused every sync
        for a day after 1.19.1 (#981). Both were the committed copy going stale.
        This asserts the property that actually matters to a user — what the tap
        serves downloads and hashes to what it declares — against the copy that
        serves them.
        """
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to fetch the tap and its tarball")
        formula = _tap_file("Formula/keel.rb", "the published tap formula", self)
        url = re.search(r"https://github\.com/\S+?\.tar\.gz", formula)
        self.assertIsNotNone(url, "the tap's formula has no source tarball url")
        self.assertTrue(
            _is_our_tag_archive(url.group(0)),
            f"the tap serves a formula whose url is not a keel tag archive: {url.group(0)}",
        )
        declared = re.search(r'(?m)^  sha256 "([0-9a-f]{64})"', formula)
        self.assertIsNotNone(declared, "the tap's formula has no top-level sha256")
        try:
            with urllib.request.urlopen(url.group(0), timeout=60) as response:
                payload = response.read(50 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"the tap's formula points at {url.group(0)}, which does not exist")
            _could_not_look(self, "the tap formula's tarball", exc)
        except (urllib.error.URLError, OSError) as exc:
            _could_not_look(self, "the tap formula's tarball", exc)
        else:
            # `else`, not a trailing statement: the digest comparison is reached
            # only on the path that actually bound `payload`. Every handler above
            # ends in a call that never returns, but that guarantee lives in
            # `self.fail`/`_could_not_look` rather than here, so neither a reader
            # nor a static analyser could see it at the point of use
            # (`py/uninitialized-local-variable`, #1063). The three outcomes stay
            # distinct: a wrong digest fails here, an unreadable tarball fails in
            # the handler, and only a fetch that succeeded reaches this block.
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(),
                declared.group(1),
                "the tap's sha256 is not the digest of the tarball it points at; "
                "brew install would refuse",
            )


class TestTheTapReadsTheReleaseAsset(unittest.TestCase):
    """The one thing the other repository had to be told (#1023).

    The tap's `sync-formula.yml` pulled `contents/Formula/keel.rb` from this
    repository every thirty minutes. That file is gone, so an un-repointed tap
    404s on a schedule forever — the shape of #981 arriving from a new direction.
    `packaging/homebrew/tap-sync-formula.patch` is the change it needs; this pair
    of checks is how the claim that it was applied stops being only a claim.

    The offline half is a claim in a reviewable form rather than proof — nothing
    offline can read another repository — and requiring the tap's commit sha is
    what makes it falsifiable. The online half is the real check.
    """

    #: The path the tap must no longer fetch. It is the file this repo deleted.
    RETIRED = "contents/Formula/keel.rb"
    #: What it must fetch instead: rendered per release, never stale.
    ASSET = "releases/latest/download/keel.rb"

    def test_the_repointing_is_recorded_with_a_commit_sha(self):
        marker = REPO_ROOT / "packaging" / "homebrew" / "TAP_REPOINTED"
        self.assertTrue(marker.exists(), "packaging/homebrew/TAP_REPOINTED is missing")
        self.assertRegex(
            marker.read_text(encoding="utf-8"),
            r"(?m)^tap-sync-formula: [0-9a-f]{40}$",
            "record the tap commit that applied packaging/homebrew/tap-sync-formula.patch: "
            'echo "tap-sync-formula: $(git -C ../homebrew-keel rev-parse HEAD)" '
            "> packaging/homebrew/TAP_REPOINTED",
        )

    def test_the_patch_is_shipped_next_to_the_marker(self):
        """A marker asking for a change nobody can find is a request, not a fix."""
        patch = (REPO_ROOT / "packaging" / "homebrew" / "tap-sync-formula.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn("--- a/.github/workflows/sync-formula.yml", patch)
        self.assertIn("+++ b/.github/workflows/sync-formula.yml", patch)
        # It must both retire the old source and name the new one; a patch that
        # only deleted the fetch would leave the tap serving nothing new forever.
        self.assertIn(f'-{" " * 12}"https://api.github.com/repos/berkayturanci/keel/', patch)
        self.assertIn(self.ASSET, patch)

    def test_the_live_tap_no_longer_pulls_the_deleted_file(self):
        if not ONLINE:
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to read the tap's workflow")
        workflow = _tap_file(".github/workflows/sync-formula.yml", "the tap's sync workflow", self)
        self.assertNotIn(
            self.RETIRED,
            workflow,
            f"{HOMEBREW_TAP} still fetches a file this repository no longer has; its sync "
            "will fail every 30 minutes. Apply packaging/homebrew/tap-sync-formula.patch.",
        )
        self.assertIn(
            self.ASSET,
            workflow,
            f"{HOMEBREW_TAP} does not read the release asset; releases would never reach it",
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
        else:
            # See the note in `test_the_tap_serves_an_installable_formula`: the
            # comparison lives in the `else` so `published` is provably bound
            # where it is read. `_could_not_look` never returns — pinned by
            # `TestNotLookingIsNotAPass` above — so "could not ask PyPI" still
            # fails rather than reaching this block and passing.
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
            if (
                refs := {
                    ref
                    for ref in re.findall(r"keel@(v[0-9]+\.[0-9]+\.[0-9]+)", text)
                    if ref != f"v{__version__}"
                }
            )
        }
        self.assertEqual(
            stale, {}, f"install instructions pin an old tag; current is v{__version__}"
        )
