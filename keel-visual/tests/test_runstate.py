"""Tests for the pure run-state adapter."""

import unittest

from keel_visual import runstate as rs


def _record(*, action="merge", critical=0, major=0, minor=0, nit=0, issue=351, pr=361):
    return {
        "record_type": "ship_run",
        "issue": {"number": issue},
        "pull_request": {"number": pr},
        "assessment": {"merge": {"action": action, "reason": "r"}},
        "verdict": {"blocked": False, "counts": {
            "critical": critical, "major": major, "minor": minor, "nit": nit}},
    }


class TestHelpers(unittest.TestCase):
    def test_step_index_known_and_unknown(self):
        self.assertEqual(rs.step_index("s0"), 0)
        self.assertEqual(rs.step_index("s10"), 10)
        self.assertIsNone(rs.step_index("sX"))
        self.assertIsNone(rs.step_index(None))

    def test_worst_finding_precedence(self):
        self.assertEqual(rs.worst_finding({"critical": 1}), "critical")
        self.assertEqual(rs.worst_finding({"major": 2}), "major")
        self.assertEqual(rs.worst_finding({"minor": 1}), "minor")
        self.assertEqual(rs.worst_finding({}), "none")

    def test_merge_action_malformed(self):
        self.assertIsNone(rs._merge_action({"assessment": "x"}))
        self.assertIsNone(rs._merge_action({"assessment": {"merge": "x"}}))
        self.assertIsNone(rs._merge_action({"assessment": {"merge": {"action": 5}}}))

    def test_verdict_counts_clamps_and_defaults(self):
        self.assertEqual(rs._verdict_counts({}), {"critical": 0, "major": 0, "minor": 0, "nit": 0})
        self.assertEqual(
            rs._verdict_counts({"verdict": {"counts": {"critical": 2, "minor": -1, "major": "x"}}}),
            {"critical": 2, "major": 0, "minor": 0, "nit": 0},
        )


class TestBuildRunState(unittest.TestCase):
    def test_none_record_starts_at_s0(self):
        st = rs.build_run_state(None)
        self.assertEqual(st["active_index"], 0)
        self.assertEqual(st["active_id"], "s0")
        self.assertFalse(st["merged"])
        self.assertEqual(len(st["steps"]), 13)
        self.assertIsNone(st["issue"])

    def test_merged_record_sits_at_close(self):
        st = rs.build_run_state(_record(action="merge"))
        self.assertTrue(st["merged"])
        self.assertEqual(st["active_id"], "s12")
        self.assertEqual(st["issue"], 351)
        self.assertEqual(st["pr"], 361)

    def test_unmerged_record_sits_at_merge(self):
        st = rs.build_run_state(_record(action="defer"))
        self.assertFalse(st["merged"])
        self.assertEqual(st["active_id"], "s10")

    def test_checkpoint_step_overrides(self):
        st = rs.build_run_state(_record(action="merge"), checkpoint_step="s8")
        self.assertEqual(st["active_id"], "s8")

    def test_step_statuses_and_kinds(self):
        st = rs.build_run_state(_record(action="defer"), checkpoint_step="s8")
        by = {s["id"]: s for s in st["steps"]}
        self.assertEqual(by["s7"]["status"], "done")
        self.assertEqual(by["s8"]["status"], "gate")
        self.assertEqual(by["s9"]["status"], "pending")
        self.assertEqual(by["s9"]["kind"], "loop")
        self.assertEqual(by["s10"]["kind"], "merge")
        self.assertEqual(by["s4"]["kind"], "normal")

    def test_loop_status_when_active(self):
        st = rs.build_run_state(_record(action="defer"), checkpoint_step="s9")
        by = {s["id"]: s for s in st["steps"]}
        self.assertEqual(by["s9"]["status"], "loop")

    def test_gate_block_on_major(self):
        st = rs.build_run_state(_record(action="defer", major=1), checkpoint_step="s8")
        s8 = next(s for s in st["steps"] if s["id"] == "s8")
        self.assertEqual(s8["gate"]["outcome"], "fail")
        self.assertEqual(s8["gate"]["worst"], "major")

    def test_gate_pass_when_clean(self):
        st = rs.build_run_state(_record(action="defer"), checkpoint_step="s8")
        s8 = next(s for s in st["steps"] if s["id"] == "s8")
        self.assertEqual(s8["gate"]["outcome"], "pass")

    def test_merge_gate_outcome_tracks_merged(self):
        merged = rs.build_run_state(_record(action="merge"))
        s10m = next(s for s in merged["steps"] if s["id"] == "s10")
        self.assertEqual(s10m["gate"]["outcome"], "pass")
        unm = rs.build_run_state(_record(action="defer"), checkpoint_step="s5")
        s10u = next(s for s in unm["steps"] if s["id"] == "s10")
        self.assertEqual(s10u["gate"]["outcome"], "pending")

    def test_non_gate_step_has_no_gate(self):
        st = rs.build_run_state(_record(action="defer"))
        s4 = next(s for s in st["steps"] if s["id"] == "s4")
        self.assertIsNone(s4["gate"])

    def test_command_exercised_flags(self):
        st = rs.build_run_state(None, command="review")
        ex = {s["id"]: s["exercised"] for s in st["steps"]}
        self.assertTrue(ex["s7"])
        self.assertFalse(ex["s3"])

    def test_unknown_command_falls_back_to_ship(self):
        st = rs.build_run_state(None, command="bogus")
        self.assertEqual(st["command"], "ship")

    def test_non_dict_record_treated_as_empty(self):
        st = rs.build_run_state("nope")
        self.assertEqual(st["active_index"], 0)
        self.assertIsNone(st["pr"])

    def test_regression_not_reached_before_test(self):
        st = rs.build_run_state(_record(action="defer"), checkpoint_step="s2")
        self.assertFalse(st["regression"]["reached"])
        self.assertEqual(st["regression"]["coverage"], 0)

    def test_regression_reached_at_test(self):
        st = rs.build_run_state(_record(action="defer", minor=2), checkpoint_step="s8")
        self.assertTrue(st["regression"]["reached"])
        self.assertEqual(st["regression"]["coverage"], 100)
        self.assertEqual(st["regression"]["worst"], "minor")

    def test_checkpoint_out_of_range_clamped(self):
        # step_index returns None for unknown -> falls back, never crashes.
        st = rs.build_run_state(_record(action="merge"), checkpoint_step="s99")
        self.assertEqual(st["active_id"], "s12")

    def test_malformed_issue_pr_blocks(self):
        rec = _record(action="defer")
        rec["issue"] = "x"
        rec["pull_request"] = None
        st = rs.build_run_state(rec)
        self.assertIsNone(st["issue"])
        self.assertIsNone(st["pr"])

    def test_non_int_issue_number_coerced_to_none(self):
        # Defense-in-depth: a string issue number (e.g. an injection payload from
        # a corrupt ledger) never reaches the run-state as free text.
        rec = _record(action="defer")
        rec["issue"] = {"number": "42</script>"}
        rec["pull_request"] = {"number": True}
        st = rs.build_run_state(rec)
        self.assertIsNone(st["issue"])
        self.assertIsNone(st["pr"])

    def test_int_issue_number_preserved(self):
        st = rs.build_run_state(_record(action="defer", issue=7, pr=9))
        self.assertEqual(st["issue"], 7)
        self.assertEqual(st["pr"], 9)


if __name__ == "__main__":
    unittest.main()
