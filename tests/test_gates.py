"""Unit tests for gate planning + fail-soft execution."""

import unittest
from dataclasses import replace

from keel import config as cfg
from keel import gates, model
from keel.extensions import Extension
from keel.findings import Finding, summarize


def _config(gates_list=("build", "lint"), lint=True):
    data = {
        "extends": "keel",
        "core_version": "^0.1",
        "base_branch": "main",
        "knobs": {"build_gate_cmd": "make test"},
        "gates": list(gates_list),
    }
    if lint:
        data["knobs"]["lint_cmd"] = "make lint"
    return cfg.parse_config(data)


def _ext(eid, slot, on_fail="warn", kind="command", run="x"):
    mode = "deterministic" if kind == "command" else "agentic"
    return Extension(id=eid, slot=slot, kind=kind, mode=mode, agent="inherit", on_fail=on_fail,
                     anchorable=False, run=run, prompt=None, body="", source=f"{eid}.md")


class TestPlan(unittest.TestCase):
    def test_builtins_in_order(self):
        specs = gates.plan_gates(_config(("build", "lint", "jury")), {})
        self.assertEqual([s.id for s in specs], ["build", "lint", "jury"])
        self.assertEqual(specs[0].run, "make test")
        self.assertEqual(specs[2].kind, "builtin")

    def test_lint_skipped_when_absent(self):
        specs = gates.plan_gates(_config(("build", "lint"), lint=False), {})
        self.assertEqual([s.id for s in specs], ["build"])

    def test_unknown_builtin_raises(self):
        with self.assertRaises(gates.GateError):
            gates.plan_gates(_config(("build", "design-parity")), {})

    def test_extensions_appended_by_phase(self):
        loaded = {
            "guard": [_ext("guard-check", "guard", on_fail="block")],
            "tester": [_ext("design-parity", "tester")],
            "test": [_ext("test-extra", "test")],
            "pre-merge": [_ext("dp-gate", "pre-merge", on_fail="block")],
        }
        specs = gates.plan_gates(_config(("build",)), loaded)
        self.assertEqual([s.id for s in specs],
                         ["guard-check", "build", "design-parity", "test-extra", "dp-gate"])
        self.assertEqual([s.phase for s in specs],
                         ["guard", "test", "test", "test", "pre-merge"])

    def test_policy_pack_presets_slotted(self):
        data = {
            "extends": "keel",
            "core_version": "^0.1",
            "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test"},
            "gates": ["build"],
            "policy_pack": {
                "name": "sec",
                "presets": ["gitleaks", "semgrep", "bandit", "trivy"],
            },
        }
        specs = gates.plan_gates(cfg.parse_config(data), {})
        self.assertEqual(
            [s.id for s in specs],
            ["gitleaks", "build", "semgrep", "bandit", "trivy"],
        )
        self.assertEqual(
            [s.phase for s in specs],
            ["guard", "test", "test", "test", "test"],
        )
        self.assertEqual(specs[0].on_fail, "block")
        self.assertEqual(specs[1].on_fail, "block")
        self.assertEqual(specs[2].on_fail, "suggest")
        self.assertEqual(specs[3].on_fail, "suggest")
        self.assertEqual(specs[4].on_fail, "warn")


class TestRun(unittest.TestCase):
    def _specs(self):
        return gates.plan_gates(_config(("build", "lint")), {})

    def test_all_pass(self):
        outcomes = gates.run_gates(self._specs(), lambda s: (True, []))
        self.assertTrue(all(o.ok for o in outcomes))
        self.assertEqual(gates.collect_findings(outcomes), [])

    def test_failure_synthesizes_finding(self):
        outcomes = gates.run_gates(self._specs(), lambda s: (False, []))
        findings = gates.collect_findings(outcomes)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f.severity == "major" for f in findings))  # block -> major

    def test_explicit_findings_preserved(self):
        def runner(spec):
            return False, [Finding("minor", "nit-ish", spec.id)]
        outcomes = gates.run_gates(self._specs(), runner)
        sev = {f.severity for f in gates.collect_findings(outcomes)}
        self.assertEqual(sev, {"minor"})

    def test_hard_gate_error_blocks(self):
        spec = gates.GateSpec("dp", "command", "pre-merge", "block", run="x")

        def boom(s):
            raise RuntimeError("kaboom")

        outcomes = gates.run_gates([spec], boom)
        self.assertFalse(outcomes[0].ok)
        self.assertEqual(outcomes[0].findings[0].severity, "major")
        self.assertIn("kaboom", outcomes[0].error)

    def test_soft_gate_error_is_noop(self):
        spec = gates.GateSpec("flaky", "command", "test", "warn", run="x")

        def boom(s):
            raise RuntimeError("oops")

        outcomes = gates.run_gates([spec], boom)
        self.assertTrue(outcomes[0].ok)
        self.assertTrue(outcomes[0].skipped)
        self.assertEqual(outcomes[0].findings, ())

    def test_no_failsoft_reraises(self):
        spec = gates.GateSpec("x", "command", "test", "warn", run="x")
        with self.assertRaises(RuntimeError):
            gates.run_gates([spec], lambda s: (_ for _ in ()).throw(RuntimeError("x")),
                            fail_soft=False)

    def test_severity_mapping_for_soft_failure(self):
        spec = gates.GateSpec("s", "command", "test", "suggest", run="x")
        outcomes = gates.run_gates([spec], lambda s: (False, []))
        self.assertEqual(outcomes[0].findings[0].severity, "minor")


class TestGateTimeoutResolution(unittest.TestCase):
    """Per-gate timeout → project knob → built-in default (#622)."""

    def _config_with(self, timeout_s=None):
        data = {
            "extends": "keel",
            "core_version": "^0.1",
            "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test", "lint_cmd": "make lint"},
            "gates": ["build", "lint"],
        }
        if timeout_s is not None:
            data["knobs"]["gate_timeout_s"] = timeout_s
        return cfg.parse_config(data)

    def test_builtin_gates_inherit_the_project_knob(self):
        specs = gates.plan_gates(self._config_with(2400), {})
        self.assertEqual([s.timeout for s in specs], [2400, 2400])

    def test_builtin_gates_fall_back_to_the_default(self):
        specs = gates.plan_gates(self._config_with(), {})
        self.assertEqual(specs[0].timeout, model.DEFAULT_GATE_TIMEOUT_S)
        self.assertEqual(model.DEFAULT_GATE_TIMEOUT_S, 600)  # today's behaviour preserved

    def test_jury_builtin_carries_its_own_budget(self):
        # The jury builtin *does* shell out (via run_argv), so unlike an agentic gate it
        # needs a resolved limit — from knobs.jury_timeout_s, not gate_timeout_s.
        config = cfg.parse_config({
            "extends": "keel", "core_version": "^0.1", "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test", "gate_timeout_s": 1200,
                      "jury_timeout_s": 3600},
            "gates": ["jury"],
        })
        self.assertEqual(gates.plan_gates(config, {})[0].timeout, 3600)

    def test_jury_builtin_defaults_to_the_jury_constant(self):
        config = cfg.parse_config({
            "extends": "keel", "core_version": "^0.1", "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test"}, "gates": ["jury"],
        })
        self.assertEqual(gates.plan_gates(config, {})[0].timeout,
                         model.DEFAULT_JURY_TIMEOUT_S)

    def test_extension_timeout_wins_over_the_project_knob(self):
        piece = _ext("slow-gate", "pre-merge", on_fail="block")
        piece = replace(piece, timeout=5400)
        specs = gates.plan_gates(self._config_with(1200), {"pre-merge": [piece]})
        self.assertEqual(specs[-1].timeout, 5400)

    def test_extension_without_timeout_inherits_the_project_knob(self):
        specs = gates.plan_gates(self._config_with(1200), {"tester": [_ext("t", "tester")]})
        self.assertEqual(specs[-1].timeout, 1200)

    def test_guard_extension_also_resolves(self):
        specs = gates.plan_gates(self._config_with(1200), {"guard": [_ext("g", "guard")]})
        self.assertEqual(specs[0].timeout, 1200)

    def test_agentic_extension_carries_no_timeout(self):
        # Nothing shells out for an agentic piece, so a number here would advertise a
        # limit that is never applied.
        piece = _ext("a", "tester", kind="agentic", run=None)
        specs = gates.plan_gates(self._config_with(1200), {"tester": [piece]})
        self.assertIsNone(specs[-1].timeout)

    def test_agentic_guard_extension_carries_no_timeout(self):
        piece = _ext("a", "guard", kind="agentic", run=None)
        specs = gates.plan_gates(self._config_with(1200), {"guard": [piece]})
        self.assertIsNone(specs[0].timeout)


class TestTimedOutOutcome(unittest.TestCase):
    """GateOutcome.timed_out is descriptive only — it never softens the gate (#622)."""

    def _spec(self, on_fail="block"):
        return gates.GateSpec("build", "command", "test", on_fail, run="x")

    def test_timeout_is_recorded_and_still_blocks(self):
        outcomes = gates.run_gates(
            [self._spec()],
            lambda s: (False, [Finding("major", "timed out after 600s", "build")], True),
        )
        self.assertTrue(outcomes[0].timed_out)
        self.assertFalse(outcomes[0].ok)  # the merge gate is unchanged

    def test_plain_failure_is_not_flagged_as_a_timeout(self):
        outcomes = gates.run_gates(
            [self._spec()], lambda s: (False, [Finding("major", "boom", "build")], False))
        self.assertFalse(outcomes[0].timed_out)

    def test_two_tuple_runner_still_supported(self):
        # Agentic / builtin dispatchers cannot time out and return the older shape.
        outcomes = gates.run_gates([self._spec()], lambda s: (False, []))
        self.assertFalse(outcomes[0].timed_out)
        self.assertFalse(outcomes[0].ok)

    def test_passing_gate_is_never_a_timeout(self):
        outcomes = gates.run_gates([self._spec()], lambda s: (True, [], False))
        self.assertTrue(outcomes[0].ok)
        self.assertFalse(outcomes[0].timed_out)

    def test_timeout_findings_still_block_the_merge(self):
        outcomes = gates.run_gates(
            [self._spec()],
            lambda s: (False, [Finding("major", "timed out after 600s", "build")], True),
        )
        self.assertTrue(summarize(gates.collect_findings(outcomes)).blocked)

    def test_errored_gate_is_not_a_timeout(self):
        def boom(spec):
            raise RuntimeError("x")

        outcomes = gates.run_gates([self._spec()], boom)
        self.assertFalse(outcomes[0].timed_out)

    def test_generator_returning_runner_still_works(self):
        # The runner contract has always been "any 2-iterable". Indexing the raw
        # return would have made a soft gate silently pass via the fail-soft path.
        outcomes = gates.run_gates(
            [gates.GateSpec("g", "command", "test", "warn", run="x")],
            lambda s: (v for v in (False, [Finding("nit", "boom", "g")])),
        )
        self.assertFalse(outcomes[0].ok)
        self.assertIsNone(outcomes[0].error)
        self.assertFalse(outcomes[0].skipped)

    def test_non_true_third_element_is_not_a_timeout(self):
        outcomes = gates.run_gates([self._spec()], lambda s: (False, [], "oops"))
        self.assertFalse(outcomes[0].timed_out)




class TestNotRunPropagation(unittest.TestCase):
    """`not_run` + `on_fail` must survive the whole chain, or #626 re-opens.

    The leaf predicate (`ledger.record_gates_passed`) was unit-tested against
    hand-written dicts, so `on_fail=spec.on_fail` in `run_gates` and the two keys in
    `build_ship_run_record` were both green when mutated to a permissive constant —
    the gate refusal would have quietly stopped refusing.
    """

    def _spec(self, on_fail):
        return gates.GateSpec("security-review", "agentic", "pre-merge", on_fail)

    def _record(self, outcomes):
        from types import SimpleNamespace

        from keel import ledger
        return ledger.build_ship_run_record(
            command="ship", base_branch="main", changed_files=["a.py"],
            outcomes=outcomes,
            verdict=summarize(gates.collect_findings(outcomes)),
            assessment=SimpleNamespace(
                tier=2, reviewers=2, window_open=True, ci_ok=None,
                merge=SimpleNamespace(action="merge", reason="ok"),
                halted=False, bypassed_window=False),
        )

    def test_a_blocking_agentic_gate_never_certifies_end_to_end(self):
        from keel import ledger
        from keel.runner import command_gate_runner

        outcomes = gates.run_gates([self._spec("block")], command_gate_runner("."))

        self.assertTrue(outcomes[0].not_run)
        self.assertEqual(outcomes[0].on_fail, "block")
        self.assertEqual(gates.unrun_blocking(outcomes), ("security-review",))
        self.assertFalse(ledger.record_gates_passed(self._record(outcomes)))

    def test_an_advisory_agentic_gate_still_certifies_end_to_end(self):
        from keel import ledger
        from keel.runner import command_gate_runner

        outcomes = gates.run_gates([self._spec("warn")], command_gate_runner("."))

        self.assertTrue(outcomes[0].not_run)
        self.assertEqual(gates.unrun_blocking(outcomes), ())
        self.assertTrue(ledger.record_gates_passed(self._record(outcomes)))

    def test_a_recorded_result_lets_the_run_certify(self):
        from keel import ledger
        from keel.runner import command_gate_runner

        outcomes = gates.run_gates([self._spec("block")], command_gate_runner("."))
        applied, rejected = gates.apply_recorded_results(outcomes, {"security-review": "pass"})
        self.assertEqual(rejected, [])

        self.assertFalse(applied[0].not_run)     # it *was* run — by the agent
        self.assertEqual(gates.unrun_blocking(applied), ())
        self.assertTrue(ledger.record_gates_passed(self._record(applied)))

    def test_a_recorded_failure_blocks_at_the_declared_severity(self):
        from keel import ledger
        from keel.runner import command_gate_runner

        outcomes = gates.run_gates([self._spec("block")], command_gate_runner("."))
        applied, rejected = gates.apply_recorded_results(outcomes, {"security-review": "fail"})
        self.assertEqual(rejected, [])

        self.assertFalse(applied[0].ok)
        self.assertFalse(applied[0].not_run)
        self.assertEqual(applied[0].findings[0].severity, "major")
        self.assertFalse(ledger.record_gates_passed(self._record(applied)))

    def test_an_unnamed_gate_is_left_alone(self):
        outcomes = [gates.GateOutcome("build", True, not_run=True, on_fail="block")]
        self.assertEqual(gates.apply_recorded_results(outcomes, {"other": "fail"}),
                         (outcomes, []))

    def test_a_gate_keel_executed_cannot_be_overridden(self):
        # The channel exists for gates keel *cannot* run. Letting it override a measured
        # verdict would certify a run whose gates were observed failing — the same
        # fail-open from the other direction. A `warn` gate is the sharp case: its
        # failure carries no blocking finding to save it.
        from keel import ledger
        failed = gates.GateOutcome(
            "flaky-check", False,
            (Finding("nit", "flaky-check failed (exit 1)", "flaky-check"),),
            on_fail="warn")
        applied, rejected = gates.apply_recorded_results(applied_in := [failed],
                                                        {"flaky-check": "pass"})

        self.assertEqual(rejected, ["flaky-check"])
        self.assertEqual(applied, applied_in)     # verdict untouched
        self.assertFalse(ledger.record_gates_passed(self._record(applied)))

    def test_a_recorded_result_drops_timed_out_and_skipped(self):
        # A not-run gate can be neither, so carrying either forward would produce a
        # self-contradictory record (and lose the TIMEOUT label's meaning).
        stale = gates.GateOutcome("g", True, not_run=True, timed_out=True, skipped=True,
                                  on_fail="block")
        for verdict in ("pass", "fail"):
            with self.subTest(verdict=verdict):
                applied, _ = gates.apply_recorded_results([stale], {"g": verdict})

                self.assertFalse(applied[0].timed_out)
                self.assertFalse(applied[0].skipped)
                self.assertFalse(applied[0].not_run)

    def test_a_not_run_gate_reported_as_failing_keeps_the_flag(self):
        # Unreachable with the in-tree runners, but the contract allows it and a dropped
        # flag would silently re-open the certification hole.
        def runner(spec):
            return False, [], False, True

        outcomes = gates.run_gates([self._spec("block")], runner)
        self.assertTrue(outcomes[0].not_run)

    def test_concurrent_execution_preserves_order(self):
        import time

        def runner(spec):
            # simulate variable duration
            if spec.id == "slow":
                time.sleep(0.05)
            return True, [Finding("nit", f"note from {spec.id}", spec.id)]

        specs = [
            gates.GateSpec("slow", "command", "test", "warn", run="sleep 0.05"),
            gates.GateSpec("fast1", "command", "test", "warn", run="echo 1"),
            gates.GateSpec("fast2", "command", "test", "warn", run="echo 2"),
        ]
        outcomes = gates.run_gates(specs, runner, concurrency=3)
        self.assertEqual([o.gate for o in outcomes], ["slow", "fast1", "fast2"])
        self.assertTrue(all(o.ok for o in outcomes))

    def test_concurrent_execution_handles_exceptions_fail_soft(self):
        def runner(spec):
            if spec.id == "broken":
                raise RuntimeError("gate crashed")
            return True, []

        specs = [
            gates.GateSpec("ok1", "command", "test", "block", run="echo 1"),
            gates.GateSpec("broken", "command", "test", "block", run="boom"),
            gates.GateSpec("ok2", "command", "test", "block", run="echo 2"),
        ]
        outcomes = gates.run_gates(specs, runner, concurrency=2)
        self.assertEqual([o.gate for o in outcomes], ["ok1", "broken", "ok2"])
        self.assertTrue(outcomes[0].ok)
        self.assertFalse(outcomes[1].ok)
        self.assertEqual(outcomes[1].error, "gate crashed")
        self.assertTrue(outcomes[2].ok)


class TestScanPresetExclusions(unittest.TestCase):
    """The bandit preset must not walk trees that produce only false findings.

    `bandit -r .` walks everything below the working directory, including trees
    git is told to ignore. Before #834 that meant a local `.venv` (875 high
    findings from installed dependencies), nested checkouts under
    `.claude/worktrees/`, and `tests/` — where hardcoded temp paths, `urlopen`,
    and subprocesses are normal rather than defects. All 23 findings were in test
    code and none in `src/`, so the gate was permanently red and read as noise.
    """

    def _bandit_cmd(self) -> str:
        return gates.POLICY_PACK_PRESETS["bandit"][3]

    def test_bandit_excludes_the_noise_directories(self):
        cmd = self._bandit_cmd()
        self.assertIn("-x", cmd)
        for name in ("tests", ".venv", "venv", "node_modules", "site-packages"):
            self.assertIn(name, cmd, f"{name} is not excluded from the bandit scan")

    def test_exclusions_are_globs_not_anchored_paths(self):
        # A fixed `./tests` does not match `./.claude/worktrees/<name>/tests`, so
        # nested checkouts leak back in. Same prefix-anchoring trap as #820.
        for pattern in self._parsed_exclusions():
            self.assertTrue(
                pattern.startswith("*/"),
                f"exclusion {pattern!r} is prefix-anchored; use a */glob/* form",
            )

    def test_exclusions_match_a_nested_path(self):
        # The property that actually matters, asserted rather than assumed.
        import fnmatch
        nested = "./.claude/worktrees/some-branch/tests/test_cli.py"
        self.assertTrue(
            any(fnmatch.fnmatch(nested, p) for p in self._parsed_exclusions()),
            "a nested worktree's tests/ would still be scanned",
        )

    def test_bandit_stays_advisory(self):
        # Excluding directories is about signal quality, not about weakening the
        # gate's standing: it must remain a `suggest`, never silently become
        # blocking or non-reporting.
        gate_id, phase, on_fail, _cmd = gates.POLICY_PACK_PRESETS["bandit"]
        self.assertEqual((gate_id, phase, on_fail), ("bandit", "test", "suggest"))

    def _parsed_exclusions(self) -> list[str]:
        cmd = self._bandit_cmd()
        raw = cmd.split("-x", 1)[1].strip().strip("'\"")
        return [part for part in raw.split(",") if part]


if __name__ == "__main__":
    unittest.main()
