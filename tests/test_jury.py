"""Unit tests for the jury built-in gate (pure parse + fail-soft runner)."""

import json
import subprocess
import unittest

from keel import jury

SAMPLE = {
    "findings": [
        {
            "severity": "major",
            "file": "src/x.py",
            "line": 42,
            "claim": "unchecked return",
            "reviewer": "claude",
        },
        {
            "severity": "minor",
            "file": "src/x.py",
            "line": 7,
            "claim": "missing docstring",
            "reviewer": "codex",
        },
        {"severity": "nit", "file": None, "line": None, "claim": "style", "reviewer": "agy"},
    ]
}


class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _jury_ok(argv, **kw):
    if "--version" in argv:
        return _Proc(0, "jury 1.0")
    return _Proc(1, json.dumps(SAMPLE))  # nonzero exit (REQUEST CHANGES) but JSON on stdout


def _jury_absent(argv, **kw):
    raise OSError("no jury on PATH")


class TestMapSeverity(unittest.TestCase):
    def test_known(self):
        self.assertEqual(jury.map_severity("major"), "major")
        self.assertEqual(jury.map_severity("BLOCKER"), "critical")
        self.assertEqual(jury.map_severity("info"), "nit")

    def test_unknown_defaults_minor(self):
        self.assertEqual(jury.map_severity("weird"), "minor")
        self.assertEqual(jury.map_severity(""), "minor")


class TestParseFindings(unittest.TestCase):
    def test_dict(self):
        fs = jury.parse_findings(SAMPLE)
        self.assertEqual(len(fs), 3)
        self.assertEqual(fs[0].severity, "major")
        self.assertEqual(fs[0].path, "src/x.py")
        self.assertEqual(fs[0].line, 42)
        self.assertTrue(fs[0].anchorable)
        self.assertEqual(fs[0].source, "jury:claude")
        self.assertFalse(fs[2].anchorable)  # null file/line

    def test_raw_string(self):
        self.assertEqual(len(jury.parse_findings(json.dumps(SAMPLE))), 3)

    def test_bad_string(self):
        self.assertEqual(jury.parse_findings("not json"), [])

    def test_non_dict(self):
        self.assertEqual(jury.parse_findings([1, 2]), [])

    def test_no_findings_key(self):
        self.assertEqual(jury.parse_findings({"x": 1}), [])

    def test_defaults_for_missing_fields(self):
        fs = jury.parse_findings({"findings": [{"severity": "minor", "file": "a", "line": 1}]})
        self.assertEqual(fs[0].message, "(jury finding)")
        self.assertEqual(fs[0].source, "jury:consensus")


class TestAvailable(unittest.TestCase):
    def test_present(self):
        self.assertTrue(jury.available(_run=_jury_ok))

    def test_absent(self):
        self.assertFalse(jury.available(_run=_jury_absent))


class TestRunGate(unittest.TestCase):
    def test_no_diff_is_noop(self):
        self.assertEqual(jury.run_gate("", _run=_jury_ok), (True, [], False))

    def test_absent_is_noop(self):
        self.assertEqual(jury.run_gate("a diff", _run=_jury_absent), (True, [], False))

    def test_oversized_diff_skips_cli_but_emits_advisory(self):
        def fail_if_called(argv, **kw):
            raise AssertionError(f"unexpected jury call: {argv}")

        ok, findings, timed_out = jury.run_gate(
            "x" * (jury.MAX_DIFF_BYTES + 1), _run=fail_if_called
        )

        # Non-blocking (does not gate) but no longer silent: the skip is surfaced.
        self.assertTrue(ok)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "nit")
        self.assertEqual(findings[0].source, "jury:skipped-oversize")
        self.assertIn("over the", findings[0].message)

    def test_oversized_diff_blocks_in_gating_mode(self):
        def fail_if_called(argv, **kw):
            raise AssertionError(f"unexpected jury call: {argv}")

        ok, findings, timed_out = jury.run_gate(
            "x" * (jury.MAX_DIFF_BYTES + 1), mode="gating", _run=fail_if_called
        )

        self.assertFalse(ok)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "major")
        self.assertEqual(findings[0].source, "jury:skipped-oversize")

    def test_present_blocks_on_major(self):
        ok, fs, _to = jury.run_gate("some diff", _run=_jury_ok)
        self.assertFalse(ok)
        self.assertEqual(len(fs), 3)

    def test_present_clean_passes(self):
        def clean(argv, **kw):
            if "--version" in argv:
                return _Proc(0)
            return _Proc(
                0,
                json.dumps(
                    {"findings": [{"severity": "minor", "file": "a", "line": 1, "claim": "x"}]}
                ),
            )

        ok, fs, _to = jury.run_gate("diff", _run=clean)
        self.assertTrue(ok)
        self.assertEqual(len(fs), 1)


def _jury_hangs(argv, **kw):
    if "--version" in argv:
        return _Proc(0, "jury 1.0")
    raise subprocess.TimeoutExpired(cmd="jury", timeout=600)


def _jury_crashes(argv, **kw):
    if "--version" in argv:
        return _Proc(0, "jury 1.0")
    return _Proc(2, "", "Traceback (most recent call last): boom")


class TestIncompleteRun(unittest.TestCase):
    """A jury run that produced no verdict must never read as a clean pass (#624)."""

    def test_timeout_blocks_in_gating_mode(self):
        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_hangs)
        self.assertFalse(ok)
        self.assertEqual(fs[0].severity, "major")
        self.assertEqual(fs[0].source, "jury:incomplete-run")

    def test_crash_blocks_in_gating_mode(self):
        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_crashes)
        self.assertFalse(ok)
        self.assertEqual(fs[0].severity, "major")

    def test_advisory_mode_surfaces_it_without_blocking(self):
        # `minor`, not the oversize branch's `nit`: an oversize diff is a deterministic
        # skip the operator can see from the diff itself, while an incomplete run is an
        # invisible operational failure that will recur silently. `minor` is keel's
        # gated-suggestion tier — it surfaces without blocking.
        for runner in (_jury_hangs, _jury_crashes):
            with self.subTest(runner=runner.__name__):
                ok, fs, _to = jury.run_gate("diff", mode="advisory", _run=runner)
                self.assertTrue(ok)
                self.assertEqual(fs[0].severity, "minor")
                self.assertEqual(fs[0].source, "jury:incomplete-run")

    def test_timeout_message_names_the_limit_and_the_knob(self):
        _, fs, _to = jury.run_gate("diff", mode="gating", timeout=1800, _run=_jury_hangs)
        self.assertIn("timed out after 1800s", fs[0].message)
        self.assertIn("jury_timeout_s", fs[0].message)

    def test_crash_message_names_the_exit_code_and_is_not_a_timeout(self):
        _, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_crashes)
        self.assertIn("exited 2", fs[0].message)
        self.assertNotIn("timed out", fs[0].message)

    def test_nonzero_exit_carrying_findings_is_still_a_verdict(self):
        # ai-jury signals REQUEST CHANGES with a nonzero exit; that is a completed
        # review, not an incomplete run, so its findings must be used as-is.
        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_ok)
        self.assertFalse(ok)  # SAMPLE carries a major
        self.assertEqual([f.source for f in fs if f.source == "jury:incomplete-run"], [])
        self.assertEqual(len(fs), 3)

    def test_absent_cli_is_still_a_clean_no_op(self):
        # keel does not depend on ai-jury; an uninstalled CLI is not an incomplete run.
        self.assertEqual(jury.run_gate("diff", mode="gating", _run=_jury_absent), (True, [], False))

    def test_clean_run_with_zero_findings_is_a_pass(self):
        # The inverse failure, and the costlier one: if "no findings" were mistaken for
        # "no verdict", every clean jury run would block the merge.
        def _clean_empty(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            return _Proc(0, json.dumps({"findings": []}))

        self.assertEqual(jury.run_gate("diff", mode="gating", _run=_clean_empty), (True, [], False))

    def test_report_followed_by_stderr_noise_still_parses(self):
        # run_argv hands back stdout + stderr concatenated and ai-jury logs progress to
        # stderr, so a real report is always followed by "[jury] ..." lines. Parsing
        # strictly discards every finding and reports a completed panel as a crash.
        def _noisy(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            return _Proc(1, json.dumps(SAMPLE), "[jury] round 1: 3 agents reviewing\n")

        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_noisy)
        self.assertFalse(ok)
        self.assertEqual(len(fs), 3)  # findings survive
        self.assertEqual(fs[0].source, "jury:claude")  # not jury:incomplete-run

    def test_clean_exit_with_unreadable_output_is_not_a_pass(self):
        # A zero exit carrying no report is still no review. Keying on "did we parse a
        # verdict" rather than "was the exit code zero" is what catches this.
        def _garbage(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            return _Proc(0, "not a report at all")

        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_garbage)
        self.assertFalse(ok)
        self.assertEqual(fs[0].source, "jury:incomplete-run")

    def test_disabled_mode_is_treated_as_advisory(self):
        # resolve_jury returns mode "off" when the jury is disabled, and cli threads it
        # straight through, so `--no-jury` with `gates: [jury]` still reaches this code.
        ok, fs, _to = jury.run_gate("diff", mode="off", _run=_jury_hangs)
        self.assertTrue(ok)
        self.assertEqual(fs[0].severity, "minor")

    def test_timeout_is_threaded_to_the_subprocess(self):
        seen = {}

        def _capture(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            seen["timeout"] = kw["timeout"]
            return _Proc(0, json.dumps({"findings": []}))

        jury.run_gate("diff", timeout=2400, _run=_capture)
        self.assertEqual(seen["timeout"], 2400)


if __name__ == "__main__":
    unittest.main()
