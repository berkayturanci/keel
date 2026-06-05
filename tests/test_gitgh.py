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

    def test_comment_and_close(self):
        rec = _Recorder()
        github.comment(7, "hi", _run=rec)
        github.close_issue(42, _run=rec)
        self.assertEqual(rec.calls[0], ["gh", "pr", "comment", "7", "--body", "hi"])
        self.assertEqual(rec.calls[1], ["gh", "issue", "close", "42"])


if __name__ == "__main__":
    unittest.main()
