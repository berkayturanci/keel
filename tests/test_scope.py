"""Tests for pure branch-scope verification (declared files vs. PR diff)."""

import unittest
from types import SimpleNamespace

from keel import ledger, scope


class TestScopeVerify(unittest.TestCase):
    def test_in_scope_diff_passes(self):
        report = scope.verify(["a.py"], ["a.py"])

        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["advisory"])
        self.assertEqual(report["in_scope"], ["a.py"])
        self.assertEqual(report["scope_creep"], [])
        self.assertIsNone(report["note"])
        self.assertEqual(report["declared"], ["a.py"])

    def test_scope_creep_fails_and_names_the_unexpected_file(self):
        report = scope.verify(["a.py"], ["a.py", "unrelated.py"])

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["in_scope"], ["a.py"])
        self.assertEqual(report["scope_creep"], ["unrelated.py"])

    def test_docs_paths_are_exempt_from_scope_creep(self):
        report = scope.verify(
            ["a.py"],
            ["a.py", "docs/keel/cli.md", "CHANGELOG.md"],
            docs_globs=("docs/**", "*.md"),
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["scope_creep"], [])
        self.assertEqual(report["docs_exempt"], ["docs/keel/cli.md", "CHANGELOG.md"])

    def test_no_declared_scope_is_advisory_pass(self):
        report = scope.verify(None, ["a.py", "b.py"])

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["advisory"])
        self.assertEqual(report["note"], "no declared scope recorded")
        self.assertIsNone(report["declared"])

    def test_scope_waived_deferral_downgrades_creep_to_pass(self):
        report = scope.verify(
            ["a.py"], ["a.py", "unrelated.py"], deferrals=("scope-waived",)
        )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["waived"])
        self.assertEqual(report["scope_creep"], ["unrelated.py"])
        self.assertEqual(report["note"], "scope creep waived by operator deferral")

    def test_all_deferral_also_waives_scope(self):
        report = scope.verify(["a.py"], ["a.py", "x.py"], deferrals=("all",))

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["waived"])

    def test_clean_in_scope_diff_is_not_marked_waived(self):
        report = scope.verify(
            ["a.py"], ["a.py"], deferrals=("scope-waived",)
        )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["waived"])
        self.assertIsNone(report["note"])

    def test_advisory_pass_reports_waived_flag(self):
        report = scope.verify(None, ["a.py"], deferrals=("scope-waived",))

        self.assertTrue(report["advisory"])
        self.assertTrue(report["waived"])


def _record(*, declared_files):
    outcome = SimpleNamespace(gate="build", ok=True, skipped=False, error=None, findings=[])
    verdict = SimpleNamespace(blocked=False, counts={"blocker": 0})
    merge = SimpleNamespace(action="merge", reason="all gates passed")
    assessment = SimpleNamespace(
        tier=2, reviewers=2, window_open=True, ci_ok=None,
        merge=merge, halted=False, bypassed_window=False,
    )
    return ledger.build_ship_run_record(
        command="ship",
        base_branch="main",
        changed_files=["a.py"],
        declared_files=declared_files,
        outcomes=[outcome],
        verdict=verdict,
        assessment=assessment,
    )


class TestDeclaredFilesLedgerRoundTrip(unittest.TestCase):
    def test_declared_files_persist_and_read_back(self):
        record = _record(declared_files=["a.py", "b.py"])

        self.assertEqual(record["declared"], {"file_count": 2, "files": ["a.py", "b.py"]})
        decoded = ledger.parse_records(ledger.encode_record(record))[0]
        self.assertEqual(ledger.declared_files_for_record(decoded), ["a.py", "b.py"])

    def test_missing_declared_block_reads_as_none(self):
        record = _record(declared_files=None)

        self.assertIsNone(record["declared"])
        self.assertIsNone(ledger.declared_files_for_record(record))

    def test_empty_declared_list_persists_distinct_from_none(self):
        record = _record(declared_files=[])

        self.assertEqual(record["declared"], {"file_count": 0, "files": []})
        self.assertEqual(ledger.declared_files_for_record(record), [])

    def test_declared_files_coerced_to_strings(self):
        record = _record(declared_files=["a.py"])
        self.assertEqual(ledger.declared_files_for_record(record), ["a.py"])

    def test_malformed_declared_block_reads_as_none(self):
        self.assertIsNone(ledger.declared_files_for_record({"declared": "nope"}))
        self.assertIsNone(ledger.declared_files_for_record({"declared": {"files": "x"}}))


if __name__ == "__main__":
    unittest.main()
