"""Unit tests for the deterministic ship decisions."""

import unittest

from keel import ship
from keel.findings import Finding, summarize

CLEAN = summarize([])
SOFT = summarize([Finding("minor", "x", "a"), Finding("nit", "y", "b")])
BLOCKED = summarize([Finding("major", "boom", "a")])


class TestReviewerCount(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(ship.reviewer_count(3), 3)
        self.assertEqual(ship.reviewer_count(2), 2)
        self.assertEqual(ship.reviewer_count(1), 1)

    def test_unknown_tier_defaults_to_two(self):
        self.assertEqual(ship.reviewer_count(0), 2)
        self.assertEqual(ship.reviewer_count(99), 2)


class TestDecideMerge(unittest.TestCase):
    def test_block_on_findings(self):
        d = ship.decide_merge(BLOCKED, window_open=True)
        self.assertEqual(d.action, "block")

    def test_findings_block_even_for_blocker(self):
        d = ship.decide_merge(BLOCKED, window_open=False, is_blocker=True)
        self.assertEqual(d.action, "block")

    def test_merge_when_clear_and_open(self):
        d = ship.decide_merge(SOFT, window_open=True)
        self.assertEqual(d.action, "merge")
        self.assertEqual(d.reason, "clear to merge")

    def test_defer_outside_window(self):
        d = ship.decide_merge(CLEAN, window_open=False)
        self.assertEqual(d.action, "defer")

    def test_blocker_bypasses_window(self):
        d = ship.decide_merge(CLEAN, window_open=False, is_blocker=True)
        self.assertEqual(d.action, "merge")
        self.assertIn("bypass", d.reason)


class TestFixLoop(unittest.TestCase):
    def test_runs_while_blocked_and_budget(self):
        self.assertTrue(ship.should_run_fixloop(BLOCKED, current_round=0))
        self.assertTrue(ship.should_run_fixloop(BLOCKED, current_round=2))

    def test_stops_at_cap(self):
        self.assertFalse(ship.should_run_fixloop(BLOCKED, current_round=3))

    def test_stops_when_clear(self):
        self.assertFalse(ship.should_run_fixloop(CLEAN, current_round=0))


class TestCiPassing(unittest.TestCase):
    def test_unknown(self):
        self.assertIsNone(ship.ci_passing(None))
        self.assertIsNone(ship.ci_passing(""))
        self.assertIsNone(ship.ci_passing("  ,  "))

    def test_passing(self):
        self.assertTrue(ship.ci_passing("SUCCESS"))
        self.assertTrue(ship.ci_passing("SUCCESS,NEUTRAL,SKIPPED"))
        self.assertTrue(ship.ci_passing("success"))

    def test_failing(self):
        self.assertFalse(ship.ci_passing("FAILURE"))
        self.assertFalse(ship.ci_passing("SUCCESS,FAILURE"))
        self.assertFalse(ship.ci_passing("TIMED_OUT"))


TIER3 = (".github/workflows/**",)
DOCS = ("docs/**", "*.md")


class TestAssess(unittest.TestCase):
    def test_tier3_three_reviewers_and_merge(self):
        a = ship.assess(changed_files=[".github/workflows/ci.yml"], gate_verdict=CLEAN,
                        tier3_globs=TIER3, docs_globs=DOCS)
        self.assertEqual(a.tier, 3)
        self.assertEqual(a.reviewers, 3)
        self.assertTrue(a.window_open)  # no window configured -> always open
        self.assertEqual(a.merge.action, "merge")

    def test_docs_only_tier1(self):
        a = ship.assess(changed_files=["docs/x.md"], gate_verdict=CLEAN,
                        tier3_globs=TIER3, docs_globs=DOCS)
        self.assertEqual(a.tier, 1)
        self.assertEqual(a.reviewers, 1)

    def test_blocking_findings_block(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=BLOCKED)
        self.assertEqual(a.merge.action, "block")

    def test_ci_failing_blocks(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, ci_conclusion="FAILURE")
        self.assertEqual(a.ci_ok, False)
        self.assertEqual(a.merge.action, "block")
        self.assertEqual(a.merge.reason, "CI failing")

    def test_ci_passing_merges(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, ci_conclusion="SUCCESS")
        self.assertTrue(a.ci_ok)
        self.assertEqual(a.merge.action, "merge")

    def test_outside_window_defers(self):
        from datetime import datetime
        night = datetime(2026, 6, 5, 3, 0)  # inside 01:30-07:00 no-merge
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN,
                        timezone="Europe/Istanbul", merge_window="07:00-01:30", now=night)
        self.assertFalse(a.window_open)
        self.assertEqual(a.merge.action, "defer")


if __name__ == "__main__":
    unittest.main()
