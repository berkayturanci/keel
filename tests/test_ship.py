"""Unit tests for the deterministic ship decisions."""

import unittest

from keel import classify, evidence, ship
from keel import team as team_policy
from keel.findings import Finding, summarize

CLEAN = summarize([])
SOFT = summarize([Finding("minor", "x", "a"), Finding("nit", "y", "b")])
BLOCKED = summarize([Finding("major", "boom", "a")])


class TestReviewerCount(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(ship.reviewer_count(3), 3)
        self.assertEqual(ship.reviewer_count(2), 2)
        self.assertEqual(ship.reviewer_count(1), 1)

    def test_unknown_tier_defaults_to_two(self):
        self.assertEqual(ship.reviewer_count(0), 2)
        self.assertEqual(ship.reviewer_count(99), 2)

    def test_focuses_merge_when_reviewer_count_drops(self):
        one = ship.reviewer_focuses(1)
        self.assertEqual(one[0]["merged_from"], ["A", "B", "C"])
        self.assertIn("security", one[0]["focus"])

        two = ship.reviewer_focuses(2)
        self.assertEqual(two[0]["merged_from"], ["A", "B"])
        self.assertEqual(two[1]["merged_from"], ["C"])

    def test_reviewer_override_is_recorded(self):
        contract = ship.resolve_review_contract(tier=3, reviewer_override=2)
        self.assertEqual(contract["reviewers"]["count"], 2)
        self.assertEqual(contract["reviewers"]["source"], "override")
        self.assertEqual(contract["reviewers"]["tier"], 3)

    def test_unresolved_tier_is_explicit(self):
        contract = ship.resolve_review_contract(tier=None)
        self.assertEqual(contract["reviewers"]["source"], "unresolved")
        self.assertIsNone(contract["reviewers"]["tier"])


class TestJuryContract(unittest.TestCase):
    def test_tier3_auto_enables_gating_jury(self):
        contract = ship.resolve_review_contract(tier=3, gates=("build", "jury"))
        self.assertTrue(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["mode"], "gating")
        self.assertEqual(contract["jury"]["reason"], "tier-3 auto")
        self.assertTrue(contract["jury"]["configured_gate"])

    def test_no_jury_wins_over_jury(self):
        contract = ship.resolve_review_contract(tier=3, jury=True, no_jury=True)
        self.assertFalse(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["mode"], "off")
        self.assertTrue(contract["test_gates"]["no_jury_preserves_review_and_test_gates"])

    def test_jury_advisory_downgrades_enabled_jury(self):
        contract = ship.resolve_review_contract(tier=2, jury=True, jury_advisory=True)
        self.assertTrue(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["mode"], "advisory")
        self.assertFalse(contract["jury"]["verified_consensus_gates"])

    def test_review_posting_and_project_additions(self):
        contract = ship.resolve_review_contract(
            tier=2,
            review_comments="summary",
            policy_pack={
                "review": {
                    "additions": ["Check project rollout notes."],
                    "required_sections": ["Testing"],
                },
            },
        )
        self.assertEqual(contract["posting"]["mode"], "summary")
        self.assertEqual(
            contract["reviewers"]["project_additions"], ["Check project rollout notes."]
        )
        self.assertEqual(contract["reviewers"]["required_sections"], ["Testing"])

    def test_invalid_reviewer_override_rejected(self):
        with self.assertRaises(ValueError):
            ship.resolve_review_contract(tier=2, reviewer_override=4)

    def test_invalid_posting_mode_rejected(self):
        with self.assertRaises(ValueError):
            ship.resolve_review_contract(tier=2, review_comments="threaded")


class TestDecideMerge(unittest.TestCase):
    def test_block_on_findings(self):
        d = ship.decide_merge(BLOCKED, window_open=True)
        self.assertEqual(d.action, "block")

    def test_findings_block_even_for_blocker(self):
        d = ship.decide_merge(BLOCKED, window_open=False, is_blocker=True)
        self.assertEqual(d.action, "block")

    def test_merge_when_clear_and_open(self):
        d = ship.decide_merge(SOFT, window_open=True)
        self.assertEqual(d.action, "merge")
        self.assertEqual(d.reason, "clear to merge")

    def test_defer_outside_window(self):
        d = ship.decide_merge(CLEAN, window_open=False)
        self.assertEqual(d.action, "defer")

    def test_blocker_bypasses_window(self):
        d = ship.decide_merge(CLEAN, window_open=False, is_blocker=True)
        self.assertEqual(d.action, "merge")
        self.assertIn("bypass", d.reason)


class TestFixLoop(unittest.TestCase):
    def test_runs_while_blocked_and_budget(self):
        self.assertTrue(ship.should_run_fixloop(BLOCKED, current_round=0))
        self.assertTrue(ship.should_run_fixloop(BLOCKED, current_round=2))

    def test_stops_at_cap(self):
        self.assertFalse(ship.should_run_fixloop(BLOCKED, current_round=3))

    def test_stops_when_clear(self):
        self.assertFalse(ship.should_run_fixloop(CLEAN, current_round=0))


class TestCiRan(unittest.TestCase):
    """ "Everything passed" and "nothing ran" are different facts (#675)."""

    def test_empty_rollup_means_nothing_ran(self):
        self.assertIs(ship.ci_ran(""), False)
        self.assertIs(ship.ci_ran("   "), False)

    def test_unknown_when_gh_could_not_be_asked(self):
        # Also the no-PR-supplied case. keel blocks on having observed nothing,
        # not on having been unable to observe.
        self.assertIsNone(ship.ci_ran(None))

    def test_any_conclusion_means_something_ran(self):
        for conclusion in ("SUCCESS", "FAILURE", "SUCCESS,FAILURE", "PENDING"):
            with self.subTest(conclusion=conclusion):
                self.assertIs(ship.ci_ran(conclusion), True)


class TestMissingCiWorkflows(unittest.TestCase):
    """Presence checked against the project's declaration, not inferred (#675)."""

    WORKFLOWS = {"CI": "**", "CodeQL": "**"}

    def test_none_missing_when_all_declared_reported(self):
        self.assertEqual(ship.missing_ci_workflows(["CI", "CodeQL"], self.WORKFLOWS), ())

    def test_names_a_declared_workflow_that_never_ran(self):
        self.assertEqual(ship.missing_ci_workflows(["CI"], self.WORKFLOWS), ("CodeQL",))

    def test_job_names_are_the_wrong_input_and_this_is_why(self):
        """Regression guard for a false block that would have hit every keel PR.

        `ci_workflows` is keyed "CI"; the rollup reports job names like
        "test (py3.13 / ubuntu-latest)". Feeding job names here reports the
        workflow missing even though it ran, which is why the caller must pass
        github.ci_workflow_names (workflowName) and not ci_check_names.
        """
        jobs = ["test (py3.13 / ubuntu-latest)", "Analyze (Python)"]
        self.assertEqual(ship.missing_ci_workflows(jobs, {"CI": "**"}), ("CI",))
        self.assertEqual(ship.missing_ci_workflows(["CI", "CodeQL"], {"CI": "**"}), ())

    def test_matching_is_exact_not_a_prefix(self):
        # A prefix rule would let an unrelated "testing-utils" satisfy "test".
        self.assertEqual(ship.missing_ci_workflows(["testing-utils"], {"test": "**"}), ("test",))

    def test_case_insensitive(self):
        self.assertEqual(ship.missing_ci_workflows(["ci", "codeql"], self.WORKFLOWS), ())

    def test_everything_missing_when_nothing_ran(self):
        self.assertEqual(ship.missing_ci_workflows([], self.WORKFLOWS), ("CI", "CodeQL"))

    def test_no_declaration_means_no_finding(self):
        # Absence of a declaration is not evidence of a missing run.
        self.assertEqual(ship.missing_ci_workflows([], None), ())
        self.assertEqual(ship.missing_ci_workflows([], {}), ())

    def test_unreadable_names_means_no_finding(self):
        self.assertEqual(ship.missing_ci_workflows(None, self.WORKFLOWS), ())

    def test_blank_reported_names_are_ignored(self):
        self.assertEqual(ship.missing_ci_workflows(["  ", "CI", "CodeQL"], self.WORKFLOWS), ())


class TestCiPassing(unittest.TestCase):
    def test_unknown(self):
        self.assertIsNone(ship.ci_passing(None))
        self.assertIsNone(ship.ci_passing(""))
        self.assertIsNone(ship.ci_passing("  ,  "))

    def test_passing(self):
        self.assertTrue(ship.ci_passing("SUCCESS"))
        self.assertTrue(ship.ci_passing("SUCCESS,NEUTRAL,SKIPPED"))
        self.assertTrue(ship.ci_passing("success"))

    def test_failing(self):
        self.assertFalse(ship.ci_passing("FAILURE"))
        self.assertFalse(ship.ci_passing("SUCCESS,FAILURE"))
        self.assertFalse(ship.ci_passing("TIMED_OUT"))


TIER3 = (".github/workflows/**",)
DOCS = ("docs/**", "*.md")


class TestAssess(unittest.TestCase):
    def test_tier3_three_reviewers_and_merge(self):
        a = ship.assess(
            changed_files=[".github/workflows/ci.yml"],
            gate_verdict=CLEAN,
            tier3_globs=TIER3,
            docs_globs=DOCS,
        )
        self.assertEqual(a.tier, 3)
        self.assertEqual(a.reviewers, 3)
        self.assertEqual(a.review_contract["jury"]["mode"], "gating")
        self.assertTrue(a.window_open)  # no window configured -> always open
        self.assertEqual(a.merge.action, "merge")

    def test_override_reviewers_without_dropping_contract(self):
        a = ship.assess(
            changed_files=[".github/workflows/ci.yml"],
            gate_verdict=CLEAN,
            tier3_globs=TIER3,
            docs_globs=DOCS,
            reviewer_override=1,
            review_comments="summary",
            no_jury=True,
        )
        self.assertEqual(a.tier, 3)
        self.assertEqual(a.reviewers, 1)
        self.assertEqual(a.review_contract["reviewers"]["count"], 1)
        self.assertEqual(a.review_contract["posting"]["mode"], "summary")
        self.assertEqual(a.review_contract["jury"]["mode"], "off")

    def test_unreadable_changeset_classifies_fail_closed(self):
        # `None` is git.changed_files' "could not read", distinct from `[]`. It must
        # not borrow the empty-changeset answer: the default tier asks for fewer
        # reviewers and turns the jury off on a change nobody has seen.
        unreadable = ship.assess(
            changed_files=None, gate_verdict=CLEAN, tier3_globs=TIER3, docs_globs=DOCS
        )
        empty = ship.assess(
            changed_files=[], gate_verdict=CLEAN, tier3_globs=TIER3, docs_globs=DOCS
        )
        self.assertEqual(unreadable.tier, 3)
        self.assertEqual(unreadable.reviewers, 3)
        self.assertEqual(unreadable.review_contract["jury"]["mode"], "gating")
        self.assertEqual(empty.tier, 2)

    def test_a_required_gate_nobody_ran_blocks_the_merge(self):
        # `record_gates_passed` refuses to certify such a record, so reporting
        # "clear to merge" would promise a merge `keel merge` then refuses, with no
        # reason given to the operator.
        blocked = ship.assess(
            changed_files=["x.py"], gate_verdict=CLEAN, unrun_blocking_gates=("security-review",)
        )
        self.assertEqual(blocked.merge.action, "block")
        self.assertIn("security-review", blocked.merge.reason)
        self.assertIn("--gate-result", blocked.merge.reason)

    def test_advisory_gates_that_did_not_run_do_not_block(self):
        self.assertEqual(
            ship.assess(changed_files=["x.py"], gate_verdict=CLEAN).merge.action, "merge"
        )

    def test_blocking_findings_outrank_an_unrun_gate_in_the_reason(self):
        blocked = ship.assess(
            changed_files=["x.py"], gate_verdict=BLOCKED, unrun_blocking_gates=("security-review",)
        )
        self.assertEqual(blocked.merge.action, "block")
        self.assertEqual(blocked.merge.reason, "blocking findings from gate(s): a")

    def test_the_block_reason_names_the_gates_whose_findings_block(self):
        # "blocking findings present" next to a reviewer's "none blocking" read as a
        # contradiction (#1007): the findings were a failed lint gate's, and the one
        # line the operator reads did not say so.
        verdict = summarize(
            [
                Finding("major", "gate 'lint' failed", "lint"),
                Finding("critical", "gate 'build' failed", "build"),
                Finding("major", "again", "lint"),  # deduplicated
                Finding("minor", "style", "bandit"),  # not blocking: not named
            ]
        )
        self.assertEqual(ship.blocking_sources(verdict), ("build", "lint"))
        self.assertEqual(
            ship.decide_merge(verdict, window_open=True).reason,
            "blocking findings from gate(s): build, lint",
        )

    def test_jury_findings_name_the_jury_gate_once_not_each_reviewer(self):
        # The built-in jury gate writes `jury:<reviewer>` and `jury:consensus` as the
        # source (src/keel/jury.py). One gate with several voices must not render as
        # three failed gates — the gate is what the operator can act on.
        verdict = summarize(
            [
                Finding("major", "reviewer a: boom", "jury:reviewer-a"),
                Finding("critical", "reviewer b: boom", "jury:reviewer-b"),
                Finding("major", "consensus: boom", "jury:consensus"),
                Finding("major", "gate 'lint' failed", "lint"),
            ]
        )
        self.assertEqual(ship.blocking_sources(verdict), ("jury", "lint"))
        self.assertEqual(ship.block_reason(verdict), "blocking findings from gate(s): jury, lint")

    def test_a_colon_in_an_extension_gate_id_is_kept_whole(self):
        # Nothing validates extension ids beyond non-empty, so `sec:scan` is a legal
        # blocking gate; splitting every source on ':' would name a gate `sec` that
        # does not exist (narrowed review of #1008).
        verdict = summarize([Finding("major", "gate 'sec:scan' failed", "sec:scan")])
        self.assertEqual(ship.blocking_sources(verdict), ("sec:scan",))

    def test_a_blocked_verdict_with_no_attributable_source_keeps_the_old_reason(self):
        verdict = summarize([Finding("major", "boom", "")])
        self.assertTrue(verdict.blocked)
        self.assertEqual(ship.blocking_sources(verdict), ())
        self.assertEqual(ship.block_reason(verdict), "blocking findings present")

    def test_a_clean_verdict_has_no_blocking_sources(self):
        self.assertEqual(ship.blocking_sources(SOFT), ())

    def test_docs_only_tier1(self):
        a = ship.assess(
            changed_files=["docs/x.md"], gate_verdict=CLEAN, tier3_globs=TIER3, docs_globs=DOCS
        )
        self.assertEqual(a.tier, 1)
        self.assertEqual(a.reviewers, 1)

    def test_blocking_findings_block(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=BLOCKED)
        self.assertEqual(a.merge.action, "block")

    def test_ci_failing_blocks(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, ci_conclusion="FAILURE")
        self.assertEqual(a.ci_ok, False)
        self.assertEqual(a.merge.action, "block")
        self.assertEqual(a.merge.reason, "CI failing")

    def test_ci_passing_merges(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, ci_conclusion="SUCCESS")
        self.assertTrue(a.ci_ok)
        self.assertEqual(a.merge.action, "merge")

    def test_zero_checks_blocks_and_says_so(self):
        """The #675 regression: a PR nothing ran on used to assess as clear to merge.

        The reason string matters as much as the block — an operator has to be able
        to tell "nothing verified this commit" from "a check went red", and the
        assessment is written into the run ledger as evidence.
        """
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, ci_conclusion="")
        self.assertIs(a.ci_ran, False)
        self.assertEqual(a.merge.action, "block")
        self.assertIn("no CI ran", a.merge.reason)
        self.assertNotEqual(a.merge.reason, "CI failing")

    def test_docs_only_with_zero_checks_still_merges(self):
        """The carve-out `keel merge` already applies, mirrored so the two agree.

        A docs-only change legitimately matches no workflow path filter, and
        cli._ci_state's `no-checks` branch lets it through. An assessment that
        blocked it would contradict the gate it exists to predict.
        """
        a = ship.assess(
            changed_files=["docs/a.md"],
            gate_verdict=CLEAN,
            ci_conclusion="",
            ci_check_names=[],
            docs_globs=("docs/**", "*.md"),
        )
        self.assertIs(a.ci_ran, False)
        self.assertEqual(a.merge.action, "merge")

    def test_docs_only_does_not_report_declared_workflows_as_missing(self):
        a = ship.assess(
            changed_files=["docs/a.md"],
            gate_verdict=CLEAN,
            ci_conclusion="",
            ci_check_names=[],
            docs_globs=("docs/**", "*.md"),
            ci_workflows={"CI": "**"},
        )
        self.assertEqual(a.missing_workflows, ())
        self.assertEqual(a.merge.action, "merge")

    def test_an_unreadable_changeset_with_zero_checks_still_blocks(self):
        # is_docs_only fails closed on an empty/unknown changeset, so the
        # carve-out cannot be reached by simply failing to read the diff.
        a = ship.assess(changed_files=None, gate_verdict=CLEAN, ci_conclusion="")
        self.assertEqual(a.merge.action, "block")

    def test_zero_checks_is_not_confused_with_gh_being_unavailable(self):
        # Unreadable CI stays non-blocking: keel blocks on having observed
        # nothing, not on having been unable to observe.
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, ci_conclusion=None)
        self.assertIsNone(a.ci_ran)
        self.assertEqual(a.merge.action, "merge")

    def test_a_failing_check_still_reports_as_failing_not_as_unrun(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            ci_conclusion="FAILURE",
            ci_check_names=["CI"],
        )
        self.assertIs(a.ci_ran, True)
        self.assertEqual(a.merge.reason, "CI failing")

    def test_declared_workflow_that_never_ran_blocks(self):
        """Green is not enough when a workflow the project declares produced no run."""
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            ci_conclusion="SUCCESS",
            ci_check_names=["CI"],
            ci_workflow_names=["CI"],
            ci_workflows={"CI": "**", "CodeQL": "**"},
        )
        self.assertTrue(a.ci_ok)
        self.assertEqual(a.missing_workflows, ("CodeQL",))
        self.assertEqual(a.merge.action, "block")
        self.assertIn("CodeQL", a.merge.reason)

    def test_all_declared_workflows_present_merges(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            ci_conclusion="SUCCESS",
            ci_check_names=["test (py3.13 / ubuntu-latest)", "CodeQL"],
            ci_workflow_names=["CI", "CodeQL"],
            ci_workflows={"CI": "**", "CodeQL": "**"},
        )
        self.assertEqual(a.missing_workflows, ())
        self.assertEqual(a.merge.action, "merge")

    def test_a_red_check_outranks_a_missing_workflow_in_the_reason(self):
        # Both are true; the operator should be told about the failure first.
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            ci_conclusion="FAILURE",
            ci_check_names=["CI"],
            ci_workflow_names=["CI"],
            ci_workflows={"CI": "**", "CodeQL": "**"},
        )
        self.assertEqual(a.merge.action, "block")
        self.assertEqual(a.merge.reason, "CI failing")

    def test_outside_window_defers(self):
        from datetime import datetime

        night = datetime(2026, 6, 5, 3, 0)  # inside 01:30-07:00 no-merge
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            timezone="Europe/Istanbul",
            merge_window="07:00-01:30",
            now=night,
        )
        self.assertFalse(a.window_open)
        self.assertEqual(a.merge.action, "defer")
        self.assertFalse(a.halted)


from datetime import datetime  # noqa: E402

NIGHT = datetime(2026, 6, 5, 3, 0)
TZ = "Europe/Istanbul"
WIN = "07:00-01:30"


class TestHotfix(unittest.TestCase):
    def test_is_hotfix(self):
        self.assertTrue(ship.is_hotfix(["bug", "Hotfix"]))
        self.assertTrue(ship.is_hotfix(["hotfix"]))
        self.assertFalse(ship.is_hotfix(["bug", "feature"]))
        self.assertFalse(ship.is_hotfix([]))

    def test_hotfix_bypasses_closed_window(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            timezone=TZ,
            merge_window=WIN,
            now=NIGHT,
            is_blocker=True,
        )
        self.assertEqual(a.merge.action, "merge")
        self.assertTrue(a.bypassed_window)

    def test_hotfix_never_bypasses_findings(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=BLOCKED,
            timezone=TZ,
            merge_window=WIN,
            now=NIGHT,
            is_blocker=True,
        )
        self.assertEqual(a.merge.action, "block")
        self.assertFalse(a.bypassed_window)

    def test_no_bypass_flag_when_window_open(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, is_blocker=True)
        self.assertFalse(a.bypassed_window)


class TestPauseMode(unittest.TestCase):
    def test_pause_halts_outside_window(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            timezone=TZ,
            merge_window=WIN,
            merge_window_mode="pause",
            now=NIGHT,
        )
        self.assertTrue(a.halted)
        self.assertEqual(a.merge.action, "defer")

    def test_freeze_does_not_halt(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            timezone=TZ,
            merge_window=WIN,
            merge_window_mode="freeze",
            now=NIGHT,
        )
        self.assertFalse(a.halted)

    def test_pause_hotfix_not_halted(self):
        a = ship.assess(
            changed_files=["x.py"],
            gate_verdict=CLEAN,
            timezone=TZ,
            merge_window=WIN,
            merge_window_mode="pause",
            now=NIGHT,
            is_blocker=True,
        )
        self.assertFalse(a.halted)
        self.assertEqual(a.merge.action, "merge")


class TestJuryPanelDowngrade(unittest.TestCase):
    """A gating jury must reflect the panel that actually ran, not just the tier."""

    def test_unknown_panel_leaves_gating_alone(self):
        # None = not resolved yet (planning, `keel plan`, any pre-s8 caller).
        jury = ship.resolve_jury(tier=3)

        self.assertEqual(jury["mode"], "gating")
        self.assertFalse(jury["downgraded"])
        self.assertIsNone(jury["participating_vendors"])

    def test_single_vendor_panel_downgrades_to_advisory(self):
        jury = ship.resolve_jury(tier=3, participating_vendors=1)

        self.assertEqual(jury["mode"], "advisory")
        self.assertTrue(jury["downgraded"])
        self.assertFalse(jury["verified_consensus_gates"])
        self.assertTrue(jury["enabled"])

    def test_zero_participants_downgrade_too(self):
        # "A jury that did not complete cleanly never gates" is the same
        # comparison: no agent returned output means zero vendors participated.
        jury = ship.resolve_jury(tier=3, participating_vendors=0)

        self.assertEqual(jury["mode"], "advisory")
        self.assertTrue(jury["downgraded"])

    def test_minimum_vendors_gates(self):
        jury = ship.resolve_jury(tier=3, participating_vendors=ship.MINIMUM_JURY_VENDORS)

        self.assertEqual(jury["mode"], "gating")
        self.assertFalse(jury["downgraded"])
        self.assertTrue(jury["verified_consensus_gates"])

    def test_minimum_vendors_constant_is_reported(self):
        # The constant used to be written and never read; the reported value and
        # the enforced threshold must be the same thing.
        self.assertEqual(
            ship.resolve_jury(tier=3)["minimum_vendors"],
            ship.MINIMUM_JURY_VENDORS,
        )

    def test_reason_records_the_downgrade(self):
        reason = ship.resolve_jury(tier=3, participating_vendors=1)["reason"]

        self.assertIn("tier-3 auto", reason)
        self.assertIn("downgraded to advisory", reason)
        self.assertIn("minimum 2", reason)

    def test_disabled_jury_is_not_downgraded(self):
        # Nothing to downgrade, and `downgraded` must not imply "was gating".
        jury = ship.resolve_jury(tier=3, no_jury=True, participating_vendors=0)

        self.assertEqual(jury["mode"], "off")
        self.assertFalse(jury["downgraded"])

    def test_explicit_advisory_is_not_reported_as_a_downgrade(self):
        jury = ship.resolve_jury(tier=3, jury_advisory=True, participating_vendors=0)

        self.assertEqual(jury["mode"], "advisory")
        self.assertFalse(jury["downgraded"])

    def test_contract_threads_the_panel_through(self):
        contract = ship.resolve_review_contract(tier=3, jury_participating_vendors=1)

        self.assertEqual(contract["jury"]["mode"], "advisory")
        self.assertEqual(contract["jury"]["participating_vendors"], 1)

    def test_short_panel_drops_the_evidence_requirement(self):
        # The point of the change: the gate stops demanding a gating verdict the
        # jury step would decline to treat as gating.
        short = evidence.required_items(
            ship.resolve_review_contract(tier=3, jury_participating_vendors=1),
            phase=evidence.PHASE_PRE_MERGE,
        )
        full = evidence.required_items(
            ship.resolve_review_contract(tier=3, jury_participating_vendors=2),
            phase=evidence.PHASE_PRE_MERGE,
        )

        self.assertNotIn("jury-verdict", [item.id for item in short])
        self.assertIn("jury-verdict", [item.id for item in full])


class TestAssessTierReadsTheDiff(unittest.TestCase):
    """`assess` must classify from the same evidence the evidence gate uses (#845).

    `keel ship` resolves the tier twice: once for the review contract, with the
    diff, and once inside `assess`, which had no way to see it. So a workflow
    change touching nothing privileged was TIER-2 to the gate and TIER-3 to the
    assessment comment a human reads — the number shown was not the number
    enforced, and it sent maintainers to arrange a third reviewer and a paid
    gating jury that nothing would check.
    """

    WORKFLOW = ".github/workflows/publish.yml"
    GLOBS = (".github/workflows/**",)

    def _tier(self, patches):
        return ship.assess(
            changed_files=[self.WORKFLOW],
            gate_verdict=summarize(()),
            tier3_globs=self.GLOBS,
            patches=patches,
        ).tier

    def test_non_privileged_workflow_diff_downgrades(self):
        patch = "@@\n-          exit 1\n+          echo notice\n"
        self.assertEqual(self._tier({self.WORKFLOW: patch}), 2)

    def test_privileged_workflow_diff_stays_tier3(self):
        patch = "@@\n+permissions:\n+  contents: write\n"
        self.assertEqual(self._tier({self.WORKFLOW: patch}), 3)

    def test_no_patches_keeps_the_path_deciding(self):
        # No diff is no evidence; the path decides, exactly as before #845. This is
        # what every caller that cannot read a diff still relies on.
        self.assertEqual(self._tier(None), 3)

    def test_agrees_with_the_gate_on_the_same_inputs(self):
        # The property that actually failed: both sides given the same diff must
        # reach the same tier. Asserting equality rather than two literals means a
        # future change to the classifier cannot split them again unnoticed.
        patch = "@@\n-          exit 1\n+          echo notice\n"
        patches = {self.WORKFLOW: patch}
        gate_tier = classify.tier_for_files(
            [self.WORKFLOW], tier3_globs=self.GLOBS, patches=patches
        )
        self.assertEqual(self._tier(patches), gate_tier)


TEAM = team_policy.parse_team(
    {
        "implement": {"by_role": {"core": {"provider": "agy", "model": "gemini-3.8-flash-high"}}},
        "gate": {"provider": "codex", "distinct_from": "implementer"},
        "review": {
            "by_tier": {
                "2": [{"provider": "claude"}, {"provider": "codex"}],
                "3": "jury",
            }
        },
        "jury": {"mode": "gating", "min_vendors": 3},
    }
)


class TestTeamAssignment(unittest.TestCase):
    """The resolved `knobs.team` team and the review contract must agree (#1014)."""

    def _assignment(self, tier, *, no_jury=False, jury_advisory=False, **kwargs):
        return team_policy.resolve_assignment(
            TEAM,
            tier=tier,
            default_count=ship.reviewer_count(tier),
            jury_disabled=no_jury,
            jury_advisory=jury_advisory,
            **kwargs,
        )

    def test_the_contract_takes_its_bench_from_the_assignment(self):
        assignment = self._assignment(2, role="core")

        contract = ship.resolve_review_contract(tier=2, assignment=assignment)

        reviewers = contract["reviewers"]
        self.assertEqual(reviewers["count"], 2)
        self.assertEqual(reviewers["panel"], "reviewers")
        self.assertEqual(reviewers["source"], "team.review.by_tier.2")
        self.assertEqual([slot["provider"] for slot in reviewers["slots"]], ["claude", "codex"])
        self.assertEqual(
            [slot["slot"] for slot in reviewers["slots"]],
            [focus["slot"] for focus in reviewers["focuses"]],
        )

    def test_a_jury_tier_leaves_no_host_reviewers_and_gates_on_the_panel(self):
        contract = ship.resolve_review_contract(tier=3, assignment=self._assignment(3))

        self.assertEqual(contract["reviewers"]["count"], 0)
        self.assertEqual(contract["reviewers"]["panel"], "jury")
        self.assertEqual(contract["reviewers"]["slots"], [])
        self.assertEqual(contract["reviewers"]["focuses"], [])
        self.assertEqual(contract["reviewers"]["minimum_lgtm"], 0)
        self.assertEqual(contract["jury"]["mode"], "gating")
        self.assertTrue(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["reason"], "team.review panel")
        self.assertEqual(contract["jury"]["minimum_vendors"], 3)

    def test_the_panel_enables_the_jury_below_tier_three(self):
        policy = team_policy.parse_team({"review": {"by_tier": {"2": "jury"}}})
        assignment = team_policy.resolve_assignment(policy, tier=2, default_count=2)

        contract = ship.resolve_review_contract(tier=2, assignment=assignment)

        self.assertTrue(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["reason"], "team.review panel")

    def test_the_panel_outranks_every_per_run_jury_flag(self):
        """At a jury tier the panel *is* the review, so no flag may remove it.

        There are no host reviewer slots to fall back on, so `--no-jury` or
        `--jury-advisory` there would leave the tier with no required review evidence at
        all — a stricter policy producing a weaker gate. It is also the only answer the
        six commands resolving this contract can agree on: `keel review` has no
        `--no-jury`, and keel's CI passes it to `evidence-verify` and to nothing else.
        """
        for flags in (
            {"no_jury": True},
            {"jury_advisory": True},
            {"no_jury": True, "jury_advisory": True},
        ):
            with self.subTest(**flags):
                contract = ship.resolve_review_contract(
                    tier=3, assignment=self._assignment(3, **flags), **flags
                )

                self.assertTrue(contract["jury"]["enabled"])
                self.assertEqual(contract["jury"]["mode"], "gating")
                self.assertEqual(contract["reviewers"]["count"], 0)
                self.assertIn("does not apply", contract["jury"]["reason"])

    def test_no_jury_keeps_its_meaning_below_a_panel_tier(self):
        contract = ship.resolve_review_contract(
            tier=3, assignment=self._assignment(3), no_jury=True
        )
        plain = ship.resolve_review_contract(tier=3, no_jury=True)

        # The fixture's tier-3 *is* a panel, so the flag is recorded, not applied…
        self.assertEqual(contract["jury"]["mode"], "gating")
        # …while a project without a jury panel keeps the documented precedence.
        self.assertFalse(plain["jury"]["enabled"])
        self.assertEqual(plain["jury"]["mode"], "off")

    def test_the_policy_can_soften_a_jury_it_did_not_make_the_panel(self):
        policy = team_policy.parse_team({"jury": {"mode": "advisory"}})
        assignment = team_policy.resolve_assignment(policy, tier=3, default_count=3)

        contract = ship.resolve_review_contract(tier=3, assignment=assignment)

        # Tier-3 auto-enables the jury; the project's own mode makes it report-only.
        self.assertTrue(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["mode"], "advisory")
        # …and the bench is untouched: there are still three host reviewers behind it.
        self.assertEqual(contract["reviewers"]["count"], 3)

    def test_a_raised_minimum_downgrades_a_panel_that_met_the_old_floor(self):
        contract = ship.resolve_review_contract(
            tier=3, assignment=self._assignment(3), jury_participating_vendors=2
        )

        self.assertEqual(contract["jury"]["mode"], "advisory")
        self.assertIn("minimum 3", contract["jury"]["reason"])

    def test_distinct_vendors_defaults_on_from_tier_two(self):
        for tier, expected in ((1, False), (2, True), (3, True), (None, False)):
            with self.subTest(tier=tier):
                contract = ship.resolve_review_contract(tier=tier)
                self.assertIs(contract["reviewers"]["require_distinct_vendors"], expected)

    def test_an_explicit_false_is_still_honoured(self):
        contract = ship.resolve_review_contract(tier=3, require_distinct_vendors=False)

        self.assertFalse(contract["reviewers"]["require_distinct_vendors"])

    def test_assess_resolves_the_assignment_against_the_classified_tier(self):
        assessment = ship.assess(
            changed_files=["src/keel/orchestrator.py"],
            gate_verdict=CLEAN,
            tier3_globs=("src/keel/orchestrator.py",),
            ci_conclusion="SUCCESS",
            team=TEAM,
            role="core",
            legacy_agents={"core": team_policy.Seat(provider="subagent:backend-developer")},
        )

        self.assertEqual(assessment.tier, 3)
        self.assertEqual(assessment.reviewers, 0)
        self.assertEqual(assessment.assignment["review_panel"], "jury")
        self.assertEqual(assessment.assignment["implementer"]["provider"], "agy")
        self.assertTrue(assessment.assignment["gate"]["distinct_ok"])
        self.assertTrue(assessment.review_contract["reviewers"]["require_distinct_vendors"])

    def test_an_out_of_range_override_raises_before_the_team_is_resolved(self):
        """The documented ValueError, not an IndexError from inside the resolver.

        `assess` resolves the assignment first, so the range guard has to run ahead of it
        — a caller catching `ValueError` for a bad `--reviewers` was getting an
        `IndexError` out of the slot labelling instead.
        """
        with self.assertRaises(ValueError) as ctx:
            ship.assess(changed_files=["a.py"], gate_verdict=CLEAN, reviewer_override=5, team=TEAM)

        self.assertIn("must be one of 1, 2, or 3", str(ctx.exception))

    def test_assess_without_a_team_keeps_the_tier_derived_bench(self):
        assessment = ship.assess(
            changed_files=["README.md"],
            gate_verdict=CLEAN,
            ci_conclusion="SUCCESS",
            docs_globs=("*.md",),
        )

        self.assertEqual(assessment.reviewers, 1)
        self.assertFalse(assessment.assignment["configured"])
        self.assertEqual(assessment.assignment["implementer"]["provider"], "claude")


if __name__ == "__main__":
    unittest.main()
