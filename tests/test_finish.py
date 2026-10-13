"""`keel finish` (#1505): the pure decision helpers, the bounded CI wait and the step chain.

The sub-commands (`review`, `ship`, `merge`) are not re-tested here: they keep their own
tests and their own refusals. What is pinned is that finish *calls* them, in order, stops at
the first refusal, never adds a bypass, and reports a CI timeout as a timeout.
"""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from keel import cli, finish, github


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _ci(state, failing=(), pending=()):
    return {"state": state, "failing_checks": list(failing), "pending_checks": list(pending)}


class TestPlanSteps(unittest.TestCase):
    def test_reviews_come_first_and_only_when_supplied(self):
        with_reviews = [s["name"] for s in finish.plan_steps(reviews=True)]
        without = [s["name"] for s in finish.plan_steps(reviews=False)]
        self.assertEqual(with_reviews, ["review", "ci", "window", "ship", "merge"])
        self.assertEqual(without, ["ci", "window", "ship", "merge"])

    def test_rerun_names_are_listed_on_the_ci_step_only(self):
        steps = finish.plan_steps(reviews=False, rerun=("lint",))
        self.assertEqual(steps[0]["rerun_flaky"], ["lint"])
        self.assertNotIn("rerun_flaky", steps[1])
        self.assertNotIn("rerun_flaky", finish.plan_steps(reviews=False)[0])


class TestTimeoutAccounting(unittest.TestCase):
    def test_sleep_is_the_interval_until_the_deadline_is_near(self):
        self.assertEqual(finish.next_sleep(30, 1800, 0), 30)
        self.assertEqual(finish.next_sleep(30, 1800, 1790), 10)

    def test_sleep_is_never_negative(self):
        self.assertEqual(finish.next_sleep(30, 100, 500), 0.0)

    def test_poll_cap_exceeds_the_polls_a_healthy_clock_needs(self):
        self.assertEqual(finish.poll_cap(100, 30), 4 + 3)
        self.assertEqual(finish.poll_cap(90, 30), 3 + 3)

    def test_read_failures_exhaust_at_the_limit(self):
        self.assertFalse(finish.read_failures_exhausted(finish.MAX_READ_FAILURES - 1))
        self.assertTrue(finish.read_failures_exhausted(finish.MAX_READ_FAILURES))


class TestCiDecision(unittest.TestCase):
    def decide(self, ci, **kw):
        kw.setdefault("elapsed", 0)
        kw.setdefault("timeout", 100)
        return finish.ci_decision(ci, **kw)

    def test_pass_and_no_checks_proceed(self):
        self.assertEqual(self.decide(_ci("pass"))["action"], "proceed")
        self.assertEqual(self.decide(_ci("no-checks"))["action"], "proceed")

    def test_pending_waits_until_the_timeout_then_times_out(self):
        waiting = self.decide(_ci("pending", pending=["a"]), elapsed=99)
        self.assertEqual((waiting["action"], waiting["names"]), ("wait", ["a"]))
        at = self.decide(_ci("pending", pending=["a"]), elapsed=100)
        self.assertEqual((at["action"], at["names"]), ("timeout", ["a"]))

    def test_a_timeout_is_never_a_proceed_however_late(self):
        late = self.decide(_ci("pending", pending=["a"]), elapsed=10**6)
        self.assertEqual(late["action"], "timeout")

    def test_an_unrecognised_state_is_waited_on_not_passed(self):
        self.assertEqual(self.decide(None)["action"], "wait")
        self.assertEqual(self.decide({"state": "weird"})["action"], "wait")

    def test_a_failure_fails_without_waiting_for_the_rest(self):
        got = self.decide(_ci("fail", failing=["a"], pending=["b"]))
        self.assertEqual((got["action"], got["names"]), ("failed", ["a"]))

    def test_an_allowed_failure_is_rerun_once(self):
        got = self.decide(_ci("fail", failing=["flaky"]), rerun=["flaky"])
        self.assertEqual((got["action"], got["names"]), ("rerun", ["flaky"]))

    def test_a_failure_already_rerun_fails(self):
        got = self.decide(_ci("fail", failing=["flaky"]), rerun=["flaky"], reran=["flaky"])
        self.assertEqual((got["action"], got["names"]), ("failed", ["flaky"]))

    def test_one_failure_not_allowed_fails_the_whole_wait(self):
        got = self.decide(_ci("fail", failing=["flaky", "real"]), rerun=["flaky"])
        self.assertEqual((got["action"], got["names"]), ("failed", ["flaky", "real"]))

    def test_the_name_must_match_exactly(self):
        got = self.decide(_ci("fail", failing=["flaky (3.12)"]), rerun=["flaky"])
        self.assertEqual(got["action"], "failed")

    def test_a_just_rerun_failure_is_waited_out_once(self):
        got = self.decide(_ci("fail", failing=["flaky"]), rerun=["flaky"], settling=["flaky"])
        self.assertEqual(got["action"], "wait")
        late = self.decide(
            _ci("fail", failing=["flaky"]), rerun=["flaky"], settling=["flaky"], elapsed=100
        )
        self.assertEqual(late["action"], "timeout")

    def test_a_settling_failure_beside_a_new_real_failure_fails(self):
        got = self.decide(
            _ci("fail", failing=["flaky", "real"]), rerun=["flaky"], settling=["flaky"]
        )
        self.assertEqual((got["action"], got["names"]), ("failed", ["real"]))


class TestArgvBuilders(unittest.TestCase):
    BYPASS = ("--hotfix", "--no-checkpoint-gate", "--operator-override", "--blocker-rule")

    def _all(self):
        consent = finish.consent_argv(["git", "github"], "tester", "explicit")
        kw = dict(path="p.yaml", root=".", pr=7, consent=consent)
        return {
            "review": finish.review_argv(issue=3, reviews="r.json", closure="c.json", **kw),
            "ship": finish.ship_argv(
                issue=3,
                run_id="R1",
                capture_status="applied",
                capture_reason="why",
                capture_artifact="a",
                implementer="claude",
                **kw,
            ),
            "merge": finish.merge_argv(
                issue=3, method="squash", transport="auto", run_id="R1", **kw
            ),
        }

    def test_no_step_ever_carries_a_bypass_flag(self):
        for name, argv in self._all().items():
            for flag in self.BYPASS:
                self.assertNotIn(flag, argv, name)

    def test_every_built_argv_parses_with_the_real_parser(self):
        parser = cli.build_parser()
        for name, argv in self._all().items():
            ns = parser.parse_args(argv)
            self.assertEqual(ns.pr, 7, name)
        ship_ns = parser.parse_args(self._all()["ship"])
        self.assertTrue(ship_ns.live)
        self.assertTrue(ship_ns.append_ledger)
        self.assertEqual(ship_ns.capture_status, "applied")
        self.assertTrue(parser.parse_args(self._all()["review"]).live)

    def test_optional_flags_are_omitted_when_unset(self):
        kw = dict(path="p.yaml", root=".", pr=7, consent=[])
        review = finish.review_argv(issue=None, reviews="r.json", closure=None, **kw)
        self.assertNotIn("--issue", review)
        self.assertNotIn("--closure", review)
        ship = finish.ship_argv(
            issue=3,
            run_id=None,
            capture_status=None,
            capture_reason=None,
            capture_artifact=None,
            implementer=None,
            **kw,
        )
        self.assertEqual(
            ship,
            ["ship", "p.yaml", "--root", ".", "--pr", "7", "--issue", "3", "--live"]
            + ["--append-ledger"],
        )
        merge = finish.merge_argv(issue=3, method="rebase", transport="rest", run_id=None, **kw)
        self.assertNotIn("--run-id", merge)
        self.assertIn("rebase", merge)

    def test_consent_flags_are_forwarded_to_every_step(self):
        for argv in self._all().values():
            self.assertIn("--approve-scope", argv)
            self.assertEqual(argv[argv.index("--operator") + 1], "tester")
            self.assertEqual(argv[argv.index("--consent-mode") + 1], "explicit")
        self.assertEqual(finish.consent_argv([], None, None), [])


class TestRerunFailedCheck(unittest.TestCase):
    def _runner(self, rows, *, rerun_ok=True, list_ok=True):
        calls = []

        def fake(argv, **_kw):
            calls.append(argv)
            if argv[:3] == ["gh", "pr", "checks"]:
                text = rows if isinstance(rows, str) else json.dumps(rows)
                return subprocess.CompletedProcess(argv, 0 if list_ok else 8, text, "")
            return subprocess.CompletedProcess(argv, 0 if rerun_ok else 1, "", "boom")

        return fake, calls

    def _row(self, name="lint", bucket="fail", link="https://x/actions/runs/99/job/5"):
        return {"name": name, "bucket": bucket, "link": link}

    def test_reruns_the_failed_run_behind_the_named_check(self):
        fake, calls = self._runner([self._row("other"), self._row()])
        ok, detail = github.rerun_failed_check(7, "lint", _run=fake)
        self.assertTrue(ok)
        self.assertIn("run 99", detail)
        self.assertEqual(calls[1], ["gh", "run", "rerun", "99", "--failed"])

    def test_reads_the_rows_even_though_gh_exits_non_zero(self):
        fake, _ = self._runner([self._row()], list_ok=False)
        self.assertTrue(github.rerun_failed_check(7, "lint", _run=fake)[0])

    def test_unreadable_listing_is_reported(self):
        for bad in ("not json", "{}"):
            fake, _ = self._runner(bad)
            ok, detail = github.rerun_failed_check(7, "lint", _run=fake)
            self.assertFalse(ok)
            self.assertIn("could not list", detail)

    def test_empty_stdout_is_an_empty_listing(self):
        fake, _ = self._runner("")
        ok, detail = github.rerun_failed_check(7, "lint", _run=fake)
        self.assertFalse(ok)
        self.assertIn("no failed GitHub Actions run", detail)

    def test_only_a_failed_check_of_that_exact_name_is_rerun(self):
        for rows in (
            [self._row(name="lint (3.12)")],
            [self._row(bucket="pass")],
            ["junk"],
        ):
            fake, calls = self._runner(rows)
            ok, detail = github.rerun_failed_check(7, "lint", _run=fake)
            self.assertFalse(ok)
            self.assertIn("no failed GitHub Actions run", detail)
            self.assertEqual(len(calls), 1)

    def test_a_check_without_an_actions_link_cannot_be_rerun(self):
        for link in ("https://ci.example/build/1", None):
            fake, _ = self._runner([self._row(link=link)])
            self.assertFalse(github.rerun_failed_check(7, "lint", _run=fake)[0])

    def test_a_failing_rerun_command_is_reported(self):
        fake, _ = self._runner([self._row()], rerun_ok=False)
        ok, detail = github.rerun_failed_check(7, "lint", _run=fake)
        self.assertFalse(ok)
        self.assertIn("gh run rerun 99 failed: boom", detail)


class FinishCase(unittest.TestCase):
    """A project config, a fake clock and a scripted CI sequence."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.config = self._write("")
        self.now = 0.0
        self.sleeps = []
        self.snapshots = []
        self.invocations = []
        self.exit_codes = {}
        self.rerun = MagicMock(return_value=(True, "reran"))
        patches = [
            patch("keel.cli._finish_clock", side_effect=lambda: self.now),
            patch("keel.cli._finish_sleep", side_effect=self._sleep),
            patch("keel.cli._merge_snapshot_over_best_transport", side_effect=self._snapshot),
            patch("keel.cli._finish_invoke", side_effect=self._invoke),
            patch("keel.cli.github.rerun_failed_check", self.rerun),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _write(self, extra):
        path = Path(self.dir) / "project.yaml"
        path.write_text(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: tmp\n"
            f"gates: [build]\n{extra}knobs:\n  build_gate_cmd: 'true'\n",
            encoding="utf-8",
        )
        return str(path)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def _snapshot(self, _args):
        item = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        if isinstance(item, Exception):
            raise item
        return "gh-graphql", {"ci": item, "head_sha": "abc", "merge_state": "CLEAN"}

    def _invoke(self, argv, *, capture):
        self.invocations.append((argv, capture))
        code = self.exit_codes.get(argv[0], 0)
        return {"exit_code": code}

    def finish(self, *extra, json_out=True):
        argv = ["finish", self.config, "--root", self.dir, "--pr", "7", "--issue", "3"]
        argv += list(extra) + (["--json"] if json_out else [])
        rc, out, err = _run(argv)
        return rc, (json.loads(out) if json_out else out), err

    def stages(self, doc):
        return [(s["name"], s["status"]) for s in doc["steps"]]


class TestFinishChain(FinishCase):
    def test_green_ci_runs_ci_window_ship_merge_in_order(self):
        self.snapshots = [_ci("pass")]
        rc, doc, _ = self.finish()
        self.assertEqual(rc, 0)
        self.assertEqual(doc["status"], "pass")
        self.assertEqual(
            self.stages(doc),
            [("ci", "pass"), ("window", "pass"), ("ship", "pass"), ("merge", "pass")],
        )
        self.assertEqual([a[0][0] for a in self.invocations], ["ship", "merge"])
        self.assertTrue(all(capture for _, capture in self.invocations))

    def test_reviews_are_posted_first_through_keel_review(self):
        self.snapshots = [_ci("pass")]
        rc, doc, _ = self.finish("--reviews", "r.json", "--closure", "c.json")
        self.assertEqual(rc, 0)
        self.assertEqual([a[0][0] for a in self.invocations], ["review", "ship", "merge"])
        review = self.invocations[0][0]
        self.assertEqual(review[review.index("--reviews") + 1], "r.json")
        self.assertIn("--live", review)

    def test_a_refused_review_stops_before_the_wait(self):
        self.exit_codes = {"review": 1}
        self.snapshots = [_ci("pass")]
        rc, doc, _ = self.finish("--reviews", "r.json")
        self.assertEqual(rc, 1)
        self.assertEqual((doc["stopped_at"], doc["status"]), ("review", "fail"))
        self.assertEqual(self.stages(doc), [("review", "fail")])
        self.assertIn("keel review refused", doc["reason"])

    def test_a_refused_ship_stops_before_the_merge(self):
        self.exit_codes = {"ship": 1}
        self.snapshots = [_ci("pass")]
        rc, doc, _ = self.finish()
        self.assertEqual(rc, 1)
        self.assertEqual(doc["stopped_at"], "ship")
        self.assertEqual([a[0][0] for a in self.invocations], ["ship"])

    def test_a_refused_merge_is_a_failure_and_no_bypass_is_tried(self):
        self.exit_codes = {"merge": 1}
        self.snapshots = [_ci("pass")]
        rc, doc, _ = self.finish()
        self.assertEqual(rc, 1)
        self.assertEqual((doc["stopped_at"], doc["status"]), ("merge", "fail"))
        self.assertEqual([a[0][0] for a in self.invocations], ["ship", "merge"])
        for argv, _ in self.invocations:
            for flag in ("--hotfix", "--no-checkpoint-gate", "--operator-override"):
                self.assertNotIn(flag, argv)

    def test_finish_offers_no_bypass_flag_of_its_own(self):
        for flag in ("--hotfix", "--no-checkpoint-gate", "--operator-override"):
            with self.assertRaises(SystemExit) as raised:
                _run(["finish", self.config, "--pr", "7", "--issue", "3", flag])
            self.assertEqual(raised.exception.code, 2, flag)

    def test_a_closed_merge_window_stops_before_ship_and_merge(self):
        self.config = self._write("timezone: Europe/Istanbul\nmerge_window: '07:00-01:30'\n")
        self.snapshots = [_ci("pass")]
        with patch("keel.cli.window.is_merge_open", return_value=False):
            rc, doc, _ = self.finish()
        self.assertEqual(rc, 1)
        self.assertEqual((doc["status"], doc["stopped_at"]), ("window-closed", "window"))
        self.assertIn("not bypassed", doc["reason"])
        self.assertEqual(self.invocations, [])

    def test_an_open_merge_window_continues(self):
        self.config = self._write("timezone: Europe/Istanbul\nmerge_window: '07:00-01:30'\n")
        self.snapshots = [_ci("pass")]
        with patch("keel.cli.window.is_merge_open", return_value=True):
            rc, doc, _ = self.finish()
        self.assertEqual(rc, 0)
        self.assertTrue(doc["steps"][1]["open"])

    def test_missing_and_invalid_config(self):
        rc, _, err = _run(["finish", "/no/such.yaml", "--pr", "1", "--issue", "1"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)
        Path(self.config).write_text("not: [valid", encoding="utf-8")
        rc, _, err = _run(["finish", self.config, "--pr", "1", "--issue", "1"])
        self.assertEqual(rc, 1)
        self.assertTrue(err.strip())


class TestFinishDryRun(FinishCase):
    def test_json_dry_run_lists_the_plan_and_runs_nothing(self):
        rc, doc, _ = self.finish("--dry-run", "--reviews", "r.json", "--rerun-flaky", "lint")
        self.assertEqual(rc, 0)
        self.assertEqual(doc["status"], "dry-run")
        self.assertEqual(
            self.stages(doc),
            [
                ("review", "planned"),
                ("ci", "planned"),
                ("window", "planned"),
                ("ship", "planned"),
                ("merge", "planned"),
            ],
        )
        self.assertEqual(self.invocations, [])
        self.assertEqual(self.snapshots, [])

    def test_human_dry_run_prints_each_step(self):
        rc, out, _ = self.finish("--dry-run", json_out=False)
        self.assertEqual(rc, 0)
        self.assertIn("ci      wait for CI", out)
        self.assertIn("dry-run  PR #7: 4 steps, nothing run", out)
        self.assertNotIn("== keel finish", out)


class TestFinishCiWait(FinishCase):
    def test_pending_then_green_polls_until_pass(self):
        self.snapshots = [_ci("pending", pending=["a"]), _ci("pending", pending=["a"]), _ci("pass")]
        rc, doc, _ = self.finish("--ci-interval", "10")
        self.assertEqual(rc, 0)
        self.assertEqual(self.sleeps, [10, 10])
        self.assertEqual(doc["steps"][0]["polls"], 3)

    def test_still_pending_at_the_timeout_is_a_timeout_not_a_pass(self):
        self.snapshots = [_ci("pending", pending=["build", "lint"])]
        rc, doc, _ = self.finish("--ci-timeout", "50", "--ci-interval", "20")
        self.assertEqual(rc, 1)
        self.assertEqual((doc["status"], doc["stopped_at"]), ("timeout", "ci"))
        self.assertIn("timed out after 50s", doc["reason"])
        self.assertIn("2 checks still pending: build, lint", doc["reason"])
        self.assertEqual(self.sleeps, [20, 20, 10])
        self.assertEqual(self.invocations, [])

    def test_a_clock_that_never_advances_still_ends_in_a_timeout(self):
        self.snapshots = [_ci("pending", pending=["a"])]
        with patch("keel.cli._finish_sleep"):  # the clock stays at 0
            rc, doc, _ = self.finish("--ci-timeout", "60", "--ci-interval", "30")
        self.assertEqual((rc, doc["status"]), (1, "timeout"))
        self.assertIn("poll limit reached", doc["reason"])
        self.assertEqual(doc["steps"][0]["polls"], finish.poll_cap(60, 30))
        self.assertEqual(self.invocations, [])

    def test_failing_checks_are_named_and_stop_the_chain(self):
        self.snapshots = [_ci("fail", failing=["unit", "lint"], pending=["slow"])]
        rc, doc, _ = self.finish()
        self.assertEqual((rc, doc["status"], doc["stopped_at"]), (1, "fail", "ci"))
        self.assertIn("2 checks failing: unit, lint", doc["reason"])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.invocations, [])

    def test_no_checks_proceeds_so_the_merge_gate_can_apply_its_docs_carve_out(self):
        self.snapshots = [_ci("no-checks")]
        rc, doc, _ = self.finish()
        self.assertEqual(rc, 0)

    def test_a_named_flaky_check_is_rerun_once_then_waited_for(self):
        fail = _ci("fail", failing=["lint"])
        # old failure still shown right after the rerun, then pending, then green
        self.snapshots = [fail, fail, _ci("pending", pending=["lint"]), _ci("pass")]
        rc, doc, _ = self.finish("--rerun-flaky", "lint", "--ci-interval", "5")
        self.assertEqual(rc, 0)
        self.rerun.assert_called_once_with(7, "lint", cwd=self.dir)
        self.assertEqual(doc["steps"][0]["reran"], ["lint"])

    def test_a_second_failure_after_the_rerun_fails(self):
        fail = _ci("fail", failing=["lint"])
        self.snapshots = [fail]
        rc, doc, _ = self.finish("--rerun-flaky", "lint", "--ci-interval", "5")
        self.assertEqual((rc, doc["stopped_at"]), (1, "ci"))
        self.rerun.assert_called_once()
        self.assertIn("lint", doc["reason"])
        self.assertEqual(doc["steps"][0]["reran"], ["lint"])

    def test_a_check_not_named_for_rerun_is_never_rerun(self):
        self.snapshots = [_ci("fail", failing=["unit"])]
        rc, doc, _ = self.finish("--rerun-flaky", "lint")
        self.assertEqual(rc, 1)
        self.rerun.assert_not_called()

    def test_a_failed_rerun_request_fails_the_step(self):
        self.rerun.return_value = (False, "no failed GitHub Actions run found")
        self.snapshots = [_ci("fail", failing=["lint"])]
        rc, doc, _ = self.finish("--rerun-flaky", "lint")
        self.assertEqual((rc, doc["stopped_at"]), (1, "ci"))
        self.assertIn("no failed GitHub Actions run", doc["reason"])

    def test_one_unreadable_snapshot_is_retried(self):
        self.snapshots = [cli._SnapshotFailed("gh-graphql", "boom"), _ci("pass")]
        rc, doc, _ = self.finish("--ci-interval", "5")
        self.assertEqual(rc, 0)
        self.assertEqual(self.sleeps, [5])

    def test_repeated_unreadable_snapshots_fail_the_step(self):
        self.snapshots = [cli._SnapshotFailed("gh-graphql", "boom")]
        rc, doc, _ = self.finish("--ci-interval", "5")
        self.assertEqual((rc, doc["status"], doc["stopped_at"]), (1, "fail", "ci"))
        self.assertIn("could not read CI state: boom", doc["reason"])
        self.assertEqual(doc["steps"][0]["polls"], finish.MAX_READ_FAILURES)

    def test_an_unreadable_snapshot_past_the_deadline_fails_instead_of_sleeping(self):
        self.now = 0.0
        self.snapshots = [_ci("pending", pending=["a"]), cli._SnapshotFailed("rest", "boom")]
        rc, doc, _ = self.finish("--ci-timeout", "10", "--ci-interval", "10")
        self.assertEqual((rc, doc["status"]), (1, "fail"))
        self.assertIn("could not read CI state", doc["reason"])
        self.assertEqual(self.sleeps, [10])

    def test_human_output_shows_each_poll_and_the_outcome(self):
        self.snapshots = [_ci("pending", pending=["a"]), _ci("pass")]
        rc, out, _ = self.finish("--ci-interval", "5", json_out=False)
        self.assertEqual(rc, 0)
        self.assertIn("== keel finish: ci ==", out)
        self.assertIn("ci poll 1 (0s): 1 check still pending: a", out)
        self.assertIn("keel finish — pass  PR #7 merged", out)
        self.assertTrue(all(not capture for _, capture in self.invocations))

    def test_human_failure_output_names_the_step_and_reason(self):
        self.snapshots = [_ci("pending", pending=["a"])]
        rc, out, _ = self.finish("--ci-timeout", "5", "--ci-interval", "5", json_out=False)
        self.assertEqual(rc, 1)
        self.assertIn("keel finish — timeout  PR #7 stopped at ci", out)
        self.assertIn("reason : timed out after 5s", out)


class TestFinishInvoke(unittest.TestCase):
    def test_uncaptured_returns_only_the_exit_code(self):
        with patch("keel.cli.main", return_value=3) as main:
            self.assertEqual(cli._finish_invoke(["merge", "p"], capture=False), {"exit_code": 3})
        main.assert_called_once_with(["merge", "p"])

    def test_captured_json_output_is_embedded_and_json_is_requested(self):
        def fake(argv):
            print(json.dumps({"status": "pass"}))
            print("note", file=__import__("sys").stderr)
            return 0

        with patch("keel.cli.main", side_effect=fake) as main:
            got = cli._finish_invoke(["merge", "p"], capture=True)
        main.assert_called_once_with(["merge", "p", "--json"])
        self.assertEqual(got, {"exit_code": 0, "output": {"status": "pass"}, "stderr": "note"})

    def test_captured_text_output_is_kept_as_text(self):
        def fake(argv):
            print("plain text")
            return 1

        with patch("keel.cli.main", side_effect=fake):
            got = cli._finish_invoke(["ship", "p"], capture=True)
        self.assertEqual(got, {"exit_code": 1, "output": "plain text"})

    def test_the_real_clock_and_sleep_are_the_monotonic_ones(self):
        with patch("keel.cli.time.monotonic", return_value=5.0):
            self.assertEqual(cli._finish_clock(), 5.0)
        with patch("keel.cli.time.sleep") as sleep:
            cli._finish_sleep(2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
