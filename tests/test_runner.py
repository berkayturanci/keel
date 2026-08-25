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

    def test_stdin_is_devnull(self):
        passed_kwargs = {}

        def _recording_run(*a, **k):
            passed_kwargs.update(k)
            return _Proc(0, "ok")

        runner.run_command("echo test", _run=_recording_run)
        self.assertEqual(passed_kwargs.get("stdin"), subprocess.DEVNULL)


class TestRunArgv(unittest.TestCase):
    def test_ok(self):
        r = runner.run_argv(["git", "status"], _run=_ok)
        self.assertTrue(r.ok)
        self.assertIn("all good", r.output)

    def test_stdin_is_devnull(self):
        passed_kwargs = {}

        def _recording_run(*a, **k):
            passed_kwargs.update(k)
            return _Proc(0, "ok")

        runner.run_argv(["git", "status"], _run=_recording_run)
        self.assertEqual(passed_kwargs.get("stdin"), subprocess.DEVNULL)

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
        ok, findings, timed_out, _nr = run(self._spec())
        self.assertTrue(ok)
        self.assertEqual(findings, [])
        self.assertFalse(timed_out)

    def test_fail_produces_finding_with_tail(self):
        run = runner.command_gate_runner(_run=_fail)
        ok, findings, timed_out, _nr = run(self._spec(on_fail="block"))
        self.assertFalse(ok)
        self.assertFalse(timed_out)  # a plain failure is not a timeout
        self.assertEqual(findings[0].severity, "major")  # block -> major
        self.assertIn("second line", findings[0].message)

    def test_soft_fail_severity(self):
        run = runner.command_gate_runner(_run=_fail)
        _, findings, _, _nr = run(self._spec(on_fail="warn"))
        self.assertEqual(findings[0].severity, "nit")

    def test_non_command_gate_is_noop(self):
        run = runner.command_gate_runner(_run=_fail)
        ok, findings, timed_out, _nr = run(GateSpec("jury", "builtin", "test", "block"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])
        self.assertFalse(timed_out)

    def test_fail_without_location_has_no_path(self):
        run = runner.command_gate_runner(_run=_fail)
        _, findings, _, _nr = run(self._spec())
        self.assertIsNone(findings[0].path)
        self.assertFalse(findings[0].anchorable)

    def test_fail_with_location_is_anchorable(self):
        def _located(*a, **k):
            return _Proc(1, "src/app.py:42:5: undefined name 'x'", "")

        _, findings, _, _nr = runner.command_gate_runner(_run=_located)(self._spec())
        self.assertEqual(findings[0].path, "src/app.py")
        self.assertEqual(findings[0].line, 42)
        self.assertTrue(findings[0].anchorable)


class TestGateTimeout(unittest.TestCase):
    """A timeout is reported apart from a failure — but still blocks (#622)."""

    def _spec(self, on_fail="block", timeout=None):
        return GateSpec("build", "command", "test", on_fail, run="make test", timeout=timeout)

    def test_run_command_marks_timeout(self):
        r = runner.run_command("make test", _run=_timeout)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.code, 124)

    def test_run_command_failure_is_not_a_timeout(self):
        self.assertFalse(runner.run_command("make test", _run=_fail).timed_out)

    def test_run_argv_marks_timeout(self):
        self.assertTrue(runner.run_argv(["git", "status"], _run=_timeout).timed_out)

    def test_oserror_is_not_a_timeout(self):
        self.assertFalse(runner.run_command("nope", _run=_oserror).timed_out)

    def test_timeout_still_blocks_with_unchanged_severity(self):
        # The merge-gate invariant: a timed-out gate is as red as a failing one.
        ok, findings, timed_out, _nr = runner.command_gate_runner(_run=_timeout)(self._spec())
        self.assertFalse(ok)
        self.assertTrue(timed_out)
        self.assertEqual(findings[0].severity, "major")  # block -> major, same as a failure

    def test_soft_timeout_keeps_soft_severity(self):
        _, findings, _, _nr = runner.command_gate_runner(_run=_timeout)(self._spec(on_fail="warn"))
        self.assertEqual(findings[0].severity, "nit")

    def test_timeout_message_is_distinct_from_failure(self):
        _, findings, _, _nr = runner.command_gate_runner(_run=_timeout)(self._spec())
        message = findings[0].message
        self.assertIn("timed out after", message)
        self.assertIn("no pass/fail result", message)
        self.assertIn("gate_timeout_s", message)  # tells the operator how to fix it
        self.assertNotIn("build failed", message)

    def test_timeout_finding_is_not_anchored(self):
        _, findings, _, _nr = runner.command_gate_runner(_run=_timeout)(self._spec())
        self.assertIsNone(findings[0].path)
        self.assertFalse(findings[0].anchorable)

    def test_spec_timeout_overrides_the_runner_default(self):
        seen = {}

        def _capture(*a, **k):
            seen["timeout"] = k["timeout"]
            return _Proc(0)

        runner.command_gate_runner(timeout=600, _run=_capture)(self._spec(timeout=3600))
        self.assertEqual(seen["timeout"], 3600)

    def test_runner_default_applies_without_a_spec_timeout(self):
        seen = {}

        def _capture(*a, **k):
            seen["timeout"] = k["timeout"]
            return _Proc(0)

        runner.command_gate_runner(timeout=900, _run=_capture)(self._spec())
        self.assertEqual(seen["timeout"], 900)

    def test_message_quotes_the_effective_limit(self):
        _, findings, _, _nr = runner.command_gate_runner(_run=_timeout)(self._spec(timeout=1800))
        self.assertIn("timed out after 1800s", findings[0].message)


class TestFirstLocation(unittest.TestCase):
    def test_path_line_col(self):
        self.assertEqual(runner.first_location("lib/x.dart:7:3: bad"), ("lib/x.dart", 7))

    def test_path_line_only(self):
        self.assertEqual(runner.first_location("a/b.py:12: oops"), ("a/b.py", 12))

    def test_first_match_wins(self):
        self.assertEqual(
            runner.first_location("no loc\nsrc/a.py:1: x\nsrc/b.py:2: y"), ("src/a.py", 1)
        )

    def test_none_when_absent(self):
        self.assertEqual(runner.first_location("just a message"), (None, None))

    # --- equivalence guards -------------------------------------------------
    # These would FAIL if the single-pass ``re.MULTILINE`` search lost any of
    # the per-line ``.match`` semantics of the original ``splitlines`` loop.

    def test_first_match_wins_skips_leading_colon_noise(self):
        # A ``Warning: build failed`` line has a colon but no ``path:line``.
        # ``.search`` must not false-hit it; the anchored ``^`` + required digit
        # must skip to the first real location on a later line.
        text = "Warning: build failed\nsrc/app.py:42: msg"
        self.assertEqual(runner.first_location(text), ("src/app.py", 42))

    def test_path_line_col_form(self):
        self.assertEqual(runner.first_location("src/x.py:12:5: error"), ("src/x.py", 12))

    def test_leading_whitespace_location_line(self):
        # Leading-whitespace tolerance (``[ \t]*``) on the location line itself.
        self.assertEqual(runner.first_location("  src/x.py:7: warn"), ("src/x.py", 7))

    def test_bare_note_line_is_skipped(self):
        # ``Note: text`` has a colon but no digit after it -> skipped; the next
        # real location on the following line is returned.
        text = "Note: text\nsrc/real.py:3: detail"
        self.assertEqual(runner.first_location(text), ("src/real.py", 3))

    def test_match_at_end_of_line_no_trailing_token(self):
        # The line number sits at end-of-line with no trailing ``:`` or space;
        # only the ``|$`` branch can accept it. ``$`` must anchor per-line under
        # ``re.MULTILINE`` so a later line does not bleed into the path.
        self.assertEqual(runner.first_location("src/x.py:9\nnext line"), ("src/x.py", 9))
        self.assertEqual(runner.first_location("src/x.py:9"), ("src/x.py", 9))

    def test_path_cannot_span_a_newline(self):
        # The path classes exclude ``\n`` so a ``.search`` can never stitch a
        # non-location first line onto a colon/digit on the next line.
        self.assertEqual(runner.first_location("no-colon-here\n42: nope"), (None, None))

    def test_location_must_start_at_line_start(self):
        # ``^[ \t]*`` anchors the path to the start of a line: a leading
        # ``Error:`` token (colon, no digit) blocks the match on that line, so a
        # mid-line ``config.py:3`` after it is NOT picked up. This guards the
        # ``^`` anchor (a bare un-anchored ``search`` would wrongly return
        # ``("at config.py", 3)``) and matches the original per-line loop.
        self.assertEqual(runner.first_location("Error: at config.py:3 invalid"), (None, None))

    def test_windows_drive_path_is_documented_limitation(self):
        # Documented limitation (unchanged from the per-line loop): a Windows
        # ``C:\`` drive letter is read as ``path=C`` then a non-digit ``\``, so
        # the whole thing fails to match -> ``(None, None)``.
        self.assertEqual(runner.first_location(r"C:\proj\x.py:10: err"), (None, None))


if __name__ == "__main__":
    unittest.main()
