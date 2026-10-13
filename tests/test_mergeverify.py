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


class TestExtractAddedLinesByFile(unittest.TestCase):
    def test_empty_diff_returns_empty_mapping(self):
        self.assertEqual(mv.extract_added_lines_by_file(""), {})
        self.assertEqual(mv.extract_added_lines_by_file(None), {})

    def test_single_file_with_added_lines(self):
        diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1,2 +1,4 @@\n"
            " context\n"
            "-removed\n"
            "+added 1\n"
            "+added 2\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"src/a.py": ["added 1", "added 2"]},
        )

    def test_new_file_mode_extracts_added_lines(self):
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+first line\n"
            "+second line\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"new.py": ["first line", "second line"]},
        )

    def test_deleted_file_mode_has_no_added_lines(self):
        diff = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-old line 1\n"
            "-old line 2\n"
        )
        self.assertEqual(mv.extract_added_lines_by_file(diff), {})

    def test_multiple_files_and_multiple_hunks(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+line a1\n"
            "@@ -10,1 +11,2 @@\n"
            "+line a2\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -5,1 +5,2 @@\n"
            "+line b1\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"a.py": ["line a1", "line a2"], "b.py": ["line b1"]},
        )

    def test_quoted_paths_and_alternative_prefixes(self):
        diff = (
            'diff --git "a/path with spaces.py" "b/path with spaces.py"\n'
            '--- "a/path with spaces.py"\n'
            '+++ "b/path with spaces.py"\n'
            "@@ -1,1 +1,2 @@\n"
            "+spaced line\n"
            "diff --git a/alt.py b/alt.py\n"
            "--- a/alt.py\n"
            "+++ a/alt.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+alt prefix line\n"
            "diff --git noprefix.py noprefix.py\n"
            "--- noprefix.py\n"
            "+++ noprefix.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+no prefix line\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {
                "path with spaces.py": ["spaced line"],
                "alt.py": ["alt prefix line"],
                "noprefix.py": ["no prefix line"],
            },
        )

    def test_code_lines_starting_with_plus_plus(self):
        diff = "diff --git a/c.cpp b/c.cpp\n--- a/c.cpp\n+++ b/c.cpp\n@@ -1,1 +1,2 @@\n++++x;\n"
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"c.cpp": ["+++x;"]},
        )

    def test_removed_sql_comment_preserves_following_added_lines(self):
        diff = (
            "diff --git a/query.sql b/query.sql\n"
            "--- a/query.sql\n"
            "+++ b/query.sql\n"
            "@@ -1,2 +1,3 @@\n"
            "--- note\n"
            "+SELECT 1;\n"
            "+SELECT 2;\n"
            " SELECT 3;\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"query.sql": ["SELECT 1;", "SELECT 2;"]},
        )

    def test_added_line_starting_with_plus_plus_captured(self):
        diff = (
            "diff --git a/calc.c b/calc.c\n"
            "--- a/calc.c\n"
            "+++ b/calc.c\n"
            "@@ -1,1 +1,2 @@\n"
            " int a = 0;\n"
            "+++ x;\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"calc.c": ["++ x;"]},
        )

    def test_multi_file_diff_with_ambiguous_headers_splits_correctly(self):
        diff = (
            "diff --git a/query.sql b/query.sql\n"
            "--- a/query.sql\n"
            "+++ b/query.sql\n"
            "@@ -1,2 +1,2 @@\n"
            "--- note\n"
            "+SELECT 1;\n"
            "diff --git a/calc.c b/calc.c\n"
            "--- a/calc.c\n"
            "+++ b/calc.c\n"
            "@@ -1,1 +1,2 @@\n"
            " int a = 0;\n"
            "+++ x;\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"query.sql": ["SELECT 1;"], "calc.c": ["++ x;"]},
        )

    def test_hunk_header_without_counts(self):
        diff = (
            "diff --git a/query.sql b/query.sql\n"
            "--- a/query.sql\n"
            "+++ b/query.sql\n"
            "@@ -1 +1 @@\n"
            "--- note\n"
            "\\ No newline at end of file\n"
            "+++ x\n"
            "\\ No newline at end of file\n"
        )
        self.assertEqual(
            mv.extract_added_lines_by_file(diff),
            {"query.sql": ["++ x"]},
        )


class TestCheckLineSurvival(unittest.TestCase):
    def test_empty_file_content_loses_all_non_blank_lines(self):
        lost_count, lost = mv.check_line_survival(["line 1", "  ", "line 2"], "")
        self.assertEqual(lost_count, 2)
        self.assertEqual(lost, ["line 1", "line 2"])

    def test_blank_lines_are_ignored(self):
        lost_count, lost = mv.check_line_survival(["", "   "], "content")
        self.assertEqual(lost_count, 0)
        self.assertEqual(lost, [])

    def test_all_added_lines_survived_exact(self):
        lost_count, lost = mv.check_line_survival(
            ["line 1", "line 2"],
            "header\nline 1\nmiddle\nline 2\ntrailer\n",
        )
        self.assertEqual(lost_count, 0)
        self.assertEqual(lost, [])

    def test_whitespace_tolerance_stripped_matching(self):
        lost_count, lost = mv.check_line_survival(
            ["    indented line"],
            "  indented line\n",
        )
        self.assertEqual(lost_count, 0)
        self.assertEqual(lost, [])

    def test_lost_lines_detected(self):
        lost_count, lost = mv.check_line_survival(
            ["line 1", "reverted line 2"],
            "line 1\nother stuff\n",
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["reverted line 2"])


class TestLineSurvivalInVerifyMerge(unittest.TestCase):
    def test_single_shared_file_with_surviving_lines_is_clean(self):
        diff = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "--- a/CHANGELOG.md\n"
            "+++ b/CHANGELOG.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+* New feature from #550\n"
        )
        content = "## Unreleased\n* New feature from #550\n* PR 543 change\n"
        report = mv.verify_merge(
            landed=["CHANGELOG.md"],
            overtaken={"CHANGELOG.md": 550},
            diffs={550: diff},
            file_contents={"CHANGELOG.md": content},
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (1 shared file, 0 lost lines)")
        self.assertEqual(report["overtaken"], {})

    def test_multiple_shared_files_with_surviving_lines_is_clean(self):
        diff550 = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "--- a/CHANGELOG.md\n"
            "+++ b/CHANGELOG.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+* Entry 550\n"
            "diff --git a/src/shared.py b/src/shared.py\n"
            "--- a/src/shared.py\n"
            "+++ b/src/shared.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+def shared_fn(): pass\n"
        )
        diff551 = (
            "diff --git a/docs/guide.md b/docs/guide.md\n"
            "--- a/docs/guide.md\n"
            "+++ b/docs/guide.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+guide notes\n"
        )
        contents = {
            "CHANGELOG.md": "* Entry 550\n",
            "src/shared.py": "def shared_fn(): pass\n",
            "docs/guide.md": "guide notes\n",
        }
        report = mv.verify_merge(
            landed=["CHANGELOG.md", "src/shared.py", "docs/guide.md"],
            overtaken={"CHANGELOG.md": 550, "src/shared.py": 550, "docs/guide.md": 551},
            diffs={550: diff550, 551: diff551},
            file_contents=contents,
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (3 shared files, 0 lost lines)")

    def test_shared_file_with_lost_lines_is_drift(self):
        diff = (
            "diff --git a/src/keel/github.py b/src/keel/github.py\n"
            "--- a/src/keel/github.py\n"
            "+++ b/src/keel/github.py\n"
            "@@ -10,1 +10,2 @@\n"
            "+def rest_call(): pass\n"
        )
        content = "def other(): pass\n"
        report = mv.verify_merge(
            landed=["src/keel/github.py"],
            overtaken={"src/keel/github.py": 550},
            diffs={550: diff},
            file_contents={"src/keel/github.py": content},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"src/keel/github.py": 550})
        self.assertIn("#550", report["reason"])

    def test_unreadable_diff_keeps_conservative_drift(self):
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": 550},
            diffs={550: None},
            file_contents={"a.py": "line\n"},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": 550})

    def test_diff_missing_shared_file_keeps_conservative_drift(self):
        diff = (
            "diff --git a/other.py b/other.py\n"
            "--- a/other.py\n"
            "+++ b/other.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+line\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": 550},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": 550})

    def test_unreadable_file_content_keeps_conservative_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": 550},
            diffs={550: diff},
            file_contents={"a.py": None},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": 550})

    def test_clean_shared_file_and_drifted_shared_file_in_same_merge(self):
        diff550 = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "--- a/CHANGELOG.md\n"
            "+++ b/CHANGELOG.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+* Keep this\n"
            "diff --git a/src/keel/github.py b/src/keel/github.py\n"
            "--- a/src/keel/github.py\n"
            "+++ b/src/keel/github.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+def missing(): pass\n"
        )
        contents = {
            "CHANGELOG.md": "* Keep this\n",
            "src/keel/github.py": "def other(): pass\n",
        }
        report = mv.verify_merge(
            landed=["CHANGELOG.md", "src/keel/github.py"],
            overtaken={"CHANGELOG.md": 550, "src/keel/github.py": 550},
            diffs={550: diff550},
            file_contents=contents,
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"src/keel/github.py": 550})
        self.assertNotIn("CHANGELOG.md", report["overtaken"])

    def test_overtaking_pr_did_not_add_lines_to_shared_file(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,1 @@\n"
            " context\n"
            "-deleted\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": 550},
            diffs={550: diff},
            file_contents={"a.py": "context\n"},
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (1 shared file, 0 lost lines)")

    def test_surviving_lines_with_out_of_scope_files(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n+survived line\n"
        report = mv.verify_merge(
            landed=["a.py", "unexpected.py"],
            overtaken={"a.py": 550},
            intended=["a.py"],
            diffs={550: diff},
            file_contents={"a.py": "survived line\n"},
        )
        self.assertEqual(report["status"], "out-of-scope")
        self.assertEqual(report["unexpected"], ["unexpected.py"])
