"""Tests for operator-consent requirements in packaged adapters."""

import unittest
from pathlib import Path

ADAPTERS = Path(__file__).resolve().parent.parent / "src" / "keel" / "adapters" / "commands"

MUTATING_COMMANDS = (
    "coverage",
    "deps-audit",
    "flake-audit",
    "implement",
    "morning",
    "overnight",
    "pr-loop",
    "regression",
    "review-all-day",
    "review-cycle",
    "ship",
    "stale-prs",
    "triage",
    "wrap",
)

DELEGATING_COMMANDS = (
    "implement",
    "overnight",
    "pr-loop",
    "regression",
    "review-all-day",
    "review-cycle",
    "ship",
    "triage",
)


class TestAdapterConsent(unittest.TestCase):
    def test_mutating_adapters_require_live_consent_preflight(self):
        for command in MUTATING_COMMANDS:
            with self.subTest(command=command):
                body = (ADAPTERS / f"{command}.md").read_text(encoding="utf-8")
                self.assertIn(f"--command {command} --live --json", body)
                self.assertIn("requires_operator_consent", body)
                self.assertIn("--approve-scope", body)

    def test_delegating_adapters_pass_delegated_agent_scope(self):
        for command in DELEGATING_COMMANDS:
            with self.subTest(command=command):
                body = (ADAPTERS / f"{command}.md").read_text(encoding="utf-8")
                self.assertIn("operator_consent.delegated_agent_scope", body)
                self.assertIn("approved_mutation_scopes", body)
                self.assertRegex(body.lower(), r"blocks\s+or\s+escalates")

    def test_triage_preserves_additive_mcp_label_writes(self):
        body = (ADAPTERS / "triage.md").read_text(encoding="utf-8")
        self.assertIn("MCP **overwrites** the label set", body)
        self.assertIn("union of existing + new labels", body)
        self.assertIn("added vs. preserved label difference", body)
        self.assertIn("never strip a pre-existing label", body)

    def test_stale_prs_preserves_same_day_comment_idempotency(self):
        body = (ADAPTERS / "stale-prs.md").read_text(encoding="utf-8")
        self.assertIn("literal first line", body)
        self.assertIn("STALE-PRS-<DATE>-<UTC_TIMESTAMP>", body)
        self.assertIn("starts with `STALE-PRS-<DATE>-`", body)
        self.assertIn("already commented today on #N", body)

    def test_stale_prs_keeps_legacy_merge_develop_alias_project_neutral(self):
        body = (ADAPTERS / "stale-prs.md").read_text(encoding="utf-8")
        self.assertIn("--merge-develop", body)
        self.assertIn("legacy alias for `--rebase`", body)
        self.assertIn("configured `base_branch`, not a hardcoded `develop`", body)


if __name__ == "__main__":
    unittest.main()
