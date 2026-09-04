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

    def test_the_bench_does_not_move_with_the_jury_flags(self):
        """The bench is config + tier + role + --reviewers/--review-delegate. Nothing else.

        It cannot depend on the jury flags: the six commands that resolve a review
        contract do not all receive them (`keel review` has no `--no-jury`, and keel's CI
        passes it to `evidence-verify` on every run and to `ship`/`plan` on none), so a
        bench that moved with the flag would have two commands demanding different
        evidence of the same PR.
        """
        policy = team.parse_team({"review": {"by_tier": {"3": "jury"}}})
        baseline = team.resolve_assignment(policy, tier=3, default_count=3)

        for flags in (
            {"jury_disabled": True},
            {"jury_advisory": True},
            {"jury_disabled": True, "jury_advisory": True},
        ):
            with self.subTest(**flags):
                assignment = team.resolve_assignment(policy, tier=3, default_count=3, **flags)

                self.assertEqual(assignment["review_panel"], "jury")
                self.assertEqual(assignment["reviewer_count"], 0)
                self.assertTrue(assignment["jury"]["panel_is_review"])
                self.assertEqual(assignment["reviewers"], baseline["reviewers"])
                # Recorded, not applied.
                self.assertIn("does not apply", assignment["warnings"][0])
                self.assertIn("the panel is the review", assignment["warnings"][0].lower())

    def test_the_jury_flags_say_nothing_on_a_bench_they_do_not_reach(self):
        policy = team.parse_team({"review": {"by_tier": {"2": [{"provider": "codex"}]}}})

        assignment = team.resolve_assignment(
            policy, tier=2, default_count=2, jury_disabled=True, jury_advisory=True
        )

        self.assertEqual(assignment["warnings"], [])

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

    def test_an_advisory_jury_may_not_also_be_the_review_panel(self):
        """Together they mean a tier with nothing required of it.

        The panel is the review, so the tier has no host reviewers; an advisory verdict is
        not required evidence. A project would be marking a tier strictest and getting the
        weakest gate it has.
        """
        errors = self.issues({"review": {"by_tier": {"3": "jury"}}, "jury": {"mode": "advisory"}})

        self.assertIn("no enforceable review", errors[0])
        self.assertIn("review.by_tier.3", errors[0])

    def test_an_advisory_jury_is_fine_when_the_panel_is_not_the_review(self):
        raw = {
            "review": {"by_tier": {"3": [{"provider": "claude"}]}},
            "jury": {"mode": "advisory"},
        }

        self.assertEqual(self.issues(raw), [])


BENCHED = {
    "implement": {
        "default": {"provider": "claude"},
        "by_role": {"core": {"provider": "claude", "effort": "medium"}},
    },
    "lead": {"provider": "claude", "model": "opus"},
    "by_difficulty": {
        "easy": {
            "implement": {"provider": "ollama", "model": "qwen"},
            "review": [{"provider": "claude"}],
        },
        "hard": {
            "lead": {"provider": "codex"},
            "implement": {"provider": "codex"},
            "review": "jury",
            "effort": "high",
        },
    },
    "profiles": {
        "night-shift": {"implement": {"provider": "agy", "model": "gemini-3.8-pro"}},
    },
}


class TestBench(unittest.TestCase):
    """``team.by_difficulty`` / ``team.profiles`` — the benches a batch staffs from (#1017)."""

    def policy(self, raw=None):
        return team.parse_team(BENCHED if raw is None else raw)

    def test_a_band_names_the_bench_that_staffs_that_weight_of_work(self):
        assignment = team.resolve_assignment(self.policy(), tier=2, role="core", difficulty="hard")

        self.assertEqual(assignment["implementer"]["provider"], "codex")
        self.assertEqual(assignment["implementer"]["source"], "team.by_difficulty.hard.implement")
        self.assertEqual(assignment["difficulty"], "hard")
        self.assertEqual(assignment["bench"], ["team.by_difficulty.hard"])

    def test_a_bench_outranks_the_role_because_it_is_the_more_specific_statement(self):
        """`by_role` says which part of the system; a band says what the work costs.

        "The hard ones go to the strong implementer" is only expressible if the second
        wins — otherwise every role keeps its default seat and the table does nothing.
        """
        easy = team.resolve_assignment(self.policy(), tier=2, role="core", difficulty="easy")
        unbanded = team.resolve_assignment(self.policy(), tier=2, role="core")

        self.assertEqual(easy["implementer"]["provider"], "ollama")
        self.assertEqual(unbanded["implementer"]["source"], "team.implement.by_role.core")

    def test_a_named_profile_outranks_the_scored_band(self):
        assignment = team.resolve_assignment(
            self.policy(), tier=2, role="core", difficulty="hard", team_profile="night-shift"
        )

        self.assertEqual(assignment["implementer"]["provider"], "agy")
        self.assertEqual(assignment["team_profile"], "night-shift")
        self.assertEqual(
            assignment["bench"], ["team.profiles.night-shift", "team.by_difficulty.hard"]
        )

    def test_a_profile_that_names_only_an_implementer_leaves_the_band_reviewers_standing(self):
        """Each field resolves down the bench list on its own.

        A profile is not a replacement policy: taking the whole bench from the first match
        would silently drop the band's reviewers the moment an operator passed --team.
        """
        assignment = team.resolve_assignment(
            self.policy(), tier=2, role="core", difficulty="hard", team_profile="night-shift"
        )

        self.assertEqual(assignment["review_panel"], "jury")
        self.assertEqual(assignment["reviewer_source"], "team.by_difficulty.hard.review")

    def test_a_bench_review_list_replaces_the_tier_bench(self):
        assignment = team.resolve_assignment(self.policy(), tier=3, role="core", difficulty="easy")

        self.assertEqual([seat["provider"] for seat in assignment["reviewers"]], ["claude"])
        self.assertEqual(assignment["reviewer_source"], "team.by_difficulty.easy.review")

    def test_an_unknown_profile_is_reported_rather_than_silently_ignored(self):
        assignment = team.resolve_assignment(self.policy(), tier=2, team_profile="weekend")
        warning = next(w for w in assignment["warnings"] if "--team" in w)

        self.assertIn("--team 'weekend'", warning)
        self.assertIn("night-shift", warning)

    def test_an_unknown_profile_on_a_project_with_no_profiles_says_none_are_known(self):
        assignment = team.resolve_assignment(team.TeamPolicy(), tier=2, team_profile="weekend")

        self.assertIn("Known: none", next(w for w in assignment["warnings"] if "--team" in w))

    def test_the_lead_comes_from_the_bench_then_the_policy_then_the_host(self):
        policy = self.policy()

        self.assertEqual(
            team.resolve_assignment(policy, tier=2, difficulty="hard")["lead"]["provider"], "codex"
        )
        self.assertEqual(team.resolve_assignment(policy, tier=2)["lead"]["source"], "team.lead")
        self.assertEqual(
            team.resolve_assignment(team.TeamPolicy(), tier=2, host_agent="agy")["lead"],
            {
                "provider": "agy",
                "name": "agy",
                "kind": "provider",
                "model": None,
                "effort": None,
                "source": "host",
            },
        )

    def test_effort_is_the_flag_then_the_seat_then_the_bench(self):
        policy = self.policy()

        # The bench names an effort and its seat does not: the bench fills it in.
        self.assertEqual(
            team.resolve_assignment(policy, tier=2, difficulty="hard")["effort"], "high"
        )
        # The role seat names its own: no bench applies, so the seat stands.
        self.assertEqual(team.resolve_assignment(policy, tier=2, role="core")["effort"], "medium")
        # The flag is the operator speaking about this run and outranks both.
        self.assertEqual(
            team.resolve_assignment(policy, tier=2, role="core", effort="low")["effort"], "low"
        )

    def test_the_fix_seat_inherits_the_implementer_effort_through_the_alias(self):
        policy = team.parse_team({**BENCHED, "fix": {"provider": "implementer"}})

        assignment = team.resolve_assignment(policy, tier=2, difficulty="hard", effort="low")

        self.assertEqual(assignment["fix"]["provider"], "codex")
        self.assertEqual(assignment["fix"]["effort"], "low")

    def test_a_bench_field_the_profile_omits_falls_through_to_the_band(self):
        """Resolution walks the whole bench list per field, not just the first bench."""
        policy = team.parse_team(
            {
                **BENCHED,
                "profiles": {"reviewers-only": {"review": [{"provider": "claude"}]}},
            }
        )

        assignment = team.resolve_assignment(
            policy, tier=2, difficulty="hard", team_profile="reviewers-only"
        )

        self.assertEqual(assignment["implementer"]["source"], "team.by_difficulty.hard.implement")
        self.assertEqual(assignment["reviewer_source"], "team.profiles.reviewers-only.review")

    def test_a_bench_that_names_nothing_is_absent_rather_than_an_empty_override(self):
        policy = team.parse_team({**BENCHED, "by_difficulty": {"hard": {}}, "profiles": "nope"})

        self.assertEqual(policy.by_difficulty, {})
        self.assertEqual(policy.profiles, {})

    def test_a_malformed_bench_entry_is_dropped(self):
        self.assertEqual(team.parse_team({"by_difficulty": {"hard": "codex"}}).by_difficulty, {})

    def test_a_bench_canonicalises_only_the_fields_it_names(self):
        policy = team.parse_team({"by_difficulty": {"easy": {"lead": {"provider": "claude"}}}})

        self.assertEqual(
            team.canonical(policy)["team"]["by_difficulty"],
            {"easy": {"lead": {"provider": "claude", "model": None, "effort": None}}},
        )

    def test_canonical_round_trips_every_bench_field(self):
        policy = self.policy()

        canonical = team.canonical(policy)["team"]

        self.assertEqual(canonical["lead"], {"provider": "claude", "model": "opus", "effort": None})
        self.assertEqual(canonical["by_difficulty"]["hard"]["review"], "jury")
        self.assertEqual(canonical["by_difficulty"]["hard"]["effort"], "high")
        self.assertEqual(
            canonical["by_difficulty"]["easy"]["review"],
            [{"provider": "claude", "model": None, "effort": None}],
        )
        self.assertEqual(
            canonical["profiles"]["night-shift"]["implement"],
            {"provider": "agy", "model": "gemini-3.8-pro", "effort": None},
        )
        self.assertEqual(team.parse_team(canonical), policy)

    def test_an_unconfigured_policy_still_canonicalises_to_nothing(self):
        """The `config_hash` guarantee: it changes iff `team` does."""
        self.assertEqual(team.canonical(team.TeamPolicy()), {})
        self.assertNotIn("lead", team.canonical(team.parse_team({}))["team"])


class TestBenchValidation(unittest.TestCase):
    def issues(self, raw):
        return team.team_issues(raw, source="knobs.team")

    def test_a_band_key_the_scorer_never_emits_is_an_error(self):
        errors = self.issues({"by_difficulty": {"medium": {"implement": {"provider": "codex"}}}})

        self.assertIn("'medium' is not a difficulty band", errors[0])
        self.assertIn("easy, standard, hard", errors[0])

    def test_the_default_lead_seat_is_validated_like_any_other(self):
        errors = self.issues({"lead": {"provider": "nope"}})

        self.assertIn("knobs.team.lead: unknown provider 'nope'", errors[0])

    def test_a_non_mapping_by_difficulty_is_left_to_the_schema(self):
        self.assertEqual(self.issues({"by_difficulty": "hard"}), [])

    def test_an_unknown_provider_in_a_bench_seat_is_reported_with_its_path(self):
        errors = self.issues(
            {
                "by_difficulty": {"hard": {"lead": {"provider": "nope"}}},
                "profiles": {"night": {"implement": {"provider": "alsonope"}}},
            }
        )

        self.assertIn("knobs.team.by_difficulty.hard.lead: unknown provider 'nope'", errors[0])
        self.assertIn("knobs.team.profiles.night.implement", errors[1])

    def test_a_bench_reviewer_bench_obeys_the_same_rules_as_a_tier_bench(self):
        errors = self.issues(
            {
                "by_difficulty": {"easy": {"review": []}},
                "profiles": {
                    "wide": {
                        "review": [
                            {"provider": "claude"},
                            {"provider": "codex"},
                            {"provider": "agy"},
                            {"provider": "ollama"},
                        ]
                    }
                },
            }
        )

        self.assertIn("by_difficulty.easy.review: an empty reviewer list", errors[0])
        self.assertIn("profiles.wide.review: 4 reviewer seats", errors[1])

    def test_a_bench_implementer_is_an_implementer_for_the_distinct_gate_check(self):
        """A gate that matches a bench seat is the first opinion wearing a newer spelling."""
        errors = self.issues(
            {
                "gate": {"provider": "codex", "distinct_from": "implementer"},
                "by_difficulty": {"hard": {"implement": {"provider": "codex"}}},
            }
        )

        self.assertIn("is also the implementer at", errors[0])
        self.assertIn("by_difficulty.hard.implement", errors[0])


class TestBenchEffortValidation(unittest.TestCase):
    """A bench `effort` is action at a distance, so it is checked where it lands (#1017).

    A seat's own `effort` sits next to its provider — one line, both halves visible.
    `by_difficulty.hard.effort` lands on whichever implementer resolves for that band,
    written somewhere else entirely, so it bypassed every rule `implement.default.effort`
    has faced since #1014: an agy seat with no model, a provider with no effort dial, a
    host subagent with no dial at all — all validated clean and all silently dropped.
    """

    def issues(self, raw):
        return team.team_issues(raw, source="knobs.team")

    def test_agy_still_needs_the_model_its_effort_suffix_rides_on(self):
        errors = self.issues(
            {
                "implement": {"default": {"provider": "agy"}},
                "by_difficulty": {"hard": {"effort": "high"}},
            }
        )

        self.assertIn("knobs.team.by_difficulty.hard.effort", errors[0])
        self.assertIn("agy spells reasoning effort as a model suffix", errors[0])
        self.assertIn("applied to the implementer at implement.default", errors[0])

    def test_a_subagent_implementer_has_no_dial_to_set(self):
        errors = self.issues(
            {
                "implement": {"default": {"provider": "subagent:backend-developer"}},
                "by_difficulty": {"hard": {"effort": "high"}},
            }
        )

        self.assertIn("is a host subagent", errors[0])
        self.assertIn("silently dropped", errors[0])

    def test_a_seat_level_subagent_effort_is_still_tolerated(self):
        """#1014's choice, and the distinction this check turns on: there the operator
        wrote both halves on one line and could see the pairing."""
        self.assertEqual(self.issues({"fix": {"provider": "subagent:x", "effort": "high"}}), [])

    def test_a_provider_with_no_effort_spelling_is_reported(self):
        errors = self.issues(
            {
                "implement": {"default": {"provider": "claude"}},
                "profiles": {"night": {"effort": "low"}},
            }
        )

        self.assertIn("knobs.team.profiles.night.effort", errors[0])
        self.assertIn("has no spelling for reasoning effort", errors[0])

    def test_an_unknown_bench_effort_is_reported_like_a_seat_one(self):
        errors = self.issues(
            {
                "implement": {"default": {"provider": "codex"}},
                "by_difficulty": {"hard": {"effort": "extreme"}},
            }
        )

        self.assertIn("unknown effort 'extreme'", errors[0])

    def test_a_seat_that_names_its_own_effort_never_receives_the_bench_one(self):
        self.assertEqual(
            self.issues(
                {
                    "implement": {
                        "default": {
                            "provider": "agy",
                            "model": "gemini-3.8-flash-high",
                            "effort": "high",
                        }
                    },
                    "by_difficulty": {"hard": {"effort": "high"}},
                }
            ),
            [],
        )

    def test_a_bench_naming_its_own_implementer_is_checked_against_that_seat_alone(self):
        """It can only land there, so an unrelated role seat must not be dragged in."""
        errors = self.issues(
            {
                "implement": {"default": {"provider": "claude"}},
                "by_difficulty": {"hard": {"implement": {"provider": "codex"}, "effort": "high"}},
            }
        )

        self.assertEqual(errors, [])

    def test_every_implementer_a_band_could_reach_is_checked(self):
        """With no bench implementer the band lands on whichever seat resolves, so all of
        them — including the deprecated knob's — are candidates."""
        errors = team.team_issues(
            {
                "implement": {"by_role": {"core": {"provider": "claude"}}},
                "by_difficulty": {"hard": {"effort": "high"}},
            },
            source="knobs.team",
            implementer_agents={"docs": "ollama"},
        )

        paths = " ".join(errors)
        self.assertIn("implement.by_role.core", paths)
        self.assertIn("knobs.implementer_agents.docs", paths)

    def test_a_bench_with_no_effort_is_not_checked(self):
        self.assertEqual(
            self.issues({"by_difficulty": {"hard": {"implement": {"provider": "claude"}}}}), []
        )

    def test_a_policy_with_no_implementer_at_all_leans_on_the_host_and_says_nothing(self):
        """The host is a per-run flag; validating against one operator's default is the
        kind of machine-dependent rule #1014 refused."""
        self.assertEqual(self.issues({"by_difficulty": {"hard": {"effort": "high"}}}), [])


if __name__ == "__main__":
    unittest.main()
