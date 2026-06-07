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

    def test_coverage_preserves_comment_and_label_idempotency(self):
        body = (ADAPTERS / "coverage.md").read_text(encoding="utf-8")
        for required in (
            "COVERAGE-<PR>-<UTC_TIMESTAMP>",
            "COVERAGE-<PR>-` prefix",
            "update it in place",
            "do **not** post a second comment",
            "coverage-regression",
            "idempotently ensure and add",
            "remove\nit if a prior run added it",
            "tooling not wired up",
            "Fail loudly on a coverage build/test failure",
        ):
            self.assertIn(required, body)

    def test_deps_audit_preserves_tracking_and_skip_semantics(self):
        body = (ADAPTERS / "deps-audit.md").read_text(encoding="utf-8")
        for required in (
            "deps-audit: <DATE>",
            "DEPS-AUDIT-<DATE>-<UTC_TIMESTAMP>",
            "append a fresh comment",
            "Skipped",
            "Per-audit failure continues with the others",
            "--severity <level>",
            "--security-only",
            "Never auto-apply upgrades",
            "route nothing",
        ):
            self.assertIn(required, body)

    def test_flake_audit_preserves_classification_and_dedupe_semantics(self):
        body = (ADAPTERS / "flake-audit.md").read_text(encoding="utf-8")
        for required in (
            "across-runs disagreement",
            "consistently fails",
            "fail_count >= 3",
            "flaky test: <fully.qualified.name>",
            "degraded run-level mode",
            "Limitations",
            "No silent dry-run mutations",
            "do NOT auto-disable",
            "Never close/edit/comment on existing flake issues",
        ):
            self.assertIn(required, body)


if __name__ == "__main__":
    unittest.main()
