"""Unit tests for gate planning + fail-soft execution."""

import unittest

from keel import config as cfg
from keel import gates
from keel.extensions import Extension
from keel.findings import Finding


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


if __name__ == "__main__":
    unittest.main()
