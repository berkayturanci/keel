"""Tests for stated-blocker detection on open PRs (#1512)."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
import unittest.mock as mock

from keel import blockers, cli
from keel.blockers import ABANDONED, CLEARED, ISSUE, PENDING, TAG, UNKNOWN, Blocker
from keel.runner import CommandResult


def _ok(out=""):
    return CommandResult(True, 0, out, stdout=out)


def _fail(err="boom"):
    return CommandResult(False, 1, err, stderr=err)


def issues(body):
    return [b.ref for b in blockers.parse_blockers(body) if b.kind == ISSUE]


class ParseTests(unittest.TestCase):
    def test_phrase_forms(self):
        for body in (
            "Blocked by #12",
            "depends on #12",
            "Depends on: PR #12",
            "waiting on issue #12",
            "blocked until #12",
            "Do not merge before #12",
            "don't merge until #12",
            "must not merge prior to #12",
        ):
            self.assertEqual(["12"], issues(body), body)

    def test_tag_forms(self):
        self.assertEqual(
            (Blocker(TAG, "v1.2.0"),), blockers.parse_blockers("Do not merge before v1.2.0.")
        )
        self.assertEqual(
            (Blocker(TAG, "1.2.0"),), blockers.parse_blockers("do not merge until `1.2.0`")
        )

    def test_v_prefixed_tag(self):
        self.assertEqual((Blocker(TAG, "v2"),), blockers.parse_blockers("do not merge before v2"))

    def test_multiple_and_dedupe(self):
        got = blockers.parse_blockers(
            "Blocked by #1, #2 and #3. Depends on #2. Do not merge before v9.0"
        )
        self.assertEqual(
            (Blocker(ISSUE, "1"), Blocker(ISSUE, "2"), Blocker(ISSUE, "3"), Blocker(TAG, "v9.0")),
            got,
        )

    def test_false_positives(self):
        for body in (
            None,
            "",
            "unblocked by #5",
            "This is not blocked by #5",
            "no longer blocked by #5",
            "Do not merge before release",
            "do not merge before 5pm",
            "do not merge until 2026-10-15",
            "do not merge before 3 reviewers approve",
            "do not merge until 10.30",
            "do not merge for 3.5 hours",
            "Upgrade dependency #123",
            "no longer do not merge before #5",
            "depends on owner/repo#5",
            "blocked by #",
            "no dependencies",
        ):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_code_and_comments_are_not_claims(self):
        body = (
            "```\nblocked by #1\n```\n"
            "~~~\ndo not merge before #2\n~~~\n"
            "<!-- depends on #3 -->\n"
            "Use `blocked by #4` in a body.\n"
        )
        self.assertEqual((), blockers.parse_blockers(body))

    def test_unterminated_fence_does_not_hide_the_rest(self):
        self.assertEqual(["5"], issues("```\nstray opener\nBlocked by #5"))
        self.assertEqual(
            [],
            issues("```text\nBlocked by #5\n```python\nexample\n```"),
        )
        self.assertEqual(
            [],
            issues("```\nstray opener\nBlocked by #5\n```python\nblocked by #99\n```"),
        )
        self.assertEqual(
            ["5"],
            issues("~~~\nstray opener\nBlocked by #5\n```\nblocked by #99\n```"),
        )

    def test_tag_continuation_characters(self):
        for body in (
            "Do not merge before v2.0.0@rc1",
            "Do not merge before v2.0.0~beta1",
            "Do not merge before 1.2.0@rc1",
        ):
            self.assertEqual(((), True), self.refs(body), body)

    def test_four_char_and_longer_closing_fences(self):
        for body in (
            "````\nBlocked by #12\n````",
            "~~~~\nBlocked by #12\n~~~~",
            "```\nBlocked by #12\n`````",
            "~~~\nBlocked by #12\n~~~~",
            "````markdown\n```\nBlocked by #12\n```\n````",
        ):
            self.assertEqual(((), False), blockers.scan_blockers(body), body)
        self.assertEqual(
            ["5"],
            issues("````\nunterminated 4-tick\nBlocked by #5\n```\nblocked by #99\n```"),
        )

    def test_blockquotes_are_not_claims(self):
        for body in (
            "> blocked by #1234",
            "> > blocked by #1234",
            "   > blocked by #1234",
            ">>> blocked by #1234",
            "> - #1234",
        ):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_github_alert_blocks_are_scanned(self):
        # GitHub alert blocks are author claims and must be scanned, not stripped.
        self.assertEqual(["5"], issues("> [!WARNING]\n> Blocked by #5"))
        self.assertEqual(["5"], issues("> [!NOTE]\n> Blocked by #5"))
        self.assertEqual(["5"], issues("> [!IMPORTANT]\n> Blocked by #5"))
        self.assertEqual(["5"], issues("> [!CAUTION]\n> Blocked by #5"))
        self.assertEqual(["5"], issues("> [!TIP]\n> Blocked by #5"))
        self.assertEqual(["5", "6"], issues("> [!NOTE]\n> Blocked by:\n> - #5\n> - #6"))
        self.assertEqual(["5"], issues("   >   [!warning]   \n> Blocked by #5"))
        self.assertEqual(
            ["5"],
            issues("> [!NOTE]\n> Blocked by #5\n\n> Quoted:\n> Blocked by #99"),
        )

    def test_details_blocks_are_not_claims(self):
        body = (
            "<details><summary>Release notes</summary>\n"
            "Bumps foo from 1.0 to 1.1\n"
            "depends on #1234\n"
            "</details>"
        )
        self.assertEqual((), blockers.parse_blockers(body))

    def test_real_blocker_outside_quote_and_details_is_captured(self):
        for body in (
            "Blocked by #5\n\n> blocked by #1234",
            "> blocked by #1234\n\nBlocked by #5",
            (
                "Blocked by #5\n\n"
                "<details><summary>Release notes</summary>\n"
                "depends on #1234\n"
                "</details>"
            ),
            (
                "<details><summary>Release notes</summary>\n"
                "depends on #1234\n"
                "</details>\n\n"
                "Blocked by #5"
            ),
        ):
            self.assertEqual(["5"], issues(body), body)

    def test_nested_and_unterminated_details(self):
        nested = (
            "<details><summary>Outer</summary>\n"
            "outer notes\n"
            "<details><summary>Inner</summary>\n"
            "depends on #1234\n"
            "</details>\n"
            "more outer depends on #5678\n"
            "</details>"
        )
        self.assertEqual((), blockers.parse_blockers(nested))

        nested_with_real = (
            "Blocked by #5\n\n"
            "<details><summary>Outer</summary>\n"
            "<details><summary>Inner</summary>\n"
            "depends on #1234\n"
            "</details>\n"
            "depends on #5678\n"
            "</details>"
        )
        self.assertEqual(["5"], issues(nested_with_real))

        unterminated = (
            "Blocked by #5\n\n"
            "<details><summary>Release notes</summary>\n"
            "depends on #1234 (never closed)"
        )
        self.assertEqual(["5"], issues(unterminated))

        unterminated_alone = "<details><summary>Release notes</summary>\ndepends on #1234"
        self.assertEqual((), blockers.parse_blockers(unterminated_alone))

        stray_close = "</details>\nBlocked by #5"
        self.assertEqual(["5"], issues(stray_close))

    def test_crlf_with_blockquote(self):
        body = "Blocked by #5\r\n> blocked by #1234\r\n"
        self.assertEqual(["5"], issues(body))
        self.assertEqual((), blockers.parse_blockers("> blocked by #1234\r\n"))

    def test_crlf_bodies(self):
        for body in (
            "Depends on:\r\n- #1\r\n- #2\r\n- #3\r\n\r\nNotes",
            "Depends on:\r- #1\r- #2\r- #3",
        ):
            self.assertEqual((("1", "2", "3"), False), self.refs(body), body)
        self.assertEqual((("5",), False), self.refs("Do not merge before #5\r\nThanks"))
        self.assertEqual(((), True), self.refs("Depends on:\r\n- owner/repo#9\r\n"))
        self.assertEqual(((), True), self.refs("Blocked by:\r\n- https://x.test/9\r\n"))
        self.assertEqual((("1",), True), self.refs("Depends on #1\r\n- Fixes #5\r\n"))
        self.assertEqual((("1",), False), self.refs("Depends on #1\r\n\r\n- Fixes #5"))

    def test_protocol_relative_and_mismatched_links_are_foreign(self):
        for body in (
            "Blocked by [#12](//github.com/me/keel/issues/12)",
            "Blocked by [#12](https://github.com/me/keel/issues/99)",
            "Blocked by [#12](/issues/99)",
            "Blocked by [#12](#99)",
            "Blocked by [#12](../keel/issues/12)",
            "Blocked by [#12](issues/12)",
            "Blocked by [#12](/issues/12abc)",
            "Blocked by [#12](/issues/12/../../other/issues/12)",
        ):
            self.assertEqual(((), True), self.refs(body, "me/keel"), body)
        self.assertEqual((("12",), False), self.refs("Blocked by [#12](/pull/12)", "me/keel"))
        self.assertEqual((("12",), False), self.refs("Blocked by [#12](#12)", "me/keel"))

    def test_tag_trailing_dot_slash_is_refused(self):
        self.assertEqual(((), True), self.refs("do not merge before v1/."))

    def test_label_followed_by_prose_is_not_flagged(self):
        for body in ("Upgrade dependency: lodash", "Dependency: bump"):
            self.assertEqual(((), False), self.refs(body), body)

    def test_token_accounting_unreadable_targets_are_incomplete(self):
        cases = (
            ("Blocked by #1\nDependency: owner/repo#9", ("1",), True),
            ("Dependency: owner/repo#9", (), True),
            ("Needs dependency: https://github.com/o/r/issues/9", (), True),
            ("Blocked by #1 and https://example.com/ticket/9", ("1",), True),
            ("Blocked by #1 (merged) and #2", ("1",), True),
            ("Blocked by #5 (needs python 3.12.1)", ("5",), True),
            ("Blocked by #5 (v8 engine)", ("5",), True),
        )
        for body, want_refs, want_incomplete in cases:
            self.assertEqual((want_refs, want_incomplete), self.refs(body), body)

    def test_first_target_may_not_sit_past_a_blank_line(self):
        self.assertEqual(((), False), self.refs("Blocked by\n\n#5 is the next ticket"))
        self.assertEqual((("5",), False), self.refs("Blocked by\n#5"))
        self.assertEqual(((), False), self.refs("Depends on:"))

    def test_blank_line_ends_before_and_list_separator_zones(self):
        self.assertEqual(((), False), self.refs("Do not merge before\n\n#5"))
        self.assertEqual(((), False), self.refs("Do not merge before\n\nv1.2.0"))
        self.assertEqual((("5",), False), self.refs("Do not merge before\n#5"))
        self.assertEqual((("1",), False), self.refs("Blocked by #1,\n\n#2"))
        self.assertEqual((("1",), False), self.refs("Blocked by #1 and\n\n#2"))
        self.assertEqual((("1",), False), self.refs("Depends on:\n- #1,\n\n#2"))

    def test_phrase_ending_its_line_stops_at_non_bullet_line(self):
        self.assertEqual((("12",), False), self.refs("Depends on:\n- #12\nFixes #15"))
        self.assertEqual((("12", "13"), False), self.refs("Depends on:\n- #12\n- #13\nFixes #15"))
        self.assertEqual((("12",), False), self.refs("Depends on:\n#12\nFixes #15"))
        self.assertEqual(((), True), self.refs("Depends on:\n- owner/repo#9\nFixes #15"))

    def test_inline_code_quoting_a_phrase_is_not_a_claim(self):
        for body in ("Use `do not merge before #2` in a body.", "Say `depends on #3` here."):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_multi_backtick_and_curly_apostrophe_inline_code_are_not_claims(self):
        for body in (
            "``Blocked by #12``",
            "```Do not merge before #12```",
            "``Use `Blocked by #12` here``",
            "`Don\u2019t merge before #12`",
            "``Don\u2019t merge before #12``",
            "Use `Dependency: #3` in a body.",
        ):
            self.assertEqual(((), False), blockers.scan_blockers(body), body)
        self.assertEqual((("7",), False), self.refs("Blocked by ``#7``"))
        self.assertEqual(
            ((Blocker(ISSUE, "2"),), False),
            blockers.scan_blockers("```Blocked by #1```\nBlocked by #2\n```\ncode\n```"),
        )
        self.assertEqual(
            ((Blocker(ISSUE, "1"), Blocker(TAG, "v1-dependency")), False),
            blockers.scan_blockers("Blocked by #1\nDo not merge before `v1-dependency`"),
        )

    def test_multiline_inline_code_is_not_a_claim(self):
        for body in (
            "Example: ``\nBlocked by #12\n`` end",
            "Example: `\nBlocked by #12\n` end",
            "Example: ``Blocked\nby #12`` end",
            "Example: `\ndo not merge before #12\n` end",
            "Example: ```\nBlocked by #12\n``` end",
        ):
            self.assertEqual(((), False), blockers.scan_blockers(body), body)
        self.assertEqual(
            (("5",), False),
            self.refs("Example: ``\nBlocked by #12\n`` end\nBlocked by #5"),
        )
        self.assertEqual(
            (("5",), False),
            self.refs("Stray ` backtick in para 1\n\nBlocked by `#5`"),
        )
        self.assertEqual((("7",), False), self.refs("Blocked by ``\n#7\n``"))

    def test_github_alert_fenced_code_is_not_a_claim(self):
        for body in (
            "> [!NOTE]\n> ```text\n> Blocked by #12\n> ```",
            "> [!WARNING]\n> ~~~\n> do not merge before #12\n> ~~~",
            "> [!TIP]\n> ````markdown\n> ```\n> Blocked by #12\n> ```\n> ````",
        ):
            self.assertEqual(((), False), blockers.scan_blockers(body), body)
        self.assertEqual(
            (("5",), False),
            self.refs("> [!NOTE]\n> ```text\n> Blocked by #12\n> ```\n> Blocked by #5"),
        )

    def test_list_grammar(self):
        want = ["1", "2", "3"]
        for body in (
            "Blocked by #1, #2, and #3",
            "Blocked by #1, #2 and #3",
            "Blocked by #1, #2, or #3",
            "Blocked by #1 or #2 or #3",
            "Blocked by #1 / #2 / #3",
            "Blocked by #1 & #2 & #3",
            "Blocked by #1 + #2 + #3",
            "Blocked by #1,#2,#3",
            "Blocked by PR #1, PR #2, issue #3",
            "Blocked by PR#1, PR#2, issue#3",
            "Blocked by pull request #1 and pull request #2 and #3",
            "Depends on:\n- #1\n- #2\n* #3\n\nSomething else",
            "Depends on: - #1\n+ #2\n- PR #3",
            "Depends on `#1`, `#2`, `#3`",
        ):
            got = issues(body)
            self.assertEqual(want, got, body)
            self.assertFalse(blockers.scan_blockers(body)[1], body)

    def test_markdown_wrapped_refs(self):
        for body in (
            "Blocked by [#12](https://github.com/o/r/issues/12)",
            "Blocked by **#12**",
            "**Blocked by:** #12",
            "Blocked by _#12_",
            "Blocked by *#12*",
            "Blocked by __#12__",
            "Blocked by [#12](/issues/12), [#13](/pull/13) and **#14**",
            "Blocked by [PR #12](/issues/12)",
        ):
            got = blockers.scan_blockers(body, "o/r")
            self.assertEqual(Blocker(ISSUE, "12"), got[0][0], body)
            self.assertFalse(got[1], body)
        self.assertEqual(
            ["12", "13", "14"], issues("Blocked by [#12](/issues/12), [#13](/pull/13) and **#14**")
        )

    def test_unreadable_reference_is_incomplete(self):
        for body in (
            "Blocked by [the auth work](https://github.com/o/r/issues/12)",
            "Dependencies: #1, #2 -- blocked by o/r#12",
            "Blocked by <https://github.com/o/r/pull/12>",
            "Upgrade dependency: lodash (#123)",
            "Dependency: bump, see #4",
        ):
            self.assertTrue(blockers.scan_blockers(body)[1], body)

    def test_noun_dependency_is_not_a_phrase_at_all(self):
        for body in (
            "Upgrade dependency lodash (#123)",
            "- Fix dependency resolution (#45)",
            "Upgrade dependency #123",
            "needs dependency bump, see #4",
        ):
            self.assertEqual(((), False), blockers.scan_blockers(body), body)
        self.assertEqual((("7",), False), self.refs("Dependency: #7"))

    def refs(self, body, repo=None):
        got, incomplete = blockers.scan_blockers(body, repo)
        return tuple(b.ref for b in got), incomplete

    def test_non_blocking_phrase_is_not_a_blocker(self):
        for body in (
            "Depends on: none. Fixes #9",
            "Dependency: none, closes #9",
            "Blocked by: none. Fixes #9",
        ):
            self.assertEqual(((), False), blockers.scan_blockers(body), body)

    def test_links_into_this_repository_read_as_bare_refs(self):
        own = "Blocked by [#12](https://github.com/Me/Keel/issues/12)"
        self.assertEqual((("12",), False), self.refs(own, "me/keel"))
        self.assertEqual(
            (("12",), False),
            self.refs("Blocked by [#12](https://github.com/me/keel/pull/12)", "me/keel"),
        )
        self.assertEqual((("12",), False), self.refs("Blocked by [#12](/issues/12)"))

    def test_links_into_another_repository_are_never_this_repositorys_ref(self):
        for body in (
            "Blocked by [#12](https://github.com/other/repo/issues/12)",
            "Blocked by [#12](https://github.com/me/keel-other/issues/12)",
            "Blocked by [#12](https://example.com/whatever)",
        ):
            self.assertEqual(((), True), self.refs(body, "me/keel"), body)
        # no repository known: every absolute link is foreign
        self.assertEqual(
            ((), True), self.refs("Blocked by [#12](https://github.com/me/keel/issues/12)")
        )
        # in a list, the foreign half sets incomplete and the own half is still read
        self.assertEqual(
            (("1",), True),
            self.refs("Blocked by #1, [#2](https://github.com/other/repo/pull/2)", "me/keel"),
        )

    def test_second_before_target_is_incomplete(self):
        for body in (
            "do not merge before v1.0 and v2.0",
            "do not merge before #1 and v2.0",
            "do not merge before v1.0, 2.0.1",
            "do not merge before #1 & #2",
            "Do not merge before v2 is released and #2 is merged",
            "do not merge before v1.0 and then v2 lands",
            "Blocked by #1 and v2.0",
        ):
            self.assertTrue(blockers.scan_blockers(body)[1], body)
        self.assertFalse(
            blockers.scan_blockers("do not merge before v1.0 and then 2 reviewers sign off")[1]
        )

    def test_before_tag_followed_by_plain_prose_is_complete(self):
        for body, want in (
            ("Do not merge before v2 is released", "v2"),
            ("Do not merge before v1.2.0 ships", "v1.2.0"),
            ("do not merge until 1.2.0 is cut", "1.2.0"),
            ("Do not merge before v1.2.0.", "v1.2.0"),
            ("Do not merge before v1.2.0, please", "v1.2.0"),
            ("Do not merge before v1.2.0; see notes", "v1.2.0"),
            ("Do not merge before v1.2.0: see notes", "v1.2.0"),
            ("Do not merge before v1.2.0! Thanks", "v1.2.0"),
            ("Do not merge before v1.2.0? Ask first", "v1.2.0"),
            ("(Do not merge before v1.2.0) thanks", "v1.2.0"),
            ("[Do not merge before v1.2.0]", "v1.2.0"),
            ("{Do not merge before v1.2.0}", "v1.2.0"),
        ):
            self.assertEqual(((Blocker(TAG, want),), False), blockers.scan_blockers(body), body)

    def test_tag_with_dot_dot_is_refused(self):
        self.assertEqual(((), True), self.refs("do not merge before v1/../../x"))
        self.assertEqual(((), True), self.refs("do not merge before v1/"))

    def test_merge_this_pr_forms(self):
        for body in (
            "Do not merge this PR until #5",
            "don't merge this before #5",
            "do not merge the pull request until #5",
            "do not merge it until #5",
        ):
            self.assertEqual((("5",), False), self.refs(body), body)

    def test_curly_apostrophe_and_until_after(self):
        self.assertEqual(["5"], issues("Don\u2019t merge before #5"))
        self.assertEqual(["5"], issues("do not merge until after #5"))
        self.assertEqual(["5"], issues("Do not merge prior to #5"))

    def test_historical_phrasing_is_not_a_live_blocker(self):
        for body in (
            "previously blocked by #5",
            "was blocked by #5, now merged",
            "formerly depends on #5",
        ):
            self.assertEqual((), blockers.parse_blockers(body), body)

    def test_dates_are_not_tags(self):
        self.assertEqual((), blockers.parse_blockers("do not merge until 2026.10.15"))

    def test_before_forms_accept_pr_prefix(self):
        self.assertEqual(["12"], issues("do not merge before PR #12"))

    def test_every_phrase_is_read_or_marks_incomplete(self):
        """A phrase followed by an unreadable reference-like target marks the PR incomplete."""
        for body in (
            "do not merge before owner/repo#12",
            "do not merge before v2/sdk#12",
            "do not merge before v2#12",
            "do not merge before v1/repo#9",
            "do not merge before 1.2.0/repo#9",
            "do not merge before 1.2.0#9",
            "do not merge before https://github.com/o/r/issues/12",
            "do not merge before [#12](https://github.com/other/repo/issues/12)",
            "do not merge until after owner/repo#12",
            "Depends on:\n- owner/repo#9",
            "Depends on:\n- https://github.com/o/r/issues/9",
            "Blocked by:\n- https://example.com/ticket/9",
            "Blocked by: https://example.com/ticket/9",
            "Blocked by owner/repo#9",
            "waiting on [#12](//github.com/other/repo/issues/12)",
            "waiting on [#12](../other/issues/12)",
            "blocked until v2.0",
        ):
            self.assertEqual(((), True), self.refs(body, "me/keel"), body)

    def test_foreign_ref_tag_continuation_is_not_read_as_tag(self):
        # A tag target must not be followed by `#\d` (or `/owner/repo#N` continuation);
        # such a target is a foreign repository reference -> incomplete.
        for body in (
            "do not merge before v2/sdk#12",
            "do not merge before v2#12",
            "do not merge before v1/repo#9",
            "do not merge before 1.2.0/repo#9",
            "do not merge before 1.2.0#9",
        ):
            self.assertEqual(((), True), self.refs(body), body)
        self.assertEqual(
            (Blocker(TAG, "v2/sdk"),),
            blockers.parse_blockers("do not merge before v2/sdk"),
        )
        self.assertEqual(
            (Blocker(TAG, "v2"),),
            blockers.parse_blockers("do not merge before v2"),
        )
        self.assertEqual(
            (Blocker(TAG, "1.2.0"),),
            blockers.parse_blockers("do not merge before 1.2.0"),
        )

    def test_negation_anchored_to_same_line(self):
        self.assertEqual(["5"], issues("The previous fix was\nBlocked by #5"))
        self.assertEqual(["5"], issues("Nothing was done.\nBlocked by #5"))
        self.assertEqual([], issues("This was blocked by #5"))
        self.assertEqual([], issues("no longer blocked by #5"))
        self.assertEqual([], issues("not blocked by #5 any more"))
        # Only pre-phrase negation on the same line is checked; trailing qualifiers still count
        # as a stated blocker so phrases like "Blocked by #5 (not ready yet)" are not dropped.
        self.assertEqual(["5"], issues("Blocked by #5 — no longer applies"))

    def test_phrases_with_no_reference_like_target_are_not_flagged(self):
        for body in (
            "behaviour depends on the config",
            "do not merge before release",
            "Blocked by the auth work.\n\nSee #9 for context",
            "Depends on:\n\n- unrelated #9 after a blank line",
            "depends on the config.\nSee #5 for details",
        ):
            self.assertEqual(((), False), self.refs(body), body)

    def test_unrelated_bullet_after_a_blank_line_is_not_a_continuation(self):
        self.assertEqual((("1",), False), self.refs("Depends on #1\n\n- Fixes #5"))
        self.assertEqual((("1",), True), self.refs("Depends on #1\n- Fixes #5"))

    def test_incomplete_when_a_reference_is_not_captured(self):
        for body in (
            "Blocked by #1 and owner/repo#9",
            "Blocked by #1, #2, and https://github.com/o/r/issues/9",
            "Blocked by #1;\n- see owner/r#9",
            "Depends on:\n- #1\n- owner/r#9",
            "depends on owner/repo#5",
            "blocked by https://github.com/o/r/pull/5",
            "do not merge before #1 and #2",
            "do not merge before v1.2 / #4",
        ):
            self.assertTrue(blockers.scan_blockers(body)[1], body)

    def test_not_incomplete_when_nothing_is_dropped(self):
        for body in ("Blocked by #1, and the auth work", "Blocked by #1.\nSee #9 for context", ""):
            self.assertFalse(blockers.scan_blockers(body)[1], body)

    def test_backticked_ref_outside_phrase_is_kept(self):
        self.assertEqual(["7"], issues("blocked by `#7`"))

    def test_backticked_details_tag_does_not_hide_subsequent_blocker(self):
        body = "Blocked by #1\n\nUse `<details>` for release notes.\n\nBlocked by #2"
        self.assertEqual((("1", "2"), False), self.refs(body))
        body_with_real = (
            "Blocked by #1\n\n"
            "Use `<details>` or `</details>` in docs.\n\n"
            "<details><summary>Notes</summary>\n"
            " Mention `</details>` inside.\n"
            " Blocked by #99\n"
            "</details>\n\n"
            "Blocked by #2"
        )
        self.assertEqual((("1", "2"), False), self.refs(body_with_real))

    def test_double_underscore_and_asterisk_in_tag_ref_preserved(self):
        for body in (
            "Do not merge before `v1__rc`",
            "Do not merge before v1__rc",
            "Do not merge before **v1__rc**",
            "Do not merge before __v1__rc__",
        ):
            self.assertEqual(((Blocker(TAG, "v1__rc"),), False), blockers.scan_blockers(body), body)

    def test_negation_with_trailing_space_before_newline_does_not_negate_next_line(self):
        self.assertEqual(["5"], issues("The previous fix was \nBlocked by #5"))
        self.assertEqual(["5"], issues("The previous fix was\t\nDo not merge before #5"))


class ResolveTests(unittest.TestCase):
    def resolve(self, blocker, result):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return result

        with mock.patch("keel.blockers.run_argv", lambda argv, **kw: run(argv, **kw)):
            return blockers.resolve_blocker(blocker), calls

    def resolve_tag_404(self, blocker, *, repo):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return repo if argv[2] == "repos/{owner}/{repo}" else _fail("gh: Not Found (HTTP 404)")

        with mock.patch("keel.blockers.run_argv", run):
            return blockers.resolve_blocker(blocker), calls

    def test_issue_states(self):
        b = Blocker(ISSUE, "9")
        cases = {
            "closed completed issue": ("closed completed issue", CLEARED),
            "closed, no reason (old issue)": ("closed - issue", CLEARED),
            "closed not_planned": ("closed not_planned issue", ABANDONED),
            "closed duplicate": ("closed duplicate issue", ABANDONED),
            "merged PR": ("closed completed 2026-10-01T00:00:00Z", CLEARED),
            "merged PR, no reason": ("closed - 2026-10-01T00:00:00Z", CLEARED),
            "PR closed unmerged": ("closed - unmerged", ABANDONED),
            "open issue": ("open - issue", PENDING),
            "open PR": ("open - unmerged", PENDING),
            "odd state": ("locked - issue", UNKNOWN),
            "empty": ("", UNKNOWN),
            "short": ("closed", UNKNOWN),
        }
        for name, (out, want) in cases.items():
            self.assertEqual(want, self.resolve(b, _ok(out + "\n"))[0], name)
        self.assertEqual(UNKNOWN, self.resolve(b, _fail("HTTP 404 Not Found"))[0])
        _, calls = self.resolve(b, _ok("closed - issue"))
        self.assertIn("repos/{owner}/{repo}/issues/9", calls[0])
        self.assertIn("merged_at", calls[0][-1])

    def test_malformed_successful_responses_are_unknown(self):
        issue, tag = Blocker(ISSUE, "9"), Blocker(TAG, "v1")
        for out in ("closed - nonsense", "closed completed null", "open - nonsense"):
            self.assertEqual(UNKNOWN, self.resolve(issue, _ok(out + "\n"))[0], out)
        for out in ("null", "refs/tags/v1.0", "refs/heads/v1"):
            self.assertEqual(UNKNOWN, self.resolve(tag, _ok(out + "\n"))[0], out)

    def test_tag_states(self):
        b = Blocker(TAG, "v1")
        state, calls = self.resolve(b, _ok("refs/tags/v1"))
        self.assertEqual(CLEARED, state)
        self.assertIn("repos/{owner}/{repo}/git/ref/tags/v1", calls[0])
        self.assertEqual(UNKNOWN, self.resolve(b, _ok(""))[0])
        # a 404 on the tag alone: the repository answers, so the tag is simply not there
        self.assertEqual(PENDING, self.resolve_tag_404(b, repo=_ok("123"))[0])
        # a 404 that the repository probe shares (private repo, no auth): unknown, not pending
        self.assertEqual(UNKNOWN, self.resolve_tag_404(b, repo=_fail("HTTP 404"))[0])
        self.assertEqual(UNKNOWN, self.resolve_tag_404(b, repo=_ok(""))[0])
        _, calls = self.resolve_tag_404(b, repo=_ok("1"))
        self.assertEqual(["gh", "api", "repos/{owner}/{repo}", "--jq", ".id"], calls[1])
        for err in ("gh: command not found", "repository not found", "Could not resolve host"):
            self.assertEqual(UNKNOWN, self.resolve(b, _fail(err))[0], err)

    def test_read_failure_is_never_cleared(self):
        for b in (Blocker(ISSUE, "9"), Blocker(TAG, "v1")):
            for res in (
                _fail("could not resolve host"),
                _fail(""),
                CommandResult(False, 127, "", spawn_failed=True),
            ):
                self.assertEqual(UNKNOWN, self.resolve(b, res)[0])

    def test_unknown_carries_the_reason(self):
        def detail(blocker, res):
            with mock.patch("keel.blockers.run_argv", lambda argv, **kw: res):
                return blockers.resolve_detail(blocker)

        issue, tag = Blocker(ISSUE, "9"), Blocker(TAG, "v1")
        self.assertEqual(
            (UNKNOWN, "API rate limit exceeded"),
            detail(issue, _fail("\n API rate limit exceeded\nmore")),
        )
        self.assertEqual((UNKNOWN, "gh exited 1"), detail(issue, _fail("")))
        self.assertEqual((UNKNOWN, "gh exited 127"), detail(tag, CommandResult(False, 127, "")))
        self.assertEqual((UNKNOWN, "unexpected answer: 'weird'"), detail(issue, _ok("weird")))
        self.assertEqual(
            (UNKNOWN, "unexpected answer: 'locked - issue'"), detail(issue, _ok("locked - issue"))
        )
        self.assertEqual(
            (UNKNOWN, "unexpected answer: 'closed - nonsense'"),
            detail(issue, _ok("closed - nonsense")),
        )
        self.assertEqual((UNKNOWN, "empty answer for the tag lookup"), detail(tag, _ok("")))
        self.assertEqual(
            (UNKNOWN, "unexpected answer: 'null'"),
            detail(tag, _ok("null\n")),
        )
        self.assertEqual((UNKNOWN, "x" * 120), detail(issue, _fail("x" * 300)))
        self.assertEqual((CLEARED, None), detail(issue, _ok("closed - issue")))
        self.assertEqual(
            (UNKNOWN, "tag lookup was a 404 and the repository could not be read"),
            self.tag_404_detail(tag),
        )

    def tag_404_detail(self, blocker):
        def run(argv, **kw):
            return _ok("") if argv[2] == "repos/{owner}/{repo}" else _fail("HTTP 404")

        with mock.patch("keel.blockers.run_argv", run):
            return blockers.resolve_detail(blocker)

    def test_real_run_argv_signature(self):
        import types

        def fake_subprocess_run(argv, **kw):
            return types.SimpleNamespace(returncode=0, stdout="closed completed issue\n", stderr="")

        self.assertEqual(
            CLEARED, blockers.resolve_blocker(Blocker(ISSUE, "3"), _run=fake_subprocess_run)
        )

    def test_run_injection(self):
        seen = []

        def fake(argv, **kw):
            seen.append(kw.get("_run"))
            return _ok("open")

        sentinel = object()
        with mock.patch("keel.blockers.run_argv", fake):
            blockers.resolve_blocker(Blocker(ISSUE, "1"), _run=sentinel)
            blockers.resolve_blocker(Blocker(ISSUE, "1"))
        self.assertEqual([sentinel, None], seen)


def _pr(number, body, draft=False, title="t"):
    return {"number": number, "title": title, "url": f"u/{number}", "isDraft": draft, "body": body}


C = "closed completed issue"
OPEN = "open - issue"


def _router(
    prs, issue_states=None, tags=None, list_result=None, cwds=None, slug_result=None, limits=None
):
    issue_states = issue_states or {}
    tags = tags or {}

    def run(argv, **kw):
        if cwds is not None:
            cwds.append(kw.get("cwd"))
        if argv[:3] == ["gh", "repo", "view"]:
            return slug_result if slug_result is not None else _ok("me/keel\n")
        if argv[:3] == ["gh", "pr", "list"]:
            n = int(argv[argv.index("--limit") + 1])
            if limits is not None:
                limits.append(n)
            return list_result if list_result is not None else _ok(json.dumps(prs[:n]))
        if argv[2] == "repos/{owner}/{repo}":
            return _ok("1")
        if "git/ref/tags" in argv[2]:
            name = argv[2].split("git/ref/tags/", 1)[1]
            res = tags.get(name, "fail")
            return _ok(f"refs/tags/{name}") if res == "yes" else _fail("HTTP 404 Not Found")
        num = argv[2].rsplit("/", 1)[1]
        state = issue_states.get(num)
        return _ok(state) if state else _fail("network down")

    return run


class StaleBlockersTests(unittest.TestCase):
    def report(self, **kw):
        with mock.patch("keel.blockers.run_argv", _router(**kw)):
            return blockers.stale_blockers()

    def test_flags_cleared_and_orders(self):
        prs = [
            _pr(30, "Blocked by #1 and #2", draft=True),
            _pr(10, "Do not merge before v2.0. Blocked by #1"),
            _pr(20, "Blocked by #2"),
            _pr(40, "no blocker here"),
            _pr(50, "Blocked by #50 and #99"),
            _pr(60, None),
        ]
        rep = self.report(
            prs=prs, issue_states={"1": C, "2": OPEN, "99": OPEN}, tags={"v2.0": "yes"}
        )
        self.assertTrue(rep["ok"])
        self.assertEqual(0, rep["unknown_blockers"])
        self.assertEqual([10, 30], [p["number"] for p in rep["prs"]])
        ten, thirty = rep["prs"]
        self.assertTrue(ten["all_cleared"])
        self.assertFalse(thirty["all_cleared"])
        self.assertTrue(thirty["draft"])
        self.assertEqual(
            [("1", CLEARED), ("2", PENDING)], [(e["ref"], e["state"]) for e in thirty["blockers"]]
        )

    def test_unreadable_blocker_is_not_cleared(self):
        rep = self.report(prs=[_pr(5, "Blocked by #77 and #1")], issue_states={"1": C})
        self.assertEqual(UNKNOWN, rep["prs"][0]["blockers"][0]["state"])
        self.assertFalse(rep["prs"][0]["all_cleared"])

    def test_abandoned_is_reported_never_cleared(self):
        rep = self.report(
            prs=[_pr(5, "Blocked by #1 and #2"), _pr(6, "Blocked by #2")],
            issue_states={"1": C, "2": "closed - unmerged"},
        )
        self.assertEqual([5, 6], [p["number"] for p in rep["prs"]])
        self.assertEqual([CLEARED, ABANDONED], [e["state"] for e in rep["prs"][0]["blockers"]])
        self.assertFalse(rep["prs"][0]["all_cleared"])
        self.assertFalse(rep["prs"][1]["all_cleared"])

    def test_self_reference_is_dropped(self):
        rep = self.report(prs=[_pr(50, "Blocked by #50 and #1")], issue_states={"1": C})
        self.assertEqual(
            [("1", CLEARED)], [(e["ref"], e["state"]) for e in rep["prs"][0]["blockers"]]
        )
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_oxford_list_is_not_all_cleared_while_the_third_is_open(self):
        rep = self.report(
            prs=[_pr(5, "Blocked by #1, #2, and #3")], issue_states={"1": C, "2": C, "3": OPEN}
        )
        self.assertEqual(["1", "2", "3"], [e["ref"] for e in rep["prs"][0]["blockers"]])
        self.assertFalse(rep["prs"][0]["all_cleared"])

    def test_incomplete_list_is_never_all_cleared(self):
        rep = self.report(prs=[_pr(5, "Blocked by #1 and owner/repo#9")], issue_states={"1": C})
        self.assertTrue(rep["prs"][0]["incomplete"])
        self.assertFalse(rep["prs"][0]["all_cleared"])
        rep = self.report(prs=[_pr(6, "Blocked by #1")], issue_states={"1": C})
        self.assertFalse(rep["prs"][0]["incomplete"])
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_before_tag_followed_by_prose_and_open_issue_is_not_all_cleared(self):
        rep = self.report(
            prs=[_pr(5, "Do not merge before v2 is released and #2 is merged")],
            issue_states={"2": OPEN},
            tags={"v2": "yes"},
        )
        self.assertEqual(1, len(rep["prs"]))
        self.assertEqual([{"kind": TAG, "ref": "v2", "state": CLEARED}], rep["prs"][0]["blockers"])
        self.assertTrue(rep["prs"][0]["incomplete"])
        self.assertFalse(rep["prs"][0]["all_cleared"])
        rep_clean = self.report(
            prs=[_pr(6, "Do not merge before v2 is released")],
            tags={"v2": "yes"},
        )
        self.assertFalse(rep_clean["prs"][0]["incomplete"])
        self.assertTrue(rep_clean["prs"][0]["all_cleared"])

    def test_before_tag_punctuation_and_dependency_noun_in_report(self):
        rep = self.report(
            prs=[
                _pr(10, "Do not merge before v1.2.0, please"),
                _pr(20, "Do not merge before v1.2.0; see notes"),
                _pr(30, "(Do not merge before v1.2.0) thanks"),
                _pr(40, "Blocked by #1\nDo not merge before `v1-dependency`"),
            ],
            issue_states={"1": C},
            tags={"v1.2.0": "yes"},
        )
        self.assertEqual([10, 20, 30, 40], [p["number"] for p in rep["prs"]])
        for pr in rep["prs"][:3]:
            self.assertFalse(pr["incomplete"], pr)
            self.assertTrue(pr["all_cleared"], pr)
            self.assertEqual([{"kind": TAG, "ref": "v1.2.0", "state": CLEARED}], pr["blockers"])
        forty = rep["prs"][3]
        self.assertFalse(forty["incomplete"])
        self.assertFalse(forty["all_cleared"])
        self.assertEqual(
            [
                {"kind": ISSUE, "ref": "1", "state": CLEARED},
                {"kind": TAG, "ref": "v1-dependency", "state": PENDING},
            ],
            forty["blockers"],
        )

    def test_incomplete_pr_with_pending_blocker_is_reported(self):
        rep = self.report(
            prs=[_pr(5, "Blocked by #1 and owner/repo#9")],
            issue_states={"1": OPEN},
        )
        self.assertEqual(1, len(rep["prs"]))
        self.assertEqual(5, rep["prs"][0]["number"])
        self.assertTrue(rep["prs"][0]["incomplete"])
        self.assertFalse(rep["prs"][0]["all_cleared"])
        self.assertEqual(
            [{"kind": "issue", "ref": "1", "state": PENDING}],
            rep["prs"][0]["blockers"],
        )

    def test_token_accounting_stale_blockers_reports_incomplete_and_not_dropped(self):
        rep = self.report(
            prs=[
                _pr(10, "Blocked by #1\nDependency: owner/repo#9"),
                _pr(20, "Dependency: owner/repo#9"),
                _pr(30, "Needs dependency: https://github.com/o/r/issues/9"),
                _pr(40, "Blocked by #1 and https://example.com/ticket/9"),
                _pr(50, "Blocked by #1 (merged) and #2"),
            ],
            issue_states={"1": C, "2": C},
        )
        self.assertEqual([10, 20, 30, 40, 50], [p["number"] for p in rep["prs"]])
        for pr in rep["prs"]:
            self.assertTrue(pr["incomplete"], pr)
            self.assertFalse(pr["all_cleared"], pr)
        self.assertEqual(["1"], [e["ref"] for e in rep["prs"][0]["blockers"]])
        self.assertEqual([], rep["prs"][1]["blockers"])
        self.assertEqual([], rep["prs"][2]["blockers"])
        self.assertEqual(["1"], [e["ref"] for e in rep["prs"][3]["blockers"]])
        self.assertEqual(["1"], [e["ref"] for e in rep["prs"][4]["blockers"]])

    def test_pr_with_only_unreadable_refs_is_reported(self):
        rep = self.report(
            prs=[
                _pr(5, "Blocked by [the auth work](https://github.com/o/r/issues/12)"),
                _pr(6, "Blocked by owner/repo#9"),
                _pr(7, "Blocked by #7 and owner/repo#9"),
                _pr(8, "Upgrade dependency #3"),
            ],
        )
        self.assertEqual([5, 6, 7], [p["number"] for p in rep["prs"]])
        for pr in rep["prs"]:
            self.assertEqual([], pr["blockers"])
            self.assertTrue(pr["incomplete"])
            self.assertFalse(pr["all_cleared"])

    def test_readable_markdown_ref_resolves(self):
        calls = []
        base = _router(
            prs=[_pr(5, "Blocked by [#1](/issues/1)")],
            issue_states={"1": C},
            slug_result=_fail(),
        )

        def router(argv, **kw):
            calls.append(argv[:3])
            return base(argv, **kw)

        with mock.patch("keel.blockers.run_argv", router):
            rep = blockers.stale_blockers()
        self.assertTrue(rep["prs"][0]["all_cleared"])
        self.assertNotIn(["gh", "repo", "view"], calls)

    def test_slash_tag(self):
        rep = self.report(prs=[_pr(5, "do not merge before v1/rc2")], tags={"v1/rc2": "yes"})
        self.assertEqual(CLEARED, rep["prs"][0]["blockers"][0]["state"])

    def test_double_underscore_tag_reaches_github_unchanged(self):
        rep = self.report(
            prs=[_pr(5, "Do not merge before `v1__rc`")],
            tags={"v1__rc": "yes", "v1rc": "no"},
        )
        self.assertEqual(
            [{"kind": TAG, "ref": "v1__rc", "state": CLEARED}],
            rep["prs"][0]["blockers"],
        )
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_prefix_tag_does_not_clear_unsupported_continuation(self):
        rep = self.report(
            prs=[_pr(5, "Do not merge before v2.0.0@rc1")],
            tags={"v2.0.0": "yes"},
        )
        self.assertEqual([5], [p["number"] for p in rep["prs"]])
        self.assertEqual([], rep["prs"][0]["blockers"])
        self.assertTrue(rep["prs"][0]["incomplete"])
        self.assertFalse(rep["prs"][0]["all_cleared"])

    def test_backticked_details_does_not_cause_false_all_cleared(self):
        body = "Blocked by #1\n\nUse `<details>` for release notes.\n\nBlocked by #2"
        rep = self.report(prs=[_pr(5, body)], issue_states={"1": C, "2": OPEN})
        self.assertEqual(
            [("1", CLEARED), ("2", PENDING)],
            [(e["ref"], e["state"]) for e in rep["prs"][0]["blockers"]],
        )
        self.assertFalse(rep["prs"][0]["all_cleared"])

    def test_multiline_inline_code_and_alert_fence_not_reported_in_stale_blockers(self):
        prs = [
            _pr(5, "Example: ``\nBlocked by #12\n`` end"),
            _pr(6, "> [!NOTE]\n> ```text\n> Blocked by #12\n> ```"),
        ]
        rep = self.report(prs=prs, issue_states={"12": C})
        self.assertEqual([], rep["prs"])

    def test_truncation_is_flagged(self):
        limits = []
        router = _router(prs=[_pr(1, "x"), _pr(2, "y"), _pr(3, "z")], limits=limits)
        with mock.patch("keel.blockers.run_argv", router):
            self.assertTrue(blockers.stale_blockers(limit=1)["truncated"])
            self.assertTrue(blockers.stale_blockers(limit=2)["truncated"])
            self.assertFalse(blockers.stale_blockers(limit=3)["truncated"])
        self.assertEqual([2, 3, 4], limits)

    def test_read_failure_is_never_silence(self):
        # `gh pr list` works; every REST call fails (rate limit): the PRs are listed, as unknown
        prs = [_pr(5, "Blocked by #77"), _pr(6, "Blocked by #78"), _pr(7, "no blocker")]
        rep = self.report(prs=prs, issue_states={})
        self.assertEqual([5, 6], [p["number"] for p in rep["prs"]])
        for pr in rep["prs"]:
            self.assertEqual(UNKNOWN, pr["blockers"][0]["state"])
            self.assertIn("reason", pr["blockers"][0])
        self.assertEqual("network down", rep["prs"][0]["blockers"][0]["reason"])
        self.assertEqual(2, rep["unknown_blockers"])
        text = blockers.render(rep)
        self.assertIn("#5 t — blocker state could not be checked", text)
        self.assertIn("  - #77: unknown (network down)", text)
        self.assertIn("2 blocker(s) could not be checked", text)
        self.assertNotIn("No open PRs", text)

    def test_uppercase_scheme_still_looks_up_the_repository(self):
        link = "Blocked by [#1](HTTPS://github.com/me/keel/issues/1)"
        rep = self.report(prs=[_pr(5, link)], issue_states={"1": C})
        self.assertTrue(rep["prs"][0]["all_cleared"])

    def test_null_title_and_url_render_as_empty(self):
        row = {"number": 5, "title": None, "url": None, "isDraft": False, "body": "Blocked by #1"}
        rep = self.report(prs=[row], issue_states={"1": C})
        self.assertEqual(("", ""), (rep["prs"][0]["title"], rep["prs"][0]["url"]))

    def test_repo_slug_lookup(self):
        link = "Blocked by [#1](https://github.com/me/keel/issues/1)"
        rep = self.report(prs=[_pr(5, link)], issue_states={"1": C})
        self.assertTrue(rep["prs"][0]["all_cleared"])
        for bad in (_fail(), _ok("not a slug"), _ok("")):
            rep = self.report(prs=[_pr(5, link)], issue_states={"1": C}, slug_result=bad)
            self.assertEqual([], rep["prs"][0]["blockers"])
            self.assertTrue(rep["prs"][0]["incomplete"])

    def test_malformed_rows_are_skipped(self):
        rows = [
            1,
            None,
            {"title": "no number"},
            {"number": "7", "body": "Blocked by #1"},
            {"number": True, "body": "Blocked by #1"},
            _pr(8, "Blocked by #1"),
            {"number": 9, "body": 5},
        ]
        rep = self.report(prs=rows, issue_states={"1": C})
        self.assertEqual([8], [p["number"] for p in rep["prs"]])

    def test_distinct_unknown_blockers_are_counted_once(self):
        prs = [_pr(5, "Blocked by #77"), _pr(6, "Blocked by #77")]
        self.assertEqual(1, self.report(prs=prs)["unknown_blockers"])

    def test_no_unknown_when_all_read(self):
        rep = self.report(prs=[_pr(5, "Blocked by #1")], issue_states={"1": C})
        self.assertEqual(0, rep["unknown_blockers"])
        self.assertNotIn("could not be checked", blockers.render(rep))

    def test_pr_list_unreadable(self):
        for res in (_fail(), _ok("not json"), _ok('{"a": 1}')):
            self.assertFalse(self.report(prs=[], list_result=res)["ok"])

    def test_pr_list_empty_output_is_empty_list(self):
        rep = self.report(prs=[], list_result=_ok(""))
        self.assertTrue(rep["ok"])
        self.assertEqual([], rep["prs"])

    def test_run_injection_to_pr_list(self):
        seen = []

        def fake(argv, **kw):
            seen.append(kw.get("_run"))
            return _ok("[]")

        sentinel = object()
        with mock.patch("keel.blockers.run_argv", fake):
            blockers.stale_blockers(_run=sentinel)
        self.assertEqual([sentinel], seen)


class RenderAndCliTests(unittest.TestCase):
    def test_render(self):
        self.assertIn(
            "could not",
            blockers.render(
                {
                    "ok": False,
                    "error": "could not",
                    "truncated": False,
                    "unknown_blockers": 0,
                    "prs": [],
                }
            ),
        )
        self.assertEqual(
            "No open PRs with a cleared, abandoned or unchecked blocker.",
            blockers.render(
                {"ok": True, "error": None, "truncated": False, "unknown_blockers": 0, "prs": []}
            ),
        )
        out = blockers.render(
            {
                "ok": True,
                "error": None,
                "truncated": True,
                "unknown_blockers": 0,
                "prs": [
                    {
                        "number": 3,
                        "title": "x",
                        "draft": True,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [
                            {"kind": ISSUE, "ref": "1", "state": CLEARED},
                            {"kind": TAG, "ref": "v1", "state": PENDING},
                        ],
                    },
                    {
                        "number": 5,
                        "title": "z",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": True,
                        "blockers": [{"kind": ISSUE, "ref": "3", "state": CLEARED}],
                    },
                    {
                        "number": 4,
                        "title": "y",
                        "draft": False,
                        "all_cleared": True,
                        "incomplete": False,
                        "blockers": [{"kind": ISSUE, "ref": "2", "state": CLEARED}],
                    },
                ],
            }
        )
        self.assertIn("#3 (draft) x — some blockers cleared, abandoned or unchecked", out)
        self.assertIn("  - #1: cleared", out)
        self.assertIn("  - v1: pending", out)
        self.assertIn("#4 y — all blockers cleared", out)
        out2 = blockers.render(
            {
                "ok": True,
                "error": None,
                "truncated": False,
                "unknown_blockers": 0,
                "prs": [
                    {
                        "number": 6,
                        "title": "a",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": True,
                        "blockers": [],
                    },
                    {
                        "number": 7,
                        "title": "b",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [{"kind": ISSUE, "ref": "1", "state": ABANDONED}],
                    },
                ],
            }
        )
        self.assertIn("#6 a — blocker reference could not be read", out2)
        self.assertIn("#7 b — blocker abandoned, none cleared", out2)
        self.assertIn("#5 z — blocker list may be incomplete", out)
        self.assertIn("raise --limit", out)

    def test_render_mixed_abandoned_and_unknown(self):
        out = blockers.render(
            {
                "ok": True,
                "error": None,
                "truncated": False,
                "unknown_blockers": 1,
                "prs": [
                    {
                        "number": 9,
                        "title": "m",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [
                            {"kind": ISSUE, "ref": "1", "state": ABANDONED},
                            {"kind": ISSUE, "ref": "2", "state": UNKNOWN, "reason": "rate limit"},
                        ],
                    },
                    {
                        "number": 10,
                        "title": "p",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [
                            {"kind": ISSUE, "ref": "1", "state": ABANDONED},
                            {"kind": ISSUE, "ref": "3", "state": PENDING},
                        ],
                    },
                ],
            }
        )
        self.assertIn("#9 m — some blockers cleared, abandoned or unchecked", out)
        self.assertIn("  - #1: abandoned", out)
        self.assertIn("  - #2: unknown (rate limit)", out)
        self.assertIn("#10 p — some blockers cleared, abandoned or unchecked", out)
        self.assertNotIn("#10 p — blocker abandoned, none cleared", out)

    def test_render_pending_and_unknown_without_cleared(self):
        out = blockers.render(
            {
                "ok": True,
                "error": None,
                "truncated": False,
                "unknown_blockers": 1,
                "prs": [
                    {
                        "number": 11,
                        "title": "pu",
                        "draft": False,
                        "all_cleared": False,
                        "incomplete": False,
                        "blockers": [
                            {"kind": ISSUE, "ref": "1", "state": PENDING},
                            {"kind": ISSUE, "ref": "2", "state": UNKNOWN, "reason": "rate limit"},
                        ],
                    },
                ],
            }
        )
        self.assertIn("#11 pu — no blocker cleared, some unchecked", out)
        self.assertNotIn("some blockers cleared", out)

    def run_cli(self, argv, router):
        buf = io.StringIO()
        with mock.patch("keel.blockers.run_argv", router), contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_cli_text_and_json(self):
        router = _router(prs=[_pr(8, "Blocked by #1")], issue_states={"1": C})
        code, out = self.run_cli(["stale-blockers"], router)
        self.assertEqual(0, code)
        self.assertIn("#8 t — all blockers cleared", out)
        code, out = self.run_cli(["stale-blockers", "--json", "--limit", "5"], router)
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual(8, data["prs"][0]["number"])
        abandoned_router = _router(
            prs=[_pr(9, "Blocked by #2")], issue_states={"2": "closed - unmerged"}
        )
        code, out = self.run_cli(["stale-blockers"], abandoned_router)
        self.assertEqual(0, code)
        self.assertIn("#9 t — blocker abandoned, none cleared", out)

    def test_cli_passes_root_as_cwd_and_rejects_bad_limit(self):
        cwds = []
        router = _router(prs=[_pr(8, "Blocked by #1")], issue_states={"1": C}, cwds=cwds)
        self.assertEqual(0, self.run_cli(["stale-blockers", "--root", "/some/dir"], router)[0])
        self.assertEqual({"/some/dir"}, set(cwds))
        for bad in ("0", "-3"):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as ctx,
            ):
                cli.main(["stale-blockers", "--limit", bad])
            self.assertEqual(2, ctx.exception.code)

    def test_cli_exits_2_when_a_blocker_could_not_be_read(self):
        router = _router(prs=[_pr(8, "Blocked by #1")], issue_states={})
        code, out = self.run_cli(["stale-blockers"], router)
        self.assertEqual(2, code)
        self.assertIn("1 blocker(s) could not be checked", out)
        code, out = self.run_cli(["stale-blockers", "--json"], router)
        self.assertEqual(2, code)
        self.assertEqual(1, json.loads(out)["unknown_blockers"])

    def test_cli_failure_exit(self):
        code, out = self.run_cli(["stale-blockers"], _router(prs=[], list_result=_fail()))
        self.assertEqual(1, code)
        self.assertIn("could not list", out)


if __name__ == "__main__":
    unittest.main()
