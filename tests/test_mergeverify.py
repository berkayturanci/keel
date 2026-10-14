"""Unit tests for the post-merge silent-revert check (issue #561, #1511).

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
            overtaken={"src/keel/github.py": [550]},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"src/keel/github.py": [550]})
        # The overtaking PR must be named: "some file drifted" is not actionable.
        self.assertIn("#550", report["reason"])

    def test_an_overtaking_pr_that_touched_other_files_is_not_drift(self):
        # Another PR merged in the window, but nowhere near this merge's files.
        report = mv.verify_merge(
            landed=["website/index.html"],
            overtaken={"src/keel/github.py": [550]},
        )
        self.assertEqual(report["status"], "clean")

    def test_no_overtaking_prs_at_all(self):
        self.assertEqual(mv.verify_merge(["a.py"], {})["status"], "clean")
        self.assertEqual(mv.verify_merge(["a.py"], None)["status"], "clean")

    def test_every_overtaken_file_is_reported_not_just_the_first(self):
        report = mv.verify_merge(
            landed=["a.py", "b.py", "c.py"],
            overtaken={"a.py": [550], "b.py": [546]},
        )
        self.assertEqual(report["overtaken"], {"a.py": [550], "b.py": [546]})

    def test_blank_paths_are_ignored_on_both_sides(self):
        self.assertEqual(mv.verify_merge(["  ", "a.py"], {"  ": [1]})["status"], "clean")

    def test_single_int_in_overtaken_normalized_to_list(self):
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": 550},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})


class TestScopeIsTheSecondarySignal(unittest.TestCase):
    """Kept, but weaker — it reported *clean* on the incident this module exists for."""

    def test_a_file_outside_the_prs_own_diff(self):
        report = mv.verify_merge(landed=["a.py", "b.py"], intended=["a.py"])
        self.assertEqual(report["status"], "out-of-scope")
        self.assertEqual(report["unexpected"], ["b.py"])

    def test_overtaking_outranks_scope(self):
        # Both true; the operator needs the revert-shaped one first.
        report = mv.verify_merge(
            landed=["a.py", "b.py"], overtaken={"a.py": [550]}, intended=["a.py"]
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
        self.assertTrue(mv.is_drift(mv.verify_merge(["a"], {"a": [1]})))
        for other in (
            mv.verify_merge(["a"], {}),
            mv.verify_merge(None),
            mv.verify_merge(["a", "b"], {}, ["a"]),
        ):
            self.assertFalse(mv.is_drift(other))
        self.assertFalse(mv.is_drift("not a report"))

    def test_render_names_each_file_and_its_overtaking_pr(self):
        text = mv.render(mv.verify_merge(["a.py"], {"a.py": [550]}))
        self.assertIn("drift", text)
        self.assertIn("a.py", text)
        self.assertIn("#550", text)

    def test_render_lists_out_of_scope_files(self):
        text = mv.render(mv.verify_merge(["a.py", "b.py"], {}, ["a.py"]))
        self.assertIn("b.py", text)
        self.assertIn("not in the PR's own diff", text)

    def test_render_handles_a_clean_report(self):
        self.assertIn("clean", mv.render(mv.verify_merge(["a.py"], {})))

    def test_render_handles_int_overtaken(self):
        report = {"status": "drift", "reason": "drift", "overtaken": {"a.py": 550}}
        text = mv.render(report)
        self.assertIn("#550", text)

    def test_every_report_carries_the_schema_version(self):
        for report in (
            mv.verify_merge(None),
            mv.verify_merge(["a"], {}),
            mv.verify_merge(["a"], {"a": [1]}),
        ):
            self.assertEqual(report["schema_version"], mv.SCHEMA_VERSION)


class TestDiffParser(unittest.TestCase):
    def test_empty_diff_returns_empty_mapping(self):
        self.assertEqual(mv.parse_diff_by_file(""), {})
        self.assertIsNone(mv.parse_diff_by_file(None))

    def test_single_file_with_added_and_deleted_lines(self):
        diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1,2 +1,3 @@\n"
            " context\n"
            "-removed\n"
            "+added 1\n"
            "+added 2\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["src/a.py"].added_lines, ("added 1", "added 2"))
        self.assertEqual(parsed["src/a.py"].deleted_lines, ("removed",))
        self.assertFalse(parsed["src/a.py"].is_deleted)

    def test_new_file_mode(self):
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+first line\n"
            "+second line\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["new.py"].added_lines, ("first line", "second line"))
        self.assertEqual(parsed["new.py"].deleted_lines, ())
        self.assertFalse(parsed["new.py"].is_deleted)

    def test_deleted_file_mode(self):
        diff = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-old line 1\n"
            "-old line 2\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["old.py"].added_lines, ())
        self.assertEqual(parsed["old.py"].deleted_lines, ("old line 1", "old line 2"))
        self.assertTrue(parsed["old.py"].is_deleted)

    def test_multiple_files_and_multiple_hunks(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,2 @@\n"
            " ctx\n"
            "+line a1\n"
            "@@ -10,1 +11,2 @@\n"
            " ctx2\n"
            "+line a2\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -5,1 +5,2 @@\n"
            " ctx3\n"
            "+line b1\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(
            {p: list(fd.added_lines) for p, fd in parsed.items()},
            {"a.py": ["line a1", "line a2"], "b.py": ["line b1"]},
        )

    def test_quoted_paths_and_alternative_prefixes(self):
        diff = (
            'diff --git "a/path with spaces.py" "b/path with spaces.py"\n'
            '--- "a/path with spaces.py"\n'
            '+++ "b/path with spaces.py"\n'
            "@@ -0,0 +1,1 @@\n"
            "+spaced line\n"
            "diff --git a/alt.py b/alt.py\n"
            "--- a/alt.py\n"
            "+++ b/alt.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+alt prefix line\n"
            "diff --git a/noprefix.py b/noprefix.py\n"
            "--- noprefix.py\n"
            "+++ noprefix.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+no prefix line\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(
            {p: list(fd.added_lines) for p, fd in parsed.items()},
            {
                "path with spaces.py": ["spaced line"],
                "alt.py": ["alt prefix line"],
                "noprefix.py": ["no prefix line"],
            },
        )

    def test_noprefix_diff_with_top_level_a_directory(self):
        # When diff is noprefix, a top-level `a/` path like `a/foo.py` must not be mangled
        diff = (
            "diff --git a/foo.py a/foo.py\n"
            "--- a/foo.py\n"
            "+++ a/foo.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+top level a line\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertIn("a/foo.py", parsed)

    def test_quoted_path_with_c_octal_escapes(self):
        # Git escapes tabs as \t and octal escapes like \303\251 for non-ASCII ('é')
        diff = (
            'diff --git "a/tab\\tname.py" "b/tab\\tname.py"\n'
            '--- "a/tab\\tname.py"\n'
            '+++ "b/tab\\tname.py"\n'
            "@@ -0,0 +1,1 @@\n"
            "+tab line\n"
            'diff --git "a/\\303\\251.py" "b/\\303\\251.py"\n'
            '--- "a/\\303\\251.py"\n'
            '+++ "b/\\303\\251.py"\n'
            "@@ -0,0 +1,1 @@\n"
            "+accented line\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertIn("tab\tname.py", parsed)
        self.assertIn("é.py", parsed)
        self.assertEqual(parsed["é.py"].added_lines, ("accented line",))

    def test_malformed_quoted_path_fallback(self):
        # A quoted path with invalid escape falls back safely
        diff = (
            'diff --git "a/bad\\xname.py" "b/bad\\xname.py"\n'
            '--- "a/bad\\xname.py"\n'
            '+++ "b/bad\\xname.py"\n'
            "@@ -0,0 +1,1 @@\n"
            "+line\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)

    def test_truncated_diff_ending_after_plus_header_is_rejected(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_truncated_diff_incomplete_hunk_is_rejected(self):
        # Header promises 2 new lines (+1,2), but only 1 line provided before EOF
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,2 @@\n+only one line\n"
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_truncated_diff_incomplete_hunk_before_next_file_is_rejected(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+only one line\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+b line\n"
        )
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_hunk_without_file_header_is_rejected(self):
        diff = "@@ -0,0 +1,1 @@\n+orphan hunk\n"
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_diff_with_excess_lines_in_hunk_is_rejected(self):
        # Header promises 1 line (+1,1), but 2 lines provided
        diff = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line 1\n+line 2\n"
        )
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_diff_with_dev_null_without_old_file_is_rejected(self):
        diff = "diff --git a/a.py b/a.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-line\n"
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_hunk_header_without_counts_parsed(self):
        diff = (
            "diff --git a/query.sql b/query.sql\n"
            "--- a/query.sql\n"
            "+++ b/query.sql\n"
            "@@ -1 +1 @@\n"
            "-note\n"
            "+SELECT 1;\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["query.sql"].added_lines, ("SELECT 1;",))
        self.assertEqual(parsed["query.sql"].deleted_lines, ("note",))


class TestCheckLineSurvival(unittest.TestCase):
    def test_empty_file_content_loses_all_non_blank_lines(self):
        lost_count, lost = mv.check_multiset_survival(
            ["line 1", "  ", "line 2"], (), "line 1\nline 2\n", ""
        )
        self.assertEqual(lost_count, 2)
        self.assertEqual(lost, ["line 1", "line 2"])

    def test_blank_lines_are_ignored(self):
        lost_count, lost = mv.check_multiset_survival(["", "   "], (), "", "content")
        self.assertEqual(lost_count, 0)
        self.assertEqual(lost, [])

    def test_all_added_lines_survived_exact(self):
        lost_count, lost = mv.check_multiset_survival(
            ["line 1", "line 2"],
            (),
            "line 1\nline 2\n",
            "header\nline 1\nmiddle\nline 2\ntrailer\n",
        )
        self.assertEqual(lost_count, 0)
        self.assertEqual(lost, [])

    def test_lost_lines_detected(self):
        lost_count, lost = mv.check_multiset_survival(
            ["line 1", "reverted line 2"],
            (),
            "line 1\nreverted line 2\n",
            "line 1\nother stuff\n",
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["reverted line 2"])

    def test_hunk_of_only_generic_lines_wiped(self):
        # A hunk composed only of generic lines (e.g. `}`, `return None`)
        lost_count, lost = mv.check_multiset_survival(
            added_lines=["}", "return None"],
            deleted_lines=[],
            landed_content="def fn():\nreturn None\n}\n",
            merge_content="def fn():\npass\n",
        )
        self.assertEqual(lost_count, 2)
        self.assertIn("}", lost)
        self.assertIn("return None", lost)

    def test_re_added_deletion_is_drift(self):
        # PR deleted a line, but merge commit re-added it
        lost_count, lost = mv.check_multiset_survival(
            added_lines=[],
            deleted_lines=["legacy_feature = True"],
            landed_content="other_code = 1\n",
            merge_content="other_code = 1\nlegacy_feature = True\n",
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["legacy_feature = True"])

    def test_duplicate_identical_additions_where_one_is_removed(self):
        # Base had one occurrence; PR added a duplicate (so 2 total).
        # Merge only keeps 1 occurrence.
        lost_count, lost = mv.check_multiset_survival(
            added_lines=["entry: active"],
            deleted_lines=[],
            landed_content="entry: active\nentry: active\n",
            merge_content="entry: active\n",
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["entry: active"])

    def test_whole_file_deletion_that_comes_back(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=[],
            deleted_lines=["old line"],
            landed_content=mv.FILE_ABSENT,
            merge_content="old line\n",
            is_deleted=True,
        )
        self.assertEqual(lost_count, 1)

    def test_whole_file_deletion_that_stays_deleted_is_clean(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=[],
            deleted_lines=["old line"],
            landed_content=mv.FILE_ABSENT,
            merge_content=mv.FILE_ABSENT,
            is_deleted=True,
        )
        self.assertEqual(lost_count, 0)
        self.assertEqual(lost, [])

    def test_whole_file_deletion_with_unreadable_merge_content_is_drift(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=[],
            deleted_lines=["old line"],
            landed_content=mv.FILE_ABSENT,
            merge_content=None,
            is_deleted=True,
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["unreadable merge commit content"])

    def test_non_deleted_file_with_file_absent_in_check_multiset_survival(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=["line"],
            deleted_lines=[],
            landed_content="line\n",
            merge_content=mv.FILE_ABSENT,
            is_deleted=False,
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["file missing in merge commit"])

    def test_non_deleted_file_with_landed_absent_in_check_multiset_survival(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=["line"],
            deleted_lines=[],
            landed_content=mv.FILE_ABSENT,
            merge_content="line\n",
            is_deleted=False,
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["landed content absent"])

    def test_unreadable_content_returns_drift(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=["line"],
            deleted_lines=[],
            landed_content=None,
            merge_content="line\n",
            is_deleted=False,
        )
        self.assertEqual(lost_count, 1)


class TestLineSurvivalInVerifyMerge(unittest.TestCase):
    def test_single_shared_file_with_surviving_lines_is_clean(self):
        diff = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "--- a/CHANGELOG.md\n"
            "+++ b/CHANGELOG.md\n"
            "@@ -0,0 +1,1 @@\n"
            "+* New feature from #550\n"
        )
        pr_content = "* New feature from #550\n"
        content = "## Unreleased\n* New feature from #550\n* PR 543 change\n"
        report = mv.verify_merge(
            landed=["CHANGELOG.md"],
            overtaken={"CHANGELOG.md": [550]},
            diffs={550: diff},
            file_contents={"CHANGELOG.md": content},
            pr_file_contents={550: {"CHANGELOG.md": pr_content}},
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (1 shared file, 0 lost lines)")
        self.assertEqual(report["overtaken"], {})
        self.assertEqual(report["shared_files"], ["CHANGELOG.md"])

    def test_multiple_shared_files_with_surviving_lines_is_clean(self):
        diff550 = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "--- a/CHANGELOG.md\n"
            "+++ b/CHANGELOG.md\n"
            "@@ -0,0 +1,1 @@\n"
            "+* Entry 550\n"
            "diff --git a/src/shared.py b/src/shared.py\n"
            "--- a/src/shared.py\n"
            "+++ b/src/shared.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def shared_fn(): pass\n"
        )
        diff551 = (
            "diff --git a/docs/guide.md b/docs/guide.md\n"
            "--- a/docs/guide.md\n"
            "+++ b/docs/guide.md\n"
            "@@ -0,0 +1,1 @@\n"
            "+guide notes\n"
        )
        contents = {
            "CHANGELOG.md": "* Entry 550\n",
            "src/shared.py": "def shared_fn(): pass\n",
            "docs/guide.md": "guide notes\n",
        }
        report = mv.verify_merge(
            landed=["CHANGELOG.md", "src/shared.py", "docs/guide.md"],
            overtaken={"CHANGELOG.md": [550], "src/shared.py": [550], "docs/guide.md": [551]},
            diffs={550: diff550, 551: diff551},
            file_contents=contents,
            pr_file_contents={
                550: {"CHANGELOG.md": "* Entry 550\n", "src/shared.py": "def shared_fn(): pass\n"},
                551: {"docs/guide.md": "guide notes\n"},
            },
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (3 shared files, 0 lost lines)")
        self.assertEqual(report["overtaken"], {})
        self.assertEqual(report["shared_files"], ["CHANGELOG.md", "docs/guide.md", "src/shared.py"])

    def test_shared_file_with_lost_lines_is_drift(self):
        diff = (
            "diff --git a/src/keel/github.py b/src/keel/github.py\n"
            "--- a/src/keel/github.py\n"
            "+++ b/src/keel/github.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def rest_call(): pass\n"
        )
        pr_content = "def rest_call(): pass\n"
        merge_content = "def other(): pass\n"
        report = mv.verify_merge(
            landed=["src/keel/github.py"],
            overtaken={"src/keel/github.py": [550]},
            diffs={550: diff},
            file_contents={"src/keel/github.py": merge_content},
            pr_file_contents={550: {"src/keel/github.py": pr_content}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"src/keel/github.py": [550]})
        self.assertIn("#550", report["reason"])

    def test_two_prs_adding_to_same_file_where_one_is_reverted(self):
        # Codex major & MUST test 1: two PRs add to same file, one is reverted
        diff550 = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def feature_550(): pass\n"
        )
        diff551 = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def feature_551(): pass\n"
        )
        # Merge commit kept #550's feature, but lost #551's feature:
        merge_content = "def feature_550(): pass\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            diffs={550: diff550, 551: diff551},
            file_contents={"a.py": merge_content},
            pr_file_contents={
                550: {"a.py": "def feature_550(): pass\n"},
                551: {"a.py": "def feature_550(): pass\ndef feature_551(): pass\n"},
            },
        )
        self.assertEqual(report["status"], "drift")
        # Only #551 was reverted!
        self.assertEqual(report["overtaken"], {"a.py": [551]})
        self.assertEqual(report["shared_files"], ["a.py"])
        self.assertIn("#551", report["reason"])

    def test_two_prs_adding_to_same_file_both_reverted(self):
        diff550 = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def feature_550(): pass\n"
        )
        diff551 = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def feature_551(): pass\n"
        )
        merge_content = "def clean(): pass\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            diffs={550: diff550, 551: diff551},
            file_contents={"a.py": merge_content},
            pr_file_contents={
                550: {"a.py": "def feature_550(): pass\n"},
                551: {"a.py": "def feature_551(): pass\n"},
            },
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550, 551]})
        self.assertIn("#550, #551", report["reason"])

    def test_hunk_of_only_generic_lines_wiped_reports_drift(self):
        # Sonnet major & MUST test 2: generic lines like '}' or 'return None' wiped
        diff = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,2 @@\n+}\n+return None\n"
        )
        pr_content = "}\nreturn None\n"
        merge_content = ""
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": merge_content},
            pr_file_contents={550: {"a.py": pr_content}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_re_added_deletion_reports_drift(self):
        # Sonnet major & MUST test 3: PR deleted lines, stale branch re-added them
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,1 @@\n"
            " context\n"
            "-deleted_line\n"
        )
        pr_content = "context\n"
        merge_content = "context\ndeleted_line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": merge_content},
            pr_file_contents={550: {"a.py": pr_content}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_whole_file_deletion_that_comes_back_reports_drift(self):
        # Sonnet major & MUST test 4: whole-file deletion reappearing is drift
        diff = (
            "diff --git a/a.py b/a.py\n"
            "deleted file mode 100644\n"
            "--- a/a.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-deleted content\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "recreated\n"},
            pr_file_contents={550: {"a.py": mv.FILE_ABSENT}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertIn(
            "deleted file reappeared in merge commit (#550)", report["drift_details"]["a.py"]
        )

    def test_whole_file_deletion_that_stays_deleted_is_clean(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "deleted file mode 100644\n"
            "--- a/a.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-deleted content\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": mv.FILE_ABSENT},
            pr_file_contents={550: {"a.py": mv.FILE_ABSENT}},
        )
        self.assertEqual(report["status"], "clean")

    def test_whole_file_deletion_with_unreadable_merge_content_is_drift(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "deleted file mode 100644\n"
            "--- a/a.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-deleted content\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": None},
            pr_file_contents={550: {"a.py": mv.FILE_ABSENT}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertIn("unreadable merge commit content for a.py", report["drift_details"]["a.py"])

    def test_non_deleted_file_missing_in_merge_commit_is_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+added line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": mv.FILE_ABSENT},
            pr_file_contents={550: {"a.py": "added line\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertIn("file missing in merge commit for a.py", report["drift_details"]["a.py"])

    def test_non_deleted_file_with_landed_content_absent_is_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+added line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "added line\n"},
            pr_file_contents={550: {"a.py": mv.FILE_ABSENT}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertIn("landed content absent for a.py (#550)", report["drift_details"]["a.py"])

    def test_duplicate_identical_additions_where_one_is_removed_reports_drift(self):
        # Codex major & MUST test 5: duplicate identical addition removed
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,2 @@\n"
            " dup_line\n"
            "+dup_line\n"
        )
        pr_content = "dup_line\ndup_line\n"
        merge_content = "dup_line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": merge_content},
            pr_file_contents={550: {"a.py": pr_content}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_unreadable_diff_keeps_conservative_drift(self):
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: None},
            file_contents={"a.py": "line\n"},
            pr_file_contents={550: {"a.py": "line\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_malformed_truncated_diff_keeps_conservative_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
            pr_file_contents={550: {"a.py": "line\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_diff_missing_shared_file_keeps_conservative_drift(self):
        diff = (
            "diff --git a/other.py b/other.py\n"
            "--- a/other.py\n"
            "+++ b/other.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+line\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
            pr_file_contents={550: {"a.py": "line\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_unreadable_file_content_keeps_conservative_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": None},
            pr_file_contents={550: {"a.py": "line\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_unreadable_pr_landed_content_keeps_conservative_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
            pr_file_contents={550: {"a.py": None}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_clean_shared_file_and_drifted_shared_file_in_same_merge(self):
        diff550 = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "--- a/CHANGELOG.md\n"
            "+++ b/CHANGELOG.md\n"
            "@@ -0,0 +1,1 @@\n"
            "+* Keep this\n"
            "diff --git a/src/keel/github.py b/src/keel/github.py\n"
            "--- a/src/keel/github.py\n"
            "+++ b/src/keel/github.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+def missing(): pass\n"
        )
        contents = {
            "CHANGELOG.md": "* Keep this\n",
            "src/keel/github.py": "def other(): pass\n",
        }
        pr_contents = {
            550: {
                "CHANGELOG.md": "* Keep this\n",
                "src/keel/github.py": "def missing(): pass\n",
            }
        }
        report = mv.verify_merge(
            landed=["CHANGELOG.md", "src/keel/github.py"],
            overtaken={"CHANGELOG.md": [550], "src/keel/github.py": [550]},
            diffs={550: diff550},
            file_contents=contents,
            pr_file_contents=pr_contents,
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"src/keel/github.py": [550]})
        self.assertEqual(report["shared_files"], ["CHANGELOG.md", "src/keel/github.py"])
        self.assertNotIn("CHANGELOG.md", report["overtaken"])

    def test_overtaking_pr_with_surviving_deletion(self):
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
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "context\n"},
            pr_file_contents={550: {"a.py": "context\n"}},
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["reason"], "clean (1 shared file, 0 lost lines)")

    def test_surviving_lines_with_out_of_scope_files(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+survived line\n"
        report = mv.verify_merge(
            landed=["a.py", "unexpected.py"],
            overtaken={"a.py": [550]},
            intended=["a.py"],
            diffs={550: diff},
            file_contents={"a.py": "survived line\n"},
            pr_file_contents={550: {"a.py": "survived line\n"}},
        )
        self.assertEqual(report["status"], "out-of-scope")
        self.assertEqual(report["unexpected"], ["unexpected.py"])

    def test_tuple_keyed_pr_file_contents(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+survived\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "survived\n"},
            pr_file_contents={(550, "a.py"): "survived\n"},
        )
        self.assertEqual(report["status"], "clean")

    def test_empty_hunk_is_handled(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +0,0 @@\n"
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["a.py"].added_lines, ())

    def test_diff_with_no_newline_marker(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+line\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["a.py"].added_lines, ("line",))

    def test_diff_with_file_headers_without_hunks_before_next_file(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+line\n"
        )
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_hunk_with_too_many_lines_is_rejected(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,1 @@\n"
            " unexpected context line\n"
        )
        self.assertIsNone(mv.parse_diff_by_file(diff))

    def test_check_multiset_survival_duplicate_added_lines_lost(self):
        lost, viols = mv.check_multiset_survival(["lost", "lost"], [], "lost\nlost\n", "")
        self.assertEqual(lost, 2)
        self.assertEqual(viols, ["lost"])

    def test_check_multiset_survival_duplicate_deleted_lines_readded_with_blank(self):
        lost, viols = mv.check_multiset_survival([], ["del", " ", "del"], "", "del\ndel\n")
        self.assertEqual(lost, 2)
        self.assertEqual(viols, ["del"])

    def test_verify_merge_when_pr_file_contents_is_none(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
            pr_file_contents=None,
        )
        self.assertEqual(report["status"], "drift")

    def test_verify_merge_when_flat_pr_file_contents_missing_entry(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
            pr_file_contents={(999, "a.py"): "line\n"},
        )
        self.assertEqual(report["status"], "drift")

    def test_verify_merge_with_duplicate_overtaken_pr_in_list(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 550]},
            diffs={550: diff},
            file_contents={"a.py": "line\n"},
            pr_file_contents={550: {"a.py": "line\n"}},
        )
        self.assertEqual(report["status"], "clean")

    def test_schema_version_is_v2(self):
        self.assertEqual(mv.SCHEMA_VERSION, "keel.merge-verify.v2")
        self.assertEqual(repr(mv.FILE_ABSENT), "<FILE_ABSENT>")

    def test_diff_with_form_feed_in_added_line_is_parsed(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+line with\x0cformfeed\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["a.py"].added_lines, ("line with\x0cformfeed",))

    def test_check_multiset_survival_with_form_feed(self):
        lost_count, viols = mv.check_multiset_survival(
            added_lines=["line with\x0cformfeed"],
            deleted_lines=[],
            landed_content="line with\x0cformfeed\n",
            merge_content="line with\x0cformfeed\n",
        )
        self.assertEqual(lost_count, 0)
        self.assertEqual(viols, [])

    def test_render_renders_drift_details(self):
        report = {
            "status": "drift",
            "reason": "drift detected",
            "overtaken": {"a.py": [550]},
            "drift_details": {"a.py": ["lost line: x (#550)", "unreadable content"]},
        }
        rendered = mv.render(report)
        self.assertIn("a.py", rendered)
        self.assertIn("#550", rendered)
        self.assertIn("* lost line: x (#550)", rendered)
        self.assertIn("* unreadable content", rendered)

    def test_invalid_hunk_line_prefix_produces_drift(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\ngarbage\n"
        self.assertIsNone(mv.parse_diff_by_file(diff))
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "merge content\n"},
            pr_file_contents={550: {"a.py": "landed content\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})

    def test_octal_escaped_utf8_filename_survives_cleanly_in_verify_merge(self):
        diff = (
            'diff --git "a/\\303\\251.py" "b/\\303\\251.py"\n'
            '--- "a/\\303\\251.py"\n'
            '+++ "b/\\303\\251.py"\n'
            "@@ -0,0 +1,1 @@\n"
            "+accented = True\n"
        )
        report = mv.verify_merge(
            landed=["é.py"],
            overtaken={"é.py": [550]},
            diffs={550: diff},
            file_contents={"é.py": "accented = True\n"},
            pr_file_contents={550: {"é.py": "accented = True\n"}},
        )
        self.assertEqual(report["status"], "clean")

    def test_whole_file_deletion_with_unreadable_landed_content_is_drift(self):
        lost_count, lost = mv.check_multiset_survival(
            added_lines=[],
            deleted_lines=["old line"],
            landed_content=None,
            merge_content=mv.FILE_ABSENT,
            is_deleted=True,
        )
        self.assertEqual(lost_count, 1)
        self.assertEqual(lost, ["unreadable landed content"])

        lost_count_present, lost_present = mv.check_multiset_survival(
            added_lines=[],
            deleted_lines=["old line"],
            landed_content="old line\n",
            merge_content=mv.FILE_ABSENT,
            is_deleted=True,
        )
        self.assertEqual(lost_count_present, 1)
        self.assertEqual(lost_present, ["deleted file still present in overtaking commit"])

        diff = (
            "diff --git a/a.py b/a.py\n"
            "deleted file mode 100644\n"
            "--- a/a.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-deleted content\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": mv.FILE_ABSENT},
            pr_file_contents={550: {"a.py": None}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertIn("unreadable landed content for a.py (#550)", report["drift_details"]["a.py"])

        report_present = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": mv.FILE_ABSENT},
            pr_file_contents={550: {"a.py": "deleted content\n"}},
        )
        self.assertEqual(report_present["status"], "drift")
        self.assertIn(
            "deleted file still present in overtaking commit for a.py (#550)",
            report_present["drift_details"]["a.py"],
        )

    def test_diff_with_lone_carriage_return_inside_line(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\rwith_cr\n"
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["a.py"].added_lines, ("line\rwith_cr",))
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "line\rwith_cr\n"},
            pr_file_contents={550: {"a.py": "line\rwith_cr\n"}},
        )
        self.assertEqual(report["status"], "clean")

    def test_later_overtaking_pr_editing_earlier_overtaking_pr_line_is_conservative_drift(self):
        diff550 = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+TIMEOUT = 30\n"
        )
        diff551 = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-TIMEOUT = 30\n"
            "+TIMEOUT = 60\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            diffs={550: diff550, 551: diff551},
            file_contents={"a.py": "TIMEOUT = 60\n"},
            pr_file_contents={
                550: {"a.py": "TIMEOUT = 30\n"},
                551: {"a.py": "TIMEOUT = 60\n"},
            },
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertEqual(report["shared_files"], ["a.py"])
        self.assertEqual(report["drift_details"], {"a.py": ["TIMEOUT = 30 (#550)"]})

    def test_truncated_hunk_header_after_completed_hunk_is_rejected(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+first hunk survived\n"
            "@@ -20,2 +20,"
        )
        self.assertIsNone(mv.parse_diff_by_file(diff))
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "first hunk survived\n"},
            pr_file_contents={550: {"a.py": "first hunk survived\nsecond hunk lost\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertIn("could not verify line survival (fail-closed)", report["reason"])

    def test_metadata_only_mode_change_and_unchanged_rename_are_parsed_and_clean(self):
        diff = (
            "diff --git a/script.sh b/script.sh\n"
            "old mode 100644\n"
            "new mode 100755\n"
            "diff --git a/old_name.py b/new_name.py\n"
            "similarity index 100%\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertIn("script.sh", parsed)
        self.assertEqual(parsed["script.sh"].added_lines, ())
        self.assertEqual(parsed["script.sh"].deleted_lines, ())
        self.assertFalse(parsed["script.sh"].is_deleted)
        self.assertIn("new_name.py", parsed)
        self.assertFalse(parsed["new_name.py"].is_deleted)
        self.assertIn("old_name.py", parsed)
        self.assertTrue(parsed["old_name.py"].is_deleted)

        report = mv.verify_merge(
            landed=["script.sh", "new_name.py", "old_name.py"],
            overtaken={"script.sh": [550], "new_name.py": [550], "old_name.py": [550]},
            diffs={550: diff},
            file_contents={
                "script.sh": "#!/bin/sh\necho hi\n",
                "new_name.py": "def fn(): pass\n",
                "old_name.py": mv.FILE_ABSENT,
            },
            pr_file_contents={
                550: {
                    "script.sh": "#!/bin/sh\necho hi\n",
                    "new_name.py": "def fn(): pass\n",
                    "old_name.py": mv.FILE_ABSENT,
                }
            },
        )
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["overtaken"], {})

    def test_drift_details_attribute_lost_lines_to_each_pr_and_deduplicate(self):
        diff550 = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+def f550(): pass\n"
        )
        diff551 = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+def f551(): pass\n"
        )
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            diffs={550: diff550, 551: diff551},
            file_contents={"a.py": "def base(): pass\n"},
            pr_file_contents={
                550: {"a.py": "def f550(): pass\n"},
                551: {"a.py": "def f551(): pass\n"},
            },
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(
            report["drift_details"]["a.py"],
            ["def f550(): pass (#550)", "def f551(): pass (#551)"],
        )

    def test_unreadable_inputs_drift_reason_distinguishes_could_not_verify_from_silent_revert(self):
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+line\n"
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550, 551]},
            diffs={550: diff, 551: diff},
            file_contents={"a.py": None},
            pr_file_contents={550: {"a.py": "line\n"}, 551: {"a.py": "line\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertIn("could not verify line survival (fail-closed)", report["reason"])
        self.assertNotIn("the shape of a silent revert", report["reason"])
        self.assertEqual(
            report["drift_details"]["a.py"],
            ["unreadable merge commit content for a.py"],
        )

    def test_generic_deleted_line_readded_by_later_pr_is_conservative_drift(self):
        diff550 = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,3 +1,2 @@\n"
            " def a():\n"
            "-    return None\n"
            "     return 1\n"
        )
        # #550 reduced `    return None` count from 1 to 0; this PR added a new function
        # that legitimately contains `    return None`, bringing count back to 1.
        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff550},
            file_contents={"a.py": "def a():\n    return 1\ndef b():\n    return None\n"},
            pr_file_contents={550: {"a.py": "def a():\n    return 1\n"}},
        )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["overtaken"], {"a.py": [550]})
        self.assertEqual(report["drift_details"], {"a.py": ["    return None (#550)"]})
        self.assertIn("the shape of a silent revert", report["reason"])

    def test_truncated_new_file_metadata_without_patch_is_rejected(self):
        for diff in (
            "diff --git a/a.py b/a.py\nnew file mode 100644\nindex 0000000..1234567\n",
            "diff --git a/a.py b/a.py\nnew file mode 100644\n",
            "diff --git a/a.py b/a.py\ndeleted file mode 100644\nindex 1234567..0000000\n",
        ):
            with self.subTest(diff=diff):
                self.assertIsNone(mv.parse_diff_by_file(diff))
                report = mv.verify_merge(
                    landed=["a.py"],
                    overtaken={"a.py": [550]},
                    diffs={550: diff},
                    file_contents={"a.py": ""},
                    pr_file_contents={550: {"a.py": "feature\n"}},
                )
                self.assertEqual(report["status"], "drift")
                self.assertEqual(report["overtaken"], {"a.py": [550]})
                self.assertIn("could not verify line survival (fail-closed)", report["reason"])

    def test_empty_new_and_deleted_file_with_empty_blob_sha_are_parsed(self):
        diff = (
            "diff --git a/empty_new.py b/empty_new.py\n"
            "new file mode 100644\n"
            "index 0000000..e69de29\n"
            "diff --git a/empty_del.py b/empty_del.py\n"
            "deleted file mode 100644\n"
            "index e69de29..0000000\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertFalse(parsed["empty_new.py"].is_deleted)
        self.assertFalse(parsed["empty_new.py"].has_hunks)
        self.assertTrue(parsed["empty_del.py"].is_deleted)

    def test_truncated_mode_change_with_content_change_is_drift(self):
        diff_with_index = (
            "diff --git a/script.sh b/script.sh\n"
            "old mode 100644\n"
            "new mode 100755\n"
            "index 1111111..2222222\n"
        )
        self.assertIsNone(mv.parse_diff_by_file(diff_with_index))
        report_idx = mv.verify_merge(
            landed=["script.sh"],
            overtaken={"script.sh": [550]},
            diffs={550: diff_with_index},
            file_contents={"script.sh": "#!/bin/sh\n"},
            pr_file_contents={550: {"script.sh": "#!/bin/sh\nfeature\n"}},
        )
        self.assertEqual(report_idx["status"], "drift")
        self.assertIn("could not verify line survival (fail-closed)", report_idx["reason"])

        # Even if truncated before `index` (indistinguishable from pure mode change in diff syntax),
        # checking landed content lines on a hunkless diff catches lost content at verification.
        diff_before_index = "diff --git a/script.sh b/script.sh\nold mode 100644\nnew mode 100755\n"
        report_lost = mv.verify_merge(
            landed=["script.sh"],
            overtaken={"script.sh": [550]},
            diffs={550: diff_before_index},
            file_contents={"script.sh": "#!/bin/sh\n"},
            pr_file_contents={550: {"script.sh": "#!/bin/sh\nfeature\n"}},
        )
        self.assertEqual(report_lost["status"], "drift")
        self.assertEqual(report_lost["overtaken"], {"script.sh": [550]})
        self.assertEqual(report_lost["drift_details"], {"script.sh": ["feature (#550)"]})
        self.assertIn("the shape of a silent revert", report_lost["reason"])

    def test_modified_binary_file_in_diff_is_parsed_and_does_not_fail_closed(self):
        diff = (
            "diff --git a/x.png b/x.png\n"
            "index a1b2c3d..e4f5a6b 100644\n"
            "Binary files a/x.png and b/x.png differ\n"
            "diff --git a/old.png b/old.png\n"
            "deleted file mode 100644\n"
            "index a1b2c3d..0000000\n"
            "Binary files a/old.png and /dev/null differ\n"
            "diff --git a/a.py b/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,2 @@\n"
            " base\n"
            "+survived\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertIn("x.png", parsed)
        self.assertFalse(parsed["x.png"].is_deleted)
        self.assertTrue(parsed["old.png"].is_deleted)
        self.assertEqual(parsed["a.py"].added_lines, ("survived",))

        report = mv.verify_merge(
            landed=["a.py"],
            overtaken={"a.py": [550]},
            diffs={550: diff},
            file_contents={"a.py": "base\nsurvived\n"},
            pr_file_contents={550: {"a.py": "base\nsurvived\n"}},
        )
        self.assertEqual(report["status"], "clean")

    def test_rename_with_content_changes_and_copy_from_to(self):
        diff = (
            "diff --git a/old.py b/new.py\n"
            "similarity index 85%\n"
            "rename from old.py\n"
            "rename to new.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/old.py\n"
            "+++ b/new.py\n"
            "@@ -1,1 +1,2 @@\n"
            " keep\n"
            "+added_in_rename\n"
            "diff --git a/src.py b/copied.py\n"
            "similarity index 100%\n"
            "copy from src.py\n"
            "copy to copied.py\n"
        )
        parsed = mv.parse_diff_by_file(diff)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["new.py"].added_lines, ("added_in_rename",))
        self.assertTrue(parsed["old.py"].is_deleted)
        self.assertIn("copied.py", parsed)
        self.assertFalse(parsed["copied.py"].is_deleted)

        report_clean = mv.verify_merge(
            landed=["new.py", "copied.py"],
            overtaken={"new.py": [550], "copied.py": [550]},
            diffs={550: diff},
            file_contents={"new.py": "keep\nadded_in_rename\n", "copied.py": "keep\n"},
            pr_file_contents={550: {"new.py": "keep\nadded_in_rename\n", "copied.py": "keep\n"}},
        )
        self.assertEqual(report_clean["status"], "clean")

        report_drift = mv.verify_merge(
            landed=["new.py"],
            overtaken={"new.py": [550]},
            diffs={550: diff},
            file_contents={"new.py": "keep\n"},
            pr_file_contents={550: {"new.py": "keep\nadded_in_rename\n"}},
        )
        self.assertEqual(report_drift["status"], "drift")
        self.assertEqual(report_drift["drift_details"], {"new.py": ["added_in_rename (#550)"]})

        # Truncated rename/copy with <100% similarity or malformed headers is rejected
        for bad_diff in (
            (
                "diff --git a/old.py b/new.py\n"
                "similarity index 85%\nrename from old.py\nrename to new.py\n"
            ),
            (
                "diff --git a/src.py b/dst.py\n"
                "similarity index 80%\ncopy from src.py\ncopy to dst.py\n"
            ),
            "diff --git a/a.py b/a.py\nsimilarity index bad\n",
            "diff --git a/a.py b/a.py\nindex bad_index_line\n",
            "diff --git a/a.py b/a.py\nindex 1111111..2222222\n",
            "diff --git a/a.py b/a.py\nold mode 100644\n",
            "diff --git a/a.py b/b.py\nrename from a.py\n",
        ):
            with self.subTest(bad_diff=bad_diff):
                self.assertIsNone(mv.parse_diff_by_file(bad_diff))

    def test_verify_merge_without_diffs_or_file_contents_uses_could_not_verify_reason(self):
        report = mv.verify_merge(landed=["a.py"], overtaken={"a.py": [550]})
        self.assertEqual(report["status"], "drift")
        self.assertIn("could not verify line survival (fail-closed)", report["reason"])
        self.assertNotIn("the shape of a silent revert", report["reason"])
