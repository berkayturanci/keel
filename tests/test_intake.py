"""Tests for issue intake readiness classification."""

import unittest

from keel import intake


class TestIssueIntake(unittest.TestCase):
    def test_ready_issue_extracts_objective_deliverable_and_acceptance(self):
        record = intake.assess_issue(
            title="Add release docs",
            body=(
                "## Problem\n"
                "Users need release behavior documented.\n\n"
                "## Proposed direction\n"
                "Document every release parameter.\n\n"
                "## Acceptance criteria\n"
                "- README explains release flags.\n"
                "- Tests cover dry-run output.\n"
            ),
            labels=("risk:docs",),
        )

        self.assertEqual(record["status"], intake.READY)
        self.assertTrue(record["can_mutate_code"])
        self.assertEqual(record["objective"], "Users need release behavior documented.")
        self.assertEqual(record["deliverable"], "Document every release parameter.")
        self.assertEqual(len(record["acceptance_criteria"]), 2)
        self.assertEqual(record["required_docs_tests"]["docs"], "required")
        self.assertEqual(record["required_docs_tests"]["tests"], "required")
        self.assertTrue(record["risk_tier_inputs"]["has_high_risk_signal"])

    def test_missing_acceptance_criteria_needs_input(self):
        record = intake.assess_issue(
            title="Add setup guide",
            body="## Problem\nUsers need setup guidance.\n\n## Deliverable\nWrite the guide.\n",
        )

        self.assertEqual(record["status"], intake.NEEDS_INPUT)
        self.assertFalse(record["can_mutate_code"])
        self.assertIn("acceptance_criteria", record["missing_info"])
        self.assertIn("What acceptance criteria define done", record["questions"][0])
        self.assertEqual(record["ledger_record"]["readiness"], intake.NEEDS_INPUT)

    def test_bullet_objective_and_empty_bullet_are_handled(self):
        record = intake.assess_issue(
            title="Add migration note",
            body=(
                "## Problem\n"
                "- Users need a safer migration warning.\n\n"
                "## Deliverable\n"
                "- Add the warning.\n\n"
                "## Acceptance criteria\n"
                "-   \n"
                "- Warning is documented.\n"
            ),
        )

        self.assertEqual(record["objective"], "Users need a safer migration warning.")
        self.assertEqual(record["deliverable"], "Add the warning.")
        self.assertEqual(record["acceptance_criteria"], ["Warning is documented."])

    def test_ambiguous_scope_needs_input(self):
        record = intake.assess_issue(
            title="Maybe improve sync",
            body=(
                "## Problem\nSync updates can be confusing.\n\n"
                "## Deliverable\nMaybe improve the warning behavior.\n\n"
                "## Acceptance criteria\n"
                "- Existing extensions are preserved.\n"
            ),
        )

        self.assertEqual(record["status"], intake.NEEDS_INPUT)
        self.assertIn("scope_clarity", record["missing_info"])
        self.assertTrue(record["questions"])

    def test_blocked_dependency_blocks_work(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n\n"
                "Blocked by #120.\n"
            ),
        )

        self.assertEqual(record["status"], intake.BLOCKED)
        self.assertFalse(record["can_mutate_code"])
        self.assertTrue(record["blockers"])
        self.assertTrue(record["work_block_policy"]["skip_when_not_ready"])

    def test_blocked_label_without_sentence_uses_default_summary(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n"
            ),
            labels=("blocked",),
        )

        self.assertEqual(record["status"], intake.BLOCKED)
        self.assertEqual(record["blockers"], ["Declared blocked dependency."])

    def test_out_of_scope_wins_over_other_signals(self):
        record = intake.assess_issue(
            title="Support unrelated runtime",
            body=(
                "## Problem\nSomeone requested another runtime.\n\n"
                "## Deliverable\nDo not build it.\n\n"
                "## Acceptance criteria\n"
                "- Request is marked not planned.\n"
            ),
            labels=("not-planned", "blocked"),
        )

        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertEqual(record["questions"], [])
        self.assertFalse(record["can_mutate_code"])


if __name__ == "__main__":
    unittest.main()
