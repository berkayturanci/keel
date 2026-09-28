"""A jury gate that cannot run never reads as a pass (#1369).

``gates: [jury]`` on a host without the ``jury`` binary, or on an empty diff, was a
no-op: :func:`keel.jury.run_gate` returned ``(True, [])``. ``keel run-gates`` printed
``ok  jury`` and exited 0, and a dry ``keel ship`` said ``MERGE — clear to merge`` while
nothing had judged the change. Two cases, one rule:

* **Beside another gate** the jury stays the documented s8 no-op: it does not hold the
  merge, but it reads ``SKIPPED  jury`` with a ``nit`` saying why, never ``ok``.
* **Alone** it is the #1364 case — a plan with nothing that judges — reached through a
  gate that is planned but cannot run: ``FAIL  jury``, ``unconfigured``, blocking.

The jury CLI is stubbed out (``jury.available``) and the diff is stubbed (``git.diff``),
so the result does not depend on whether this host has ai-jury installed or a git repo.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keel import cli, gates, git, jury, ledger, runtime
from keel.findings import Finding, summarize
from keel.gates import GateOutcome, GateSpec
from keel.runner import CommandResult

_DIFF = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n a\n+b\n"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class _Project(unittest.TestCase):
    """A temp project with no ``jury`` CLI, a stubbed diff, and ``gh`` never called."""

    diff_text: str | None = _DIFF

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        real_detect = runtime.detect

        def detect(root=".", **kwargs):
            kwargs.setdefault("run", lambda argv, **_kw: CommandResult(False, 1, "stubbed"))
            kwargs.setdefault(
                "which", lambda name: "/bin/gh" if name == "gh" else shutil.which(name)
            )
            return real_detect(root, **kwargs)

        for patcher in (
            patch.object(runtime, "detect", detect),
            patch.object(jury, "available", return_value=False),
            patch.object(git, "diff", return_value=self.diff_text),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def project(self, gates_line: str) -> str:
        text = "\n".join(
            [
                "extends: keel",
                'core_version: "^1.0"',
                "base_branch: main",
                gates_line,
                "knobs:",
                '  build_gate_cmd: "true"',
                "",
            ]
        )
        path = self.root / "project.yaml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run_gates(self, gates_line: str, *extra: str):
        return run(["run-gates", self.project(gates_line), "--root", str(self.root), *extra])

    def ship(self, gates_line: str, *extra: str):
        return run(["ship", self.project(gates_line), "--root", str(self.root), *extra])


class AJuryAloneThatCannotRunBlocks(_Project):
    """``gates: [jury]`` with no ``jury`` CLI: nothing judged, so the run blocks."""

    def test_run_gates_fails_it_and_says_why(self):
        rc, out, err = self.run_gates("gates: [jury]")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("FAIL  jury", out)
        self.assertNotIn("ok  jury", out)
        self.assertIn(f"[major] jury: {gates.LONE_JURY_JUDGED_NOTHING}", out)
        # The runner's own reason rides along beneath it.
        self.assertIn(f"[nit] jury:not-run: jury did not run: {jury.NOT_RUN_NO_CLI}", out)
        self.assertIn("BLOCKED", out)

    def test_the_json_report_carries_it_as_unconfigured(self):
        rc, out, _ = self.run_gates("gates: [jury]", "--json")
        self.assertEqual(rc, 1, out)
        report = json.loads(out)
        self.assertTrue(report["blocked"])
        [outcome] = report["gate_outcomes"]
        self.assertEqual(outcome["gate"], "jury")
        self.assertIs(outcome["ok"], False)
        self.assertIs(outcome["unconfigured"], True)

    def test_a_dry_ship_blocks_rather_than_reporting_merge(self):
        rc, out, err = self.ship("gates: [jury]")
        self.assertIn("decision      : BLOCK", out, err)
        self.assertNotIn("decision      : MERGE", out)
        self.assertIn("gate(s): jury", out)
        self.assertIn("gate jury           FAIL", out)

    def test_a_recorded_result_cannot_clear_it(self):
        # keel executed the gate and measured that it judged nothing; that is not `not_run`.
        rc, _, err = self.ship("gates: [jury]", "--gate-result", "jury=pass")
        self.assertEqual(rc, 1)
        self.assertIn("--gate-result cannot override a gate keel executed: jury", err)

    def test_a_jury_that_ran_alone_is_still_a_gate(self):
        # Only a jury that could not run is failed: one that ran and passed judged.
        with patch.object(jury, "run_gate", return_value=(True, [], False)):
            rc, out, err = self.run_gates("gates: [jury]")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("ok  jury", out)
        self.assertNotIn(gates.LONE_JURY_JUDGED_NOTHING, out)


class AJuryAloneOnAnEmptyDiffBlocks(_Project):
    """An empty diff gives the jury nothing to review: the same block, the other reason."""

    diff_text = ""

    def test_run_gates_fails_it_and_names_the_empty_diff(self):
        rc, out, err = self.run_gates("gates: [jury]")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("FAIL  jury", out)
        self.assertIn(gates.LONE_JURY_JUDGED_NOTHING, out)
        self.assertIn(jury.NOT_RUN_EMPTY_DIFF, out)


class AJuryBesideAnotherGateStaysANoOp(_Project):
    """``gates: [build, jury]`` with no CLI: build judges; the jury is skipped, not ok."""

    def test_run_gates_passes_and_reports_the_jury_skipped(self):
        rc, out, err = self.run_gates("gates: [build, jury]")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("ok  build", out)
        self.assertIn("SKIPPED  jury", out)
        self.assertNotIn("ok  jury", out)
        self.assertIn(f"[nit] jury:not-run: jury did not run: {jury.NOT_RUN_NO_CLI}", out)
        self.assertNotIn(gates.LONE_JURY_JUDGED_NOTHING, out)

    def test_a_dry_ship_still_reads_merge(self):
        rc, out, err = self.ship("gates: [build, jury]")
        self.assertIn("decision      : MERGE", out, err)
        self.assertIn("gate jury           SKIPPED", out)

    def test_the_json_report_marks_it_skipped(self):
        rc, out, _ = self.run_gates("gates: [build, jury]", "--json")
        self.assertEqual(rc, 0, out)
        jury_outcome = next(o for o in json.loads(out)["gate_outcomes"] if o["gate"] == "jury")
        self.assertIs(jury_outcome["ok"], True)
        self.assertIs(jury_outcome["skipped"], True)
        self.assertIs(jury_outcome["unconfigured"], False)


def _spec(gid="jury", kind="builtin", on_fail="block"):
    return GateSpec(gid, kind, "test", on_fail)


def _skipped(gid="jury", error=None):
    finding = Finding("nit", f"jury did not run: {jury.NOT_RUN_NO_CLI}", jury.NOT_RUN_SOURCE)
    return GateOutcome(gid, True, (finding,), error=error, skipped=True)


class TheLoneJuryRuleIsKeyedOnThePlan(unittest.TestCase):
    """:func:`keel.gates.lone_jury_cannot_judge` fails exactly one shape and leaves the rest."""

    def test_a_lone_skipped_jury_fails_unconfigured(self):
        [outcome] = gates.lone_jury_cannot_judge([_spec()], [_skipped()])
        self.assertEqual(outcome.gate, "jury")
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.skipped)
        self.assertTrue(outcome.unconfigured)
        self.assertEqual(outcome.on_fail, "block")
        self.assertTrue(summarize(list(outcome.findings)).blocked)
        self.assertEqual([f.source for f in outcome.findings], ["jury", jury.NOT_RUN_SOURCE])

    def test_another_gate_keeps_the_no_op(self):
        build = GateOutcome("build", True)
        result = gates.lone_jury_cannot_judge(
            [GateSpec("build", "command", "test", "block", run="true"), _spec()],
            [build, _skipped()],
        )
        self.assertEqual(result, [build, _skipped()])

    def test_a_jury_that_judged_is_left_alone(self):
        ran = GateOutcome("jury", True)
        self.assertEqual(gates.lone_jury_cannot_judge([_spec()], [ran]), [ran])

    def test_an_extension_called_jury_is_not_the_builtin(self):
        spec = _spec(kind="command")
        self.assertEqual(gates.lone_jury_cannot_judge([spec], [_skipped()]), [_skipped()])

    def test_another_builtin_is_not_the_jury(self):
        spec = _spec(gid="tdd-order")
        outcome = _skipped(gid="tdd-order")
        self.assertEqual(gates.lone_jury_cannot_judge([spec], [outcome]), [outcome])

    def test_a_soft_gate_that_errored_is_not_a_jury_that_could_not_run(self):
        errored = _skipped(error="boom")
        spec = _spec(on_fail="warn")
        self.assertEqual(gates.lone_jury_cannot_judge([spec], [errored]), [errored])

    def test_the_record_certifies_the_no_op_and_refuses_the_lone_jury(self):
        # `keel merge` reads the ledger through `record_gates_passed`: the no-op beside
        # build still certifies (unchanged), the lone jury does not.
        def record(outcomes):
            return ledger.build_ship_run_record(
                command="ship",
                base_branch="main",
                changed_files=[],
                outcomes=outcomes,
                verdict=summarize(gates.collect_findings(outcomes)),
                assessment=SimpleNamespace(
                    tier=1,
                    reviewers=1,
                    window_open=True,
                    ci_ok=None,
                    merge=SimpleNamespace(action="merge", reason=""),
                    halted=False,
                    bypassed_window=False,
                ),
            )

        beside = [GateOutcome("build", True), _skipped()]
        self.assertTrue(ledger.record_gates_passed(record(beside)))
        alone = gates.lone_jury_cannot_judge([_spec()], [_skipped()])
        self.assertFalse(ledger.record_gates_passed(record(alone)))


class TheRunnerCarriesSkipped(unittest.TestCase):
    """The runner's fifth element becomes ``GateOutcome.skipped``, on a pass only."""

    def test_a_passing_skipped_result_is_skipped(self):
        [outcome] = gates.run_gates([_spec()], lambda _s: (True, [], False, False, True))
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.skipped)

    def test_a_failing_result_is_a_failure_whatever_it_says(self):
        [outcome] = gates.run_gates([_spec()], lambda _s: (False, [], False, False, True))
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.skipped)

    def test_the_four_element_form_is_not_skipped(self):
        [outcome] = gates.run_gates([_spec()], lambda _s: (True, [], False, False))
        self.assertFalse(outcome.skipped)

    def test_the_cli_runner_marks_a_jury_that_could_not_run(self):
        runner = cli._gate_runner(".", _DIFF)
        with patch.object(jury, "available", return_value=False):
            *_, skipped = runner(_spec())
        self.assertIs(skipped, True)

    def test_the_cli_runner_does_not_mark_a_jury_that_ran(self):
        runner = cli._gate_runner(".", _DIFF)
        with patch.object(jury, "run_gate", return_value=(True, [], False)):
            *_, skipped = runner(_spec())
        self.assertIs(skipped, False)


class TheLabelIsNotOk(unittest.TestCase):
    """A skipped outcome reads ``SKIPPED`` wherever gates are listed, never ``ok``."""

    def test_skipped_reads_skipped(self):
        self.assertEqual(cli._gate_status(_skipped()), "SKIPPED")

    def test_not_run_still_reads_not_run(self):
        self.assertEqual(cli._gate_status(GateOutcome("x", True, not_run=True)), "NOT-RUN")

    def test_a_pass_still_reads_ok(self):
        self.assertEqual(cli._gate_status(GateOutcome("x", True)), "ok")


class TheCouldNotRunTestReadsTheSource(unittest.TestCase):
    def test_only_the_not_run_finding_counts(self):
        self.assertTrue(jury.could_not_run([_skipped().findings[0]]))
        self.assertFalse(jury.could_not_run([Finding("nit", "x", "jury:consensus")]))
        self.assertFalse(jury.could_not_run([]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
