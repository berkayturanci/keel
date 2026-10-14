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


class TestLineSurvival(unittest.TestCase):
    def test_shared_file_with_all_added_lines_present_is_clean(self):
        report = mv.verify_merge(
            landed=["CHANGELOG.md"],
            overtaken={"CHANGELOG.md": 550},
            added_by_pr={550: {"CHANGELOG.md": ["- entry 550", "   ", ""]}},
            merged_contents={"CHANGELOG.md": "# Changelog\n\n- entry 550\n- entry 543\n"},
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (1 shared files, 0 lost lines)")
        self.assertEqual(report["overtaken"], {"CHANGELOG.md": 550})
        self.assertEqual(report["lost_lines"], {})
        self.assertEqual(report["unverifiable"], [])
        self.assertIn("clean (1 shared files, 0 lost lines)", mv.render(report))

    def test_shared_file_with_lost_added_lines_is_drift(self):
        report = mv.verify_merge(
            landed=["src/keel/github.py"],
            overtaken={"src/keel/github.py": 550},
            added_by_pr={
                550: {
                    "src/keel/github.py": [
                        "kept = 1",
                        "lost_a = 1",
                        "lost_b = 2",
                        "lost_c = 3",
                        "lost_d = 4",
                    ]
                }
            },
            merged_contents={"src/keel/github.py": "kept = 1\n"},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(
            report["lost_lines"],
            {
                "src/keel/github.py": {
                    "pr": 550,
                    "count": 4,
                    "samples": ["lost_a = 1", "lost_b = 2", "lost_c = 3"],
                }
            },
        )
        self.assertEqual(report["unverifiable"], [])
        rendered = mv.render(report)
        self.assertIn("4 lost line(s)", rendered)
        self.assertIn("lost_a = 1", rendered)
        self.assertNotIn("lost_d = 4", rendered)

    def test_unverifiable_file_falls_back_to_drift(self):
        for added, contents in (
            ({550: {"a.py": None}}, {"a.py": "x = 1\n"}),
            ({550: {"a.py": ["x = 1"]}}, {"a.py": None}),
        ):
            with self.subTest(added=added, contents=contents):
                report = mv.verify_merge(
                    landed=["a.py"],
                    overtaken={"a.py": 550},
                    added_by_pr=added,
                    merged_contents=contents,
                )
                self.assertEqual(report["status"], "drift")
                self.assertIn("unverifiable", report["reason"])
                self.assertEqual(report["unverifiable"], ["a.py"])
                self.assertEqual(report["lost_lines"], {})
                self.assertIn("unverifiable", mv.render(report))

    def test_multiple_overtaking_prs_on_same_file_checks_every_pr(self):
        report = mv.verify_merge(
            landed=["CHANGELOG.md"],
            overtaken={"CHANGELOG.md": [550, 551]},
            added_by_pr={
                550: {"CHANGELOG.md": ["- entry 550"]},
                551: {"CHANGELOG.md": ["- entry 551"]},
            },
            merged_contents={"CHANGELOG.md": "# Changelog\n\n- entry 550\n- entry 543\n"},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"CHANGELOG.md": 551})
        self.assertEqual(
            report["lost_lines"],
            {"CHANGELOG.md": {"pr": 551, "count": 1, "samples": ["- entry 551"]}},
        )
        self.assertIn("CHANGELOG.md  — also changed by #551 (1 lost line(s))", mv.render(report))

        unverifiable_second = mv.verify_merge(
            landed=["CHANGELOG.md"],
            overtaken={"CHANGELOG.md": [550, 551]},
            added_by_pr={
                550: {"CHANGELOG.md": ["- entry 550"]},
                551: {"CHANGELOG.md": None},
            },
            merged_contents={"CHANGELOG.md": "# Changelog\n\n- entry 550\n"},
        )
        self.assertEqual(unverifiable_second["status"], "drift")
        self.assertEqual(unverifiable_second["overtaken"], {"CHANGELOG.md": 551})
        self.assertEqual(unverifiable_second["unverifiable"], ["CHANGELOG.md"])

        both_clean = mv.verify_merge(
            landed=["CHANGELOG.md", "extra.py"],
            overtaken={"CHANGELOG.md": [550, 551]},
            intended=["CHANGELOG.md"],
            added_by_pr={
                550: {"CHANGELOG.md": ["- entry 550"]},
                551: {"CHANGELOG.md": ["- entry 551"]},
            },
            merged_contents={"CHANGELOG.md": "# Changelog\n\n- entry 550\n- entry 551\n"},
        )
        self.assertEqual(both_clean["status"], "out-of-scope")
        self.assertEqual(both_clean["overtaken"], {"CHANGELOG.md": 550})
        self.assertEqual(both_clean["unexpected"], ["extra.py"])

    def test_multiple_overtaking_prs_losing_different_lines_preserves_per_pr_attribution(self):
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            added_by_pr={
                550: {"a.py": ["x"]},
                551: {"a.py": ["y"]},
            },
            merged_contents={"a.py": ""},
        )
        self.assertEqual(report["status"], "drift")
        self.assertIn("#550: 1 lost", report["reason"])
        self.assertIn("#551: 1 lost", report["reason"])
        self.assertEqual(
            report["lost_lines"],
            {
                "a.py": {
                    "pr": 550,
                    "count": 2,
                    "samples": ["x", "y"],
                    "by_pr": {
                        550: {"count": 1, "samples": ["x"]},
                        551: {"count": 1, "samples": ["y"]},
                    },
                }
            },
        )
        rendered = mv.render(report)
        self.assertIn("a.py  — also changed by #550 (1 lost line(s))", rendered)
        self.assertIn("a.py  — also changed by #551 (1 lost line(s))", rendered)

    def test_mixed_lost_and_unverifiable_prs_on_same_file_records_both(self):
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            added_by_pr={
                550: {"a.py": ["x"]},
                551: {"a.py": None},
            },
            merged_contents={"a.py": ""},
        )
        self.assertEqual(report["status"], "drift")
        self.assertIn("a.py", report["lost_lines"])
        self.assertEqual(report["unverifiable"], ["a.py"])
        self.assertIn("#550: 1 lost", report["reason"])
        self.assertIn("#551: unverifiable", report["reason"])
        rendered = mv.render(report)
        self.assertIn("a.py  — also changed by #550 (1 lost line(s))", rendered)
        self.assertIn("a.py  — also changed by #551 (unverifiable)", rendered)
