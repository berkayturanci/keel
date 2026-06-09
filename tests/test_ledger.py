"""Tests for the structured run ledger."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from keel import config as cfg
from keel import ledger


def _config(*, run_ledger: str | None = None) -> cfg.ProjectConfig:
    reports = {"run_ledger": run_ledger} if run_ledger is not None else {}
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.7",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack={"name": "test", "reports": reports},
    )


def _record() -> dict:
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

    def test_encode_parse_append_and_read_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".keel" / "state" / "run-ledger.jsonl"
            self.assertEqual(ledger.read_records(path), [])

            record = _record()
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
