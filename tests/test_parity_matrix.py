"""Tests for the legacy-to-keel command parity matrix."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "docs/keel/parity-matrix.md"

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


if __name__ == "__main__":
    unittest.main()
