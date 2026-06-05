"""Unit tests for the optional ai-jury integration (pure parser + fail-soft runner)."""

import unittest
from types import SimpleNamespace

from keel import jury


def _run_returning(out, code=0):
    def _run(argv, **kwargs):
        return SimpleNamespace(returncode=code, stdout=out, stderr="")
    return _run


def _raises(*a, **k):
    raise OSError("jury: command not found")


PAYLOAD = {
    "findings": [
        {"severity": "major", "file": "a.py", "line": 42, "claim": "boom"},
        {"severity": "minor", "file": "b.py", "line": "7", "evidence": "no claim, has evidence"},
        {"severity": "info", "claim": "unknown severity, no file"},
    ]
}


class TestParse(unittest.TestCase):
    def test_maps_fields(self):
        fs = jury.parse_jury_findings(PAYLOAD)
        self.assertEqual(len(fs), 3)
        self.assertEqual((fs[0].severity, fs[0].path, fs[0].line), ("major", "a.py", 42))
        self.assertTrue(fs[0].anchorable)
        self.assertEqual(fs[0].source, "jury")

    def test_string_line_parsed(self):
        self.assertEqual(jury.parse_jury_findings(PAYLOAD)[1].line, 7)

    def test_evidence_fallback_message(self):
        self.assertEqual(jury.parse_jury_findings(PAYLOAD)[1].message, "no claim, has evidence")

    def test_unknown_severity_maps_to_minor_and_no_location(self):
        f = jury.parse_jury_findings(PAYLOAD)[2]
        self.assertEqual(f.severity, "minor")
        self.assertIsNone(f.path)
        self.assertFalse(f.anchorable)

    def test_default_message_when_empty(self):
        f = jury.parse_jury_findings({"findings": [{"severity": "nit"}]})[0]
        self.assertEqual(f.message, "jury finding")
        self.assertIsNone(f.line)

    def test_empty_and_missing(self):
        self.assertEqual(jury.parse_jury_findings({}), [])
        self.assertEqual(jury.parse_jury_findings({"findings": None}), [])


class TestRunJury(unittest.TestCase):
    def test_blocking_major(self):
        out = '{"findings":[{"severity":"major","file":"x.py","line":1,"claim":"b"}]}'
        ok, findings = jury.run_jury("diff", _run=_run_returning(out))
        self.assertFalse(ok)
        self.assertEqual(len(findings), 1)

    def test_clean_minor_passes(self):
        out = '{"findings":[{"severity":"minor","file":"x.py","line":1,"claim":"n"}]}'
        ok, findings = jury.run_jury("diff", _run=_run_returning(out))
        self.assertTrue(ok)
        self.assertEqual(len(findings), 1)

    def test_no_findings(self):
        ok, findings = jury.run_jury("diff", _run=_run_returning("{}"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_mock_flag_passes_through(self):
        seen = {}

        def _run(argv, **kwargs):
            seen["argv"] = argv
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

        jury.run_jury("diff", mock=True, _run=_run)
        self.assertIn("--mock", seen["argv"])
        self.assertEqual(seen["argv"][0], "jury")

    def test_non_json_is_noop(self):
        ok, findings = jury.run_jury("diff", _run=_run_returning("not json"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_json_list_is_noop(self):
        ok, findings = jury.run_jury("diff", _run=_run_returning("[]"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_empty_output_is_noop(self):
        ok, findings = jury.run_jury("diff", _run=_run_returning(""))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_missing_cli_is_failsoft(self):
        ok, findings = jury.run_jury("diff", _run=_raises)
        self.assertTrue(ok)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
