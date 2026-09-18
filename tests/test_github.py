import json
import subprocess
import unittest
from unittest.mock import MagicMock

from keel import github


def _proc(out: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["gh"], returncode=code, stdout=out, stderr="")


#: One row of REST's closed-pull-request page, in the two fields the window reads.
def _closed_pr(number: int, merged: str, updated: str | None = None) -> dict:
    return {"number": number, "merged_at": merged, "updated_at": updated or merged}


def _window_gh(pages, *, landed="behind"):
    """A `gh` that serves `pulls/<n>` from ``pages`` and answers the reachability check.

    `rest_pr_merge_window` asks two questions per poll — the pull request, then whether
    its `merge_commit_sha` is actually on the base branch — so a fixture that only
    answers the first runs out mid-poll.
    """
    bodies = list(pages)

    def run(argv, **kwargs):
        if any("/compare/" in part for part in argv):
            return _proc(landed)
        return bodies.pop(0) if len(bodies) > 1 else bodies[0]

    return MagicMock(side_effect=run)


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

    def test_merge_pr_pins_the_head_it_is_given(self):
        # GraphQL's spelling of the REST `sha` pin: GitHub refuses if the head is elsewhere.
        mock_runner = MagicMock(return_value=_proc(""))
        github.merge_pr(10, method="squash", head_sha="abc", _run=mock_runner)
        self.assertEqual(
            mock_runner.call_args[0][0],
            ["gh", "pr", "merge", "10", "--squash", "--match-head-commit", "abc"],
        )

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

    def test_label_list_argv_pages_past_ghs_default(self):
        mock_runner = MagicMock(return_value=_proc('[{"name":"bug"}]'))
        res = github.list_labels(repo="owner/repo", _run=mock_runner)
        self.assertTrue(res.ok)
        self.assertEqual(
            mock_runner.call_args[0][0],
            [
                "gh",
                "label",
                "list",
                "--limit",
                str(github.LABEL_PAGE_LIMIT),
                "--json",
                "name",
                "--repo",
                "owner/repo",
            ],
        )

    def test_label_list_without_a_repo_uses_the_working_directory(self):
        self.assertEqual(
            github.label_list_argv(),
            ["gh", "label", "list", "--limit", str(github.LABEL_PAGE_LIMIT), "--json", "name"],
        )

    def test_create_label(self):
        mock_runner = MagicMock(return_value=_proc(""))
        res = github.create_label("status:done", repo="owner/repo", _run=mock_runner)
        self.assertTrue(res.ok)
        self.assertEqual(
            mock_runner.call_args[0][0],
            ["gh", "label", "create", "status:done", "--repo", "owner/repo"],
        )

    def test_create_label_without_a_repo(self):
        self.assertEqual(
            github.label_create_argv("role:core"), ["gh", "label", "create", "role:core"]
        )


class TestRestTransport(unittest.TestCase):
    """The REST readers (#1175) — argv shape, and the two answers that are not obvious."""

    def test_the_probe_asks_the_endpoint_and_nothing_else(self):
        mock = MagicMock(return_value=_proc('{"data":{"__typename":"Query"}}'))
        self.assertTrue(github.graphql_available(_run=mock))
        self.assertEqual(
            mock.call_args[0][0], ["gh", "api", "graphql", "-f", "query=query{__typename}"]
        )
        self.assertFalse(github.graphql_available(_run=MagicMock(return_value=_proc("403", 1))))

    def test_paginated_pages_are_concatenated_documents_not_one(self):
        """`gh api --paginate` emits one JSON document per page, back to back.

        A two-page read is `[…][…]`, which `json.loads` rejects outright — so a reader
        that only tried it saw the second page as a syntax error and reported *no
        checks*, which the merge gate reads as "CI has not run".
        """
        body = '[{"name": "a"}]\n[{"name": "b"}]'
        rows = github.rest_json(github.run_argv(["true"], _run=MagicMock(return_value=_proc(body))))
        self.assertEqual(rows, [{"name": "a"}, {"name": "b"}])

    def test_a_paginated_check_runs_body_is_unwrapped_page_by_page(self):
        """`check-runs` answers with an **object**, and `--paginate` repeats it.

        ``{"total_count": N, "check_runs": [...]}`` per page, concatenated — so a head
        with more than a hundred checks arrives as ``{…}{…}`` and `rest_json` correctly
        reports *two page objects*. Read as a list of check runs, those became two
        entries with no name, no status and no conclusion: neither a failure nor pending,
        so the reducer counted them as checks that had reported and returned **pass**.
        A merge gate saw an all-green rollup for a head whose real checks it never read.
        """

        def page(name: str, conclusion: str) -> str:
            run = {"name": name, "status": "completed", "conclusion": conclusion}
            return json.dumps({"total_count": 1, "check_runs": [run]})

        body = page("a", "success") + page("b", "failure")
        rollup = github.rest_rollup(
            github.rest_json(github.run_argv(["true"], _run=MagicMock(return_value=_proc(body)))),
            None,
        )
        self.assertEqual(
            [(e["name"], e["conclusion"]) for e in rollup], [("a", "SUCCESS"), ("b", "FAILURE")]
        )

    def test_the_rollup_shape_is_case_and_nothing_more(self):
        rollup = github.rest_rollup(
            {
                "check_runs": [
                    {
                        "name": "ci",
                        "status": "in_progress",
                        "conclusion": None,
                        "started_at": "T1",
                        "completed_at": None,
                    }
                ]
            },
            [{"context": "legacy/ci", "state": "success"}],
        )
        self.assertEqual(
            rollup[0],
            {
                "name": "ci",
                "status": "IN_PROGRESS",
                "conclusion": None,
                "startedAt": "T1",
                "completedAt": None,
            },
        )
        # A commit status keeps its `state` and nothing is invented beside it; the
        # timestamps come along because the dedupe orders entries by them.
        self.assertEqual(rollup[1]["context"], "legacy/ci")
        self.assertEqual(rollup[1]["state"], "SUCCESS")

    def test_a_commit_status_is_carried_through_not_translated(self):
        """`state` is what a `StatusContext` carries, and what the reducer reads (#1202).

        #1175 translated it into a `status`/`conclusion` pair here, because the reducer
        looked at neither `state` nor anything else a status owns. That worked on this
        wire and left the two speaking different shapes — and the GraphQL one, which is
        the default nearly every run takes, still scored a **failing** status as a check
        that had reported. The reducer learned the field instead, so this is a copy.
        """
        row = github.rest_rollup(None, [{"context": "jenkins", "state": "failure"}])[0]
        self.assertEqual(row["context"], "jenkins")
        self.assertEqual(row["state"], "FAILURE")
        # No second spelling of the same verdict to drift from.
        self.assertNotIn("conclusion", row)
        self.assertNotIn("status", row)

    def test_a_full_page_that_did_not_reach_past_the_window_is_unreadable(self):
        """The truncation rule, re-derived on REST's only usable sort key.

        REST cannot sort by merge time, so the page comes back ordered by `updated`
        descending. The rule still holds because merging a pull request *updates* it:
        `merged_at <= updated_at` always, so if the oldest row this page reached was
        updated before `since`, every row it did not reach was merged earlier still and
        the window was seen whole. When it was not, the read saw only part of the answer
        — and a partial read must never render as "nothing overtook this merge".
        """
        rows = json.dumps([_closed_pr(1, "2026-06-02T00:00:00Z")])
        self.assertIsNone(
            github.rest_prs_merged_between(
                "main",
                "2026-06-01T00:00:00Z",
                "2026-06-03T00:00:00Z",
                _run=MagicMock(return_value=_proc(rows)),
                limit=1,
            )
        )
        # The same page, read back far enough to have cleared the window, is an answer.
        older = json.dumps(
            [_closed_pr(1, "2026-06-02T00:00:00Z"), _closed_pr(2, "2026-05-01T00:00:00Z")]
        )
        self.assertEqual(
            github.rest_prs_merged_between(
                "main",
                "2026-06-01T00:00:00Z",
                "2026-06-03T00:00:00Z",
                _run=MagicMock(return_value=_proc(older)),
                limit=5,
            ),
            [1],
        )

    def test_the_merge_window_read_waits_for_the_commit_sha_to_settle(self):
        """Merged, but `merge_commit_sha` not yet populated — the same poll GraphQL gets.

        Reading in that instant is how the drift check reports "no merge commit yet" for
        a merge that had just happened.
        """

        def window(sha: str) -> str:
            return json.dumps(
                {
                    "created_at": "T0",
                    "merged_at": "T1",
                    "base": {"ref": "main"},
                    "merge_commit_sha": sha,
                }
            )

        settling, settled = window(""), window("abc")
        mock = _window_gh([_proc(settling), _proc(settled)])
        window = github.rest_pr_merge_window(7, _run=mock, _sleep=lambda _s: None)
        self.assertEqual(window["merge_commit"], "abc")

    def test_a_speculative_test_merge_sha_is_not_the_landed_commit(self):
        """REST fills `merge_commit_sha` while the pull request is still open.

        GitHub documents it as the **speculative test-merge** SHA (`refs/pull/<n>/merge`)
        until the merge lands, when it becomes the commit that actually landed. So
        "merged, and the field is filled" is true on the first post-merge read even while
        the cached test SHA is still being served, and the drift check would judge the
        test merge instead of the squash. GraphQL's `mergeCommit.oid` is null until the
        real commit exists, which is why the same poll is correct there.
        """
        merged = json.dumps(
            {
                "created_at": "T0",
                "merged_at": "T1",
                "base": {"ref": "main"},
                "merge_commit_sha": "testsha",
            }
        )
        # The SHA is on no branch: `compare` says the two have diverged.
        self.assertIsNone(
            github.rest_pr_merge_window(
                7, _run=_window_gh([_proc(merged)], landed="diverged"), _sleep=lambda _s: None
            )
        )
        # The same body, once that SHA is reachable from the base branch, is an answer.
        window = github.rest_pr_merge_window(
            7, _run=_window_gh([_proc(merged)], landed="behind"), _sleep=lambda _s: None
        )
        self.assertEqual(window["merge_commit"], "testsha")

    def test_the_rest_merge_names_its_method_and_its_pin(self):
        mock = MagicMock(return_value=_proc('{"merged": true}'))
        github.rest_merge_pr(7, method="squash", head_sha="abc", _run=mock)
        argv = mock.call_args[0][0]
        self.assertEqual(argv[:5], ["gh", "api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/merge"])
        self.assertIn("merge_method=squash", argv)
        self.assertIn("sha=abc", argv)
        # No head to pin means no pin sent, rather than an empty one the API rejects.
        mock.reset_mock()
        github.rest_merge_pr(7, method="squash", head_sha=None, _run=mock)
        self.assertFalse([a for a in mock.call_args[0][0] if a.startswith("sha=")])


class TestRestTransportDegradesHonestly(unittest.TestCase):
    """Every unreadable shape becomes ``None``, never a confident wrong answer.

    These readers sit under a merge gate and a post-merge drift check, so the cost of
    guessing is a green light: an empty rollup reads as "CI has not run", an empty
    overtaking set reads as "nothing overtook this merge".
    """

    def _json(self, out: str = "", code: int = 0):
        return github.rest_json(
            github.run_argv(["true"], _run=MagicMock(return_value=_proc(out, code)))
        )

    def test_a_failed_call_an_empty_body_and_a_broken_second_page_are_all_none(self):
        self.assertIsNone(self._json("[]", 1))
        self.assertIsNone(self._json("   "))
        # A first page that parses and a second that does not: the read is partial, and
        # a partial page of check runs is not a shorter list of check runs.
        self.assertIsNone(self._json('[{"name": "a"}] {"broken"'))

    def test_a_single_object_page_is_returned_as_the_object(self):
        self.assertEqual(self._json('{"head": {"sha": "abc"}}'), {"head": {"sha": "abc"}})
        # Two object pages are neither one object nor a list of rows, so both are kept
        # rather than silently reporting the first as the whole answer.
        self.assertEqual(self._json('{"a": 1}{"b": 2}'), [{"a": 1}, {"b": 2}])

    def test_the_rollup_ignores_shapes_it_does_not_recognise(self):
        # A payload that is neither the documented object nor a list contributes
        # nothing, and a stray non-object row inside one is skipped rather than
        # crashing the merge gate on a shape GitHub has not documented.
        self.assertEqual(github.rest_rollup("nonsense", None), [])
        self.assertEqual(github.rest_rollup({"check_runs": "nope"}, None), [])
        self.assertEqual(github.rest_rollup(["nope", None], ["nope"]), [])
        # A bare list of run objects is not a shape this endpoint returns, and reading
        # one would be guessing at a payload GitHub has not documented.
        self.assertEqual(github.rest_rollup([{"name": "a", "status": "completed"}], None), [])

    def test_an_unreadable_window_and_an_unreadable_page_are_none(self):
        self.assertIsNone(github.rest_pr_merge_window(7, _run=_window_gh([_proc("[]")])))
        # A pull request that is not merged has no window worth reporting either.
        self.assertIsNone(
            github.rest_pr_merge_window(
                7, _run=MagicMock(return_value=_proc('{"created_at": "", "merged_at": ""}'))
            )
        )
        self.assertIsNone(
            github.rest_prs_merged_between(
                "main", "A", "B", _run=MagicMock(return_value=_proc('{"not": "a list"}'))
            )
        )

    def test_an_unmerged_pull_request_has_no_window(self):
        """All four fields, exactly as the GraphQL reader requires.

        REST answers an unmerged pull request with `created_at` and `base.ref` and a null
        `merged_at`, so "any field present" reported a window for one — and a caller that
        supplied `--merge-sha` then judged drift on a merge that had not happened, where
        the same call over GraphQL answers `unknown`.
        """
        unmerged = json.dumps(
            {
                "created_at": "T0",
                "merged_at": None,
                "base": {"ref": "main"},
                "merge_commit_sha": None,
            }
        )
        self.assertIsNone(github.rest_pr_merge_window(7, _run=_window_gh([_proc(unmerged)])))

    def test_a_window_that_never_settles_returns_nothing(self):
        settling = json.dumps(
            {
                "created_at": "T0",
                "merged_at": "T1",
                "base": {"ref": "main"},
                "merge_commit_sha": "",
            }
        )
        mock = _window_gh([_proc(settling)])
        # A merge whose commit never appears is not a merge this check can read, so it
        # is `None` and the caller says "no merge commit yet" — but it says so *after*
        # the poll, not instead of it.
        self.assertIsNone(github.rest_pr_merge_window(7, _run=mock, _sleep=lambda _s: None))
        self.assertEqual(
            sum(1 for c in mock.call_args_list if "/compare/" not in " ".join(c[0][0])),
            github.MERGE_COMMIT_POLL_ATTEMPTS,
        )


if __name__ == "__main__":
    unittest.main()
