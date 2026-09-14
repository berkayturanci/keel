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

    def test_a_clause_is_not_a_label(self):
        """A label is a short name; a clause before a dash is still the sentence.

        The label strip exists so `Decision: this issue is out of scope` is seen. Capped
        only by length, it also cut `Users need safer sync — this issue is not in scope
        for Windows.` at the dash and read the remainder as a closure — a carve-out
        refused, #1168 inverted. The subject test cannot catch it, because the subject is
        genuinely there; it is just not where the sentence starts, which is what the
        anchor is for.
        """
        for sentence in (
            "Users need safer sync — this issue is not in scope for Windows.",
            "Users need safer sync: this issue is not in scope for Windows.",
            "The guard must ship first - this issue is not planned for 1.22.",
        ):
            with self.subTest(sentence=sentence):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        f"## Problem\n{sentence}\n\n## Deliverable\nShip the guard.\n\n"
                        "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.READY)
                self.assertTrue(record["can_mutate_code"])

    def test_the_issue_must_be_the_subject_not_a_mention(self):
        """A sentence that carves out a part is not a sentence that closes the whole.

        The body pattern was briefly unanchored, on the reasoning that naming the issue
        was enough. It is not: "A backport of this issue is out of scope" names the issue
        and refuses it, which is #1168 inverted — `/keel:ship` stops at s1 on an issue
        that is in scope and only excluded a backport. The subject has to be the issue,
        so the pattern anchors at the start of the *sentence* — which markdown does not
        move, unlike the start of a line.
        """
        for sentence in (
            "A backport of this issue is out of scope.",
            "Backporting this issue is not in scope for 1.22.",
            "Support for this issue is not planned.",
            "Any rewrite beyond this issue is out of scope.",
        ):
            with self.subTest(sentence=sentence):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        f"## Problem\nUsers need safer sync. {sentence}\n\n"
                        "## Deliverable\nShip the guard.\n\n"
                        "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.READY)
                self.assertTrue(record["can_mutate_code"])

    def test_a_label_is_stripped_in_a_heading_and_nowhere_else(self):
        """`Decision —` in a heading is a record label; in prose it is a preposition.

        The strip exists so `## Decision — this issue is out of scope; closing` reads as
        the closure it is. Applied to prose it turned carve-outs into closures:
        `For Windows: this issue is not in scope.` and `On Android — this issue is not
        planned.` each left a clean subject-form match behind, which the subject test
        cannot see through — the subject really is there, just not where the sentence
        starts.
        """
        for heading in (
            "Decision \u2014 this issue is out of scope; closing",
            "Follow-up: this issue is out of scope",
            "Update 2026-09-14: this issue is not planned",
            "Won't do: this issue is out of scope",
        ):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync", body=self.WELL_FORMED + f"\n## {heading}\n"
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)

        for sentence in (
            "For Windows: this issue is not in scope.",
            "On Android \u2014 this issue is not planned.",
            "For Linux: this issue is not planned.",
            "iOS 18: this issue is not in scope.",
        ):
            with self.subTest(sentence=sentence):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=(
                        f"## Problem\n{sentence}\n\n## Deliverable\nShip the guard.\n\n"
                        "## Acceptance criteria\n- Guard blocks unsafe sync.\n"
                    ),
                )
                self.assertEqual(record["status"], intake.READY)
                self.assertTrue(record["can_mutate_code"])

    def test_the_short_form_belongs_to_the_title_alone(self):
        """A title names the issue's whole subject; a body sentence qualifies.

        `Out of scope for v1: the Android client.` opening an issue is a boundary,
        `Not in scope for Windows.` is a carve-out, and neither closes anything — but each
        matches the short form. Fifteen review rounds tried to separate those by position
        and each rule inverted on some real sentence, so the body asks only the question
        that has an answer: is the issue the subject.

        The cost, stated rather than discovered: an issue whose body opens
        `Not planned for this release.` and says nothing else is `ready`. Writing it in
        the title, or naming the issue, still blocks.
        """
        for title in ("Out of scope: fix the matcher", "Not planned: support legacy sync"):
            with self.subTest(title=title):
                record = intake.assess_issue(title=title, body=self.WELL_FORMED)
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)

        for opening in (
            "Out of scope for v1: the Android client. This issue adds the iOS client.",
            "Not in scope for Windows.",
            "Not planned for the mobile client.",
        ):
            with self.subTest(opening=opening):
                record = intake.assess_issue(
                    title="Add safe sync", body=f"{opening}\n\n" + self.WELL_FORMED
                )
                self.assertEqual(record["status"], intake.READY)

    def test_a_heading_is_a_position_not_a_verdict(self):
        """`## Not planned` over a list of features is a boundary, not a closure.

        A heading has the same shape as an opener — one line, nothing in front — so the
        short form was briefly applied to it. That refused an issue whose `## Not planned`
        section listed features, while the identical bullets under `## Non-goals` passed:
        position, not meaning. A heading that really closes the issue says so, and the
        named form catches that wherever it sits.
        """
        for heading in ("Not planned", "Wontfix: legacy sync", "Not in scope"):
            with self.subTest(heading=heading):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## {heading}\n- Mobile UI\n- Dark mode\n",
                )
                self.assertEqual(record["status"], intake.READY)

        named = intake.assess_issue(
            title="Add safe sync",
            body=self.WELL_FORMED + "\n## Not planned\nThis issue is out of scope; closing.\n",
        )
        self.assertEqual(named["status"], intake.OUT_OF_SCOPE)

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

    def test_a_single_top_heading_is_a_title_not_a_section(self):
        """`# Out of scope` over a run of `##` sections must not swallow them.

        The skip runs until a heading of the same or shallower depth, which is right
        while levels are consistent. Issue bodies mix them: one `#` over several `##`
        makes every `##` structurally nested, so an `# Out of scope` owned the whole
        document — including the `## Decision` that closed the issue. A level used once
        is a title; the level used repeatedly is the section level, and a heading there
        always starts a new section.
        """
        record = intake.assess_issue(
            title="Add safe sync",
            body=(
                "# Out of scope\n- Mobile UI.\n\n"
                "## Decision\nThis issue is out of scope; closing.\n\n" + self.WELL_FORMED
            ),
        )
        self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
        self.assertFalse(record["can_mutate_code"])

    def test_an_exclusion_section_hides_its_own_prose_and_nothing_else(self):
        """The section's body is dropped; a nested heading is read on its own merits.

        Owning nested headings was tried and failed in both directions: `# Out of scope`
        over a run of `##` sections swallowed the `## Decision` that closed the issue, and
        every heuristic for telling a title level from a section level inverted on some
        real body. The simple rule is safe now because of the subject test — a bullet
        under a boundary section saying `Out of scope: the mobile client` names no issue
        and matches nothing, so the contents no longer need hiding, only the prose the
        section itself carries.

        The cost is stated rather than discovered: a sentence that *names the issue*
        blocks wherever it sits, a subsection of a boundary section included. Erring
        toward stopping is the right side for a gate that decides whether code may be
        mutated.
        """
        for tail, expected in (
            ("### Mobile\n- Out of scope: the mobile client.\n", intake.READY),
            ("### Mobile\n#### Later\n- Out of scope: closing.\n", intake.READY),
            ("### Mobile\n- This issue is out of scope for mobile.\n", intake.OUT_OF_SCOPE),
        ):
            with self.subTest(tail=tail[:26]):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## Out of scope\n{tail}",
                )
                self.assertEqual(record["status"], expected)

    def test_an_exclusion_heading_does_not_swallow_a_later_section_at_any_level(self):
        """Both nesting directions, since each broke a different heuristic."""
        h1 = (
            "# Problem\nUsers need safer sync.\n\n# Deliverable\nShip the guard.\n\n"
            "# Acceptance criteria\n- Guard blocks unsafe sync.\n"
        )
        for body in (
            # `#` sections, then `# Out of scope`, then a lone `##` closure.
            h1 + "\n# Out of scope\n- Mobile UI.\n\n## Decision\n"
            "This issue is out of scope; closing.\n",
            # One `#` over a run of `##` sections.
            "# Out of scope\n- Mobile UI.\n\n## Decision\n"
            "This issue is out of scope; closing.\n\n" + self.WELL_FORMED,
        ):
            with self.subTest(body=body[:30]):
                record = intake.assess_issue(title="Add safe sync", body=body)
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)
                self.assertFalse(record["can_mutate_code"])

    def test_an_unpunctuated_bullet_cannot_swallow_the_line_below_it(self):
        """`_sentences` joins lines with no terminator; a list item is its own statement.

        A plain bullet above a declaration put the declaration mid-sentence, past the
        anchor. List items are candidates in their own right — and only list items: every
        line, and block quotes, were each tried and each refused prose the moment a wrap
        put the phrase at a line start.
        """
        for body_tail in (
            "- Discussed with the team\n- This issue is out of scope; closing.\n",
            "1) Reviewed with the team\n2) This issue is out of scope.\n",
        ):
            with self.subTest(tail=body_tail[:24]):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## Decision\n{body_tail}",
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)

    def test_a_declaration_keeps_its_own_punctuation(self):
        """The label strip must not eat the declaration it is meant to uncover.

        `_LEADING_LABEL_RE` removes a short `Decision:` / `Status —` prefix so the
        subject can be seen. It cannot tell that prefix from the declaration's own
        punctuation, so `This issue is out of scope: closing.` was stripped down to
        `closing.` and the closure was lost. Every body test used a semicolon, which is
        not a terminator, so none of them saw it. The sentence is tried as written first.
        """
        for sentence in (
            "This issue is out of scope; closing.",
            "This issue is out of scope: closing.",
            "This issue is out of scope — we will not ship it.",
            "This issue is out of scope – deferred.",
            "This issue is out of scope - closing.",
        ):
            with self.subTest(sentence=sentence):
                record = intake.assess_issue(
                    title="Add safe sync",
                    body=self.WELL_FORMED + f"\n## Decision\n{sentence}\n",
                )
                self.assertEqual(record["status"], intake.OUT_OF_SCOPE)

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
