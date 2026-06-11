"""Unit tests for the jury built-in gate (pure parse + fail-soft runner)."""

import json
import unittest

from keel import jury

SAMPLE = {"findings": [
    {"severity": "major", "file": "src/x.py", "line": 42, "claim": "unchecked return",
     "reviewer": "claude"},
    {"severity": "minor", "file": "src/x.py", "line": 7, "claim": "missing docstring",
     "reviewer": "codex"},
    {"severity": "nit", "file": None, "line": None, "claim": "style", "reviewer": "agy"},
]}


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
        self.assertEqual(jury.run_gate("", _run=_jury_ok), (True, []))

    def test_absent_is_noop(self):
        self.assertEqual(jury.run_gate("a diff", _run=_jury_absent), (True, []))

    def test_oversized_diff_skips_cli_but_emits_advisory(self):
        def fail_if_called(argv, **kw):
            raise AssertionError(f"unexpected jury call: {argv}")

        ok, findings = jury.run_gate(
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

        ok, findings = jury.run_gate(
            "x" * (jury.MAX_DIFF_BYTES + 1), mode="gating", _run=fail_if_called
        )

        self.assertFalse(ok)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "major")
        self.assertEqual(findings[0].source, "jury:skipped-oversize")

    def test_present_blocks_on_major(self):
        ok, fs = jury.run_gate("some diff", _run=_jury_ok)
        self.assertFalse(ok)
        self.assertEqual(len(fs), 3)

    def test_present_clean_passes(self):
        def clean(argv, **kw):
            if "--version" in argv:
                return _Proc(0)
            return _Proc(0, json.dumps(
                {"findings": [{"severity": "minor", "file": "a", "line": 1, "claim": "x"}]}))
        ok, fs = jury.run_gate("diff", _run=clean)
        self.assertTrue(ok)
        self.assertEqual(len(fs), 1)


if __name__ == "__main__":
    unittest.main()
