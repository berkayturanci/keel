"""Unit tests for the jury built-in gate (pure parse + fail-soft runner)."""

import json
import subprocess
import unittest

from keel import jury

SAMPLE = {
    "findings": [
        {
            "severity": "major",
            "file": "src/x.py",
            "line": 42,
            "claim": "unchecked return",
            "reviewer": "claude",
        },
        {
            "severity": "minor",
            "file": "src/x.py",
            "line": 7,
            "claim": "missing docstring",
            "reviewer": "codex",
        },
        {"severity": "nit", "file": None, "line": None, "claim": "style", "reviewer": "agy"},
    ]
}


class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _jury_ok(argv, **kw):
    if "--version" in argv:
        return _Proc(0, "jury 1.0")
    return _Proc(1, json.dumps(SAMPLE))  # nonzero exit (REQUEST CHANGES) but JSON on stdout


def _jury_absent(argv, **kw):
    raise OSError("no jury on PATH")


class TestMapSeverity(unittest.TestCase):
    def test_known(self):
        self.assertEqual(jury.map_severity("major"), "major")
        self.assertEqual(jury.map_severity("BLOCKER"), "critical")
        self.assertEqual(jury.map_severity("info"), "nit")

    def test_unknown_defaults_minor(self):
        self.assertEqual(jury.map_severity("weird"), "minor")
        self.assertEqual(jury.map_severity(""), "minor")


class TestParseFindings(unittest.TestCase):
    def test_dict(self):
        fs = jury.parse_findings(SAMPLE)
        self.assertEqual(len(fs), 3)
        self.assertEqual(fs[0].severity, "major")
        self.assertEqual(fs[0].path, "src/x.py")
        self.assertEqual(fs[0].line, 42)
        self.assertTrue(fs[0].anchorable)
        self.assertEqual(fs[0].source, "jury:claude")
        self.assertFalse(fs[2].anchorable)  # null file/line

    def test_raw_string(self):
        self.assertEqual(len(jury.parse_findings(json.dumps(SAMPLE))), 3)

    def test_bad_string(self):
        self.assertEqual(jury.parse_findings("not json"), [])

    def test_non_dict(self):
        self.assertEqual(jury.parse_findings([1, 2]), [])

    def test_no_findings_key(self):
        self.assertEqual(jury.parse_findings({"x": 1}), [])

    def test_defaults_for_missing_fields(self):
        fs = jury.parse_findings({"findings": [{"severity": "minor", "file": "a", "line": 1}]})
        self.assertEqual(fs[0].message, "(jury finding)")
        self.assertEqual(fs[0].source, "jury:consensus")


class TestAvailable(unittest.TestCase):
    def test_present(self):
        self.assertTrue(jury.available(_run=_jury_ok))

    def test_absent(self):
        self.assertFalse(jury.available(_run=_jury_absent))


class TestRunGate(unittest.TestCase):
    def test_no_diff_is_noop(self):
        self.assertEqual(jury.run_gate("", _run=_jury_ok), (True, [], False))

    def test_absent_is_noop(self):
        self.assertEqual(jury.run_gate("a diff", _run=_jury_absent), (True, [], False))

    def test_oversized_diff_skips_cli_but_emits_advisory(self):
        def fail_if_called(argv, **kw):
            raise AssertionError(f"unexpected jury call: {argv}")

        ok, findings, timed_out = jury.run_gate(
            "x" * (jury.MAX_DIFF_BYTES + 1), _run=fail_if_called
        )

        # Non-blocking (does not gate) but no longer silent: the skip is surfaced.
        self.assertTrue(ok)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "nit")
        self.assertEqual(findings[0].source, "jury:skipped-oversize")
        self.assertIn("over the", findings[0].message)

    def test_oversized_diff_blocks_in_gating_mode(self):
        def fail_if_called(argv, **kw):
            raise AssertionError(f"unexpected jury call: {argv}")

        ok, findings, timed_out = jury.run_gate(
            "x" * (jury.MAX_DIFF_BYTES + 1), mode="gating", _run=fail_if_called
        )

        self.assertFalse(ok)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "major")
        self.assertEqual(findings[0].source, "jury:skipped-oversize")

    def test_present_blocks_on_major(self):
        ok, fs, _to = jury.run_gate("some diff", _run=_jury_ok)
        self.assertFalse(ok)
        self.assertEqual(len(fs), 3)

    def test_present_clean_passes(self):
        def clean(argv, **kw):
            if "--version" in argv:
                return _Proc(0)
            return _Proc(
                0,
                json.dumps(
                    {"findings": [{"severity": "minor", "file": "a", "line": 1, "claim": "x"}]}
                ),
            )

        ok, fs, _to = jury.run_gate("diff", _run=clean)
        self.assertTrue(ok)
        self.assertEqual(len(fs), 1)


def _jury_hangs(argv, **kw):
    if "--version" in argv:
        return _Proc(0, "jury 1.0")
    raise subprocess.TimeoutExpired(cmd="jury", timeout=600)


def _jury_crashes(argv, **kw):
    if "--version" in argv:
        return _Proc(0, "jury 1.0")
    return _Proc(2, "", "Traceback (most recent call last): boom")


class TestIncompleteRun(unittest.TestCase):
    """A jury run that produced no verdict must never read as a clean pass (#624)."""

    def test_timeout_blocks_in_gating_mode(self):
        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_hangs)
        self.assertFalse(ok)
        self.assertEqual(fs[0].severity, "major")
        self.assertEqual(fs[0].source, "jury:incomplete-run")

    def test_crash_blocks_in_gating_mode(self):
        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_crashes)
        self.assertFalse(ok)
        self.assertEqual(fs[0].severity, "major")

    def test_advisory_mode_surfaces_it_without_blocking(self):
        # `minor`, not the oversize branch's `nit`: an oversize diff is a deterministic
        # skip the operator can see from the diff itself, while an incomplete run is an
        # invisible operational failure that will recur silently. `minor` is keel's
        # gated-suggestion tier — it surfaces without blocking.
        for runner in (_jury_hangs, _jury_crashes):
            with self.subTest(runner=runner.__name__):
                ok, fs, _to = jury.run_gate("diff", mode="advisory", _run=runner)
                self.assertTrue(ok)
                self.assertEqual(fs[0].severity, "minor")
                self.assertEqual(fs[0].source, "jury:incomplete-run")

    def test_timeout_message_names_the_limit_and_the_knob(self):
        _, fs, _to = jury.run_gate("diff", mode="gating", timeout=1800, _run=_jury_hangs)
        self.assertIn("timed out after 1800s", fs[0].message)
        self.assertIn("jury_timeout_s", fs[0].message)

    def test_crash_message_names_the_exit_code_and_is_not_a_timeout(self):
        _, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_crashes)
        self.assertIn("exited 2", fs[0].message)
        self.assertNotIn("timed out", fs[0].message)

    def test_nonzero_exit_carrying_findings_is_still_a_verdict(self):
        # ai-jury signals REQUEST CHANGES with a nonzero exit; that is a completed
        # review, not an incomplete run, so its findings must be used as-is.
        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_jury_ok)
        self.assertFalse(ok)  # SAMPLE carries a major
        self.assertEqual([f.source for f in fs if f.source == "jury:incomplete-run"], [])
        self.assertEqual(len(fs), 3)

    def test_absent_cli_is_still_a_clean_no_op(self):
        # keel does not depend on ai-jury; an uninstalled CLI is not an incomplete run.
        self.assertEqual(jury.run_gate("diff", mode="gating", _run=_jury_absent), (True, [], False))

    def test_clean_run_with_zero_findings_is_a_pass(self):
        # The inverse failure, and the costlier one: if "no findings" were mistaken for
        # "no verdict", every clean jury run would block the merge.
        def _clean_empty(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            return _Proc(0, json.dumps({"findings": []}))

        self.assertEqual(jury.run_gate("diff", mode="gating", _run=_clean_empty), (True, [], False))

    def test_report_followed_by_stderr_noise_still_parses(self):
        # run_argv hands back stdout + stderr concatenated and ai-jury logs progress to
        # stderr, so a real report is always followed by "[jury] ..." lines. Parsing
        # strictly discards every finding and reports a completed panel as a crash.
        def _noisy(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            return _Proc(1, json.dumps(SAMPLE), "[jury] round 1: 3 agents reviewing\n")

        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_noisy)
        self.assertFalse(ok)
        self.assertEqual(len(fs), 3)  # findings survive
        self.assertEqual(fs[0].source, "jury:claude")  # not jury:incomplete-run

    def test_clean_exit_with_unreadable_output_is_not_a_pass(self):
        # A zero exit carrying no report is still no review. Keying on "did we parse a
        # verdict" rather than "was the exit code zero" is what catches this.
        def _garbage(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            return _Proc(0, "not a report at all")

        ok, fs, _to = jury.run_gate("diff", mode="gating", _run=_garbage)
        self.assertFalse(ok)
        self.assertEqual(fs[0].source, "jury:incomplete-run")

    def test_disabled_mode_is_treated_as_advisory(self):
        # resolve_jury returns mode "off" when the jury is disabled, and cli threads it
        # straight through, so `--no-jury` with `gates: [jury]` still reaches this code.
        ok, fs, _to = jury.run_gate("diff", mode="off", _run=_jury_hangs)
        self.assertTrue(ok)
        self.assertEqual(fs[0].severity, "minor")

    def test_timeout_is_threaded_to_the_subprocess(self):
        seen = {}

        def _capture(argv, **kw):
            if "--version" in argv:
                return _Proc(0, "jury 1.0")
            seen["timeout"] = kw["timeout"]
            return _Proc(0, json.dumps({"findings": []}))

        jury.run_gate("diff", timeout=2400, _run=_capture)
        self.assertEqual(seen["timeout"], 2400)


# --------------------------------------------------------------------------- #
# Per-reviewer ballots (#1015): the panel mapped onto s7 review evidence.
# --------------------------------------------------------------------------- #

BALLOT_REPORT = {
    "schema_version": "1.1",
    "findings": [
        {
            "severity": "major",
            "file": "src/keel/review.py",
            "line": 42,
            "claim": "the closure path drops the jury post",
            "reviewer": "alpha",
        },
        {
            "severity": "note",
            "file": "docs/keel/evidence.md",
            "line": None,
            "claim": "stale wording",
            "reviewer": "beta",
        },
    ],
    "consensus": [
        {
            "representative": {
                "severity": "major",
                "file": "src/keel/review.py",
                "line": 42,
                "claim": "the closure path drops the jury post",
                "reviewer": "alpha",
            },
            "reviewers": ["alpha", "gamma"],
            "verification_status": "verified",
        },
        {
            "representative": {
                "severity": "nit",
                "file": "docs/keel/evidence.md",
                "line": None,
                "claim": "stale wording",
                "reviewer": "beta",
            },
            "reviewers": ["beta"],
            "verification_status": "unsupported",
        },
    ],
    "reviewers": [
        {
            "name": "alpha",
            "vendor": "anthropic",
            "model": "claude-opus-4",
            "verdict": "REQUEST_CHANGES",
            "findings": [0],
            "round1_ok": True,
            "verified_count": 1,
        },
        {
            "name": "beta",
            "vendor": "google",
            "model": "gemini-3-pro",
            "verdict": "COMMENT",
            "findings": [1],
            "round1_ok": True,
            "verified_count": 0,
        },
        {
            "name": "gamma",
            "vendor": "anthropic",
            "model": "",
            "verdict": "APPROVE",
            "findings": [0],
            "round1_ok": True,
            "verified_count": 1,
        },
        {
            "name": "chair",
            "role": "chair",
            "vendor": "openai",
            "model": "gpt-5",
            "verdict": "REQUEST_CHANGES",
        },
    ],
}


class TestParsePanel(unittest.TestCase):
    def setUp(self):
        self.panel = jury.parse_panel(BALLOT_REPORT)

    def test_the_chair_is_not_a_panelist(self):
        """The chair is the consensus record; it renders as the jury verdict."""
        self.assertEqual([b.reviewer for b in self.panel.ballots], ["alpha", "beta", "gamma"])
        self.assertEqual(self.panel.size, 3)
        self.assertEqual(self.panel.chair.reviewer, "chair")
        self.assertEqual(self.panel.chair.vendor, "openai")

    def test_vendors_are_distinct_lower_cased_and_ordered(self):
        self.assertEqual(self.panel.vendors, ("anthropic", "google"))

    def test_a_ballot_carries_its_own_provenance_and_findings(self):
        alpha = self.panel.ballots[0]
        self.assertEqual(alpha.vendor, "anthropic")
        self.assertEqual(alpha.model, "claude-opus-4")
        self.assertEqual(alpha.verdict, "REQUEST_CHANGES")
        self.assertEqual(
            alpha.findings,
            (
                {
                    "severity": "major",
                    "path": "src/keel/review.py",
                    "line": 42,
                    "message": "the closure path drops the jury post",
                },
            ),
        )

    def test_an_empty_model_reads_as_unset(self):
        """ai-jury writes "" when the CLI default is in force; keel omits the field."""
        self.assertIsNone(self.panel.ballots[2].model)

    def test_severities_are_mapped_into_keels_vocabulary(self):
        self.assertEqual(self.panel.ballots[1].findings[0]["severity"], "nit")

    def test_not_a_ballot_report_is_none_not_an_error(self):
        for value in ({"findings": []}, "not json at all", 5, {"reviewers": "chair"}):
            with self.subTest(value=value):
                self.assertIsNone(jury.parse_panel(value))

    def test_a_json_string_with_trailing_noise_still_parses(self):
        raw = json.dumps(BALLOT_REPORT) + "\n[jury] done\n"
        self.assertEqual(jury.parse_panel(raw).size, 3)

    def test_a_malformed_ballot_raises_rather_than_vanishing(self):
        for reviewers in (["nope"], [{"vendor": "anthropic"}], [{"name": "  "}]):
            with self.subTest(reviewers=reviewers):
                with self.assertRaises(jury.JuryReportError):
                    jury.parse_panel({"findings": [], "reviewers": reviewers})

    def test_unresolvable_finding_indexes_are_dropped(self):
        panel = jury.parse_panel(
            {
                "findings": [{"severity": "nit", "file": "src/a.py", "claim": "x"}],
                "reviewers": [{"name": "a", "verdict": "APPROVE", "findings": [0, "x", True, 99]}],
            }
        )
        self.assertEqual(len(panel.ballots[0].findings), 1)
        self.assertEqual(panel.ballots[0].findings[0]["path"], "src/a.py")

    def test_a_missing_findings_list_on_an_old_approve_is_not_a_review(self):
        """Schema 1.1 empty APPROVE used to enter ballots with an invented Checked scope."""
        panel = jury.parse_panel({"reviewers": [{"name": "a", "verdict": "APPROVE"}]})
        self.assertEqual(panel.ballots, ())
        self.assertEqual(panel.size, 0)

    def test_a_schema_12_review_keeps_empty_findings_when_the_report_says_so(self):
        panel = jury.parse_panel(
            {
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "APPROVE",
                        "counts_as_review": True,
                        "scope_substantive": True,
                        "scope": "Checked src/a.py as stated by the reviewer.",
                    }
                ]
            }
        )
        self.assertEqual(panel.ballots[0].findings, ())
        self.assertEqual(panel.ballots[0].verified_count, 0)
        self.assertTrue(panel.ballots[0].round1_ok)

    def test_a_non_dict_finding_is_dropped(self):
        panel = jury.parse_panel(
            {
                "findings": ["nope", {"severity": "nit", "file": "src/a.py", "claim": "x"}],
                "reviewers": [{"name": "a", "verdict": "APPROVE", "findings": [0, 1]}],
            }
        )
        self.assertEqual(len(panel.ballots[0].findings), 1)
        self.assertEqual(panel.ballots[0].findings[0]["path"], "src/a.py")

    def test_a_non_integer_verified_count_reads_as_zero(self):
        panel = jury.parse_panel(
            {
                "findings": [{"file": "src/a.py", "claim": "x"}],
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "APPROVE",
                        "findings": [0],
                        "verified_count": "two",
                    }
                ],
            }
        )
        self.assertEqual(panel.ballots[0].verified_count, 0)


class TestMapVerdict(unittest.TestCase):
    def test_both_vocabularies_fold_onto_keels(self):
        cases = {
            "APPROVE": "LGTM",
            "READY": "LGTM",
            "REQUEST_CHANGES": "REQUEST_CHANGES",
            "REQUEST CHANGES": "REQUEST_CHANGES",
            "needs-info": "REQUEST_CHANGES",
            "COMMENT": "COMMENT",
            "UNCLEAR": "COMMENT",
            "ABSTAIN": "ABSTAIN",
            "NO_QUORUM": "ABSTAIN",
        }
        for token, expected in cases.items():
            with self.subTest(token=token):
                self.assertEqual(jury.map_verdict(token), expected)

    def test_an_empty_verdict_is_an_abstention_never_an_approval(self):
        self.assertEqual(jury.map_verdict(""), "ABSTAIN")

    def test_an_unknown_token_is_carried_through_not_approved(self):
        self.assertEqual(jury.map_verdict("mostly fine"), "MOSTLY_FINE")


class TestBallotProse(unittest.TestCase):
    """The scope/testing lines keel synthesises must survive its own gate."""

    def test_a_scope_names_the_files_the_ballot_named(self):
        scope = jury.ballot_scope(jury.parse_panel(BALLOT_REPORT).ballots[0])
        self.assertIn("src/keel/review.py", scope)
        self.assertIn("alpha", scope)

    def test_an_empty_approve_does_not_invent_a_checked_opener(self):
        """The #1150 repro: schema 1.1 empty APPROVE used to pass substance by construction."""
        ballot = jury.Ballot(reviewer="gamma", verdict="LGTM")
        scope = jury.ballot_scope(ballot)
        self.assertNotIn("Checked", scope)
        self.assertIn("did not review", scope)
        self.assertFalse(jury.ballot_is_review(ballot))

    def test_a_long_file_list_is_capped_and_counted(self):
        ballot = jury.Ballot(
            reviewer="alpha",
            verdict="COMMENT",
            findings=tuple(
                {"severity": "nit", "path": f"src/f{i}.py", "line": None, "message": "m"}
                for i in range(10)
            ),
        )
        scope = jury.ballot_scope(ballot)
        self.assertIn("named 10 file(s)", scope)
        self.assertIn("(+2 more)", scope)
        self.assertNotIn("src/f9.py", scope)

    def test_a_scope_lists_each_file_once_and_skips_the_pathless(self):
        """A whole-diff finding names no file; a repeated file is still one file."""
        ballot = jury.Ballot(
            reviewer="alpha",
            verdict="COMMENT",
            findings=(
                {"severity": "nit", "path": None, "line": None, "message": "whole diff"},
                {"severity": "nit", "path": "src/a.py", "line": 1, "message": "one"},
                {"severity": "nit", "path": "src/a.py", "line": 9, "message": "two"},
            ),
        )

        scope = jury.ballot_scope(ballot)

        self.assertIn("named 1 file(s): src/a.py.", scope)

    def test_testing_reports_the_verification_round(self):
        panel = jury.parse_panel(BALLOT_REPORT)
        self.assertIn("upheld 1 consensus group(s)", jury.ballot_testing(panel.ballots[0]))
        self.assertIn("upheld no consensus group", jury.ballot_testing(panel.ballots[1]))

    def test_a_failed_adapter_is_stated_rather_than_hidden(self):
        ballot = jury.Ballot(reviewer="alpha", verdict="ABSTAIN", round1_ok=False)
        self.assertIn("adapter reported a failed run", jury.ballot_testing(ballot))

    def test_schema_12_scope_and_testing_are_preferred_over_derived_prose(self):
        ballot = jury.Ballot(
            reviewer="alpha",
            verdict="LGTM",
            counts_as_review=True,
            scope_substantive=True,
            scope="Checked `src/keel/review.py` as stated by the reviewer.",
            testing="Ran python -m unittest tests.test_review -v",
            findings=(
                {
                    "severity": "nit",
                    "path": "src/other.py",
                    "line": 1,
                    "message": "unused",
                },
            ),
        )
        self.assertEqual(
            jury.ballot_scope(ballot),
            "Checked `src/keel/review.py` as stated by the reviewer.",
        )
        self.assertEqual(jury.ballot_testing(ballot), "Ran python -m unittest tests.test_review -v")
        self.assertNotIn("src/other.py", jury.ballot_scope(ballot))


class TestReviewsBundle(unittest.TestCase):
    def test_a_ballot_becomes_a_review_record(self):
        record = jury.parse_panel(BALLOT_REPORT).reviews()[0]

        self.assertEqual(record["reviewer"], "alpha")
        self.assertEqual(record["verdict"], "REQUEST_CHANGES")
        self.assertEqual(record["vendor"], "anthropic")
        self.assertEqual(record["model"], "claude-opus-4")
        self.assertEqual(record["findings"][0]["path"], "src/keel/review.py")
        self.assertIn("Checked", record["scope"])
        self.assertIn("ai-jury verification", record["testing"])

    def test_every_panelist_gets_exactly_one_record(self):
        self.assertEqual(len(jury.parse_panel(BALLOT_REPORT).reviews()), 3)


class TestVerifiedFindings(unittest.TestCase):
    """Only what the verification round upheld may gate a merge."""

    def test_only_verified_consensus_groups_feed_the_fix_loop(self):
        found = jury.verified_findings(jury.parse_panel(BALLOT_REPORT))

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "major")
        self.assertEqual(found[0].source, "jury:alpha")
        self.assertEqual(found[0].path, "src/keel/review.py")
        self.assertTrue(found[0].anchorable)

    def test_a_group_without_reviewers_is_attributed_to_the_consensus(self):
        panel = jury.parse_panel(
            {
                "reviewers": [{"name": "a"}],
                "consensus": [
                    {
                        "representative": {"severity": "minor", "claim": "x"},
                        "verification_status": "verified",
                    },
                    "not-a-group",
                    {"representative": "not-a-finding", "verification_status": "verified"},
                ],
            }
        )
        found = jury.verified_findings(panel)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].source, "jury:consensus")
        self.assertFalse(found[0].anchorable)

    def test_non_string_reviewers_are_dropped_from_a_group(self):
        panel = jury.parse_panel(
            {
                "reviewers": [{"name": "a"}],
                "consensus": [
                    {
                        "representative": {"severity": "minor", "claim": "x"},
                        "reviewers": [5],
                        "verification_status": "verified",
                    }
                ],
            }
        )
        self.assertEqual(jury.verified_findings(panel)[0].source, "jury:consensus")

    def test_a_finding_without_a_claim_still_says_something(self):
        panel = jury.parse_panel(
            {
                "reviewers": [{"name": "a"}],
                "consensus": [
                    {"representative": {"severity": "nit"}, "verification_status": "verified"}
                ],
            }
        )
        self.assertEqual(jury.verified_findings(panel)[0].message, "(jury finding)")


class TestJuryVerdictRecord(unittest.TestCase):
    def test_the_record_declares_the_panel_it_came_from(self):
        record = jury.jury_verdict(jury.parse_panel(BALLOT_REPORT))

        self.assertEqual(record["verdict"], "REQUEST_CHANGES")
        self.assertEqual(record["panelists"], 3)
        self.assertEqual(record["participating_vendors"], 2)
        self.assertEqual(
            record["participants"],
            ["alpha (anthropic)", "beta (google)", "gamma (anthropic)"],
        )
        self.assertEqual(
            record["findings_summary"], ["major: the closure path drops the jury post"]
        )
        self.assertIsNone(record["remaining_risks"])

    def test_a_chairless_panel_abstains_rather_than_approving(self):
        panel = jury.parse_panel(
            {
                "findings": [{"file": "src/a.py", "claim": "x"}],
                "reviewers": [{"name": "a", "verdict": "APPROVE", "findings": [0]}],
            }
        )
        record = jury.jury_verdict(panel)

        self.assertEqual(record["verdict"], "ABSTAIN")
        self.assertEqual(record["participants"], ["a"])
        self.assertEqual(record["participating_vendors"], 0)
        self.assertEqual(record["remaining_risks"], "none identified")


class TestAbstentionsAreNotReviews(unittest.TestCase):
    """#1150: ABSTAIN / counts_as_review: false / empty findings must not look like reviews."""

    def test_an_abstain_ballot_is_dropped_from_the_panel_and_the_size(self):
        panel = jury.parse_panel(
            {
                "findings": [{"file": "src/a.py", "claim": "x"}],
                "reviewers": [
                    {"name": "alpha", "verdict": "APPROVE", "findings": [0], "vendor": "anthropic"},
                    {
                        "name": "beta",
                        "verdict": "ABSTAIN",
                        "findings": [0],
                        "vendor": "google",
                        "abstention_cause": "named_nothing",
                    },
                    {"name": "chair", "role": "chair", "verdict": "APPROVE"},
                ],
            }
        )
        self.assertEqual([b.reviewer for b in panel.ballots], ["alpha"])
        self.assertEqual(panel.size, 1)
        self.assertEqual(panel.vendors, ("anthropic",))
        self.assertEqual(len(panel.reviews()), 1)
        self.assertEqual(jury.jury_verdict(panel)["panelists"], 1)
        self.assertEqual(jury.jury_verdict(panel)["participants"], ["alpha (anthropic)"])

    def test_counts_as_review_false_is_dropped_even_with_paths_and_approve(self):
        panel = jury.parse_panel(
            {
                "schema_version": "1.2",
                "findings": [{"file": "src/a.py", "claim": "x"}],
                "reviewers": [
                    {
                        "name": "alpha",
                        "verdict": "APPROVE",
                        "findings": [0],
                        "counts_as_review": True,
                        "scope_substantive": True,
                        "scope": "Checked `src/a.py`.",
                    },
                    {
                        "name": "beta",
                        "verdict": "APPROVE",
                        "findings": [0],
                        "counts_as_review": False,
                        "scope_substantive": False,
                        "abstention_cause": "named_nothing",
                    },
                ],
            }
        )
        self.assertEqual([b.reviewer for b in panel.ballots], ["alpha"])
        self.assertEqual(panel.size, 1)

    def test_scope_substantive_false_fails_closed_without_counts_as_review(self):
        panel = jury.parse_panel(
            {
                "findings": [{"file": "src/a.py", "claim": "x"}],
                "reviewers": [
                    {
                        "name": "alpha",
                        "verdict": "APPROVE",
                        "findings": [0],
                        "scope_substantive": False,
                    }
                ],
            }
        )
        self.assertEqual(panel.ballots, ())

    def test_an_empty_findings_approve_on_an_old_report_is_dropped(self):
        panel = jury.parse_panel(
            {
                "schema_version": "1.1",
                "reviewers": [
                    {"name": "alpha", "verdict": "APPROVE", "findings": []},
                    {"name": "beta", "verdict": "COMMENT"},
                ],
            }
        )
        self.assertEqual(panel.ballots, ())
        self.assertEqual(panel.size, 0)

    def test_no_quorum_is_an_abstention_and_is_dropped(self):
        panel = jury.parse_panel({"reviewers": [{"name": "alpha", "verdict": "NO_QUORUM"}]})
        self.assertEqual(panel.ballots, ())

    def test_a_non_boolean_counts_as_review_is_ignored_and_empty_fails_closed(self):
        panel = jury.parse_panel(
            {"reviewers": [{"name": "alpha", "verdict": "APPROVE", "counts_as_review": 1}]}
        )
        self.assertEqual(panel.ballots, ())

    def test_abstention_scope_is_anchorless_and_fails_substance(self):
        from keel import artifacts, evidence

        ballot = jury.Ballot(
            reviewer="beta",
            verdict="ABSTAIN",
            abstention_cause="named_nothing",
            findings=({"severity": "nit", "path": "src/a.py", "line": 1, "message": "x"},),
        )
        scope = jury.ballot_scope(ballot)
        self.assertNotIn("Checked", scope)
        self.assertNotIn("src/a.py", scope)
        self.assertNotIn("`", scope)
        body = artifacts.render_review_verdict(
            reviewer=ballot.reviewer,
            head_sha="abc123",
            verdict=ballot.verdict,
            scope=scope,
            findings=[],
            testing="none",
        )
        ok, _reason = evidence.verdict_substance(body, pr_title="chore: unrelated")
        self.assertFalse(ok)

    def test_as_review_on_an_abstention_does_not_pass_substance(self):
        from keel import artifacts, evidence

        ballot = jury.Ballot(reviewer="beta", verdict="ABSTAIN")
        record = ballot.as_review()
        self.assertNotIn("Checked", record["scope"])
        body = artifacts.render_review_verdict(
            reviewer=record["reviewer"],
            head_sha="abc123",
            verdict=record["verdict"],
            scope=record["scope"],
            findings=record["findings"],
            testing=record["testing"],
        )
        ok, _reason = evidence.verdict_substance(body, pr_title="chore: unrelated")
        self.assertFalse(ok)

    def test_a_hand_built_panel_does_not_count_an_abstention_in_size_or_reviews(self):
        review = jury.Ballot(
            reviewer="alpha",
            verdict="LGTM",
            vendor="anthropic",
            findings=({"severity": "nit", "path": "src/a.py", "line": None, "message": "x"},),
        )
        abstain = jury.Ballot(reviewer="beta", verdict="ABSTAIN", vendor="google")
        panel = jury.Panel(ballots=(review, abstain))
        self.assertEqual(panel.size, 1)
        self.assertEqual(panel.vendors, ("anthropic",))
        self.assertEqual(len(panel.reviews()), 1)
        self.assertEqual(panel.reviews()[0]["reviewer"], "alpha")
        self.assertEqual(jury.jury_verdict(panel)["panelists"], 1)

    def test_counts_as_review_true_is_enough_without_scope_substantive(self):
        panel = jury.parse_panel(
            {
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "APPROVE",
                        "counts_as_review": True,
                        "scope": "Checked `src/a.py` as stated.",
                    }
                ]
            }
        )
        self.assertEqual(panel.size, 1)
        self.assertEqual(panel.ballots[0].scope, "Checked `src/a.py` as stated.")

    def test_scope_substantive_true_is_enough_without_counts_as_review(self):
        panel = jury.parse_panel(
            {
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "COMMENT",
                        "scope_substantive": True,
                        "scope": "Checked `src/a.py` as stated.",
                    }
                ]
            }
        )
        self.assertEqual(panel.size, 1)

    def test_abstain_wins_over_counts_as_review_true(self):
        panel = jury.parse_panel(
            {
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "ABSTAIN",
                        "counts_as_review": True,
                        "scope_substantive": True,
                        "scope": "Checked `src/a.py`.",
                    }
                ]
            }
        )
        self.assertEqual(panel.ballots, ())

    def test_a_declared_review_without_scope_or_paths_does_not_invent_checked(self):
        ballot = jury.Ballot(reviewer="a", verdict="LGTM", counts_as_review=True)
        self.assertTrue(jury.ballot_is_review(ballot))
        self.assertNotIn("Checked", jury.ballot_scope(ballot))

    def test_blank_and_non_string_scope_fields_are_treated_as_absent(self):
        panel = jury.parse_panel(
            {
                "findings": [{"file": "src/a.py", "claim": "x"}],
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "APPROVE",
                        "findings": [0],
                        "scope": "   ",
                        "testing": 5,
                        "abstention_cause": "",
                    }
                ],
            }
        )
        ballot = panel.ballots[0]
        self.assertIsNone(ballot.scope)
        self.assertIsNone(ballot.testing)
        self.assertIsNone(ballot.abstention_cause)
        self.assertIn("Checked", jury.ballot_scope(ballot))
        self.assertIn("src/a.py", jury.ballot_scope(ballot))

    def test_parsed_schema_12_fields_are_preserved_on_the_ballot(self):
        panel = jury.parse_panel(
            {
                "reviewers": [
                    {
                        "name": "a",
                        "verdict": "APPROVE",
                        "counts_as_review": True,
                        "scope_substantive": True,
                        "scope": "Checked `src/a.py`.",
                        "testing": "Ran the unit suite.",
                        "abstention_cause": "",
                    }
                ]
            }
        )
        ballot = panel.ballots[0]
        self.assertTrue(ballot.counts_as_review)
        self.assertTrue(ballot.scope_substantive)
        self.assertEqual(ballot.scope, "Checked `src/a.py`.")
        self.assertEqual(ballot.testing, "Ran the unit suite.")
        self.assertIsNone(ballot.abstention_cause)
        self.assertEqual(jury.ballot_testing(ballot), "Ran the unit suite.")


if __name__ == "__main__":
    unittest.main()
