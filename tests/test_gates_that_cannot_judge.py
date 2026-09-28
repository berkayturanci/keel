"""A gate that cannot judge never reads as a pass, and never burns work (#1364).

Four holes, one principle:

* **``gates: []``** — or no ``gates:`` key, which loads the same — planned zero gates.
  ``keel run-gates`` printed nothing and exited 0, and a dry ``keel ship`` said MERGE,
  while ``keel merge`` refused the empty record. A plan with nothing to judge now blocks
  with an outcome named after the key.
* **A blank ``knobs.build_gate_cmd``** (``" "``) passed ``minLength: 1``, ran
  ``sh -c ' '``, exited 0 and reported ``ok build``. The schema refuses it, and the
  command runner treats a blank command as no command.
* **The s4 loop** spent its whole budget on a gate no implementer can make green. An
  outcome that cannot judge now ends the loop at once, ``unconfigured``, blocked.
* **Belt and braces from the #1365 review**: the unset-command verdict lived only in
  ``command_gate_runner``. :func:`keel.gates.run_gates` re-checks it after any runner, so
  a runner answering ``(True, [])`` for every spec cannot pass an unset gate — while a
  gate the runner scoped out (``--phases``) stays NOT-RUN.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keel import cli, gates, ledger, loop, runtime
from keel import config as cfg
from keel.extensions import Extension
from keel.findings import summarize
from keel.runner import CommandResult, command_gate_runner


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _data(**overrides):
    data = {
        "extends": "keel",
        "core_version": "^1.0",
        "base_branch": "main",
        "knobs": {"build_gate_cmd": "true"},
    }
    data.update(overrides)
    return data


def _tester(eid="smoke", run="true"):
    return Extension(
        id=eid,
        slot="tester",
        kind="command",
        mode="deterministic",
        agent="inherit",
        on_fail="block",
        anchorable=False,
        run=run,
        prompt=None,
        body="",
        source=f"{eid}.md",
    )


class _Project(unittest.TestCase):
    """A temp project root, with ``gh auth status`` answered by a stub (never GitHub)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.gh_probes = []
        real_detect = runtime.detect

        def probe(argv, **_kw):
            self.gh_probes.append(list(argv))
            return CommandResult(False, 1, "stubbed: the suite never runs gh")

        def detect(root=".", **kwargs):
            kwargs.setdefault("run", probe)
            kwargs.setdefault(
                "which", lambda name: "/bin/gh" if name == "gh" else shutil.which(name)
            )
            return real_detect(root, **kwargs)

        patcher = patch.object(runtime, "detect", detect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def project(self, gates_line: str | None, knobs: str = '  build_gate_cmd: "true"') -> str:
        lines = ["extends: keel", 'core_version: "^1.0"', "base_branch: main"]
        if gates_line is not None:
            lines.append(gates_line)
        lines += ["knobs:", knobs, ""]
        path = self.root / "project.yaml"
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)


class AnEmptyGateListBlocks(_Project):
    """``gates: []`` is a blocking finding in ``run-gates`` and in the dry assessment."""

    def test_run_gates_fails_and_names_the_key(self):
        rc, out, err = run(["run-gates", self.project("gates: []"), "--root", str(self.root)])
        self.assertEqual(rc, 1, out + err)
        self.assertIn("FAIL  gates", out)
        self.assertIn(f"[major] gates: {gates.NO_GATES_PLANNED}", out)
        self.assertIn("BLOCKED", out)
        self.assertEqual(self.gh_probes, [["gh", "auth", "status"]])

    def test_an_absent_gates_key_is_the_same_config_and_blocks_the_same(self):
        rc, out, _ = run(["run-gates", self.project(None), "--root", str(self.root)])
        self.assertEqual(rc, 1, out)
        self.assertIn(gates.NO_GATES_PLANNED, out)

    def test_a_list_that_plans_nothing_blocks_too(self):
        # `lint` with no `knobs.lint_cmd` is dropped at planning, so the plan is empty.
        rc, out, _ = run(["run-gates", self.project("gates: [lint]"), "--root", str(self.root)])
        self.assertEqual(rc, 1, out)
        self.assertIn(gates.NO_GATES_PLANNED, out)

    def test_the_phase_scope_does_not_hide_it(self):
        # Not a gate outside the scope: there is no gate in any scope.
        rc, out, _ = run(
            [
                "run-gates",
                self.project("gates: []"),
                "--root",
                str(self.root),
                "--phases",
                "guard,test",
            ]
        )
        self.assertEqual(rc, 1, out)
        self.assertIn(gates.NO_GATES_PLANNED, out)

    def test_the_json_report_carries_it_as_unconfigured(self):
        rc, out, _ = run(
            ["run-gates", self.project("gates: []"), "--root", str(self.root), "--json"]
        )
        self.assertEqual(rc, 1, out)
        report = json.loads(out)
        self.assertTrue(report["blocked"])
        self.assertEqual(report["gates"], [])
        [outcome] = report["gate_outcomes"]
        self.assertEqual(outcome["gate"], "gates")
        self.assertFalse(outcome["ok"])
        self.assertFalse(outcome["not_run"])
        self.assertIs(outcome.get("unconfigured"), True)

    def test_a_configured_gate_is_unaffected(self):
        rc, out, _ = run(["run-gates", self.project("gates: [build]"), "--root", str(self.root)])
        self.assertEqual(rc, 0, out)
        self.assertIn("ok  build", out)
        self.assertNotIn(gates.NO_GATES_PLANNED, out)

    def test_a_dry_ship_blocks_rather_than_reporting_merge(self):
        rc, out, err = run(["ship", self.project("gates: []"), "--root", str(self.root)])
        self.assertIn("decision      : BLOCK", out, err)
        self.assertNotIn("decision      : MERGE", out)
        self.assertIn("gate(s): gates", out)

    def test_a_recorded_result_cannot_clear_it(self):
        rc, _, err = run(
            [
                "ship",
                self.project("gates: []"),
                "--root",
                str(self.root),
                "--gate-result",
                "gates=pass",
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("--gate-result names no planned gate: gates", err)


class TheEmptyPlanIsJudgedOnThePlan(unittest.TestCase):
    """Keyed on what is planned, not on the ``gates:`` key alone."""

    def test_an_empty_plan_yields_the_blocking_outcome(self):
        outcome = gates.nothing_to_judge(())
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.gate, gates.NO_GATES_ID)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.unconfigured)
        self.assertEqual(outcome.on_fail, "block")
        self.assertTrue(summarize(list(outcome.findings)).blocked)
        self.assertIn("gates:", outcome.findings[0].message)

    def test_an_extension_gate_is_a_gate(self):
        # `gates: []` beside a tester Lego is a documented way to run project gates.
        config = cfg.parse_config(_data(gates=[]))
        specs = gates.plan_gates(config, {"tester": [_tester()]})
        self.assertIsNone(gates.nothing_to_judge(specs))

    def test_the_tdd_order_gate_alone_is_nothing_to_judge(self):
        # It reads the other gates' verdict; "green" over no gates is vacuous.
        config = cfg.parse_config(_data(gates=[]))
        specs = gates.plan_gates(config, {}, implement_mode="tdd")
        self.assertEqual([spec.id for spec in specs], ["tdd-order"])
        self.assertIsNotNone(gates.nothing_to_judge(specs))

    def test_the_merge_certification_refuses_the_record(self):
        # `keel merge` reads the ledger through `record_gates_passed`. It already refused an
        # empty gate list; the new outcome keeps the record refused, and the verdict blocked.
        outcome = gates.nothing_to_judge(())
        verdict = summarize(list(outcome.findings))
        record = ledger.build_ship_run_record(
            command="ship",
            base_branch="main",
            changed_files=[],
            outcomes=[outcome],
            verdict=verdict,
            assessment=SimpleNamespace(
                tier=2,
                reviewers=2,
                window_open=True,
                ci_ok=None,
                merge=SimpleNamespace(action="block", reason="gates"),
                halted=False,
                bypassed_window=False,
            ),
        )
        self.assertTrue(record["verdict"]["blocked"])
        self.assertFalse(ledger.record_gates_passed(record))


class ABlankBuildCommandIsNoCommand(unittest.TestCase):
    """``build_gate_cmd: " "`` is refused by the schema and failed by the runner."""

    def test_the_schema_refuses_it(self):
        for blank in (" ", "\t", "  \n "):
            with self.subTest(blank=blank):
                data = _data(gates=["build"], knobs={"build_gate_cmd": blank})
                errors = cfg.validate_data(data)
                self.assertTrue(any("build_gate_cmd" in e for e in errors), errors)
                with self.assertRaises(cfg.ConfigError):
                    cfg.parse_config(data)

    def test_a_command_with_surrounding_space_is_still_a_command(self):
        data = _data(gates=["build"], knobs={"build_gate_cmd": "  make test  "})
        self.assertEqual(cfg.validate_data(data), [])

    def test_the_runner_fails_it_and_runs_nothing(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        spec = gates.GateSpec("build", "command", "test", "block", run=" ")
        ok, findings, timed_out, not_run = command_gate_runner(_run=fake_run)(spec)
        self.assertFalse(ok)
        self.assertEqual(findings, [gates.unconfigured_finding(spec)])
        self.assertFalse(timed_out)
        self.assertFalse(not_run)
        self.assertEqual(calls, [])

    def test_run_gates_reports_it_unconfigured(self):
        spec = gates.GateSpec("build", "command", "test", "block", run="   ")
        [outcome] = gates.run_gates([spec], command_gate_runner(_run=None))
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.unconfigured)
        self.assertEqual(outcome.findings[0].message, gates.UNCONFIGURED_BUILD_GATE)


class RunGatesRechecksAfterTheRunner(unittest.TestCase):
    """Belt and braces: the unset-command verdict no longer lives only in one runner."""

    def test_a_runner_answering_ok_for_everything_cannot_pass_an_unset_gate(self):
        for run_value in (None, "", " "):
            with self.subTest(run=run_value):
                spec = gates.GateSpec("build", "command", "test", "block", run=run_value)
                [outcome] = gates.run_gates([spec], lambda _spec: (True, []))
                self.assertFalse(outcome.ok)
                self.assertFalse(outcome.not_run)
                self.assertTrue(outcome.unconfigured)
                self.assertEqual(outcome.findings, (gates.unconfigured_finding(spec),))
                self.assertEqual(outcome.on_fail, "block")

    def test_a_soft_unset_gate_fails_at_its_own_severity(self):
        spec = gates.GateSpec("smoke", "command", "test", "suggest", run=None)
        [outcome] = gates.run_gates([spec], lambda _spec: (True, [], False, False))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.findings[0].severity, "minor")

    def test_a_gate_the_runner_scoped_out_is_still_not_run(self):
        # The `--phases` contract: out of scope is NOT-RUN, never a failure.
        spec = gates.GateSpec("build", "command", "test", "block", run=None)
        [outcome] = gates.run_gates([spec], lambda _spec: (True, [], False, True))
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.not_run)
        self.assertFalse(outcome.unconfigured)
        self.assertEqual(outcome.findings, ())

    def test_a_configured_command_gate_is_left_to_the_runner(self):
        spec = gates.GateSpec("build", "command", "test", "block", run="make test")
        [outcome] = gates.run_gates([spec], lambda _spec: (True, []))
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.unconfigured)

    def test_a_non_command_gate_is_not_a_command_gate(self):
        spec = gates.GateSpec("jury", "builtin", "test", "block")
        [outcome] = gates.run_gates([spec], lambda _spec: (True, []))
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.unconfigured)


class TheRemedyNamesOnlyGatesThatJudge(_Project):
    """The empty-plan finding must not advise a gate that is a no-op here (#1368 review).

    ``gates: [jury]`` on a host without the ``jury`` binary, or on an empty diff, judges
    nothing. It used to report ``ok jury`` and a dry ship said MERGE; since #1369 it fails
    (``tests/test_jury_gate_that_cannot_run.py``), but it still cannot clear this block by
    judging, so "list jury" would only trade one block for another.
    """

    def test_the_remedy_does_not_offer_jury(self):
        self.assertNotIn("jury", gates.NO_GATES_PLANNED)
        self.assertIn("knobs.build_gate_cmd", gates.NO_GATES_PLANNED)
        self.assertIn("knobs.lint_cmd", gates.NO_GATES_PLANNED)


class ABlankLintCommandIsOff(_Project):
    """``lint_cmd: " "`` plans no lint gate, exactly as ``lint_cmd: ""`` does (#1368 review)."""

    def _ids(self, lint):
        config = cfg.parse_config(
            _data(gates=["build", "lint"], knobs={"build_gate_cmd": "true", "lint_cmd": lint})
        )
        return [spec.id for spec in gates.plan_gates(config, {})]

    def test_blank_and_empty_plan_no_lint_and_a_command_does(self):
        for blank in ("", " ", "\t "):
            with self.subTest(lint=blank):
                self.assertEqual(self._ids(blank), ["build"])
        self.assertEqual(self._ids("make lint"), ["build", "lint"])

    def test_run_gates_does_not_fail_a_lint_gate_it_was_told_is_off(self):
        project = self.project("gates: [build, lint]", '  build_gate_cmd: "true"\n  lint_cmd: " "')
        rc, out, _ = run(["run-gates", project, "--root", str(self.root)])
        self.assertEqual(rc, 0, out)
        self.assertIn("ok  build", out)
        self.assertNotIn("lint", out)

    def test_config_hash_is_unchanged_for_a_blank_lint_command(self):
        # Measured on origin/main 341586a2: planning changed, the canonical config did not.
        config = cfg.parse_config(
            _data(gates=["build", "lint"], knobs={"build_gate_cmd": "true", "lint_cmd": " "})
        )
        self.assertEqual(
            cfg.config_hash(config),
            "4f14561dc92b9613f8f1511cbd6564b3691d7282dea4e66520bef232bcad810e",
        )


class TheUnconfiguredFindingNamesWhatToSet(unittest.TestCase):
    """Every command gate backed by a knob names that knob; an extension names its file."""

    def test_build_names_its_knob(self):
        spec = gates.GateSpec("build", "command", "test", "block", run=None)
        self.assertIn("knobs.build_gate_cmd", gates.unconfigured_finding(spec).message)

    def test_lint_names_its_knob(self):
        spec = gates.GateSpec("lint", "command", "test", "block", run=" ")
        finding = gates.unconfigured_finding(spec)
        self.assertEqual(finding.message, gates.UNCONFIGURED_LINT_GATE)
        self.assertIn("knobs.lint_cmd", finding.message)
        self.assertEqual(finding.severity, "major")

    def test_an_extension_gate_names_run_in_its_file(self):
        source = ".keel/extensions/smoke.md"
        spec = gates.GateSpec("smoke", "command", "test", "suggest", run=" ", source=source)
        finding = gates.unconfigured_finding(spec)
        self.assertIn("'smoke'", finding.message)
        self.assertIn(f"set run: in {source}", finding.message)
        self.assertEqual(finding.severity, "minor")

    def test_an_extension_called_build_is_not_the_builtin(self):
        spec = gates.GateSpec("build", "command", "test", "block", run=" ", source="build.md")
        message = gates.unconfigured_finding(spec).message
        self.assertNotIn("knobs.build_gate_cmd", message)
        self.assertIn("set run: in build.md", message)

    def test_a_spec_keel_planned_from_nothing_names_nothing_it_cannot(self):
        spec = gates.GateSpec(
            "semgrep", "command", "test", "suggest", run=None, source="policy_pack:preset:x"
        )
        self.assertEqual(
            gates.unconfigured_finding(spec).message, "gate 'semgrep' has no command configured"
        )


class TheOrderGateCannotBeGreenBesideAnEmptyPlan(unittest.TestCase):
    """``gates: []`` under ``implement_mode: tdd`` plans only ``tdd-order`` (#1368 review).

    The failing ``gates`` outcome is not a planned spec, so the order gate's "other gates
    green" input skipped it and a test-first history certified ``tdd-order`` as passed
    beside ``FAIL gates``. It is red in every phase, so it is red for the order gate too.
    """

    def test_a_test_first_history_does_not_pass_the_order_gate(self):
        from keel import tdd

        config = cfg.parse_config(
            _data(
                gates=[],
                knobs={"build_gate_cmd": "true", "implement_mode": "tdd"},
                policy_pack={
                    "name": "p",
                    "test_groups": {
                        "unit": {
                            "command": "true",
                            "paths": ["tests/**"],
                            "test_paths": ["tests/**"],
                        }
                    },
                },
            )
        )
        specs = gates.plan_gates(config, {})
        self.assertEqual([spec.id for spec in specs], [tdd.GATE_ID])
        log = (
            f"{tdd.RECORD_SEP}aaa1{tdd.FIELD_SEP}base{tdd.FIELD_SEP}test: pin it\n"
            "A\ttests/test_x.py\n"
            f"{tdd.RECORD_SEP}bbb2{tdd.FIELD_SEP}aaa1{tdd.FIELD_SEP}feat: do it\n"
            "M\tsrc/x.py\n"
        )
        with (
            patch("keel.cli._ship_base_ref", return_value="origin/main"),
            patch("keel.cli.git.commit_log", return_value=log),
        ):
            outcomes, result = cli._run_planned_gates(
                specs, lambda _spec: (True, []), config=config, root="."
            )
        by_gate = {outcome.gate: outcome for outcome in outcomes}
        self.assertEqual(list(by_gate), [gates.NO_GATES_ID, tdd.GATE_ID])
        self.assertFalse(by_gate[gates.NO_GATES_ID].ok)
        self.assertFalse(by_gate[tdd.GATE_ID].ok)
        self.assertFalse(result.ok)


class TheLoopStopsOnAGateThatCannotJudge(unittest.TestCase):
    """An unconfigured gate ends the s4 loop at once instead of spending the budget."""

    POLICY = loop.LoopPolicy(True, max_iterations=5)

    def _result(self, gid="build", *, ok=False, unconfigured=True, on_fail="block"):
        return loop.GateResult(id=gid, ok=ok, on_fail=on_fail, unconfigured=unconfigured)

    def test_the_first_iteration_stops_blocked(self):
        decision = loop.decide(1, (self._result(),), self.POLICY)
        self.assertEqual(decision.status, loop.UNCONFIGURED)
        self.assertTrue(decision.blocked)
        self.assertIsNone(decision.next_iteration)
        self.assertEqual(decision.unconfigured, ("build",))
        self.assertEqual(decision.as_dict()["unconfigured"], ["build"])

    def test_an_ordinary_red_gate_still_continues(self):
        decision = loop.decide(1, (self._result(unconfigured=False),), self.POLICY)
        self.assertEqual(decision.status, loop.CONTINUE)
        self.assertFalse(decision.blocked)

    def test_a_soft_unconfigured_gate_does_not_stop_it(self):
        gate_results = (
            self._result("smoke", on_fail="suggest"),
            self._result("build", unconfigured=False),
        )
        decision = loop.decide(1, gate_results, self.POLICY)
        self.assertEqual(decision.status, loop.CONTINUE)
        self.assertEqual(decision.unconfigured, ())

    def test_no_brief_is_rendered_and_the_next_action_says_why(self):
        document = loop.brief_document(
            "base brief", iteration=1, gates=(self._result(),), policy=self.POLICY
        )
        self.assertIsNone(document["brief"])
        self.assertIsNone(document["prompt_file"])
        self.assertTrue(document["decision"]["blocked"])
        self.assertIn("build cannot judge", document["next_action"])
        self.assertIn("do not iterate again", document["next_action"])

    def test_parse_gates_reads_the_flag(self):
        [result] = loop.parse_gates(
            [{"gate": "build", "ok": False, "not_run": False, "unconfigured": True}]
        )
        self.assertTrue(result.unconfigured)
        [plain] = loop.parse_gates([{"gate": "build", "ok": False}])
        self.assertFalse(plain.unconfigured)


class TheLoopRecipeEndToEnd(_Project):
    """``run-gates --json`` into ``loop brief``: the s4 recipe, on an unset build command."""

    def _loop_brief(self, gates_line: str | None, knobs: str) -> tuple[int, dict]:
        project = self.project(gates_line, knobs)
        rc, report, _ = run(
            [
                "run-gates",
                project,
                "--root",
                str(self.root),
                "--phase",
                "s4",
                "--phases",
                "guard,test",
                "--defer-jury",
                "--json",
            ]
        )
        self.assertEqual(rc, 1, report)
        gates_file = self.root / "iter-1.json"
        gates_file.write_text(report, encoding="utf-8")
        brief = self.root / "brief.md"
        brief.write_text("implement the issue\n", encoding="utf-8")
        rc, out, err = run(
            [
                "loop",
                "brief",
                "--project",
                project,
                "--root",
                str(self.root),
                "--iteration",
                "1",
                "--brief",
                str(brief),
                "--gates",
                str(gates_file),
                "--max-iterations",
                "5",
                "--json",
            ]
        )
        return rc, json.loads(out or err)

    def test_an_unset_build_command_stops_the_loop_at_iteration_one(self):
        rc, document = self._loop_brief("gates: [build]", "  lint_cmd: make lint")
        self.assertEqual(rc, 1)
        self.assertEqual(document["decision"]["status"], "unconfigured")
        self.assertEqual(document["decision"]["unconfigured"], ["build"])
        self.assertIsNone(document["brief"])

    def test_an_empty_gate_list_stops_it_too(self):
        rc, document = self._loop_brief("gates: []", '  build_gate_cmd: "true"')
        self.assertEqual(rc, 1)
        self.assertEqual(document["decision"]["status"], "unconfigured")
        self.assertEqual(document["decision"]["unconfigured"], ["gates"])


if __name__ == "__main__":
    unittest.main()
