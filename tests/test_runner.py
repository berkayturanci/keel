"""Unit tests for the shell-command gate runner (injected subprocess)."""

import subprocess
import unittest

from keel import runner
from keel.gates import GateSpec


class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _ok(*a, **k):
    return _Proc(0, "all good")


def _fail(*a, **k):
    return _Proc(1, "", "boom\nsecond line")


def _timeout(*a, **k):
    raise subprocess.TimeoutExpired(cmd="x", timeout=1)


def _oserror(*a, **k):
    raise OSError("no such binary")


class TestRunCommand(unittest.TestCase):
    def test_ok(self):
        r = runner.run_command("echo hi", _run=_ok)
        self.assertTrue(r.ok)
        self.assertEqual(r.code, 0)
        self.assertIn("all good", r.output)

    def test_fail_merges_stdout_stderr(self):
        r = runner.run_command("false", _run=_fail)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, 1)
        self.assertIn("boom", r.output)

    def test_timeout_failsoft(self):
        r = runner.run_command("sleep 99", timeout=1, _run=_timeout)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, 124)
        self.assertIn("timed out", r.output)

    def test_oserror_failsoft(self):
        r = runner.run_command("nope", _run=_oserror)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, 127)
        self.assertIn("no such binary", r.output)


class TestRunArgv(unittest.TestCase):
    def test_ok(self):
        r = runner.run_argv(["git", "status"], _run=_ok)
        self.assertTrue(r.ok)
        self.assertIn("all good", r.output)

    def test_timeout_failsoft(self):
        r = runner.run_argv(["git", "status"], _run=_timeout)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, 124)

    def test_oserror_failsoft(self):
        r = runner.run_argv(["nope"], _run=_oserror)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, 127)


class TestCommandGateRunner(unittest.TestCase):
    def _spec(self, on_fail="block"):
        return GateSpec("build", "command", "test", on_fail, run="make test")

    def test_pass(self):
        run = runner.command_gate_runner(_run=_ok)
        ok, findings = run(self._spec())
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_fail_produces_finding_with_tail(self):
        run = runner.command_gate_runner(_run=_fail)
        ok, findings = run(self._spec(on_fail="block"))
        self.assertFalse(ok)
        self.assertEqual(findings[0].severity, "major")  # block -> major
        self.assertIn("second line", findings[0].message)

    def test_soft_fail_severity(self):
        run = runner.command_gate_runner(_run=_fail)
        _, findings = run(self._spec(on_fail="warn"))
        self.assertEqual(findings[0].severity, "nit")

    def test_non_command_gate_is_noop(self):
        run = runner.command_gate_runner(_run=_fail)
        ok, findings = run(GateSpec("jury", "builtin", "test", "block"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_fail_without_location_has_no_path(self):
        run = runner.command_gate_runner(_run=_fail)
        _, findings = run(self._spec())
        self.assertIsNone(findings[0].path)
        self.assertFalse(findings[0].anchorable)

    def test_fail_with_location_is_anchorable(self):
        def _located(*a, **k):
            return _Proc(1, "src/app.py:42:5: undefined name 'x'", "")
        _, findings = runner.command_gate_runner(_run=_located)(self._spec())
        self.assertEqual(findings[0].path, "src/app.py")
        self.assertEqual(findings[0].line, 42)
        self.assertTrue(findings[0].anchorable)


def _dispatch(diff="patch", jury_json='{"findings":[]}'):
    def _run(argv, **kwargs):
        if argv and argv[0] == "git":
            return _Proc(0, diff)
        if argv and argv[0] == "jury":
            return _Proc(0, jury_json)
        return _Proc(0, "")
    return _run


class TestGateRunner(unittest.TestCase):
    def _jury_spec(self):
        return GateSpec("jury", "builtin", "test", "block")

    def test_jury_builtin_blocks_on_major(self):
        out = '{"findings":[{"severity":"major","file":"x.py","line":1,"claim":"bad"}]}'
        run = runner.gate_runner(".", base="main", _run=_dispatch(jury_json=out))
        ok, findings = run(self._jury_spec())
        self.assertFalse(ok)
        self.assertEqual(findings[0].source, "jury")

    def test_jury_builtin_clean(self):
        run = runner.gate_runner(".", base="main", _run=_dispatch())
        ok, findings = run(self._jury_spec())
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_jury_without_base(self):
        run = runner.gate_runner(".", base=None, _run=_dispatch())
        ok, findings = run(self._jury_spec())
        self.assertTrue(ok)

    def test_jury_diff_failsoft(self):
        def _run(argv, **kwargs):
            if argv and argv[0] == "git":
                return _Proc(1, "", "not a repo")  # diff fails -> empty diff
            return _Proc(0, "{}")
        ok, findings = runner.gate_runner(".", base="main", _run=_run)(self._jury_spec())
        self.assertTrue(ok)

    def test_command_gate_delegates(self):
        run = runner.gate_runner(_run=_ok)
        ok, findings = run(GateSpec("build", "command", "test", "block", run="make test"))
        self.assertTrue(ok)


class TestFirstLocation(unittest.TestCase):
    def test_path_line_col(self):
        self.assertEqual(runner.first_location("lib/x.dart:7:3: bad"), ("lib/x.dart", 7))

    def test_path_line_only(self):
        self.assertEqual(runner.first_location("a/b.py:12: oops"), ("a/b.py", 12))

    def test_first_match_wins(self):
        self.assertEqual(runner.first_location("no loc\nsrc/a.py:1: x\nsrc/b.py:2: y"),
                         ("src/a.py", 1))

    def test_none_when_absent(self):
        self.assertEqual(runner.first_location("just a message"), (None, None))


if __name__ == "__main__":
    unittest.main()
