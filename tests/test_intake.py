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

    def test_markdown_in_front_of_a_declaration_changes_nothing(self):
        """The body form names the issue, so nothing that precedes it matters.

        Eight review rounds went into making a start-anchored form survive markdown —
        list markers, task boxes, block quotes, thematic breaks, HTML comments, images,
        emphasis runs, a heading glued on by sentence-joining, and a wrap that moved the
        phrase to a line start. Each fix broke the other direction: a closed issue let
        through, or an in-scope one refused. "Starts the sentence" is not a property
        markdown preserves, so the body no longer asks for it.
        """
        for prefix in (
            "",
            "- ",
            "* ",
            "+ ",
            "> ",
            "- [x] ",
            "- [ ] ",
            "1. ",
            "2) ",
            "**",
            "_",
            "---\n",
            "<!-- note -->\n",
            "![diagram](d.png)\n",
            "***\n",
        ):
            with self.subTest(prefix=prefix):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        self.WELL_FORMED
                        + f"\n## Decision\n{prefix}This issue is out of scope; closing.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                self.assertFalse(record["can_mutate_code"])

    def test_a_bare_scope_phrase_in_the_body_is_not_a_declaration(self):
        """`Out of scope: X` does not say whether X is the issue or a boundary.

        `Out of scope: mobile UI` and `Out of scope: closing` are the same string, and a
        `## Out of scope` section is full of the first kind. Only naming the issue
        distinguishes them, so the body requires that. The title keeps the short form —
        one line, nothing in front of it (asserted in the title tests).
        """
        record = intake.assess_issue(
            title="Add safe sync",
            body=self.WELL_FORMED + "\n## Decision\nOut of scope: closing.\n",
        )
        self.assertEqual(record["status"], intake.READY)

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
            "Won't do: this issue is out of scope",
            "Resolution: the issue is not planned",
        ):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync", body=self.WELL_FORMED + f"\n## {heading}\n"
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                self.assertFalse(record["can_mutate_code"])

    def test_a_qualified_exclusion_heading_is_still_only_a_boundary(self):
        """`## Out of scope for v1` is the same kind of section as `## Out of scope`.

        The exclusion test is *starts with*, not equals. An equality test refused the
        issues that qualified their heading — #1168 coming back through the heading text,
        which this change reads as prose. `## Out of scope: closing.` is in this list
        deliberately: it has the shape of a qualified boundary heading and no way to tell
        it from `## Out of scope: mobile UI`, so it is dropped as a boundary rather than
        read as a declaration. A close that
        needs to be seen goes in the section body, or under a heading that is not an
        exclusion — both of which block (asserted above).
        """
        for heading in (
            "Out of scope",
            "Out of scope for v1",
            "Out of scope: mobile UI",
            "Out of scope for this change",
            "Out of scope: closing.",
            "Non-goals for now",
            "Not in scope for this release",
        ):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## {heading}\n- Mobile UI.\n",
                )
                self.assertEqual(record["status"], intake.READY)

    def test_a_wrapped_block_quote_is_still_a_wrap(self):
        """Markdown prefixes every continuation line of a quote with `>`.

        Treating each `>` line as its own statement made a wrapped quote a wrap wearing a
        marker, so prose inside a quote was refused — the same inverse defect as a bare
        wrap, one round later. A one-line quoted declaration is still caught, through the
        sentence path and the leading-markup strip (asserted below).
        """
        record = intake.assess_issue(
            title="Add safe sync",
            body=(
                self.WELL_FORMED
                + "\n## Notes\n> The parser rewrite is\n> out of scope for this change.\n"
            ),
        )
        self.assertEqual(record["status"], intake.READY)

    def test_an_exclusion_section_owns_its_nested_headings(self):
        """`## Out of scope` followed by `### Mobile` is one boundary, not two sections.

        Section chunks end at the *next heading of any level*, so skipping only the
        heading that matched let the sub-section and its bullets back in — and refused
        the issue again, which is #1168 for the third time.
        """
        for tail in (
            "### Mobile\n- Out of scope: the mobile client.\n",
            "### Mobile\n- This issue is out of scope for mobile.\n",
            "### Mobile\n#### Later\n- Out of scope: closing.\n",
        ):
            with self.subTest(tail=tail[:24]):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## Out of scope\n{tail}",
                )
                self.assertEqual(record["status"], intake.READY)

    def test_a_sibling_section_after_an_exclusion_is_read_again(self):
        # The skip ends at the next heading of the same or shallower level; it does not
        # swallow the rest of the document.
        record = intake.assess_issue(
            title="Add safe sync",
            body=(
                self.WELL_FORMED + "\n## Out of scope\n- Mobile UI.\n\n## Decision\n"
                "This issue is out of scope; closing.\n"
            ),
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)

    def test_a_wrapped_line_does_not_become_a_declaration(self):
        """The inverse of #1188, and the cost of the previous round's fix.

        Treating every line as a candidate re-anchors the pattern at each wrap, so prose
        that merely mentions scope is refused the moment a line happens to break before
        it. Only lines that stand on their own — list items and quotes — are candidates,
        and a wrap carries no marker.
        """
        for body in (
            "## Problem\nUsers need safer sync. The parser rewrite is\n"
            "out of scope for this change.\n\n## Deliverable\nShip the guard.\n\n"
            "## Acceptance criteria\n- Guard blocks unsafe sync.\n",
            "## Problem\nUsers need safer sync.\n\n## Deliverable\n"
            "- Rewriting the parser is\n  out of scope for this change.\n\n"
            "## Acceptance criteria\n- Guard blocks unsafe sync.\n",
        ):
            with self.subTest(body=body[:40]):
                record = intake.assess_issue(title="Add safe sync", body=body)
                self.assertEqual(record["status"], intake.READY)
                self.assertTrue(record["can_mutate_code"])

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
