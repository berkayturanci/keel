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
