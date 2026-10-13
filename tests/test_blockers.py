"""Tests for stated-blocker detection on open PRs (#1512)."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from keel import blockers, cli
from keel.blockers import CLEARED, ISSUE, PENDING, TAG, UNKNOWN, Blocker
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
            (Blocker(TAG, "1.2"),), blockers.parse_blockers("do not merge until `1.2`")
        )

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

    def test_backticked_ref_outside_phrase_is_kept(self):
        self.assertEqual(["7"], issues("blocked by `#7`"))


class ResolveTests(unittest.TestCase):
    def resolve(self, blocker, result):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return result

        with patch("keel.blockers.run_argv", lambda argv, **kw: run(argv, **kw)):
            return blockers.resolve_blocker(blocker), calls

    def test_issue_states(self):
        b = Blocker(ISSUE, "9")
        self.assertEqual(CLEARED, self.resolve(b, _ok("closed\n"))[0])
        self.assertEqual(CLEARED, self.resolve(b, _ok("MERGED"))[0])
        self.assertEqual(PENDING, self.resolve(b, _ok("open"))[0])
        self.assertEqual(UNKNOWN, self.resolve(b, _ok("weird"))[0])
        self.assertEqual(UNKNOWN, self.resolve(b, _fail("HTTP 404 Not Found"))[0])
        state, calls = self.resolve(b, _ok("closed"))
        self.assertIn("repos/{owner}/{repo}/issues/9", calls[0])

    def test_tag_states(self):
        b = Blocker(TAG, "v1")
        state, calls = self.resolve(b, _ok("refs/tags/v1"))
        self.assertEqual(CLEARED, state)
        self.assertIn("repos/{owner}/{repo}/git/ref/tags/v1", calls[0])
        self.assertEqual(UNKNOWN, self.resolve(b, _ok(""))[0])
        self.assertEqual(PENDING, self.resolve(b, _fail("gh: Not Found (HTTP 404)"))[0])
        self.assertEqual(PENDING, self.resolve(b, _fail("gh: not found"))[0])

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
        with patch("keel.blockers.run_argv", fake):
            blockers.resolve_blocker(Blocker(ISSUE, "1"), _run=sentinel)
            blockers.resolve_blocker(Blocker(ISSUE, "1"))
        self.assertEqual([sentinel, None], seen)


def _pr(number, body, draft=False, title="t"):
    return {"number": number, "title": title, "url": f"u/{number}", "isDraft": draft, "body": body}


def _router(prs, issue_states=None, tags=None, list_result=None):
    issue_states = issue_states or {}
    tags = tags or {}

    def run(argv, **kw):
        if argv[:3] == ["gh", "pr", "list"]:
            return list_result if list_result is not None else _ok(json.dumps(prs))
        if "git/ref/tags" in argv[2]:
            name = argv[2].rsplit("/", 1)[1]
            res = tags.get(name, "fail")
            return _ok(f"refs/tags/{name}") if res == "yes" else _fail("HTTP 404 Not Found")
        num = argv[2].rsplit("/", 1)[1]
        state = issue_states.get(num)
        return _ok(state) if state else _fail("network down")

    return run


class StaleBlockersTests(unittest.TestCase):
    def report(self, **kw):
        with patch("keel.blockers.run_argv", _router(**kw)):
            return blockers.stale_blockers()

    def test_flags_cleared_and_orders(self):
        prs = [
            _pr(30, "Blocked by #1 and #2", draft=True),
            _pr(10, "Do not merge before v2.0 / blocked by #1"),
            _pr(20, "Blocked by #2"),
            _pr(40, "no blocker here"),
            _pr(50, "Blocked by #50 and #99"),
            _pr(60, None),
        ]
        rep = self.report(
            prs=prs, issue_states={"1": "closed", "2": "open", "99": "open"}, tags={"v2.0": "yes"}
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
        rep = self.report(prs=[_pr(5, "Blocked by #77 and #1")], issue_states={"1": "closed"})
        self.assertEqual(UNKNOWN, rep["prs"][0]["blockers"][0]["state"])
        self.assertFalse(rep["prs"][0]["all_cleared"])

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
        with patch("keel.blockers.run_argv", fake):
            blockers.stale_blockers(_run=sentinel)
        self.assertEqual([sentinel], seen)


class RenderAndCliTests(unittest.TestCase):
    def test_render(self):
        self.assertIn("could not", blockers.render({"ok": False, "error": "could not", "prs": []}))
        self.assertEqual(
            "No open PRs with a cleared blocker.",
            blockers.render({"ok": True, "error": None, "prs": []}),
        )
        out = blockers.render(
            {
                "ok": True,
                "error": None,
                "prs": [
                    {
                        "number": 3,
                        "title": "x",
                        "draft": True,
                        "all_cleared": False,
                        "blockers": [
                            {"kind": ISSUE, "ref": "1", "state": CLEARED},
                            {"kind": TAG, "ref": "v1", "state": PENDING},
                        ],
                    },
                    {
                        "number": 4,
                        "title": "y",
                        "draft": False,
                        "all_cleared": True,
                        "blockers": [{"kind": ISSUE, "ref": "2", "state": CLEARED}],
                    },
                ],
            }
        )
        self.assertIn("#3 (draft) x — some blockers cleared", out)
        self.assertIn("  - #1: cleared", out)
        self.assertIn("  - v1: pending", out)
        self.assertIn("#4 y — all blockers cleared", out)

    def run_cli(self, argv, router):
        buf = io.StringIO()
        with patch("keel.blockers.run_argv", router), contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_cli_text_and_json(self):
        router = _router(prs=[_pr(8, "Blocked by #1")], issue_states={"1": "closed"})
        code, out = self.run_cli(["stale-blockers"], router)
        self.assertEqual(0, code)
        self.assertIn("#8 t — all blockers cleared", out)
        code, out = self.run_cli(["stale-blockers", "--json", "--limit", "5"], router)
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual(8, data["prs"][0]["number"])

    def test_cli_failure_exit(self):
        code, out = self.run_cli(["stale-blockers"], _router(prs=[], list_result=_fail()))
        self.assertEqual(1, code)
        self.assertIn("could not list", out)


if __name__ == "__main__":
    unittest.main()
