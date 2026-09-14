"""Tests for the pure capture-reconcile cross-check."""

import unittest

from keel import capture, captureverify


def _record(pr, *, marker=None, artifact=None, reviewers=None):
    capture_block = {"marker": marker}
    if artifact is not None:
        capture_block["artifact"] = artifact
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "pull_request": {"number": pr},
        "capture": capture_block,
    }
    if reviewers is not None:
        record["actors"] = {"reviewers": reviewers}
    return record


def _marker(pr, status):
    return f"compound-learning: pr={pr} status={status}"


class TestReconcile(unittest.TestCase):
    def test_clean_path_no_findings(self):
        records = [_record(7, marker=_marker(7, "applied"), artifact="artifacts/7.md")]

        report = captureverify.reconcile(records, [7])

        self.assertEqual(report["schema_version"], captureverify.RECONCILE_SCHEMA_VERSION)
        self.assertTrue(report["ok"])
        self.assertEqual(report["merged_prs"], [7])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["summary"]["checked"], 1)
        self.assertEqual(report["summary"]["findings"], 0)
        result = report["results"][0]
        self.assertTrue(result["ok"])
        self.assertEqual(result["marker_status"], "applied")
        self.assertEqual(result["artifact"], "artifacts/7.md")

    def test_missing_marker_is_a_finding(self):
        report = captureverify.reconcile([], [9])

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_MISSING_MARKER], 1)
        finding = report["findings"][0]
        self.assertEqual(finding["type"], captureverify.FINDING_MISSING_MARKER)
        self.assertEqual(finding["pr"], 9)
        self.assertIn("no capture marker", finding["reason"])

    def test_an_outside_sink_is_noted_not_faulted(self):
        """`applied-elsewhere` is a note: the absence is the sink's design (#1185).

        A sink outside the checkout records an absolute path, and the run ledger is
        committed — so that path travels to teammates and CI runners where it names
        nothing. Reporting it as `applied-without-artifact` accused a run that did exactly
        what it was configured to do, using the finding reserved for a file that is
        genuinely missing.
        """
        for artifact in ("/Users/b/knowledge/one.md", "~/knowledge/one.md"):
            with self.subTest(artifact=artifact):
                report = captureverify.reconcile(
                    [_record(5, marker=_marker(5, "applied"), artifact=artifact)], [5]
                )
                self.assertTrue(report["ok"], "a note must not fail the run")
                self.assertEqual(report["findings"], [])
                self.assertEqual(
                    [note["type"] for note in report["notes"]],
                    [captureverify.NOTE_APPLIED_ELSEWHERE],
                )
                self.assertEqual(report["summary"]["notes"], 1)

    def test_a_cross_host_duplicate_is_noted_not_faulted(self):
        """The record that has no artifact *because* the sink is elsewhere (#1185).

        A duplicate run on a host that cannot read the first run's file drops the path it
        would have reused — correctly — and records `applied` with none. Without the scope
        beside it that is indistinguishable from a capture that produced no file at all,
        and the run was faulted for the sink doing exactly what it was configured to do.
        """
        record = _record(5, marker=_marker(5, "applied"))
        record["capture"]["artifact_scope"] = capture.ARTIFACT_SCOPE_MACHINE
        report = captureverify.reconcile([record], [5])
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])
        self.assertEqual(
            [note["type"] for note in report["notes"]], [captureverify.NOTE_APPLIED_ELSEWHERE]
        )

    def test_a_row_that_is_not_applied_gets_no_note(self):
        """The note asserts a host wrote a file, so only an `applied` row can carry it.

        Attached on the recorded scope alone it appeared beside `invalid-marker` and on a
        `deferred` row — telling the operator a lesson was written somewhere they cannot
        see, for a run that wrote nothing at all.
        """
        record = _record(6, marker=_marker(6, "deferred"))
        record["capture"]["artifact_scope"] = capture.ARTIFACT_SCOPE_MACHINE
        report = captureverify.reconcile([record], [6])
        self.assertEqual(report["notes"], [])

    def test_an_in_repo_artifact_is_neither_faulted_nor_noted(self):
        report = captureverify.reconcile(
            [_record(5, marker=_marker(5, "applied"), artifact=".keel/learning/one.md")], [5]
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["notes"], [])

    def test_a_recorded_scope_outranks_the_paths_shape(self):
        # A record written by a project whose sink was in-repo keeps its meaning even if
        # the path looks anchored; the field describes the run that wrote it.
        record = _record(5, marker=_marker(5, "applied"), artifact="/srv/checkout/x.md")
        record["capture"]["artifact_scope"] = capture.ARTIFACT_SCOPE_REPOSITORY
        report = captureverify.reconcile([record], [5])
        self.assertEqual(report["notes"], [])

    def test_applied_without_artifact_is_a_finding(self):
        records = [_record(5, marker=_marker(5, "applied"))]

        report = captureverify.reconcile(records, [5])

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_APPLIED_WITHOUT_ARTIFACT], 1)
        finding = report["findings"][0]
        self.assertEqual(finding["type"], captureverify.FINDING_APPLIED_WITHOUT_ARTIFACT)
        self.assertEqual(finding["pr"], 5)

    def test_applied_with_blank_artifact_is_a_finding(self):
        records = [_record(5, marker=_marker(5, "applied"), artifact="   ")]

        report = captureverify.reconcile(records, [5])

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_APPLIED_WITHOUT_ARTIFACT], 1)

    def test_deferred_and_skipped_need_no_artifact(self):
        records = [
            _record(1, marker=_marker(1, "deferred")),
            _record(2, marker=_marker(2, "skipped:no-policy")),
        ]

        report = captureverify.reconcile(records, [1, 2])

        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])

    def test_reviewer_count_mismatch_is_a_finding(self):
        records = [
            _record(
                3,
                marker=_marker(3, "applied"),
                artifact="artifacts/3.md",
                reviewers=["agent-a", "agent-b"],
            )
        ]

        report = captureverify.reconcile(records, [3], verdict_counts={3: 1})

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_REVIEWER_COUNT_MISMATCH], 1)
        finding = report["findings"][0]
        self.assertEqual(finding["type"], captureverify.FINDING_REVIEWER_COUNT_MISMATCH)
        self.assertEqual(finding["recorded_reviewers"], 2)
        self.assertEqual(finding["posted_verdicts"], 1)

    def test_reviewer_count_equal_is_ok(self):
        records = [
            _record(
                3,
                marker=_marker(3, "applied"),
                artifact="artifacts/3.md",
                reviewers=["agent-a", "agent-b"],
            )
        ]

        report = captureverify.reconcile(records, [3], verdict_counts={3: 2})

        self.assertTrue(report["ok"])

    def test_verdict_counts_omitted_is_advisory(self):
        records = [
            _record(
                3,
                marker=_marker(3, "applied"),
                artifact="artifacts/3.md",
                reviewers=["agent-a", "agent-b"],
            )
        ]

        report = captureverify.reconcile(records, [3])

        self.assertTrue(report["ok"])
        self.assertIsNone(report["results"][0]["posted_verdicts"])

    def test_recorded_reviewers_ignores_non_string_and_blank(self):
        records = [
            _record(
                3,
                marker=_marker(3, "applied"),
                artifact="artifacts/3.md",
                reviewers=["agent-a", "", 7, "  "],
            )
        ]

        report = captureverify.reconcile(records, [3], verdict_counts={3: 1})

        self.assertTrue(report["ok"])
        self.assertEqual(report["results"][0]["recorded_reviewers"], 1)

    def test_helpers_tolerate_malformed_records(self):
        # No ledger record at all for the PR: artifact None, reviewers 0.
        report = captureverify.reconcile([], [4], verdict_counts={4: 0})

        result = report["results"][0]
        self.assertIsNone(result["artifact"])
        self.assertEqual(result["recorded_reviewers"], 0)

    def test_non_dict_capture_and_actors_are_tolerated(self):
        records = [
            {
                "record_type": "ship_run",
                "pull_request": {"number": 8},
                "capture": "not-a-dict",
                "actors": "not-a-dict",
            }
        ]

        report = captureverify.reconcile(records, [8], verdict_counts={8: 0})

        result = report["results"][0]
        self.assertIsNone(result["artifact"])
        self.assertEqual(result["recorded_reviewers"], 0)

    def test_capture_block_without_artifact_key(self):
        records = [
            {
                "record_type": "ship_run",
                "pull_request": {"number": 8},
                "capture": {"marker": _marker(8, "deferred")},
                "actors": {"reviewers": "not-a-list"},
            }
        ]

        report = captureverify.reconcile(records, [8])

        self.assertTrue(report["ok"])
        self.assertEqual(report["results"][0]["recorded_reviewers"], 0)

    def test_invalid_marker_is_a_finding(self):
        records = [_record(6, marker="invalid-marker-content")]

        report = captureverify.reconcile(records, [6])

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_INVALID_MARKER], 1)
        finding = report["findings"][0]
        self.assertEqual(finding["type"], captureverify.FINDING_INVALID_MARKER)
        self.assertEqual(finding["pr"], 6)
        self.assertIn("invalid capture marker", finding["reason"])

    def test_invalid_marker_with_mismatched_pr_is_a_finding(self):
        # Marker claims PR 999 while record is for PR 6
        records = [_record(6, marker=_marker(999, "applied"), artifact="artifacts/6.md")]

        report = captureverify.reconcile(records, [6])

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_INVALID_MARKER], 1)
        finding = report["findings"][0]
        self.assertEqual(finding["type"], captureverify.FINDING_INVALID_MARKER)
        self.assertEqual(finding["pr"], 6)

    def test_invalid_marker_fallback_reason(self):
        from unittest.mock import patch

        ret = {"ok": False, "status": "invalid", "reason": None}
        with patch("keel.capture._verify_pr", return_value=ret):
            report = captureverify.reconcile([], [6])
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"][captureverify.FINDING_INVALID_MARKER], 1)
        self.assertIn("invalid capture marker in the ledger", report["findings"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
