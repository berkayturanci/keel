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
