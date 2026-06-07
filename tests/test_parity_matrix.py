"""Tests for the legacy-to-keel command parity matrix."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "docs/keel/parity-matrix.md"
SHIP_BASELINE = REPO_ROOT / "docs/keel/ship-baseline.md"

EXPECTED_COLUMNS = (
    "Legacy command",
    "Keel command",
    "Status",
    "Legacy behavior",
    "Keel target behavior",
    "Known gaps / owner issues",
    "Project extension points",
    "GitHub side effects",
    "Dry-run behavior",
    "Runtime capabilities",
)

EXPECTED_LEGACY_COMMANDS = (
    "ship",
    "ship-v2",
    "morning",
    "pr-loop",
    "review-cycle",
    "overnight",
    "wrap",
    "triage",
    "stale-prs",
    "regression",
    "review-all-day",
    "coverage",
    "deps-audit",
    "flake-audit",
    "implement",
    "ci-check",
    "Project-provided commands",
)


def _matrix_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells == list(EXPECTED_COLUMNS):
            header = cells
            continue
        if header is None or set(cells) == {"---"}:
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells, strict=True)))
    return rows


class TestParityMatrix(unittest.TestCase):
    def test_matrix_contains_required_columns(self):
        text = MATRIX.read_text(encoding="utf-8")
        expected = "| " + " | ".join(EXPECTED_COLUMNS) + " |"
        self.assertIn(expected, text)

    def test_matrix_covers_every_umbrella_command(self):
        rows = _matrix_rows()
        by_legacy = {row["Legacy command"].strip("`"): row for row in rows}
        self.assertEqual(set(by_legacy), set(EXPECTED_LEGACY_COMMANDS))

    def test_each_row_has_owner_issue_and_required_contract_fields(self):
        for row in _matrix_rows():
            with self.subTest(row=row["Legacy command"]):
                self.assertIn("#", row["Known gaps / owner issues"])
                for column in EXPECTED_COLUMNS:
                    self.assertTrue(row[column], column)

    def test_ship_row_records_parity_evidence(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}
        ship = rows["ship"]
        self.assertEqual(ship["Status"], "`parity-proven`")
        self.assertIn("#81", ship["Known gaps / owner issues"])
        self.assertIn("ship-baseline.md", ship["Known gaps / owner issues"])
        self.assertIn("#69", ship["Known gaps / owner issues"])
        self.assertIn("review_merge_contract", ship["Known gaps / owner issues"])

    def test_ship_v2_row_records_first_class_profile_evidence(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}
        ship_v2 = rows["ship-v2"]
        self.assertEqual(ship_v2["Status"], "`parity-proven`")
        self.assertIn("#61", ship_v2["Known gaps / owner issues"])
        self.assertIn("workflow_profile", ship_v2["Known gaps / owner issues"])
        self.assertIn("compound", ship_v2["Known gaps / owner issues"])


class TestShipBaseline(unittest.TestCase):
    def test_baseline_records_source_comparison_and_parity_evidence(self):
        text = SHIP_BASELINE.read_text(encoding="utf-8")
        for required in (
            "Captured on",
            "Source SHA-256",
            "Compared against",
            "Structured Contract Check",
            "Delta Classification",
            "Ship Parity Evidence",
            "review_merge_contract",
            "#69",
            "The parity matrix can therefore mark `ship` as `parity-proven`",
        ):
            self.assertIn(required, text)

    def test_baseline_classifies_required_delta_destinations(self):
        text = SHIP_BASELINE.read_text(encoding="utf-8")
        for classification in (
            "Core invariant",
            "Runtime / transport",
            "Project policy / extension / project command",
            "Extension / capture invariant",
        ):
            self.assertIn(classification, text)

    def test_baseline_remains_consumer_neutral(self):
        text = SHIP_BASELINE.read_text(encoding="utf-8")
        lower = text.lower()
        forbidden = ("smartinventory", "eventoid")
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, lower)
        private_path_patterns = (
            r"/Users/[^\s`]+",
            r"/home/[^\s`]+",
            r"~/[^\s`]+",
            r"[A-Za-z]:\\Users\\[^\s`]+",
        )
        for pattern in private_path_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
