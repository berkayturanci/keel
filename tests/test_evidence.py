"""Tests for deterministic ship evidence verification."""

import unittest

from keel import agents, artifacts, closure, evidence, ship


def _review_contract(
    *,
    reviewers=2,
    jury=False,
    no_jury=False,
    jury_advisory=False,
    require_distinct_vendors=False,
):
    return ship.resolve_review_contract(
        tier=None,
        reviewer_override=reviewers,
        jury=jury,
        no_jury=no_jury,
        jury_advisory=jury_advisory,
        require_distinct_vendors=require_distinct_vendors,
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
        self.assertEqual(
            ids,
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "jury-verdict",
            ],
        )
        self.assertIn("pull_request_body", contract["not_accepted"])
        self.assertIn("untrusted_public_comment", contract["not_accepted"])
        self.assertTrue(contract["fail_closed"])

    def test_no_jury_drops_jury_requirement(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True, no_jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok."),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "pass")
        self.assertNotIn("jury-verdict", report["missing"])


class TestCountReviewVerdicts(unittest.TestCase):
    def test_counts_distinct_trusted_reviewers(self):
        count = evidence.count_review_verdicts(
            pr_comments=[
                _trusted_comment(
                    "keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.", reviewer="agent-a"
                ),
                _trusted_comment(
                    "keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.", reviewer="agent-b"
                ),
                # idempotent re-post by agent-a collapses to one verdict
                _trusted_comment("keel.review-verdict.v1\nLGTM again", reviewer="agent-a"),
            ],
        )

        self.assertEqual(count, 2)

    def test_ignores_untrusted_and_non_verdicts(self):
        count = evidence.count_review_verdicts(
            pr_comments=[
                _untrusted_comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok."),
                _comment("just a chat comment"),
            ],
            pr_reviews=[
                _comment("keel.review-verdict.v1\nLGTM\nreviewer: r1\n\nsrc/keel/evidence.py: ok.")
            ],
        )

        self.assertEqual(count, 1)

    def test_empty_inputs_count_zero(self):
        self.assertEqual(evidence.count_review_verdicts(), 0)


class TestEvidenceVerify(unittest.TestCase):
    def test_missing_closure_blocks(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.")],
            issue_comments=[],
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["missing"], ["closure-comment-pr", "closure-comment-issue"])

    def test_missing_review_blocks(self):
        report = evidence.verify(
            _review_contract(reviewers=2),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok."),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["missing"], ["review-verdict-2"])

    def test_missing_jury_blocks_when_gating(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok."),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["missing"], ["jury-verdict"])

    def test_deferral_allows_missing_item(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok."),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            deferrals=("jury-verdict",),
        )

        self.assertEqual(report["status"], "pass")
        self.assertTrue(
            next(item for item in report["results"] if item["id"] == "jury-verdict")["deferred"]
        )

    def test_dry_run_has_no_required_evidence(self):
        report = evidence.verify(_review_contract(reviewers=3, jury=True), dry_run=True)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["required_count"], 0)

    def test_pr_body_chat_summary_and_assessment_are_not_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_body=f"{closure.COMMENT_MARKER}\nkeel.review-verdict.v1\nLGTM"
            "\n\nsrc/keel/evidence.py: ok.",
            pr_comments=[
                _comment("### \U0001f6a2 keel ship\nLGTM reviewer verdict"),
                _comment("chat summary: reviewer says LGTM"),
            ],
            issue_comments=[],
            pr_reviews=[],
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["counts"]["closure_pr"], 0)
        self.assertEqual(report["counts"]["review_verdict"], 0)

    def test_review_marker_wins_even_when_comment_mentions_jury(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment(
                    "keel.review-verdict.v1\nReviewer LGTM; --no-jury was checked."
                    "\n\nsrc/keel/ship.py: ok."
                ),
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

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)

    def test_review_verdicts_are_distinct_by_declared_reviewer(self):
        report = evidence.verify(
            _review_contract(reviewers=2),
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                _comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
                _comment("keel.review-verdict.v1\nreviewer: alpha\nLGTM again"),
                _comment(
                    "keel.review-verdict.v1\nreviewer: beta\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
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
                    "body": "keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.",
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
                _comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nhead: old\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
                _comment(
                    "keel.review-verdict.v1\nreviewer: beta\nhead: abc123\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
                _comment("keel.jury-verdict.v1\nhead: old\nAI Jury LGTM"),
                _comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            pr_reviews=[
                {
                    "body": "keel.review-verdict.v1\nreviewer: gamma\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok.",
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
                _comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
                _comment("keel.jury-verdict.v1\nAI Jury LGTM"),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1", "jury-verdict"])

    def test_untrusted_comment_markers_are_not_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _untrusted_comment(closure.COMMENT_MARKER),
                _untrusted_comment(
                    "keel.review-verdict.v1\nreviewer: forged\nhead: abc123\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
                _untrusted_comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ],
            issue_comments=[_untrusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["counts"]["closure_pr"], 0)
        self.assertEqual(report["counts"]["closure_issue"], 0)
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)
        self.assertEqual(
            report["missing"],
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "jury-verdict",
            ],
        )

    def test_trusted_comment_markers_are_evidence(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=True),
            pr_comments=[
                _trusted_comment(closure.COMMENT_MARKER),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nhead: abc123\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
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
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
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
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
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
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
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
            "body": "keel.review-verdict.v1\nhead: abc123\nLGTM\n\nsrc/keel/evidence.py: ok.",
            "author_association": "NONE",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }

        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(closure.COMMENT_MARKER), bot_comment],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1"])

    def test_missing_author_association_fails_closed_when_enforced(self):
        missing_association = {
            "body": "keel.review-verdict.v1\nreviewer: fixture\nhead: abc123\nLGTM"
            "\n\nsrc/keel/evidence.py: ok.",
            "user": {"login": "fixture-agent"},
        }
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(closure.COMMENT_MARKER), missing_association],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

        self.assertEqual(report["status"], "waiting")
        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1"])

    def test_missing_author_association_is_accepted_only_when_not_enforced(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[
                {"body": closure.COMMENT_MARKER},
                {
                    "body": "keel.review-verdict.v1\nreviewer: fixture\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                },
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
            pr_comments=[
                _trusted_comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.")
            ],
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
            pr_comments=[
                {
                    "author_association": "NONE",
                    "user": {"login": "github-actions[bot]"},
                    "body": "### \U0001f6a2 keel ship\nstatus: pass",
                }
            ],
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-assessment-comment")

    def test_untrusted_ship_assessment_does_not_arm_gate(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[
                {
                    "author_association": "NONE",
                    "user": {"login": "drive-by"},
                    "body": "### \U0001f6a2 keel ship\nstatus: pass",
                }
            ],
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


class TestShipProvenanceArming(unittest.TestCase):
    """The marker a live run posts on its own PR arms the gate (#1013)."""

    def _provenance(self):
        return artifacts.render_ship_provenance(
            run_id="run-1",
            issue=1013,
            head_sha="0c458965",
            implementer_attribution=agents.attribution("agy", "gemini-3.8-flash-high"),
        )

    def test_marker_arms_gate_with_no_other_signal(self):
        # The live shape: branch named `fix/2467-slug`, no verdicts posted, ledger
        # in an unreadable per-run worktree. Every legacy signal is absent.
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            head_ref="fix/2467-slug",
            pr_comments=[_trusted_comment(self._provenance())],
            pr_reviews=[],
            ledger_records=[],
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-provenance-comment")
        self.assertEqual(decision["source"], evidence.SHIP_PROVENANCE_MARKER)

    def test_without_the_marker_that_same_pr_is_ungated(self):
        # The regression this fixes: identical inputs minus the comment.
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            head_ref="fix/2467-slug",
            pr_comments=[],
            pr_reviews=[],
            ledger_records=[],
        )

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")

    def test_marker_is_consulted_before_the_branch_regex(self):
        # Both signals present: the reason must name the marker, not the branch.
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            head_ref="fix/issue-266-hardening",
            pr_comments=[_trusted_comment(self._provenance())],
        )

        self.assertEqual(decision["reason"], "ship-provenance-comment")

    def test_the_gate_label_still_wins_over_the_marker(self):
        decision = evidence.gate_decision(
            ["keel:ship"],
            "keel:ship",
            pr_comments=[_trusted_comment(self._provenance())],
        )

        self.assertEqual(decision["reason"], "gate-label")

    def test_operator_waiver_still_disarms_over_the_marker(self):
        decision = evidence.gate_decision(
            ["keel:evidence-waived"],
            "keel:ship",
            pr_comments=[_trusted_comment(self._provenance())],
        )

        self.assertFalse(decision["enforced"])
        self.assertTrue(decision["waived"])
        self.assertEqual(decision["reason"], "operator-waiver-label")

    def test_untrusted_marker_does_not_arm(self):
        # An outside contributor must not be able to manufacture provenance.
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[_untrusted_comment(self._provenance())],
        )

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")

    def test_every_legacy_arming_path_still_arms(self):
        # Nothing was removed when the marker went in front.
        cases = {
            "gate-label": {"labels": ["keel:ship"]},
            "ship-branch": {"head_ref": "fix/issue-266-hardening"},
            "ship-assessment-comment": {
                "pr_comments": [_trusted_comment("### \U0001f6a2 keel ship\nstatus: pass")]
            },
            "review-verdict-marker": {
                "pr_comments": [
                    _trusted_comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.")
                ]
            },
            "ship-run-ledger": {"ledger_records": [{"run": "ship"}]},
        }
        for reason, kwargs in cases.items():
            with self.subTest(reason=reason):
                labels = kwargs.pop("labels", [])
                decision = evidence.gate_decision(labels, "keel:ship", **kwargs)
                self.assertTrue(decision["enforced"])
                self.assertEqual(decision["reason"], reason)


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
            pr_comments=[_comment("keel.review-verdict.v1\nLGTM\n\nsrc/keel/evidence.py: ok.")],
            issue_comments=[],
        )

        self.assertEqual(report["status"], "waiting")
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


def _ship_run_record(*, pr_number=42):
    return {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "target": "demo",
        "actors": {"implementer": "codex", "reviewers": ["claude"], "tester": "codex"},
        "pull_request": {"number": pr_number},
        "changes": {"file_count": 1, "files": ["src/keel/evidence.py"]},
        "capture": {"status": "applied"},
        "run_id": "run-1",
        "run_context": {
            "host_agent": "codex",
            "transport": "gh",
            "profile": "standard",
            "jury_mode": "off",
            "consent": {"status": "approved", "scopes": ["git"]},
        },
    }


class TestClosureNormalization(unittest.TestCase):
    def test_normalize_strips_trailing_whitespace_and_collapses_blanks(self):
        drifted = "a  \n\n\n\nb   \n"
        self.assertEqual(evidence._normalize_closure_body(drifted), "a\n\nb")

    def test_normalize_drops_leading_and_trailing_blank_lines(self):
        self.assertEqual(evidence._normalize_closure_body("\n\nx\n\n"), "x")

    def test_body_matches_record_round_trip(self):
        record = _ship_run_record()
        rendered = closure.render_closure_comment(record)
        self.assertTrue(evidence.closure_body_matches_record(rendered, record))

    def test_body_matches_record_tolerates_harmless_drift(self):
        record = _ship_run_record()
        rendered = closure.render_closure_comment(record)
        drifted = rendered.replace("\n\n", "\n\n\n") + "\n\n   \n"
        drifted = "\n".join(line + "  " for line in drifted.splitlines())
        self.assertTrue(evidence.closure_body_matches_record(drifted, record))

    def test_body_matches_record_rejects_content_change(self):
        record = _ship_run_record()
        tampered = closure.render_closure_comment(record).replace("codex", "intruder")
        self.assertFalse(evidence.closure_body_matches_record(tampered, record))


class TestClosureFidelity(unittest.TestCase):
    def _verify(self, *, pr_body, issue_body, record):
        return evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(pr_body)],
            issue_comments=[_trusted_comment(issue_body)],
            ledger_record=record,
            deferrals=("review",),
        )

    def test_matching_closure_passes(self):
        record = _ship_run_record()
        body = closure.render_closure_comment(record)
        report = self._verify(pr_body=body, issue_body=body, record=record)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["closure_pr"], 1)
        self.assertEqual(report["counts"]["closure_issue"], 1)

    def test_mismatched_pr_closure_fails_with_reason(self):
        record = _ship_run_record()
        good = closure.render_closure_comment(record)
        tampered = good.replace("codex", "intruder")
        report = self._verify(pr_body=tampered, issue_body=good, record=record)

        self.assertEqual(report["status"], "fail")
        self.assertIn("closure-comment-pr", report["missing"])
        self.assertNotIn("closure-comment-issue", report["missing"])
        pr_result = next(item for item in report["results"] if item["id"] == "closure-comment-pr")
        self.assertEqual(
            pr_result["reason"],
            "closure comment does not match the ship_run ledger record",
        )

    def test_mismatched_issue_closure_fails(self):
        record = _ship_run_record()
        good = closure.render_closure_comment(record)
        tampered = good.replace("applied", "skipped")
        report = self._verify(pr_body=good, issue_body=tampered, record=record)

        self.assertEqual(report["status"], "fail")
        self.assertIn("closure-comment-issue", report["missing"])
        issue_result = next(
            item for item in report["results"] if item["id"] == "closure-comment-issue"
        )
        self.assertEqual(
            issue_result["reason"],
            "closure comment does not match the ship_run ledger record",
        )

    def test_no_ledger_record_keeps_marker_only_behavior(self):
        # A hand-written marker-bearing body passes when no record exists.
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(closure.COMMENT_MARKER + "\nhand written")],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER + "\nhand written")],
            ledger_record=None,
            deferrals=("review",),
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["closure_pr"], 1)

    def test_marker_only_body_fails_when_record_exists(self):
        # The GAP-9 regression: marker presence alone no longer suffices.
        record = _ship_run_record()
        good = closure.render_closure_comment(record)
        stale = closure.COMMENT_MARKER + "\nstale hand-written body"
        report = self._verify(pr_body=stale, issue_body=good, record=record)

        self.assertEqual(report["status"], "fail")
        self.assertIn("closure-comment-pr", report["missing"])

    def test_multiple_closures_one_matching_passes(self):
        record = _ship_run_record()
        good = closure.render_closure_comment(record)
        stale = closure.COMMENT_MARKER + "\nsuperseded stale body"
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_trusted_comment(stale), _trusted_comment(good)],
            issue_comments=[_trusted_comment(stale), _trusted_comment(good)],
            ledger_record=record,
            deferrals=("review",),
        )

        self.assertEqual(report["status"], "pass")
        # The matching re-post supersedes the stale one; no mismatch reason fires.
        pr_result = next(item for item in report["results"] if item["id"] == "closure-comment-pr")
        self.assertTrue(pr_result["ok"])

    def test_whitespace_drift_still_matches(self):
        record = _ship_run_record()
        good = closure.render_closure_comment(record)
        drifted = good.replace("- **Tester:** codex", "- **Tester:** codex   ")
        drifted = drifted.replace("\n\n", "\n\n\n")
        report = self._verify(pr_body=drifted, issue_body=drifted, record=record)

        self.assertEqual(report["status"], "pass")


def _verdict(reviewer, *, head="abc123", vendor=None, model=None, scope=None):
    from keel import artifacts

    return _comment(
        artifacts.render_review_verdict(
            reviewer=reviewer,
            head_sha=head,
            vendor=vendor,
            model=model,
            # The renderer's generic default scope does not satisfy the substance
            # gate, by design: "Full changed-file diff and relevant contracts"
            # names nothing (#926). A fixture standing in for a real verdict has
            # to look like one.
            scope=scope or "src/keel/evidence.py and tests/test_evidence.py",
        )
    )


class TestDistinctVendorCheck(unittest.TestCase):
    """The pure, I/O-free vendor-distinctness primitive."""

    def test_non_positive_required_count_always_passes(self):
        result = evidence.distinct_vendor_check([], required_count=0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["duplicated"], [])
        self.assertEqual(result["missing_provenance"], 0)

    def test_distinct_vendors_pass(self):
        result = evidence.distinct_vendor_check(["claude", "codex"], required_count=2)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["reason"])

    def test_duplicate_vendors_fail(self):
        result = evidence.distinct_vendor_check(["claude", "claude"], required_count=2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicated"], ["claude"])
        self.assertIn("share a vendor", result["reason"])

    def test_missing_provenance_fails(self):
        result = evidence.distinct_vendor_check(["claude", None], required_count=2)
        self.assertFalse(result["ok"])
        self.assertIn("missing vendor provenance", result["reason"])
        self.assertEqual(result["missing_provenance"], 1)

    def test_triple_duplicate_reports_each_vendor_once(self):
        result = evidence.distinct_vendor_check(["claude", "claude", "codex"], required_count=3)
        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicated"], ["claude"])


class TestPanelVendorCheck(unittest.TestCase):
    """A jury panel is held to the jury's own cross-vendor rule (#1015)."""

    def test_a_panel_may_span_fewer_vendors_than_it_has_ballots(self):
        result = evidence.panel_vendor_check(
            ["anthropic", "google", "anthropic"], required_count=3, minimum_vendors=2
        )

        self.assertTrue(result["ok"])
        self.assertIsNone(result["reason"])
        # …and the repetition is still reported, so a reader can see the shape.
        self.assertEqual(result["duplicated"], ["anthropic"])

    def test_one_vendor_for_the_whole_panel_is_one_opinion_n_times(self):
        result = evidence.panel_vendor_check(
            ["anthropic", "anthropic", "anthropic"], required_count=3, minimum_vendors=2
        )

        self.assertFalse(result["ok"])
        self.assertIn("1 distinct vendor(s)", result["reason"])
        self.assertIn("minimum of 2", result["reason"])

    def test_missing_provenance_still_fails(self):
        """The relaxation is in how many vendors, never in whether they are declared."""
        result = evidence.panel_vendor_check(
            ["anthropic", None, "google"], required_count=3, minimum_vendors=2
        )

        self.assertFalse(result["ok"])
        self.assertIn("missing vendor provenance", result["reason"])
        self.assertEqual(result["missing_provenance"], 1)

    def test_nothing_required_passes(self):
        result = evidence.panel_vendor_check([], required_count=0, minimum_vendors=2)

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_provenance"], 0)


class TestJuryPanelDistinctnessInVerify(unittest.TestCase):
    """`verify` asks the panel question of a panel, and the bench question of a bench."""

    @staticmethod
    def _panel_contract(*, panel_size=3, minimum_vendors=2):
        from keel import team as team_policy

        assignment = team_policy.resolve_assignment(
            team_policy.parse_team(
                {
                    "review": {"by_tier": {"3": "jury"}},
                    "jury": {"mode": "gating", "min_vendors": minimum_vendors},
                }
            ),
            tier=3,
            default_count=3,
        )
        return ship.resolve_review_contract(
            tier=3,
            assignment=assignment,
            require_distinct_vendors=True,
            jury_panel_size=panel_size,
        )

    def _verify(self, contract, vendors):
        return evidence.verify(
            contract,
            pr_comments=[
                _comment(closure.COMMENT_MARKER),
                *(_verdict(f"panelist-{i}", vendor=vendor) for i, vendor in enumerate(vendors)),
                _comment(
                    f"{evidence.JURY_VERDICT_MARKER}\nhead: abc123\nvendors: 2\npanelists: 3\n"
                ),
            ],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
        )

    def test_three_ballots_from_two_vendors_pass(self):
        """The strict per-slot rule would refuse this; a panel is not a bench (#1015)."""
        report = self._verify(self._panel_contract(), ["anthropic", "google", "anthropic"])

        self.assertEqual(report["status"], "pass")
        self.assertFalse([f for f in report["findings"] if f["id"] == "review-vendor-distinctness"])

    def test_a_panel_that_spans_one_vendor_is_refused(self):
        report = self._verify(self._panel_contract(), ["anthropic", "anthropic", "anthropic"])

        finding = next(f for f in report["findings"] if f["id"] == "review-vendor-distinctness")
        self.assertEqual(report["status"], "fail")
        self.assertIn("1 distinct vendor(s)", finding["message"])

    def test_a_raised_minimum_is_honoured(self):
        report = self._verify(
            self._panel_contract(minimum_vendors=3), ["anthropic", "google", "anthropic"]
        )

        finding = next(f for f in report["findings"] if f["id"] == "review-vendor-distinctness")
        self.assertIn("minimum of 3", finding["message"])

    def test_a_contract_without_a_jury_block_falls_back_to_the_schema_floor(self):
        """A hand-built contract still gets the documented minimum, not zero."""
        contract = {
            "reviewers": {"count": 2, "panel": "jury", "require_distinct_vendors": True},
        }

        report = self._verify(contract, ["anthropic", "anthropic"])

        finding = next(f for f in report["findings"] if f["id"] == "review-vendor-distinctness")
        self.assertIn("minimum of 2", finding["message"])


class TestReviewPanelAccessor(unittest.TestCase):
    def test_a_contract_without_a_reviewers_block_reads_as_the_host_bench(self):
        for contract in ({}, {"reviewers": "jury"}, {"reviewers": {"panel": ""}}):
            with self.subTest(contract=contract):
                self.assertEqual(evidence.review_panel(contract), "reviewers")

    def test_a_jury_panel_is_reported_as_one(self):
        self.assertEqual(evidence.review_panel({"reviewers": {"panel": "jury"}}), "jury")


class TestRequireDistinctVendors(unittest.TestCase):
    """The optional ``require_distinct_vendors`` evidence knob (default OFF)."""

    def _verify(self, *, verdicts, reviewers=2, require, deferrals=()):
        return evidence.verify(
            _review_contract(reviewers=reviewers, require_distinct_vendors=require),
            pr_comments=[_comment(closure.COMMENT_MARKER), *verdicts],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
            deferrals=deferrals,
        )

    def test_knob_off_allows_duplicate_vendors(self):
        report = self._verify(
            verdicts=[
                _verdict("alpha", vendor="claude"),
                _verdict("beta", vendor="claude"),
            ],
            require=False,
        )
        self.assertEqual(report["status"], "pass")
        self.assertFalse(any(f["id"] == "review-vendor-distinctness" for f in report["findings"]))

    def test_knob_off_allows_missing_provenance(self):
        report = self._verify(
            verdicts=[_verdict("alpha"), _verdict("beta")],
            require=False,
        )
        self.assertEqual(report["status"], "pass")

    def test_knob_on_distinct_vendors_pass(self):
        report = self._verify(
            verdicts=[
                _verdict("alpha", vendor="claude"),
                _verdict("beta", vendor="codex"),
            ],
            require=True,
        )
        self.assertEqual(report["status"], "pass")

    def test_knob_on_duplicate_vendors_fail(self):
        report = self._verify(
            verdicts=[
                _verdict("alpha", vendor="claude"),
                _verdict("beta", vendor="claude"),
            ],
            require=True,
        )
        self.assertEqual(report["status"], "fail")
        finding = next(f for f in report["findings"] if f["id"] == "review-vendor-distinctness")
        self.assertEqual(finding["severity"], "major")
        self.assertIn("claude", finding["message"])

    def test_knob_on_missing_provenance_fail(self):
        report = self._verify(
            verdicts=[_verdict("alpha", vendor="claude"), _verdict("beta")],
            require=True,
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(f["id"] == "review-vendor-distinctness" for f in report["findings"]))

    def test_knob_on_with_zero_reviewers_is_noop(self):
        report = evidence.verify(
            _review_contract(reviewers=1, require_distinct_vendors=True),
            pr_comments=[_comment(closure.COMMENT_MARKER)],
            issue_comments=[_comment(closure.COMMENT_MARKER)],
            head_sha="abc123",
            dry_run=True,
        )
        self.assertFalse(any(f["id"] == "review-vendor-distinctness" for f in report["findings"]))

    def test_deferred_review_skips_distinctness(self):
        report = self._verify(
            verdicts=[
                _verdict("alpha", vendor="claude"),
                _verdict("beta", vendor="claude"),
            ],
            require=True,
            deferrals=("review",),
        )
        self.assertFalse(any(f["id"] == "review-vendor-distinctness" for f in report["findings"]))

    def test_provenance_map_skips_untrusted_head_mismatch_and_duplicates(self):
        provenance = evidence._review_vendor_provenance(
            [
                _untrusted_comment(
                    "keel.review-verdict.v1\nreviewer: u\nvendor: claude\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
                _comment(
                    "keel.review-verdict.v1\nreviewer: a\nhead: old\nvendor: x\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
                _comment(
                    "keel.review-verdict.v1\nreviewer: b\nhead: abc123\nvendor: claude\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
                _comment(
                    "keel.review-verdict.v1\nreviewer: b\nhead: abc123\nvendor: codex\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            head_sha="abc123",
        )
        self.assertEqual(provenance, {"reviewer:b": "claude"})

    def test_provenance_map_records_missing_vendor_as_none(self):
        provenance = evidence._review_vendor_provenance(
            [
                _comment(
                    "keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"
                    "\n\nsrc/keel/evidence.py: ok."
                )
            ],
            head_sha="abc123",
        )
        self.assertEqual(provenance, {"reviewer:a": None})

    def test_contract_exposes_require_distinct_vendors(self):
        on = evidence.contract_as_dict(_review_contract(require_distinct_vendors=True))
        off = evidence.contract_as_dict(_review_contract(require_distinct_vendors=False))
        self.assertTrue(on["require_distinct_vendors"])
        self.assertFalse(off["require_distinct_vendors"])


def _satisfied_evidence_kwargs():
    """Evidence inputs that satisfy a one-reviewer contract (closure + review)."""
    return {
        "pr_comments": [
            _trusted_comment(_closure_with_run_context()),
            _trusted_comment(
                "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
            ),
        ],
        "issue_comments": [_trusted_comment(_closure_with_run_context())],
    }


def _ledger_record(implementer):
    return {"actors": {"implementer": implementer}}


class TestAgentLabelVendors(unittest.TestCase):
    def test_extracts_lowercased_vendor_slugs(self):
        self.assertEqual(
            evidence.agent_label_vendors(["agent:Claude", "model:gpt-5", "agent:codex"]),
            ["claude", "codex"],
        )

    def test_ignores_blank_and_non_agent_labels(self):
        self.assertEqual(
            evidence.agent_label_vendors(["agent:", "agent: ", "tier:3", 7, None]),
            [],
        )

    def test_none_labels_yield_empty(self):
        self.assertEqual(evidence.agent_label_vendors(None), [])


class TestLedgerImplementerVendor(unittest.TestCase):
    def test_vendor_before_colon(self):
        self.assertEqual(
            evidence.ledger_implementer_vendor(_ledger_record("ollama:qwen2.5")),
            "ollama",
        )

    def test_bare_codename(self):
        self.assertEqual(
            evidence.ledger_implementer_vendor(_ledger_record("Claude")),
            "claude",
        )

    def test_none_record(self):
        self.assertIsNone(evidence.ledger_implementer_vendor(None))

    def test_missing_or_blank_implementer(self):
        self.assertIsNone(evidence.ledger_implementer_vendor({"actors": {}}))
        self.assertIsNone(evidence.ledger_implementer_vendor(_ledger_record("  ")))
        self.assertIsNone(evidence.ledger_implementer_vendor(_ledger_record(None)))

    def test_non_dict_actors(self):
        self.assertIsNone(evidence.ledger_implementer_vendor({"actors": "claude"}))


class TestAttributionCheck(unittest.TestCase):
    def test_present_and_matching_vendor_ok(self):
        result = evidence.attribution_check(["agent:claude"], implementer_vendor="claude")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["reason"])

    def test_missing_label_is_finding(self):
        result = evidence.attribution_check([], implementer_vendor="claude")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing-label")

    def test_vendor_mismatch_is_finding(self):
        result = evidence.attribution_check(["agent:codex"], implementer_vendor="claude")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "vendor-mismatch")

    def test_no_implementer_is_presence_only(self):
        result = evidence.attribution_check(["agent:codex"], implementer_vendor=None)
        self.assertTrue(result["ok"])

    def test_matching_among_multiple_labels_ok(self):
        result = evidence.attribution_check(
            ["agent:codex", "agent:claude"], implementer_vendor="claude"
        )
        self.assertTrue(result["ok"])

    def test_implementer_vendor_is_normalized(self):
        result = evidence.attribution_check(["agent:claude"], implementer_vendor="  Claude ")
        self.assertTrue(result["ok"])


class TestAttributionVerifyWiring(unittest.TestCase):
    def test_present_and_matching_passes(self):
        # Cross-check matching is exercised at the pure layer; here we confirm the
        # presence layer does not flag a PR that carries an agent:* label. No
        # ledger_record is passed so closure-fidelity matching stays out of scope.
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=["agent:claude"],
            **_satisfied_evidence_kwargs(),
        )
        self.assertEqual(report["status"], "pass")
        self.assertNotIn("attribution-label", [f["id"] for f in report["findings"]])

    def test_matching_vendor_with_ledger_record_passes(self):
        # A real ship_run record (implementer "codex"): closure comments render-
        # match it and the agent:codex label matches its implementer vendor.
        record = _ship_run_record()
        body = closure.render_closure_comment(record)
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=["agent:codex"],
            ledger_record=record,
            pr_comments=[
                _trusted_comment(body),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            issue_comments=[_trusted_comment(body)],
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_vendor_mismatch_with_ledger_record_fails(self):
        # Same matching closure, but the label vendor (claude) contradicts the
        # record's implementer vendor (codex) -> attribution finding, gate fails.
        record = _ship_run_record()
        body = closure.render_closure_comment(record)
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=["agent:claude"],
            ledger_record=record,
            pr_comments=[
                _trusted_comment(body),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            issue_comments=[_trusted_comment(body)],
        )
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["missing"], [])
        finding = next(f for f in report["findings"] if f["id"] == "attribution-label")
        self.assertEqual(finding["severity"], "major")
        self.assertIn("does not match", finding["message"])

    def test_missing_label_fails_when_gate_active(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=[],
            **_satisfied_evidence_kwargs(),
        )
        self.assertEqual(report["status"], "fail")
        finding = next(f for f in report["findings"] if f["id"] == "attribution-label")
        self.assertEqual(finding["severity"], "major")
        self.assertIn("missing a mandatory agent", finding["message"])

    def test_no_ledger_record_is_presence_only(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=["agent:codex"],
            ledger_record=None,
            **_satisfied_evidence_kwargs(),
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_gate_inactive_skips_attribution_check(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=[],
            enforced=False,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_dry_run_skips_attribution_check(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=[],
            dry_run=True,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])


#: The live PR state issue #1013 reports: the host wrote both halves itself, so the
#: label and the ledger agreed with each other and disagreed with keel.
_LIVE_IMPLEMENTER = "gemini:gemini-3.8-flash-high"
_LIVE_LABELS = ["agent:gemini", "model:gemini"]


class TestLedgerImplementer(unittest.TestCase):
    def test_returns_the_whole_string(self):
        self.assertEqual(
            evidence.ledger_implementer(_ledger_record(_LIVE_IMPLEMENTER)),
            _LIVE_IMPLEMENTER,
        )

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(evidence.ledger_implementer(_ledger_record("  codex  ")), "codex")

    def test_missing_blank_or_malformed_reads_as_none(self):
        for record in (None, "not-a-dict", {}, {"actors": "codex"}, _ledger_record("  ")):
            with self.subTest(record=record):
                self.assertIsNone(evidence.ledger_implementer(record))


class TestModelLabelBases(unittest.TestCase):
    def test_extracts_lowercased_bases(self):
        self.assertEqual(
            evidence.model_label_bases(["model:Gemini-3", "agent:agy", "model:gpt-4o"]),
            ["gemini-3", "gpt-4o"],
        )

    def test_ignores_blank_and_non_model_labels(self):
        self.assertEqual(evidence.model_label_bases(["model:", "model: ", 7, None]), [])

    def test_none_labels(self):
        self.assertEqual(evidence.model_label_bases(None), [])


class TestAttributionVocabularyCheck(unittest.TestCase):
    def test_the_live_labels_are_refused(self):
        result = evidence.attribution_vocabulary_check(_LIVE_LABELS, implementer=_LIVE_IMPLEMENTER)
        self.assertFalse(result["ok"])
        self.assertTrue(result["checked"])
        self.assertEqual(result["reason"], "model-label")
        self.assertEqual(result["expected"]["model_label"], "model:gemini-3")

    def test_keels_own_labels_pass(self):
        result = evidence.attribution_vocabulary_check(
            ["agent:agy", "model:gemini-3"], implementer="agy:gemini-3.8-flash-high"
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["checked"])

    def test_a_wrong_agent_label_is_refused(self):
        result = evidence.attribution_vocabulary_check(
            ["agent:claude"], implementer="agy:gemini-3.8-flash-high"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "agent-label")

    def test_a_profile_run_labels_agent_cli(self):
        # keel writes `cli` (never the profile name) into actors.implementer, so the
        # expected label for every generic-CLI profile is agent:cli.
        result = evidence.attribution_vocabulary_check(
            ["agent:cli", "model:composer-1"], implementer="cli:composer-1"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            evidence.attribution_vocabulary_check([], implementer="cli:composer-1")["expected"][
                "agent_label"
            ],
            "agent:cli",
        )

    def test_no_implementer_is_not_checked(self):
        result = evidence.attribution_vocabulary_check(_LIVE_LABELS, implementer=None)
        self.assertTrue(result["ok"])
        self.assertFalse(result["checked"])
        self.assertEqual(result["reason"], "no-implementer")
        self.assertIsNone(result["expected"])

    def test_absent_labels_are_not_judged(self):
        # A missing agent: label is attribution_check's missing-label finding; this
        # check must not repeat it, and it cannot judge a label that is not there.
        result = evidence.attribution_vocabulary_check([], implementer=_LIVE_IMPLEMENTER)
        self.assertTrue(result["ok"])
        self.assertTrue(result["checked"])

    def test_a_model_less_implementer_leaves_model_labels_alone(self):
        # `claude` records no model, so there is no expected model label to compare.
        result = evidence.attribution_vocabulary_check(
            ["agent:claude", "model:whatever"], implementer="claude"
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(result["expected"]["model_label"])

    def test_one_matching_label_among_several_is_enough(self):
        result = evidence.attribution_vocabulary_check(
            ["agent:codex", "agent:agy", "model:gemini-3"],
            implementer="agy:gemini-3.8-flash-high",
        )
        self.assertTrue(result["ok"])


class TestAttributionVocabularyVerifyWiring(unittest.TestCase):
    """Replaying the live PR state through ``verify`` (#1013 acceptance)."""

    def _live_record(self):
        record = _ship_run_record()
        record["actors"]["implementer"] = _LIVE_IMPLEMENTER
        return record

    def _report(self, labels, record):
        body = closure.render_closure_comment(record)
        return evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=labels,
            ledger_record=record,
            pr_comments=[
                _trusted_comment(body),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            issue_comments=[_trusted_comment(body)],
        )

    def test_live_labels_produce_a_blocking_finding(self):
        report = self._report(_LIVE_LABELS, self._live_record())

        self.assertEqual(report["status"], "fail")
        finding = next(f for f in report["findings"] if f["id"] == "attribution-vocabulary")
        self.assertEqual(finding["severity"], "major")
        self.assertEqual(finding["kind"], "attribution")

    def test_the_finding_names_the_expected_labels(self):
        report = self._report(_LIVE_LABELS, self._live_record())
        finding = next(f for f in report["findings"] if f["id"] == "attribution-vocabulary")

        self.assertIn("agent:gemini", finding["message"])
        self.assertIn("model:gemini-3", finding["message"])
        self.assertIn("keel attribution", finding["message"])

    def test_the_old_vendor_cross_check_passed_on_this_state(self):
        # Why the new check is needed at all: agent:gemini *matches* the ledger's
        # `gemini:` vendor, so the label/ledger cross-check has nothing to say.
        report = self._report(_LIVE_LABELS, self._live_record())

        self.assertNotIn("attribution-label", [f["id"] for f in report["findings"]])

    def test_keels_own_labels_pass_the_same_replay(self):
        record = self._live_record()
        record["actors"]["implementer"] = "agy:gemini-3.8-flash-high"
        report = self._report(["agent:agy", "model:gemini-3"], record)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_no_ledger_record_skips_rather_than_fails(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=_LIVE_LABELS,
            ledger_record=None,
            **_satisfied_evidence_kwargs(),
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_labels_not_fetched_skips_the_check(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=None,
            ledger_record=self._live_record(),
            **_satisfied_evidence_kwargs(),
        )

        self.assertNotIn("attribution-vocabulary", [f["id"] for f in report["findings"]])

    def test_dry_run_skips_the_check(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=_LIVE_LABELS,
            ledger_record=self._live_record(),
            dry_run=True,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_gate_inactive_skips_the_check(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_labels=_LIVE_LABELS,
            ledger_record=self._live_record(),
            enforced=False,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])


class TestEvidencePhases(unittest.TestCase):
    def test_pre_merge_phase_excludes_closure_items(self):
        items = evidence.required_items(
            _review_contract(reviewers=2, jury=True),
            phase=evidence.PHASE_PRE_MERGE,
        )

        self.assertEqual(
            [item.id for item in items],
            ["review-verdict-1", "review-verdict-2", "jury-verdict"],
        )

    def test_post_merge_phase_is_only_the_closure_pair(self):
        items = evidence.required_items(
            _review_contract(reviewers=2, jury=True),
            phase=evidence.PHASE_POST_MERGE,
        )

        self.assertEqual(
            [item.id for item in items],
            ["closure-comment-pr", "closure-comment-issue"],
        )

    def test_default_phase_is_all_and_partitions_exactly(self):
        contract = _review_contract(reviewers=2, jury=True)

        every = evidence.required_items(contract)
        pre = evidence.required_items(contract, phase=evidence.PHASE_PRE_MERGE)
        post = evidence.required_items(contract, phase=evidence.PHASE_POST_MERGE)

        # `all` is the default, and the two phases partition it with no overlap
        # and nothing dropped — a requirement cannot go missing between phases.
        self.assertEqual(
            evidence.required_items(contract, phase=evidence.PHASE_ALL),
            every,
        )
        self.assertEqual(set(pre) | set(post), set(every))
        self.assertEqual(set(pre) & set(post), set())

    def test_items_declare_their_phase(self):
        by_id = {
            item.id: item.phase
            for item in evidence.required_items(_review_contract(reviewers=1, jury=True))
        }

        self.assertEqual(by_id["closure-comment-pr"], evidence.PHASE_POST_MERGE)
        self.assertEqual(by_id["closure-comment-issue"], evidence.PHASE_POST_MERGE)
        self.assertEqual(by_id["review-verdict-1"], evidence.PHASE_PRE_MERGE)
        self.assertEqual(by_id["jury-verdict"], evidence.PHASE_PRE_MERGE)

    def test_as_dict_carries_phase(self):
        item = evidence.required_items(_review_contract(reviewers=1))[0]

        self.assertEqual(item.as_dict()["phase"], item.phase)

    def test_unknown_phase_raises_rather_than_narrowing(self):
        with self.assertRaises(ValueError) as ctx:
            evidence.required_items(_review_contract(reviewers=1), phase="premerge")

        self.assertIn("premerge", str(ctx.exception))

    def test_unknown_phase_raises_even_when_unenforced(self):
        # The guard runs before the dry-run/unenforced short-circuit, so a typo is
        # never masked by a run that happens to require nothing.
        with self.assertRaises(ValueError):
            evidence.required_items(
                _review_contract(reviewers=1),
                enforced=False,
                phase="nope",
            )

    def test_verify_reports_the_phase_it_checked(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            phase=evidence.PHASE_PRE_MERGE,
            pr_labels=["agent:claude"],
        )

        self.assertEqual(report["phase"], evidence.PHASE_PRE_MERGE)

    def test_pre_merge_verify_does_not_demand_closure_comments(self):
        # The regression this whole split exists for: at s10 the closure comments
        # have not been posted yet, so requiring them makes the backbone unmergeable.
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_verdict("a", head="abc")],
            head_sha="abc",
            pr_labels=["agent:claude"],
            phase=evidence.PHASE_PRE_MERGE,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["missing"], [])


class TestEvidenceArming(unittest.TestCase):
    def test_unarmed_gate_passes_by_default(self):
        report = evidence.verify(_review_contract(reviewers=1), enforced=False)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_require_armed_turns_an_unarmed_gate_into_a_failure(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            enforced=False,
            require_armed=True,
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual([f["id"] for f in report["findings"]], ["gate-unarmed"])
        self.assertEqual(report["findings"][0]["severity"], "major")

    def test_require_armed_accepts_a_deliberate_operator_waiver(self):
        # A waiver is an explicit operator act; the point of the check is to
        # separate that from a gate that armed late or not at all.
        report = evidence.verify(
            _review_contract(reviewers=1),
            enforced=False,
            require_armed=True,
            waived=True,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_require_armed_is_quiet_when_the_gate_is_armed(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            pr_comments=[_verdict("a", head="abc")],
            head_sha="abc",
            pr_labels=["agent:claude"],
            phase=evidence.PHASE_PRE_MERGE,
            require_armed=True,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])

    def test_require_armed_is_suppressed_under_dry_run(self):
        report = evidence.verify(
            _review_contract(reviewers=1),
            dry_run=True,
            enforced=False,
            require_armed=True,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["findings"], [])


class TestJuryParticipatingVendors(unittest.TestCase):
    """The panel size reaches a CI gate only through the posted jury verdict."""

    @staticmethod
    def _verdict_comment(*, vendors=None, participants=(), head="abc", panelists=None):
        from keel import artifacts

        return _comment(
            artifacts.render_jury_verdict(
                head_sha=head,
                participants=participants,
                participating_vendors=vendors,
                panelists=panelists,
            )
        )

    def test_reads_the_declared_count(self):
        got = evidence.jury_participating_vendors(
            [self._verdict_comment(vendors=1)], head_sha="abc"
        )

        self.assertEqual(got, 1)

    def test_zero_is_a_real_answer_not_a_missing_one(self):
        # A run where no agent returned output declares 0; conflating that with
        # "not declared" would leave the gate demanding a verdict that cannot exist.
        got = evidence.jury_participating_vendors(
            [self._verdict_comment(vendors=0)], head_sha="abc"
        )

        self.assertEqual(got, 0)
        self.assertIsNotNone(got)

    def test_no_verdict_posted_is_undeclared(self):
        self.assertIsNone(evidence.jury_participating_vendors([], head_sha="abc"))

    def test_verdict_without_the_field_is_undeclared(self):
        # Back-compat: a verdict posted before the field existed must not be read
        # as a short panel and silently relax the gate.
        body = f"{evidence.JURY_VERDICT_MARKER}\nhead: abc\n\nAI Jury verdict: LGTM.\n"

        self.assertIsNone(evidence.jury_participating_vendors([_comment(body)], head_sha="abc"))

    def test_count_is_inferred_from_participants_when_omitted(self):
        got = evidence.jury_participating_vendors(
            [self._verdict_comment(participants=["claude", "codex"])], head_sha="abc"
        )

        self.assertEqual(got, 2)

    def test_head_mismatch_is_ignored(self):
        got = evidence.jury_participating_vendors(
            [self._verdict_comment(vendors=1, head="stale")], head_sha="abc"
        )

        self.assertIsNone(got)

    def test_a_run_that_resolved_no_head_declares_nothing(self):
        """#1069: the blank head is the half `_matches_head` read as *do not filter*.

        A `vendors: 1` verdict posted against some earlier head reached a gate resolving
        at a head this run never established, and relaxed it. Every blank-head shape
        `keel.juryavail.is_pinnable_head` refuses is refused here, including the non-string
        shapes a JSON payload can hand over.
        """
        stale = [self._verdict_comment(vendors=1, head="three-heads-ago")]

        for head_sha in (None, "", "  \t ", 0, ["abc"], b"abc"):
            with self.subTest(head_sha=head_sha):
                self.assertIsNone(evidence.jury_participating_vendors(stale, head_sha=head_sha))

    def test_the_two_safe_readers_keep_the_permissive_head_rule(self):
        """…and only that one reader moved (#1069).

        `jury_panel_size` feeds `max(declared, minimum_vendors)` and `panel_verdict_posted`
        is refused on a blank head by its own caller, so neither can relax a gate and
        neither takes the pin's rule. A change that pushed the guard down into
        `_matches_head` would fail here.
        """
        stale = [self._verdict_comment(vendors=1, panelists=4, head="three-heads-ago")]

        self.assertIsNone(evidence.jury_participating_vendors(stale, head_sha=None))
        self.assertEqual(evidence.jury_panel_size(stale, head_sha=None), 4)
        self.assertTrue(evidence.panel_verdict_posted(stale, head_sha=None))

    def test_untrusted_author_is_ignored(self):
        item = {
            "body": self._verdict_comment(vendors=1)["body"],
            "author_association": "NONE",
            "user": {"login": "drive-by"},
        }

        self.assertIsNone(evidence.jury_participating_vendors([item], head_sha="abc"))

    def test_largest_declared_count_wins(self):
        # A corrected re-post must not be capped by the stale partial run.
        got = evidence.jury_participating_vendors(
            [self._verdict_comment(vendors=1), self._verdict_comment(vendors=3)],
            head_sha="abc",
        )

        self.assertEqual(got, 3)

    def test_reads_from_pr_reviews_too(self):
        got = evidence.jury_participating_vendors(
            None, [self._verdict_comment(vendors=2)], head_sha="abc"
        )

        self.assertEqual(got, 2)

    def test_non_numeric_count_is_rejected(self):
        body = f"{evidence.JURY_VERDICT_MARKER}\nhead: abc\nvendors: many\n"

        self.assertIsNone(evidence.jury_participating_vendors([_comment(body)], head_sha="abc"))

    def test_negative_count_is_rejected(self):
        body = f"{evidence.JURY_VERDICT_MARKER}\nhead: abc\nvendors: -1\n"

        self.assertIsNone(evidence.jury_participating_vendors([_comment(body)], head_sha="abc"))

    def test_short_panel_verdict_relaxes_the_requirement_end_to_end(self):
        from keel import ship as ship_mod

        declared = evidence.jury_participating_vendors(
            [self._verdict_comment(vendors=1)], head_sha="abc"
        )
        contract = ship_mod.resolve_review_contract(tier=3, jury_participating_vendors=declared)

        ids = [
            item.id for item in evidence.required_items(contract, phase=evidence.PHASE_PRE_MERGE)
        ]
        self.assertNotIn("jury-verdict", ids)
        self.assertEqual(contract["jury"]["mode"], "advisory")


class TestJuryPanelSize(unittest.TestCase):
    """The panel's own size reaches a CI gate the way its vendor count does (#1015)."""

    @staticmethod
    def _verdict_comment(*, panelists=None, participants=(), head="abc"):
        from keel import artifacts

        return _comment(
            artifacts.render_jury_verdict(
                head_sha=head,
                participants=participants,
                panelists=panelists,
            )
        )

    def test_reads_the_declared_size(self):
        self.assertEqual(
            evidence.jury_panel_size([self._verdict_comment(panelists=4)], head_sha="abc"), 4
        )

    def test_no_verdict_posted_is_undeclared(self):
        self.assertIsNone(evidence.jury_panel_size([], head_sha="abc"))

    def test_a_verdict_predating_the_field_is_undeclared(self):
        body = f"{evidence.JURY_VERDICT_MARKER}\nhead: abc\nvendors: 2\n"

        self.assertIsNone(evidence.jury_panel_size([_comment(body)], head_sha="abc"))

    def test_size_is_inferred_from_participants_when_omitted(self):
        got = evidence.jury_panel_size(
            [self._verdict_comment(participants=["a", "b", "c"])], head_sha="abc"
        )

        self.assertEqual(got, 3)

    def test_head_mismatch_is_ignored(self):
        got = evidence.jury_panel_size(
            [self._verdict_comment(panelists=3, head="stale")], head_sha="abc"
        )

        self.assertIsNone(got)

    def test_reads_from_pr_reviews_too(self):
        got = evidence.jury_panel_size(None, [self._verdict_comment(panelists=2)], head_sha="abc")

        self.assertEqual(got, 2)

    def test_the_largest_declared_size_wins(self):
        """A re-post completing a partial panel raises the bar; it never lowers it."""
        got = evidence.jury_panel_size(
            [self._verdict_comment(panelists=2), self._verdict_comment(panelists=5)],
            head_sha="abc",
        )

        self.assertEqual(got, 5)

    def test_a_declared_panel_sizes_the_requirement_end_to_end(self):
        from keel import ship as ship_mod
        from keel import team as team_policy

        assignment = team_policy.resolve_assignment(
            team_policy.parse_team(
                {"review": {"by_tier": {"3": "jury"}}, "jury": {"mode": "gating"}}
            ),
            tier=3,
            default_count=3,
        )
        declared = evidence.jury_panel_size([self._verdict_comment(panelists=4)], head_sha="abc")
        contract = ship_mod.resolve_review_contract(
            tier=3, assignment=assignment, jury_panel_size=declared
        )

        items = evidence.required_items(contract, phase=evidence.PHASE_PRE_MERGE)
        self.assertEqual(
            [item.id for item in items],
            [
                "review-verdict-1",
                "review-verdict-2",
                "review-verdict-3",
                "review-verdict-4",
                "jury-verdict",
            ],
        )
        # …and the requirement says which panel is expected to have posted them.
        self.assertIn("ai-jury panelist", items[0].description)


class TestEvidenceThreeWayStatus(unittest.TestCase):
    def test_waiting_status_when_evidence_absent_without_findings(self):
        report = evidence.verify(
            _review_contract(reviewers=2, jury=True),
            pr_comments=[],
            issue_comments=[],
            pr_reviews=[],
            head_sha="abc1234",
            pr_labels=["agent:codex"],
        )
        self.assertEqual(report["status"], evidence.STATUS_WAITING)
        self.assertEqual(
            report["missing"],
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "jury-verdict",
            ],
        )
        self.assertEqual(report["findings"], [])

    def test_pass_status_when_all_evidence_present(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=False),
            pr_comments=[
                _trusted_comment(closure.COMMENT_MARKER),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            pr_labels=["agent:codex"],
        )
        self.assertEqual(report["status"], evidence.STATUS_PASS)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["findings"], [])

    def test_fail_status_when_blocking_finding_present(self):
        report = evidence.verify(
            _review_contract(reviewers=1, jury=False),
            pr_comments=[
                _trusted_comment(closure.COMMENT_MARKER),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            issue_comments=[_trusted_comment(closure.COMMENT_MARKER)],
            pr_labels=[],  # Missing agent attribution label triggers blocking finding
        )
        self.assertEqual(report["status"], evidence.STATUS_FAIL)
        self.assertTrue(any(f["id"] == "attribution-label" for f in report["findings"]))

    def test_fail_status_when_closure_mismatches_ledger(self):
        record = _ship_run_record()
        tampered_body = f"{closure.COMMENT_MARKER}\nTampered content without matching record"
        report = evidence.verify(
            _review_contract(reviewers=1, jury=False),
            pr_comments=[
                _trusted_comment(tampered_body),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: alpha\nLGTM\n\nsrc/keel/evidence.py: ok."
                ),
            ],
            issue_comments=[_trusted_comment(tampered_body)],
            pr_labels=["agent:codex"],
            ledger_record=record,
        )
        self.assertEqual(report["status"], evidence.STATUS_FAIL)


class TestEvidenceHeaderParsing(unittest.TestCase):
    def test_fields_extracts_top_level_headers(self):
        body = """<!-- keel.review-verdict.v1 -->
reviewer: claude
head: abc1234
vendor: anthropic
model: claude-3-7-sonnet
verdict: pass

Here is the review body.
reviewer: spoofed
head: 0000000
vendor: fake
"""
        fields = evidence._fields(body)
        self.assertEqual(fields["reviewer"], "claude")
        self.assertEqual(fields["head"], "abc1234")
        self.assertEqual(fields["vendor"], "anthropic")
        self.assertEqual(fields["model"], "claude-3-7-sonnet")

    def test_fields_empty_and_no_headers(self):
        self.assertEqual(evidence._fields(""), {})
        self.assertEqual(evidence._fields(None), {})
        self.assertEqual(evidence._fields("Just a regular comment without headers"), {})

    def test_a_marker_only_line_is_skipped_but_prose_naming_one_ends_the_block(self):
        # The header rule reaches the field parser too (#1026): the artifact's own
        # marker line is not a field, while a line that merely mentions a marker is
        # prose — and prose ends the block, so nothing below it can be harvested.
        body = (
            "keel.review-verdict.v1\n"
            "reviewer: claude\n"
            "I re-read the keel.jury-verdict.v1 branch.\n"
            "vendor: spoofed\n"
        )

        self.assertEqual(evidence._fields(body), {"reviewer": "claude"})

    def test_fields_leading_blank_lines_and_duplicate_keys(self):
        body = "\n\n  \nreviewer: claude\nreviewer: second\nhead: 123\n"
        fields = evidence._fields(body)
        self.assertEqual(fields["reviewer"], "claude")
        self.assertEqual(fields["head"], "123")


class TestHeaderAnchoredMarkerClassification(unittest.TestCase):
    """#1026: a marker quoted in prose is content, never a classification signal.

    Observed live: two ``keel.review-verdict.v1`` comments whose scope text
    mentioned the literal string ``keel.jury-verdict.v1`` were counted as
    ``jury_verdict: 2, review_verdict: 0``, so the review that happened was
    invisible to the gate.
    """

    JURY_MARKER_IN_SCOPE = (
        "keel.review-verdict.v1\n"
        "reviewer: alpha\n"
        "head: abc123\n"
        "\n"
        "Verdict: pass\n"
        "\n"
        "Scope reviewed: checked that keel.jury-verdict.v1 classification in "
        "src/keel/evidence.py is anchored to the header.\n"
    )

    REVIEW_MARKER_IN_JURY_PROSE = (
        "keel.jury-verdict.v1\n"
        "head: abc123\n"
        "vendors: 2\n"
        "\n"
        "AI Jury verdict: LGTM.\n"
        "\n"
        "Panel read the keel.review-verdict.v1 path in src/keel/evidence.py.\n"
    )

    def _counts(self, *, pr_comments, reviewers=1, jury=False, no_jury=True):
        report = evidence.verify(
            _review_contract(reviewers=reviewers, jury=jury, no_jury=no_jury),
            pr_comments=pr_comments,
            issue_comments=[],
            head_sha="abc123",
        )
        return report["counts"]

    def test_review_verdict_quoting_the_jury_marker_counts_as_a_review(self):
        counts = self._counts(pr_comments=[_trusted_comment(self.JURY_MARKER_IN_SCOPE)])

        self.assertEqual(counts["review_verdict"], 1)
        self.assertEqual(counts["jury_verdict"], 0)

    def test_jury_verdict_quoting_the_review_marker_counts_as_a_jury_verdict(self):
        counts = self._counts(
            pr_comments=[_trusted_comment(self.REVIEW_MARKER_IN_JURY_PROSE)],
            jury=True,
            no_jury=False,
        )

        self.assertEqual(counts["jury_verdict"], 1)
        self.assertEqual(counts["review_verdict"], 0)

    def test_the_gate_still_finds_both_required_review_verdicts(self):
        # The live symptom: `missing: review-verdict-1, review-verdict-2`.
        report = evidence.verify(
            _review_contract(reviewers=2, no_jury=True),
            pr_comments=[
                _trusted_comment(self.JURY_MARKER_IN_SCOPE, reviewer="agent-a"),
                _trusted_comment(
                    self.JURY_MARKER_IN_SCOPE.replace("reviewer: alpha", "reviewer: beta"),
                    reviewer="agent-b",
                ),
            ],
            head_sha="abc123",
            phase=evidence.PHASE_PRE_MERGE,
        )

        self.assertEqual(report["missing"], [])
        self.assertEqual(report["status"], evidence.STATUS_PASS)

    def test_closure_marker_in_prose_is_not_a_closure_comment(self):
        body = (
            "keel.review-verdict.v1\n"
            "reviewer: alpha\n"
            "head: abc123\n"
            "\n"
            "Checked that keel.closure-comment.v1 is still emitted by "
            "src/keel/closure.py.\n"
        )

        counts = self._counts(pr_comments=[_trusted_comment(body)])

        self.assertEqual(counts["closure_pr"], 0)
        self.assertEqual(counts["review_verdict"], 1)

    def test_deferral_marker_in_prose_does_not_change_the_classification(self):
        body = (
            "keel.review-verdict.v1\n"
            "reviewer: alpha\n"
            "head: abc123\n"
            "\n"
            "The keel.deferral.v1 comment for src/keel/evidence.py is already posted.\n"
        )

        counts = self._counts(pr_comments=[_trusted_comment(body)])

        self.assertEqual(counts["review_verdict"], 1)

    def test_a_deferral_comment_is_classified_as_one_and_counts_for_nothing(self):
        body = "keel.deferral.v1\nfinding: minor-1\n\nDeferred to a follow-up issue.\n"

        self.assertEqual(evidence.marker_in_header(body), evidence.DEFERRAL_MARKER)
        self.assertEqual(
            self._counts(pr_comments=[_trusted_comment(body)]),
            {"closure_pr": 0, "closure_issue": 0, "review_verdict": 0, "jury_verdict": 0},
        )

    def test_provenance_marker_in_prose_does_not_arm_the_gate(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[
                _trusted_comment(
                    "No keel.ship-provenance.v1 comment was posted on this pull request.\n"
                )
            ],
        )

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")

    def test_review_marker_in_prose_does_not_arm_the_gate(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_reviews=[
                _trusted_comment("Please post a keel.review-verdict.v1 comment when done.\n")
            ],
        )

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")


class TestHeaderAnchoredShipAssessment(unittest.TestCase):
    """#1035: the ship-assessment heading only classifies from the header line.

    The heading is a Markdown heading rather than a versioned ``keel.*.v1`` marker,
    so it could not join ``CLASSIFICATION_MARKERS`` in #1026 and stayed a whole-body
    substring test. It is consulted as an *exclusion* by both verdict classifiers, so
    a reviewer who quoted the heading while describing what they reviewed disarmed
    their own verdict and ``evidence-verify`` reported it missing.
    """

    HEADING_IN_REVIEW_PROSE = (
        "keel.review-verdict.v1\n"
        "reviewer: alpha\n"
        "head: abc123\n"
        "\n"
        "Verdict: pass\n"
        "\n"
        "Scope reviewed: the `### \U0001f6a2 keel ship` comment claims the gates passed, "
        "but src/keel/evidence.py never re-ran them.\n"
    )

    BANNER_IN_REVIEW_PROSE = (
        "keel.review-verdict.v1\n"
        "reviewer: alpha\n"
        "head: abc123\n"
        "\n"
        "Verdict: pass\n"
        "\n"
        "Scope reviewed: the `keel ship \u2014 keel (base main)` banner in "
        "src/keel/cli.py still prints the resolved base.\n"
    )

    HEADING_IN_JURY_PROSE = (
        "keel.jury-verdict.v1\n"
        "head: abc123\n"
        "vendors: 2\n"
        "\n"
        "AI Jury verdict: LGTM.\n"
        "\n"
        "Panel checked the `### \U0001f6a2 keel ship` assessment against the ledger.\n"
    )

    BANNER_IN_JURY_PROSE = (
        "keel.jury-verdict.v1\n"
        "head: abc123\n"
        "vendors: 2\n"
        "\n"
        "AI Jury verdict: LGTM.\n"
        "\n"
        "Panel checked the `keel ship \u2014 keel (base main)` banner.\n"
    )

    ASSESSMENT = (
        "### \U0001f6a2 keel ship\n"
        "\n"
        "```\n"
        "keel ship \u2014 keel  (base main)\n"
        "  decision      : MERGE \u2014 clear to merge\n"
        "```\n"
    )

    #: A raw paste of the CLI summary, with no Markdown heading above it.
    BANNER_ASSESSMENT = "keel ship \u2014 keel  (base main)\n  decision      : MERGE\n"

    def _counts(self, *, pr_comments, reviewers=1, jury=False, no_jury=True):
        report = evidence.verify(
            _review_contract(reviewers=reviewers, jury=jury, no_jury=no_jury),
            pr_comments=pr_comments,
            issue_comments=[],
            head_sha="abc123",
        )
        return report["counts"]

    def test_review_verdict_quoting_the_assessment_heading_is_counted(self):
        counts = self._counts(pr_comments=[_trusted_comment(self.HEADING_IN_REVIEW_PROSE)])

        self.assertEqual(counts["review_verdict"], 1)

    def test_review_verdict_quoting_the_assessment_banner_is_counted(self):
        counts = self._counts(pr_comments=[_trusted_comment(self.BANNER_IN_REVIEW_PROSE)])

        self.assertEqual(counts["review_verdict"], 1)

    def test_jury_verdict_quoting_the_assessment_heading_is_counted(self):
        counts = self._counts(
            pr_comments=[_trusted_comment(self.HEADING_IN_JURY_PROSE)],
            jury=True,
            no_jury=False,
        )

        self.assertEqual(counts["jury_verdict"], 1)

    def test_jury_verdict_quoting_the_assessment_banner_is_counted(self):
        counts = self._counts(
            pr_comments=[_trusted_comment(self.BANNER_IN_JURY_PROSE)],
            jury=True,
            no_jury=False,
        )

        self.assertEqual(counts["jury_verdict"], 1)

    def test_the_gate_no_longer_reports_a_quoted_verdict_missing(self):
        # The live symptom: `missing: review-verdict-1` for a comment on the PR.
        report = evidence.verify(
            _review_contract(reviewers=1, no_jury=True),
            pr_comments=[_trusted_comment(self.HEADING_IN_REVIEW_PROSE, reviewer="agent-a")],
            head_sha="abc123",
            phase=evidence.PHASE_PRE_MERGE,
        )

        self.assertEqual(report["missing"], [])
        self.assertEqual(report["status"], evidence.STATUS_PASS)

    def test_a_real_assessment_still_arms_the_gate_and_counts_for_nothing(self):
        decision = evidence.gate_decision(
            [], "keel:ship", pr_comments=[_trusted_comment(self.ASSESSMENT)]
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-assessment-comment")
        self.assertEqual(
            self._counts(pr_comments=[_trusted_comment(self.ASSESSMENT)]),
            {"closure_pr": 0, "closure_issue": 0, "review_verdict": 0, "jury_verdict": 0},
        )

    def test_a_banner_only_assessment_still_arms_the_gate(self):
        decision = evidence.gate_decision(
            [], "keel:ship", pr_comments=[_trusted_comment(self.BANNER_ASSESSMENT)]
        )

        self.assertTrue(decision["enforced"])
        self.assertEqual(decision["reason"], "ship-assessment-comment")

    def test_the_heading_quoted_in_prose_does_not_arm_the_gate(self):
        decision = evidence.gate_decision(
            [],
            "keel:ship",
            pr_comments=[
                _trusted_comment("Please post the `### \U0001f6a2 keel ship` assessment.\n")
            ],
        )

        self.assertFalse(decision["enforced"])
        self.assertEqual(decision["reason"], "no-ship-provenance")


class TestMalformedMarkerHeader(unittest.TestCase):
    """A header naming two markers does not say which artifact it is (#1026)."""

    TWO_MARKERS = (
        "keel.review-verdict.v1 keel.jury-verdict.v1\n"
        "reviewer: alpha\n"
        "head: abc123\n"
        "\n"
        "Verdict: pass. src/keel/evidence.py reads correctly.\n"
    )

    def _report(self, comment):
        return evidence.verify(
            _review_contract(reviewers=1, no_jury=True),
            pr_comments=[comment],
            issue_comments=[],
            head_sha="abc123",
            phase=evidence.PHASE_PRE_MERGE,
        )

    def test_a_two_marker_header_counts_for_neither_artifact(self):
        report = self._report(_trusted_comment(self.TWO_MARKERS))

        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["counts"]["jury_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1"])

    def test_the_exclusion_is_reported_as_an_advisory_finding(self):
        report = self._report(_trusted_comment(self.TWO_MARKERS))

        findings = [
            finding
            for finding in report["findings"]
            if finding["id"] == evidence.MALFORMED_MARKER_FINDING
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "minor")
        self.assertEqual(findings[0]["kind"], "evidence")
        self.assertIn("keel.review-verdict.v1, keel.jury-verdict.v1", findings[0]["message"])
        # minor never blocks: the requirement it failed to satisfy fails on its own.
        self.assertEqual(report["status"], evidence.STATUS_WAITING)

    def test_a_browser_honoured_close_tag_does_not_smuggle_in_a_verdict(self):
        # `--!>` ends a comment for a browser but not for keel's literal matcher, so
        # this body renders invisibly *and* must not count. It does not.
        report = self._report(
            _trusted_comment(
                "<!-- keel.review-verdict.v1 --!>\n"
                "reviewer: a\n"
                "head: abc123\n"
                "\n"
                "src/keel/evidence.py: ok."
            )
        )

        self.assertEqual(report["counts"]["review_verdict"], 0)
        self.assertEqual(report["missing"], ["review-verdict-1"])

    def test_an_untrusted_malformed_comment_is_not_reported(self):
        report = self._report(_untrusted_comment(self.TWO_MARKERS))

        self.assertEqual(
            [f for f in report["findings"] if f["id"] == evidence.MALFORMED_MARKER_FINDING],
            [],
        )

    def test_a_well_formed_comment_is_not_reported(self):
        report = self._report(
            _trusted_comment(
                "keel.review-verdict.v1\nreviewer: a\nhead: abc123\n\nsrc/keel/evidence.py: ok."
            )
        )

        self.assertEqual(
            [f for f in report["findings"] if f["id"] == evidence.MALFORMED_MARKER_FINDING],
            [],
        )
        self.assertEqual(report["status"], evidence.STATUS_PASS)


class TestMarkerInHeader(unittest.TestCase):
    def test_bare_and_html_wrapped_markers_both_resolve(self):
        self.assertEqual(
            evidence.marker_in_header("keel.review-verdict.v1\nreviewer: a\n"),
            evidence.REVIEW_VERDICT_MARKER,
        )
        self.assertEqual(
            evidence.marker_in_header("<!-- keel.review-verdict.v1 -->\nreviewer: a\n"),
            evidence.REVIEW_VERDICT_MARKER,
        )
        self.assertEqual(
            evidence.marker_in_header(closure.COMMENT_MARKER),
            closure.CLOSURE_SCHEMA_VERSION,
        )

    def test_leading_blank_lines_are_skipped_not_treated_as_the_end(self):
        self.assertEqual(
            evidence.marker_in_header("\n\n  \nkeel.jury-verdict.v1\nhead: abc\n"),
            evidence.JURY_VERDICT_MARKER,
        )

    def test_no_marker_yields_none(self):
        self.assertIsNone(evidence.marker_in_header(""))
        self.assertIsNone(evidence.marker_in_header("\n \n"))
        self.assertIsNone(evidence.marker_in_header("Just a chat comment.\n"))
        self.assertIsNone(
            evidence.marker_in_header("Thanks!\n\nkeel.review-verdict.v1\nreviewer: a\n")
        )

    def test_two_markers_in_the_header_yield_none(self):
        self.assertIsNone(
            evidence.marker_in_header("keel.review-verdict.v1 keel.jury-verdict.v1\n")
        )

    def test_the_wrapper_is_matched_literally_not_parsed_as_html(self):
        # CodeQL py/bad-tag-filter: a regex that treats `-->` as *the* comment
        # terminator is wrong about HTML — a browser also ends a comment at `--!>`.
        # keel does not need to parse HTML; it needs to recognise the one shape
        # `closure.render_closure_comment` writes and refuse everything else, so a
        # body cannot render as an invisible comment while counting as evidence.
        for header in (
            "<!-- keel.review-verdict.v1 --!>",  # the `--!>` close a browser honours
            "<!-- keel.review-verdict.v1 --!>\nreviewer: a\nhead: abc",
            "<!--! keel.review-verdict.v1 -->",  # bogus opener
            "<!-- keel.review-verdict.v1",  # unterminated
            "keel.review-verdict.v1 -->",  # close with no opener
            "<!-- keel.review-verdict.v1 --> trailing prose",
            "<!-- keel.closure-comment.v1 --> <!-- keel.deferral.v1 -->",  # two wrappers
            "<!---->",  # empty wrapper
            "<!-->",  # overlapping delimiters
        ):
            with self.subTest(header=header):
                self.assertEqual(evidence.header_markers(header), ())
                self.assertIsNone(evidence.marker_in_header(header))

    def test_the_wrapper_keel_writes_classifies_with_or_without_inner_spaces(self):
        self.assertEqual(
            evidence.marker_in_header("<!--keel.review-verdict.v1-->"),
            evidence.REVIEW_VERDICT_MARKER,
        )
        self.assertEqual(
            evidence.marker_in_header("<!--   keel.review-verdict.v1   -->"),
            evidence.REVIEW_VERDICT_MARKER,
        )

    def test_header_markers_reports_them_in_a_stable_order(self):
        self.assertEqual(
            evidence.header_markers("keel.jury-verdict.v1 keel.review-verdict.v1\n"),
            (evidence.REVIEW_VERDICT_MARKER, evidence.JURY_VERDICT_MARKER),
        )
        self.assertEqual(evidence.header_markers("plain prose"), ())

    def test_every_renderer_emits_a_header_the_classifier_recognises(self):
        # The rule is only as good as its agreement with what keel actually posts.
        self.assertEqual(
            evidence.marker_in_header(
                artifacts.render_review_verdict(reviewer="alpha", head_sha="abc")
            ),
            evidence.REVIEW_VERDICT_MARKER,
        )
        self.assertEqual(
            evidence.marker_in_header(artifacts.render_jury_verdict(head_sha="abc")),
            evidence.JURY_VERDICT_MARKER,
        )
        self.assertEqual(
            evidence.marker_in_header(artifacts.render_ship_provenance(head_sha="abc")),
            evidence.SHIP_PROVENANCE_MARKER,
        )
        self.assertEqual(
            evidence.marker_in_header(closure.render_closure_comment({})),
            closure.CLOSURE_SCHEMA_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
