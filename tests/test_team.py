"""Unit tests for ``knobs.team`` — the per-role / per-tier provider policy (#1014)."""

import unittest

from keel import ship, team

FULL = {
    "implement": {
        "default": {"provider": "claude"},
        "by_role": {
            "core": {"provider": "agy", "model": "gemini-3.8-flash-high", "effort": "high"},
            "docs": {"provider": "subagent:docs-writer"},
        },
    },
    "gate": {"provider": "codex", "distinct_from": "implementer"},
    "review": {
        "by_tier": {
            "1": [{"provider": "claude"}],
            "2": [{"provider": "claude"}, {"provider": "codex"}],
            "3": "jury",
        }
    },
    "jury": {"mode": "gating", "min_vendors": 3},
    "fix": {"provider": "implementer"},
}


class TestSeat(unittest.TestCase):
    def test_a_bare_provider_is_a_provider_seat(self):
        seat = team.Seat(provider="agy", model="gemini-3.8-flash", effort="high")

        self.assertEqual(seat.kind, "provider")
        self.assertEqual(seat.name, "agy")
        self.assertEqual(
            seat.as_dict(),
            {
                "provider": "agy",
                "name": "agy",
                "kind": "provider",
                "model": "gemini-3.8-flash",
                "effort": "high",
            },
        )

    def test_the_subagent_prefix_names_a_host_subagent_not_a_vendor(self):
        seat = team.Seat(provider="subagent:backend-developer")

        self.assertEqual(seat.kind, "subagent")
        self.assertEqual(seat.name, "backend-developer")

    def test_the_implementer_alias_is_its_own_kind(self):
        self.assertEqual(team.Seat(provider="implementer").kind, "alias")

    def test_annotations_are_only_present_when_supplied(self):
        gate = team.Seat(provider="codex", distinct_from="implementer")

        record = gate.as_dict(source="team.gate", slot="A")

        self.assertEqual(record["distinct_from"], "implementer")
        self.assertEqual(record["source"], "team.gate")
        self.assertEqual(record["slot"], "A")
        self.assertNotIn("slot", team.Seat(provider="codex").as_dict())


class TestParseTeam(unittest.TestCase):
    def test_no_block_is_an_unconfigured_policy(self):
        for raw in (None, "team", []):
            with self.subTest(raw=raw):
                policy = team.parse_team(raw)
                self.assertFalse(policy.configured)
                self.assertIsNone(policy.implement)
                self.assertEqual(policy.implement_by_role, {})

    def test_a_full_block_round_trips_into_typed_seats(self):
        policy = team.parse_team(FULL)

        self.assertTrue(policy.configured)
        self.assertEqual(policy.implement, team.Seat(provider="claude"))
        self.assertEqual(policy.implement_by_role["core"].effort, "high")
        self.assertEqual(policy.gate.distinct_from, "implementer")
        self.assertEqual(policy.review_by_tier["3"], "jury")
        self.assertEqual(len(policy.review_by_tier["2"]), 2)
        self.assertEqual(policy.jury_mode, "gating")
        self.assertEqual(policy.jury_min_vendors, 3)
        self.assertEqual(policy.fix, team.Seat(provider="implementer"))

    def test_malformed_members_are_dropped_rather_than_half_parsed(self):
        policy = team.parse_team(
            {
                "implement": {"default": "claude", "by_role": {1: {"provider": "codex"}}},
                "review": {"by_tier": {2: [{"provider": "claude"}], "1": 7}, "default": None},
                "gate": {"model": "opus"},
                "jury": {"min_vendors": "two"},
            }
        )

        self.assertTrue(policy.configured)
        self.assertIsNone(policy.implement)
        self.assertEqual(policy.implement_by_role, {})
        self.assertEqual(policy.review_by_tier, {})
        self.assertIsNone(policy.review)
        self.assertIsNone(policy.gate)
        self.assertIsNone(policy.jury_min_vendors)

    def test_blank_strings_read_as_unset(self):
        policy = team.parse_team({"fix": {"provider": " codex ", "model": "  ", "effort": ""}})

        self.assertEqual(policy.fix, team.Seat(provider="codex"))

    def test_a_by_role_entry_that_names_no_provider_is_skipped(self):
        policy = team.parse_team({"implement": {"by_role": {"core": {"model": "opus"}}}})

        self.assertEqual(policy.implement_by_role, {})

    def test_review_default_accepts_seats_or_the_jury_literal(self):
        seats = team.parse_team({"review": {"default": [{"provider": "claude"}]}})
        panel = team.parse_team({"review": {"default": "jury"}})

        self.assertEqual(seats.review, (team.Seat(provider="claude"),))
        self.assertEqual(panel.review, "jury")

    def test_review_for_prefers_the_tier_then_the_default(self):
        policy = team.parse_team(
            {"review": {"default": [{"provider": "codex"}], "by_tier": {"3": "jury"}}}
        )

        self.assertEqual(policy.review_for(3), ("jury", "team.review.by_tier.3"))
        self.assertEqual(
            policy.review_for(1), ((team.Seat(provider="codex"),), "team.review.default")
        )
        self.assertEqual(team.TeamPolicy().review_for(2), (None, None))


class TestCanonical(unittest.TestCase):
    def test_an_unconfigured_policy_contributes_nothing_to_the_hash(self):
        self.assertEqual(team.canonical(team.TeamPolicy()), {})

    def test_an_empty_block_is_present_but_empty(self):
        self.assertEqual(team.canonical(team.parse_team({})), {"team": {}})

    def test_every_configured_section_is_rendered(self):
        rendered = team.canonical(team.parse_team(FULL))["team"]

        self.assertEqual(rendered["implement"]["default"]["provider"], "claude")
        self.assertEqual(rendered["implement"]["by_role"]["core"]["effort"], "high")
        self.assertEqual(rendered["gate"]["distinct_from"], "implementer")
        self.assertEqual(rendered["review"]["by_tier"]["3"], "jury")
        self.assertEqual(len(rendered["review"]["by_tier"]["2"]), 2)
        self.assertEqual(rendered["jury"], {"mode": "gating", "min_vendors": 3})
        self.assertEqual(rendered["fix"]["provider"], "implementer")

    def test_a_jury_literal_default_and_a_mode_only_jury_render(self):
        rendered = team.canonical(
            team.parse_team({"review": {"default": "jury"}, "jury": {"mode": "advisory"}})
        )["team"]

        self.assertEqual(rendered["review"]["default"], "jury")
        self.assertEqual(rendered["jury"], {"mode": "advisory"})

    def test_a_by_role_only_implement_block_renders_without_a_default(self):
        rendered = team.canonical(
            team.parse_team({"implement": {"by_role": {"core": {"provider": "codex"}}}})
        )["team"]

        self.assertEqual(list(rendered["implement"]), ["by_role"])

    def test_a_min_vendors_only_jury_renders(self):
        rendered = team.canonical(team.parse_team({"jury": {"min_vendors": 4}}))["team"]

        self.assertEqual(rendered["jury"], {"min_vendors": 4})


class TestLegacySeats(unittest.TestCase):
    def test_a_value_naming_a_provider_is_that_provider(self):
        seats = team.legacy_seats({"core": "codex"}, provider_names={"codex", "claude"})

        self.assertEqual(seats["core"], team.Seat(provider="codex"))

    def test_anything_else_is_the_host_subagent_it_always_meant(self):
        seats = team.legacy_seats({"core": "backend-developer"}, provider_names={"codex"})

        self.assertEqual(seats["core"].kind, "subagent")
        self.assertEqual(seats["core"].name, "backend-developer")

    def test_a_blank_value_names_nobody(self):
        self.assertEqual(team.legacy_seats({"core": "  ", "docs": 3}), {})

    def test_a_vendor_model_value_is_that_vendor_and_that_model(self):
        """`docs/keel/models.md` documents this spelling; it is not a subagent name.

        Reading the whole string as one opaque name turned
        `anthropic-api:claude-3-7-sonnet-20250219` into a host subagent called
        `subagent:anthropic-api:claude-3-7-…`, which no host has — so the seat stopped
        reaching `keel delegate run` at all.
        """
        seats = team.legacy_seats(
            {"frontend": "anthropic-api:claude-3-7-sonnet-20250219"},
            provider_names={"anthropic-api"},
        )

        self.assertEqual(seats["frontend"].kind, "provider")
        self.assertEqual(seats["frontend"].provider, "anthropic-api")
        self.assertEqual(seats["frontend"].model, "claude-3-7-sonnet-20250219")

    def test_an_explicit_subagent_value_is_not_prefixed_twice(self):
        seats = team.legacy_seats({"docs": "subagent:writer"}, provider_names={"claude"})

        self.assertEqual(seats["docs"].provider, "subagent:writer")

    def test_an_unresolvable_head_is_still_a_subagent_name(self):
        seats = team.legacy_seats({"core": "backend-developer"}, provider_names={"claude"})

        self.assertEqual(seats["core"].provider, "subagent:backend-developer")


class TestSeatFromToken(unittest.TestCase):
    def test_a_vendor_model_token_splits_on_the_first_colon(self):
        seat = team.seat_from_token("ollama:qwen2.5:7b")

        self.assertEqual(seat.provider, "ollama")
        self.assertEqual(seat.model, "qwen2.5:7b")

    def test_a_bare_vendor_carries_no_model(self):
        self.assertIsNone(team.seat_from_token(" codex ").model)

    def test_a_subagent_tokens_colon_is_not_a_model_separator(self):
        seat = team.seat_from_token("subagent:backend-developer")

        self.assertEqual(seat.kind, "subagent")
        self.assertIsNone(seat.model)


class TestSlotLabels(unittest.TestCase):
    def test_the_labels_match_the_focus_slots_ship_dispatches(self):
        for count in range(0, 4):
            with self.subTest(count=count):
                self.assertEqual(
                    team.slot_labels(count),
                    tuple(focus["slot"] for focus in ship.reviewer_focuses(count)),
                )

    def test_the_labeller_is_total_beyond_keels_own_vocabulary(self):
        # Not reachable through the CLI (--reviewers is 1|2|3), and that is the point:
        # running short here raised IndexError from inside the resolver instead of the
        # ValueError the caller documents for an out-of-range count.
        self.assertEqual(team.slot_labels(5), ("A", "B", "C", "D", "E"))
        self.assertEqual(team.slot_labels(-1), ())


class TestResolveAssignment(unittest.TestCase):
    def test_an_unconfigured_policy_staffs_the_host_agent(self):
        assignment = team.resolve_assignment(team.TeamPolicy(), tier=2, default_count=2)

        self.assertFalse(assignment["configured"])
        self.assertEqual(assignment["implementer"]["provider"], "claude")
        self.assertEqual(assignment["implementer"]["source"], "host")
        self.assertEqual(assignment["reviewer_count"], 2)
        self.assertEqual(assignment["reviewer_source"], "risk-tier")
        self.assertIsNone(assignment["gate"])
        self.assertEqual(assignment["fix"]["alias"], "implementer")
        self.assertEqual(assignment["fix"]["source"], "default")

    def test_an_unresolved_tier_says_so_rather_than_claiming_a_tier(self):
        assignment = team.resolve_assignment(team.TeamPolicy(), tier=None)

        self.assertEqual(assignment["reviewer_source"], "unresolved")

    def test_the_role_seat_wins_over_the_default_and_the_legacy_knob(self):
        policy = team.parse_team(FULL)
        legacy = {"core": team.Seat(provider="subagent:backend-developer")}

        assignment = team.resolve_assignment(policy, tier=2, role="core", legacy=legacy)

        self.assertEqual(assignment["implementer"]["provider"], "agy")
        self.assertEqual(assignment["implementer"]["source"], "team.implement.by_role.core")

    def test_the_default_seat_covers_a_role_the_policy_does_not_name(self):
        assignment = team.resolve_assignment(team.parse_team(FULL), tier=2, role="infra")

        self.assertEqual(assignment["implementer"]["source"], "team.implement.default")

    def test_the_deprecated_knob_still_routes_when_team_names_no_implementer(self):
        legacy = {"core": team.Seat(provider="subagent:backend-developer")}

        assignment = team.resolve_assignment(
            team.parse_team({"gate": {"provider": "codex"}}), role="core", legacy=legacy
        )

        self.assertEqual(assignment["implementer"]["kind"], "subagent")
        self.assertEqual(
            assignment["implementer"]["source"], "knobs.implementer_agents.core (deprecated)"
        )

    def test_delegate_is_a_per_run_override_of_the_policy(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=2, role="core", delegate="anthropic-api:claude-opus-4-5"
        )

        self.assertEqual(assignment["implementer"]["provider"], "anthropic-api")
        self.assertEqual(assignment["implementer"]["model"], "claude-opus-4-5")
        self.assertEqual(assignment["implementer"]["source"], "flag:--delegate")

    def test_a_tier_names_its_own_reviewer_seats(self):
        assignment = team.resolve_assignment(team.parse_team(FULL), tier=2, default_count=2)

        self.assertEqual(assignment["review_panel"], "reviewers")
        self.assertEqual(assignment["reviewer_count"], 2)
        self.assertEqual(
            [seat["provider"] for seat in assignment["reviewers"]], ["claude", "codex"]
        )
        self.assertEqual([seat["slot"] for seat in assignment["reviewers"]], ["A", "C"])
        self.assertEqual(assignment["reviewer_source"], "team.review.by_tier.2")

    def test_a_jury_tier_empties_the_reviewer_bench(self):
        assignment = team.resolve_assignment(team.parse_team(FULL), tier=3, default_count=3)

        self.assertEqual(assignment["review_panel"], "jury")
        self.assertEqual(assignment["reviewers"], [])
        self.assertEqual(assignment["reviewer_count"], 0)
        self.assertTrue(assignment["jury"]["panel_is_review"])
        self.assertEqual(assignment["jury"]["min_vendors"], 3)

    def test_reviewers_override_on_a_jury_tier_is_reported_not_silently_applied(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=3, default_count=3, reviewer_override=2
        )

        self.assertEqual(assignment["reviewer_count"], 0)
        self.assertIn("--reviewers 2 ignored", assignment["warnings"][0])

    def test_an_override_resizes_the_bench_and_pads_with_the_host(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=2, default_count=2, reviewer_override=3
        )

        self.assertEqual(assignment["reviewer_source"], "override")
        self.assertEqual(
            [seat["provider"] for seat in assignment["reviewers"]], ["claude", "codex", "claude"]
        )

    def test_padding_a_bench_with_a_seated_host_is_reported(self):
        """`--reviewers 3` on a two-seat tier must not quietly duplicate a vendor.

        The third slot is filled with the host agent, which is already slot A — so the
        panel cannot return three distinct vendors, and `require_distinct_vendors` (on by
        default from TIER-2) rejects it at the evidence gate, long after the run.
        """
        policy = team.parse_team(
            {"review": {"by_tier": {"2": [{"provider": "claude"}, {"provider": "codex"}]}}}
        )

        assignment = team.resolve_assignment(
            policy, tier=2, default_count=2, reviewer_override=3, host_agent="claude"
        )

        self.assertEqual(
            [seat["provider"] for seat in assignment["reviewers"]], ["claude", "codex", "claude"]
        )
        self.assertIn("is already seated", assignment["warnings"][0])

    def test_padding_with_an_unseated_host_is_not_a_duplicate(self):
        policy = team.parse_team({"review": {"by_tier": {"2": [{"provider": "codex"}]}}})

        assignment = team.resolve_assignment(
            policy, tier=2, default_count=2, reviewer_override=2, host_agent="claude"
        )

        self.assertEqual(assignment["warnings"], [])

    def test_no_jury_staffs_the_tiers_reviewers_instead_of_nobody(self):
        policy = team.parse_team({"review": {"by_tier": {"3": "jury"}}})

        assignment = team.resolve_assignment(policy, tier=3, default_count=3, jury_disabled=True)

        self.assertEqual(assignment["review_panel"], "reviewers")
        self.assertEqual(assignment["reviewer_count"], 3)
        self.assertFalse(assignment["jury"]["panel_is_review"])
        self.assertIn("--no-jury skips the panel, never the review", assignment["warnings"][0])

    def test_a_surplus_seat_is_reported_rather_than_silently_undispatched(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=2, default_count=2, reviewer_override=1
        )

        self.assertEqual(assignment["reviewer_count"], 1)
        self.assertIn("surplus seats are not dispatched", assignment["warnings"][0])

    def test_review_delegates_replace_slots_positionally(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=2, review_delegates=["", "agy:gemini-3.8-flash"]
        )

        self.assertEqual(assignment["reviewers"][0]["provider"], "agy")
        self.assertEqual(assignment["reviewers"][0]["source"], "flag:--review-delegate")
        self.assertEqual(assignment["reviewers"][1]["source"], "team.review.by_tier.2")

    def test_a_review_delegate_past_the_last_slot_is_reported(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=1, default_count=1, review_delegates=["codex", "agy"]
        )

        self.assertEqual(assignment["reviewers"][0]["provider"], "codex")
        self.assertIn("only 1 reviewer slot(s) are staffed", assignment["warnings"][0])

    def test_the_default_review_policy_covers_an_unnamed_tier(self):
        policy = team.parse_team({"review": {"default": [{"provider": "codex"}]}})

        assignment = team.resolve_assignment(policy, tier=2, default_count=2)

        self.assertEqual(assignment["reviewer_source"], "team.review.default")
        self.assertEqual(assignment["reviewer_count"], 1)

    def test_a_gate_from_another_vendor_is_a_real_second_opinion(self):
        assignment = team.resolve_assignment(team.parse_team(FULL), tier=2, role="core")

        self.assertTrue(assignment["gate"]["distinct_ok"])
        self.assertEqual(assignment["gate"]["source"], "team.gate")
        self.assertEqual(assignment["warnings"], [])

    def test_a_gate_that_became_the_implementer_at_run_time_is_flagged(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=2, role="core", delegate="codex"
        )

        self.assertFalse(assignment["gate"]["distinct_ok"])
        self.assertIn("a second opinion from the first opinion", assignment["warnings"][0])

    def test_an_explicit_fix_seat_is_not_the_implementer(self):
        policy = team.parse_team({"fix": {"provider": "codex"}})

        assignment = team.resolve_assignment(policy, tier=2, delegate="agy:gemini-3.8-flash")

        self.assertEqual(assignment["fix"]["provider"], "codex")
        self.assertIsNone(assignment["fix"]["alias"])

    def test_the_implementer_alias_resolves_to_the_implementer_seat(self):
        assignment = team.resolve_assignment(
            team.parse_team(FULL), tier=2, role="core", default_count=2
        )

        self.assertEqual(assignment["fix"]["provider"], "agy")
        self.assertEqual(assignment["fix"]["alias"], "implementer")


class TestRequireDistinctVendors(unittest.TestCase):
    def test_unset_resolves_from_the_tier(self):
        self.assertFalse(team.require_distinct_vendors(None, 1))
        self.assertTrue(team.require_distinct_vendors(None, 2))
        self.assertTrue(team.require_distinct_vendors(None, 3))
        self.assertFalse(team.require_distinct_vendors(None, None))

    def test_an_explicit_setting_wins_in_both_directions(self):
        self.assertFalse(team.require_distinct_vendors(False, 3))
        self.assertTrue(team.require_distinct_vendors(True, 1))


class TestTeamIssues(unittest.TestCase):
    def issues(self, raw, **kwargs):
        return team.team_issues(raw, source="p.yaml: knobs.team", **kwargs)

    def test_no_block_and_a_malformed_block_report_nothing(self):
        self.assertEqual(self.issues(None), [])
        self.assertEqual(self.issues("team"), [])

    def test_the_dogfood_shape_is_valid(self):
        self.assertEqual(self.issues(FULL), [])

    def test_a_profile_name_is_a_provider(self):
        raw = {"implement": {"default": {"provider": "grok", "effort": "high"}}}

        self.assertEqual(self.issues(raw, profiles={"grok": "openai-compatible"}), [])

    def test_an_unknown_provider_is_refused_with_the_subagent_hint(self):
        errors = self.issues({"implement": {"default": {"provider": "backend-developer"}}})

        self.assertEqual(len(errors), 1)
        self.assertIn("unknown provider 'backend-developer'", errors[0])
        self.assertIn("'subagent:backend-developer'", errors[0])

    def test_a_subagent_prefix_needs_a_name(self):
        errors = self.issues({"fix": {"provider": "subagent:"}})

        self.assertIn("needs a subagent name after it", errors[0])

    def test_the_implementer_alias_is_only_valid_at_fix(self):
        self.assertEqual(self.issues({"fix": {"provider": "implementer"}}), [])

        errors = self.issues({"gate": {"provider": "implementer"}})

        self.assertIn("only valid at team.fix.provider", errors[0])

    def test_an_unknown_effort_is_named(self):
        errors = self.issues({"fix": {"provider": "codex", "effort": "maximum"}})

        self.assertIn("unknown effort 'maximum'", errors[0])

    def test_an_effort_a_provider_cannot_honour_is_refused(self):
        errors = self.issues({"implement": {"default": {"provider": "claude", "effort": "high"}}})

        self.assertIn("has no spelling for reasoning effort", errors[0])

    def test_a_subagent_seat_never_reaches_the_effort_check(self):
        errors = self.issues({"fix": {"provider": "subagent:x", "effort": "high"}})

        self.assertEqual(errors, [])

    def test_agy_needs_the_model_its_effort_suffix_rides_on(self):
        errors = self.issues({"implement": {"default": {"provider": "agy", "effort": "high"}}})

        self.assertIn("model suffix", errors[0])

    def test_a_gate_equal_to_the_implementer_is_not_a_second_opinion(self):
        errors = self.issues(
            {
                "implement": {"by_role": {"core": {"provider": "codex"}}},
                "gate": {"provider": "codex", "distinct_from": "implementer"},
            }
        )

        self.assertIn("is also the implementer at", errors[0])

    def test_a_gate_without_distinct_from_may_match_the_implementer(self):
        raw = {
            "implement": {"default": {"provider": "codex"}},
            "gate": {"provider": "codex"},
        }

        self.assertEqual(self.issues(raw), [])

    def test_only_the_implementer_seat_can_be_named_as_distinct_from(self):
        errors = self.issues({"gate": {"provider": "codex", "distinct_from": "reviewer"}})

        self.assertIn("is not a seat", errors[0])

    def test_an_integer_tier_key_is_explained_rather_than_accepted(self):
        errors = self.issues({"review": {"by_tier": {1: [{"provider": "claude"}]}}})

        self.assertIn('quote the key as "1", "2" or "3"', errors[0])

    def test_a_review_value_is_seats_or_the_jury_literal(self):
        errors = self.issues({"review": {"by_tier": {"3": "panel"}}})

        self.assertIn("neither a list of reviewer seats nor 'jury'", errors[0])

    def test_an_empty_reviewer_list_would_leave_the_change_unreviewed(self):
        errors = self.issues({"review": {"default": []}})

        self.assertIn("an empty reviewer list", errors[0])

    def test_a_tier_may_not_name_more_seats_than_keel_dispatches(self):
        seats = [{"provider": "claude"}, {"provider": "codex"}, {"provider": "agy"}]
        errors = self.issues({"review": {"by_tier": {"2": [*seats, {"provider": "ollama"}]}}})

        self.assertIn("keel dispatches at most 3", errors[0])

    def test_the_gate_rule_sees_the_deprecated_role_knob_too(self):
        """`implementer_agents` still resolves implementers, so the gate must clear them.

        Checking `team.implement*` alone let `implementer_agents: {core: codex}` sit beside
        `gate: {provider: codex, distinct_from: implementer}` — the mandatory second
        opinion being the first opinion, accepted by `keel validate`.
        """
        raw = {"gate": {"provider": "codex", "distinct_from": "implementer"}}

        errors = self.issues(raw, implementer_agents={"core": "codex"})

        self.assertIn("knobs.implementer_agents.core", errors[0])
        self.assertIn("is not a second opinion", errors[0])

    def test_a_legacy_subagent_role_does_not_clash_with_a_vendor_gate(self):
        raw = {"gate": {"provider": "codex", "distinct_from": "implementer"}}

        self.assertEqual(self.issues(raw, implementer_agents={"core": "backend-developer"}), [])

    def test_an_unknown_jury_mode_is_named(self):
        errors = self.issues({"jury": {"mode": "blocking"}})

        self.assertIn("unknown mode 'blocking'", errors[0])


if __name__ == "__main__":
    unittest.main()
