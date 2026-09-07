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


#: The scope line of the gate verdict on berkayturanci/ai-jury#753, verbatim, that
#: the rule refused for punctuation (#1106). Eleven symbols, four call sites and
#: three test classes, and the message was that it "names nothing concrete".
AI_JURY_753 = (
    "Traced #745/#746/#747 from the PR claims through cache.cache_key, cli hints_block "
    "wiring (collect_static_hints → cache_key and both run_jury/review_diff), "
    "orchestrator.run_jury (context-mode filter then join), config.config_hash / "
    "validate_config / JuryConfig.__post_init__ / _from_dict, "
    "adapters.GenericCLIAdapter._prompt_mode/build_argv/_stdin_for, load_config callers "
    "(cli main, jury config, run-agent, doctor), and docs/CHANGELOG. Ran unittest on "
    "TheHintsBlockIsPartOfTheKey, PromptModeIsPartOfTheRunIdentity, RoutingValidation."
)


class TheAnchorSetReadsHowReviewersActuallyWrite(unittest.TestCase):
    """#1106: the shapes a genuine review used, refused over punctuation.

    Every anchor missed :data:`AI_JURY_753` for the same reason — the reviewer
    wrote ``cache.cache_key``, not ``cache.cache_key()`` and not
    ``` `cache.cache_key` ```. A gate that refuses a real review for its
    punctuation teaches the operator to reword verdicts until the checker is
    happy, which is the receipt-shaped behaviour #926 set out to stop.
    """

    def test_the_refused_verdict_from_the_issue_is_accepted(self):
        ok, _ = evidence.verdict_substance(HEADER + AI_JURY_753, pr_title=TITLE)

        self.assertTrue(ok)

    def test_a_dotted_identifier_without_parentheses(self):
        # Adding parentheses to something you are not calling is worse prose.
        ok, _ = evidence.verdict_substance(
            HEADER + "The payload folds cache.cache_key in only when "
            "collect_static_hints returned a block.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_a_class_and_its_method_corroborates(self):
        """``Config.reload`` reads, but not alone — ``GitHub.com`` is its shape.

        A bare dotted token is a token with a dot in it. Backticks, a directory
        separator, a ``:42`` or a ``()`` are the author pointing at something; a
        capital letter is not, which is why this form asks for a second token or
        a mark.
        """
        alone, reason = evidence.verdict_substance(
            HEADER + "Config.reload picks the value up on the next run.",
            pr_title=TITLE,
        )
        self.assertFalse(alone)
        self.assertIn("nothing concrete", reason)

        corroborated, _ = evidence.verdict_substance(
            HEADER + "Config.reload picks the value up before validate_config runs.",
            pr_title=TITLE,
        )
        self.assertTrue(corroborated)

        backticked, _ = evidence.verdict_substance(
            HEADER + "`Config.reload` picks the value up on the next run.",
            pr_title=TITLE,
        )
        self.assertTrue(backticked)

    def test_an_abbreviation_is_not_a_dotted_identifier(self):
        """Both sides of the dot need two characters, or every "e.g." anchors."""
        ok, reason = evidence.verdict_substance(
            HEADER + "The change is fine, e.g. the ordering holds and i.e. nothing else moves",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_several_distinct_bare_identifiers(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "collect_static_hints feeds cache_key, and run_jury joins the block "
            "after the diff-only filter",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_two_are_enough_and_a_private_name_counts(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "The api_key_env handling matches what _is_allowed already did",
            pr_title="chore: unrelated",
        )
        self.assertTrue(ok)

    def test_a_bare_source_file_is_read_without_its_directory(self):
        """``evidence.py`` is a file the path anchors never saw — but it corroborates.

        One filename in a sentence of prose is ``Node.js``. Two of them, or one
        inside a clause that says an act of review happened, is a review.
        """
        alone, reason = evidence.verdict_substance(
            HEADER + "The new branch in evidence.py is unreachable from the CLI.",
            pr_title="chore: unrelated",
        )
        self.assertFalse(alone)
        self.assertIn("nothing concrete", reason)

        two_files, _ = evidence.verdict_substance(
            HEADER + "The new branch in evidence.py is unreachable from the CLI, "
            "and contracts.py never reaches it either.",
            pr_title="chore: unrelated",
        )
        self.assertTrue(two_files)

        inside_a_clause, _ = evidence.verdict_substance(
            HEADER + "Read evidence.py end to end and found nothing.",
            pr_title="chore: unrelated",
        )
        self.assertTrue(inside_a_clause)

    def test_camel_case_class_names_need_a_dot_or_backticks(self):
        """Bare CamelCase is a product name as often as it is a class.

        ``TheHintsBlockIsPartOfTheKey`` is a real test class and this refuses it,
        which is the price of refusing "GitHub". A reviewer naming a class the
        way a reviewer normally does — with a dot or in backticks — still reads.
        """
        bare, _ = evidence.verdict_substance(
            HEADER + "TheHintsBlockIsPartOfTheKey and RoutingValidation both pin the frozen key",
            pr_title="chore: unrelated",
        )
        self.assertFalse(bare)

        marked, _ = evidence.verdict_substance(
            HEADER + "`TheHintsBlockIsPartOfTheKey` and tests.RoutingValidation pin the key",
            pr_title="chore: unrelated",
        )
        self.assertTrue(marked)

    def test_one_bare_identifier_is_not_enough(self):
        """The floor is what makes a bare identifier safe to read at all.

        A single one is as likely to be prose as a symbol, and one occurrence
        would have admitted 18 of the 75 rubber stamps in the record.
        """
        ok, reason = evidence.verdict_substance(
            HEADER + "The api_key_env handling is correct and the tests all cover it",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_a_domain_word_shaped_like_camel_case_is_not_enough_on_its_own(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "The GitHub side of this is unchanged and the behaviour is the same",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)


class ADotIsNotEnoughToMakeASymbol(unittest.TestCase):
    """A hostname is spelled like ``module.symbol`` and is not one.

    The dotted anchor read any two-and-two token, so ``github.com`` and
    ``pypi.org`` anchored a verdict — and in the corpus the commonest one was
    the ``claude.ai`` in a "Generated by Claude Code" footer, which let eight
    verdicts that pointed at nothing pass on their own signature. The token now
    has to carry a mark prose does not use, or name a kind of file.
    """

    def test_a_hostname_in_a_link_is_not_a_symbol(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "Looked at the change; see "
            "https://github.com/berkayturanci/keel/pull/1 for context and it "
            "all seems right to me.",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_bare_hostnames_are_not_symbols(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "The release notes point at github.com and the package "
            "lands on pypi.org, so this all seems right.",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_a_tool_footer_does_not_anchor_the_verdict_it_signs(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "Looks good to me, nothing further.\n"
            "_Generated by [Claude Code](https://claude.ai/code)_",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_the_marked_forms_still_read(self):
        """Each marked token corroborates; a review that walked the change has two."""
        for prose in (
            "cache.cache_key folds the block in only when collect_static_hints ran",
            "JuryConfig.__post_init__ raises before validate_config builds the adapter",
            "subprocess.DEVNULL is passed for stdin now and _stdin_for stops guessing",
            "The rewrite touches CHANGELOG.md and evidence.py and nothing else",
        ):
            with self.subTest(prose=prose[:40]):
                ok, _ = evidence.verdict_substance(HEADER + prose, pr_title="chore: unrelated")
                self.assertTrue(ok)


class TwoProductNamesAreNotTwoIdentifiers(unittest.TestCase):
    """The bare-identifier floor counted CamelCase, and product names are CamelCase.

    "GitHub" and "GitLab" are shaped exactly like "JuryConfig", so two of them
    in a sentence cleared a floor of two while naming nothing in the change. No
    pattern separates the two, and a list of product names to refuse would need
    a new entry for every product a reviewer mentions — so the identifier shape
    now rests on the underscore, which is a mark prose does not use.
    """

    def test_two_product_names_do_not_anchor(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "The GitHub and GitLab side of this is unchanged and the rest looks fine.",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_two_underscored_identifiers_still_anchor(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "The api_key_env handling matches what _is_allowed already did",
            pr_title="chore: unrelated",
        )

        self.assertTrue(ok)

    def test_bare_camel_case_is_not_read_as_an_identifier(self):
        """The shipped rule refuses ``TheHintsBlockIsPartOfTheKey``, alone or paired.

        The first draft of #1106 read CamelCase and its pull request body said
        so; the corpus took it back out, because a floor of two then admitted
        "The GitHub and GitLab side of this is unchanged". This pins the shipped
        behaviour against the description, so the claim cannot come back.
        """
        for prose in (
            "TheHintsBlockIsPartOfTheKey is part of the frozen key",
            "TheHintsBlockIsPartOfTheKey and RoutingValidation both pin the frozen key",
        ):
            with self.subTest(prose=prose[:40]):
                ok, reason = evidence.verdict_substance(HEADER + prose, pr_title="chore: unrelated")

                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)

    def test_wrapping_underscores_are_not_a_joining_underscore(self):
        """Two alphanumeric segments are the rule; wrapping underscores are not.

        The comment on ``_VERDICT_BARE_IDENTIFIER`` offered ``_private`` and
        ``__dunder__`` as things it matches. Neither has a second segment, so
        neither ever matched; what matches in ``__post_init__`` is ``post_init``.
        """
        ok, reason = evidence.verdict_substance(
            HEADER + "The _private flag and the __dunder__ hook are both unchanged here",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

        joined, _ = evidence.verdict_substance(
            HEADER + "The __post_init__ hook and the _is_allowed guard are both unchanged",
            pr_title="chore: unrelated",
        )

        self.assertTrue(joined)


class ABareDottedTokenCorroboratesRatherThanAnchors(unittest.TestCase):
    """Round three of "the widening admits a non-symbol", answered by shape.

    Twice the fix was a finer character class, and twice the next token that
    class admits turned up: ``claude.ai`` in a tool footer, then ``GitHub`` as
    bare CamelCase, then ``Node.js`` and ``GitHub.com``. That sequence does not
    end, because ``Node.js`` and ``evidence.py`` are one shape and so are
    ``GitHub.com`` and ``Config.parse`` — and a blocklist of products or TLDs
    cannot be finished, because anyone can register the next one.

    So the count carries it instead of the spelling. Prose mentions a product
    once in passing; a review that walked the change names more than one thing,
    or names one inside a clause that says it looked. The forms whose
    *punctuation* is the author pointing — backticks, a directory separator, a
    ``:42``, a ``()`` — still anchor on their own.
    """

    def test_a_js_product_name_is_not_a_source_file(self):
        for prose in (
            "The Node.js side of this is unchanged and everything looks fine.",
            "The Next.js side of this is unchanged and everything looks fine.",
            "The Vue.js side of this is unchanged and everything looks fine.",
            "The D3.js side of this is unchanged and everything looks fine.",
        ):
            with self.subTest(prose=prose[:24]):
                ok, reason = evidence.verdict_substance(HEADER + prose, pr_title="chore: unrelated")

                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)

    def test_a_capitalised_hostname_is_not_a_dotted_symbol(self):
        """``[A-Z][a-z]`` made any capitalised first segment a symbol."""
        for prose in (
            "See GitHub.com for context; the rest looks fine to me.",
            "See GitLab.com for context; the rest looks fine to me.",
            "See OpenAI.com for context; the rest looks fine to me.",
            "See SourceForge.net for context; the rest looks fine to me.",
        ):
            with self.subTest(prose=prose[:24]):
                ok, reason = evidence.verdict_substance(HEADER + prose, pr_title="chore: unrelated")

                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)

    def test_a_real_javascript_file_still_reads_beside_a_second_token(self):
        """``.js`` stays in the extension list — it is a real source extension.

        Dropping it to refuse four product names would refuse the reviews in
        the record that name ``content.js`` and ``board.html``.
        """
        ok, _ = evidence.verdict_substance(
            HEADER + "content.js drops the paragraph and render_board.js is untouched.",
            pr_title="chore: unrelated",
        )

        self.assertTrue(ok)

    def test_one_written_token_is_one_piece_of_evidence(self):
        """``cache.cache_key`` is read by two patterns and still counts once.

        Counting the whole token and its second half separately would let a
        single token clear a floor that exists to require two.
        """
        self.assertEqual(
            evidence._verdict_corroborators("cache.cache_key folds the block in"),
            {"cache.cache_key"},
        )
        self.assertEqual(
            evidence._verdict_corroborators("evidence.py, evidence.py and evidence.py"),
            {"evidence.py"},
        )

        ok, reason = evidence.verdict_substance(
            HEADER + "cache.cache_key folds the block in when it is non-empty.",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)


class AnExtensionlessPathIsNotAnAnchor(unittest.TestCase):
    """The one proposal in #1106 the corpus refuted, recorded so it stays refused.

    A bare ``word/word`` token was meant to read ``docs/CHANGELOG`` and
    ``.github/workflows``. Replayed over every verdict posted across keel and
    ai-jury it read ``1/3``, ``2/3``, ``I/O``, ``offline/live`` and
    ``build/lint/bandit`` — slash-separated alternatives are simply how these
    reviewers write — matching 305 of the 507 refused verdicts on prose that
    points at nothing. A stricter two-slash variant fared no better in kind
    (``off/advisory/gating``, ``test/lint/coverage``) and reached only nine
    verdicts no other anchor did. ``docs/CHANGELOG`` in the verdict this issue
    is about is carried by the dotted and bare-identifier anchors anyway.
    """

    def test_slashed_words_do_not_anchor(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "The offline/live split is unchanged and seat 1/3 agreed with it",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)


class TheEscapeClauseNamesAnActOfReview(unittest.TestCase):
    """ "Checked" alone was one verb's worth of vocabulary (#1106)."""

    def test_every_added_verb_opens_a_clean_review_that_names_what_it_read(self):
        for verb in ("Traced", "Read", "Ran", "Inspected", "Verified"):
            with self.subTest(verb=verb):
                ok, _ = evidence.verdict_substance(
                    HEADER + f"{verb} the cache_key ordering and the prefix match, "
                    "and found nothing.",
                    pr_title=TITLE,
                )
                self.assertTrue(ok)

    def test_an_added_verb_needs_an_object_that_names_something(self):
        """The #926 receipt reopened under a synonym (#1106 review).

        ``test_reviewed_is_not_one_of_the_verbs`` pinned that "Reviewed the whole
        diff and everything looks correct here" is refused. Swapping the first
        word for one of the five verbs this rule added made the same sentence
        pass, because the clause asked for a verb and eight characters and never
        for an object. It asks for the object now.
        """
        for verb in ("Read", "Traced", "Ran", "Inspected", "Verified"):
            with self.subTest(verb=verb):
                ok, reason = evidence.verdict_substance(
                    HEADER + f"{verb} the whole diff and everything looks correct here",
                    pr_title="chore: unrelated",
                )

                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)

    def test_an_added_verb_reads_a_path_as_its_object(self):
        ok, _ = evidence.verdict_substance(
            HEADER + "Read src/keel/evidence.py end to end and found nothing.",
            pr_title="chore: unrelated",
        )
        self.assertTrue(ok)

    def test_checked_keeps_a_free_form_object(self):
        """The incumbent verb is calibrated; the five added ones are not.

        Holding "checked" to the same requirement refuses 13 real reviews in the
        corpus — "Checked the formula syntax, the version URL and the checksum
        placeholder" names three things in English and no symbol. That latitude
        is what the record supports for this verb and only this verb.
        """
        ok, _ = evidence.verdict_substance(
            HEADER + "Checked the ordering, the prefix match and the case handling.",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

    def test_the_object_may_sit_on_the_next_line_as_a_bullet(self):
        """ "Checked:" with the list under it is the same clause, laid out."""
        ok, _ = evidence.verdict_substance(
            HEADER + "Checked:\n- the denylist ordering, and found nothing",
            pr_title=TITLE,
        )
        self.assertTrue(ok)

        added, _ = evidence.verdict_substance(
            HEADER + "Traced:\n- cache_key through the wiring, and found nothing",
            pr_title=TITLE,
        )
        self.assertTrue(added)

    def test_a_verb_with_no_object_is_not_a_clause(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "Checked. Verified. Read.", pr_title="chore: unrelated"
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)

    def test_reviewed_is_not_one_of_the_verbs(self):
        """Admitting it would admit the receipt itself.

        The #926 shape opens with the word, and so does the default template's
        "Scope reviewed:" line — on the record, adding it would have passed all
        75 rubber stamps and all ten default templates.
        """
        ok, reason = evidence.verdict_substance(
            HEADER + "Reviewed the whole diff and everything looks correct here",
            pr_title="chore: unrelated",
        )

        self.assertFalse(ok)
        self.assertIn("nothing concrete", reason)


class TheNineTwoSixReceiptShapeStaysRefused(unittest.TestCase):
    """The calibration the widening is measured against.

    ``Reviewed <PR title>: <generic affirmation>`` names nothing at all — no
    path, no dotted identifier, no second bare identifier, and "reviewed" is not
    an act-of-review verb — so the anchor half refuses it on its own, with no
    help from the novelty floor.
    """

    def test_the_observed_shape_is_refused_with_no_title_to_lean_on(self):
        for prose in OBSERVED:
            with self.subTest(prose=prose[:48]):
                ok, reason = evidence.verdict_substance(HEADER + prose, pr_title="")

                self.assertFalse(ok)
                self.assertIn("nothing concrete", reason)

    def test_a_generic_affirmation_naming_nothing(self):
        ok, reason = evidence.verdict_substance(
            HEADER + "Reviewed the pull request. The implementation is robust, "
            "well-bounded, and the coverage bar is maintained throughout.",
            pr_title="",
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

    def test_a_widened_anchor_does_not_readmit_a_restatement(self):
        """The independence #1106 relies on, pinned against the new anchors.

        A title full of identifiers now satisfies the anchor half through the
        dotted *and* the bare-identifier route. Restating it is still refused —
        by the other guard, which is what lets the anchor set be generous.
        """
        title = "fix(cache): fold cache.cache_key into collect_static_hints"
        body = HEADER + "Reviewed fix(cache): fold cache.cache_key into collect_static_hints."

        self.assertTrue(evidence.verdict_substance(body, pr_title="chore: unrelated")[0])

        ok, reason = evidence.verdict_substance(body, pr_title=title)

        self.assertFalse(ok)
        self.assertIn("restated", reason)


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
