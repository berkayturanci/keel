"""Unit tests for the post-merge silent-revert check (issue #561).

The scenario these encode is real: on 2026-07-10, #550 merged at 13:02 touching
``src/keel/github.py`` and three others; #543 had branched on the 8th, merged at
15:19, and its squash removed 41 lines from ``github.py`` — a file a
"label the search input" change has no reason to touch. CI stayed green because
the reverted state was internally consistent.
"""

import unittest

from keel import mergeverify as mv


class TestOvertakenIsTheSignal(unittest.TestCase):
    def test_the_historical_shape_is_drift(self):
        report = mv.verify_merge(
            landed=["website/index.html", "src/keel/github.py"],
            overtaken={"src/keel/github.py": 550},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"src/keel/github.py": 550})
        # The overtaking PR must be named: "some file drifted" is not actionable.
        self.assertIn("#550", report["reason"])

    def test_an_overtaking_pr_that_touched_other_files_is_not_drift(self):
        # Another PR merged in the window, but nowhere near this merge's files.
        report = mv.verify_merge(
            landed=["website/index.html"],
            overtaken={"src/keel/github.py": 550},
        )
        self.assertEqual(report["status"], "clean")

    def test_no_overtaking_prs_at_all(self):
        self.assertEqual(mv.verify_merge(["a.py"], {})["status"], "clean")
        self.assertEqual(mv.verify_merge(["a.py"], None)["status"], "clean")

    def test_every_overtaken_file_is_reported_not_just_the_first(self):
        report = mv.verify_merge(
            landed=["a.py", "b.py", "c.py"],
            overtaken={"a.py": 550, "b.py": 546},
        )
        self.assertEqual(report["overtaken"], {"a.py": 550, "b.py": 546})

    def test_blank_paths_are_ignored_on_both_sides(self):
        self.assertEqual(mv.verify_merge(["  ", "a.py"], {"  ": 1})["status"], "clean")


class TestScopeIsTheSecondarySignal(unittest.TestCase):
    """Kept, but weaker — it reported *clean* on the incident this module exists for."""

    def test_a_file_outside_the_prs_own_diff(self):
        report = mv.verify_merge(landed=["a.py", "b.py"], intended=["a.py"])
        self.assertEqual(report["status"], "out-of-scope")
        self.assertEqual(report["unexpected"], ["b.py"])

    def test_overtaking_outranks_scope(self):
        # Both true; the operator needs the revert-shaped one first.
        report = mv.verify_merge(
            landed=["a.py", "b.py"], overtaken={"a.py": 550}, intended=["a.py"]
        )
        self.assertEqual(report["status"], "drift")

    def test_scope_is_skipped_when_the_pr_file_list_is_unreadable(self):
        self.assertEqual(mv.verify_merge(["a.py"], {}, None)["status"], "clean")

    def test_a_merge_narrower_than_the_pr_is_not_flagged(self):
        # An identical change already on the base makes a file's diff empty; that
        # is ordinary, not a revert.
        self.assertEqual(mv.verify_merge(["a.py"], {}, ["a.py", "b.py"])["status"], "clean")


class TestUnknownIsNotClean(unittest.TestCase):
    def test_unreadable_merge_is_unknown(self):
        report = mv.verify_merge(None)
        self.assertEqual(report["status"], "unknown")
        self.assertFalse(mv.is_drift(report))

    def test_unknown_never_reads_as_a_pass(self):
        # Failing to look is not evidence that nothing drifted (#675's rule).
        self.assertNotEqual(mv.verify_merge(None)["status"], "clean")


class TestReportSurface(unittest.TestCase):
    def test_is_drift_only_for_drift(self):
        self.assertTrue(mv.is_drift(mv.verify_merge(["a"], {"a": 1})))
        for other in (
            mv.verify_merge(["a"], {}),
            mv.verify_merge(None),
            mv.verify_merge(["a", "b"], {}, ["a"]),
        ):
            self.assertFalse(mv.is_drift(other))
        self.assertFalse(mv.is_drift("not a report"))

    def test_render_names_each_file_and_its_overtaking_pr(self):
        text = mv.render(mv.verify_merge(["a.py"], {"a.py": 550}))
        self.assertIn("drift", text)
        self.assertIn("a.py", text)
        self.assertIn("#550", text)

    def test_render_lists_out_of_scope_files(self):
        text = mv.render(mv.verify_merge(["a.py", "b.py"], {}, ["a.py"]))
        self.assertIn("b.py", text)
        self.assertIn("not in the PR's own diff", text)

    def test_render_handles_a_clean_report(self):
        self.assertIn("clean", mv.render(mv.verify_merge(["a.py"], {})))

    def test_every_report_carries_the_schema_version(self):
        for report in (
            mv.verify_merge(None),
            mv.verify_merge(["a"], {}),
            mv.verify_merge(["a"], {"a": 1}),
        ):
            self.assertEqual(report["schema_version"], mv.SCHEMA_VERSION)
