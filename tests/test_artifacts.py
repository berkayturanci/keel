"""Tests for canonical ship artifact renderers."""

import unittest

from keel import artifacts, evidence


class TestArtifactContract(unittest.TestCase):
    def test_contract_lists_renderers_and_markers(self):
        contract = artifacts.contract_as_dict()

        self.assertEqual(contract["schema_version"], "keel.artifacts.v1")
        self.assertEqual(contract["markers"]["review_verdict"],
                         evidence.REVIEW_VERDICT_MARKER)
        self.assertEqual(contract["markers"]["jury_verdict"], evidence.JURY_VERDICT_MARKER)
        self.assertEqual(contract["markers"]["review_cycle_summary"],
                         artifacts.REVIEW_CYCLE_SUMMARY_MARKER)
        self.assertEqual(contract["renderers"]["review_cycle_summary"],
                         "keel.artifacts.render_review_cycle_summary")
        self.assertEqual(contract["markers"]["step_handoff"], "<!-- keel.step-handoff.v1 -->")
        self.assertEqual(
            contract["markers"]["run_control_halt"],
            "<!-- keel.run-control-halt.v1 -->",
        )
        self.assertEqual(contract["adapter_rule"], "post rendered markdown verbatim when available")


class TestPrBodyRenderer(unittest.TestCase):
    def test_pr_body_has_required_sections_and_closing_reference(self):
        body = artifacts.render_pr_body(
            issue_number=217,
            issue_intake={
                "objective": "Artifacts drift between projects.",
                "deliverable": "Add canonical renderers.",
            },
            changed_files=["src/keel/artifacts.py", "docs/keel/cli.md"],
            testing=["make coverage — passed"],
            docs_impact="Updated docs.",
        )

        self.assertIn("## Summary", body)
        self.assertIn("## Context / Root Cause", body)
        self.assertIn("## Changes Made", body)
        self.assertIn("## Testing", body)
        self.assertIn("## Docs Impact", body)
        self.assertIn("Closes #217", body)
        self.assertNotEqual(body.strip(), "Closes #217")

    def test_pr_body_uses_safe_placeholders_when_issue_is_unknown(self):
        body = artifacts.render_pr_body()

        self.assertIn("Refs #<issue-number>", body)
        self.assertIn("No changed files recorded yet.", body)


class TestIssueUpdateRenderer(unittest.TestCase):
    def test_issue_update_has_stable_marker(self):
        body = artifacts.render_issue_update(
            issue_number=217,
            pull_request=220,
            status="needs-review",
            summary="PR opened.",
            next_step="Wait for CI.",
        )

        self.assertIn("<!-- keel.issue-update.v1 -->", body)
        self.assertIn("- **Issue:** #217", body)
        self.assertIn("- **Pull request:** #220", body)


class TestVerdictRenderers(unittest.TestCase):
    def test_review_verdict_is_head_bound_and_marker_based(self):
        body = artifacts.render_review_verdict(
            reviewer="Reviewer A",
            head_sha="abc123",
            findings=[{"severity": "minor", "message": "Consider a docs note."}],
        )

        self.assertIn("keel.review-verdict.v1", body)
        self.assertIn("reviewer: reviewer-a", body)
        self.assertIn("head: abc123", body)
        self.assertIn("- minor: Consider a docs note.", body)

    def test_review_verdict_omits_provenance_lines_by_default(self):
        body = artifacts.render_review_verdict(reviewer="Reviewer A", head_sha="abc123")

        self.assertNotIn("vendor:", body)
        self.assertNotIn("model:", body)

    def test_review_verdict_renders_vendor_and_model_provenance(self):
        body = artifacts.render_review_verdict(
            reviewer="Reviewer A",
            head_sha="abc123",
            vendor="Claude",
            model="Opus 4.8",
        )

        self.assertIn("vendor: claude", body)
        self.assertIn("model: opus-4-8", body)

    def test_review_verdict_model_requires_vendor(self):
        body = artifacts.render_review_verdict(
            reviewer="Reviewer A",
            head_sha="abc123",
            model="opus",
        )

        self.assertNotIn("vendor:", body)
        self.assertNotIn("model:", body)

    def test_review_verdict_falls_back_when_findings_have_no_messages(self):
        body = artifacts.render_review_verdict(
            reviewer="Reviewer A",
            head_sha="abc123",
            findings=[{"severity": "minor"}],
        )

        self.assertIn("Findings:\n- none", body)

    def test_jury_verdict_is_head_bound_and_lists_participants(self):
        body = artifacts.render_jury_verdict(
            head_sha="abc123",
            participants=["Codex", "Claude"],
            findings_summary=["No blockers found."],
        )

        self.assertIn("keel.jury-verdict.v1", body)
        self.assertIn("head: abc123", body)
        self.assertIn("Participants: Codex, Claude.", body)
        self.assertIn("- No blockers found.", body)


class TestReviewCycleSummaryRenderer(unittest.TestCase):
    def _reviewer(self, **overrides):
        base = {
            "codename": "Alpha-1",
            "focus": "Security",
            "verdict": "LGTM",
            "findings": [],
        }
        base.update(overrides)
        return base

    def test_full_summary_is_head_bound_marker_based_and_block(self):
        body = artifacts.render_review_cycle_summary(
            head_sha="cb05fe2",
            run_id="run-7:cycle-summary",
            reviewers=[
                self._reviewer(
                    codename="Alpha-2269",
                    focus="Security and architecture",
                    verdict="LGTM-with-suggestions",
                    findings=[{
                        "severity": "minor",
                        "location": "web/functions/pubsub-debug.log:1",
                        "description": "Accidental debug log",
                        "suggested_fix": "Remove it",
                    }],
                    clean_areas=["auth flows"],
                ),
                self._reviewer(
                    codename="Beta-2269",
                    focus="Bugs",
                    verdict="LGTM",
                    findings=[
                        {"severity": "nit", "location": "a.js:1",
                         "description": "binds via && over ||", "suggested_fix": ""},
                        {"severity": "critical", "location": "x.js:1",
                         "description": "pipe | and\nnewline", "suggested_fix": "fix"},
                    ],
                ),
            ],
        )

        self.assertTrue(body.startswith("keel.review-cycle-summary.v1\n"))
        self.assertIn("head: cb05fe2", body)
        self.assertIn("## Reviewer: Alpha-2269 (Focus: Security and architecture)", body)
        self.assertIn("Verdict: LGTM-with-suggestions", body)
        self.assertIn("| Severity | File:Line | Description | Suggested Fix |", body)
        self.assertIn("| minor | web/functions/pubsub-debug.log:1 |", body)
        # critical folds into the blocker bucket and sorts ahead of the nit.
        self.assertIn("Severity Histogram: blocker 1 · major 0 · minor 1 · nit 1", body)
        self.assertLess(body.index("| critical | x.js:1 |"), body.index("| nit | a.js:1 |"))
        # table delimiters and newlines inside a cell are escaped/folded.
        self.assertIn("pipe \\| and newline", body)
        # an empty suggested fix renders the placeholder, not a blank cell.
        self.assertIn("binds via && over \\|\\| | — |", body)
        self.assertIn("## Consolidated Summary", body)
        self.assertIn("- Alpha-2269: LGTM-with-suggestions", body)
        self.assertIn("Clean areas: auth flows", body)
        self.assertIn("Merge recommendation: ❌ block", body)
        self.assertIn("<!-- keel.run-id: run-7:cycle-summary -->", body)

    def test_clean_reviewer_approves_without_run_id_marker(self):
        body = artifacts.render_review_cycle_summary(
            reviewers=[self._reviewer(verdict="LGTM", findings=[])],
        )

        self.assertIn("head: <head-sha>", body)
        self.assertIn("No findings.", body)
        self.assertIn("Severity Histogram: blocker 0 · major 0 · minor 0 · nit 0", body)
        self.assertIn("Clean areas: none reported", body)
        self.assertIn("Merge recommendation: ✅ approve", body)
        self.assertNotIn("keel.run-id", body)

    def test_minor_finding_downgrades_lgtm_to_request_changes(self):
        body = artifacts.render_review_cycle_summary(
            reviewers=[self._reviewer(
                verdict="LGTM",
                findings=[{"severity": "minor", "location": "a:1",
                           "description": "d", "suggested_fix": "f"}],
            )],
        )

        self.assertIn("Merge recommendation: ⚠️ request changes", body)

    def test_nit_only_approves_with_cosmetic_note(self):
        body = artifacts.render_review_cycle_summary(
            reviewers=[self._reviewer(
                verdict="LGTM-with-suggestions",
                findings=[{"severity": "nit", "location": "a:1",
                           "description": "d", "suggested_fix": "f"}],
            )],
        )

        self.assertIn("Merge recommendation: ✅ approve (cosmetic nits)", body)

    def test_non_lgtm_verdict_blocks_even_without_findings(self):
        body = artifacts.render_review_cycle_summary(
            reviewers=[self._reviewer(verdict="needs fixes", findings=[])],
        )

        self.assertIn("Merge recommendation: ❌ block", body)

    def test_non_dict_and_unknown_severity_inputs_are_tolerated(self):
        body = artifacts.render_review_cycle_summary(
            reviewers=[
                "garbage",  # skipped: not a dict
                self._reviewer(
                    codename="Gamma",
                    findings=[
                        "junk",  # skipped: not a dict
                        {"severity": "wat", "location": "z:1",
                         "description": "unknown sev", "suggested_fix": "x"},
                    ],
                    clean_areas=["core", "", 5],  # non-strings/blanks filtered out
                ),
                # a reviewer with no "findings" key at all is tolerated.
                {"codename": "Delta", "verdict": "LGTM", "clean_areas": ["core", "tests"]},
            ],
        )

        # the unknown severity still renders in the table but is not counted.
        self.assertIn("| wat | z:1 |", body)
        self.assertIn("Severity Histogram: blocker 0 · major 0 · minor 0 · nit 0", body)
        # the consolidated clean-area list de-dupes "core" across both reviewers.
        self.assertIn("Clean areas: core, tests", body)
        self.assertNotIn("core, tests, core", body)

    def test_empty_reviewers_renders_valid_skeleton(self):
        body = artifacts.render_review_cycle_summary(reviewers=(), run_id="   ")

        self.assertNotIn("---", body)  # no per-reviewer separators
        self.assertIn("Reviewer verdicts:\n- none", body)
        self.assertIn("Merge recommendation: ✅ approve", body)
        self.assertNotIn("keel.run-id", body)  # blank run-id is not embedded

    def test_render_is_byte_stable_for_the_same_input(self):
        reviewers = [self._reviewer(
            findings=[{"severity": "major", "location": "a:1",
                       "description": "d", "suggested_fix": "f"}])]
        first = artifacts.render_review_cycle_summary(reviewers=reviewers, head_sha="h")
        second = artifacts.render_review_cycle_summary(reviewers=reviewers, head_sha="h")
        self.assertEqual(first, second)


class TestCoverageDeltaRenderer(unittest.TestCase):
    def test_full_delta_with_bold_rows_skipped_area_and_missing_values(self):
        body = artifacts.render_coverage_delta(
            codename="COVERAGE-1-T", base_sha="aaa", head_sha="bbb",
            areas=[
                "junk",  # non-dict area is skipped
                {"name": "core", "rows": [
                    "junk",  # non-dict row is skipped
                    {"unit": "a.py", "base": 90.0, "head": 90.9, "files": 2},   # +0.9 → bold
                    {"unit": "b.py", "base": 80.0, "head": 80.1, "files": 1},   # +0.1 → plain
                    {"unit": "new.py", "base": None, "head": 100.0, "files": 1},  # missing base
                    {"unit": "wt.py", "base": 90.0, "head": True},  # boolean head → not a number
                ], "overall": {"base": 88.0, "head": 88.2}},  # +0.2 → label-only bold
                {"name": "empty", "overall": {"base": 50.0, "head": 50.0}},  # no rows key
                {"name": "rows-only", "rows": [
                    {"unit": "z.py", "base": 70.0, "head": 71.0, "files": 1}]},  # no overall
                {"name": "docs", "skipped": True, "skip_reason": "untouched"},
            ])
        self.assertTrue(body.startswith("COVERAGE-1-T\n"))
        self.assertIn("Coverage delta: base@aaa → head@bbb", body)
        self.assertIn("| **a.py** | **90.0%** | **90.9%** | **+0.9%** | **2** |", body)
        self.assertIn("| b.py | 80.0% | 80.1% | +0.1% | 1 |", body)
        self.assertIn("| new.py | — | 100.0% | — | 1 |", body)
        self.assertIn("| wt.py | 90.0% | — | — |  |", body)
        self.assertIn("| **overall** | 88.0% | 88.2% | +0.2% |  |", body)
        self.assertIn("| **overall** | 50.0% | 50.0% | +0.0% |  |", body)
        self.assertIn("_docs coverage skipped: untouched_", body)
        self.assertIn("<!-- keel.coverage-delta.v1 -->", body)

    def test_defaults_when_shas_missing(self):
        body = artifacts.render_coverage_delta(codename="C")
        self.assertIn("base@<base> → head@<head>", body)


class TestDepsAuditRenderer(unittest.TestCase):
    def test_full_report_sorted_with_empty_ecosystem_and_skipped(self):
        body = artifacts.render_deps_audit(
            codename="DEPS-T",
            ecosystems=[
                "junk",  # non-dict ecosystem is skipped
                {"name": "pip", "findings": [
                    {"package": "p1", "version": "1", "severity": "low",
                     "advisory": "A1", "fix_available": "yes"},
                    {"package": "p2", "version": "2", "severity": "critical",
                     "advisory": "A2", "fix_available": "no"},
                    {"severity": "weird"},  # unknown severity: shown, not counted
                ]},
                {"name": "npm", "findings": [], "threshold": "moderate"},
            ],
            licences=[{"status": "changed", "package": "x",
                       "baseline": "MIT", "current": "GPL"}],
            skipped=["cargo"])
        self.assertTrue(body.startswith("DEPS-T\n"))
        self.assertIn("critical: 1 | high: 0 | moderate: 0 | low: 1", body)
        self.assertLess(body.index("| p2 |"), body.index("| p1 |"))  # critical before low
        self.assertIn("_No npm findings at or above moderate severity._", body)
        self.assertIn("## Licences", body)
        self.assertIn("| changed | x | MIT | GPL |", body)
        self.assertIn("## Skipped\n\n- cargo", body)
        self.assertIn("<!-- keel.deps-audit.v1 -->", body)

    def test_security_only_omits_licences_and_skipped(self):
        body = artifacts.render_deps_audit(
            codename="D", ecosystems=[], licences=[{"status": "x"}], security_only=True)
        self.assertNotIn("Licences", body)
        self.assertNotIn("## Skipped", body)

    def test_licence_no_drift_when_empty(self):
        body = artifacts.render_deps_audit(codename="D", licences=[])
        self.assertIn("_licences: no drift_", body)


class TestFlakeAuditRenderer(unittest.TestCase):
    def test_full_report(self):
        body = artifacts.render_flake_audit(
            codename="FLAKE-T",
            summary={"runs": 10, "distinct": 2, "classified": 1, "opened": 1},
            new_flakes=[
                "junk",  # non-dict is skipped
                {"test": "t1", "fail_rate": "3/10", "failures": 3,
                 "samples": ["u1", "u2"], "signature": "Boom | x"}],
            tracked=[{"test": "t2", "issue": 7}, {"test": "t3", "issue": "n/a"}],
            limitations=["history partial"])
        self.assertTrue(body.startswith("FLAKE-T\n"))
        self.assertIn("runs examined: 10 · distinct failing tests: 2 · "
                      "classified flakes: 1 · newly-opened issues: 1", body)
        self.assertIn("| t1 | 3/10 | 3 | u1, u2 | Boom \\| x |", body)
        self.assertIn("- t2 — see #7", body)
        self.assertIn("- t3 — see n/a", body)
        self.assertIn("## Limitations\n\n- history partial", body)
        self.assertIn("<!-- keel.flake-audit.v1 -->", body)

    def test_empty_report_omits_optional_sections(self):
        body = artifacts.render_flake_audit(codename="F")
        self.assertIn("runs examined: 0 · distinct failing tests: 0 · "
                      "classified flakes: 0 · newly-opened issues: 0", body)
        self.assertIn("_no new flakes above threshold_", body)
        self.assertNotIn("## Already tracked", body)
        self.assertNotIn("## Limitations", body)

    def test_flake_without_samples_shows_placeholder(self):
        body = artifacts.render_flake_audit(
            codename="F",
            new_flakes=[{"test": "t", "fail_rate": "3/9", "failures": 3, "signature": "s"}])
        self.assertIn("| t | 3/9 | 3 | — | s |", body)

    def test_count_ignores_boolean_noise(self):
        body = artifacts.render_flake_audit(codename="F", summary={"runs": True, "classified": 2})
        self.assertIn("runs examined: 0 ·", body)        # True is not a count
        self.assertIn("classified flakes: 2 ·", body)


class TestScanFindingRenderer(unittest.TestCase):
    def test_full_issue_body_with_regression_of_as_last_line(self):
        body = artifacts.render_scan_finding_issue(
            problem="Null deref in parser", location="src/p.py:42", severity="major",
            justification="crashes on empty input", evidence="if x:\n  y",
            suggested_fix="guard x", source="regression", regression_of=120)
        self.assertIn("## Problem\n\nNull deref in parser", body)
        self.assertIn("## Location\n\n`src/p.py:42`", body)
        self.assertIn("major — crashes on empty input", body)
        self.assertIn("```\nif x:\n  y\n```", body)
        self.assertIn("## Suggested fix\n\nguard x", body)
        self.assertIn("Found by keel regression.", body)
        self.assertIn("<!-- keel.scan-finding.v1 -->", body)
        self.assertTrue(body.rstrip().endswith("regression-of: #120"))

    def test_defaults_and_no_regression_line(self):
        body = artifacts.render_scan_finding_issue()
        self.assertIn("A scan finding was reported without a problem statement.", body)
        self.assertIn("`unknown`", body)
        self.assertIn("minor — no justification recorded", body)
        self.assertIn("Found by keel scan.", body)
        self.assertNotIn("regression-of:", body)
        self.assertTrue(body.rstrip().endswith("<!-- keel.scan-finding.v1 -->"))


class TestTriageAuditRenderer(unittest.TestCase):
    def test_full_comment_with_run_id_and_numeric_tier(self):
        body = artifacts.render_triage_audit(
            issue=77, role="core", priority="priority:high", status="status:in-progress",
            tier=3, rationale="Touches tier-3 orchestrator path.", run_id="run-9:triage-77")
        self.assertTrue(body.startswith("keel.triage-audit.v1\n"))
        self.assertIn("keel triage — #77: role: core · priority: priority:high · "
                      "status: status:in-progress · tier: 3", body)
        self.assertIn("Touches tier-3 orchestrator path.", body)
        self.assertIn("<!-- keel.run-id: run-9:triage-77 -->", body)

    def test_defaults_without_run_id(self):
        body = artifacts.render_triage_audit(issue=5)
        self.assertIn("keel triage — #5: role: unassigned · priority: unset · "
                      "status: unset · tier: n/a", body)
        self.assertIn("Classified from the existing label set.", body)
        self.assertNotIn("keel.run-id", body)

    def test_scalar_tier_handles_string_and_boolean(self):
        string_tier = artifacts.render_triage_audit(issue=1, tier="2")
        self.assertIn("tier: 2", string_tier)
        boolean_tier = artifacts.render_triage_audit(issue=1, tier=True)  # bool → fallback
        self.assertIn("tier: n/a", boolean_tier)


class TestExtensionResultRenderer(unittest.TestCase):
    def test_extension_result_has_stable_shape(self):
        body = artifacts.render_extension_result(
            slot="pre-merge",
            extension_id="design-parity",
            status="passed",
            mode="blocking",
            summary="No deltas.",
            artifacts=["reports/design.md"],
            follow_ups=["none"],
        )

        self.assertIn("<!-- keel.extension-result.v1 -->", body)
        self.assertIn("- **Slot:** `pre-merge`", body)
        self.assertIn("- **Extension:** `design-parity`", body)
        self.assertIn("  - reports/design.md", body)


class TestStepHandoffRenderer(unittest.TestCase):
    def test_step_handoff_has_stable_marker_and_evidence_list(self):
        body = artifacts.render_step_handoff(
            step_id="s7",
            step_name="review",
            status="complete",
            summary="Two reviewers posted LGTM.",
            next_step="s8",
            evidence_ids=["review-verdict-1", "review-verdict-2"],
        )

        self.assertIn("<!-- keel.step-handoff.v1 -->", body)
        self.assertIn("- **Step:** `s7`", body)
        self.assertIn("- **Status:** complete", body)
        self.assertIn("  - review-verdict-1", body)
        self.assertIn("  - review-verdict-2", body)


if __name__ == "__main__":
    unittest.main()
