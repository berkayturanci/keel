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


if __name__ == "__main__":
    unittest.main()
