"""Tests for deterministic run budgets, step caps, and oscillation controls."""

import unittest

from keel import artifacts, runcontrols


class TestRunControlContract(unittest.TestCase):
    def test_contract_exposes_budget_caps_and_oscillation(self):
        contract = runcontrols.contract_as_dict()

        self.assertEqual(contract["schema_version"], "keel.run-controls.v1")
        self.assertTrue(contract["fail_closed"])
        self.assertFalse(contract["wall_clock_timeouts"])
        self.assertIn("fixloop", contract["step_caps"]["overrides"])
        self.assertEqual(
            contract["renderer"]["marker"],
            "<!-- keel.run-control-halt.v1 -->",
        )


class TestRunControlEvaluation(unittest.TestCase):
    def test_passes_when_budget_caps_and_outputs_converge(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "action": "patch", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "test", "diff_fingerprint": "b"},
                {"slot": "tester", "action": "unit", "diff_fingerprint": "c"},
            ],
            max_work_units=10,
        )

        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["hard_halt"])
        self.assertIsNone(report["reason"])
        self.assertEqual(report["summary"]["work_units"], 3)

    def test_budget_breach_halts_fail_closed_with_rendered_reason(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "work_units": 2},
                {"slot": "tester", "work_units": 2},
            ],
            max_work_units=3,
        )

        self.assertEqual(report["status"], "halt")
        self.assertTrue(report["fail_closed"])
        self.assertEqual(report["reason"]["reason"], "run-budget-exceeded")
        self.assertIn("<!-- keel.run-control-halt.v1 -->", report["reason"]["rendered"])

    def test_step_cap_breach_halts_per_slot(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "tester", "action": "unit"},
                {"slot": "tester", "action": "integration"},
                {"slot": "tester", "action": "smoke"},
            ],
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["control"], "step-cap")
        self.assertEqual(report["reason"]["scope"], "tester")
        self.assertEqual(report["reason"]["observed"], 3)
        self.assertEqual(report["reason"]["limit"], 2)

    def test_step_cap_can_use_step_id_when_slot_is_missing(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"step_id": "s6", "action": "ci"},
                {"step_id": "s6", "action": "ci-rerun"},
            ],
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["scope"], "s6")

    def test_repeated_identical_actions_detect_oscillation(self):
        report = runcontrols.evaluate_run_controls(
            [
                {
                    "step_id": "s9",
                    "slot": "fixloop",
                    "action": "apply-same-patch",
                    "output_fingerprint": "same",
                },
                {
                    "step_id": "s9",
                    "slot": "fixloop",
                    "action": "apply-same-patch",
                    "output_fingerprint": "same",
                },
                {
                    "step_id": "s9",
                    "slot": "fixloop",
                    "action": "apply-same-patch",
                    "output_fingerprint": "same",
                },
            ],
            step_caps={"fixloop": 10},
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["control"], "oscillation")
        self.assertEqual(report["reason"]["reason"], "repeated-identical-action")

    def test_empty_actions_do_not_create_false_oscillation(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"step_id": "", "slot": "", "action": "", "output_fingerprint": ""},
                {"step_id": "", "slot": "", "action": "", "output_fingerprint": ""},
                {"step_id": "", "slot": "", "action": "", "output_fingerprint": ""},
            ]
        )

        self.assertEqual(report["status"], "pass")

        # Step/slot present but action and output_fingerprint empty should not trigger oscillation
        report_with_steps = runcontrols.evaluate_run_controls(
            [
                {"step_id": "s4", "slot": "guard", "action": "", "output_fingerprint": ""},
                {"step_id": "s4", "slot": "guard", "action": "", "output_fingerprint": ""},
                {"step_id": "s4", "slot": "guard", "action": "", "output_fingerprint": ""},
            ],
            step_caps={"guard": 10},
        )
        self.assertEqual(report_with_steps["status"], "pass")

    def test_alternating_diff_detects_non_convergence(self):
        report = runcontrols.evaluate_run_controls(
            [
                {
                    "slot": "fixloop",
                    "action": "patch-a",
                    "output_fingerprint": "round-1",
                    "diff_fingerprint": "a",
                },
                {
                    "slot": "fixloop",
                    "action": "patch-b",
                    "output_fingerprint": "round-2",
                    "diff_fingerprint": "b",
                },
                {
                    "slot": "fixloop",
                    "action": "patch-a-again",
                    "output_fingerprint": "round-3",
                    "diff_fingerprint": "a",
                },
                {
                    "slot": "fixloop",
                    "action": "patch-b-again",
                    "output_fingerprint": "round-4",
                    "diff_fingerprint": "b",
                },
            ],
            step_caps={"fixloop": 10},
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["reason"], "alternating-diff-fingerprint")
        self.assertEqual(report["reason"]["scope"], "diff")

    def test_repeated_single_diff_is_not_alternation(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "action": "patch", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "test", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "patch-again", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "test-again", "diff_fingerprint": "a"},
            ],
            step_caps={"fixloop": 10},
        )

        self.assertEqual(report["status"], "pass")

    def test_repeating_three_cycle_is_intentional_non_convergence(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "action": "patch-a", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "patch-b", "diff_fingerprint": "b"},
                {"slot": "fixloop", "action": "patch-c", "diff_fingerprint": "c"},
                {"slot": "fixloop", "action": "patch-a2", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "patch-b2", "diff_fingerprint": "b"},
                {"slot": "fixloop", "action": "patch-c2", "diff_fingerprint": "c"},
            ],
            step_caps={"fixloop": 10},
            alternating_diff_window=6,
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["reason"], "alternating-diff-fingerprint")
        self.assertEqual(report["reason"]["observed"], "a,b,c,a,b,c")

    def test_soft_failures_do_not_halt_without_a_hard_breach(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "after:ci", "action": "record-warning", "soft_failure": True},
            ],
        )

        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["hard_halt"])

    def test_invalid_limits_fall_back_to_defaults(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "action": "patch"},
                {"slot": "fixloop", "action": "test"},
                {"slot": "fixloop", "action": "patch-again"},
                {"slot": "fixloop", "action": "test-again"},
            ],
            max_work_units=0,
            default_step_cap=0,
            step_caps={"fixloop": 0},
            identical_action_threshold=1,
            alternating_diff_window=3,
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["control"], "step-cap")
        self.assertEqual(report["reason"]["limit"], runcontrols.DEFAULT_FIXLOOP_CAP)

    def test_invalid_identical_threshold_falls_back_to_default(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "action": "patch", "output_fingerprint": "same"},
                {"slot": "fixloop", "action": "patch", "output_fingerprint": "same"},
                {"slot": "fixloop", "action": "patch", "output_fingerprint": "same"},
            ],
            step_caps={"fixloop": 10},
            identical_action_threshold=1,
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["reason"], "repeated-identical-action")
        self.assertEqual(report["reason"]["limit"], runcontrols.DEFAULT_IDENTICAL_THRESHOLD)

    def test_invalid_alternating_window_falls_back_to_default(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "fixloop", "action": "a1", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "b1", "diff_fingerprint": "b"},
                {"slot": "fixloop", "action": "a2", "diff_fingerprint": "a"},
                {"slot": "fixloop", "action": "b2", "diff_fingerprint": "b"},
            ],
            step_caps={"fixloop": 10},
            alternating_diff_window=3,
        )

        self.assertEqual(report["status"], "halt")
        self.assertEqual(report["reason"]["reason"], "alternating-diff-fingerprint")

    def test_non_dict_events_are_ignored_deterministically(self):
        report = runcontrols.evaluate_run_controls(
            [
                {"slot": "tester", "action": "unit"},
                "not-an-event",
            ]
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["event_count"], 1)


class TestFixAttribution(unittest.TestCase):
    """Who fixed which round — the sentence the s11 closure comment embeds (#1016)."""

    def test_the_contract_advertises_the_attribution_reader(self):
        block = runcontrols.contract_as_dict()["attribution"]

        self.assertEqual(block["schema_version"], "keel.fix-attribution.v1")
        self.assertEqual(block["reader"], "keel.runcontrols.fix_attribution")
        self.assertIn("provider", block["event_fields"])
        self.assertEqual(block["fix_event"], {"slot": "fixloop", "step_id": "s9"})

    def test_the_worked_example_from_the_issue(self):
        report = runcontrols.fix_attribution(
            [
                {"slot": "implement", "provider": "agy", "attribution": {"agent_label": "agy"}},
                {"slot": "fixloop", "provider": "anthropic-api", "attribution": "opus", "round": 2},
            ]
        )

        self.assertEqual(report["sentence"], "implemented by agy, fixed by opus in round 2")
        self.assertEqual(report["implementer"]["provider"], "agy")
        self.assertEqual(report["rounds"][0]["round"], 2)
        self.assertEqual(report["rounds"][0]["provider"], "anthropic-api")

    def test_step_ids_identify_the_events_when_slots_do_not(self):
        report = runcontrols.fix_attribution(
            [
                {"step_id": "s4", "provider": "codex"},
                {"step_id": "s9", "provider": "codex", "stage": "implementer"},
                {"step_id": "s9", "provider": "claude", "stage": "gate"},
            ]
        )

        self.assertEqual(
            report["sentence"],
            "implemented by codex, fixed by codex in round 1 and claude in round 2",
        )
        self.assertEqual([item["stage"] for item in report["rounds"]], ["implementer", "gate"])

    def test_rounds_without_an_explicit_number_are_numbered_by_position(self):
        report = runcontrols.fix_attribution(
            [{"slot": "fixloop", "provider": "agy"}, {"slot": "fixloop", "provider": "agy"}]
        )

        self.assertEqual([item["round"] for item in report["rounds"]], [1, 2])
        self.assertEqual(report["sentence"], "fixed by agy in round 1 and agy in round 2")

    def test_an_actorless_event_records_nothing(self):
        report = runcontrols.fix_attribution(
            [
                {"slot": "implement"},
                {"slot": "fixloop", "attribution": {"model_label": "gemini-3.8-flash"}},
                {"slot": "reviewers", "provider": "codex"},
                "not an event",
                {"slot": "fixloop", "provider": "  ", "round": 0},
            ]
        )

        self.assertIsNone(report["implementer"])
        self.assertEqual([item["actor"] for item in report["rounds"]], ["gemini-3.8-flash"])
        self.assertEqual(report["sentence"], "fixed by gemini-3.8-flash in round 1")

    def test_the_first_implement_event_wins(self):
        report = runcontrols.fix_attribution(
            [{"slot": "implement", "provider": "agy"}, {"slot": "implement", "provider": "codex"}]
        )

        self.assertEqual(report["implementer"]["provider"], "agy")
        self.assertIsNone(report["implementer"]["attribution"])
        self.assertEqual(report["sentence"], "implemented by agy")

    def test_an_empty_run_says_so_rather_than_guessing(self):
        self.assertEqual(
            runcontrols.fix_attribution([])["sentence"], "no implementer or fixer recorded"
        )

    def test_an_unusable_attribution_object_falls_back_to_the_provider(self):
        report = runcontrols.fix_attribution(
            [{"slot": "fixloop", "provider": "codex", "attribution": {"system": "codex-cli"}}]
        )

        self.assertEqual(report["rounds"][0]["actor"], "codex")
        self.assertIsNone(report["rounds"][0]["attribution"])


class TestRunControlRenderer(unittest.TestCase):
    def test_run_control_halt_renderer_has_stable_shape(self):
        body = artifacts.render_run_control_halt(
            control="step-cap",
            reason="step-cap-exceeded",
            scope="fixloop",
            observed=4,
            limit=3,
        )

        self.assertIn("<!-- keel.run-control-halt.v1 -->", body)
        self.assertIn("- **Control:** `step-cap`", body)
        self.assertIn("- **Scope:** fixloop", body)


if __name__ == "__main__":
    unittest.main()
