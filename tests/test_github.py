import subprocess
import unittest
from unittest.mock import MagicMock

from keel import github


def _proc(out: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["gh"], returncode=code, stdout=out, stderr="")


class TestGithubComments(unittest.TestCase):
    def test_post_issue_comment_uses_raw_field(self):
        mock_runner = MagicMock(return_value=_proc('{"id": 123}'))
        res = github.post_issue_comment(
            "owner/repo", 42, "@mention please review", _run=mock_runner
        )
        self.assertTrue(res.ok)
        mock_runner.assert_called_once()
        cmd = mock_runner.call_args[0][0]
        self.assertEqual(
            cmd,
            [
                "gh",
                "api",
                "repos/owner/repo/issues/42/comments",
                "-X",
                "POST",
                "-F",
                "body=@mention please review",
            ],
        )

    def test_edit_issue_comment_uses_raw_field(self):
        mock_runner = MagicMock(return_value=_proc('{"id": 123}'))
        res = github.edit_issue_comment("owner/repo", 999, "@updated review body", _run=mock_runner)
        self.assertTrue(res.ok)
        mock_runner.assert_called_once()
        cmd = mock_runner.call_args[0][0]
        self.assertEqual(
            cmd,
            [
                "gh",
                "api",
                "repos/owner/repo/issues/comments/999",
                "-X",
                "PATCH",
                "-F",
                "body=@updated review body",
            ],
        )

    def test_merge_pr(self):
        mock_runner = MagicMock(return_value=_proc(""))
        res = github.merge_pr(10, method="squash", _run=mock_runner)
        self.assertTrue(res.ok)
        self.assertEqual(mock_runner.call_args[0][0], ["gh", "pr", "merge", "10", "--squash"])

    def test_comment(self):
        mock_runner = MagicMock(return_value=_proc(""))
        res = github.comment(10, "lgtm", _run=mock_runner)
        self.assertTrue(res.ok)
        self.assertEqual(
            mock_runner.call_args[0][0],
            ["gh", "pr", "comment", "10", "--body", "lgtm"],
        )

    def test_close_issue(self):
        mock_runner = MagicMock(return_value=_proc(""))
        res = github.close_issue(42, _run=mock_runner)
        self.assertTrue(res.ok)
        self.assertEqual(mock_runner.call_args[0][0], ["gh", "issue", "close", "42"])

    def test_issue_facts(self):
        mock_runner = MagicMock(return_value=_proc('{"title":"T","labels":[]}'))
        res = github.issue_facts(42, _run=mock_runner)
        self.assertTrue(res.ok)
        self.assertEqual(
            mock_runner.call_args[0][0],
            ["gh", "issue", "view", "42", "--json", "title,labels"],
        )


if __name__ == "__main__":
    unittest.main()
