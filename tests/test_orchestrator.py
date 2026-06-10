"""Unit tests for the pure run planner + config-injection assertions."""

import unittest
from pathlib import Path

from keel import config as cfg
from keel import model
from keel import orchestrator as orch
from keel.extensions import load_extensions, parse_extension

PROJECTS = Path(__file__).resolve().parent.parent / "projects"


class TestBuildPlan(unittest.TestCase):
    def test_plan_covers_whole_backbone(self):
        config = cfg.load_config(PROJECTS / "keel.yaml")
        plan = orch.build_plan(config, {})
        self.assertEqual(len(plan), 13)  # s0..s12
        self.assertEqual(plan[0].step_id, "s0")
        self.assertEqual(plan[-1].step_id, "s12")
        for item in plan:
            self.assertEqual(
                [slot.name for slot in item.extension_slots],
                [slot.name for slot in model.slots_for_step(item.step_id)],
            )

    def test_plan_json_exposes_empty_add_only_slots(self):
        config = cfg.load_config(PROJECTS / "keel.yaml")
        plan = orch.plan_as_dict(orch.build_plan(config, {}))
        by_step = {item["step_id"]: item for item in plan}
        self.assertEqual(by_step["s10"]["extension_slots"], [
            {
                "name": "pre-merge",
                "step_id": "s10",
                "execution_mode": "deterministic",
                "may_block": True,
                "adapter_required": False,
                "hook_count": 0,
                "customization": "add-only",
                "failure_mode": "blocking-capable",
            },
            {
                "name": "after:merge",
                "step_id": "s10",
                "execution_mode": "deterministic",
                "may_block": False,
                "adapter_required": False,
                "hook_count": 0,
                "customization": "add-only",
                "failure_mode": "fail-soft",
            },
        ])
        self.assertEqual(by_step["s0"]["extension_slots"][0]["name"], "after:config")
        self.assertTrue(all(item["extension_slots"] for item in plan))

    def test_builtin_gates_land_on_test_step(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        plan = orch.build_plan(config, {})
        test_step = next(p for p in plan if p.step_name == "test")
        self.assertEqual(test_step.gates, ("build", "lint", "jury"))
        merge_step = next(p for p in plan if p.step_name == "merge")
        self.assertEqual(merge_step.gates, ())

    def test_example_flutter_design_parity_lego_lands_in_slots(self):
        config = cfg.load_config(PROJECTS / "example-flutter.yaml")
        # Provide the extensions the config references (parsed inline; no disk).
        loaded = {
            "tester": [parse_extension(
                "---\nid: design-parity\nslot: tester\nkind: command\nrun: x\n---\n",
                source="design-parity.md")],
            "pre-merge": [parse_extension(
                "---\nid: design-parity-gate\nslot: pre-merge\nkind: command\n"
                "on_fail: block\nrun: x\n---\n",
                source="design-parity-gate.md")],
        }
        plan = orch.build_plan(config, loaded)
        test_step = next(p for p in plan if p.step_name == "test")
        merge_step = next(p for p in plan if p.step_name == "merge")
        self.assertEqual(test_step.gates, ("build", "lint", "design-parity"))
        self.assertEqual(merge_step.gates, ("design-parity-gate",))

    def test_hooks_render_in_backbone_step_order(self):
        config = cfg.parse_config({
            "extends": "keel",
            "core_version": "^0.5",
            "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test"},
            "extensions": {
                "guard": ["guard.md"],
                "after:ci": ["ci-report.md"],
                "after:close": ["notify.md"],
            },
        })
        loaded = {
            "guard": [parse_extension(
                "---\nid: guard-check\nslot: guard\nkind: command\non_fail: block\n"
                "run: ./tools/guard\nrequired_capabilities: [shell]\n---\n",
                source="guard.md",
            )],
            "after:ci": [parse_extension(
                "---\nid: ci-report\nslot: after:ci\nkind: command\nrun: ./tools/report\n"
                "optional_capabilities: [gh]\n---\n",
                source="ci-report.md",
            )],
            "after:close": [parse_extension(
                "---\nid: close-note\nslot: after:close\nkind: command\nrun: ./tools/close\n---\n",
                source="notify.md",
            )],
        }
        plan = orch.build_plan(config, loaded)
        guard_step = next(p for p in plan if p.step_name == "guard")
        ci_step = next(p for p in plan if p.step_name == "ci")
        close_step = next(p for p in plan if p.step_name == "close")
        self.assertEqual(guard_step.gates, ("guard-check",))
        self.assertEqual(guard_step.hooks[0].slot, "guard")
        self.assertEqual(guard_step.extension_slots[0].hook_count, 1)
        self.assertEqual(ci_step.hooks[0].optional_capabilities, ("gh",))
        self.assertEqual(close_step.hooks[0].id, "close-note")
        rendered = orch.render_plan(config, plan)
        self.assertIn("hook: guard-check [guard; command; on_fail=block", rendered)
        self.assertIn("mode=deterministic", rendered)
        self.assertIn("required=shell", rendered)

    def test_agentic_steps_flagged(self):
        config = cfg.load_config(PROJECTS / "keel.yaml")
        plan = orch.build_plan(config, {})
        agentic = {p.step_name for p in plan if p.agentic}
        self.assertEqual(agentic, {"implement", "classify", "review"})

    def test_extensions_are_add_only_and_do_not_change_backbone_order(self):
        config = cfg.parse_config({
            "extends": "keel",
            "core_version": "^1.0",
            "base_branch": "main",
            "knobs": {"build_gate_cmd": "make test"},
            "extensions": {
                "before:select": ["queue-note.md"],
                "pre-merge": ["merge-gate.md"],
                "after:close": ["close-report.md"],
            },
        })
        loaded = {
            "before:select": [parse_extension(
                "---\nid: queue-note\nslot: before:select\nkind: command\nrun: true\n---\n",
                source="queue-note.md",
            )],
            "pre-merge": [parse_extension(
                "---\nid: merge-gate\nslot: pre-merge\nkind: command\n"
                "on_fail: block\nrun: true\n---\n",
                source="merge-gate.md",
            )],
            "after:close": [parse_extension(
                "---\nid: close-report\nslot: after:close\nkind: command\nrun: true\n---\n",
                source="close-report.md",
            )],
        }

        plan = orch.build_plan(config, loaded)

        self.assertEqual([item.step_id for item in plan], list(model.step_ids()))
        self.assertEqual(plan[0].step_name, "config")
        self.assertEqual(plan[-1].step_name, "close")
        self.assertEqual(
            [hook.id for item in plan for hook in item.hooks],
            ["queue-note", "merge-gate", "close-report"],
        )


class TestNoForeignLeak(unittest.TestCase):
    """Config-injection: a project's plan must not contain another's specifics."""

    def test_example_flutter_plan_has_no_gradle(self):
        config = cfg.load_config(PROJECTS / "example-flutter.yaml")
        text = orch.render_plan(config, orch.build_plan(config, {})).lower()
        # render_plan shows repo/base/core; the gradle build cmd must not appear.
        self.assertNotIn("gradle", text)
        self.assertIn("example-flutter", text)

    def test_render_is_deterministic(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        a = orch.render_plan(config, orch.build_plan(config, {}))
        b = orch.render_plan(config, orch.build_plan(config, {}))
        self.assertEqual(a, b)
        self.assertIn("s10  merge", a)


class TestRealExtensionFiles(unittest.TestCase):
    """If the example-flutter extension files exist on disk, they must parse + plan."""

    def test_load_is_failsoft_when_absent(self):
        config = cfg.load_config(PROJECTS / "example-flutter.yaml")
        # keel repo does not ship example-flutter's .keel/extensions; fail-soft -> problems.
        loaded, problems = load_extensions(config, PROJECTS.parent, strict=False)
        self.assertEqual(loaded["tester"], [])
        self.assertTrue(problems)  # files live in the example-flutter repo, not here


if __name__ == "__main__":
    unittest.main()
