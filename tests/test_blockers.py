"""Tests for stated-blocker detection on open PRs (#1512)."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
import unittest.mock as mock

from keel import blockers, cli
from keel.blockers import ABANDONED, CLEARED, ISSUE, PENDING, TAG, UNKNOWN, Blocker
from keel.runner import CommandResult


def _ok(out=""):
    return CommandResult(True, 0, out, stdout=out)


def _fail(err="boom"):
    return CommandResult(False, 1, err, stderr=err)


def issues(body):
    return [b.ref for b in blockers.parse_blockers(body) if b.kind == ISSUE]


class ParseTests(unittest.TestCase):
    def test_phrase_forms(self):
        for body in (
            "Blocked by #12",
            "depends on #12",
            "Depends on: PR #12",
            "waiting on issue #12",
            "blocked until #12",
            "Do not merge before #12",
            "don't merge until #12",
            "must not merge prior to #12",
        ):
            self.assertEqual(["12"], issues(body), body)

    def test_tag_forms(self):
        self.assertEqual(
            (Blocker(TAG, "v1.2.0"),), blockers.parse_blockers("Do not merge before v1.2.0.")
        )
        self.assertEqual(
            (Blocker(TAG, "1.2.0"),), blockers.parse_blockers("do not merge until `1.2.0`")
        )

    def test_v_prefixed_tag(self):
        self.assertEqual((Blocker(TAG, "v2"),), blockers.parse_blockers("do not merge before v2"))

    def test_multiple_and_dedupe(self):
        got = blockers.parse_blockers(
            "Blocked by #1, #2 and #3. Depends on #2. Do not merge before v9.0"
        )
        self.assertEqual(
            (Blocker(ISSUE, "1"), Blocker(ISSUE, "2"), Blocker(ISSUE, "3"), Blocker(TAG, "v9.0")),
            got,
        )

    def test_false_positives(self):
        for body in (
            None,
            "",
            "unblocked by #5",
            "This is not blocked by #5",
            "no longer blocked by #5",
            "Do not merge before release",
            "do not merge before 5pm",
            "do not merge until 2026-10-15",
            "do not merge before 3 reviewers approve",
            "do not merge until 10.30",
            "do not merge for 3.5 hours",
            "Upgrade dependency #123",
            "no longer do not merge before #5",
            "depends on owner/repo#5",
            "blocked by #",
            "no dependencies",
        ):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_code_and_comments_are_not_claims(self):
        body = (
            "```\nblocked by #1\n```\n"
            "~~~\ndo not merge before #2\n~~~\n"
            "<!-- depends on #3 -->\n"
            "Use `blocked by #4` in a body.\n"
            "```\nunterminated fence blocked by #5"
        )
        self.assertEqual((), blockers.parse_blockers(body))

    def test_inline_code_quoting_a_phrase_is_not_a_claim(self):
        for body in ("Use `do not merge before #2` in a body.", "Say `depends on #3` here."):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_list_grammar(self):
        want = ["1", "2", "3"]
        for body in (
            "Blocked by #1, #2, and #3",
            "Blocked by #1, #2 and #3",
            "Blocked by #1, #2, or #3",
            "Blocked by #1 or #2 or #3",
            "Blocked by #1 / #2 / #3",
            "Blocked by #1 & #2 & #3",
            "Blocked by #1 + #2 + #3",
            "Blocked by #1,#2,#3",
            "Blocked by PR #1, PR #2, issue #3",
            "Blocked by PR#1, PR#2, issue#3",
            "Blocked by pull request #1 and pull request #2 and #3",
            "Depends on:\n- #1\n- #2\n* #3\n\nSomething else",
            "Depends on: - #1\n+ #2\n- PR #3",
            "Depends on `#1`, `#2`, `#3`",
        ):
            got = issues(body)
            self.assertEqual(want, got, body)
            self.assertFalse(blockers.scan_blockers(body)[1], body)

    def test_markdown_wrapped_refs(self):
        for body in (
            "Blocked by [#12](https://github.com/o/r/issues/12)",
            "Blocked by **#12**",
            "**Blocked by:** #12",
            "Blocked by _#12_",
            "Blocked by *#12*",
            "Blocked by __#12__",
            "Blocked by [#12](u), [#13](v) and **#14**",
            "Blocked by [PR #12](u)",
        ):
            got = blockers.scan_blockers(body)
            self.assertEqual(Blocker(ISSUE, "12"), got[0][0], body)
            self.assertFalse(got[1], body)
        self.assertEqual(["12", "13", "14"], issues("Blocked by [#12](u), [#13](v) and **#14**"))

    def test_unreadable_reference_is_incomplete(self):
        for body in (
            "Blocked by [the auth work](https://github.com/o/r/issues/12)",
            "Dependencies: #1, #2 -- blocked by o/r#12",
            "Blocked by <https://github.com/o/r/pull/12>",
        ):
            blockers_, incomplete = blockers.scan_blockers(body)
            self.assertTrue(incomplete, body)

    def test_curly_apostrophe_and_until_after(self):
        self.assertEqual(["5"], issues("Don\u2019t merge before #5"))
        self.assertEqual(["5"], issues("do not merge until after #5"))
        self.assertEqual(["5"], issues("Do not merge prior to #5"))

    def test_historical_phrasing_is_not_a_live_blocker(self):
        for body in (
            "previously blocked by #5",
            "was blocked by #5, now merged",
            "formerly depends on #5",
        ):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_dates_are_not_tags(self):
        self.assertEqual((), blockers.parse_blockers("do not merge until 2026.10.15"))

    def test_before_forms_accept_pr_prefix(self):
        self.assertEqual(["12"], issues("do not merge before PR #12"))

    def test_noun_dependency_needs_a_label(self):
        self.assertEqual(((), False), blockers.scan_blockers("Upgrade dependency #123"))
        self.assertEqual(["7"], issues("Dependency: #7"))

    def test_incomplete_when_a_reference_is_not_captured(self):
        for body in (
            "Blocked by #1 and owner/repo#9",
            "Blocked by #1, #2, and https://github.com/o/r/issues/9",
            "Blocked by #1;\n- see owner/r#9",
            "Depends on:\n- #1\n- owner/r#9",
            "depends on owner/repo#5",
            "blocked by https://github.com/o/r/pull/5",
            "do not merge before #1 and #2",
            "do not merge before v1.2 / #4",
        ):
            self.assertTrue(blockers.scan_blockers(body)[1], body)

    def test_not_incomplete_when_nothing_is_dropped(self):
        for body in ("Blocked by #1, and the auth work", "Blocked by #1.\nSee #9 for context", ""):
            self.assertFalse(blockers.scan_blockers(body)[1], body)

    def test_backticked_ref_outside_phrase_is_kept(self):
        self.assertEqual(["7"], issues("blocked by `#7`"))


class ResolveTests(unittest.TestCase):
    def resolve(self, blocker, result):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return result

        with mock.patch("keel.blockers.run_argv", lambda argv, **kw: run(argv, **kw)):
            return blockers.resolve_blocker(blocker), calls

    def resolve_tag_404(self, blocker, *, repo):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return repo if argv[2] == "repos/{owner}/{repo}" else _fail("gh: Not Found (HTTP 404)")

        with mock.patch("keel.blockers.run_argv", run):
            return blockers.resolve_blocker(blocker), calls

    def test_issue_states(self):
        b = Blocker(ISSUE, "9")
        cases = {
            "closed completed issue": ("closed completed issue", CLEARED),
            "closed, no reason (old issue)": ("closed - issue", CLEARED),
            "closed not_planned": ("closed not_planned issue", ABANDONED),
            "closed duplicate": ("closed duplicate issue", ABANDONED),
            "merged PR": ("closed completed 2026-10-01T00:00:00Z", CLEARED),
            "merged PR, no reason": ("closed - 2026-10-01T00:00:00Z", CLEARED),
            "PR closed unmerged": ("closed - unmerged", ABANDONED),
            "open issue": ("open - issue", PENDING),
            "open PR": ("open - unmerged", PENDING),
            "odd state": ("locked - issue", UNKNOWN),
            "empty": ("", UNKNOWN),
            "short": ("closed", UNKNOWN),
        }
        for name, (out, want) in cases.items():
            self.assertEqual(want, self.resolve(b, _ok(out + "\n"))[0], name)
        self.assertEqual(UNKNOWN, self.resolve(b, _fail("HTTP 404 Not Found"))[0])
        _, calls = self.resolve(b, _ok("closed - issue"))
        self.assertIn("repos/{owner}/{repo}/issues/9", calls[0])
        self.assertIn("merged_at", calls[0][-1])

    def test_tag_states(self):
        b = Blocker(TAG, "v1")
        state, calls = self.resolve(b, _ok("refs/tags/v1"))
        self.assertEqual(CLEARED, state)
        self.assertIn("repos/{owner}/{repo}/git/ref/tags/v1", calls[0])
        self.assertEqual(UNKNOWN, self.resolve(b, _ok(""))[0])
        # a 404 on the tag alone: the repository answers, so the tag is simply not there
        self.assertEqual(PENDING, self.resolve_tag_404(b, repo=_ok("123"))[0])
        # a 404 that the repository probe shares (private repo, no auth): unknown, not pending
        self.assertEqual(UNKNOWN, self.resolve_tag_404(b, repo=_fail("HTTP 404"))[0])
        self.assertEqual(UNKNOWN, self.resolve_tag_404(b, repo=_ok(""))[0])
        _, calls = self.resolve_tag_404(b, repo=_ok("1"))
        self.assertEqual(["gh", "api", "repos/{owner}/{repo}", "--jq", ".id"], calls[1])
        for err in ("gh: command not found", "repository not found", "Could not resolve host"):
            self.assertEqual(UNKNOWN, self.resolve(b, _fail(err))[0], err)

    def test_read_failure_is_never_cleared(self):
        for b in (Blocker(ISSUE, "9"), Blocker(TAG, "v1")):
            for res in (
                _fail("could not resolve host"),
                _fail(""),
                CommandResult(False, 127, "", spawn_failed=True),
            ):
                self.assertEqual(UNKNOWN, self.resolve(b, res)[0])

    def test_run_injection(self):
        seen = []

        def fake(argv, **kw):
            seen.append(kw.get("_run"))
            return _ok("open")

        sentinel = object()
        with mock.patch("keel.blockers.run_argv", fake):
            blockers.resolve_blocker(Blocker(ISSUE, "1"), _run=sentinel)
            blockers.resolve_blocker(Blocker(ISSUE, "1"))
        self.assertEqual([sentinel, None], seen)


def _pr(number, body, draft=False, title="t"):
    return {"number": number, "title": title, "url": f"u/{number}", "isDraft": draft, "body": body}


C = "closed completed issue"
OPEN = "open - issue"


def _router(prs, issue_states=None, tags=None, list_result=None, cwds=None):
    issue_states = issue_states or {}
    tags = tags or {}

    def run(argv, **kw):
        if cwds is not None:
            cwds.append(kw.get("cwd"))
        if argv[:3] == ["gh", "pr", "list"]:
            return list_result if list_result is not None else _ok(json.dumps(prs))
        if argv[2] == "repos/{owner}/{repo}":
            return _ok("1")
        if "git/ref/tags" in argv[2]:
            name = argv[2].split("git/ref/tags/", 1)[1]
            res = tags.get(name, "fail")
            return _ok(f"refs/tags/{name}") if res == "yes" else _fail("HTTP 404 Not Found")
        num = argv[2].rsplit("/", 1)[1]
        state = issue_states.get(num)
        return _ok(state) if state else _fail("network down")

    return run


class StaleBlockersTests(unittest.TestCase):
    def report(self, **kw):
        with mock.patch("keel.blockers.run_argv", _router(**kw)):
            return blockers.stale_blockers()

    def test_flags_cleared_and_orders(self):
        prs = [
            _pr(30, "Blocked by #1 and #2", draft=True),
            _pr(10, "Do not merge before v2.0. Blocked by #1"),
            _pr(20, "Blocked by #2"),
            _pr(40, "no blocker here"),
            _pr(50, "Blocked by #50 and #99"),
            _pr(60, None),
        ]
        rep = self.report(
            prs=prs, issue_states={"1": C, "2": OPEN, "99": OPEN}, tags={"v2.0": "yes"}
        )
        self.assertTrue(rep["ok"])
        self.assertEqual([10, 30], [p["number"] for p in rep["prs"]])
        ten, thirty = rep["prs"]
        self.assertTrue(ten["all_cleared"])
        self.assertFalse(thirty["all_cleared"])
        self.assertTrue(thirty["draft"])
        self.assertEqual(
            [("1", CLEARED), ("2", PENDING)], [(e["ref"], e["state"]) for e in thirty["blockers"]]
        )

    def test_unreadable_blocker_is_not_cleared(self):
        rep = self.report(prs=[_pr(5, "Blocked by #77")], issue_states={})
        self.assertEqual([], rep["prs"])
        rep = self.report(prs=[_pr(5, "Blocked by #77 and #1")], issue_states={"1": C})
        self.assertEqual(UNKNOWN, rep["prs"][0]["blockers"][0]["state"])
        self.assertFalse(rep["prs"][0]["all_cleared"])

    def test_abandoned_is_reported_never_cleared(self):
        rep = self.report(
            prs=[_pr(5, "Blocked by #1 and #2"), _pr(6, "Blocked by #2")],
            issue_states={"1": C, "2": "closed - unmerged"},
        )
        self.assertEqual([5, 6], [p["number"] for p in rep["prs"]])
        self.assertEqual([CLEARED, ABANDONED], [e["state"] for e in rep["prs"][0]["blockers"]])
        self.assertFalse(rep["prs"][0]["all_cleared"])
        self.assertFalse(rep["prs"][1]["all_cleared"])

    def test_self_reference_is_dropped(self):
        rep = self.report(prs=[_pr(50, "Blocked by #50 and #1")], issue_states={"1": C})
        self.assertEqual(
            [("1", CLEARED)], [(e["ref"], e["state"]) for e in rep["prs"][0]["blockers"]]
        )
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_oxford_list_is_not_certified_while_the_third_is_open(self):
        rep = self.report(
            prs=[_pr(5, "Blocked by #1, #2, and #3")], issue_states={"1": C, "2": C, "3": OPEN}
        )
        self.assertEqual(["1", "2", "3"], [e["ref"] for e in rep["prs"][0]["blockers"]])
        self.assertFalse(rep["prs"][0]["all_cleared"])

    def test_incomplete_list_is_never_all_cleared(self):
        rep = self.report(prs=[_pr(5, "Blocked by #1 and owner/repo#9")], issue_states={"1": C})
        self.assertTrue(rep["prs"][0]["incomplete"])
        self.assertFalse(rep["prs"][0]["all_cleared"])
        rep = self.report(prs=[_pr(6, "Blocked by #1")], issue_states={"1": C})
        self.assertFalse(rep["prs"][0]["incomplete"])
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_pr_with_only_unreadable_refs_is_reported(self):
        rep = self.report(
            prs=[
                _pr(5, "Blocked by [the auth work](https://github.com/o/r/issues/12)"),
                _pr(6, "Blocked by owner/repo#9"),
                _pr(7, "Blocked by #7 and owner/repo#9"),
                _pr(8, "Upgrade dependency #3"),
            ],
        )
        self.assertEqual([5, 6, 7], [p["number"] for p in rep["prs"]])
        for pr in rep["prs"]:
            self.assertEqual([], pr["blockers"])
            self.assertTrue(pr["incomplete"])
            self.assertFalse(pr["all_cleared"])

    def test_readable_markdown_ref_resolves(self):
        rep = self.report(prs=[_pr(5, "Blocked by [#1](u)")], issue_states={"1": C})
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_slash_tag(self):
        rep = self.report(prs=[_pr(5, "do not merge before v1/rc2")], tags={"v1/rc2": "yes"})
        self.assertEqual(CLEARED, rep["prs"][0]["blockers"][0]["state"])

    def test_truncation_is_flagged(self):
        with mock.patch("keel.blockers.run_argv", _router(prs=[_pr(1, "x"), _pr(2, "y")])):
            self.assertTrue(blockers.stale_blockers(limit=2)["truncated"])
            self.assertFalse(blockers.stale_blockers(limit=3)["truncated"])

    def test_malformed_rows_are_skipped(self):
        rows = [
            1,
            None,
            {"title": "no number"},
            {"number": "7", "body": "Blocked by #1"},
            _pr(8, "Blocked by #1"),
            {"number": 9, "body": 5},
        ]
        rep = self.report(prs=rows, issue_states={"1": C})
        self.assertEqual([8], [p["number"] for p in rep["prs"]])

    def test_pr_list_unreadable(self):
        for res in (_fail(), _ok("not json"), _ok('{"a": 1}')):
            self.assertFalse(self.report(prs=[], list_result=res)["ok"])

    def test_pr_list_empty_output_is_empty_list(self):
        rep = self.report(prs=[], list_result=_ok(""))
        self.assertTrue(rep["ok"])
        self.assertEqual([], rep["prs"])

    def test_run_injection_to_pr_list(self):
        seen = []

        def fake(argv, **kw):
            seen.append(kw.get("_run"))
            return _ok("[]")

        sentinel = object()
        with mock.patch("keel.blockers.run_argv", fake):
            blockers.stale_blockers(_run=sentinel)
        self.assertEqual([sentinel], seen)


class RenderAndCliTests(unittest.TestCase):
    def test_render(self):
        self.assertIn(
            "could not",
            blockers.render({"ok": False, "error": "could not", "truncated": False, "prs": []}),
        )
        self.assertEqual(
            "No open PRs with a cleared or abandoned blocker.",
            blockers.render({"ok": True, "error": None, "truncated": False, "prs": []}),
        )
        out = blockers.render(
            {
                "ok": True,
                "error": None,
                "truncated": True,
                "prs": [
                    {
                        "number": 3,
                        "title": "x",
                        "draft": True,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [
                            {"kind": ISSUE, "ref": "1", "state": CLEARED},
                            {"kind": TAG, "ref": "v1", "state": PENDING},
                        ],
                    },
                    {
                        "number": 5,
                        "title": "z",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": True,
                        "blockers": [{"kind": ISSUE, "ref": "3", "state": CLEARED}],
                    },
                    {
                        "number": 4,
                        "title": "y",
                        "draft": False,
                        "all_cleared": True,
                        "incomplete": False,
                        "blockers": [{"kind": ISSUE, "ref": "2", "state": CLEARED}],
                    },
                ],
            }
        )
        self.assertIn("#3 (draft) x — some blockers cleared or abandoned", out)
        self.assertIn("  - #1: cleared", out)
        self.assertIn("  - v1: pending", out)
        self.assertIn("#4 y — all blockers cleared", out)
        out2 = blockers.render(
            {
                "ok": True,
                "error": None,
                "truncated": False,
                "prs": [
                    {
                        "number": 6,
                        "title": "a",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": True,
                        "blockers": [],
                    },
                    {
                        "number": 7,
                        "title": "b",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [{"kind": ISSUE, "ref": "1", "state": ABANDONED}],
                    },
                ],
            }
        )
        self.assertIn("#6 a — blocker reference could not be read", out2)
        self.assertIn("#7 b — blocker abandoned, none cleared", out2)
        self.assertIn("#5 z — blocker list may be incomplete", out)
        self.assertIn("raise --limit", out)

    def run_cli(self, argv, router):
        buf = io.StringIO()
        with mock.patch("keel.blockers.run_argv", router), contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_cli_text_and_json(self):
        router = _router(prs=[_pr(8, "Blocked by #1")], issue_states={"1": C})
        code, out = self.run_cli(["stale-blockers"], router)
        self.assertEqual(0, code)
        self.assertIn("#8 t — all blockers cleared", out)
        code, out = self.run_cli(["stale-blockers", "--json", "--limit", "5"], router)
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual(8, data["prs"][0]["number"])

    def test_cli_passes_root_as_cwd_and_rejects_bad_limit(self):
        cwds = []
        router = _router(prs=[_pr(8, "Blocked by #1")], issue_states={"1": C}, cwds=cwds)
        self.assertEqual(0, self.run_cli(["stale-blockers", "--root", "/some/dir"], router)[0])
        self.assertEqual({"/some/dir"}, set(cwds))
        for bad in ("0", "-3"):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as ctx,
            ):
                cli.main(["stale-blockers", "--limit", bad])
            self.assertEqual(2, ctx.exception.code)

    def test_cli_failure_exit(self):
        code, out = self.run_cli(["stale-blockers"], _router(prs=[], list_result=_fail()))
        self.assertEqual(1, code)
        self.assertIn("could not list", out)


if __name__ == "__main__":
    unittest.main()
