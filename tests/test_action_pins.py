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


def _files():
    """Every file in this repo that can pin an action.

    Not just ``.github/workflows/*.yml``. The composite action published to the
    Marketplace lives at the repo root, runs in *consumers'* workflows, and was
    the one place carrying an unpinned ``actions/setup-python@v7`` — precisely
    because this check's glob could not see it (#933). ``.yaml`` is included so a
    file added under the other spelling is not silently out of scope.
    """
    seen = []
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        seen.append(path)
    for name in ("action.yml", "action.yaml"):
        candidate = REPO / name
        if candidate.is_file():
            seen.append(candidate)
    return seen


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
        wrong, unreachable = judge_pins(_pins(), _tags_for)
        self.assertEqual([], wrong)
        self.assertEqual([], unreachable, UNREACHABLE_MESSAGE)


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


if __name__ == "__main__":
    unittest.main()
