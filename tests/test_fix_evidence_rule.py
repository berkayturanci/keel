"""The fix-evidence rule is stated in three places, and the strict two agree (#1289).

An audit reverted each of 14 closed swarm fixes and re-ran its guarding tests. Today all
14 fail without their fix; **three survived at the time their issue was closed** — #877,
whose fix was never written, and half of each of #871 and #879. All three offered
"Maintained 100% line + branch test coverage across the repository." as evidence, a
sentence that cannot fail: `fail_under = 100` is enforced in CI, so it is true of every
merged pull request before anyone writes it.

The rule is stated three times **at two different strengths**, which is the point:

* `AGENTS.md` and `src/keel/adapters/commands/ship.md` bind the agents that open most of
  the pull requests here, and that authored every closure the audit found wanting. An
  agent can run the revert itself, so it is required to.
* `CONTRIBUTING.md` and the pull-request template ask a human contributor for the same
  sentence and explicitly do not gate on it. A required field that a newcomer does not
  understand is filled with boilerplate — which is how the coverage sentence got there.

Two statements of one rule can disagree, so the two strict copies are compared rather
than trusted: the `N/A` category list and the unit of evidence. The prose is not asserted
word for word; a test that pins wording gets edited into agreement rather than read.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS = REPO_ROOT / "AGENTS.md"
SHIP = REPO_ROOT / "src" / "keel" / "adapters" / "commands" / "ship.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
TEMPLATE = REPO_ROOT / ".github" / "pull_request_template.md"

#: The closed list both strict copies must offer, and no more.
CATEGORIES = ("docs", "pure refactor", "dependency bump", "packaging")


def _flat(path: Path) -> str:
    """The file with runs of whitespace collapsed.

    These files wrap at ~95 columns, so a phrase the rule turns on is routinely split
    across a line break. Matching the raw text pins the wrap, not the rule.
    """
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _categories(text: str) -> set[str]:
    """The `N/A — <a | b | c>` category list as that file states it.

    Anchored on the `|` on purpose: `ship.md` also carries the older, free-form
    `N/A — <reason>` for its other sections, and matching the first `N/A —` in the file
    would compare that instead. The first version of this test did exactly that and
    reported a disagreement that was really an ambiguity.
    """
    found = re.search(r"`N/A — <([^>]*\|[^>]*)>`", text)
    return {part.strip() for part in found.group(1).split("|")} if found else set()


class TheStrictCopiesAgree(unittest.TestCase):
    def test_both_strict_copies_offer_the_same_N_A_categories(self):
        agents, ship = _categories(_flat(AGENTS)), _categories(_flat(SHIP))

        self.assertEqual(set(CATEGORIES), agents, "AGENTS.md's N/A list drifted")
        self.assertEqual(agents, ship, "ship.md and AGENTS.md disagree on the N/A list")

    def test_both_strict_copies_state_the_unit_as_the_behaviour_not_the_hunk(self):
        """#871's guarded and unguarded arms share one git hunk, so a per-hunk claim
        passes while half the fix is unpinned. Both copies must say so."""
        for path in (AGENTS, SHIP):
            text = _flat(path)
            with self.subTest(file=path.name):
                self.assertTrue("each arm of a conditional" in text, f"{path.name}: unit drifted")
                self.assertTrue("not each git hunk" in text, f"{path.name}: unit drifted")

    def test_both_strict_copies_refuse_N_A_on_the_same_triggers(self):
        """The trigger set, not just the category list. `#1268` carries no label today, so a
        label-only refusal would let `Closes #1268` + `N/A — pure refactor` through — which is
        how #877 was closed."""
        for path in (AGENTS, SHIP):
            text = _flat(path)
            with self.subTest(file=path.name):
                self.assertTrue("`fix(`/`sec(`" in text, f"{path.name}: title trigger dropped")
                self.assertTrue("or unlabelled" in text, f"{path.name}: unlabelled trigger dropped")

    def test_both_strict_copies_require_the_failure_to_be_an_assertion(self):
        """A solo revert that raises or hangs is not a test failing."""
        for path in (AGENTS, SHIP):
            with self.subTest(file=path.name):
                msg = f"{path.name}: failure kind drifted"
                self.assertTrue("as an assertion" in _flat(path), msg)


class TheReviewersAreAskedTheQuestion(unittest.TestCase):
    """Where the enforcement actually lives.

    A rule in a document that nothing verifies is the weakest of the three options open
    to a project — a CI check, a reviewer's rubric, or prose. keel has the middle one:
    `policy_pack.review.additions` reaches every review as
    `review_merge_contract.reviewers.project_additions` (`src/keel/ship.py`). Putting the
    question there is what makes it asked rather than merely written, so it is pinned.
    """

    def test_the_rubric_asks_what_fails_without_the_fix(self):
        import yaml  # noqa: PLC0415 - test-only, keeps the module importable without it

        policy = yaml.safe_load((REPO_ROOT / "projects" / "keel.yaml").read_text("utf-8"))
        additions = " ".join(policy["policy_pack"]["review"]["additions"])
        flat = re.sub(r"\s+", " ", additions)

        self.assertIn("what fails without it", flat)
        self.assertIn("each arm of a conditional", flat)
        self.assertIn("not each git hunk", flat)
        self.assertIn("as an assertion", flat)

    def test_the_rubric_also_asks_the_question_a_revert_cannot_answer(self):
        import yaml  # noqa: PLC0415 - test-only

        policy = yaml.safe_load((REPO_ROOT / "projects" / "keel.yaml").read_text("utf-8"))
        flat = re.sub(r"\s+", " ", " ".join(policy["policy_pack"]["review"]["additions"]))

        self.assertIn("necessary, not sufficient", flat)
        self.assertIn("the fix changes the outcome", flat)


class TheContributorCopiesDoNotGate(unittest.TestCase):
    """The newcomer-facing copies must stay advisory. This is load-bearing: the audit's
    failures were all agent-authored, and a mandatory field is what produced the
    unfalsifiable sentence in the first place."""

    def test_contributing_says_it_is_not_a_gate(self):
        self.assertIn("not a gate", _flat(CONTRIBUTING), "CONTRIBUTING lost its non-gate promise")

    def test_the_template_marks_the_section_optional_and_adds_no_checkboxes(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        heading = "## What breaks without this fix? (optional)"

        self.assertIn(heading, text)
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        self.assertEqual([], re.findall(r"^[-*]\s*\[[ xX]\]", section, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
