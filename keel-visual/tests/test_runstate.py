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
    def test_phase_index_known_and_unknown(self):
        ship = rs.flows.flow_for("ship")
        self.assertEqual(rs._phase_index(ship, "s0"), 0)
        self.assertEqual(rs._phase_index(ship, "s10"), 10)
        self.assertIsNone(rs._phase_index(ship, "sX"))
        self.assertIsNone(rs._phase_index(ship, None))

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


class TestCurrentStepFromCheckpoint(unittest.TestCase):
    def test_reads_current_step(self):
        self.assertEqual(
            rs.current_step_from_checkpoint({"position": {"current_step": "s6"}}), "s6"
        )

    def test_none_and_malformed(self):
        self.assertIsNone(rs.current_step_from_checkpoint(None))
        self.assertIsNone(rs.current_step_from_checkpoint("nope"))
        self.assertIsNone(rs.current_step_from_checkpoint({"position": "x"}))
        self.assertIsNone(rs.current_step_from_checkpoint({"position": {"current_step": ""}}))
        self.assertIsNone(rs.current_step_from_checkpoint({"position": {"current_step": 5}}))


class TestLiveStateFromCheckpoint(unittest.TestCase):
    def test_extracts_recognised_fields(self):
        rec = {"state": {"merge": "merged", "capture": "applied", "close": "closed",
                         "last_gate": "test", "stop_reason": None}}
        self.assertEqual(
            rs.live_state_from_checkpoint(rec),
            {"merge": "merged", "capture": "applied", "close": "closed", "last_gate": "test"},
        )

    def test_malformed(self):
        self.assertEqual(rs.live_state_from_checkpoint(None), {})
        self.assertEqual(rs.live_state_from_checkpoint("x"), {})
        self.assertEqual(rs.live_state_from_checkpoint({"state": "x"}), {})
        self.assertEqual(rs.live_state_from_checkpoint({"state": {"merge": 5, "close": ""}}), {})


class TestMergeOutcome(unittest.TestCase):
    def test_live_state_maps(self):
        self.assertEqual(rs._merge_outcome(merged=False, live_merge="merged"), "pass")
        self.assertEqual(rs._merge_outcome(merged=False, live_merge="pending"), "pending")
        self.assertEqual(rs._merge_outcome(merged=False, live_merge="failed"), "fail")

    def test_falls_back_to_ledger_merged(self):
        self.assertEqual(rs._merge_outcome(merged=True, live_merge=None), "pass")
        self.assertEqual(rs._merge_outcome(merged=False, live_merge=None), "pending")


class TestLiveCheckpointInBuild(unittest.TestCase):
    def test_live_merged_without_ledger_record(self):
        st = rs.build_run_state(None, checkpoint_step="s10", checkpoint_state={"merge": "merged"})
        self.assertTrue(st["merged"])
        self.assertEqual(st["merge_state"], "merged")
        s10 = next(s for s in st["steps"] if s["id"] == "s10")
        self.assertEqual(s10["gate"]["outcome"], "pass")

    def test_live_pending_merge(self):
        st = rs.build_run_state(None, checkpoint_step="s10", checkpoint_state={"merge": "pending"})
        self.assertFalse(st["merged"])
        s10 = next(s for s in st["steps"] if s["id"] == "s10")
        self.assertEqual(s10["gate"]["outcome"], "pending")

    def test_live_failed_merge(self):
        st = rs.build_run_state(None, checkpoint_step="s10", checkpoint_state={"merge": "failed"})
        s10 = next(s for s in st["steps"] if s["id"] == "s10")
        self.assertEqual(s10["gate"]["outcome"], "fail")

    def test_no_checkpoint_state_keeps_ledger_behavior(self):
        st = rs.build_run_state(None, checkpoint_step="s10")
        self.assertIsNone(st["merge_state"])


class TestJury(unittest.TestCase):
    def test_active_modes(self):
        for mode in ("gating", "advisory"):
            j = rs.jury_from_record({"run_context": {"jury_mode": mode}})
            self.assertEqual(j, {"mode": mode, "active": True})

    def test_inactive_modes(self):
        for mode in ("off", "none", "disabled", ""):
            self.assertFalse(rs.jury_from_record({"run_context": {"jury_mode": mode}})["active"])

    def test_unknown_mode_is_normalised_to_safe_token(self):
        # An unrecognised / attacker-crafted mode must never surface as-is (it
        # would reach the web chip's innerHTML). Enabled-but-unknown -> "on".
        j = rs.jury_from_record({"run_context": {"jury_mode": "<img src=x onerror=alert(1)>"}})
        self.assertTrue(j["active"])
        self.assertEqual(j["mode"], "on")
        self.assertNotIn("<", j["mode"])

    def test_mode_is_normalised_lowercase(self):
        self.assertEqual(rs.jury_from_record({"run_context": {"jury_mode": "  GATING "}})["mode"],
                         "gating")

    def test_missing_or_malformed(self):
        self.assertEqual(rs.jury_from_record(None), {"mode": None, "active": False})
        self.assertEqual(rs.jury_from_record({}), {"mode": None, "active": False})
        self.assertEqual(
            rs.jury_from_record({"run_context": "x"}), {"mode": None, "active": False})
        self.assertEqual(
            rs.jury_from_record({"run_context": {"jury_mode": 5}}), {"mode": None, "active": False})

    def test_build_run_state_includes_active_jury(self):
        rec = {
            "record_type": "ship_run",
            "assessment": {"tier": 3, "merge": {"action": "defer"}},
            "run_context": {"jury_mode": "gating"},
        }
        st = rs.build_run_state(rec, checkpoint_step="s7")
        self.assertTrue(st["jury"]["active"])
        self.assertEqual(st["jury"]["mode"], "gating")

    def test_non_ship_command_has_no_jury(self):
        st = rs.build_run_state(
            {"record_type": "ship_run", "run_context": {"jury_mode": "gating"}},
            command="overnight",
        )
        self.assertEqual(st["jury"], {"mode": None, "active": False})

    def test_no_record_no_jury(self):
        self.assertEqual(rs.build_run_state(None)["jury"], {"mode": None, "active": False})


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

    def test_command_renders_its_own_flow(self):
        # A non-ship command renders its own phases (not the ship backbone), all
        # exercised, with the flow's kinds.
        st = rs.build_run_state(None, command="overnight")
        self.assertEqual(st["command"], "overnight")
        ids = [s["id"] for s in st["steps"]]
        self.assertEqual(ids, ["config", "preflight", "queue", "loop", "report"])
        self.assertTrue(all(s["exercised"] for s in st["steps"]))
        loop = next(s for s in st["steps"] if s["id"] == "loop")
        self.assertEqual(loop["kind"], "loop")

    def test_non_ship_command_has_no_merge_or_regression(self):
        st = rs.build_run_state(None, command="deps-audit")
        self.assertFalse(st["merged"])
        self.assertFalse(st["regression"]["reached"])
        self.assertTrue(all(s["gate"] is None for s in st["steps"]))

    def test_non_ship_gate_has_neutral_outcome(self):
        # wrap has a 'gates' gate phase but no ship finding data -> neutral.
        st = rs.build_run_state(None, command="wrap", checkpoint_step="gates")
        gate = next(s for s in st["steps"] if s["id"] == "gates")
        self.assertEqual(gate["gate"], {"kind": "gate", "outcome": "pending"})

    def test_unknown_command_falls_back_to_ship(self):
        st = rs.build_run_state(None, command="bogus")
        self.assertEqual(st["command"], "ship")
        self.assertEqual([s["id"] for s in st["steps"]], [f"s{i}" for i in range(13)])

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
        # _phase_index returns None for an unknown step -> falls back, never crashes.
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
