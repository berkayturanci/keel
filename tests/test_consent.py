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

    def test_unknown_side_effect_is_rejected(self):
        with self.assertRaises(ValueError):
            consent.side_effect_scopes(("typo_write",))

    def test_capabilities_map_to_side_effects(self):
        effects = consent.capability_side_effects(
            ("release-publish", "secret-access", "production-adjacent", "private-setup")
        )
        self.assertEqual(
            effects,
            ("release", "secret_access", "production_access", "credential_access"),
        )


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
        self.assertEqual(contract["consent_record"]["source"], "flag")
        self.assertFalse(contract["consent_record"]["secret_values_recorded"])

    def test_live_standing_approval_records_source(self):
        now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
        contract = consent.build_consent_contract(
            command="overnight",
            side_effects=("git_branch", "pull_request"),
            dry_run=False,
            approved_scopes=("git,github",),
            approval_source="env",
            operator="automation:nightly",
            target="nightly queue",
            now=now,
        )
        self.assertEqual(contract["status"], "approved")
        self.assertEqual(contract["approval_source"], "env")
        self.assertEqual(contract["consent_record"]["source"], "env")
        self.assertEqual(contract["consent_record"]["operator"], "automation:nightly")

    def test_unknown_approval_source_rejected(self):
        with self.assertRaises(ValueError):
            consent.build_consent_contract(
                command="ship",
                side_effects=("git_branch",),
                dry_run=False,
                approved_scopes=("git",),
                approval_source="cron",
            )

    def test_agent_mode_emits_contract_without_blocking(self):
        contract = consent.build_consent_contract(
            command="ship",
            side_effects=("git_branch", "pull_request"),
            dry_run=False,
            mode="agent",
        )
        self.assertFalse(contract["requires_operator_consent"])
        self.assertEqual(contract["status"], "agent-delegated")
        self.assertEqual(contract["mode"], "agent")
        self.assertEqual(contract["missing_scope"], ["git", "github"])

    def test_unknown_consent_mode_rejected(self):
        with self.assertRaises(ValueError):
            consent.build_consent_contract(
                command="ship",
                side_effects=("git_branch",),
                dry_run=False,
                mode="maybe",
            )

    def test_extra_approved_scope_is_not_delegated_without_planned_side_effect(self):
        contract = consent.build_consent_contract(
            command="ship",
            side_effects=("git_branch", "pull_request"),
            dry_run=False,
            approved_scopes=("git,github,secrets,release",),
            operator="operator",
        )
        self.assertEqual(contract["approved_scope"], ["git", "github", "secrets", "release"])
        self.assertEqual(contract["effective_approved_scope"], ["git", "github"])
        self.assertEqual(
            contract["delegated_agent_scope"]["approved_mutation_scopes"],
            ["git", "github"],
        )
        self.assertEqual(contract["consent_record"]["scopes_approved"], ["git", "github"])

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
