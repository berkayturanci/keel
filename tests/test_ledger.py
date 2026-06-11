"""Tests for the structured run ledger."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from keel import config as cfg
from keel import ledger, redaction


def _config(
    *,
    run_ledger: str | None = None,
    deny_patterns: list[dict] | None = None,
    learning_policy: dict | None = None,
) -> cfg.ProjectConfig:
    reports = {"run_ledger": run_ledger} if run_ledger is not None else {}
    policy_pack = {"name": "test", "reports": reports}
    if deny_patterns is not None:
        policy_pack["capture_redaction"] = {"deny_patterns": deny_patterns}
    if learning_policy is not None:
        policy_pack["capture"] = {"learning": learning_policy}
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.7",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack=policy_pack,
    )


def _record(*, config: cfg.ProjectConfig | None = None) -> dict:
    outcome = SimpleNamespace(gate="build", ok=True, skipped=False, error=None, findings=[])
    verdict = SimpleNamespace(blocked=False, counts={"blocker": 0})
    merge = SimpleNamespace(action="merge", reason="all gates passed")
    assessment = SimpleNamespace(
        tier=2,
        reviewers=2,
        window_open=True,
        ci_ok=None,
        merge=merge,
        halted=False,
        bypassed_window=False,
    )
    return ledger.build_ship_run_record(
        command="ship",
        base_branch="main",
        changed_files=["src/keel/ledger.py"],
        outcomes=[outcome],
        verdict=verdict,
        assessment=assessment,
        issue_intake={"status": "ready"},
        target="issue #140",
        run_id="RUN-140",
        issue_number=140,
        pr_number=160,
        branch="feat/issue-140-run-ledger",
        head_sha="abc123",
        capture_status="applied",
        capture_reason="capture hook completed",
        config=config,
        implementer="codex:gpt-5",
        reviewer_agents=["reviewer-a:gpt-5", "reviewer-b:claude"],
        tester="tester:gpt-5-mini",
    )


class TestLedgerContract(unittest.TestCase):
    def test_default_contract_is_consumer_neutral(self):
        contract = ledger.ledger_contract_as_dict(_config())

        self.assertEqual(contract["schema_version"], ledger.LEDGER_SCHEMA_VERSION)
        self.assertEqual(contract["format"], "jsonl")
        self.assertEqual(contract["path"], ".keel/state/run-ledger.jsonl")
        self.assertEqual(contract["path_source"], "default")
        self.assertTrue(contract["consumer_neutral"])
        self.assertTrue(contract["capture_redaction"]["default_redaction"])
        self.assertFalse(contract["capture_redaction"]["audit_includes_original_values"])
        self.assertEqual(contract["capture_contract"]["schema_version"], "keel.capture.v1")
        self.assertTrue(contract["capture_contract"]["fail_soft"]["enabled"])
        self.assertEqual(contract["capture_health"]["schema_version"],
                         "keel.capture-health.v1")
        self.assertTrue(contract["capture_health"]["consumer_neutral"])
        self.assertIn("ship", contract["append_owner"])
        self.assertIn("morning", contract["readers"])

    def test_reports_override_changes_path_only(self):
        config = _config(run_ledger="state/runs.jsonl")

        self.assertEqual(
            ledger.ledger_contract_as_dict(config)["path_source"],
            "policy_pack.reports.run_ledger",
        )
        self.assertEqual(str(ledger.resolve_path("/repo", config)), "/repo/state/runs.jsonl")


class TestLedgerRecords(unittest.TestCase):
    def test_ship_run_record_schema_is_stable(self):
        record = _record()

        self.assertEqual(list(record), [
            "schema_version",
            "record_type",
            "command",
            "run_id",
            "target",
            "issue",
            "pull_request",
            "git",
            "changes",
            "gates",
            "verdict",
            "assessment",
            "actors",
            "run_context",
            "issue_intake",
            "capture",
        ])
        self.assertEqual(record["record_type"], "ship_run")
        self.assertEqual(record["issue"]["number"], 140)
        self.assertEqual(record["changes"]["file_count"], 1)
        self.assertEqual(record["gates"][0]["finding_count"], 0)
        self.assertEqual(record["actors"]["implementer"], "codex:gpt-5")
        self.assertEqual(record["actors"]["reviewers"], ["reviewer-a:gpt-5", "reviewer-b:claude"])
        self.assertEqual(record["actors"]["tester"], "tester:gpt-5-mini")
        self.assertEqual(record["capture"]["schema_version"], "keel.capture.v1")
        self.assertEqual(record["capture"]["marker"],
                         "compound-learning: pr=160 status=applied")
        self.assertEqual(record["capture"]["learning"]["decision"], "marker-only")
        self.assertEqual(record["capture"]["learning"]["reason"], "policy-unavailable")
        self.assertTrue(record["capture"]["fail_soft"])

    def test_run_context_block_is_built_from_inputs(self):
        record = ledger.build_ship_run_record(
            command="ship",
            base_branch="main",
            changed_files=["src/keel/ledger.py"],
            outcomes=[],
            verdict=SimpleNamespace(blocked=False, counts={}),
            assessment=SimpleNamespace(
                tier=2, reviewers=2, window_open=True, ci_ok=None,
                merge=SimpleNamespace(action="merge", reason="ok"),
                halted=False, bypassed_window=False,
            ),
            host_agent="claude",
            transport="mcp",
            profile="compound",
            jury_mode="gating",
            consent_status="approved",
            consent_scopes=["pr-merge", "label"],
        )

        run_context = record["run_context"]
        self.assertEqual(run_context["host_agent"], "claude")
        self.assertEqual(run_context["transport"], "mcp")
        self.assertEqual(run_context["profile"], "compound")
        self.assertEqual(run_context["jury_mode"], "gating")
        self.assertEqual(run_context["consent"]["status"], "approved")
        self.assertEqual(run_context["consent"]["scopes"], ["pr-merge", "label"])

    def test_run_context_block_is_optional_and_degrades(self):
        # The default fixture passes none of the run-context inputs.
        run_context = _record()["run_context"]
        self.assertIsNone(run_context["host_agent"])
        self.assertIsNone(run_context["transport"])
        self.assertIsNone(run_context["profile"])
        self.assertIsNone(run_context["jury_mode"])
        self.assertIsNone(run_context["consent"]["status"])
        self.assertEqual(run_context["consent"]["scopes"], [])

    def test_run_context_blank_scalars_and_scopes_degrade(self):
        record = ledger.build_ship_run_record(
            command="ship",
            base_branch="main",
            changed_files=[],
            outcomes=[],
            verdict=SimpleNamespace(blocked=False, counts={}),
            assessment=SimpleNamespace(
                tier=1, reviewers=1, window_open=True, ci_ok=None,
                merge=SimpleNamespace(action="merge", reason="ok"),
                halted=False, bypassed_window=False,
            ),
            host_agent="  ",
            transport="",
            profile="   ",
            jury_mode="",
            consent_status="  ",
            consent_scopes=["pr-merge", "  ", ""],
        )

        run_context = record["run_context"]
        self.assertIsNone(run_context["host_agent"])
        self.assertIsNone(run_context["transport"])
        self.assertIsNone(run_context["profile"])
        self.assertIsNone(run_context["jury_mode"])
        self.assertIsNone(run_context["consent"]["status"])
        # Blank scope entries are dropped; non-blank ones are kept.
        self.assertEqual(run_context["consent"]["scopes"], ["pr-merge"])

    def test_ship_run_record_can_store_learning_create_decision(self):
        record = _record(
            config=_config(learning_policy={
                "enabled": True,
                "mode": "create-learning",
                "reason": "new invariant",
            })
        )

        learning = record["capture"]["learning"]
        self.assertEqual(learning["decision"], "create-learning")
        self.assertEqual(learning["reason"], "new invariant")
        self.assertTrue(learning["durable_artifact"])

    def test_capture_health_clean_session(self):
        record = _record(
            config=_config(learning_policy={
                "enabled": True,
                "mode": "create-learning",
            })
        )

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["schema_version"], "keel.capture-health.v1")
        self.assertEqual(summary["status"], "clean")
        self.assertEqual(summary["counts"]["applied"], 1)
        self.assertEqual(summary["counts"]["create_learning"], 1)
        self.assertEqual(summary["counts"]["needs_reconcile"], 0)
        self.assertEqual(summary["reconcile_actions"], [])
        self.assertTrue(summary["dry_run"]["no_mutations"])

    def test_capture_health_surfaces_missing_marker_distinctly(self):
        record = _record()
        record["capture"]["marker"] = None

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["status"], "needs-reconcile")
        self.assertEqual(summary["counts"]["missing_marker"], 1)
        self.assertEqual(summary["counts"]["skipped"], 0)
        self.assertEqual(summary["items"][0]["status"], "missing-marker")
        self.assertEqual(summary["items"][0]["reconcile_actions"][0]["type"],
                         "capture-reconcile")

    def test_capture_health_counts_skipped_by_allowed_reason(self):
        record = _record()
        record["capture"] = {
            "schema_version": "keel.capture.v1",
            "status": "skipped",
            "reason": "capture recursion guard matched",
            "marker_reason": "recursion-guard",
            "marker": "compound-learning: pr=160 status=skipped:recursion-guard",
            "learning": {
                "decision": "marker-only",
                "reason": "capture-skipped",
            },
        }

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["status"], "clean")
        self.assertEqual(summary["counts"]["skipped"], 1)
        self.assertEqual(summary["counts"]["marker_only"], 1)
        self.assertEqual(summary["skipped_by_reason"], {"recursion-guard": 1})

    def test_capture_health_deferred_capture_needs_reconcile(self):
        record = _record(
            config=_config(learning_policy={
                "enabled": True,
                "mode": "defer",
            })
        )
        record["capture"]["status"] = "deferred"
        record["capture"]["reason"] = "capture extension can be rerun"
        record["capture"]["marker"] = "compound-learning: pr=160 status=deferred"

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["status"], "needs-reconcile")
        self.assertEqual(summary["counts"]["deferred"], 1)
        self.assertEqual(summary["counts"]["needs_reconcile"], 1)
        self.assertEqual(summary["reconcile_actions"][0]["pr"], 160)
        self.assertIn("--merged-pr 160", summary["reconcile_actions"][0]["command"])

    def test_capture_health_counts_duplicate_learning(self):
        record = _record()
        record["capture"]["learning"] = {
            "decision": "duplicate",
            "reason": "duplicate-learning",
        }

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["counts"]["duplicate_learning"], 1)
        self.assertEqual(summary["status"], "clean")

    def test_capture_health_handles_malformed_capture_block_without_pr(self):
        record = _record()
        record["pull_request"] = {"number": None}
        record["capture"] = "not a capture block"

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["counts"]["missing_marker"], 1)
        self.assertEqual(summary["counts"]["marker_only"], 0)
        self.assertEqual(summary["reconcile_actions"][0]["pr"], None)
        self.assertNotIn("--merged-pr", summary["reconcile_actions"][0]["command"])

    def test_sanitize_record_redacts_default_secret_patterns(self):
        record = _record()
        record["capture"]["reason"] = (
            "Bearer abcdefghijklmnopqrstuvwxyz and "
            "https://user:supersecret@example.test/repo"
        )

        sanitized = ledger.sanitize_record(record, _config())

        serialized = json.dumps(sanitized, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("supersecret", serialized)
        self.assertIn("[REDACTED:bearer-token]", serialized)
        self.assertIn("[REDACTED:credentials]", serialized)
        self.assertEqual(sanitized["redaction"]["status"], "applied")
        self.assertEqual(sanitized["redaction"]["redaction_count"], 2)

    def test_sanitize_record_uses_project_deny_patterns(self):
        record = _record()
        record["capture"]["reason"] = "Private host: internal.example.test"
        config = _config(deny_patterns=[
            {"id": "private-host", "pattern": r"internal\.example\.test"}
        ])

        sanitized = ledger.sanitize_record(record, config)

        self.assertNotIn("internal.example.test", json.dumps(sanitized, sort_keys=True))
        self.assertIn("[REDACTED:private-host]", sanitized["capture"]["reason"])
        self.assertEqual(sanitized["redaction"]["rules"][0]["id"], "private-host")

    def test_sanitize_record_uses_project_custom_replacement(self):
        record = _record()
        record["capture"]["reason"] = "Ticket: https://tickets.example.test/ABC-123"
        config = _config(deny_patterns=[
            {
                "id": "ticket-url",
                "pattern": r"https://tickets\.example\.test/[A-Z]+-[0-9]+",
                "replacement": "[REDACTED:ticket-url]",
            }
        ])

        sanitized = ledger.sanitize_record(record, config)

        self.assertEqual(sanitized["capture"]["reason"], "Ticket: [REDACTED:ticket-url]")
        self.assertEqual(sanitized["redaction"]["rules"][0]["id"], "ticket-url")

    def test_project_replacement_is_literal_and_cannot_reinsert_secret(self):
        record = _record()
        record["capture"]["reason"] = "captured top-secret-value"
        config = _config(deny_patterns=[
            {"id": "bad-replacement", "pattern": r"top-secret-value", "replacement": r"\g<0>"}
        ])

        sanitized = ledger.sanitize_record(record, config)

        self.assertNotIn("top-secret-value", json.dumps(sanitized, sort_keys=True))
        self.assertEqual(sanitized["capture"]["reason"], r"captured \g<0>")

    def test_redaction_policy_without_config_uses_defaults(self):
        policy = redaction.policy_from_config()

        result = redaction.sanitize("ghp_" + "a" * 24, policy)

        self.assertEqual(result.value, "[REDACTED:github-token]")
        self.assertEqual(result.audit["redaction_count"], 1)

    def test_malformed_project_redaction_entries_are_ignored_until_valid_regex_compiles(self):
        record = _record()
        record["capture"]["reason"] = "x marks the value"
        config = _config(deny_patterns=[
            "not-a-rule",
            {"id": "missing-pattern"},
            {"pattern": "x"},
        ])

        contract = ledger.ledger_contract_as_dict(config)["capture_redaction"]
        sanitized = ledger.sanitize_record(record, config)

        self.assertEqual(contract["configured_rule_ids"],
                         ["missing-pattern", "deny-pattern-3"])
        self.assertEqual(
            sanitized["capture"]["reason"],
            "[REDACTED:deny-pattern-3] marks the value",
        )

    def test_non_list_project_redaction_policy_is_ignored(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.7",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            policy_pack={"name": "test", "capture_redaction": {"deny_patterns": "not-a-list"}},
        )

        contract = ledger.ledger_contract_as_dict(config)["capture_redaction"]
        sanitized = ledger.sanitize_record(_record(), config)

        self.assertEqual(contract["configured_rule_count"], 0)
        self.assertEqual(sanitized["redaction"]["redaction_count"], 0)

    def test_sanitize_record_leaves_safe_text_unchanged(self):
        record = _record()
        record["capture"]["reason"] = "Capture skipped because no hook is configured."

        sanitized = ledger.sanitize_record(record, _config())

        self.assertEqual(sanitized["capture"]["reason"], record["capture"]["reason"])
        self.assertEqual(sanitized["redaction"]["redaction_count"], 0)

    def test_encode_parse_append_and_read_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".keel" / "state" / "run-ledger.jsonl"
            self.assertEqual(ledger.read_records(path), [])

            record = _record()
            record = ledger.sanitize_record(record, _config())
            ledger.append_record(path, record)
            ledger.append_record(path, record)

            self.assertEqual(ledger.parse_records("\n" + ledger.encode_record(record)),
                             [record])
            self.assertEqual(ledger.read_records(path), [record, record])

    def test_parse_rejects_invalid_json_and_schema(self):
        with self.assertRaisesRegex(ledger.LedgerError, "invalid JSON"):
            ledger.parse_records("{")

        bad_schema = dict(_record(), schema_version="other")
        with self.assertRaisesRegex(ledger.LedgerError, "unsupported schema_version"):
            ledger.encode_record(bad_schema)

        bad_type = dict(_record(), record_type="other")
        with self.assertRaisesRegex(ledger.LedgerError, "unsupported record_type"):
            ledger.parse_records(ledger.encode_record(_record()) + "\n" + json.dumps(bad_type))

        with self.assertRaisesRegex(ledger.LedgerError, "record must be an object"):
            ledger.parse_records("[]")
