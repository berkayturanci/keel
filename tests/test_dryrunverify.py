"""Tests for the pure post-hoc dry-run integrity reconciliation."""

import unittest

from keel import dryrunverify as drv


def _snap(*, run_ids=(), branches=(), prs=()):
    return drv.ArtifactSnapshot(
        ledger_run_ids=tuple(run_ids),
        branches=tuple(branches),
        pr_numbers=tuple(prs),
    )


class TestIssueBranchPattern(unittest.TestCase):
    def test_matches_typed_issue_branch(self):
        pattern = drv.issue_branch_pattern(8)
        self.assertTrue(pattern.search("feature/issue-8"))
        self.assertTrue(pattern.search("fix/issue-8-some-slug"))

    def test_does_not_match_other_issue(self):
        pattern = drv.issue_branch_pattern(8)
        self.assertFalse(pattern.search("feature/issue-80"))
        self.assertFalse(pattern.search("feature/issue-9"))

    def test_does_not_match_unknown_type(self):
        self.assertFalse(drv.issue_branch_pattern(8).search("wip/issue-8"))


class TestReconcileClean(unittest.TestCase):
    def test_identical_snapshots_are_clean(self):
        before = _snap(run_ids=["r1"], branches=["main"], prs=[1])
        after = _snap(run_ids=["r1"], branches=["main"], prs=[1])
        report = drv.reconcile(before, after, run_id="dry", issue=8)
        self.assertEqual(report["verdict"], drv.VERDICT_CLEAN)
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])

    def test_new_unrelated_artifacts_are_not_flagged(self):
        # A new ledger record with a DIFFERENT run_id and a branch for a
        # different issue must not be attributed to this dry run.
        before = _snap(run_ids=["r1"], branches=["main"])
        after = _snap(run_ids=["r1", "other-run"], branches=["main", "feature/issue-9"])
        report = drv.reconcile(before, after, run_id="dry", issue=8)
        self.assertEqual(report["verdict"], drv.VERDICT_CLEAN)
        self.assertEqual(report["findings"], [])
        # The diff still surfaces what was added, for transparency.
        self.assertEqual(report["added"]["ledger_run_ids"], ["other-run"])
        self.assertEqual(report["added"]["branches"], ["feature/issue-9"])


class TestReconcileViolated(unittest.TestCase):
    def test_new_ledger_record_for_run_is_flagged(self):
        before = _snap(run_ids=["r1"])
        after = _snap(run_ids=["r1", "dry"])
        report = drv.reconcile(before, after, run_id="dry", issue=8)
        self.assertEqual(report["verdict"], drv.VERDICT_VIOLATED)
        self.assertFalse(report["ok"])
        self.assertEqual(report["findings"][0]["finding"], drv.FINDING_NEW_LEDGER_RECORD)
        self.assertIn("append no record", report["findings"][0]["message"])

    def test_new_branch_for_issue_is_flagged(self):
        before = _snap(branches=["main"])
        after = _snap(branches=["main", "feature/issue-8"])
        report = drv.reconcile(before, after, run_id="dry", issue=8)
        self.assertEqual(report["verdict"], drv.VERDICT_VIOLATED)
        self.assertEqual(report["findings"][0]["finding"], drv.FINDING_NEW_BRANCH)
        self.assertEqual(report["findings"][0]["artifact"], "feature/issue-8")

    def test_new_pr_is_flagged(self):
        before = _snap(prs=[1])
        after = _snap(prs=[1, 42])
        report = drv.reconcile(before, after, run_id="dry", issue=8)
        self.assertEqual(report["findings"][0]["finding"], drv.FINDING_NEW_PR)
        self.assertEqual(report["findings"][0]["artifact"], 42)
        self.assertIn("open no PR", report["findings"][0]["message"])

    def test_all_three_leaks_flagged_together(self):
        before = _snap(run_ids=["r1"], branches=["main"], prs=[1])
        after = _snap(
            run_ids=["r1", "dry"],
            branches=["main", "feature/issue-8"],
            prs=[1, 42],
        )
        report = drv.reconcile(before, after, run_id="dry", issue=8)
        kinds = sorted(f["finding"] for f in report["findings"])
        self.assertEqual(kinds, ["new-branch", "new-ledger-record", "new-pr"])
        self.assertEqual(report["summary"]["new_ledger_records"], 1)
        self.assertEqual(report["summary"]["new_branches"], 1)
        self.assertEqual(report["summary"]["new_prs"], 1)
        self.assertEqual(report["summary"]["findings"], 3)


class TestAddedHelper(unittest.TestCase):
    def test_added_preserves_order_and_dedupes(self):
        self.assertEqual(
            drv._added(("b", "c", "c", "d"), ("a", "b")),
            ["c", "d"],
        )


if __name__ == "__main__":
    unittest.main()
