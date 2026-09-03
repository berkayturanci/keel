"""Tests for the shared work-block contract."""

from __future__ import annotations

import unittest
from pathlib import Path

from keel import config as cfg
from keel import workblock

PROJECTS = Path(__file__).resolve().parents[1] / "projects"


def _config() -> cfg.ProjectConfig:
    return cfg.load_config(PROJECTS / "example-flutter.yaml")


class TestWorkBlockContract(unittest.TestCase):
    def test_daytime_contract_shape(self):
        contract = workblock.contract_as_dict(config=_config(), mode="daytime")
        self.assertEqual(contract["schema_version"], workblock.WORK_BLOCK_SCHEMA_VERSION)
        self.assertEqual(contract["mode"], "daytime")
        self.assertEqual(
            contract["queue"]["accepted_inputs"], ["explicit_issue_numbers", "queue_selector"]
        )
        self.assertEqual(contract["queue"]["explicit_issue_order"], "as-provided")
        self.assertTrue(contract["queue"]["refresh_readiness_between_issues"])
        self.assertTrue(contract["per_issue"]["isolated_branch_worktree"])
        self.assertTrue(contract["per_issue"]["child_honors_capture_contract"])
        self.assertFalse(contract["failure_policy"]["continue_after_blocked"])
        self.assertTrue(contract["failure_policy"]["daytime_operator_can_redirect_between_items"])
        self.assertEqual(
            contract["final_report"]["outcome_buckets"], list(workblock.OUTCOME_BUCKETS)
        )
        self.assertIn("needs_input", contract["final_report"]["outcome_buckets"])

    def test_overnight_contract_reuses_the_same_primitive(self):
        contract = workblock.contract_as_dict(config=_config(), mode="overnight")
        self.assertEqual(contract["mode"], "overnight")
        self.assertTrue(contract["failure_policy"]["continue_after_blocked"])
        self.assertIn("work-block", contract["shared_with"])

    def test_rejects_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "daytime or overnight"):
            workblock.contract_as_dict(config=_config(), mode="other")


class TestDelegationPropagation(unittest.TestCase):
    """A block hands its staffing to every child ship, or it did not staff anything (#1017)."""

    def test_an_unstaffed_block_still_publishes_the_contract(self):
        delegation = workblock.contract_as_dict(config=_config(), mode="daytime")["delegation"]

        self.assertEqual(delegation["flags"], list(workblock.DELEGATION_FLAGS))
        self.assertTrue(delegation["propagate_to_every_child_ship"])
        self.assertTrue(delegation["record_effective_values_in_session_report"])
        self.assertEqual(delegation["child_args"], [])
        self.assertIsNone(delegation["effective"]["delegate"])

    def test_only_the_flags_the_operator_passed_are_handed_down(self):
        args = workblock.child_ship_args(
            delegate="codex:gpt-5",
            review_delegates=("agy", "", "claude"),
            effort="high",
            team_profile="night-shift",
            reviewer_override=2,
        )

        self.assertEqual(
            args,
            (
                "--delegate",
                "codex:gpt-5",
                "--review-delegate",
                "agy",
                "--review-delegate",
                "claude",
                "--effort",
                "high",
                "--team",
                "night-shift",
                "--reviewers",
                "2",
            ),
        )

    def test_the_order_is_fixed_because_the_line_is_quoted_into_the_report(self):
        """Two issues in one block must not read as two different teams having run."""
        kwargs = {"delegate": "codex", "effort": "low", "reviewer_override": 1}

        self.assertEqual(workblock.child_ship_args(**kwargs), workblock.child_ship_args(**kwargs))

    def test_the_effective_values_are_published_beside_the_child_args(self):
        delegation = workblock.delegation_as_dict(
            delegate="codex", review_delegates=("agy",), effort="high", team_profile="night"
        )

        self.assertEqual(
            delegation["effective"],
            {
                "delegate": "codex",
                "review_delegates": ["agy"],
                "effort": "high",
                "team": "night",
                "reviewers": None,
            },
        )
        self.assertIn("--team", delegation["child_args"])
        self.assertIn("/keel:ship", delegation["child_handoff_template"])

    def test_a_resolved_delegation_reaches_the_work_block_contract(self):
        contract = workblock.contract_as_dict(
            config=_config(),
            mode="overnight",
            delegation=workblock.delegation_as_dict(delegate="codex"),
        )

        self.assertEqual(contract["delegation"]["child_args"], ["--delegate", "codex"])
        self.assertTrue(contract["per_issue"]["child_inherits_team_assignment"])


if __name__ == "__main__":
    unittest.main()
