"""Tests for deterministic ship evidence verification."""

import unittest

from keel import closure, evidence, ship


def _review_contract(*, reviewers=2, jury=False, no_jury=False, jury_advisory=False):
    return ship.resolve_review_contract(
        tier=None,
        reviewer_override=reviewers,
        jury=jury,
        no_jury=no_jury,
        jury_advisory=jury_advisory,
    )


def _comment(body):
    return {"body": body, "author_association": "MEMBER"}


def _trusted_comment(body, *, reviewer=None):
    comment = {"body": body, "author_association": "MEMBER"}
    if reviewer is not None:
        comment["user"] = {"login": reviewer}
    return comment


def _untrusted_comment(body):
    return {"body": body, "author_association": "NONE", "user": {"login": "drive-by"}}


def _closure_with_run_context(
    *,
    host="codex",
    transport="gh",
    profile="standard",
    jury="off",
    consent="approved (scopes: filesystem, git, github)",
):
    return (
        f"{closure.COMMENT_MARKER}\n\n"
        "## Ship outcome\n\n"
        "### Run context\n\n"
        f"- **Host agent:** {host}\n"
        f"- **Transport:** {transport}\n"
        f"- **Profile:** {profile}\n"
        f"- **Jury:** {jury}\n"
        f"- **Consent:** {consent}\n"
    )


class TestEvidenceContract(unittest.TestCase):
    def test_required_items_include_closure_review_and_gating_jury(self):
        contract = evidence.contract_as_dict(_review_contract(reviewers=2, jury=True))

        ids = [item["id"] for item in contract["required"]]
        self.assertEqual(ids, [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
            "jury-verdict",
        ])
        self.assertIn("pull_request_body", contract["not_accepted"])
        self.assertIn("untrusted_public_comment", contract["not_accepted"])
        self.assertTrue(contract["fail_closed"])

    def test_no_jury_drops_jury_requirement(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True, no_jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "pass")
        self.assertNotIn("jury-verdict", report["missing"])


class TestEvidenceVerify(unittest.TestCase):
    def test_missing_closure_blocks(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_comment("keel.review-verdict.v1\nLGTM")],
            issue_comments=[],
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["missing"], ["closure-comment-pr", "closure-comment-issue"])

    def test_missing_review_blocks(self):
        report = evidence.verify(
            _review_contract(reviewers=2),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["missing"], ["review-verdict-2"])

    def test_missing_jury_blocks_when_gating(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["missing"], ["jury-verdict"])

    def test_deferral_allows_missing_item(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            deferrals=("jury-verdict",),
        )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(
            next(item for item in report["results"] if item["id"] == "jury-verdict")
            ["deferred"]
        )

    def test_dry_run_has_no_required_evidence(self):
        report = evidence.verify(_review_contract(reviewers=3, jury=True), dry_run=True)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["required_count"], 0)

    def test_pr_body_chat_summary_and_assessment_are_not_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_body=f"{closure.COMMENT_MARKER}\nkeel.review-verdict.v1\nLGTM",
            pr_comments=[
                _comment("### \U0001f6a2 keel ship\nLGTM reviewer verdict"),
                _comment("chat summary: reviewer says LGTM"),
            ],
            issue_comments=[],
            pr_reviews=[],
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["closure_pr"], 0)
        self.assertEqual(report["counts"]["review_verdict"], 0)

    def test_review_marker_wins_even_when_comment_mentions_jury(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nReviewer LGTM; --no-jury was checked."),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["review_verdict"], 1)

    def test_prose_review_and_jury_verdicts_are_not_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("Reviewer verdict: LGTM"),
                _comment("AI Jury verdict: LGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)

    def test_review_verdicts_are_distinct_by_declared_reviewer(self):
        report = evidence.verify(
            _review_contract(reviewers=2),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM"),
                _comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM again"),
                _comment("keel.review-verdict.v1\nreviewer: beta\nLGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["review_verdict"], 2)

    def test_review_verdicts_can_fall_back_to_github_user_identity(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                {
                    "body": "keel.review-verdict.v1\nLGTM",
                    "user": {"login": "reviewer-one"},
                    "author_association": "MEMBER",
                },
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["review_verdict"], 1)

    def test_review_verdicts_are_bound_to_current_head(self):
        report = evidence.verify(
            _review_contract(reviewers=2, jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nreviewer: alpha\nhead: old\nLGTM"),
                _comment("keel.review-verdict.v1\nreviewer: beta\nhead: abc123\nLGTM"),
                _comment("keel.jury-verdict.v1\nhead: old\nAI Jury LGTM"),
                _comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            pr_reviews=[
                {
                    "body": "keel.review-verdict.v1\nreviewer: gamma\nLGTM",
                    "commit_id": "abc123",
                    "author_association": "MEMBER",
                },
            ],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["review_verdict"], 2)
        self.assertEqual(report["counts"]["jury_verdict"], 1)

    def test_review_verdicts_without_matching_head_are_ignored_when_head_known(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM"),
                _comment("keel.jury-verdict.v1\nAI Jury LGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1", "jury-verdict"])

    def test_untrusted_comment_markers_are_not_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _untrusted_comment(closure.COMMENT_MARKER),
                _untrusted_comment("keel.review-verdict.v1\nreviewer: forged\nhead: abc123\nLGTM"),
                _untrusted_comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ],
            issue_comments=[_untrusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["closure_pr"], 0)
        self.assertEqual(report["counts"]["closure_issue"], 0)
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)
        self.assertEqual(report["missing"], [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "jury-verdict",
        ])

    def test_trusted_comment_markers_are_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _trusted_comment(closure.COMMENT_MARKER),
                _trusted_comment("keel.review-verdict.v1\nreviewer: alpha\nhead: abc123\nLGTM"),
                _trusted_comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["closure_pr"], 1)
        self.assertEqual(report["counts"]["closure_issue"], 1)
        self.assertEqual(report["counts"]["review_verdict"], 1)
        self.assertEqual(report["counts"]["jury_verdict"], 1)

    def test_fully_populated_run_context_has_no_finding(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _trusted_comment(_closure_with_run_context()),
                _trusted_comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM"),
            ],
            issue_comments=[_trusted_comment(_closure_with_run_context())],
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_partially_degraded_run_context_has_no_empty_context_finding(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _trusted_comment(_closure_with_run_context(host="unknown") + "\n### Capture\n"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM"),
            ],
            issue_comments=[_trusted_comment(_closure_with_run_context(transport="unknown"))],
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_fully_degraded_run_context_emits_major_finding(self):
        empty = _closure_with_run_context(
            host="unknown",
            transport="unknown",
            profile="unknown",
            jury="off",
            consent="unknown (scopes: none)",
        )
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _trusted_comment(empty),
                _trusted_comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM"),
            ],
            issue_comments=[_trusted_comment(_closure_with_run_context())],
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["findings"][0]["id"], "run-context-empty")
        self.assertEqual(report["findings"][0]["severity"], "major")

    def test_fully_degraded_run_context_is_minor_when_not_enforced(self):
        empty = _closure_with_run_context(
            host="unknown",
            transport="unknown",
            profile="unknown",
            jury="off",
            consent="unknown (scopes: none)",
        )
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(empty)],
            issue_comments=[],
            enforced=False,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"][0]["severity"], "minor")

    def test_explicit_untrusted_bot_comment_markers_are_not_evidence(self):
        bot_comment = {
            "body": "keel.review-verdict.v1\nhead: abc123\nLGTM",
            "author_association": "NONE",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }

        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(closure.COMMENT_MARKER), bot_comment],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1"])

    def test_missing_author_association_fails_closed_when_enforced(self):
        missing_association = {
            "body": "keel.review-verdict.v1\nreviewer: fixture\nhead: abc123\nLGTM",
            "user": {"login": "fixture-agent"},
        }
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(closure.COMMENT_MARKER), missing_association],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1"])

    def test_missing_author_association_is_accepted_only_when_not_enforced(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                {"body": closure.COMMENT_MARKER},
                {"body": "keel.review-verdict.v1\nreviewer: fixture\nLGTM"},
            ],
            issue_comments=[{"body": closure.COMMENT_MARKER}],
            enforced=False,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["closure_pr"], 1)
        self.assertEqual(report["counts"]["review_verdict"], 1)

    def test_unknown_item_is_not_present(self):
        item = evidence.EvidenceItem("future", "future", True, "future evidence")

        self.assertFalse(evidence._is_present(item, {}))


class TestGateActive(unittest.TestCase):
    def test_legacy_label_present(self):
        self.assertTrue(evidence.gate_active(["keel:ship", "other"], "keel:ship"))

    def test_legacy_label_absent(self):
        self.assertFalse(evidence.gate_active(["other"], "keel:ship"))

    def test_empty_labels(self):
        self.assertFalse(evidence.gate_active([], "keel:ship"))

    def test_none_labels(self):
        self.assertFalse(evidence.gate_active(None, "keel:ship"))

    def test_empty_gate_label_never_matches(self):
        # A blank gate label must never activate the gate, even if a PR somehow
        # carried an empty-string label — otherwise the gate would silently
        # disable itself. (The schema also forbids a blank evidence_gate_label.)
        self.assertFalse(evidence.gate_active(["keel:ship", ""], ""))
        self.assertFalse(evidence.gate_active([], ""))

    def test_ship_branch_arms_gate_without_label(self):
        decision = evidence.gate_decision([], "keel:ship", head_ref="fix/issue-266-hardening")

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-branch")

    def test_review_marker_arms_gate_without_label(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[_trusted_comment("keel.review-verdict.v1\nLGTM")],
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "review-verdict-marker")

    def test_ship_assessment_comment_arms_gate_without_label(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[_trusted_comment("### \U0001f6a2 keel ship\nstatus: pass")],
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-assessment-comment")

    def test_github_actions_ship_assessment_arms_gate_without_label(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[{
                "author_association": "NONE",
                "user": {"login": "github-actions[bot]"},
                "body": "### \U0001f6a2 keel ship\nstatus: pass",
            }],
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-assessment-comment")

    def test_untrusted_ship_assessment_does_not_arm_gate(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[{
                "author_association": "NONE",
                "user": {"login": "drive-by"},
                "body": "### \U0001f6a2 keel ship\nstatus: pass",
            }],
        )

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")

    def test_operator_waiver_disarms_even_with_ship_provenance(self):
        decision = evidence.gate_decision(
            ["keel:evidence-waived"],
            "keel:ship",
            head_ref="fix/issue-266-hardening",
        )

        self.assertFalse(decision["enforced"])
        self.assertTrue(decision["waived"])
        self.assertEqual(decision["reason"], "operator-waiver-label")

    def test_operator_waiver_disarms_even_with_ship_assessment(self):
        decision = evidence.gate_decision(
            ["keel:evidence-waived"],
            "keel:ship",
            pr_comments=[_trusted_comment("### \U0001f6a2 keel ship\nstatus: pass")],
        )

        self.assertFalse(decision["enforced"])
        self.assertTrue(decision["waived"])
        self.assertEqual(decision["reason"], "operator-waiver-label")

    def test_hand_authored_pr_without_ship_provenance_is_ungated(self):
        decision = evidence.gate_decision([], "keel:ship", head_ref="docs/readme-polish")

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")

    def test_legacy_gate_label_still_arms_existing_workflows(self):
        decision = evidence.gate_decision(["keel:ship"], "keel:ship")

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "gate-label")

    def test_ledger_record_arms_gate(self):
        decision = evidence.gate_decision([], "keel:ship", ledger_records=[{"run": "ship"}])

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-run-ledger")

    def test_blank_waiver_label_cannot_disarm(self):
        decision = evidence.gate_decision([""], "keel:ship", waiver_label="")

        self.assertFalse(decision["waived"])
        self.assertEqual(decision["reason"], "no-ship-provenance")


class TestEvidenceEnforcement(unittest.TestCase):
    def test_verify_not_enforced_passes_with_no_required(self):
        report = evidence.verify(
            _review_contract(reviewers=3, jury=True),
            pr_comments=[],
            issue_comments=[],
            enforced=False,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["required_count"], 0)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["results"], [])
        self.assertFalse(report["enforced"])

    def test_verify_enforced_default_is_unchanged(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_comment("keel.review-verdict.v1\nLGTM")],
            issue_comments=[],
        )

        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["enforced"])
        self.assertEqual(report["missing"], ["closure-comment-pr", "closure-comment-issue"])

    def test_required_items_disabled_when_not_enforced(self):
        self.assertEqual(
            evidence.required_items(_review_contract(reviewers=2), enforced=False),
            (),
        )

    def test_contract_as_dict_not_enforced_clears_required(self):
        contract = evidence.contract_as_dict(
            _review_contract(reviewers=2, jury=True),
            enforced=False,
        )

        self.assertFalse(contract["enforced"])
        self.assertEqual(contract["required"], [])
        self.assertEqual(contract["active_required"], [])

    def test_contract_as_dict_enforced_default_keeps_required(self):
        contract = evidence.contract_as_dict(_review_contract(reviewers=1))

        self.assertTrue(contract["enforced"])
        self.assertTrue(contract["required"])


if __name__ == "__main__":
    unittest.main()
