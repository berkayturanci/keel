"""Unit tests for the thin git/gh wrappers (argv construction + fail-soft)."""

import json
import unittest

from keel import git, github

#: Realistic object names — the wrappers validate a parsed SHA's shape, so a
#: placeholder no longer passes for one.
SHA_A = "0c4589650d0f129271ca84779442d1046ceb8482"
SHA_B = "1f2e3d4c5b6a79887766554433221100ffeeddcc"


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
        # None, not []: "git failed" must stay distinguishable from "no files changed",
        # or an unreadable diff classifies as an empty one (see #628).
        self.assertIsNone(git.changed_files("a", "b", _run=_Recorder(code=2)))

    def test_diff_returns_patch(self):
        rec = _Recorder(out="@@ -1 +1 @@\n-a\n+b")
        self.assertEqual(git.diff("main", "HEAD", _run=rec), "@@ -1 +1 @@\n-a\n+b")
        self.assertEqual(rec.calls[0], ["git", "diff", "main...HEAD"])

    def test_diff_failsoft(self):
        # None, not "": an unreadable diff must not read as an empty one, or the
        # review gate passes on a change nobody reviewed (#628).
        self.assertIsNone(git.diff("a", "b", _run=_Recorder(code=1)))

    def test_rev_parse_resolves_sha(self):
        rec = _Recorder(out=SHA_A + "\n")
        self.assertEqual(git.rev_parse("origin/main", _run=rec), SHA_A)
        self.assertEqual(rec.calls[0], ["git", "rev-parse", "--verify", "--quiet", "origin/main"])

    def test_rev_parse_failsoft_on_error(self):
        self.assertIsNone(git.rev_parse("origin/main", _run=_Recorder(code=1)))

    def test_rev_parse_failsoft_on_empty(self):
        self.assertIsNone(git.rev_parse("origin/main", _run=_Recorder(out="\n")))

    def test_merge_base_argv_and_value(self):
        rec = _Recorder(out=SHA_B + "\n")
        self.assertEqual(git.merge_base("head", "tip", _run=rec), SHA_B)
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

    def test_ci_conclusion_empty_rollup_is_not_none(self):
        """An empty rollup is a fact about the PR; None is a fact about the runner.

        Folding the first into the second is #675: nothing ran, so nothing
        verified this head, and the merge gate must be able to see the difference.
        """
        self.assertEqual(github.ci_conclusion(7, _run=_Recorder(out="\n")), "")

    def test_ci_conclusion_failsoft(self):
        self.assertIsNone(github.ci_conclusion(7, _run=_Recorder(code=1)))

    def test_ci_check_names_parsed(self):
        rec = _Recorder(out="CI\nAnalyze (Python)\n")
        self.assertEqual(github.ci_check_names(7, _run=rec), ["CI", "Analyze (Python)"])
        argv = rec.calls[0]
        self.assertEqual(argv[:4], ["gh", "pr", "view", "7"])
        self.assertIn("statusCheckRollup", argv)

    def test_ci_check_names_empty_rollup_is_empty_list(self):
        self.assertEqual(github.ci_check_names(7, _run=_Recorder(out="\n")), [])

    def test_ci_check_names_failsoft(self):
        self.assertIsNone(github.ci_check_names(7, _run=_Recorder(code=1)))

    def test_ci_workflow_names_prefers_workflow_over_job_name(self):
        rec = _Recorder(out="CI\nCodeQL\n")
        self.assertEqual(github.ci_workflow_names(7, _run=rec), ["CI", "CodeQL"])
        jq_expr = rec.calls[0][rec.calls[0].index("--jq") + 1]
        # workflowName first: the rollup's job names never equal the declared
        # workflow name on a matrix build.
        self.assertLess(jq_expr.index(".workflowName"), jq_expr.index(".context"))

    def test_ci_workflow_names_failsoft(self):
        self.assertIsNone(github.ci_workflow_names(7, _run=_Recorder(code=1)))

    def test_ci_workflow_names_empty_rollup_is_empty_list(self):
        self.assertEqual(github.ci_workflow_names(7, _run=_Recorder(out="\n")), [])

    def test_ci_check_names_uses_the_same_identity_as_ci_conclusion(self):
        # Both views must agree about what "one check" is, or a name could be
        # reported missing while its conclusion was counted.
        rec = _Recorder(out="CI\n")
        github.ci_check_names(7, _run=rec)
        jq_expr = rec.calls[0][rec.calls[0].index("--jq") + 1]
        self.assertIn(".context", jq_expr)
        self.assertIn(".name", jq_expr)

    def test_ci_conclusion_jq_dedupes_by_check_identity(self):
        rec = _Recorder(out="SUCCESS\n")
        github.ci_conclusion(7, _run=rec)
        argv = rec.calls[0]
        self.assertEqual(argv[:4], ["gh", "pr", "view", "7"])
        jq_expr = argv[argv.index("--jq") + 1]
        self.assertIn("group_by", jq_expr)
        self.assertIn("max_by", jq_expr)

    def test_pr_state_parsed(self):
        for raw, expected in (("OPEN\n", "open"), ("MERGED\n", "merged"), ("CLOSED\n", "closed")):
            with self.subTest(raw=raw):
                self.assertEqual(github.pr_state(7, _run=_Recorder(out=raw)), expected)

    def test_pr_state_unreadable_is_none_not_missing(self):
        """A failed gh call is a fact about the runner, not about the PR (#635)."""
        self.assertIsNone(github.pr_state(7, _run=_Recorder(code=1)))

    def test_pr_state_unexpected_value_is_none(self):
        self.assertIsNone(github.pr_state(7, _run=_Recorder(out="DRAFT\n")))
        self.assertIsNone(github.pr_state(7, _run=_Recorder(out="\n")))

    def test_pr_files_parsed(self):
        self.assertEqual(github.pr_files(7, _run=_Recorder(out="a.py\nb.py\n")), ["a.py", "b.py"])

    def test_pr_files_failsoft(self):
        self.assertIsNone(github.pr_files(7, _run=_Recorder(code=1)))

    def test_commit_files_parsed(self):
        rec = _Recorder(out="src/x.py\n")
        self.assertEqual(github.commit_files("abc", _run=rec), ["src/x.py"])
        self.assertIn("abc", " ".join(rec.calls[0]))

    def test_commit_files_failsoft(self):
        self.assertIsNone(github.commit_files("abc", _run=_Recorder(code=1)))

    def test_pr_merge_window_parsed(self):
        rec = _Recorder(out="2026-07-08T23:02:16Z\t2026-07-10T15:19:57Z\tmain\t7c140f3\n")
        self.assertEqual(
            github.pr_merge_window(543, _run=rec),
            {
                "branched_at": "2026-07-08T23:02:16Z",
                "merged_at": "2026-07-10T15:19:57Z",
                "base": "main",
                "merge_commit": "7c140f3",
            },
        )

    def test_pr_merge_window_none_for_an_unmerged_pr(self):
        # No merge commit -> the check has nothing to verify.
        rec = _Recorder(out="2026-07-08T23:02:16Z\t\tmain\t\n")
        self.assertIsNone(github.pr_merge_window(543, _run=rec))

    def test_pr_merge_window_failsoft(self):
        self.assertIsNone(github.pr_merge_window(543, _run=_Recorder(code=1)))
        self.assertIsNone(github.pr_merge_window(543, _run=_Recorder(out="garbage\n")))

    @staticmethod
    def _merged_page(*rows):
        """`gh pr list --json number,mergedAt` output, newest first."""
        return _Recorder(out=json.dumps([{"number": n, "mergedAt": at} for n, at in rows]))

    def test_prs_merged_between_keeps_only_the_window(self):
        # The window filter moved from --jq into Python (#937): the truncation
        # check below needs the raw timestamps, which --jq had discarded.
        page = self._merged_page((550, "B0"), (546, "A5"), (540, "9x"))

        self.assertEqual(
            [546],
            github.prs_merged_between("main", "A", "B", _run=page),
            "entries outside the half-open window must be dropped",
        )

    def test_prs_merged_between_skips_malformed_rows(self):
        page = _Recorder(
            out=json.dumps(
                [{"number": 546, "mergedAt": "A5"}, {"number": "nope", "mergedAt": "A6"}]
            )
        )

        self.assertEqual([546], github.prs_merged_between("main", "A", "B", _run=page))

    def test_prs_merged_between_failsoft(self):
        self.assertIsNone(github.prs_merged_between("main", "A", "B", _run=_Recorder(code=1)))

    def test_a_truncated_page_reads_as_unreadable_not_as_empty(self):
        """#937, the same rule as #933 through a read that *succeeded*.

        `gh pr list` returns the newest N merges and the window filter runs after
        that cut. On a repo where more than N merged since the window closed,
        none of the window's merges are in the page, the filter matches nothing,
        and an empty list reads as "nothing overtook this merge".
        """
        # A full page whose oldest entry still merged after the window opened:
        # the read never reached back far enough to see the window.
        full_page = self._merged_page(*[(900 + i, f"Z{i:03d}") for i in range(5)])

        self.assertIsNone(
            github.prs_merged_between("main", "A", "B", _run=full_page, limit=5),
            "a page that could not reach the window must not report it empty",
        )

    def test_a_full_page_that_does_reach_the_window_is_trusted(self):
        """The counterweight: `full` alone is not truncation.

        If the oldest entry predates the window, the page spans it, and an empty
        result is a real answer.
        """
        page = self._merged_page(
            (905, "C0"),
            (904, "B5"),
            (903, "A5"),
            (902, "A1"),
            (901, "09"),
        )

        self.assertEqual(
            # Only A5 and A1 fall inside the half-open (A, B) window: B5 sorts
            # after B, C0 after that.
            [903, 902],
            github.prs_merged_between("main", "A", "B", _run=page, limit=5),
        )

    def test_a_short_page_is_never_treated_as_truncated(self):
        page = self._merged_page((550, "Z9"))

        self.assertEqual(
            [],
            github.prs_merged_between("main", "A", "B", _run=page, limit=5),
            "a page shorter than the limit saw everything there was",
        )

    def test_an_empty_page_is_an_answer(self):
        self.assertEqual(
            [],
            github.prs_merged_between("main", "A", "B", _run=_Recorder(out="[]")),
        )

    def test_prs_merged_between_rejects_output_that_is_not_a_json_list(self):
        self.assertIsNone(
            github.prs_merged_between("main", "A", "B", _run=_Recorder(out="garbage"))
        )
        self.assertIsNone(
            github.prs_merged_between("main", "A", "B", _run=_Recorder(out='{"a": 1}'))
        )

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
                "gh",
                "pr",
                "view",
                "7",
                "--json",
                "headRefOid,mergeStateStatus,statusCheckRollup",
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
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--limit",
                "5",
                "--json",
                "number",
                "--search",
                "merged:>=2026-06-01",
            ],
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
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--limit",
                "100",
                "--json",
                "number,headRefName",
            ],
        )

    def test_list_prs_argv_with_head(self):
        rec = _Recorder(out="[]")
        github.list_prs(head="feature/issue-8", limit=5, _run=rec)
        self.assertEqual(
            rec.calls[0],
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--limit",
                "5",
                "--json",
                "number,headRefName",
                "--head",
                "feature/issue-8",
            ],
        )


class TestListBranches(unittest.TestCase):
    def test_list_branches_argv_and_raw_result(self):
        rec = _Recorder(out="main\nfeature/issue-8\norigin/main\n\n")
        result = git.list_branches(_run=rec)
        self.assertEqual(
            rec.calls[0],
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
        )
        # Returns the raw CommandResult (parsing is the caller's) so an error is
        # distinguishable from an empty repo.
        self.assertTrue(result.ok)
        self.assertIn("feature/issue-8", result.output)

    def test_list_branches_error_is_visible(self):
        result = git.list_branches(_run=_Recorder(code=1))
        self.assertFalse(result.ok)


class TestStderrContamination(unittest.TestCase):
    """git can warn on stderr while exiting 0; parsers must read stdout alone (#629).

    The trigger is ordinary: a tag and a branch sharing a name makes git print
    ``warning: refname '<x>' is ambiguous.`` and still succeed.
    """

    WARN = "warning: refname 'feature' is ambiguous.\n"

    def test_merge_base_ignores_stderr_warning(self):
        rec = _Recorder(out=SHA_A + "\n", err=self.WARN)
        self.assertEqual(git.merge_base("feature", "main", _run=rec), SHA_A)

    def test_rev_parse_ignores_stderr_warning(self):
        rec = _Recorder(out=SHA_A + "\n", err=self.WARN)
        self.assertEqual(git.rev_parse("feature", _run=rec), SHA_A)

    def test_merge_base_rejects_a_non_sha(self):
        # Second line of defence: even if stderr ever reached stdout, a warning line
        # is not a SHA and must not be handed on as one.
        rec = _Recorder(out="warning: refname is ambiguous.\n")
        self.assertIsNone(git.merge_base("a", "b", _run=rec))

    def test_changed_files_drops_no_phantom_from_stderr(self):
        rec = _Recorder(out="src/a.py\n", err=self.WARN)
        self.assertEqual(git.changed_files("main", "feature", _run=rec), ["src/a.py"])

    def test_diff_carries_no_stderr_noise(self):
        rec = _Recorder(out="@@ -1 +1 @@\n-a\n+b", err=self.WARN)
        self.assertEqual(git.diff("main", "HEAD", _run=rec), "@@ -1 +1 @@\n-a\n+b")

    def test_command_result_keeps_streams_separate(self):
        from keel.runner import run_argv

        rec = _Recorder(out="payload", err="noise")
        r = run_argv(["git", "x"], _run=rec)
        self.assertEqual(r.stdout, "payload")
        self.assertEqual(r.stderr, "noise")
        self.assertEqual(r.output, "payloadnoise")  # diagnostics still see both


class TestGithubRetry(unittest.TestCase):
    def test_is_transient_error(self):
        from keel.runner import CommandResult

        # ok result is not transient
        self.assertFalse(github.is_transient_error(CommandResult(True, 0, "ok", stdout="ok")))
        # timed out result is transient
        self.assertTrue(
            github.is_transient_error(
                CommandResult(False, 124, "timed out", timed_out=True, stderr="timed out")
            )
        )
        # rate limit in stderr/stdout is transient
        self.assertTrue(
            github.is_transient_error(
                CommandResult(False, 1, "HTTP 429", stderr="HTTP 429: Too Many Requests")
            )
        )
        self.assertTrue(
            github.is_transient_error(
                CommandResult(False, 1, "rate", stderr="secondary rate limit reached")
            )
        )
        self.assertTrue(
            github.is_transient_error(CommandResult(False, 1, "502", stdout="502 Bad Gateway"))
        )
        self.assertTrue(
            github.is_transient_error(
                CommandResult(False, 1, "net", stderr="Could not resolve host: github.com")
            )
        )
        # non-transient error (e.g. 404 not found, validation error)
        self.assertFalse(
            github.is_transient_error(CommandResult(False, 1, "404", stderr="HTTP 404: Not Found"))
        )
        self.assertFalse(
            github.is_transient_error(
                CommandResult(False, 1, "bad flag", stderr="invalid flag --foo")
            )
        )

    def test_run_argv_retry_succeeds_first_attempt(self):
        sleeps = []
        rec = _Recorder(code=0, out="success")
        res = github.run_argv_retry(
            ["gh", "pr", "view"], _run=rec, _sleep=sleeps.append, max_attempts=3
        )
        self.assertTrue(res.ok)
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(sleeps, [])

    def test_run_argv_retry_recovers_on_second_attempt(self):
        sleeps = []
        call_count = [0]

        def flaky_run(argv, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _Proc(1, "", "HTTP 503 Service Unavailable")
            return _Proc(0, "data", "")

        res = github.run_argv_retry(
            ["gh", "api", "foo"],
            _run=flaky_run,
            _sleep=sleeps.append,
            max_attempts=3,
            backoff_factor=1.0,
            jitter=False,
        )
        self.assertTrue(res.ok)
        self.assertEqual(call_count[0], 2)
        self.assertEqual(sleeps, [1.0])

    def test_run_argv_retry_exhausts_attempts(self):
        sleeps = []
        rec = _Recorder(code=1, err="rate limit exceeded")
        res = github.run_argv_retry(
            ["gh", "api", "foo"],
            _run=rec,
            _sleep=sleeps.append,
            max_attempts=3,
            backoff_factor=0.5,
            jitter=True,
        )
        self.assertFalse(res.ok)
        self.assertEqual(len(rec.calls), 3)
        # Attempt 1 -> 0.5 * 1 + 0.1 = 0.6, Attempt 2 -> 0.5 * 2 + 0.1 = 1.1
        self.assertEqual(sleeps, [0.6, 1.1])

    def test_run_argv_retry_no_retry_on_fatal_error(self):
        sleeps = []
        rec = _Recorder(code=1, err="HTTP 404: Not Found")
        res = github.run_argv_retry(
            ["gh", "api", "foo"], _run=rec, _sleep=sleeps.append, max_attempts=3
        )
        self.assertFalse(res.ok)
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(sleeps, [])


class TheRetryIsActuallyWiredToTheReads(unittest.TestCase):
    """#938: `run_argv_retry` was written, tested, and called by nothing.

    Every read went through plain `run_argv`, so one blip failed the whole check.
    Since #936 an unreadable input exits 2 instead of quietly passing, and one
    `verify-merge` run makes 4 + N of these reads — so the loud path fired on
    exactly the busiest days.
    """

    @staticmethod
    def _flaky(first_stderr, then_stdout):
        """Fails once with a transient error, then succeeds."""
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            if len(calls) == 1:
                return _Proc(1, "", first_stderr)
            return _Proc(0, then_stdout, "")

        run.calls = calls
        return run

    def test_pr_files_recovers_from_a_transient_failure(self):
        sleeps = []
        run = self._flaky("HTTP 503 Service Unavailable", "a.py\nb.py\n")

        files = github.pr_files(940, _run=run, _sleep=sleeps.append)

        self.assertEqual(["a.py", "b.py"], files)
        self.assertEqual(2, len(run.calls))
        self.assertEqual(1, len(sleeps))

    def test_commit_files_recovers_from_a_transient_failure(self):
        run = self._flaky("secondary rate limit", "src/keel/cli.py\n")

        self.assertEqual(
            ["src/keel/cli.py"],
            github.commit_files("abc123", _run=run, _sleep=lambda _d: None),
        )

    def test_prs_merged_between_recovers_from_a_transient_failure(self):
        run = self._flaky(
            "connection reset by peer",
            json.dumps([{"number": 810, "mergedAt": "T1a"}, {"number": 811, "mergedAt": "T1b"}]),
        )

        self.assertEqual(
            [810, 811],
            github.prs_merged_between("main", "T1", "T2", _run=run, _sleep=lambda _d: None),
        )

    def test_a_persistent_failure_is_still_unreadable(self):
        # The retry must not become a slower way to fail open: three transient
        # failures is still None, which is still `unknown` and still exit 2.
        rec = _Recorder(code=1, err="rate limit exceeded")

        self.assertIsNone(github.pr_files(940, _run=rec, _sleep=lambda _d: None))
        self.assertEqual(3, len(rec.calls))

    def test_a_fatal_error_is_not_retried(self):
        rec = _Recorder(code=1, err="HTTP 404: Not Found")

        self.assertIsNone(github.pr_files(940, _run=rec, _sleep=lambda _d: None))
        self.assertEqual(1, len(rec.calls), "a 404 is an answer, not a blip")


class AJustMergedPrIsPolledForItsMergeCommit(unittest.TestCase):
    """#938: ship.md says to run the drift check "immediately after a successful
    merge" — the one moment `mergeCommit.oid` is least likely to be populated."""

    ROW = "2026-08-01T00:00:00Z\t2026-08-02T00:00:00Z\tmain\t{sha}"

    def test_the_sha_arriving_on_a_later_read_is_used(self):
        sleeps, calls = [], []

        def run(argv, **kwargs):
            calls.append(argv)
            sha = "deadbeef" if len(calls) >= 2 else ""
            return _Proc(0, self.ROW.format(sha=sha), "")

        window = github.pr_merge_window(940, _run=run, _sleep=sleeps.append)

        self.assertEqual("deadbeef", window["merge_commit"])
        self.assertEqual(2, len(calls))
        self.assertEqual([github.MERGE_COMMIT_POLL_DELAY_S], sleeps)

    def test_the_poll_gives_up_rather_than_hanging(self):
        # Bounded, and giving up is still None — the poll must not become a way to
        # eventually pass. It runs right after an irreversible merge, so a hang
        # would be worse than an `unknown`.
        sleeps, calls = [], []

        def run(argv, **kwargs):
            calls.append(argv)
            return _Proc(0, self.ROW.format(sha=""), "")

        self.assertIsNone(github.pr_merge_window(940, _run=run, _sleep=sleeps.append))
        self.assertEqual(github.MERGE_COMMIT_POLL_ATTEMPTS, len(calls))
        # One fewer sleep than attempts: nothing waits after the last read.
        self.assertEqual(github.MERGE_COMMIT_POLL_ATTEMPTS - 1, len(sleeps))

    def test_an_unmerged_pr_is_answered_at_once(self):
        # Only the settling case waits. An unmerged PR is a real answer, and
        # sleeping on it would make every such call three seconds slower.
        sleeps, calls = [], []

        def run(argv, **kwargs):
            calls.append(argv)
            return _Proc(0, "2026-08-01T00:00:00Z\t\tmain\t", "")

        self.assertIsNone(github.pr_merge_window(940, _run=run, _sleep=sleeps.append))
        self.assertEqual(1, len(calls))
        self.assertEqual([], sleeps)

    def test_an_unreadable_gh_is_answered_at_once(self):
        sleeps = []
        rec = _Recorder(code=1, err="HTTP 404: Not Found")

        self.assertIsNone(github.pr_merge_window(940, _run=rec, _sleep=sleeps.append))
        self.assertEqual(1, len(rec.calls))
        self.assertEqual([], sleeps)

    def test_a_populated_sha_is_returned_without_waiting(self):
        sleeps, calls = [], []

        def run(argv, **kwargs):
            calls.append(argv)
            return _Proc(0, self.ROW.format(sha="cafe1234"), "")

        window = github.pr_merge_window(940, _run=run, _sleep=sleeps.append)

        self.assertEqual("cafe1234", window["merge_commit"])
        self.assertEqual(1, len(calls))
        self.assertEqual([], sleeps)


if __name__ == "__main__":
    unittest.main()
