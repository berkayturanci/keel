"""Unit tests for findings + the severity → decision mapping."""

import unittest

from keel import findings as fnd
from keel.findings import Finding


class TestFinding(unittest.TestCase):
    def test_valid(self):
        f = Finding("major", "boom", "reviewer-A")
        self.assertEqual(f.severity, "major")

    def test_invalid_severity(self):
        with self.assertRaises(fnd.FindingError):
            Finding("blocker", "x", "y")


class TestDecision(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(fnd.decision_for("critical"), "block")
        self.assertEqual(fnd.decision_for("major"), "block")
        self.assertEqual(fnd.decision_for("minor"), "suggest")
        self.assertEqual(fnd.decision_for("nit"), "advisory")

    def test_unknown_severity(self):
        with self.assertRaises(fnd.FindingError):
            fnd.decision_for("whatever")


class TestAnchorable(unittest.TestCase):
    def test_anchorable_requires_path_and_line(self):
        anchored = Finding("nit", "m", "s", path="a.py", line=3, anchorable=True)
        self.assertTrue(fnd.is_anchorable(anchored))
        self.assertFalse(fnd.is_anchorable(Finding("nit", "m", "s", anchorable=True)))
        self.assertFalse(fnd.is_anchorable(Finding("nit", "m", "s", path="a.py", line=3)))
        self.assertFalse(fnd.is_anchorable(Finding("nit", "m", "s", path="a.py", anchorable=True)))


class TestSortAndSummarize(unittest.TestCase):
    def test_sort_is_deterministic_by_severity(self):
        fs = [Finding("nit", "z", "b"), Finding("critical", "a", "a"), Finding("minor", "m", "a")]
        order = [f.severity for f in fnd.sort_findings(fs)]
        self.assertEqual(order, ["critical", "minor", "nit"])

    def test_summarize_counts_and_block(self):
        v = fnd.summarize([
            Finding("major", "x", "a"),
            Finding("nit", "y", "b"),
            Finding("nit", "z", "c"),
        ])
        self.assertTrue(v.blocked)
        self.assertEqual(v.counts["major"], 1)
        self.assertEqual(v.counts["nit"], 2)
        self.assertEqual(v.counts["critical"], 0)
        self.assertEqual(len(v.findings), 3)

    def test_summarize_not_blocked_when_only_soft(self):
        v = fnd.summarize([Finding("minor", "x", "a"), Finding("nit", "y", "b")])
        self.assertFalse(v.blocked)

    def test_summarize_empty(self):
        v = fnd.summarize([])
        self.assertFalse(v.blocked)
        self.assertEqual(sum(v.counts.values()), 0)


if __name__ == "__main__":
    unittest.main()
