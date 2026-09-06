"""Tests for deterministic backbone step completion verification."""

import unittest

from keel import evidence, model, provenance, stepverifier


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
        self.assertFalse(handoff["provenance"]["capability_scope"]["can_expand_capabilities"])

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

        # Two refusals, not one: the handoff never claimed `review-verdict-2` either,
        # so the document and the report are each refused on their own terms (#1101).
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            report["missing"],
            ["handoff claims no evidence id: review-verdict-2", "review-verdict-2"],
        )

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


class TestHandoffSchemaIsWholeNotPrefix(unittest.TestCase):
    """#1101 — the verifier checked 2 of the 10 fields the renderer emits."""

    #: Pinned on purpose. Adding, removing or renaming a handoff field must be a
    #: deliberate edit *here* as well as in `HANDOFF_FIELDS`, so a schema change fails
    #: a test rather than silently widening what the verifier accepts.
    PUBLISHED_SCHEMA = [
        ("schema_version", "string", False),
        ("step_id", "string", False),
        ("step_name", "string", False),
        ("status", "string", False),
        ("summary", "string", False),
        ("evidence_ids", "array", False),
        ("next_step", "string", True),
        ("producer", "string", True),
        ("provenance", "object", False),
        ("rendered", "string", False),
    ]

    def test_schema_change_fails_a_test_rather_than_widening_what_passes(self):
        self.assertEqual(
            [
                (field.name, field.json_type, field.nullable)
                for field in stepverifier.HANDOFF_FIELDS
            ],
            self.PUBLISHED_SCHEMA,
        )

    def test_producer_contract_and_verifier_read_one_declaration(self):
        handoff = stepverifier.build_handoff(step_id="s4")
        published = stepverifier.contract_as_dict(_review_contract())["handoff_schema"]
        names = [field.name for field in stepverifier.HANDOFF_FIELDS]

        # The producer emits exactly the declared schema — no more, no fewer, in order.
        self.assertEqual(list(handoff), names)
        self.assertEqual(published["required_fields"], names)
        self.assertEqual([field["name"] for field in published["fields"]], names)
        self.assertEqual(
            published["fields"][6],
            {"name": "next_step", "type": "string", "nullable": True},
        )

    def test_every_rendered_backbone_handoff_still_verifies(self):
        review_contract = _review_contract(reviewers=2, jury=True)
        evidence_report = _evidence_report(
            "review-verdict-1",
            "review-verdict-2",
            "jury-verdict",
            "closure-comment-pr",
            "closure-comment-issue",
        )

        for requirement in stepverifier.step_requirements(review_contract):
            with self.subTest(step=requirement.step_id):
                report = stepverifier.verify_step_completion(
                    step_id=requirement.step_id,
                    handoff=stepverifier.build_handoff(
                        step_id=requirement.step_id,
                        summary="Step complete.",
                        evidence_ids=requirement.required_evidence,
                        next_step="the next backbone step",
                        producer="opus-implementer",
                    ),
                    evidence_report=evidence_report,
                    review_contract=review_contract,
                )

                self.assertEqual(report["missing"], [])
                self.assertEqual(report["status"], "pass")

    def test_every_declared_field_is_refused_by_name_when_absent(self):
        for field in stepverifier.HANDOFF_FIELDS:
            with self.subTest(field=field.name):
                handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
                del handoff[field.name]
                report = _verify(handoff)

                self.assertEqual(report["status"], "fail")
                self.assertIn(f"handoff field missing: {field.name}", report["missing"])

    def test_a_null_field_is_refused_by_name_unless_the_schema_allows_null(self):
        for field in stepverifier.HANDOFF_FIELDS:
            with self.subTest(field=field.name):
                handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
                handoff[field.name] = None
                report = _verify(handoff)

                if field.nullable:
                    self.assertNotIn(f"handoff field null: {field.name}", report["missing"])
                else:
                    self.assertEqual(report["status"], "fail")
                    self.assertIn(f"handoff field null: {field.name}", report["missing"])

    def test_a_field_of_the_wrong_type_is_refused_by_name(self):
        wrong = {"string": 7, "array": "review-verdict-1", "object": "tagged"}
        for field in stepverifier.HANDOFF_FIELDS:
            with self.subTest(field=field.name):
                handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
                handoff[field.name] = wrong[field.json_type]
                report = _verify(handoff)

                self.assertEqual(report["status"], "fail")
                self.assertIn(f"handoff field wrong type: {field.name}", report["missing"])

    def test_a_blank_field_is_refused_by_name(self):
        handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
        handoff["summary"] = "   "
        handoff["provenance"] = {}
        report = _verify(handoff)

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff field empty: summary", report["missing"])
        self.assertIn("handoff field empty: provenance", report["missing"])

    def test_a_handoff_naming_the_wrong_step_name_is_refused(self):
        handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
        handoff["step_name"] = "review"
        report = _verify(handoff)

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff step name mismatch", report["missing"])


class TestHandoffBodyMatchesItsFields(unittest.TestCase):
    def test_a_body_rendered_from_other_fields_is_refused(self):
        handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
        # The marker is still there — this is the substring the old check settled for.
        handoff["rendered"] = stepverifier.build_handoff(
            step_id="s4",
            summary="Something else entirely.",
        )["rendered"]
        report = _verify(handoff)

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff body does not match its own fields", report["missing"])

    def test_a_body_is_rerendered_even_when_the_evidence_list_is_unusable(self):
        handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
        handoff["evidence_ids"] = "review-verdict-1"
        report = _verify(handoff)

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff field wrong type: evidence_ids", report["missing"])


class TestHandoffClaimsItsEvidence(unittest.TestCase):
    def test_empty_evidence_ids_on_a_completed_step_is_refused(self):
        review_contract = _review_contract(reviewers=2)
        report = stepverifier.verify_step_completion(
            step_id="s7",
            handoff=stepverifier.build_handoff(step_id="s7", summary="Review complete."),
            evidence_report=_evidence_report("review-verdict-1", "review-verdict-2"),
            review_contract=review_contract,
        )

        # The evidence report is green: only the handoff's own empty claim refuses it.
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            report["missing"],
            [
                "handoff claims no evidence id: review-verdict-1",
                "handoff claims no evidence id: review-verdict-2",
            ],
        )

    def test_empty_evidence_ids_are_legitimate_where_the_contract_requires_none(self):
        report = _verify(stepverifier.build_handoff(step_id="s4", summary="Implemented."))

        self.assertEqual(report["status"], "pass")

    def test_an_incomplete_step_is_not_asked_to_claim_evidence(self):
        report = stepverifier.verify_step_completion(
            step_id="s7",
            handoff=stepverifier.build_handoff(step_id="s7", status="blocked"),
            evidence_report=_evidence_report("review-verdict-1"),
            review_contract=_review_contract(reviewers=1),
        )

        self.assertEqual(report["missing"], ["handoff not complete"])

    def test_an_unnamed_evidence_id_is_refused(self):
        handoff = stepverifier.build_handoff(step_id="s4", summary="Done.")
        handoff["evidence_ids"] = ["review-verdict-1", 7]
        report = _verify(handoff)

        self.assertEqual(report["status"], "fail")
        self.assertIn("handoff evidence ids are not all named", report["missing"])


class TestHandoffProvenance(unittest.TestCase):
    def _handoff(self, **overrides) -> dict:
        handoff = stepverifier.build_handoff(
            step_id="s4",
            summary="Done.",
            producer="opus-implementer",
        )
        handoff["provenance"].update(overrides)
        return handoff

    def test_a_foreign_provenance_schema_is_refused(self):
        report = _verify(self._handoff(schema_version="something.else.v1"))

        self.assertIn("handoff provenance schema mismatch", report["missing"])

    def test_provenance_claiming_to_be_trusted_instructions_is_refused(self):
        role = _verify(self._handoff(role="trusted-operator"))
        trusted = _verify(self._handoff(trusted_as_instructions=True))

        self.assertIn("handoff provenance is not untrusted output", role["missing"])
        self.assertIn("handoff provenance is not untrusted output", trusted["missing"])

    def test_provenance_that_expands_capabilities_is_refused(self):
        absent = _verify(self._handoff(capability_scope="everything"))
        expanding = _verify(
            self._handoff(
                capability_scope={"allowed_capabilities": [], "can_expand_capabilities": True}
            )
        )

        self.assertIn("handoff provenance expands capabilities", absent["missing"])
        self.assertIn("handoff provenance expands capabilities", expanding["missing"])

    def test_provenance_without_a_source_is_refused(self):
        report = _verify(self._handoff(source="opus-implementer"))

        self.assertIn("handoff provenance names no source", report["missing"])

    def test_provenance_tagged_for_another_step_is_refused(self):
        report = _verify(self._handoff(source={"agent_id": "opus-implementer", "step_id": "s7"}))

        self.assertIn("handoff provenance step mismatch", report["missing"])

    def test_a_tag_missing_canonical_members_is_refused_by_name(self):
        """ "Canonical" has to mean the shape `source_tag` emits.

        A tag carrying only the members this check happens to read is not the tag
        the renderer builds, and accepting it claims a check that was not made.
        The required keys are derived from `source_tag`, so a member added there
        fails this rather than going unchecked.
        """
        # Only the nested members are testable by removal: every top-level key has a
        # check of its own above, which is the point — this guard is what catches a
        # member added to `source_tag` later that nothing else reads.
        self.assertEqual(
            stepverifier._canonical_provenance_keys()[0],
            frozenset(provenance.source_tag(source_agent="a", step_id="s")),
        )
        cases = {
            "provenance.source": ("vendor", "model"),
            "provenance.capability_scope": ("allowed_capabilities", "unknown_capabilities"),
        }
        for where, dropped in cases.items():
            with self.subTest(where=where):
                handoff = self._handoff()
                tag = handoff["provenance"]
                target = tag
                for part in where.split(".")[1:]:
                    target = target[part]
                for key in dropped:
                    target.pop(key)

                report = _verify(handoff)

                detail = " ".join(report["missing"])
                self.assertIn(f"{where} missing", detail)
                for key in dropped:
                    self.assertIn(key, detail)

    def test_provenance_naming_no_agent_is_refused_even_with_no_producer(self):
        """The source has to say who, whether or not the handoff names a producer.

        ``source_tag`` always writes ``agent_id`` — ``unknown-agent`` when the caller
        names nobody — so a tag without the key did not come from the renderer. The
        check used to read it only when ``producer`` was a non-blank string, so a
        handoff with ``producer: null`` passed by answering "who says so?" with nothing.
        """
        for source in ({"step_id": "s4"}, {"step_id": "s4", "agent_id": "  "}):
            with self.subTest(source=source):
                handoff = self._handoff(source=source)
                handoff["producer"] = None

                report = _verify(handoff)

                self.assertIn("handoff provenance names no agent", report["missing"])

    def test_the_renderers_unknown_agent_default_still_verifies(self):
        """A handoff built with no producer is legitimate and must stay verifiable."""
        handoff = stepverifier.build_handoff(step_id="s4", summary="Done.", producer=None)

        self.assertEqual(handoff["provenance"]["source"]["agent_id"], "unknown-agent")
        self.assertNotIn("handoff provenance names no agent", _verify(handoff)["missing"])

    def test_provenance_naming_a_different_producer_is_refused(self):
        report = _verify(self._handoff(source={"agent_id": "someone-else", "step_id": "s4"}))

        self.assertIn("handoff provenance producer mismatch", report["missing"])


def _verify(handoff: dict | None, *, step_id: str = "s4") -> dict:
    """Verify a handoff for a step whose contract requires no public evidence."""
    return stepverifier.verify_step_completion(
        step_id=step_id,
        handoff=handoff,
        evidence_report=_evidence_report(),
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
