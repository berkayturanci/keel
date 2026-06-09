"""Tests for the post-merge capture contract."""

import unittest

from keel import capture
from keel import config as cfg


def _config_with_capture_policy(policy):
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.8",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack={"name": "test", "capture": policy},
    )


class TestCaptureContract(unittest.TestCase):
    def test_contract_is_consumer_neutral(self):
        contract = capture.contract_as_dict()

        self.assertEqual(contract["schema_version"], "keel.capture.v1")
        self.assertEqual(contract["marker"]["prefix"], "compound-learning")
        self.assertIn("recursion-guard", contract["marker"]["skip_reasons"])
        self.assertEqual(contract["durable_artifacts"]["project_destination"],
                         "extension-owned")
        self.assertTrue(contract["fail_soft"]["enabled"])

    def test_contract_reads_project_capture_policy(self):
        contract = capture.contract_as_dict(
            _config_with_capture_policy({"enabled": True, "mode": "marker-only"})
        )

        self.assertTrue(contract["policy_enabled"])
        self.assertEqual(contract["policy_mode"], "marker-only")

    def test_marker_round_trip_for_applied(self):
        text = capture.marker_text(pr_number=167, status="applied")

        self.assertEqual(text, "compound-learning: pr=167 status=applied")
        parsed = capture.parse_marker(text)
        self.assertEqual(parsed.pr_number, 167)
        self.assertEqual(parsed.status, "applied")
        self.assertIsNone(parsed.reason)
        self.assertEqual(parsed.as_dict()["text"], text)

    def test_marker_round_trip_for_allowed_skip(self):
        text = capture.marker_text(
            pr_number=167,
            status="skipped",
            reason="no-policy",
        )

        self.assertEqual(text, "compound-learning: pr=167 status=skipped:no-policy")
        parsed = capture.parse_marker(text)
        self.assertEqual(parsed.status, "skipped")
        self.assertEqual(parsed.reason, "no-policy")

    def test_skipped_marker_requires_allowed_reason(self):
        with self.assertRaisesRegex(capture.CaptureError, "allowed skip reason"):
            capture.marker_text(pr_number=167, status="skipped", reason="custom")

    def test_marker_rejects_invalid_inputs(self):
        with self.assertRaisesRegex(capture.CaptureError, "positive PR number"):
            capture.marker_text(pr_number=0, status="applied")
        with self.assertRaisesRegex(capture.CaptureError, "status is required"):
            capture.normalize_status(None)
        with self.assertRaisesRegex(capture.CaptureError, "unsupported capture status"):
            capture.normalize_status("unknown")
        with self.assertRaisesRegex(capture.CaptureError, "invalid capture marker"):
            capture.parse_marker("not a marker")

    def test_verify_session_reports_complete(self):
        records = [
            {
                "schema_version": "keel.run-ledger.v1",
                "record_type": "ship_run",
                "pull_request": {"number": 167},
                "capture": {
                    "marker": "compound-learning: pr=167 status=skipped:no-policy",
                },
            }
        ]

        report = capture.verify_session(records, [167])

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["summary"]["ok"], 1)
        self.assertEqual(report["results"][0]["reason"], "no-policy")

    def test_verify_session_reports_missing_and_invalid(self):
        records = [
            {
                "schema_version": "keel.run-ledger.v1",
                "record_type": "ship_run",
                "pull_request": {"number": 168},
                "capture": {
                    "marker": "compound-learning: pr=999 status=applied",
                },
            }
        ]

        report = capture.verify_session(records, [167, 168])

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["summary"], {"ok": 0, "missing": 1, "invalid": 1})
        self.assertEqual(report["results"][0]["status"], "missing")
        self.assertEqual(report["results"][1]["status"], "invalid")

    def test_verify_session_reports_malformed_marker(self):
        records = [
            {
                "schema_version": "keel.run-ledger.v1",
                "record_type": "ship_run",
                "pull_request": {"number": 168},
                "capture": {"marker": "not a marker"},
            }
        ]

        report = capture.verify_session(records, [168])

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["results"][0]["status"], "invalid")
        self.assertEqual(report["results"][0]["reason"], "invalid capture marker")

    def test_verify_session_rejects_duplicate_markers(self):
        records = [
            {
                "schema_version": "keel.run-ledger.v1",
                "record_type": "ship_run",
                "pull_request": {"number": 168},
                "capture": {"marker": "compound-learning: pr=168 status=applied"},
            },
            {
                "schema_version": "keel.run-ledger.v1",
                "record_type": "ship_run",
                "pull_request": {"number": 168},
                "capture": {"marker": "compound-learning: pr=168 status=skipped:no-policy"},
            },
        ]

        report = capture.verify_session(records, [168])

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["summary"]["invalid"], 1)
        self.assertEqual(report["results"][0]["status"], "invalid")
        self.assertEqual(report["results"][0]["marker_count"], 2)

    def test_verify_session_ignores_records_without_marker(self):
        records = [
            {
                "schema_version": "keel.run-ledger.v1",
                "record_type": "ship_run",
                "pull_request": {"number": 168},
                "capture": {"marker": None},
            }
        ]

        report = capture.verify_session(records, [168])

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["results"][0]["status"], "missing")

    def test_record_marker_preserves_free_text_but_uses_closed_marker_reason(self):
        record = capture.record_marker(
            pr_number=170,
            status="skipped",
            reason="dry-run",
        )

        self.assertEqual(record["reason"], "dry-run")
        self.assertEqual(record["marker_reason"], "dry-run")
        self.assertEqual(record["marker"], "compound-learning: pr=170 status=skipped:dry-run")

    def test_record_marker_without_pr_does_not_create_marker(self):
        record = capture.record_marker(
            pr_number=None,
            status="skipped:dry-run",
            reason="dry run assessment",
        )

        self.assertEqual(record["status"], "skipped")
        self.assertEqual(record["marker_reason"], "dry-run")
        self.assertIsNone(record["marker"])

    def test_recursion_guard_detects_capture_work(self):
        self.assertTrue(capture.recursion_guard(title="Add capture contract"))
        self.assertTrue(capture.recursion_guard(labels=["capture"]))
        self.assertTrue(capture.recursion_guard(changed_files=["src/keel/capture.py"]))
        self.assertFalse(capture.recursion_guard(title="Add review contract"))
