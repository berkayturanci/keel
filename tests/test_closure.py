"""Tests for the deterministic closure-comment renderer."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from keel import closure

GOLDEN = Path(__file__).resolve().parent / "golden" / "closure_full.md"


def _record(**overrides):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "command": "ship",
        "run_id": "RUN-170",
        "target": "issue #170",
        "issue": {"number": 170},
        "pull_request": {"number": 190},
        "git": {"base_branch": "main", "branch": "feat/170", "head_sha": "abc123"},
        "changes": {
            "file_count": 2,
            "files": ["src/keel/closure.py", "docs/keel/cli.md"],
        },
        "capture": {
            "schema_version": "keel.capture.v1",
            "status": "applied",
            "reason": "capture hook completed",
            "marker": "compound-learning: pr=190 status=applied",
        },
        "actors": {
            "implementer": "codex (gpt-5)",
            "reviewers": ["claude (opus)", "qwen (ollama)", "AI Jury"],
            "tester": "host (manual list)",
        },
    }
    record.update(overrides)
    return record


class TestRenderClosureComment(unittest.TestCase):
    def test_full_record_matches_golden(self):
        rendered = closure.render_closure_comment(_record())
        if os.environ.get("UPDATE_GOLDEN"):  # pragma: no cover - dev-only refresh
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(rendered, encoding="utf-8")
        self.assertEqual(rendered, GOLDEN.read_text(encoding="utf-8"))

    def test_deterministic(self):
        record = _record()
        self.assertEqual(
            closure.render_closure_comment(record),
            closure.render_closure_comment(record),
        )

    def test_heading_and_sections_present(self):
        rendered = closure.render_closure_comment(_record())
        self.assertTrue(rendered.startswith(f"## {closure.HEADING}\n"))
        for label in (
            "**Target:**",
            "**Implementer:**",
            "**Reviewers:**",
            "**Tester:**",
            "**PR:**",
            "**Changed files:**",
            "**Capture:**",
            "**Run id:**",
        ):
            self.assertIn(label, rendered)

    def test_implementer_rendered_as_is(self):
        rendered = closure.render_closure_comment(_record())
        self.assertIn("- **Implementer:** codex (gpt-5)", rendered)

    def test_jury_noted_when_present(self):
        rendered = closure.render_closure_comment(_record())
        self.assertIn(
            f"- **Reviewers:** claude (opus), qwen (ollama) — {closure.JURY_LABEL}",
            rendered,
        )

    def test_jury_only_no_real_reviewers(self):
        record = _record(actors={"implementer": "codex (gpt-5)",
                                 "reviewers": ["AI Jury"], "tester": None})
        rendered = closure.render_closure_comment(record)
        self.assertIn(f"- **Reviewers:** {closure.JURY_LABEL}", rendered)
        self.assertNotIn("—", rendered)

    def test_reviewers_blank_strings_only(self):
        record = _record(actors={"implementer": "codex (gpt-5)",
                                 "reviewers": [""], "tester": None})
        self.assertIn("- **Reviewers:** none", closure.render_closure_comment(record))

    def test_jury_absent(self):
        record = _record(actors={"implementer": "codex (gpt-5)",
                                 "reviewers": ["claude (opus)"], "tester": None})
        rendered = closure.render_closure_comment(record)
        self.assertNotIn(closure.JURY_LABEL, rendered)
        self.assertIn("- **Reviewers:** claude (opus)", rendered)

    def test_capture_applied(self):
        record = _record(capture={"status": "applied", "reason": None})
        self.assertIn("- **Capture:** applied", closure.render_closure_comment(record))

    def test_capture_deferred_with_reason(self):
        record = _record(capture={"status": "deferred", "reason": "out of window"})
        self.assertIn(
            "- **Capture:** deferred (out of window)",
            closure.render_closure_comment(record),
        )

    def test_capture_skipped(self):
        record = _record(capture={"status": "skipped", "reason": "recursion-guard"})
        self.assertIn(
            "- **Capture:** skipped (recursion-guard)",
            closure.render_closure_comment(record),
        )

    def test_capture_status_none(self):
        record = _record(capture={"status": None, "reason": None})
        self.assertIn("- **Capture:** not recorded", closure.render_closure_comment(record))

    def test_capture_block_missing(self):
        record = _record()
        del record["capture"]
        self.assertIn("- **Capture:** not recorded", closure.render_closure_comment(record))

    def test_no_tester(self):
        record = _record(actors={"implementer": "codex (gpt-5)", "reviewers": [],
                                 "tester": None})
        self.assertIn("- **Tester:** none", closure.render_closure_comment(record))

    def test_empty_reviewers(self):
        record = _record(actors={"implementer": "codex (gpt-5)", "reviewers": [],
                                 "tester": "host"})
        self.assertIn("- **Reviewers:** none", closure.render_closure_comment(record))

    def test_reviewers_not_a_list(self):
        record = _record(actors={"implementer": "codex (gpt-5)", "reviewers": None,
                                 "tester": "host"})
        self.assertIn("- **Reviewers:** none", closure.render_closure_comment(record))

    def test_no_pr(self):
        record = _record(pull_request={"number": None})
        self.assertIn("- **PR:** none", closure.render_closure_comment(record))

    def test_pull_request_block_missing(self):
        record = _record()
        del record["pull_request"]
        self.assertIn("- **PR:** none", closure.render_closure_comment(record))

    def test_no_docs_touched_single_file(self):
        record = _record(changes={"file_count": 1, "files": ["src/keel/closure.py"]})
        rendered = closure.render_closure_comment(record)
        self.assertIn("- **Changed files:** 1", rendered)
        self.assertIn("  - `src/keel/closure.py`", rendered)
        self.assertNotIn("docs/", rendered)

    def test_no_changed_files(self):
        record = _record(changes={"file_count": 0, "files": []})
        rendered = closure.render_closure_comment(record)
        self.assertIn("- **Changed files:** 0", rendered)

    def test_changes_block_missing(self):
        record = _record()
        del record["changes"]
        rendered = closure.render_closure_comment(record)
        self.assertIn("- **Changed files:** 0", rendered)

    def test_changes_files_not_a_list(self):
        record = _record(changes={"file_count": None, "files": None})
        rendered = closure.render_closure_comment(record)
        self.assertIn("- **Changed files:** 0", rendered)

    def test_actors_block_missing(self):
        record = _record()
        del record["actors"]
        rendered = closure.render_closure_comment(record)
        self.assertIn("- **Implementer:** none", rendered)
        self.assertIn("- **Reviewers:** none", rendered)
        self.assertIn("- **Tester:** none", rendered)

    def test_no_target(self):
        record = _record(target=None)
        rendered = closure.render_closure_comment(record)
        self.assertNotIn("**Target:**", rendered)

    def test_blank_target(self):
        record = _record(target="   ")
        rendered = closure.render_closure_comment(record)
        self.assertNotIn("**Target:**", rendered)

    def test_no_run_id(self):
        record = _record(run_id=None)
        self.assertIn("- **Run id:** none", closure.render_closure_comment(record))


class TestClosureContract(unittest.TestCase):
    def test_contract_shape(self):
        contract = closure.contract_as_dict()
        self.assertEqual(contract["schema_version"], closure.CLOSURE_SCHEMA_VERSION)
        self.assertEqual(contract["heading"], closure.HEADING)
        self.assertTrue(contract["deterministic"])
        self.assertTrue(contract["consumer_neutral"])
        self.assertTrue(contract["mirror_not_parser"])
        self.assertEqual(contract["jury_label"], closure.JURY_LABEL)
        self.assertIn("implementer", contract["sections"])
        self.assertIn("capture", contract["sections"])


if __name__ == "__main__":
    unittest.main()
