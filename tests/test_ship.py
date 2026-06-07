"""Unit tests for the deterministic ship decisions."""

import unittest

from keel import ship
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
        self.assertEqual(contract["reviewers"]["project_additions"],
                         ["Check project rollout notes."])
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
        a = ship.assess(changed_files=[".github/workflows/ci.yml"], gate_verdict=CLEAN,
                        tier3_globs=TIER3, docs_globs=DOCS)
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

    def test_docs_only_tier1(self):
        a = ship.assess(changed_files=["docs/x.md"], gate_verdict=CLEAN,
                        tier3_globs=TIER3, docs_globs=DOCS)
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

    def test_outside_window_defers(self):
        from datetime import datetime
        night = datetime(2026, 6, 5, 3, 0)  # inside 01:30-07:00 no-merge
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN,
                        timezone="Europe/Istanbul", merge_window="07:00-01:30", now=night)
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
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, timezone=TZ,
                        merge_window=WIN, now=NIGHT, is_blocker=True)
        self.assertEqual(a.merge.action, "merge")
        self.assertTrue(a.bypassed_window)

    def test_hotfix_never_bypasses_findings(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=BLOCKED, timezone=TZ,
                        merge_window=WIN, now=NIGHT, is_blocker=True)
        self.assertEqual(a.merge.action, "block")
        self.assertFalse(a.bypassed_window)

    def test_no_bypass_flag_when_window_open(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, is_blocker=True)
        self.assertFalse(a.bypassed_window)


class TestPauseMode(unittest.TestCase):
    def test_pause_halts_outside_window(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, timezone=TZ,
                        merge_window=WIN, merge_window_mode="pause", now=NIGHT)
        self.assertTrue(a.halted)
        self.assertEqual(a.merge.action, "defer")

    def test_freeze_does_not_halt(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, timezone=TZ,
                        merge_window=WIN, merge_window_mode="freeze", now=NIGHT)
        self.assertFalse(a.halted)

    def test_pause_hotfix_not_halted(self):
        a = ship.assess(changed_files=["x.py"], gate_verdict=CLEAN, timezone=TZ,
                        merge_window=WIN, merge_window_mode="pause", now=NIGHT, is_blocker=True)
        self.assertFalse(a.halted)
        self.assertEqual(a.merge.action, "merge")


if __name__ == "__main__":
    unittest.main()
