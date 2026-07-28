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

    def test_jury_builtin_carries_no_subprocess_timeout(self):
        config = cfg.parse_config({
            "extends": "keel", "core_version": "^0.1", "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test"}, "gates": ["jury"],
        })
        self.assertIsNone(gates.plan_gates(config, {})[0].timeout)

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


if __name__ == "__main__":
    unittest.main()
