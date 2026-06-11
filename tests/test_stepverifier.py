"""Tests for deterministic backbone step completion verification."""

import unittest

from keel import evidence, model, stepverifier


def _review_contract(*, reviewers: int = 2, jury: bool = False) -> dict:
    return {
        "reviewers": {"count": reviewers},
        "jury": {
            "enabled": jury,
            "mode": "gating" if jury else "off",
        },
    }


class TestStepVerificationContract(unittest.TestCase):
    def test_contract_covers_every_backbone_step(self):
        contract = stepverifier.contract_as_dict(_review_contract(reviewers=2, jury=True))

        self.assertEqual(contract["schema_version"], "keel.step-verification.v1")
        self.assertTrue(contract["no_premature_termination"])
        self.assertEqual(contract["handoff_schema"]["marker"], "<!-- keel.step-handoff.v1 -->")
        self.assertEqual(
            [step["step_id"] for step in contract["steps"]],
            list(model.step_ids()),
        )

    def test_requirements_map_review_jury_and_closure_to_backbone_steps(self):
        requirements = {
            requirement.step_id: requirement
            for requirement in stepverifier.step_requirements(
                _review_contract(reviewers=2, jury=True)
            )
        }

        self.assertEqual(
            requirements["s7"].required_evidence,
            ("review-verdict-1", "review-verdict-2"),
        )
        self.assertEqual(requirements["s8"].required_evidence, ("jury-verdict",))
        self.assertEqual(
            requirements["s12"].required_evidence,
            ("closure-comment-pr", "closure-comment-issue"),
        )
        self.assertEqual(requirements["s4"].required_evidence, ())

    def test_dry_run_disables_active_step_evidence(self):
        requirements = {
            requirement.step_id: requirement
            for requirement in stepverifier.step_requirements(
                _review_contract(reviewers=3, jury=True),
                dry_run=True,
            )
        }

        self.assertEqual(requirements["s7"].required_evidence, ())
        self.assertEqual(requirements["s8"].required_evidence, ())
        self.assertEqual(requirements["s12"].required_evidence, ())


class TestHandoffBuilder(unittest.TestCase):
    def test_build_handoff_uses_canonical_renderer(self):
        handoff = stepverifier.build_handoff(
            step_id="s7",
            summary="Review complete.",
            evidence_ids=("review-verdict-1", "review-verdict-2"),
            next_step="s8",
            producer="codex-reviewer",
        )

        self.assertEqual(handoff["schema_version"], "keel.step-handoff.v1")
        self.assertEqual(handoff["step_name"], "review")
        self.assertEqual(handoff["status"], "complete")
        self.assertEqual(handoff["producer"], "codex-reviewer")
        self.assertEqual(
            handoff["provenance"]["schema_version"],
            "keel.agent-output-provenance.v1",
        )
        self.assertFalse(handoff["provenance"]["trusted_as_instructions"])
        self.assertEqual(handoff["provenance"]["source"]["agent_id"], "codex-reviewer")
        self.assertEqual(handoff["provenance"]["source"]["step_id"], "s7")
        self.assertIn("<!-- keel.step-handoff.v1 -->", handoff["rendered"])

    def test_build_handoff_records_vendor_model_and_capability_scope(self):
        handoff = stepverifier.build_handoff(
            step_id="s7",
            producer="reviewer-a",
            vendor="openai",
            model_name="gpt-5",
            allowed_capabilities=("gh", "", "shell", "gh", "future-capability"),
        )

        self.assertEqual(handoff["provenance"]["source"]["vendor"], "openai")
        self.assertEqual(handoff["provenance"]["source"]["model"], "gpt-5")
        self.assertEqual(
            handoff["provenance"]["capability_scope"]["allowed_capabilities"],
            ["gh", "shell"],
        )
        self.assertEqual(
            handoff["provenance"]["capability_scope"]["unknown_capabilities"],
            ["future-capability"],
        )
        self.assertFalse(
            handoff["provenance"]["capability_scope"]["can_expand_capabilities"]
        )

    def test_unknown_step_fails_closed_by_raising(self):
        with self.assertRaises(KeyError):
            stepverifier.build_handoff(step_id="s404")


class TestVerifyStepCompletion(unittest.TestCase):
    def test_step_passes_when_handoff_and_required_evidence_are_complete(self):
        review_contract = _review_contract(reviewers=2)
        handoff = stepverifier.build_handoff(
            step_id="s7",
            summary="Review complete.",
            evidence_ids=("review-verdict-1", "review-verdict-2"),
        )
        report = stepverifier.verify_step_completion(
            step_id="s7",
            handoff=handoff,
            evidence_report=_evidence_report("review-verdict-1", "review-verdict-2"),
            review_contract=review_contract,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["missing"], [])

    def test_step_fails_when_required_evidence_is_missing(self):
        review_contract = _review_contract(reviewers=2)
        handoff = stepverifier.build_handoff(
            step_id="s7",
            summary="Review complete.",
            evidence_ids=("review-verdict-1",),
        )
        report = stepverifier.verify_step_completion(
            step_id="s7",
            handoff=handoff,
            evidence_report=_evidence_report("review-verdict-1"),
            review_contract=review_contract,
        )

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["missing"], ["review-verdict-2"])

    def test_step_fails_without_complete_handoff(self):
        report = stepverifier.verify_step_completion(
            step_id="s4",
            handoff=stepverifier.build_handoff(step_id="s4", status="blocked"),
            evidence_report=_evidence_report(),
            review_contract=_review_contract(),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff not complete", report["missing"])

    def test_step_fails_when_handoff_is_for_another_step(self):
        report = stepverifier.verify_step_completion(
            step_id="s8",
            handoff=stepverifier.build_handoff(step_id="s7"),
            evidence_report=_evidence_report("jury-verdict"),
            review_contract=_review_contract(jury=True),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff step mismatch", report["missing"])

    def test_step_fails_when_handoff_is_missing(self):
        report = stepverifier.verify_step_completion(
            step_id="s4",
            handoff=None,
            evidence_report=_evidence_report(),
            review_contract=_review_contract(),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff missing", report["missing"])

    def test_step_fails_when_handoff_schema_is_wrong(self):
        handoff = stepverifier.build_handoff(step_id="s4")
        handoff["schema_version"] = "future"
        report = stepverifier.verify_step_completion(
            step_id="s4",
            handoff=handoff,
            evidence_report=_evidence_report(),
            review_contract=_review_contract(),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff schema mismatch", report["missing"])

    def test_step_fails_without_canonical_handoff_marker(self):
        handoff = stepverifier.build_handoff(step_id="s4")
        handoff["rendered"] = "plain text"
        report = stepverifier.verify_step_completion(
            step_id="s4",
            handoff=handoff,
            evidence_report=_evidence_report(),
            review_contract=_review_contract(),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("canonical handoff renderer missing", report["missing"])

    def test_step_fails_closed_when_evidence_report_is_missing(self):
        report = stepverifier.verify_step_completion(
            step_id="s7",
            handoff=stepverifier.build_handoff(step_id="s7"),
            evidence_report=None,
            review_contract=_review_contract(reviewers=1),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("review-verdict-1", report["missing"])

    def test_step_fails_closed_when_evidence_results_shape_is_wrong(self):
        report = stepverifier.verify_step_completion(
            step_id="s7",
            handoff=stepverifier.build_handoff(step_id="s7"),
            evidence_report={"results": "not-a-list"},
            review_contract=_review_contract(reviewers=1),
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("review-verdict-1", report["missing"])

    def test_unknown_verification_step_fails_closed_by_raising(self):
        with self.assertRaises(KeyError):
            stepverifier.verify_step_completion(
                step_id="s404",
                handoff=None,
                evidence_report=None,
                review_contract=_review_contract(),
            )


def _evidence_report(*ok_ids: str) -> dict:
    required = {"closure-comment-pr", "closure-comment-issue", *ok_ids}
    results = [
        {
            "id": item.id,
            "kind": item.kind,
            "required": item.required,
            "present": item.id in ok_ids,
            "deferred": False,
            "ok": item.id in ok_ids,
            "reason": None,
        }
        for item in evidence.required_items(_review_contract(reviewers=2, jury=True))
        if item.id in required
    ]
    return {
        "schema_version": evidence.SCHEMA_VERSION,
        "status": "pass" if all(result["ok"] for result in results) else "fail",
        "results": results,
    }


if __name__ == "__main__":
    unittest.main()
