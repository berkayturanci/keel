"""Tests for the pure close-ordering reconciliation."""

import unittest

from keel import closeorder


def _record(*, action="merge", record_type="ship_run", issue=8):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": record_type,
        "issue": {"number": issue},
        "assessment": {"merge": {"action": action, "reason": "clear to merge"}},
    }
    return record


class TestRecordAttestsMerge(unittest.TestCase):
    def test_merge_action_attests(self):
        self.assertTrue(closeorder.record_attests_merge(_record(action="merge")))

    def test_defer_action_does_not_attest(self):
        self.assertFalse(closeorder.record_attests_merge(_record(action="defer")))

    def test_block_action_does_not_attest(self):
        self.assertFalse(closeorder.record_attests_merge(_record(action="block")))

    def test_none_record_does_not_attest(self):
        self.assertFalse(closeorder.record_attests_merge(None))

    def test_non_dict_record_does_not_attest(self):
        self.assertFalse(closeorder.record_attests_merge("nope"))

    def test_wrong_record_type_does_not_attest(self):
        self.assertFalse(
            closeorder.record_attests_merge(_record(record_type="capture"))
        )

    def test_missing_assessment_does_not_attest(self):
        self.assertFalse(closeorder.record_attests_merge({"record_type": "ship_run"}))

    def test_malformed_merge_block_does_not_attest(self):
        record = {"record_type": "ship_run", "assessment": {"merge": "nope"}}
        self.assertFalse(closeorder.record_attests_merge(record))


class TestLatestRecordForIssue(unittest.TestCase):
    def test_returns_last_matching_record(self):
        records = [
            _record(action="defer", issue=8),
            _record(action="merge", issue=8),
        ]
        latest = closeorder.latest_record_for_issue(records, 8)
        self.assertEqual(latest["assessment"]["merge"]["action"], "merge")

    def test_skips_non_dict_and_other_types(self):
        records = ["nope", {"record_type": "capture", "issue": {"number": 8}}]
        self.assertIsNone(closeorder.latest_record_for_issue(records, 8))

    def test_skips_malformed_issue_block(self):
        records = [{"record_type": "ship_run", "issue": "nope"}]
        self.assertIsNone(closeorder.latest_record_for_issue(records, 8))

    def test_no_match_returns_none(self):
        self.assertIsNone(
            closeorder.latest_record_for_issue([_record(issue=9)], 8)
        )


class TestReconcile(unittest.TestCase):
    def test_consistent_closed_with_merge_record(self):
        observed = [closeorder.ObservedIssue(
            number=8, closed=True, labels=("status:done",), record=_record(action="merge"),
        )]
        report = closeorder.reconcile(observed)
        self.assertEqual(report["verdict"], closeorder.VERDICT_OK)
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["issues"][0]["consistent"])
        self.assertTrue(report["issues"][0]["merge_attested"])

    def test_premature_close_without_merge_record(self):
        observed = [closeorder.ObservedIssue(
            number=8, closed=True, record=_record(action="defer"),
        )]
        report = closeorder.reconcile(observed)
        self.assertEqual(report["verdict"], closeorder.VERDICT_FLAGGED)
        self.assertFalse(report["ok"])
        self.assertEqual(
            [f["finding"] for f in report["findings"]],
            [closeorder.FINDING_PREMATURE_CLOSE],
        )
        self.assertIn("closed but no ship_run", report["findings"][0]["message"])

    def test_premature_status_done_without_merge_record(self):
        observed = [closeorder.ObservedIssue(
            number=8, labels=("status:done",), record=None,
        )]
        report = closeorder.reconcile(observed)
        self.assertEqual(report["verdict"], closeorder.VERDICT_FLAGGED)
        self.assertEqual(
            [f["finding"] for f in report["findings"]],
            [closeorder.FINDING_PREMATURE_STATUS_DONE],
        )
        self.assertIn("status:done", report["findings"][0]["message"])

    def test_closed_and_status_done_flags_both(self):
        observed = [closeorder.ObservedIssue(
            number=8, closed=True, labels=("status:done",), record=None,
        )]
        report = closeorder.reconcile(observed)
        self.assertEqual(
            sorted(f["finding"] for f in report["findings"]),
            [closeorder.FINDING_PREMATURE_CLOSE, closeorder.FINDING_PREMATURE_STATUS_DONE],
        )
        self.assertEqual(report["summary"]["flagged"], 1)
        self.assertEqual(report["summary"]["findings"], 2)

    def test_open_and_not_done_never_flagged(self):
        observed = [closeorder.ObservedIssue(number=8, closed=False, record=None)]
        report = closeorder.reconcile(observed)
        self.assertEqual(report["verdict"], closeorder.VERDICT_OK)
        self.assertEqual(report["findings"], [])

    def test_custom_done_label_respected(self):
        observed = [closeorder.ObservedIssue(
            number=8, labels=("Done",), record=None,
        )]
        report = closeorder.reconcile(observed, done_label="Done")
        self.assertEqual(report["verdict"], closeorder.VERDICT_FLAGGED)
        self.assertEqual(report["done_label"], "Done")

    def test_summary_counts_multiple_issues(self):
        observed = [
            closeorder.ObservedIssue(number=8, closed=True, record=_record(action="merge")),
            closeorder.ObservedIssue(number=9, closed=True, record=None),
        ]
        report = closeorder.reconcile(observed)
        self.assertEqual(report["summary"]["observed"], 2)
        self.assertEqual(report["summary"]["flagged"], 1)


if __name__ == "__main__":
    unittest.main()
