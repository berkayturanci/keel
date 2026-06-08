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

    def test_standalone_implement_and_ci_check_rows_record_parity_evidence(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}
        implement = rows["implement"]
        ci_check = rows["ci-check"]

        self.assertEqual(implement["Status"], "`parity-proven`")
        self.assertIn("#76", implement["Known gaps / owner issues"])
        self.assertIn("standalone-step", implement["Known gaps / owner issues"])
        self.assertIn("without merging", implement["Known gaps / owner issues"])

        self.assertEqual(ci_check["Status"], "`parity-proven`")
        self.assertIn("#76", ci_check["Known gaps / owner issues"])
        self.assertIn("standalone-diagnostic", ci_check["Known gaps / owner issues"])
        self.assertIn("read-only", ci_check["Known gaps / owner issues"])

    def test_feedback_workflow_rows_remain_distinct_from_ship(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}
        pr_loop = rows["pr-loop"]
        review_cycle = rows["review-cycle"]

        self.assertEqual(pr_loop["Status"], "`parity-proven`")
        self.assertIn("#73", pr_loop["Known gaps / owner issues"])
        self.assertIn("feedback_workflow", pr_loop["Known gaps / owner issues"])
        self.assertIn("merge handoff", pr_loop["Keel target behavior"])
        self.assertIn("CI-green exit", pr_loop["Keel target behavior"])

        self.assertEqual(review_cycle["Status"], "`parity-proven`")
        self.assertIn("#73", review_cycle["Known gaps / owner issues"])
        self.assertIn("feedback_workflow", review_cycle["Known gaps / owner issues"])
        self.assertIn("review-cycle-complete", review_cycle["Known gaps / owner issues"])
        self.assertIn("no-merge", review_cycle["Keel target behavior"])

    def test_triage_and_stale_prs_rows_record_parity_evidence(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}
        triage = rows["triage"]
        stale_prs = rows["stale-prs"]

        self.assertEqual(triage["Status"], "`parity-proven`")
        self.assertIn("#70", triage["Known gaps / owner issues"])
        self.assertIn("MCP label writes use explicit union", triage["Keel target behavior"])
        self.assertIn("--assign", triage["Keel target behavior"])
        self.assertIn("DRY-RUN:", triage["Dry-run behavior"])

        self.assertEqual(stale_prs["Status"], "`parity-proven`")
        self.assertIn("#70", stale_prs["Known gaps / owner issues"])
        self.assertIn("STALE-PRS-<DATE>-", stale_prs["Keel target behavior"])
        self.assertIn("--merge-develop", stale_prs["GitHub side effects"])
        self.assertIn("no git checkout/merge/push", stale_prs["Dry-run behavior"])

    def test_triage_details_document_exact_flag_mapping_and_platform_migration(self):
        text = MATRIX.read_text(encoding="utf-8")
        for required in (
            "## Triage parity details",
            "`/keel:triage --dry-run`",
            "`/keel:triage --label <name>`",
            "`/keel:triage --assign`",
            "explicit union of existing + added labels",
            "migrating from legacy `platform:*` labels",
            "translate legacy platform\nvalues to configured routing roles",
        ):
            self.assertIn(required, text)

    def test_stale_prs_details_document_exact_flag_mapping_and_idempotency(self):
        text = MATRIX.read_text(encoding="utf-8")
        for required in (
            "## Stale PR parity details",
            "`/keel:stale-prs --days <N>`",
            "`/keel:stale-prs --dry-run`",
            "`/keel:stale-prs --rebase`",
            "`/keel:stale-prs --merge-develop`",
            "drift > review-stalled > likely-abandoned",
            "first line starts `STALE-PRS-<DATE>-`",
            "git merge --abort",
        ):
            self.assertIn(required, text)

    def test_scan_and_file_rows_record_parity_evidence(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}
        regression = rows["regression"]
        review_all_day = rows["review-all-day"]

        self.assertEqual(regression["Status"], "`parity-proven`")
        self.assertIn("#71", regression["Known gaps / owner issues"])
        self.assertIn("scan_contract", regression["Known gaps / owner issues"])
        self.assertIn("dedupe", regression["Known gaps / owner issues"])

        self.assertEqual(review_all_day["Status"], "`parity-proven`")
        self.assertIn("#71", review_all_day["Known gaps / owner issues"])
        self.assertIn("time-window-scan", review_all_day["Known gaps / owner issues"])
        self.assertIn("[review-all-day]", review_all_day["Known gaps / owner issues"])

    def test_reporting_rows_record_parity_evidence(self):
        rows = {row["Legacy command"].strip("`"): row for row in _matrix_rows()}

        coverage = rows["coverage"]
        self.assertEqual(coverage["Status"], "`parity-proven`")
        self.assertIn("#72", coverage["Known gaps / owner issues"])
        self.assertIn("one-comment-per-pr", coverage["Known gaps / owner issues"])
        self.assertIn("do-not-post-duplicate", coverage["Known gaps / owner issues"])
        self.assertIn("coverage-regression", coverage["Legacy behavior"])
        self.assertIn("DRY-RUN:", coverage["Dry-run behavior"])

        deps_audit = rows["deps-audit"]
        self.assertEqual(deps_audit["Status"], "`parity-proven`")
        self.assertIn("#72", deps_audit["Known gaps / owner issues"])
        self.assertIn("append-per-run", deps_audit["Known gaps / owner issues"])
        self.assertIn("deps-audit: <DATE>", deps_audit["Keel target behavior"])
        self.assertIn("routes nothing", deps_audit["Dry-run behavior"])

        flake_audit = rows["flake-audit"]
        self.assertEqual(flake_audit["Status"], "`parity-proven`")
        self.assertIn("#72", flake_audit["Known gaps / owner issues"])
        self.assertIn("across-run-disagreement-only", flake_audit["Known gaps / owner issues"])
        self.assertIn("fail_count >= 3", flake_audit["Keel target behavior"])
        self.assertIn("does not comment on existing flake issues", flake_audit["Dry-run behavior"])

    def test_reporting_details_document_exact_flag_mapping_and_idempotency(self):
        text = MATRIX.read_text(encoding="utf-8")
        for required in (
            "## Reporting command parity details",
            "`/keel:coverage [pr number]`",
            "`/keel:coverage --base <branch>`",
            "first line starts `COVERAGE-<PR>-`",
            "does not post a duplicate",
            "`coverage-regression`",
            "`/keel:deps-audit [<ecosystem>\\|all]`",
            "exact title `deps-audit: <DATE>`",
            "first line starts `DEPS-AUDIT-<DATE>-`",
            "`/keel:deps-audit --severity low\\|moderate\\|high\\|critical`",
            "it never auto-applies dependency upgrades",
            "`/keel:flake-audit --days <N> --runs <N>`",
            "`/keel:flake-audit --threshold <P>`",
            "`fail_count >= 3`",
            "title `flaky test: <fully.qualified.name>`",
            "Run-level mode reports failing runs",
            "never edits tests or adds skip/quarantine annotations",
        ):
            self.assertIn(required, text)


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
