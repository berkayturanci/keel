"""Tests for operator consent contracts."""

import unittest
from datetime import UTC, datetime

from keel import consent


class TestConsentScopes(unittest.TestCase):
    def test_side_effects_map_to_mutation_scopes(self):
        scopes = consent.side_effect_scopes(
            ("git_branch", "pull_request", "capture", "secret_access", "check_runs")
        )
        self.assertEqual(scopes, ("filesystem", "git", "github", "secrets"))

    def test_unknown_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            consent.normalize_scopes(("filesystem,bogus",))


class TestConsentContract(unittest.TestCase):
    def test_dry_run_shows_live_required_scope_without_requiring_consent(self):
        contract = consent.build_consent_contract(
            command="ship",
            side_effects=("git_branch", "pull_request", "capture"),
            dry_run=True,
            target="issue #82",
        )
        self.assertFalse(contract["requires_operator_consent"])
        self.assertTrue(contract["would_require_operator_consent"])
        self.assertEqual(contract["status"], "not-required-dry-run")
        self.assertEqual(contract["consent_scope"], ["filesystem", "git", "github"])
        self.assertIsNone(contract["consent_record"])
        self.assertIn("live run would require", contract["consent_prompt"])

    def test_live_missing_consent_blocks(self):
        contract = consent.build_consent_contract(
            command="wrap",
            side_effects=("git_commit", "pull_request"),
            dry_run=False,
            approved_scopes=("git",),
        )
        ok, message = consent.assert_operator_consent(contract)
        self.assertFalse(ok)
        self.assertIn("github", contract["missing_scope"])
        self.assertIn("Missing approved scope: github", message)

    def test_live_approval_records_scope_without_secret_values(self):
        now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
        contract = consent.build_consent_contract(
            command="ship",
            side_effects=("git_branch", "pull_request"),
            dry_run=False,
            approved_scopes=("github,git",),
            operator="operator@example.com",
            target="issue #82",
            now=now,
        )
        self.assertFalse(contract["requires_operator_consent"])
        self.assertEqual(contract["status"], "approved")
        self.assertEqual(
            contract["delegated_agent_scope"]["approved_mutation_scopes"],
            ["git", "github"],
        )
        self.assertEqual(contract["consent_record"]["timestamp"], "2026-06-07T12:00:00Z")
        self.assertFalse(contract["consent_record"]["secret_values_recorded"])

    def test_read_only_effects_do_not_require_consent(self):
        contract = consent.build_consent_contract(
            command="ci-check",
            side_effects=("check_runs",),
            dry_run=False,
        )
        self.assertFalse(contract["requires_operator_consent"])
        self.assertFalse(contract["would_require_operator_consent"])
        self.assertEqual(contract["status"], "not-required-read-only")


if __name__ == "__main__":
    unittest.main()
