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
                         "last_gate": "test", "jury_mode": "gating", "stop_reason": None}}
        self.assertEqual(
            rs.live_state_from_checkpoint(rec),
            {"merge": "merged", "capture": "applied", "close": "closed",
             "last_gate": "test", "jury_mode": "gating"},
        )

    def test_malformed(self):
        self.assertEqual(rs.live_state_from_checkpoint(None), {})
        self.assertEqual(rs.live_state_from_checkpoint("x"), {})
        self.assertEqual(rs.live_state_from_checkpoint({"state": "x"}), {})
        self.assertEqual(rs.live_state_from_checkpoint({"state": {"merge": 5, "close": ""}}), {})


def _outcome(groups=None, reviews=None):
    """A minimal serialized ai-jury outcome (the bare ``outcome_to_dict`` shape)."""
    return {"reviews": reviews if reviews is not None else [], "groups": groups or []}


class TestJuryVerdictFromOutcome(unittest.TestCase):
    def test_bare_outcome_counts_and_blocking_verdict(self):
        data = _outcome(
            groups=[{"severity": "major", "status": "verified"},
                    {"severity": "minor", "status": ""},
                    {"severity": "nit", "status": "verified"}],
            reviews=[{"agent": "claude"}, {"agent": "codex"}, {"agent": "claude"}],
        )
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "REQUEST CHANGES")
        self.assertEqual(out["counts"], {"critical": 0, "major": 1, "minor": 1, "nit": 1})
        self.assertEqual(out["reviewers"], 2)  # distinct agents, dupes collapse

    def test_cache_entry_wrapper_shape(self):
        inner = _outcome(groups=[{"severity": "minor", "status": "verified"}],
                         reviews=[{"agent": "claude"}])
        out = rs.jury_verdict_from_outcome({"cache_schema": 1, "outcome": inner, "mac": "x"})
        self.assertEqual(out["verdict"], "COMMENT")
        self.assertEqual(out["counts"]["minor"], 1)
        self.assertEqual(out["reviewers"], 1)

    def test_unsupported_groups_never_count_or_block(self):
        data = _outcome(groups=[{"severity": "critical", "status": "unsupported"},
                                {"severity": "major", "status": "unsupported"}])
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "APPROVE")
        self.assertEqual(out["counts"], {"critical": 0, "major": 0, "minor": 0, "nit": 0})

    def test_verdict_mapping_edges(self):
        crit = _outcome(groups=[{"severity": "critical", "status": "verified"}])
        self.assertEqual(rs.jury_verdict_from_outcome(crit)["verdict"], "REQUEST CHANGES")
        nit = _outcome(groups=[{"severity": "nit", "status": "verified"}])
        self.assertEqual(rs.jury_verdict_from_outcome(nit)["verdict"], "COMMENT")
        self.assertEqual(rs.jury_verdict_from_outcome(_outcome())["verdict"], "APPROVE")

    def test_unrecognised_shapes_are_none(self):
        # Neither a bare outcome (no "reviews") nor a cache entry (no "outcome"
        # dict) — includes a `--format json` report, which is neither.
        for bad in (None, [], "x", 5, {}, {"outcome": "x"}, {"groups": []},
                    {"schema_version": 1, "metadata": {}}):
            self.assertIsNone(rs.jury_verdict_from_outcome(bad))

    def test_malformed_groups_and_severities_are_dropped(self):
        data = _outcome(groups=["x", {"severity": 5}, {"severity": "info"},
                                {"severity": "  MAJOR ", "status": "verified"}])
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["counts"], {"critical": 0, "major": 1, "minor": 0, "nit": 0})
        self.assertEqual(out["verdict"], "REQUEST CHANGES")

    def test_groups_not_a_list_reads_clean(self):
        out = rs.jury_verdict_from_outcome({"reviews": [], "groups": "x"})
        self.assertEqual(out["verdict"], "APPROVE")

    def test_reviewers_dedup_and_drop_malformed(self):
        data = _outcome(reviews=[{"agent": "a"}, {"agent": " a "}, {"agent": " "},
                                 {"agent": 5}, "x"])
        self.assertEqual(rs.jury_verdict_from_outcome(data)["reviewers"], 1)

    def test_reviews_not_a_list_counts_zero(self):
        out = rs.jury_verdict_from_outcome({"reviews": "x"})
        self.assertEqual(out["reviewers"], 0)


class TestJuryVerdictInBuild(unittest.TestCase):
    _SUMMARY = {"verdict": "REQUEST CHANGES",
                "counts": {"critical": 0, "major": 1, "minor": 0, "nit": 0},
                "reviewers": 3}

    def test_passthrough_on_ship(self):
        st = rs.build_run_state(None, checkpoint_step="s7", jury_verdict=self._SUMMARY)
        self.assertEqual(st["jury_verdict"], self._SUMMARY)

    def test_default_is_none(self):
        st = rs.build_run_state(None, checkpoint_step="s7")
        self.assertIsNone(st["jury_verdict"])

    def test_non_ship_commands_have_no_verdict(self):
        st = rs.build_run_state(None, command="overnight", jury_verdict=self._SUMMARY)
        self.assertIsNone(st["jury_verdict"])

    def test_unknown_verdict_literal_drops_block(self):
        # Only the three recognised literals ever reach the payload/DOM.
        bad = dict(self._SUMMARY, verdict="<img src=x onerror=alert(1)>")
        st = rs.build_run_state(None, checkpoint_step="s7", jury_verdict=bad)
        self.assertIsNone(st["jury_verdict"])

    def test_malformed_block_drops(self):
        for bad in ("x", 5, [], {}, {"counts": {}}):
            st = rs.build_run_state(None, checkpoint_step="s7", jury_verdict=bad)
            self.assertIsNone(st["jury_verdict"])

    def test_counts_and_reviewers_coerced(self):
        raw = {"verdict": "APPROVE",
               "counts": {"critical": -1, "major": "x", "minor": True},
               "reviewers": "many"}
        st = rs.build_run_state(None, checkpoint_step="s7", jury_verdict=raw)
        self.assertEqual(st["jury_verdict"],
                         {"verdict": "APPROVE",
                          "counts": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
                          "reviewers": 0})

    def test_counts_block_not_a_dict(self):
        raw = {"verdict": "COMMENT", "counts": "x", "reviewers": 2}
        st = rs.build_run_state(None, checkpoint_step="s7", jury_verdict=raw)
        self.assertEqual(st["jury_verdict"]["counts"],
                         {"critical": 0, "major": 0, "minor": 0, "nit": 0})
        self.assertEqual(st["jury_verdict"]["reviewers"], 2)


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

    def test_jury_from_checkpoint_live(self):
        self.assertEqual(rs.jury_from_checkpoint({"jury_mode": "advisory"}),
                         {"mode": "advisory", "active": True})
        self.assertEqual(rs.jury_from_checkpoint({"jury_mode": "off"}),
                         {"mode": "off", "active": False})
        # missing / malformed -> inactive default (callers fall back to the ledger)
        self.assertEqual(rs.jury_from_checkpoint({}), {"mode": None, "active": False})
        self.assertEqual(rs.jury_from_checkpoint(None), {"mode": None, "active": False})

    def test_live_checkpoint_jury_wins_over_ledger(self):
        # a run in progress: checkpoint says gating, the (stale/absent) ledger says off
        st = rs.build_run_state(
            {"record_type": "ship_run", "run_context": {"jury_mode": "off"}},
            checkpoint_step="s7",
            checkpoint_state={"jury_mode": "gating"},
        )
        self.assertEqual(st["jury"], {"mode": "gating", "active": True})

    def test_jury_falls_back_to_ledger_when_checkpoint_silent(self):
        # post-run: no live jury in the checkpoint -> read the ledger
        st = rs.build_run_state(
            {"record_type": "ship_run", "run_context": {"jury_mode": "advisory"}},
            checkpoint_step="s7",
            checkpoint_state={"merge": "pending"},
        )
        self.assertEqual(st["jury"], {"mode": "advisory", "active": True})

    def test_no_record_no_jury(self):
        self.assertEqual(rs.build_run_state(None)["jury"], {"mode": None, "active": False})


def _rich_record():
    """A record carrying every ledger field the projection surfaces (see keel.ledger)."""
    rec = _record(action="merge")
    rec["assessment"].update({"tier": 3, "window_open": True, "bypassed_window": False})
    rec["gates"] = [
        {"gate": "build", "ok": True, "skipped": False, "error": None, "finding_count": 0},
        {"gate": "evidence", "ok": False, "skipped": False, "error": "no evidence",
         "finding_count": 2},
        {"gate": "jury", "ok": False, "skipped": True, "error": None, "finding_count": 0},
    ]
    rec["actors"] = {"implementer": "agent:claude", "reviewers": ["agent:codex", "agent:gemini"],
                     "tester": "agent:anthropic-api"}
    rec["run_context"] = {"host_agent": "claude-code"}
    rec["changes"] = {"file_count": 4, "files": ["a.py"]}
    return rec


class TestGatesFromRecord(unittest.TestCase):
    def test_projects_named_gates(self):
        gates = rs.gates_from_record(_rich_record())
        self.assertEqual(gates, [
            {"name": "build", "ok": True, "skipped": False, "error": None, "finding_count": 0},
            {"name": "evidence", "ok": False, "skipped": False, "error": "no evidence",
             "finding_count": 2},
            {"name": "jury", "ok": False, "skipped": True, "error": None, "finding_count": 0},
        ])

    def test_missing_or_malformed_block(self):
        self.assertEqual(rs.gates_from_record(None), [])
        self.assertEqual(rs.gates_from_record({}), [])
        self.assertEqual(rs.gates_from_record({"gates": "x"}), [])

    def test_drops_entries_without_a_usable_name(self):
        gates = rs.gates_from_record({"gates": ["x", {}, {"gate": ""}, {"gate": 5},
                                                {"gate": "lint", "ok": True}]})
        self.assertEqual([g["name"] for g in gates], ["lint"])

    def test_coerces_malformed_fields(self):
        # Non-bool ok/skipped, blank/non-str error, negative/bool counts — every
        # field degrades to its safe default instead of surfacing junk.
        gates = rs.gates_from_record({"gates": [
            {"gate": "build", "ok": "yes", "skipped": 1, "error": "  ", "finding_count": -1},
            {"gate": "lint", "ok": True, "skipped": False, "error": 5, "finding_count": True},
        ]})
        self.assertEqual(gates[0], {"name": "build", "ok": False, "skipped": False,
                                    "error": None, "finding_count": 0})
        self.assertEqual(gates[1], {"name": "lint", "ok": True, "skipped": False,
                                    "error": None, "finding_count": 0})


class TestLedgerFieldsInBuild(unittest.TestCase):
    def test_rich_record_surfaces_every_field(self):
        st = rs.build_run_state(_rich_record())
        self.assertEqual(st["tier"], 3)
        self.assertTrue(st["window_open"])
        self.assertFalse(st["bypassed_window"])
        self.assertEqual([g["name"] for g in st["gates"]], ["build", "evidence", "jury"])
        self.assertEqual(st["reviewers"], ["agent:codex", "agent:gemini"])
        self.assertEqual(st["tester"], "agent:anthropic-api")
        self.assertEqual(st["host_agent"], "claude-code")
        self.assertEqual(st["merge_reason"], "r")
        self.assertEqual(st["file_count"], 4)

    def test_no_record_leaves_fields_empty(self):
        st = rs.build_run_state(None)
        self.assertIsNone(st["tier"])
        self.assertIsNone(st["window_open"])
        self.assertIsNone(st["bypassed_window"])
        self.assertEqual(st["gates"], [])
        self.assertEqual(st["reviewers"], [])
        self.assertIsNone(st["tester"])
        self.assertIsNone(st["host_agent"])
        self.assertIsNone(st["merge_reason"])
        self.assertIsNone(st["file_count"])

    def test_bypassed_window_surfaces_when_set(self):
        rec = _rich_record()
        rec["assessment"]["bypassed_window"] = True
        self.assertTrue(rs.build_run_state(rec)["bypassed_window"])

    def test_malformed_fields_degrade_to_none(self):
        rec = _record(action="defer")
        rec["assessment"].update({"tier": "three", "window_open": "yes",
                                  "bypassed_window": 1})
        rec["actors"] = {"reviewers": "agent:codex", "tester": "  "}
        rec["run_context"] = {"host_agent": 5}
        rec["changes"] = {"file_count": -2}
        st = rs.build_run_state(rec)
        self.assertIsNone(st["tier"])
        self.assertIsNone(st["window_open"])
        self.assertIsNone(st["bypassed_window"])
        self.assertEqual(st["reviewers"], [])
        self.assertIsNone(st["tester"])
        self.assertIsNone(st["host_agent"])
        self.assertIsNone(st["file_count"])

    def test_malformed_blocks_degrade_to_none(self):
        rec = _record(action="defer")
        rec["assessment"] = "x"
        rec["actors"] = "x"
        rec["run_context"] = "x"
        rec["changes"] = "x"
        st = rs.build_run_state(rec)
        self.assertIsNone(st["tier"])
        self.assertIsNone(st["window_open"])
        self.assertEqual(st["reviewers"], [])
        self.assertIsNone(st["merge_reason"])
        self.assertIsNone(st["file_count"])

    def test_bool_tier_and_count_rejected(self):
        # bools are ints in Python — a corrupt record must not surface tier=True.
        rec = _record(action="defer")
        rec["assessment"]["tier"] = True
        rec["changes"] = {"file_count": True}
        st = rs.build_run_state(rec)
        self.assertIsNone(st["tier"])
        self.assertIsNone(st["file_count"])

    def test_reviewers_list_drops_junk_entries(self):
        rec = _record(action="defer")
        rec["actors"] = {"reviewers": ["agent:codex", "", 5, "  ", "agent:gemini"]}
        self.assertEqual(rs.build_run_state(rec)["reviewers"],
                         ["agent:codex", "agent:gemini"])

    def test_merge_reason_read_alongside_action(self):
        rec = _record(action="defer")
        rec["assessment"]["merge"]["reason"] = "window closed"
        st = rs.build_run_state(rec)
        self.assertEqual(st["merge_reason"], "window closed")
        rec["assessment"]["merge"]["reason"] = 5
        self.assertIsNone(rs.build_run_state(rec)["merge_reason"])


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


class RealLedgerRecordTests(unittest.TestCase):
    """Non-circular key pinning: project a record built by keel's OWN ledger
    writer (#575 review) so a future ledger rename can't silently zero fields."""

    def _real_record(self):
        from types import SimpleNamespace

        from keel import ledger

        outcome = SimpleNamespace(gate="build", ok=True, skipped=False, error=None,
                                  findings=[])
        failed = SimpleNamespace(gate="evidence", ok=False, skipped=False,
                                 error="missing verdict", findings=[object(), object()])
        verdict = SimpleNamespace(counts={"critical": 0, "major": 1, "minor": 0, "nit": 0},
                                  blocked=True)
        assessment = SimpleNamespace(
            tier=3, reviewers=2, window_open=False, ci_ok=True, halted=False,
            bypassed_window=True,
            merge=SimpleNamespace(action="blocked", reason="window closed"),
        )
        return ledger.build_ship_run_record(
            command="ship", base_branch="main", changed_files=["a.py", "b.py"],
            outcomes=[outcome, failed], verdict=verdict, assessment=assessment,
            issue_number=7, pr_number=9, branch="feat/x",
            implementer="anthropic-api:claude-sonnet-5",
            reviewer_agents=["claude", "codex"], tester="claude",
            host_agent="claude",
        )

    def test_projection_reads_real_writer_keys(self):
        state = rs.build_run_state(self._real_record(), checkpoint_step="s8")
        self.assertEqual(state["tier"], 3)
        self.assertIs(state["window_open"], False)
        self.assertIs(state["bypassed_window"], True)
        self.assertEqual(state["merge_reason"], "window closed")
        self.assertEqual(state["file_count"], 2)
        self.assertEqual(state["reviewers"], ["claude", "codex"])
        self.assertEqual(state["tester"], "claude")
        self.assertEqual(state["host_agent"], "claude")
        self.assertEqual(
            state["gates"],
            [{"name": "build", "ok": True, "skipped": False, "error": None,
              "finding_count": 0},
             {"name": "evidence", "ok": False, "skipped": False,
              "error": "missing verdict", "finding_count": 2}],
        )


class JuryVerdictGateFidelityTests(unittest.TestCase):
    """The verdict must match ai-jury's DEFAULT gate posture (#579 review):
    only VERIFIED critical/major blocks; unverified/disputed stays COMMENT."""

    def test_unverified_critical_is_comment_not_blocking(self):
        data = {"reviews": [{"agent": "claude"}],
                "groups": [{"severity": "critical", "status": ""}]}
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "COMMENT")
        self.assertEqual(out["counts"]["critical"], 1)

    def test_disputed_critical_is_comment_not_blocking(self):
        data = {"reviews": [{"agent": "claude"}],
                "groups": [{"severity": "critical", "status": "needs_human_decision"}]}
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "COMMENT")

    def test_verified_major_blocks(self):
        data = {"reviews": [{"agent": "claude"}],
                "groups": [{"severity": "major", "status": "verified"}]}
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "REQUEST CHANGES")

    def test_report_shape_accepted(self):
        # The `jury --format json` report is the shape ai-jury's public CLI
        # actually emits — and the one ship.md s8 saves to the discovery path.
        data = {
            "schema_version": "ai-jury.report.v1",
            "metadata": {"agents": [{"agent": "claude"}, {"agent": "codex"}]},
            "consensus": [
                {"representative": {"severity": "major"},
                 "verification_status": "verified"},
                {"representative": {"severity": "minor"},
                 "verification_status": None},
                {"representative": {"severity": "critical"},
                 "verification_status": "unsupported"},
            ],
            "verdict": "",
        }
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "REQUEST CHANGES")
        self.assertEqual(out["counts"], {"critical": 0, "major": 1, "minor": 1, "nit": 0})
        self.assertEqual(out["reviewers"], 2)

    def test_report_shape_malformed_entries_skipped(self):
        data = {"schema_version": "x", "consensus": ["junk", {"representative": None}],
                "metadata": None}
        out = rs.jury_verdict_from_outcome(data)
        self.assertEqual(out["verdict"], "APPROVE")
        self.assertEqual(out["reviewers"], 0)


if __name__ == "__main__":
    unittest.main()
