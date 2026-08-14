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


def _config_with_learning_policy(policy):
    return _config_with_capture_policy({"learning": policy})


def _record(pr, *, issue=None, marker=None):
    return {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "issue": {"number": issue},
        "pull_request": {"number": pr},
        "capture": {"marker": marker},
    }


class TestCaptureContract(unittest.TestCase):
    def test_contract_is_consumer_neutral(self):
        contract = capture.contract_as_dict()

        self.assertEqual(contract["schema_version"], "keel.capture.v1")
        self.assertEqual(contract["marker"]["prefix"], "compound-learning")
        self.assertIn("recursion-guard", contract["marker"]["skip_reasons"])
        self.assertEqual(contract["durable_artifacts"]["project_destination"],
                         "extension-owned")
        self.assertTrue(contract["fail_soft"]["enabled"])
        self.assertEqual(contract["reconcile"]["primitive"], "capture.reconcile_session")
        self.assertTrue(contract["reconcile"]["idempotent"])

    def test_contract_reads_project_capture_policy(self):
        contract = capture.contract_as_dict(
            _config_with_capture_policy({"enabled": True, "mode": "marker-only"})
        )

        self.assertTrue(contract["policy_enabled"])
        self.assertEqual(contract["policy_mode"], "marker-only")

    def test_contract_exposes_learning_quality_policy(self):
        contract = capture.contract_as_dict(
            _config_with_learning_policy({"enabled": True, "mode": "create-learning"})
        )

        learning = contract["learning_quality"]
        self.assertEqual(learning["schema_version"], "keel.capture-learning.v1")
        self.assertEqual(learning["decisions"], [
            "create-learning",
            "marker-only",
            "defer",
            "duplicate",
        ])
        self.assertTrue(learning["policy_enabled"])
        self.assertEqual(learning["policy_mode"], "create-learning")
        self.assertTrue(learning["marker_required_for_every_merge"])
        self.assertTrue(learning["durable_learning_optional"])

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

    def test_record_marker_without_status_still_records_learning_decision(self):
        record = capture.record_marker(
            pr_number=170,
            status=None,
            reason="not captured yet",
        )

        self.assertIsNone(record["status"])
        self.assertIsNone(record["marker"])
        self.assertEqual(record["learning"]["decision"], "marker-only")
        self.assertEqual(record["learning"]["reason"], "policy-unavailable")

    def test_learning_quality_policy_unavailable_records_marker_only(self):
        record = capture.record_marker(
            pr_number=170,
            status="applied",
            changed_files=["src/keel/capture.py"],
        )

        self.assertEqual(record["marker"], "compound-learning: pr=170 status=applied")
        self.assertEqual(record["learning"]["decision"], "marker-only")
        self.assertEqual(record["learning"]["reason"], "policy-unavailable")
        self.assertFalse(record["learning"]["durable_artifact"])

    def test_learning_quality_policy_can_create_learning(self):
        decision = capture.learning_decision(
            title="Fix recurring CI release failure",
            labels=["enhancement"],
            changed_files=["src/keel/release.py"],
            capture_status="applied",
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "create-learning",
                "reason": "new release invariant",
            }),
        )

        self.assertEqual(decision["decision"], "create-learning")
        self.assertEqual(decision["reason"], "new release invariant")
        self.assertTrue(decision["durable_artifact"])

    def test_learning_quality_create_policy_is_marker_only_when_capture_skipped(self):
        decision = capture.learning_decision(
            title="Capture skipped by policy",
            changed_files=["src/keel/capture.py"],
            capture_status="skipped:no-policy",
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "create-learning",
            }),
        )

        self.assertEqual(decision["decision"], "marker-only")
        self.assertEqual(decision["reason"], "capture-skipped")
        self.assertFalse(decision["durable_artifact"])

    def test_learning_quality_policy_can_defer(self):
        decision = capture.learning_decision(
            title="Needs human synthesis",
            changed_files=["docs/keel/release.md"],
            capture_status="applied",
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "defer",
            }),
        )

        self.assertEqual(decision["decision"], "defer")
        self.assertEqual(decision["reason"], "policy-deferred")
        self.assertFalse(decision["durable_artifact"])

    def test_learning_quality_defer_does_not_use_raw_capture_reason(self):
        decision = capture.learning_decision(
            title="Needs human synthesis",
            changed_files=["docs/keel/release.md"],
            capture_status="deferred",
            capture_reason="operator-specific temporary note",
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "defer",
            }),
        )

        self.assertEqual(decision["decision"], "defer")
        self.assertEqual(decision["reason"], "policy-deferred")

    def test_learning_quality_marker_only_policy(self):
        decision = capture.learning_decision(
            title="Routine generated adapter sync",
            changed_files=[".claude/commands/keel/ship.md"],
            capture_status="applied",
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "marker-only",
                "reason": "routine generated sync",
            }),
        )

        self.assertEqual(decision["decision"], "marker-only")
        self.assertEqual(decision["reason"], "routine generated sync")

    def test_learning_quality_detects_duplicate_fingerprint(self):
        fingerprint = capture.learning_fingerprint(
            title="Release invariant",
            labels=["enhancement"],
            changed_files=["src/keel/release.py"],
        )
        existing = [{
            "run_id": "RUN-1",
            "pull_request": {"number": 1},
            "capture": {
                "learning": {
                    "decision": "create-learning",
                    "fingerprint": fingerprint,
                },
            },
        }]

        decision = capture.learning_decision(
            title="  release   invariant ",
            labels=["enhancement"],
            changed_files=["SRC\\KEEL\\RELEASE.PY"],
            capture_status="applied",
            existing_records=existing,
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "create-learning",
            }),
        )

        self.assertEqual(decision["decision"], "duplicate")
        self.assertEqual(decision["reason"], "duplicate-learning")
        self.assertEqual(decision["duplicate_of"], "RUN-1")
        self.assertFalse(decision["durable_artifact"])

    def test_learning_quality_can_disable_dedupe(self):
        fingerprint = capture.learning_fingerprint(
            title="Release invariant",
            labels=["enhancement"],
            changed_files=["src/keel/release.py"],
        )
        existing = [{
            "run_id": "RUN-1",
            "capture": {
                "learning": {
                    "decision": "create-learning",
                    "fingerprint": fingerprint,
                },
            },
        }]

        decision = capture.learning_decision(
            title="Release invariant",
            labels=["enhancement"],
            changed_files=["src/keel/release.py"],
            capture_status="applied",
            existing_records=existing,
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "create-learning",
                "dedupe": {"enabled": False},
            }),
        )

        self.assertEqual(decision["decision"], "create-learning")
        self.assertNotIn("duplicate_of", decision)

    def test_learning_quality_duplicate_scan_ignores_irrelevant_records(self):
        fingerprint = capture.learning_fingerprint(
            title="Release invariant",
            labels=["enhancement"],
            changed_files=["src/keel/release.py"],
        )
        existing = [
            "not a record",
            {"capture": "not a capture block"},
            {"capture": {}},
            {
                "capture": {
                    "learning": {
                        "decision": "create-learning",
                        "fingerprint": "different",
                    },
                },
            },
            {
                "capture": {
                    "learning": {
                        "decision": "marker-only",
                        "fingerprint": fingerprint,
                    },
                },
            },
            {
                "pull_request": {"number": 12},
                "capture": {
                    "learning": {
                        "decision": "duplicate",
                        "fingerprint": fingerprint,
                    },
                },
            },
        ]

        decision = capture.learning_decision(
            title="Release invariant",
            labels=["enhancement"],
            changed_files=["src/keel/release.py"],
            capture_status="applied",
            existing_records=existing,
            config=_config_with_learning_policy({
                "enabled": True,
                "mode": "create-learning",
            }),
        )

        self.assertEqual(decision["decision"], "duplicate")
        self.assertEqual(decision["duplicate_of"], "12")

    def test_learning_result_rejects_unknown_decision(self):
        with self.assertRaisesRegex(capture.CaptureError, "unsupported learning decision"):
            capture._learning_result(  # noqa: SLF001 - exercising validation guard.
                "unknown",
                reason="test",
                fingerprint="abc123",
                policy={},
            )

    def test_recursion_guard_detects_capture_work(self):
        self.assertTrue(capture.recursion_guard(title="Add capture contract"))
        self.assertTrue(capture.recursion_guard(labels=["capture"]))
        self.assertTrue(capture.recursion_guard(changed_files=["src/keel/capture.py"]))
        self.assertFalse(capture.recursion_guard(title="Add review contract"))

    def test_reconcile_reports_already_complete_without_actions(self):
        plan = capture.reconcile_session(
            [_record(170, marker="compound-learning: pr=170 status=applied")],
            [170],
        )

        self.assertEqual(plan["status"], "complete")
        self.assertEqual(plan["summary"], {"complete": 1, "actionable": 0, "blocked": 0})
        self.assertEqual(plan["results"][0]["actions"], [])

    def test_reconcile_valid_marker_can_still_close_unambiguous_issue(self):
        plan = capture.reconcile_session(
            [_record(170, marker="compound-learning: pr=170 status=applied")],
            [{"number": 170, "issue_numbers": [45]}],
        )
        result = plan["results"][0]

        self.assertEqual(plan["status"], "actionable")
        self.assertEqual(result["marker"], "compound-learning: pr=170 status=applied")
        self.assertEqual(result["issue_numbers"], [45])
        self.assertEqual(result["actions"], [{
            "type": "close-linked-issue",
            "pr": 170,
            "idempotency_key": "close-linked-issue:issue-45:pr-170",
            "issue": 45,
        }])

    def test_reconcile_valid_marker_blocks_ambiguous_issue_closeout(self):
        plan = capture.reconcile_session(
            [_record(170, marker="compound-learning: pr=170 status=applied")],
            [{"number": 170, "issue_numbers": [45, 46]}],
        )

        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["results"][0]["status"], "ambiguous")
        self.assertEqual(plan["results"][0]["actions"], [])

    def test_reconcile_missing_marker_records_no_policy_skip_and_issue_close(self):
        plan = capture.reconcile_session([_record(170, issue=45)], [170])
        result = plan["results"][0]

        self.assertEqual(plan["status"], "actionable")
        self.assertEqual(result["marker"], "compound-learning: pr=170 status=skipped:no-policy")
        self.assertEqual([action["type"] for action in result["actions"]], [
            "emit-capture-marker",
            "post-closure-summary",
            "record-skip",
            "close-linked-issue",
        ])
        self.assertEqual(result["actions"][-1]["issue"], 45)

    def test_reconcile_records_capability_unavailable_when_extension_policy_exists(self):
        plan = capture.reconcile_session(
            [_record(999, issue=1), {"record_type": "other"}, _record(172, issue=2)],
            [171],
            config=_config_with_capture_policy({"enabled": True, "mode": "extension"}),
            capture_capability_available=False,
        )

        result = plan["results"][0]
        self.assertIn("capability-unavailable", result["marker"])
        self.assertEqual(result["actions"][2]["reason"], "capability-unavailable")
        self.assertEqual([action["type"] for action in result["actions"]], [
            "emit-capture-marker",
            "post-closure-summary",
            "record-skip",
        ])

    def test_reconcile_marker_only_policy_records_applied_marker(self):
        plan = capture.reconcile_session(
            [_record(171, issue=46)],
            [171],
            config=_config_with_capture_policy({"enabled": True, "mode": "marker-only"}),
            capture_capability_available=False,
        )

        result = plan["results"][0]
        self.assertEqual(result["marker"], "compound-learning: pr=171 status=applied")
        self.assertEqual(result["reason"], "marker-only capture policy configured")
        self.assertEqual([action["type"] for action in result["actions"]], [
            "emit-capture-marker",
            "post-closure-summary",
            "close-linked-issue",
        ])

    def test_reconcile_defers_to_capture_extension_when_capability_available(self):
        plan = capture.reconcile_session(
            [_record(172)],
            [{
                "number": 172,
                "labels": ["enhancement"],
                "changed_files": ["src/keel/runner.py"],
                "issue_numbers": [50],
            }],
            config=_config_with_capture_policy({"enabled": True, "mode": "extension"}),
            capture_capability_available=True,
        )

        result = plan["results"][0]
        self.assertEqual(result["marker"], "compound-learning: pr=172 status=deferred")
        self.assertEqual(result["actions"][0]["type"], "run-capture-extension")
        self.assertEqual(result["actions"][1]["type"], "emit-capture-marker")
        self.assertEqual(result["actions"][-1]["type"], "close-linked-issue")

    def test_reconcile_respects_recursion_guard(self):
        plan = capture.reconcile_session(
            [_record(173)],
            [{
                "number": 173,
                "title": "Add capture reconcile",
                "labels": "capture",
                "changed_files": "src/keel/capture.py",
                "issue_numbers": "47",
            }],
            config=_config_with_capture_policy({"enabled": True, "mode": "extension"}),
            capture_capability_available=True,
        )

        result = plan["results"][0]
        self.assertIn("skipped:recursion-guard", result["marker"])
        self.assertEqual(result["reason"], "capture recursion guard matched")
        self.assertEqual(result["issue_numbers"], [])

    def test_reconcile_blocks_ambiguous_link_and_invalid_markers(self):
        ambiguous = capture.reconcile_session(
            [_record(174, issue=48), _record(174, issue=49)],
            [174],
        )
        invalid = capture.reconcile_session(
            [_record(175, marker="not a marker")],
            [175],
        )

        self.assertEqual(ambiguous["status"], "blocked")
        self.assertEqual(ambiguous["results"][0]["status"], "ambiguous")
        self.assertEqual(ambiguous["results"][0]["actions"], [])
        self.assertEqual(invalid["status"], "blocked")
        self.assertEqual(invalid["results"][0]["status"], "invalid")

    def test_reconcile_rejects_invalid_pr_info(self):
        with self.assertRaisesRegex(capture.CaptureError, "positive number"):
            capture.reconcile_session([], [{"number": 0}])


class TestRetrieveRelevantLearnings(unittest.TestCase):
    def test_missing_or_empty_dir_returns_empty(self):
        self.assertEqual(capture.retrieve_relevant_learnings("auth token", "/nonexistent/dir"), [])

    def test_empty_tokens_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(capture.retrieve_relevant_learnings("the and for", td), [])

    def test_retrieves_relevant_learning_records(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "auth_tokens.md").write_text(
                "# Auth Token Handling\nAlways refresh expired bearer tokens."
            )
            (p / "database_lock.md").write_text(
                "# SQLite Locking\nDo not hold write locks across network calls."
            )
            (p / "ignored.bin").write_bytes(b"\x00\x01\x02")

            results = capture.retrieve_relevant_learnings("fix auth token expiry", td)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["file"], "auth_tokens.md")
            self.assertEqual(results[0]["title"], "Auth Token Handling")
            self.assertEqual(results[0]["summary"], "Always refresh expired bearer tokens.")
            self.assertGreater(results[0]["score"], 0)

    def test_unreadable_file_is_skipped_fail_soft(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "lesson.md").write_text("# Lesson\nSome content")
            with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
                self.assertEqual(capture.retrieve_relevant_learnings("lesson content", td), [])


if __name__ == "__main__":
    unittest.main()
