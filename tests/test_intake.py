"""Tests for issue intake readiness classification."""

import unittest

from keel import intake


class TestIssueIntake(unittest.TestCase):
    def test_ready_issue_extracts_objective_deliverable_and_acceptance(self):
        record = intake.assess_issue(
            title="Add release docs",
            body=(
                "## Problem\n"
                "Users need release behavior documented.\n\n"
                "## Proposed direction\n"
                "Document every release parameter.\n\n"
                "## Acceptance criteria\n"
                "- README explains release flags.\n"
                "- Tests cover dry-run output.\n"
            ),
            labels=("risk:docs",),
        )

        self.assertEqual(record["status"], intake.READY)
        self.assertTrue(record["can_mutate_code"])
        self.assertEqual(record["objective"], "Users need release behavior documented.")
        self.assertEqual(record["deliverable"], "Document every release parameter.")
        self.assertEqual(len(record["acceptance_criteria"]), 2)
        self.assertEqual(record["required_docs_tests"]["docs"], "required")
        self.assertEqual(record["required_docs_tests"]["tests"], "required")
        self.assertTrue(record["risk_tier_inputs"]["has_high_risk_signal"])

    def test_missing_acceptance_criteria_needs_input(self):
        record = intake.assess_issue(
            title="Add setup guide",
            body="## Problem\nUsers need setup guidance.\n\n## Deliverable\nWrite the guide.\n",
        )

        self.assertEqual(record["status"], intake.NEEDS_INPUT)
        self.assertFalse(record["can_mutate_code"])
        self.assertIn("acceptance_criteria", record["missing_info"])
        self.assertIn("What acceptance criteria define done", record["questions"][0])
        self.assertEqual(record["ledger_record"]["readiness"], intake.NEEDS_INPUT)

    def test_missing_deliverable_needs_input_even_with_acceptance(self):
        record = intake.assess_issue(
            title="Add setup guide",
            body=(
                "## Problem\nUsers need setup guidance.\n\n"
                "## Acceptance criteria\n"
                "- Guide behavior is clear.\n"
            ),
        )

        self.assertEqual(record["status"], intake.NEEDS_INPUT)
        self.assertIn("deliverable", record["missing_info"])
        self.assertFalse(record["can_mutate_code"])

    def test_bullet_objective_and_empty_bullet_are_handled(self):
        record = intake.assess_issue(
            title="Add migration note",
            body=(
                "## Problem\n"
                "- Users need a safer migration warning.\n\n"
                "## Deliverable\n"
                "- Add the warning.\n\n"
                "## Acceptance criteria\n"
                "-   \n"
                "- Warning is documented.\n"
            ),
        )

        self.assertEqual(record["objective"], "Users need a safer migration warning.")
        self.assertEqual(record["deliverable"], "Add the warning.")
        self.assertEqual(record["acceptance_criteria"], ["Warning is documented."])

    def test_ambiguous_scope_needs_input(self):
        record = intake.assess_issue(
            title="Maybe improve sync",
            body=(
                "## Problem\nSync updates can be confusing.\n\n"
                "## Deliverable\nMaybe improve the warning behavior.\n\n"
                "## Acceptance criteria\n"
                "- Existing extensions are preserved.\n"
            ),
        )

        self.assertEqual(record["status"], intake.NEEDS_INPUT)
        self.assertIn("scope_clarity", record["missing_info"])
        self.assertTrue(record["questions"])

    def test_blocked_dependency_blocks_work(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n\n"
                "Blocked by #120.\n"
            ),
        )

        self.assertEqual(record["status"], intake.BLOCKED)
        self.assertFalse(record["can_mutate_code"])
        self.assertTrue(record["blockers"])
        self.assertTrue(record["work_block_policy"]["skip_when_not_ready"])

    def test_blocked_label_without_sentence_uses_default_summary(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n"
            ),
            labels=("blocked",),
        )

        self.assertEqual(record["status"], intake.BLOCKED)
        self.assertEqual(record["blockers"], ["Declared blocked dependency."])

    def test_none_dependency_text_does_not_block_ready_issue(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n\n"
                "Dependencies: none.\n"
            ),
        )

        self.assertEqual(record["status"], intake.READY)
        self.assertEqual(record["blockers"], [])

    def test_non_blocking_dependency_sentence_does_not_hide_real_blocker(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n\n"
                "Dependencies: none. Blocked by #120.\n"
            ),
        )

        self.assertEqual(record["status"], intake.BLOCKED)
        self.assertEqual(record["blockers"], ["Blocked by #120."])

    def test_singular_and_waiting_none_dependency_text_do_not_block(self):
        for text in ("No dependency.", "Waiting on no one."):
            with self.subTest(text=text):
                record = intake.assess_issue(
                    title="Add plugin publishing",
                    body=(
                        "## Problem\nPlugins need publishing.\n\n"
                        "## Deliverable\nShip plugin publish flow.\n\n"
                        "## Acceptance criteria\n"
                        "- Publish command is documented.\n\n"
                        f"{text}\n"
                    ),
                )

                self.assertEqual(record["status"], intake.READY)

    def test_acceptance_heading_alone_does_not_require_tests(self):
        record = intake.assess_issue(
            title="Add setup guide",
            body=(
                "## Problem\nUsers need setup guidance.\n\n"
                "## Deliverable\nWrite the guide.\n\n"
                "## Acceptance criteria\n"
                "- Guide behavior is clear.\n"
            ),
        )

        self.assertEqual(record["status"], intake.READY)
        self.assertEqual(record["required_docs_tests"]["tests"], "unspecified")

    def test_out_of_scope_wins_over_other_signals(self):
        record = intake.assess_issue(
            title="Support unrelated runtime",
            body=(
                "## Problem\nSomeone requested another runtime.\n\n"
                "## Deliverable\nDo not build it.\n\n"
                "## Acceptance criteria\n"
                "- Request is marked not planned.\n"
            ),
            labels=("not-planned", "blocked"),
        )

        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertEqual(record["questions"], [])
        self.assertFalse(record["can_mutate_code"])

    WELL_FORMED = (
        "## Problem\nUsers need safer sync.\n\n"
        "## Deliverable\nImplement the guard.\n\n"
        "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
    )

    def test_scope_exclusion_heading_and_bullets_do_not_mark_issue(self):
        for heading in ("Out of scope", "Non-goals", "Not in scope", "Not in this change"):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## {heading}\n- Mobile UI changes.\n",
                )
                self.assertEqual(record["status"], intake.READY)

    def test_even_a_declaration_sentence_inside_a_scope_section_is_a_boundary(self):
        """The section is dropped whole, so its prose cannot close the issue that wrote it.

        Each heading carries a sentence that *would* match if the section were not
        removed — without that, a heading could be dropped from the set and no test
        would notice, since ordinary bullets match nothing either way.
        """
        for heading in ("Out of scope", "Non-goals", "Not in scope", "Not in this change"):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        self.WELL_FORMED + f"\n## {heading}\n"
                        "- This issue is out of scope for the mobile rewrite.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.READY)

    def test_a_declaration_outside_the_opening_sentence_still_blocks(self):
        """#1188: the scan reached only the preface's *first* sentence.

        The #1182 changelog said "title, opening paragraph, or objective"; the code read
        `_sentences(preface)[:1]`, so "Thanks. This issue is out of scope." was READY with
        `can_mutate_code: true` — s2 would mutate code on an issue declared closed.
        """
        record = intake.assess_issue(
            title="Add safe sync",
            body="Thanks for the report. This issue is out of scope.\n\n" + self.WELL_FORMED,
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertFalse(record["can_mutate_code"])

    def test_a_declaration_under_any_heading_blocks(self):
        # The allowlist was objective/problem/summary/context, so a maintainer's
        # `## Decision` — the most natural place to record a closure — did not block.
        for heading in ("Decision", "Status", "Resolution", "Update"):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        self.WELL_FORMED + f"\n## {heading}\nThis issue is out of scope; closing.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                self.assertFalse(record["can_mutate_code"])

    def test_a_close_reason_heading_is_not_a_boundary_heading(self):
        """Round-1 gate finding: dropping these silenced the statement intake reads.

        `Not planned` / `Will not do` name a *status for the issue*, not a boundary of
        the change, so their sections must be read, not dropped. With them in the
        exclusion set, an issue saying "This issue is out of scope; closing." under
        `## Not planned` came back READY with `can_mutate_code: true`.
        """
        for heading in ("Not planned", "Will not do", "Decision", "Status", "Resolution"):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        self.WELL_FORMED + f"\n## {heading}\nThis issue is out of scope; closing.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                self.assertFalse(record["can_mutate_code"])

    def test_a_heading_does_not_glue_itself_onto_the_sentence_below_it(self):
        """Round-1 gate finding: the sentence-start anchor stopped matching.

        `_sentences` joins what it is given, so scanning the remaining body as one blob
        produced `- Guard blocks unsafe sync. ## Decision Out of scope: closing.` and the
        declaration pattern's first alternative is anchored at the start of a sentence.
        The chunks are per section, without their heading lines, so the anchor holds.
        """
        record = intake.assess_issue(
            title="Add safe sync",
            body=self.WELL_FORMED + "\n## Decision\nOut of scope: closing.\n",
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)

    def test_a_declaration_written_in_the_heading_itself_blocks(self):
        """Round-2 gate finding: dropping the heading line hid the one-line form.

        Round 1 moved the heading out of the body chunk so it could not glue onto the
        sentence below it. That made a declaration written *in* the heading invisible —
        `## Decision - this issue is out of scope; closing` with nothing beneath it,
        which is the example this change's own changelog entry uses. The heading is now
        its own chunk: separate from the body, but still read.
        """
        for heading in (
            "Decision \u2014 this issue is out of scope; closing",
            "Decision - this issue is out of scope; closing",
            "Status \u2014 this issue is out of scope; closing",
            "Out of scope: closing.",
            "Won't do: this issue is out of scope",
        ):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync", body=self.WELL_FORMED + f"\n## {heading}\n"
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                self.assertFalse(record["can_mutate_code"])

    def test_a_list_marker_does_not_hide_a_declaration(self):
        """Round-3 gate finding: the sentence-start anchor met a bullet first.

        `_sentences` joins stripped lines and keeps their markdown, and the declaration
        pattern's first alternative is anchored at the start of the sentence. So
        `- Out of scope: closing.` was READY while `Out of scope: closing.` was not, and
        the tests could not see it because they only ever used the unanchored
        `this issue is ...` form as a bullet. `1. ` happened to work, but only because
        `_sentences` splits on its `.`.
        """
        for marker in ("", "- ", "* ", "+ ", "> ", "- [x] ", "- [ ] ", "1. ", "2) ", "**", "_"):
            for declaration in ("Out of scope: closing.", "Not planned: closing."):
                with self.subTest(marker=marker, declaration=declaration):
                    record = intake.assess_issue(
                        title="Add safe sync",
                        body=(self.WELL_FORMED + f"\n## Decision\n{marker}{declaration}\n"),
                    )
                    self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                    self.assertFalse(record["can_mutate_code"])

    def test_a_bare_exclusion_heading_is_still_only_a_boundary(self):
        # The exclusion test runs on the *normalised* heading, so `## Out of scope` is
        # dropped while `## Out of scope: closing.` normalises to `out of scope closing`,
        # is kept, and is read as the declaration it is (asserted above).
        record = intake.assess_issue(
            title="Add safe sync", body=self.WELL_FORMED + "\n## Out of scope\n- Mobile UI.\n"
        )
        self.assertEqual(record["status"], intake.READY)

    def test_every_exclusion_heading_survives_normalization(self):
        # `Won't do` normalises to `won t do` — an apostrophe is not alphanumeric — so a
        # set entry spelled `wont do` could never match anything. Each entry must be a
        # value `_normalize_heading` can actually produce.
        for heading in intake._SCOPE_EXCLUSION_HEADINGS:
            with self.subTest(heading=heading):
                self.assertEqual(intake._normalize_heading(heading), heading)

    def test_ordinary_prose_about_a_detail_is_not_a_declaration(self):
        # The half #1168 removed must stay removed: only a sentence declaring *the issue*
        # counts, so saying a detail is out of scope for this change reads as prose.
        record = intake.assess_issue(
            title="Add safe sync",
            body=(
                "## Problem\nFix sync. Rewriting the parser is out of scope for this change.\n\n"
                "## Deliverable\nImplement the guard.\n\n"
                "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
            ),
        )
        self.assertEqual(record["status"], intake.READY)

    def test_non_goal_names_a_boundary_not_a_closed_issue(self):
        # Deliberately not a declaration marker: "Non-goal: X" says X is not a goal of
        # this change, which is the same statement the `## Non-goals` section makes.
        record = intake.assess_issue(title="Non-goal: rewrite the parser", body=self.WELL_FORMED)
        self.assertEqual(record["status"], intake.READY)

    def test_explicit_issue_scope_declaration_is_reported_with_sentence(self):
        record = intake.assess_issue(
            title="Add safe sync",
            body=(
                "This issue is out of scope for the current release.\n\n"
                "## Deliverable\nImplement the guard.\n\n"
                "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
            ),
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertIn("This issue is out of scope", record["reason"])

    def test_not_planned_title_is_an_explicit_scope_declaration(self):
        record = intake.assess_issue(
            title="Not planned: support legacy sync",
            body=(
                "## Deliverable\nDocument the decision.\n\n"
                "## Acceptance criteria\n- Decision is recorded.\n"
            ),
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertIn("Not planned", record["reason"])

    def test_out_of_scope_label_names_the_label_in_reason(self):
        record = intake.assess_issue(
            title="Add safe sync",
            body=(
                "## Deliverable\nImplement the guard.\n\n"
                "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
            ),
            labels=("out-of-scope",),
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertIn("out-of-scope", record["reason"])

    def test_multi_word_blocker_across_newline(self):
        record = intake.assess_issue(
            title="Add plugin publishing",
            body=(
                "## Problem\nPlugins need publishing.\n\n"
                "## Deliverable\nShip plugin publish flow.\n\n"
                "## Acceptance criteria\n"
                "- Publish command is documented.\n\n"
                "This issue is blocked\n"
                "by #120.\n"
            ),
        )

        self.assertEqual(record["status"], intake.BLOCKED)
        self.assertFalse(record["can_mutate_code"])
        self.assertTrue(record["blockers"])
        self.assertTrue(record["work_block_policy"]["skip_when_not_ready"])


if __name__ == "__main__":
    unittest.main()
