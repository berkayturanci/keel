"""`tier3_globs` must follow the criterion, not just be a list someone edited (#786).

Tier-3 is for changes that can reach past CI. `.github/workflows/**` used to be in
the list wholesale, which tiered up four PRs in one session that could not reach a
user — a workflow comment, two added CI jobs, and one that pinned four action refs
*more tightly* — while leaving `Formula/keel.rb`, the file `brew install` actually
runs, at tier-2. All four were waived; the one that shipped to users was not.

Splitting the glob is only half a fix. A static list drifts the moment someone adds
a workflow, and the drift is silent: the new file simply lands at tier-2. So the
test below asserts the *rule* — every workflow that can write or holds a secret is
tiered up — rather than pinning the list it produces.
"""

from __future__ import annotations

import re
import unittest
from fnmatch import fnmatch
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: `secrets.GITHUB_TOKEN` is the per-run token every workflow gets; its scope is
#: whatever `permissions:` grants, which is covered separately. Any *other* secret
#: is a stored credential and does raise the tier.
DEFAULT_SECRET = "GITHUB_TOKEN"


def _tier3_globs() -> tuple[str, ...]:
    config = yaml.safe_load((REPO_ROOT / ".keel" / "project.yaml").read_text(encoding="utf-8"))
    return tuple(config["knobs"]["tier3_globs"])


def _write_permissions(document: object) -> set[str]:
    """Every `<scope>: write` in a workflow, top-level and per-job."""
    granted: set[str] = set()

    def collect(block: object) -> None:
        if isinstance(block, dict):
            granted.update(scope for scope, value in block.items() if value == "write")
        elif block == "write-all":
            granted.add("write-all")

    if not isinstance(document, dict):
        return granted
    collect(document.get("permissions"))
    for job in (document.get("jobs") or {}).values():
        if isinstance(job, dict):
            collect(job.get("permissions"))
    return granted


def _privileged(path: Path) -> set[str]:
    """Why this workflow can reach past CI, or an empty set if it cannot."""
    text = path.read_text(encoding="utf-8")
    reasons = {f"{scope}: write" for scope in _write_permissions(yaml.safe_load(text))}
    reasons |= {
        f"secrets.{name}"
        for name in set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", text))
        if name != DEFAULT_SECRET
    }
    return reasons


def _is_tier3(relative: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch(relative, pattern) for pattern in globs)


class TestTierThreeCoversWhatCanReachOut(unittest.TestCase):
    def test_workflows_exist_to_check(self):
        # Without this the assertions below pass on an empty directory — the
        # vacuous-green failure this repo keeps finding in other checks.
        self.assertTrue(list(WORKFLOWS.glob("*.yml")), "no workflows found")

    def test_every_privileged_workflow_is_tier3(self):
        globs = _tier3_globs()
        missing = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            reasons = _privileged(path)
            relative = path.relative_to(REPO_ROOT).as_posix()
            if reasons and not _is_tier3(relative, globs):
                missing[relative] = sorted(reasons)
        self.assertEqual(
            {},
            missing,
            "workflows that can write or hold a secret must be in tier3_globs",
        )

    def test_the_unprivileged_workflows_are_not_tiered_up(self):
        # The other direction, and the one the split exists for. Left unchecked,
        # the list creeps back to `.github/workflows/**` one entry at a time and
        # the waivers come back with it.
        globs = _tier3_globs()
        over = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if not _privileged(path) and _is_tier3(relative, globs):
                over[relative] = "no write permission, no stored secret"
        self.assertEqual({}, over, "a read-only workflow does not need three reviewers")

    def test_the_artifacts_users_install_are_tier3(self):
        # Not workflows, but they reach further than most code in this repo: the
        # formula `brew install` runs (#787) and the hash-locked tooling that
        # publishes to PyPI (#779). Both sat at tier-2 while every CI edit was
        # tier-3, which is the imbalance #786 is about.
        globs = _tier3_globs()
        for path in ("Formula/keel.rb", ".github/requirements/publish-tools.txt"):
            with self.subTest(path=path):
                self.assertTrue(_is_tier3(path, globs))

    def test_the_blanket_workflow_glob_is_gone(self):
        self.assertNotIn(".github/workflows/**", _tier3_globs())


if __name__ == "__main__":
    unittest.main()
