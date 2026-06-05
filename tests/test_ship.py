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


if __name__ == "__main__":
    unittest.main()
