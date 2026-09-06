"""A verdict must engage with the diff, checked mechanically (#926).

The evidence gate verified that verdicts *exist* with the right marker, head SHA
and distinct reviewer ids — never that any of them looked at anything. The record
showed what that permits: across 41 PRs merged in one week, 34 carried verdicts,
three reviewers posted exactly 25 each, **75 of 75 passed**, all opening
`Reviewed <PR title>: <affirmation>`, and 37 of 41 PRs were single-commit — no
review led to a change.

The three verdicts below are quoted verbatim from that record. They are the
calibration: a check that does not refuse these refuses nothing.
"""

from __future__ import annotations

import unittest

from keel import artifacts, evidence

TITLE = "sec(config): sensitive credential block in api_key_env"
HEADER = "keel.review-verdict.v1\nreviewer: r\nhead: abc123\n\n"

#: Verbatim from the record in #926.
OBSERVED = (
    "Reviewed sec(config): sensitive credential block in api_key_env. "
    "Implementation is robust, well-bounded, and maintains 100% test coverage.",
    "Reviewed sec(config): sensitive credential block in api_key_env. "
    "Correctly fails closed on system credentials and protects against "
    "credential exfiltration.",
    "Reviewed sec(config): sensitive credential protection in api_key_env. "
    "Implementation correctly validates blocked credentials and secures the "
    "configuration contract.",
)


class TheObservedNonReviewsAreRefused(unittest.TestCase):
    def test_each_one(self):
        for prose in OBSERVED:
            with self.subTest(prose=prose[:48]):
                ok, reason = evidence.verdict_substance(HEADER + prose, pr_title=TITLE)
                self.assertFalse(ok)
                self.assertTrue(reason)

    def test_an_empty_body_is_refused_with_its_own_reason(self):
        ok, reason = evidence.verdict_substance(HEADER, pr_title=TITLE)

        self.assertFalse(ok)
        self.assertIn("no prose", reason)


class ARealReviewPasses(unittest.TestCase):
    """The counterweights. A check that also refuses these is worse than none."""

    def test_a_path_and_line(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "src/keel/config.py:557 checks the denylist before the allowlist.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_a_bare_path(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "The new branch in src/keel/evidence.py is unreachable from the CLI.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_a_backticked_symbol(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "`_is_allowed_api_key_env` runs before the header is built.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_a_called_identifier(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "config.endpoint_issues() is asked before any request is made.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_a_dotted_identifier_does_not_need_parentheses(self):
        """Reviewers name symbols they are not calling without ``()`` (#1106)."""
        ok, _ = evidence.verdict_substance(
            HEADER + "cache.cache_key is the run identity, not a call site.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_an_extensionless_path_is_an_anchor(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "The release note in docs/CHANGELOG names the three issues.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_a_dot_directory_path_is_an_anchor(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "The workflow under .github/workflows is the only deploy path.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_several_bare_identifiers_are_an_anchor(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "TheHintsBlockIsPartOfTheKey and PromptModeIsPartOfTheRunIdentity "
            "pin the identity split.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_one_bare_identifier_is_not_enough(self):
        """#926 stamps already contain ``api_key_env``. One name is cheap talk."""
        ok, reason = evidence.verdict_substance(
            HEADER + "Implementation is robust and well bounded around api_key_env.",
            pr_title="chore: unrelated",
        )
        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_short_snake_case_pairs_do_not_count(self):
        """``a_b`` matches the identifier shape but is below the length floor."""
        ok, reason = evidence.verdict_substance(
            HEADER + "a_b and id_x are mentioned only as names.",
            pr_title="chore: unrelated",
        )
        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_a_genuinely_clean_review_stays_expressible(self):
        """The escape hatch the issue insists on.

        A review that found nothing is a real outcome. Forcing it to invent a
        file reference would make the check worse than nothing — it would train
        reviewers to paste a path.
        """
        ok, _ = evidence.verdict_substance(
            HEADER + "Checked the denylist ordering, the prefix match and the "
            "case-insensitivity, and found nothing.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_other_review_verbs_are_the_same_escape(self):
        for verb in ("Traced", "Read", "Ran", "Inspected", "Verified"):
            with self.subTest(verb=verb):
                ok, _ = evidence.verdict_substance(
                    HEADER + f"{verb} the denylist ordering and the prefix match.",
                    pr_title=TITLE,
                )
                self.assertTrue(ok)


class TheNineTwoSixRubberStampStillFails(unittest.TestCase):
    """Widening the anchors must not let the #926 receipt through.

    The observed shape is ``Reviewed <PR title>: <generic affirmation>``.
    It has no identifiers, no path, and opens with ``Reviewed``, which is
    deliberately not a review verb. ``_VERDICT_NOVELTY_FLOOR`` would catch
    the title-restated case on its own; this test uses an unrelated title
    so only the anchor set is under examination.
    """

    def test_reviewed_title_generic_affirmation(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "Reviewed fix(evidence): widen verdict anchors: "
            "looks correct and complete.",
            pr_title="chore: unrelated",
        )
        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_reviewed_is_not_a_review_verb(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "Reviewed the change and found it sound.",
            pr_title="chore: unrelated",
        )
        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_latin_abbreviations_are_not_dotted_identifiers(self):
        for prose in (
            "Looks good, e.g. the approach is sound.",
            "The idea is fine, i.e. nothing further to add.",
        ):
            with self.subTest(prose=prose):
                ok, reason = evidence.verdict_substance(
                    HEADER + prose, pr_title="chore: unrelated"
                )
                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)

    def test_short_slash_pairs_are_not_paths(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "The outcome is pass/fail and that is enough.",
            pr_title="chore: unrelated",
        )
        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)


class TheAiJurySevenFiveThreeGateVerdictPasses(unittest.TestCase):
    """The false reject that opened #1106, quoted from ai-jury#753.

    Three unique verdicts sat on that PR. The lead and the agy gate already
    passed (backticks / ``src/ai_jury/cache.py``). The grok gate named
    eleven symbols, four call sites and three test classes, opened with
    ``Traced``, and was refused because none of that matched the old
    punctuation. All three must pass; the #926 stamps above must not.
    """

    TITLE = (
        "fix(config): put the hints block, prompt_mode and the routing "
        "vocabulary in the run identity"
    )
    GROK_GATE = (
        "Scope reviewed: Traced #745/#746/#747 from the PR claims through "
        "cache.cache_key, cli hints_block wiring (collect_static_hints → "
        "cache_key and both run_jury/review_diff), orchestrator.run_jury "
        "(context-mode filter then join), config.config_hash / "
        "validate_config / JuryConfig.__post_init__ / _from_dict, "
        "adapters.GenericCLIAdapter._prompt_mode/build_argv/_stdin_for, "
        "load_config callers (cli main, jury config, run-agent, doctor), "
        "and docs/CHANGELOG. Ran unittest on TheHintsBlockIsPartOfTheKey, "
        "PromptModeIsPartOfTheRunIdentity, RoutingValidation, plus "
        "DocumentedExamplesAreStrictClean, test_cache, test_tiered_routing, "
        "test_metadata, test_cli_contract."
    )
    FABLE_LEAD = (
        "Scope reviewed: Read the diff across all eight files. #745: "
        "cache_key gains a keyword-only `hints` and folds hints_sha256 "
        "into the payload only when the block is non-empty."
    )
    AGY_GATE = (
        "Scope reviewed: Reviewed PR #753 diff and PR description against "
        "main across all changed files: src/ai_jury/cache.py (cache_key), "
        "src/ai_jury/cli.py (main), src/ai_jury/config.py."
    )

    def test_the_refused_gate_verdict_now_passes(self):
        ok, reason = evidence.verdict_substance(
            HEADER + self.GROK_GATE, pr_title=self.TITLE
        )
        self.assertTrue(ok, reason)

    def test_the_sibling_verdicts_on_that_pr_still_pass(self):
        for prose in (self.FABLE_LEAD, self.AGY_GATE):
            with self.subTest(prose=prose[:40]):
                ok, reason = evidence.verdict_substance(
                    HEADER + prose, pr_title=self.TITLE
                )
                self.assertTrue(ok, reason)

    def test_the_nine_two_six_stamps_stay_refused_against_this_title(self):
        for prose in OBSERVED:
            with self.subTest(prose=prose[:48]):
                ok, reason = evidence.verdict_substance(
                    HEADER + prose, pr_title=self.TITLE
                )
                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)


class TitleRestatementIsRefusedEvenWhenItLooksAnchored(unittest.TestCase):
    """The second half. A title containing a path would otherwise pass the
    anchor test while saying nothing — the anchor check alone is not enough."""

    def test_prose_that_is_the_title_again(self):
        title = "fix(evidence): src/keel/evidence.py header parsing"
        ok, reason = evidence.verdict_substance(
            HEADER + "Reviewed fix(evidence): src/keel/evidence.py header parsing.",
            pr_title=title,
        )

        self.assertFalse(ok)
        self.assertIn("restated", reason)

    def test_the_same_prose_passes_when_it_is_not_the_title(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "Reviewed fix(evidence): src/keel/evidence.py header parsing.",
            pr_title="chore: unrelated",
        )
        self.assertTrue(ok)

    def test_no_title_falls_back_to_the_anchor_check_alone(self):
        # Fixture-driven runs supply no title; the load-bearing half still applies.
        ok, _ = evidence.verdict_substance(HEADER + OBSERVED[0], pr_title="")
        self.assertFalse(ok)


class TheCanonicalTemplateDoesNotSatisfyTheGateByDefault(unittest.TestCase):
    """The finding behind the finding: keel's own renderer produced the receipt.

    `render_review_verdict` defaults to "Full changed-file diff and relevant
    contracts" and "Findings: none", which names nothing — so the template every
    reviewer was handed was the shape #926 is about.
    """

    def test_the_defaults_are_refused(self):
        body = artifacts.render_review_verdict(reviewer="r", head_sha="abc123")

        ok, reason = evidence.verdict_substance(body, pr_title=TITLE)

        self.assertFalse(ok, "the default template should not satisfy the gate")
        self.assertIn("nothing concrete", reason)

    def test_a_named_scope_is_enough(self):
        body = artifacts.render_review_verdict(
            reviewer="r",
            head_sha="abc123",
            scope="src/keel/config.py and tests/test_config.py",
        )

        ok, _ = evidence.verdict_substance(body, pr_title=TITLE)

        self.assertTrue(ok)

    def test_a_real_finding_is_enough(self):
        body = artifacts.render_review_verdict(
            reviewer="r",
            head_sha="abc123",
            findings=[{"severity": "minor", "message": "`_as_ip` returns None for names"}],
        )

        ok, _ = evidence.verdict_substance(body, pr_title=TITLE)

        self.assertTrue(ok)


class TheGateHoldsWithAReason(unittest.TestCase):
    """#926: report the rejection as a hold with a reason, not a silent pass —
    and not a silent *drop* either, which would read as "missing"."""

    @staticmethod
    def _comment(body: str) -> dict:
        return {"body": body, "author_association": "OWNER"}

    def _verify(self, bodies, *, title=TITLE):
        from keel import ship

        contract = ship.resolve_review_contract(tier=1)
        return evidence.verify(
            contract,
            pr_comments=[self._comment(b) for b in bodies],
            head_sha="abc123",
            pr_title=title,
            phase=evidence.PHASE_PRE_MERGE,
        )

    def test_an_insubstantial_verdict_is_named_in_the_findings(self):
        report = self._verify([HEADER + OBSERVED[0]])

        ids = [f["id"] for f in report["findings"]]
        self.assertIn("review-verdict-insubstantial", ids)
        message = next(
            f["message"] for f in report["findings"] if f["id"] == "review-verdict-insubstantial"
        )
        self.assertIn("reviewer:r", message, "the finding should name the reviewer")

    def test_a_substantial_verdict_produces_no_finding(self):
        report = self._verify([HEADER + "src/keel/config.py:557 is checked first."])

        ids = [f["id"] for f in report["findings"]]
        self.assertNotIn("review-verdict-insubstantial", ids)

    def test_a_reviewer_who_replaces_a_thin_verdict_is_accepted(self):
        """Correcting yourself must be possible.

        The same reviewer posting a real verdict after a thin one should be
        counted and not held — otherwise the only way out of a bad first comment
        is a new identity.
        """
        report = self._verify(
            [
                HEADER + OBSERVED[0],
                HEADER + "src/keel/config.py:557 checks the denylist first.",
            ]
        )

        ids = [f["id"] for f in report["findings"]]
        self.assertNotIn("review-verdict-insubstantial", ids)


if __name__ == "__main__":
    unittest.main()
