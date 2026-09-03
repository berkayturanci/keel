"""Unit tests for the `--wizard` question/answer planner and its thin driver (#1018).

Everything here is offline and deterministic: the probe is a literal dict, the
terminal is a list of strings, and the `isatty` answer is a lambda. That is the whole
point of the seams — the wizard's promises ("never offers an unavailable provider",
"a non-TTY run is a logged no-op") are only worth something if they can be asserted
without a machine that happens to have four agent CLIs installed.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import unittest

from keel import cli, scaffold, team, wizard, wizardrun
from keel import config as cfg


def _row(
    name,
    *,
    vendor=None,
    available=True,
    transport="cli",
    source="builtin",
    models=(),
    tools=True,
):
    return {
        "name": name,
        "vendor": vendor or name,
        "transport": transport,
        "source": source,
        "available": available,
        "reason": "fixture",
        "models": list(models),
        "capabilities": {"tools": tools, "read_only_mode": True, "model_selection": True},
    }


#: The acceptance-criteria fixture: only `claude` and `ollama:qwen2.5-coder` are
#: usable; `codex` is installed nowhere.
PROBE = {
    "schema_version": "keel.providers.v1",
    "providers": [
        _row("claude"),
        _row("codex", available=False),
        _row("agy", available=False),
        _row("ollama", transport="local", models=("qwen2.5-coder",), tools=False),
    ],
    "registry_path": "/home/op/.keel/providers.yaml",
    "registry_present": False,
    "warnings": [],
    "errors": [],
    "available": 2,
    "total": 4,
}


def _catalog(report=None):
    return wizard.Catalog.from_report(PROBE if report is None else report)


def _scripted(*answers):
    """An ``ask`` seam that replays ``answers``, then keeps returning the default."""
    remaining = list(answers)

    def ask(_prompt, default):
        return remaining.pop(0) if remaining else default

    return ask


class TestCatalog(unittest.TestCase):
    def test_only_available_rows_become_candidates(self):
        self.assertEqual(_catalog().names(), ("claude", "ollama"))

    def test_models_and_capabilities_carry_through(self):
        ollama = _catalog().get("ollama")
        self.assertEqual(ollama.models, ("qwen2.5-coder",))
        self.assertFalse(ollama.tools)
        self.assertEqual(ollama.transport, "local")

    def test_unknown_name_resolves_to_none(self):
        self.assertIsNone(_catalog().get("codex"))
        self.assertFalse(_catalog().has("codex"))

    def test_a_report_that_is_not_a_mapping_yields_nothing(self):
        self.assertEqual(wizard.Catalog.from_report(["not", "a", "report"]).candidates, ())

    def test_a_providers_key_that_is_not_a_list_yields_nothing(self):
        self.assertEqual(wizard.Catalog.from_report({"providers": {}}).candidates, ())

    def test_malformed_rows_are_skipped_not_raised(self):
        report = {
            "providers": [
                "a string",
                {"available": True},
                {"name": "   ", "available": True},
                {"name": "ok", "available": True},
            ]
        }
        self.assertEqual(wizard.Catalog.from_report(report).names(), ("ok",))

    def test_missing_fields_fall_back_without_inventing_a_vendor(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [{"name": "solo", "available": True, "capabilities": "junk"}]}
        )
        candidate = catalog.get("solo")
        self.assertEqual(candidate.vendor, "solo")
        self.assertEqual((candidate.transport, candidate.source), ("cli", "builtin"))
        self.assertFalse(candidate.tools)
        self.assertEqual(candidate.models, ())

    def test_blank_vendor_and_non_list_models_degrade(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [{"name": "solo", "vendor": "  ", "available": True, "models": "junk"}]}
        )
        self.assertEqual(catalog.get("solo").vendor, "solo")
        self.assertEqual(catalog.get("solo").models, ())

    def test_blank_model_entries_are_dropped(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [_row("agy", models=("gemini-3.8-flash", "  ", 7))]}
        )
        self.assertEqual(catalog.get("agy").models, ("gemini-3.8-flash",))

    def test_spread_puts_distinct_vendors_first(self):
        report = {
            "providers": [
                _row("a", vendor="claude"),
                _row("b", vendor="claude"),
                _row("c", vendor="agy"),
            ]
        }
        self.assertEqual(
            tuple(c.name for c in wizard.Catalog.from_report(report).spread()), ("a", "c", "b")
        )

    def test_detail_names_transport_source_tools_and_models(self):
        self.assertEqual(_catalog().get("claude").detail(), "cli · builtin · tools")
        self.assertEqual(
            _catalog().get("ollama").detail(), "local · builtin · no tools · 1 model(s)"
        )

    def test_effort_is_the_delegate_vocabulary(self):
        self.assertFalse(_catalog().get("claude").effort)
        self.assertTrue(wizard.Catalog.from_report({"providers": [_row("agy")]}).get("agy").effort)


class TestQuestion(unittest.TestCase):
    def _question(self, key="implement.provider"):
        state = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        return next(q for q in state.questions() if q.key == key)

    def test_the_unavailable_provider_is_never_offered(self):
        self.assertEqual(self._question().values(), ("claude", "ollama"))

    def test_selecting_an_unavailable_provider_is_impossible(self):
        value, error = self._question().normalize("codex")
        self.assertIsNone(value)
        self.assertIn("'codex' is not on offer here", error)

    def test_a_blank_answer_is_the_default(self):
        self.assertEqual(self._question().normalize("  "), ("claude", None))
        self.assertEqual(self._question().normalize(None), ("claude", None))

    def test_a_comma_only_answer_is_the_default(self):
        self.assertEqual(self._question("review").normalize(" , , "), ("ollama,claude", None))

    def test_the_default_is_rendered_first_and_marked(self):
        text = self._question().text()
        self.assertIn("Implementer provider — who writes the change at s4", text)
        self.assertLess(text.index("claude (default)"), text.index("ollama"))

    def test_a_question_without_help_still_renders(self):
        question = wizard.Question("k", "Prompt", (wizard.Choice("a"),), "a")
        self.assertEqual(question.text(), "Prompt\n    a (default)")

    def test_a_multi_question_says_how_many_values_it_takes(self):
        self.assertIn("comma-separate up to 2", self._question("review").text())

    def test_too_many_values_are_refused(self):
        value, error = self._question("review").normalize("claude,ollama,claude")
        self.assertIsNone(value)
        self.assertIn("at most 2 value(s), got 3", error)

    def test_the_jury_panel_cannot_be_combined_with_named_seats(self):
        state = wizard.start(_catalog(), jury="gating").with_answer("mode", wizard.CUSTOMIZE)
        question = next(q for q in state.questions() if q.key == "review")
        value, error = question.normalize("jury,claude")
        self.assertIsNone(value)
        self.assertIn("cannot be combined with named seats", error)


class TestWalk(unittest.TestCase):
    def test_quick_start_asks_one_question_and_resolves_everything(self):
        state = wizard.start(_catalog())
        self.assertEqual([q.key for q in state.questions()], ["mode"])
        resolution = state.resolve()
        self.assertTrue(resolution.quick_start)
        self.assertEqual(resolution.implement, team.Seat(provider="claude"))

    def test_customize_reveals_the_rest(self):
        state = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        self.assertEqual(
            [q.key for q in state.questions()],
            ["mode", "implement.provider", "gate.provider", "jury", "review", "review_comments"],
        )

    def test_the_model_question_appears_only_for_a_provider_that_lists_models(self):
        state = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        state = state.with_answer("implement.provider", "ollama")
        keys = [q.key for q in state.questions()]
        self.assertIn("implement.model", keys)
        self.assertEqual(state.resolve().implement.model, None)
        chosen = state.with_answer("implement.model", "qwen2.5-coder").resolve()
        self.assertEqual(wizard.seat_token(chosen.implement), "ollama:qwen2.5-coder")

    def test_the_effort_question_appears_only_for_a_provider_that_can_honour_it(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [_row("agy", models=("gemini-3.8-flash",)), _row("claude")]}
        )
        state = wizard.start(catalog).with_answer("mode", wizard.CUSTOMIZE)
        state = state.with_answer("implement.model", "gemini-3.8-flash")
        self.assertIn("implement.effort", [q.key for q in state.questions()])
        answered = state.with_answer("implement.effort", "high").resolve()
        self.assertEqual(answered.implement.effort, "high")
        none = state.with_answer("implement.effort", wizard.NONE).resolve()
        self.assertIsNone(none.implement.effort)

    def test_a_vendor_that_spells_effort_as_a_model_suffix_is_not_asked_without_one(self):
        """Otherwise the wizard writes the exact pair `keel validate` rejects."""
        catalog = wizard.Catalog.from_report(
            {"providers": [_row("agy", models=("gemini-3.8-flash",))]}
        )
        policy = team.parse_team(
            {"implement": {"default": {"provider": "agy", "model": "x", "effort": "high"}}}
        )
        state = wizard.start(catalog, policy=policy).with_answer("mode", wizard.CUSTOMIZE)
        state = state.with_answer("implement.model", wizard.NONE)
        self.assertNotIn("implement.effort", [q.key for q in state.questions()])
        self.assertIsNone(state.resolve().implement.effort)

    def test_a_vendor_with_no_listing_at_all_never_reaches_the_effort_question(self):
        catalog = wizard.Catalog.from_report({"providers": [_row("agy")]})
        state = wizard.start(catalog).with_answer("mode", wizard.CUSTOMIZE)
        self.assertNotIn("implement.effort", [q.key for q in state.questions()])
        self.assertIsNone(state.resolve().implement.effort)

    def test_the_gate_never_offers_the_implementer(self):
        state = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        gate = next(q for q in state.questions() if q.key == "gate.provider")
        self.assertEqual(gate.values(), (wizard.NONE, "ollama"))

    def test_the_jury_panel_is_offered_as_a_review_only_when_the_jury_is_on(self):
        def bench(state):
            return next(q for q in state.questions() if q.key == "review").values()

        off = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        self.assertNotIn(team.JURY_PANEL, bench(off))
        self.assertIn(team.JURY_PANEL, bench(off.with_answer("jury", "gating")))

    def test_a_jury_panel_review_stages_no_reviewer_flags(self):
        state = (
            wizard.start(_catalog())
            .with_answer("mode", wizard.CUSTOMIZE)
            .with_answer("jury", "gating")
            .with_answer("review", team.JURY_PANEL)
        )
        resolution = state.resolve()
        self.assertEqual(resolution.review, team.JURY_PANEL)
        self.assertNotIn("--reviewers", resolution.flags())
        self.assertIn("--jury", resolution.flags())

    def test_an_answer_seated_outside_the_offer_falls_back_to_the_default(self):
        """Every seat is guarded, not just the implementer (#1018 round 2, finding 4).

        `normalize` closes the interactive and `--wizard-answer` paths, but an answer
        seated straight onto `State` reaches the walk unfiltered — and the module
        promises the offer holds "even through an injected seam".
        """
        base = {"mode": wizard.CUSTOMIZE}
        implementer = wizard.State(
            catalog=_catalog(), answers={**base, "implement.provider": "ghost"}
        )
        self.assertEqual(implementer.resolve().implement.provider, "claude")

        gate = wizard.State(catalog=_catalog(), answers={**base, "gate.provider": "ghost"})
        self.assertIsNone(gate.resolve().gate)

        bench = wizard.State(catalog=_catalog(), answers={**base, "review": "ghost"})
        self.assertEqual([seat.provider for seat in bench.resolve().review], ["ollama", "claude"])

        mixed = wizard.State(catalog=_catalog(), answers={**base, "review": "ghost,claude"})
        self.assertEqual([seat.provider for seat in mixed.resolve().review], ["claude"])

    def test_a_gate_seated_as_the_implementer_is_refused(self):
        """`keel validate` refuses a gate that is the seat which wrote the change."""
        state = wizard.State(
            catalog=_catalog(),
            answers={
                "mode": wizard.CUSTOMIZE,
                "implement.provider": "claude",
                "gate.provider": "claude",
            },
        )
        self.assertIsNone(state.resolve().gate)

    def test_a_panel_seated_beside_a_jury_that_cannot_gate_is_refused(self):
        for mode in ("advisory", wizard.JURY_OFF):
            state = wizard.State(
                catalog=_catalog(),
                answers={"mode": wizard.CUSTOMIZE, "jury": mode, "review": team.JURY_PANEL},
            )
            self.assertNotEqual(state.resolve().review, team.JURY_PANEL)

    def test_an_empty_catalog_cannot_be_walked(self):
        with self.assertRaises(wizard.WizardError):
            wizard.start(wizard.Catalog()).resolve()

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaises(ValueError):
            wizard.start(_catalog(), scope="middle")

    def test_out_of_vocabulary_flag_defaults_are_ignored(self):
        state = wizard.start(_catalog(), review_comments="carrier-pigeon", jury="sometimes")
        self.assertEqual(state.review_comments, "inline")
        self.assertEqual(state.jury, wizard.JURY_OFF)


class TestDefaultsComeFromThePolicy(unittest.TestCase):
    def _policy(self, raw):
        return team.parse_team(raw)

    def test_an_available_policy_implementer_is_the_default(self):
        policy = self._policy({"implement": {"default": {"provider": "ollama", "model": "x"}}})
        state = wizard.start(_catalog(), policy=policy)
        self.assertEqual(state.resolve().implement.provider, "ollama")

    def test_an_unavailable_policy_implementer_degrades_to_what_is_here(self):
        policy = self._policy({"implement": {"default": {"provider": "codex"}}})
        resolution = wizard.start(_catalog(), policy=policy).resolve()
        self.assertEqual(resolution.implement.provider, "claude")

    def test_the_delegate_flag_wins_over_the_policy(self):
        policy = self._policy({"implement": {"default": {"provider": "claude"}}})
        state = wizard.start(_catalog(), policy=policy, delegate="ollama:qwen2.5-coder")
        self.assertEqual(wizard.seat_token(state.resolve().implement), "ollama:qwen2.5-coder")

    def test_an_unavailable_delegate_flag_is_not_a_default_either(self):
        state = wizard.start(_catalog(), delegate="codex")
        self.assertEqual(state.resolve().implement.provider, "claude")

    def test_an_available_policy_gate_is_the_default(self):
        policy = self._policy(
            {
                "implement": {"default": {"provider": "claude"}},
                "gate": {"provider": "ollama", "distinct_from": "implementer"},
            }
        )
        resolution = wizard.start(_catalog(), policy=policy).resolve()
        self.assertEqual(resolution.gate.provider, "ollama")
        self.assertEqual(resolution.gate.distinct_from, team.IMPLEMENTER)

    def test_a_gate_that_would_be_the_implementer_is_dropped(self):
        policy = self._policy({"gate": {"provider": "claude"}})
        self.assertIsNone(wizard.start(_catalog(), policy=policy).resolve().gate)

    def test_an_unavailable_gate_is_dropped(self):
        policy = self._policy({"gate": {"provider": "codex"}})
        self.assertIsNone(wizard.start(_catalog(), policy=policy).resolve().gate)

    def test_a_policy_bench_is_filtered_to_what_is_available(self):
        policy = self._policy(
            {"review": {"by_tier": {"2": [{"provider": "codex"}, {"provider": "ollama"}]}}}
        )
        resolution = wizard.start(_catalog(), policy=policy).resolve()
        self.assertEqual(resolution.review, (team.Seat(provider="ollama"),))

    def test_a_bench_with_nothing_available_falls_back_to_the_tier_default(self):
        policy = self._policy({"review": {"by_tier": {"2": [{"provider": "codex"}]}}})
        self.assertEqual(
            [seat.provider for seat in wizard.start(_catalog(), policy=policy).resolve().review],
            ["ollama", "claude"],
        )

    def test_a_tier_whose_policy_is_the_jury_stays_the_jury_while_it_gates(self):
        policy = self._policy({"review": {"by_tier": {"2": "jury"}}, "jury": {"mode": "gating"}})
        state = wizard.start(_catalog(), policy=policy, jury="gating")
        self.assertEqual(state.resolve().review, team.JURY_PANEL)

    def test_a_jury_that_cannot_gate_never_leaves_a_panel_as_the_bench(self):
        """`team._review_issues` refuses an advisory jury beside a jury panel."""
        policy = self._policy({"review": {"by_tier": {"2": "jury"}}})
        for mode in ("advisory", wizard.JURY_OFF):
            state = wizard.start(_catalog(), policy=policy, jury=mode)
            self.assertNotEqual(state.resolve().review, team.JURY_PANEL)

    def test_the_default_review_applies_where_no_tier_names_one(self):
        policy = self._policy({"review": {"default": [{"provider": "ollama"}]}})
        self.assertEqual(
            wizard.start(_catalog(), policy=policy).resolve().review,
            (team.Seat(provider="ollama"),),
        )

    def test_a_bench_keeps_the_model_its_policy_pinned(self):
        policy = self._policy(
            {"review": {"by_tier": {"2": [{"provider": "ollama", "model": "qwen2.5-coder"}]}}}
        )
        state = wizard.start(_catalog(), policy=policy).with_answer("mode", wizard.CUSTOMIZE)
        resolution = state.with_answer("review", "ollama").resolve()
        self.assertEqual(wizard.seat_token(resolution.review[0]), "ollama:qwen2.5-coder")

    def test_the_jury_mode_starts_from_the_policy(self):
        policy = self._policy({"jury": {"mode": "advisory"}})
        state = wizard.start(_catalog(), policy=policy, jury=policy.jury_mode)
        self.assertEqual(state.resolve().jury, "advisory")

    def test_unavailable_lists_only_the_provider_seats_the_machine_cannot_reach(self):
        policy = self._policy(
            {
                "implement": {
                    "default": {"provider": "codex"},
                    "by_role": {"core": {"provider": "codex"}},
                },
                "gate": {"provider": "agy"},
                "review": {
                    "default": [{"provider": "subagent:opus-reviewer"}],
                    "by_tier": {"1": "jury", "2": [{"provider": "claude"}]},
                },
                "fix": {"provider": "implementer"},
            }
        )
        self.assertEqual(wizard.unavailable(policy, _catalog()), ("codex", "agy"))


class TestResolution(unittest.TestCase):
    def _resolution(self, **answers):
        state = wizard.start(_catalog())
        for key, value in answers.items():
            state = state.with_answer(key.replace("__", "."), value)
        return state.resolve()

    def test_flags_are_the_literal_ship_grammar(self):
        resolution = self._resolution(
            mode=wizard.CUSTOMIZE,
            implement__provider="claude",
            review="claude",
            review_comments="inline",
            jury=wizard.JURY_OFF,
        )
        self.assertEqual(
            resolution.flags(),
            (
                "--delegate",
                "claude",
                "--reviewers",
                "1",
                "--review-delegate",
                "claude",
                "--review-comments",
                "inline",
                "--no-jury",
            ),
        )

    def test_only_an_answered_question_produces_a_flag(self):
        """A resolved default is not a decision; writing it back overrides the policy."""
        self.assertEqual(self._resolution().flags(), ())
        self.assertEqual(self._resolution(mode=wizard.CUSTOMIZE).flags(), ())
        one = self._resolution(mode=wizard.CUSTOMIZE, jury="gating")
        self.assertEqual(one.flags(), ("--jury",))

    def test_the_jury_mode_picks_exactly_one_jury_flag(self):
        for mode, flag in (("gating", "--jury"), ("advisory", "--jury-advisory")):
            resolution = self._resolution(mode=wizard.CUSTOMIZE, jury=mode)
            self.assertEqual(resolution.flags(), (flag,))

    def test_an_all_defaults_echo_says_nothing_is_overridden(self):
        self.assertIn("(none — every option kept its default", wizard.render(self._resolution()))

    def test_the_echo_names_the_seats_behind_the_flags(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [_row("agy", models=("gemini-3.8-flash",)), _row("claude")]}
        )
        state = wizard.start(catalog).with_answer("mode", wizard.CUSTOMIZE)
        state = state.with_answer("implement.model", "gemini-3.8-flash")
        state = state.with_answer("implement.effort", "high").with_answer("gate.provider", "claude")
        rendered = wizard.render(state.resolve())
        self.assertIn("  flags : --delegate agy:gemini-3.8-flash", rendered)
        self.assertIn("effort=high", rendered)
        self.assertIn("gate=claude (distinct from the implementer)", rendered)
        self.assertIn("review=claude,agy", rendered)
        # The bench was never answered, so it is reported but not forced onto the run.
        self.assertNotIn("--reviewers", rendered.splitlines()[0])

    def test_the_echo_names_a_jury_panel_and_every_tier(self):
        state = (
            wizard.start(_catalog(), scope=wizard.SCOPE_CONFIG)
            .with_answer("mode", wizard.CUSTOMIZE)
            .with_answer("jury", "gating")
            .with_answer("review.3", team.JURY_PANEL)
        )
        rendered = wizard.render(state.resolve())
        self.assertIn("review[3]=jury", rendered)
        run = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        run = run.with_answer("jury", "gating").with_answer("review", team.JURY_PANEL)
        self.assertIn("review=jury", wizard.render(run.resolve()))

    def test_as_dict_is_json_stable(self):
        payload = self._resolution().as_dict()
        self.assertEqual(payload["schema_version"], wizard.SCHEMA_VERSION)
        self.assertEqual(payload["scope"], wizard.SCOPE_RUN)
        self.assertTrue(payload["quick_start"])
        self.assertEqual(payload["review_by_tier"], {})
        self.assertIsNone(payload["gate"])
        self.assertEqual(payload["jury"], wizard.JURY_OFF)
        self.assertEqual(payload["team"]["fix"], {"provider": "implementer"})

    def test_as_dict_renders_a_jury_panel_and_tiers_as_strings(self):
        state = (
            wizard.start(_catalog(), scope=wizard.SCOPE_CONFIG)
            .with_answer("mode", wizard.CUSTOMIZE)
            .with_answer("jury", "gating")
            .with_answer("review.1", team.JURY_PANEL)
        )
        payload = state.resolve().as_dict()
        self.assertEqual(payload["review_by_tier"]["1"], team.JURY_PANEL)
        self.assertEqual(payload["review"], [])
        self.assertEqual(payload["team"]["review"]["by_tier"]["1"], team.JURY_PANEL)
        self.assertEqual(payload["team"]["jury"], {"mode": "gating", "min_vendors": 2})


class TestCommittablePolicy(unittest.TestCase):
    """A scaffolded `knobs.team` may only name what `keel validate` can resolve."""

    def _catalog(self):
        return wizard.Catalog.from_report(
            {"providers": [_row("claude"), _row("openrouter", source="registry")]}
        )

    def test_the_run_wizard_still_offers_a_registry_provider(self):
        state = wizard.start(self._catalog()).with_answer("mode", wizard.CUSTOMIZE)
        question = next(q for q in state.questions() if q.key == "implement.provider")
        self.assertIn("openrouter", question.values())

    def test_the_config_wizard_never_offers_one(self):
        state = wizard.start(self._catalog(), scope=wizard.SCOPE_CONFIG)
        state = state.with_answer("mode", wizard.CUSTOMIZE)
        question = next(q for q in state.questions() if q.key == "implement.provider")
        self.assertEqual(question.values(), ("claude",))
        self.assertEqual(question.normalize("openrouter")[0], None)

    def test_a_machine_with_only_registry_providers_gets_no_team_step(self):
        registry_only = wizard.Catalog.from_report(
            {"providers": [_row("openrouter", source="registry")]}
        )
        self.assertIsNone(scaffold.team_block(_scripted(), registry_only))


class TestTeamBlockRoundTrips(unittest.TestCase):
    def test_the_block_parses_back_into_the_policy_it_describes(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [_row("agy", models=("gemini-3.8-flash",)), _row("claude")]}
        )
        state = wizard.start(catalog, scope=wizard.SCOPE_CONFIG)
        for key, value in (
            ("mode", wizard.CUSTOMIZE),
            ("implement.provider", "agy"),
            ("implement.model", "gemini-3.8-flash"),
            ("implement.effort", "high"),
            ("gate.provider", "claude"),
            ("jury", "gating"),
            ("review.1", "claude"),
            ("review.2", "claude,agy"),
            ("review.3", "jury"),
        ):
            state = state.with_answer(key, value)
        block = state.resolve().team_block()
        policy = team.parse_team(block)
        self.assertEqual(policy.implement, team.Seat("agy", "gemini-3.8-flash", "high"))
        self.assertEqual(policy.gate.distinct_from, team.IMPLEMENTER)
        self.assertEqual(policy.review_by_tier["3"], team.JURY_PANEL)
        self.assertEqual(policy.jury_mode, "gating")
        self.assertEqual(policy.fix, team.Seat(provider=team.IMPLEMENTER))
        self.assertEqual(
            team.team_issues(block, source="knobs.team"),
            [],
            "the wizard must not be able to write a policy keel validate rejects",
        )

    def test_a_wizard_written_config_validates(self):
        catalog = wizard.Catalog.from_report(
            {"providers": [_row("agy", models=("gemini-3.8-flash",)), _row("claude")]}
        )
        text = scaffold.render_config(
            repo="demo",
            team=scaffold.team_block(
                _scripted(wizard.CUSTOMIZE, "agy", "gemini-3.8-flash", "high", "claude"), catalog
            ),
            generator="keel init --wizard",
        )
        data = cfg.yaml.load(text)
        self.assertEqual(cfg.validate_data(data), [])
        config = cfg.parse_config(data, source="wizard")
        self.assertEqual(config.knobs.team.implement, team.Seat("agy", "gemini-3.8-flash", "high"))


class TestApplyAnswers(unittest.TestCase):
    def test_recorded_answers_replace_the_defaults(self):
        state, errors = wizard.apply_answers(
            wizard.start(_catalog()),
            {"mode": wizard.CUSTOMIZE, "implement.provider": "ollama", "review": "claude"},
        )
        self.assertEqual(errors, ())
        resolution = state.resolve()
        self.assertEqual(resolution.implement.provider, "ollama")
        self.assertEqual(resolution.review, (team.Seat(provider="claude"),))

    def test_an_unavailable_provider_is_reported_and_not_applied(self):
        state, errors = wizard.apply_answers(
            wizard.start(_catalog()),
            {"mode": wizard.CUSTOMIZE, "implement.provider": "codex"},
        )
        self.assertIn("is not on offer here", errors[0])
        self.assertEqual(state.resolve().implement.provider, "claude")

    def test_a_real_key_this_run_never_reaches_says_so(self):
        _, errors = wizard.apply_answers(
            wizard.start(_catalog()), {"mode": wizard.CUSTOMIZE, "review.3": "claude"}
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("a real wizard question, but this run never reaches it", errors[0])
        self.assertIn("`keel init --wizard` question", errors[0])

    def test_a_misspelled_key_is_a_different_message_from_an_unreachable_one(self):
        _, errors = wizard.apply_answers(wizard.start(_catalog()), {"nonsense": "x"})
        self.assertEqual(len(errors), 1)
        self.assertIn("not a question this wizard asks", errors[0])
        self.assertIn("valid keys are", errors[0])

    def test_the_config_scope_names_the_per_tier_keys(self):
        _, errors = wizard.apply_answers(
            wizard.start(_catalog(), scope=wizard.SCOPE_CONFIG), {"review": "claude"}
        )
        self.assertIn("answer review.1 / review.2 / review.3", errors[0])

    def test_an_unreachable_model_or_effort_key_says_which(self):
        _, model_errors = wizard.apply_answers(
            wizard.start(_catalog()), {"implement.provider": "claude", "implement.model": "x"}
        )
        self.assertIn("lists no models", model_errors[0])
        _, effort_errors = wizard.apply_answers(
            wizard.start(_catalog()), {"implement.provider": "claude", "implement.effort": "high"}
        )
        self.assertIn("no spelling for reasoning effort", effort_errors[0])

    def test_any_answer_but_mode_implies_customize(self):
        """Otherwise quick-start ends the walk and every other answer is 'not asked'."""
        state, errors = wizard.apply_answers(
            wizard.start(_catalog()), {"implement.provider": "ollama"}
        )
        self.assertEqual(errors, ())
        self.assertEqual(state.resolve().implement.provider, "ollama")

    def test_an_explicit_quick_start_still_means_ignore_the_rest(self):
        state, errors = wizard.apply_answers(
            wizard.start(_catalog()),
            {"mode": wizard.QUICK_START, "implement.provider": "ollama"},
        )
        self.assertIn("never reaches it", errors[0])
        self.assertTrue(state.resolve().quick_start)
        self.assertEqual(state.resolve().flags(), ())

    def test_every_question_the_planner_can_ask_is_a_declared_key(self):
        """`QUESTION_KEYS` is what tells a typo from an unreachable branch."""
        seen = set()
        for scope in wizard.SCOPES:
            for provider in ("claude", "ollama"):
                state = wizard.start(_catalog(), scope=scope)
                state = state.with_answer("mode", wizard.CUSTOMIZE)
                state = state.with_answer("implement.provider", provider)
                for jury in wizard.JURY_ANSWERS:
                    for question in state.with_answer("jury", jury).questions():
                        seen.add(question.key)
        self.assertTrue(seen)
        self.assertEqual(seen - set(wizard.QUESTION_KEYS), set())


class TestRun(unittest.TestCase):
    def test_it_asks_every_question_in_order(self):
        asked = []

        def ask(prompt, default):
            asked.append(prompt.splitlines()[0])
            return default

        state = wizard.run(
            wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE), ask, lambda _m: None
        )
        self.assertEqual(asked[0], "Implementer provider — who writes the change at s4")
        self.assertEqual(asked[-1], "Review comment posting — how findings reach the pull request")
        self.assertIsNone(state.next_question())

    def test_a_bad_answer_is_refused_and_asked_again(self):
        replies = iter(["codex", "ollama"])
        notes = []
        state = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        question = state.next_question()
        state = wizard.run(state, lambda _p, _d: next(replies, _d), notes.append)
        self.assertEqual(question.key, "implement.provider")
        self.assertEqual(state.resolve().implement.provider, "ollama")
        self.assertIn("is not on offer here", notes[0])

    def test_a_seam_that_never_answers_falls_back_to_the_default(self):
        notes = []
        state = wizard.start(_catalog()).with_answer("mode", wizard.CUSTOMIZE)
        state = wizard.run(state, lambda _p, _d: "codex", notes.append)
        self.assertEqual(state.resolve().implement.provider, "claude")
        self.assertIn("keeping the default 'claude'", notes[wizard.MAX_ATTEMPTS])


class TestParseAnswerArgs(unittest.TestCase):
    def test_pairs_and_semicolons(self):
        answers, errors = wizard.parse_answer_args(["a=1", " b = 2 ;c=3", "  "])
        self.assertEqual(answers, {"a": "1", "b": "2", "c": "3"})
        self.assertEqual(errors, ())

    def test_a_value_may_be_empty(self):
        answers, errors = wizard.parse_answer_args(["a="])
        self.assertEqual((answers, errors), ({"a": ""}, ()))

    def test_a_non_pair_is_reported(self):
        _, errors = wizard.parse_answer_args(["nope", "=x"])
        self.assertEqual(len(errors), 2)
        self.assertIn("is not KEY=VALUE", errors[0])


class _Knobs:
    def __init__(self, policy):
        self.team = policy


class _Config:
    def __init__(self, policy=None):
        self.knobs = _Knobs(policy if policy is not None else team.TeamPolicy())


def _ship_args(**overrides):
    args = argparse.Namespace(
        wizard=True,
        wizard_answer=[],
        json=False,
        review_comments="inline",
        reviewers=None,
        delegate=None,
        review_delegate=[],
        jury=False,
        no_jury=False,
        jury_advisory=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _drive(args, config=None, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    kwargs.setdefault("_probe", lambda _c: PROBE)
    kwargs.setdefault("_isatty", lambda: True)
    kwargs.setdefault("command", "ship")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = wizardrun.run_option_wizard(args, config or _Config(), **kwargs)
    return code, out.getvalue(), err.getvalue()


class TestRunOptionWizard(unittest.TestCase):
    def test_without_the_flag_nothing_happens(self):
        args = _ship_args(wizard=False)

        def explode(_config):  # pragma: no cover - asserted by not being called
            raise AssertionError("the probe must not run without --wizard")

        code, out, _ = _drive(args, _probe=explode)
        self.assertEqual((code, out), (0, ""))
        self.assertIsNone(args.delegate)

    def test_a_non_tty_run_is_a_logged_no_op_that_keeps_the_parsed_flags(self):
        args = _ship_args(delegate="codex", reviewers=3)
        code, out, _ = _drive(args, _isatty=lambda: False)
        self.assertEqual(code, 0)
        self.assertIn(wizardrun.NON_INTERACTIVE, out)
        self.assertEqual((args.delegate, args.reviewers), ("codex", 3))

    def test_a_machine_with_no_usable_provider_is_a_logged_no_op(self):
        args = _ship_args()
        code, out, _ = _drive(args, _probe=lambda _c: {"providers": []})
        self.assertEqual(code, 0)
        self.assertIn(wizardrun.NO_PROVIDERS, out)
        self.assertIsNone(args.delegate)

    def test_an_interactive_run_writes_its_answers_onto_the_flags(self):
        args = _ship_args()
        code, out, _ = _drive(
            args,
            _ask=_scripted(
                wizard.CUSTOMIZE,
                "ollama",
                "qwen2.5-coder",
                "claude",
                "advisory",
                "claude",
                "summary",
            ),
        )
        self.assertEqual(code, 0)
        self.assertIn("keel ship --wizard — resolved", out)
        self.assertEqual(args.delegate, "ollama:qwen2.5-coder")
        self.assertEqual(args.review_delegate, ["claude"])
        self.assertEqual((args.reviewers, args.review_comments), (1, "summary"))
        self.assertEqual((args.jury, args.no_jury, args.jury_advisory), (False, False, True))

    def test_recorded_answers_work_without_a_terminal(self):
        args = _ship_args(wizard_answer=["mode=customize;implement.provider=ollama"])
        code, out, _ = _drive(args, _isatty=lambda: False)
        self.assertEqual(code, 0)
        self.assertIn("--delegate ollama", out)
        self.assertEqual(args.delegate, "ollama")

    def test_a_malformed_answer_flag_fails_closed(self):
        code, _, err = _drive(_ship_args(wizard_answer=["oops"]))
        self.assertEqual(code, 1)
        self.assertIn("is not KEY=VALUE", err)

    def test_an_answer_naming_an_unavailable_provider_fails_closed(self):
        code, _, err = _drive(
            _ship_args(wizard_answer=["mode=customize", "implement.provider=codex"]),
            _isatty=lambda: False,
        )
        self.assertEqual(code, 1)
        self.assertIn("is not on offer here", err)

    def test_a_bad_answer_inside_the_prompt_loop_is_notified(self):
        args = _ship_args()
        code, out, _ = _drive(args, _ask=_scripted(wizard.CUSTOMIZE, "codex"))
        self.assertEqual(code, 0)
        self.assertIn("wizard: implement.provider: 'codex' is not on offer here", out)

    def test_json_mode_keeps_stdout_clean(self):
        args = _ship_args(json=True)
        code, out, err = _drive(args, _isatty=lambda: False)
        self.assertEqual((code, out), (0, ""))
        self.assertIn(wizardrun.NON_INTERACTIVE, err)

    def test_a_policy_seat_this_machine_cannot_reach_is_named_once(self):
        policy = team.parse_team({"implement": {"default": {"provider": "codex"}}})
        code, out, _ = _drive(_ship_args(), _Config(policy), _ask=_scripted())
        self.assertEqual(code, 0)
        self.assertIn("wizard: knobs.team names 'codex', which is not usable here", out)

    def test_the_jury_question_opens_on_the_flags_then_the_policy(self):
        """It is where the *question* starts, never an answer written back."""
        policy = team.parse_team({"jury": {"mode": "advisory"}})
        for flag, opens_on in (
            (None, "advisory"),
            ("jury", "gating"),
            ("no_jury", wizard.JURY_OFF),
            ("jury_advisory", "advisory"),
        ):
            args = _ship_args(**({flag: True} if flag else {}))
            asked = {}

            def ask(prompt, default, _asked=asked):
                head = prompt.splitlines()[0]
                _asked.setdefault(head, default)
                # Customize to reach the jury question; accept every default after it.
                return wizard.CUSTOMIZE if head.startswith("Start style") else ""

            before = (args.jury, args.no_jury, args.jury_advisory)
            _drive(args, _Config(policy), _ask=ask)
            self.assertEqual(asked["Cross-vendor jury — the cross-vendor panel"], opens_on)
            # Nothing was answered, so the parsed jury flags are untouched.
            self.assertEqual((args.jury, args.no_jury, args.jury_advisory), before)

    def test_an_answered_jury_question_does_write_its_flag(self):
        args = _ship_args()
        _drive(args, _ask=_scripted(wizard.CUSTOMIZE, "", "", "gating"))
        self.assertEqual((args.jury, args.no_jury, args.jury_advisory), (True, False, False))

    def test_a_command_without_the_delegate_flags_only_gets_what_it_has(self):
        args = argparse.Namespace(
            wizard=True, wizard_answer=[], json=False, review_comments="inline", reviewers=None
        )
        code, out, _ = _drive(
            args,
            command="work-block",
            _ask=_scripted(wizard.CUSTOMIZE, "ollama", "", "", "", "claude"),
        )
        self.assertEqual(code, 0)
        self.assertIn("keel work-block --wizard — resolved", out)
        # Echoed for the child ship handoffs, not written onto a namespace without it.
        self.assertIn("--delegate ollama", out)
        self.assertEqual(args.reviewers, 1)
        self.assertFalse(hasattr(args, "delegate"))


class TestQuickStartChangesNothing(unittest.TestCase):
    """The docstring's promise, asserted against the real resolver (#1018 round 2).

    A wizard told to take every default must resolve the *same* team as a run with no
    `--wizard` at all. It did not: `apply_resolution` materialised every resolved value
    as a flag, so quick-start on a tier-3 change wrote `--reviewers 2` (the run bench is
    derived at a nominal tier, because the real one is not classified until s1) and
    `--no-jury` (the jury question's opening value when `knobs.team` names no mode) —
    dropping a reviewer and the gating jury from the strictest tier keel has.
    """

    #: Deliberately names no tier-3 bench and no jury mode, so both come from the
    #: risk tier — the two values the wizard was overwriting with `--reviewers 2`
    #: and `--no-jury`.
    POLICY = {
        "implement": {"default": {"provider": "claude"}},
        "review": {"by_tier": {"1": [{"provider": "claude"}]}},
    }

    def _assignment(self, args, tier):
        config = cfg.parse_config(
            {
                "extends": "keel",
                "core_version": "^1.0",
                "repo": "tmp",
                "base_branch": "main",
                "gates": ["build"],
                "knobs": {"build_gate_cmd": "true", "team": self.POLICY},
            },
            source="fixture",
        )
        return config, cli._review_assignment(config, args, tier=tier)

    def test_quick_start_leaves_a_tier_3_contract_identical_to_no_wizard(self):
        plain = _ship_args(wizard=False)
        _, without = self._assignment(plain, 3)

        wizarded = _ship_args()
        config, _ = self._assignment(_ship_args(wizard=False), 3)
        code, out, _ = _drive(wizarded, config, _ask=_scripted())  # every answer blank
        self.assertEqual(code, 0)
        self.assertIn("(none — every option kept its default", out)

        _, with_wizard = self._assignment(wizarded, 3)
        self.assertEqual(with_wizard, without)
        self.assertEqual(with_wizard["reviewer_count"], 3)
        self.assertEqual(
            (wizarded.reviewers, wizarded.delegate, wizarded.no_jury),
            (plain.reviewers, plain.delegate, plain.no_jury),
        )

    def test_the_same_holds_for_an_explicit_quick_start_answer(self):
        wizarded = _ship_args(wizard_answer=["mode=quick-start"])
        config, without = self._assignment(_ship_args(wizard=False), 3)
        _drive(wizarded, config, _isatty=lambda: False)
        _, with_wizard = self._assignment(wizarded, 3)
        self.assertEqual(with_wizard, without)

    def test_an_answered_bench_is_still_an_override(self):
        """The fix must not make the wizard unable to decide anything."""
        wizarded = _ship_args(wizard_answer=["mode=customize", "review=claude"])
        config, _ = self._assignment(_ship_args(wizard=False), 3)
        _drive(wizarded, config, _isatty=lambda: False)
        _, assignment = self._assignment(wizarded, 3)
        self.assertEqual(wizarded.reviewers, 1)
        self.assertEqual(assignment["reviewer_count"], 1)


class TestEveryReachableConfigValidates(unittest.TestCase):
    """Sweep the config-scope answer space; every output must pass `keel validate`.

    The round-2 review found six invalid outputs this way — all of them `jury.mode:
    advisory` beside a `review.by_tier` of `jury`, the pair `team._review_issues`
    refuses. A wizard that can write a config keel then refuses to load is worse than
    no wizard, so the guarantee is asserted over the whole space rather than spot-checked.
    """

    CATALOG = wizard.Catalog.from_report(
        {
            "providers": [
                _row("claude"),
                _row("agy", models=("gemini-3.8-flash",)),
                _row("ollama", transport="local", models=("qwen2.5-coder",), tools=False),
            ]
        }
    )

    def _sweep(self):
        """Every combination of the answers that can change the written block."""
        providers = ("claude", "agy", "ollama")
        benches = ("claude", "agy", "claude,agy", team.JURY_PANEL)
        for provider in providers:
            for jury in wizard.JURY_ANSWERS:
                for bench in benches:
                    for gate in (wizard.NONE, "claude"):
                        yield {
                            "mode": wizard.CUSTOMIZE,
                            "implement.provider": provider,
                            "implement.model": "gemini-3.8-flash"
                            if provider == "agy"
                            else "qwen2.5-coder",
                            "implement.effort": "high",
                            "gate.provider": gate,
                            "jury": jury,
                            "review.1": bench,
                            "review.2": bench,
                            "review.3": bench,
                        }

    def test_every_answer_set_produces_a_config_keel_validate_accepts(self):
        cases = 0
        for answers in self._sweep():
            state = wizard.start(self.CATALOG, scope=wizard.SCOPE_CONFIG)
            # Seated directly, so the sweep also covers answers that never went
            # through `normalize` — the path finding (4) is about.
            for key, value in answers.items():
                state = state.with_answer(key, value)
            block = state.resolve().team_block()
            cases += 1
            with self.subTest(**answers):
                self.assertEqual(team.team_issues(block, source="knobs.team"), [])
                text = scaffold.render_config(repo="demo", team=block)
                data = cfg.yaml.load(text)
                self.assertEqual(cfg.validate_data(data), [])
                cfg.parse_config(data, source="sweep")
        self.assertGreaterEqual(cases, 72)

    def test_the_sweep_would_notice_the_pair_it_exists_to_catch(self):
        """Vacuity guard: advisory + a jury panel really is refused by validation."""
        bad = {
            "implement": {"default": {"provider": "claude"}},
            "review": {"by_tier": {"3": team.JURY_PANEL}},
            "jury": {"mode": "advisory", "min_vendors": 2},
            "fix": {"provider": team.IMPLEMENTER},
        }
        self.assertTrue(team.team_issues(bad, source="knobs.team"))


class TestScaffoldTeamStep(unittest.TestCase):
    def test_no_catalog_writes_no_team_block(self):
        self.assertIsNone(scaffold.team_block(_scripted(), None))
        self.assertIsNone(scaffold.team_block(_scripted(), wizard.Catalog()))
        self.assertNotIn("team:", scaffold.wizard("python", lambda _p, d: d, repo="demo"))

    def test_the_team_step_renders_quoted_tier_keys(self):
        text = scaffold.wizard("python", lambda _p, d: d, repo="demo", catalog=_catalog())
        self.assertIn("  team:\n", text)
        self.assertIn('        "1":\n', text)
        self.assertIn('          - provider: "ollama"\n', text)

    def test_notify_reaches_the_caller(self):
        notes = []
        scaffold.team_block(
            _scripted(wizard.CUSTOMIZE, "codex", "claude"), _catalog(), notify=notes.append
        )
        self.assertIn("is not on offer here", notes[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
