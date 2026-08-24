"""Pinned GitHub Action SHAs must match the version they claim to be (issue #784).

Pinning by SHA is the security control; the ``# vX.Y.Z`` beside it is how a
reviewer decides whether that SHA is worth trusting. When the two disagree the
annotation is worse than absent, because it invites a check it cannot support.

This repo kept 32 pins correct by discipline alone — and then the two CI jobs
added in #778 and #780 used bare ``@v4``/``@v5`` refs, which is how #783 came to
propose bumping them to ``@v7`` while leaving them unpinned. Discipline held
until the moment it did not, and nothing noticed.

The offline cases check shape, because a malformed pin is a defect anywhere. The
online case resolves each comment against the upstream tag, needs the network,
and runs only when ``KEEL_CHECK_EXTERNAL=1`` — the same opt-in the other
compare-against-the-world checks use, so the default suite stays hermetic.
"""

from __future__ import annotations

import json
import os
import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
ONLINE = os.environ.get("KEEL_CHECK_EXTERNAL") == "1"

# owner/repo[/subpath]@<40-hex>  # vX.Y.Z
_PIN = re.compile(
    r"uses:\s*([\w.-]+/[\w.-]+(?:/[\w.-]+)*)@([0-9a-f]{40})[ \t]*#[ \t]*(\S+)"
)
_UNPINNED = re.compile(r"uses:\s*([\w.-]+/[\w.-]+(?:/[\w.-]+)*)@(?![0-9a-f]{40})(\S+)")


#: Trees whose YAML is not something this repo runs. ``docs/`` ships copy-paste
#: workflow snippets for consumers, deliberately written with readable ``@v4``
#: refs; pinning those would be pinning someone else's supply chain for them.
_NOT_OURS = ("docs/", ".venv", "node_modules", ".claude/worktrees/", "build/", "dist/")


def _files():
    """Every YAML file in this repo that pins an action — discovered, not listed.

    Enumerating known locations is exactly how this guard came to report "every
    pin verified" while the composite action at the repo root carried the only
    unpinned ``uses:`` in the tree (#933). A hardcoded list covers the misses
    already found and nothing else, so this walks instead: a composite action
    added at ``.github/actions/<name>/action.yml`` — the ordinary layout — or a
    reusable workflow outside ``.github/workflows/`` is in scope the day it lands,
    under either spelling.
    """
    found = []
    for pattern in ("*.yml", "*.yaml"):
        for path in REPO.rglob(pattern):
            rel = path.relative_to(REPO).as_posix()
            if any(part in rel for part in _NOT_OURS) or not path.is_file():
                continue
            if "uses:" in path.read_text(encoding="utf-8", errors="ignore"):
                found.append(path)
    return sorted(found)


def _label(path: Path) -> str:
    return str(path.relative_to(REPO))


def _pins():
    for path in _files():
        for action, sha, claim in _PIN.findall(path.read_text(encoding="utf-8")):
            yield _label(path), action, sha, claim


def _tags_for(repo, sha):
    """Every tag in ``repo`` pointing at ``sha``, or None if the lookup failed."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/tags?per_page=100",
        headers={"Accept": "application/vnd.github+json"},
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        # Being unable to look is not evidence the pin is wrong.
        return None
    return {tag["name"] for tag in payload if tag.get("commit", {}).get("sha") == sha}


UNREACHABLE_MESSAGE = (
    "the online pin check was requested but GitHub could not be reached; this "
    "fails rather than skipping, because a skipped test does not fail CI and so "
    "reads as a pass"
)


def judge_pins(pins, resolve):
    """Return ``(wrong, unreachable)`` for ``pins``, given a tag resolver.

    Pure, so the unreachable branch can be exercised without a network. That
    branch used to call ``skipTest`` on the first failed lookup: a skip does not
    fail CI, so a wrong pin could merge during any GitHub blip, and one
    unreachable repo stopped every later pin from being judged at all (#933).
    Unreachable repos are collected and reported by the caller instead.
    """
    wrong, unreachable = [], []
    for name, action, sha, claim in pins:
        repo = "/".join(action.split("/")[:2])
        tags = resolve(repo, sha)
        if tags is None:
            unreachable.append(f"{name}: {repo}")
        elif not tags:
            wrong.append(f"{name}: {action}@{sha[:8]} is not any released tag")
        elif claim not in tags:
            wrong.append(f"{name}: {action}@{sha[:8]} says {claim}, is {sorted(tags)}")
    return wrong, unreachable


def assert_pins_ok(case, pins, resolve):
    """Assert every pin resolved, and that every repo could be reached.

    The online test is a one-line call to this so the *wiring* is testable
    offline. With the unreachable assertion written inline in a class gated on
    ``KEEL_CHECK_EXTERNAL=1``, deleting it left the default suite green — the one
    line enforcing this issue's rule for this gate was the one line nothing
    enforced.
    """
    wrong, unreachable = judge_pins(pins, resolve)
    case.assertEqual([], wrong)
    case.assertEqual([], unreachable, UNREACHABLE_MESSAGE)


class TestActionPinShape(unittest.TestCase):
    """Offline: the pins are shaped so the online check can say something."""

    def test_every_action_is_pinned_to_a_sha(self):
        loose = [
            (_label(path), action, ref)
            for path in _files()
            for action, ref in _UNPINNED.findall(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], loose, "actions must be pinned to a 40-hex commit SHA")

    def test_the_published_action_is_in_scope(self):
        """The file consumers actually run must be one of the files checked.

        This check reported "every pin verified" for months while the composite
        action at the repo root — the only file that executes inside other
        people's workflows — was outside its glob.
        """
        published = REPO / "action.yml"
        if not published.is_file():  # pragma: no cover - repo layout guard
            self.skipTest("this repo publishes no composite action")
        self.assertIn(published, _files())

    def test_scope_is_discovered_rather_than_listed(self):
        """A hardcoded list only covers the misses already found.

        `.github/actions/<name>/action.yml` is the ordinary composite-action
        layout and does not exist here yet. Naming it in an exclusion list would
        be the same enumeration mistake one level up, so instead assert the
        walk's *shape*: it reaches outside `.github/workflows/`, and it filters
        on content rather than on a location whitelist.
        """
        self.assertTrue(
            any("workflows" not in _label(path) for path in _files()),
            "the walk never leaves .github/workflows/, so it is still a whitelist",
        )
        for path in _files():
            with self.subTest(path=_label(path)):
                self.assertIn("uses:", path.read_text(encoding="utf-8"))
        self.assertFalse(
            any(part.startswith(".github/actions") for part in _NOT_OURS),
            "the ordinary composite-action layout is excluded from discovery",
        )

    def test_consumer_facing_docs_snippets_are_out_of_scope(self):
        """Docs ship readable `@v4` examples on purpose.

        Pinning those would be pinning a reader's supply chain for them, and the
        guard failing on documentation would train people to widen its exclusions
        rather than fix a real pin.
        """
        self.assertIn("docs/", _NOT_OURS)
        self.assertEqual([], [p for p in _files() if _label(p).startswith("docs/")])

    def test_every_pin_carries_an_exact_version_comment(self):
        # A bare major ("# v4") is not enough: major tags move, so the comment
        # stops identifying a specific artifact the moment upstream retags.
        vague = [
            (name, action, claim)
            for name, action, _sha, claim in _pins()
            if not re.fullmatch(r"v\d+\.\d+\.\d+", claim)
        ]
        self.assertEqual([], vague, "pin comments must name an exact vX.Y.Z release")

    def test_the_pin_pattern_matches_something(self):
        # Keeps the assertions above from passing vacuously if the regex or the
        # comment convention changes: zero pins found would make them trivially
        # true while checking nothing.
        self.assertGreater(len(list(_pins())), 0, "no pinned actions found to check")

    def test_one_sha_is_never_given_two_different_versions(self):
        # Offline and free: the same SHA annotated differently in two files means
        # at least one is wrong, without asking GitHub which.
        claims = {}
        for name, action, sha, claim in _pins():
            claims.setdefault((action, sha), {}).setdefault(claim, []).append(name)
        self.assertEqual(
            {},
            {key: value for key, value in claims.items() if len(value) > 1},
            "one SHA is annotated with two versions",
        )


@unittest.skipUnless(ONLINE, "set KEEL_CHECK_EXTERNAL=1 to resolve pins against GitHub")
class TestActionPinsMatchUpstream(unittest.TestCase):
    """Online: the version comment is the version the SHA actually is."""

    def test_each_pin_resolves_to_the_release_it_claims(self):
        assert_pins_ok(self, _pins(), _tags_for)


class TestUnreachableIsAFailureNotASkip(unittest.TestCase):
    """The online check's verdict logic, exercised offline.

    Kept separate from the class above, which needs ``KEEL_CHECK_EXTERNAL=1``.
    Putting the "GitHub is unreachable" case only inside that class meant the
    branch was never executed by the default suite, so a regression to
    ``skipTest`` — the #933 defect — could not be caught by anything.
    """

    PINS = (("ci.yml", "actions/checkout", "a" * 40, "v7.0.1"),)

    def test_an_unreachable_repo_is_reported_not_skipped(self):
        wrong, unreachable = judge_pins(self.PINS, lambda repo, sha: None)
        self.assertEqual([], wrong)
        self.assertEqual(["ci.yml: actions/checkout"], unreachable)

    def test_one_unreachable_repo_does_not_hide_a_wrong_pin_elsewhere(self):
        pins = (*self.PINS, ("pages.yml", "actions/deploy-pages", "b" * 40, "v5.0.0"))

        def resolve(repo, sha):
            return None if repo == "actions/checkout" else {"v4.0.0"}

        wrong, unreachable = judge_pins(pins, resolve)
        self.assertEqual(1, len(unreachable))
        self.assertEqual(1, len(wrong), "the reachable pin was not judged")
        self.assertIn("says v5.0.0", wrong[0])

    def test_a_sha_that_is_no_release_at_all_is_wrong_not_unreachable(self):
        wrong, unreachable = judge_pins(self.PINS, lambda repo, sha: set())
        self.assertEqual([], unreachable)
        self.assertIn("is not any released tag", wrong[0])

    def test_a_matching_pin_reports_nothing(self):
        wrong, unreachable = judge_pins(self.PINS, lambda repo, sha: {"v7.0.1", "v7"})
        self.assertEqual(([], []), (wrong, unreachable))

    def test_the_online_check_fails_on_an_unreachable_repo(self):
        """The assertion itself, not just the function it calls.

        `judge_pins` reporting an unreachable repo is worth nothing if the caller
        ignores the second return value. This exercises the exact wiring the
        online test uses.
        """
        with self.assertRaises(AssertionError) as caught:
            assert_pins_ok(self, self.PINS, lambda repo, sha: None)
        self.assertIn("reads as a pass", str(caught.exception))

    def test_the_online_check_fails_on_a_wrong_pin(self):
        with self.assertRaises(AssertionError):
            assert_pins_ok(self, self.PINS, lambda repo, sha: {"v4.0.0"})

    def test_the_online_check_passes_when_everything_resolves(self):
        assert_pins_ok(self, self.PINS, lambda repo, sha: {"v7.0.1"})


class TestTheGuardsScope(unittest.TestCase):
    """What the guard looks at is the thing that failed before (#933)."""

    def test_paths_are_reported_relative_to_the_repo(self):
        """A bare filename cannot tell `action.yml` from `.github/…/action.yml`."""
        labels = [_label(path) for path in _files()]
        self.assertIn("action.yml", labels)
        self.assertIn(".github/workflows/ci.yml", labels)

    def test_both_yaml_spellings_are_in_scope(self):
        """A file added under the other spelling must not be silently skipped.

        There is no `.yaml` in the tree today, so this asserts the walk's
        patterns rather than its output — the alternative is a fixture file
        added purely to be discovered, which is worse.
        """
        import inspect

        source = inspect.getsource(_files)
        self.assertIn('"*.yml"', source)
        self.assertIn('"*.yaml"', source)


if __name__ == "__main__":
    unittest.main()
