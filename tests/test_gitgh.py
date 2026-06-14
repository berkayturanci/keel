"""Unit tests for the thin git/gh wrappers (argv construction + fail-soft)."""

import unittest

from keel import git, github


class _Recorder:
    """Captures the argv passed to subprocess and returns a canned proc."""

    def __init__(self, code=0, out="", err=""):
        self.code = code
        self.out = out
        self.err = err
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return _Proc(self.code, self.out, self.err)


class _Proc:
    def __init__(self, code, out, err):
        self.returncode = code
        self.stdout = out
        self.stderr = err


class TestGit(unittest.TestCase):
    def test_fetch_argv(self):
        rec = _Recorder()
        git.fetch("origin", "main", _run=rec)
        self.assertEqual(rec.calls[0], ["git", "fetch", "origin", "main", "--quiet"])

    def test_worktree_add_argv(self):
        rec = _Recorder()
        git.worktree_add("origin/main", "issue-42", "worktrees/issue-42", _run=rec)
        self.assertEqual(
            rec.calls[0],
            ["git", "worktree", "add", "-b", "issue-42", "worktrees/issue-42", "origin/main"],
        )

    def test_worktree_remove_argv(self):
        rec = _Recorder()
        git.worktree_remove("worktrees/issue-42", _run=rec)
        self.assertIn("--force", rec.calls[0])

    def test_worktree_list_argv(self):
        rec = _Recorder(out="worktree /repo\n")
        git.worktree_list(_run=rec)
        self.assertEqual(rec.calls[0], ["git", "worktree", "list", "--porcelain"])

    def test_current_branch_parsed(self):
        self.assertEqual(git.current_branch(_run=_Recorder(out="feature\n")), "feature")

    def test_current_branch_failsoft(self):
        self.assertIsNone(git.current_branch(_run=_Recorder(code=1)))

    def test_changed_files_parsed(self):
        rec = _Recorder(out="a.py\nb.dart\n\n")
        self.assertEqual(git.changed_files("origin/main", "HEAD", _run=rec), ["a.py", "b.dart"])
        self.assertEqual(rec.calls[0][-1], "origin/main...HEAD")

    def test_changed_files_failsoft(self):
        self.assertEqual(git.changed_files("a", "b", _run=_Recorder(code=2)), [])

    def test_diff_returns_patch(self):
        rec = _Recorder(out="@@ -1 +1 @@\n-a\n+b")
        self.assertEqual(git.diff("main", "HEAD", _run=rec), "@@ -1 +1 @@\n-a\n+b")
        self.assertEqual(rec.calls[0], ["git", "diff", "main...HEAD"])

    def test_diff_failsoft(self):
        self.assertEqual(git.diff("a", "b", _run=_Recorder(code=1)), "")

    def test_rev_parse_resolves_sha(self):
        rec = _Recorder(out="deadbeef\n")
        self.assertEqual(git.rev_parse("origin/main", _run=rec), "deadbeef")
        self.assertEqual(rec.calls[0], ["git", "rev-parse", "--verify", "--quiet", "origin/main"])

    def test_rev_parse_failsoft_on_error(self):
        self.assertIsNone(git.rev_parse("origin/main", _run=_Recorder(code=1)))

    def test_rev_parse_failsoft_on_empty(self):
        self.assertIsNone(git.rev_parse("origin/main", _run=_Recorder(out="\n")))

    def test_merge_base_argv_and_value(self):
        rec = _Recorder(out="abc123\n")
        self.assertEqual(git.merge_base("head", "tip", _run=rec), "abc123")
        self.assertEqual(rec.calls[0], ["git", "merge-base", "head", "tip"])

    def test_merge_base_failsoft_on_error(self):
        self.assertIsNone(git.merge_base("a", "b", _run=_Recorder(code=1)))

    def test_merge_base_failsoft_on_empty(self):
        self.assertIsNone(git.merge_base("a", "b", _run=_Recorder(out="")))

    def test_rev_count_argv_and_value(self):
        rec = _Recorder(out="4\n")
        self.assertEqual(git.rev_count("base", "tip", _run=rec), 4)
        self.assertEqual(rec.calls[0], ["git", "rev-list", "--count", "base..tip"])

    def test_rev_count_failsoft_on_error(self):
        self.assertIsNone(git.rev_count("a", "b", _run=_Recorder(code=2)))

    def test_rev_count_failsoft_on_non_numeric(self):
        self.assertIsNone(git.rev_count("a", "b", _run=_Recorder(out="oops\n")))


class TestGitHub(unittest.TestCase):
    def test_open_pr_argv(self):
        rec = _Recorder()
        github.open_pr("T", "B", "main", "feature", _run=rec)
        argv = rec.calls[0]
        self.assertEqual(argv[:3], ["gh", "pr", "create"])
        self.assertIn("--base", argv)
        self.assertIn("feature", argv)

    def test_ci_conclusion_parsed(self):
        self.assertEqual(github.ci_conclusion(7, _run=_Recorder(out="SUCCESS\n")), "SUCCESS")

    def test_ci_conclusion_empty_is_none(self):
        self.assertIsNone(github.ci_conclusion(7, _run=_Recorder(out="\n")))

    def test_ci_conclusion_failsoft(self):
        self.assertIsNone(github.ci_conclusion(7, _run=_Recorder(code=1)))

    def test_merge_pr_method(self):
        rec = _Recorder()
        github.merge_pr(7, method="rebase", _run=rec)
        self.assertEqual(rec.calls[0], ["gh", "pr", "merge", "7", "--rebase"])

    def test_pr_merge_snapshot_argv(self):
        rec = _Recorder(out="{}")
        github.pr_merge_snapshot(7, _run=rec)
        self.assertEqual(
            rec.calls[0],
            [
                "gh", "pr", "view", "7",
                "--json", "headRefOid,mergeStateStatus,statusCheckRollup",
            ],
        )

    def test_merged_prs_argv_default(self):
        rec = _Recorder(out="[]")
        result = github.merged_prs(_run=rec)
        self.assertTrue(result.ok)
        self.assertEqual(
            rec.calls[0],
            ["gh", "pr", "list", "--state", "merged", "--limit", "100", "--json", "number"],
        )

    def test_merged_prs_argv_with_search_and_limit(self):
        rec = _Recorder(out="[]")
        github.merged_prs(search="merged:>=2026-06-01", limit=5, _run=rec)
        self.assertEqual(
            rec.calls[0],
            ["gh", "pr", "list", "--state", "merged", "--limit", "5", "--json", "number",
             "--search", "merged:>=2026-06-01"],
        )

    def test_comment_and_close(self):
        rec = _Recorder()
        github.comment(7, "hi", _run=rec)
        github.close_issue(42, _run=rec)
        self.assertEqual(rec.calls[0], ["gh", "pr", "comment", "7", "--body", "hi"])
        self.assertEqual(rec.calls[1], ["gh", "issue", "close", "42"])

    def test_issue_facts(self):
        rec = _Recorder()
        github.issue_facts(42, _run=rec)
        self.assertEqual(
            rec.calls[0],
            ["gh", "issue", "view", "42", "--json", "title,labels"],
        )

    def test_list_prs_argv_default(self):
        rec = _Recorder(out="[]")
        github.list_prs(_run=rec)
        self.assertEqual(
            rec.calls[0],
            ["gh", "pr", "list", "--state", "all", "--limit", "100",
             "--json", "number,headRefName"],
        )

    def test_list_prs_argv_with_head(self):
        rec = _Recorder(out="[]")
        github.list_prs(head="feature/issue-8", limit=5, _run=rec)
        self.assertEqual(
            rec.calls[0],
            ["gh", "pr", "list", "--state", "all", "--limit", "5",
             "--json", "number,headRefName", "--head", "feature/issue-8"],
        )


class TestListBranches(unittest.TestCase):
    def test_list_branches_argv_and_raw_result(self):
        rec = _Recorder(out="main\nfeature/issue-8\norigin/main\n\n")
        result = git.list_branches(_run=rec)
        self.assertEqual(
            rec.calls[0],
            ["git", "for-each-ref", "--format=%(refname:short)",
             "refs/heads", "refs/remotes"],
        )
        # Returns the raw CommandResult (parsing is the caller's) so an error is
        # distinguishable from an empty repo.
        self.assertTrue(result.ok)
        self.assertIn("feature/issue-8", result.output)

    def test_list_branches_error_is_visible(self):
        result = git.list_branches(_run=_Recorder(code=1))
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
