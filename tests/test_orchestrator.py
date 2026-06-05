"""Unit tests for the pure run planner + config-injection assertions."""

import unittest
from pathlib import Path

from keel import config as cfg
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

    def test_builtin_gates_land_on_test_step(self):
        config = cfg.load_config(PROJECTS / "smartinventory.yaml")
        plan = orch.build_plan(config, {})
        test_step = next(p for p in plan if p.step_name == "test")
        self.assertEqual(test_step.gates, ("build", "lint", "jury"))
        merge_step = next(p for p in plan if p.step_name == "merge")
        self.assertEqual(merge_step.gates, ())

    def test_ingreview_design_parity_lego_lands_in_slots(self):
        config = cfg.load_config(PROJECTS / "ingreview.yaml")
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

    def test_agentic_steps_flagged(self):
        config = cfg.load_config(PROJECTS / "keel.yaml")
        plan = orch.build_plan(config, {})
        agentic = {p.step_name for p in plan if p.agentic}
        self.assertEqual(agentic, {"implement", "classify", "review"})


class TestNoForeignLeak(unittest.TestCase):
    """Config-injection: a project's plan must not contain another's specifics."""

    def test_ingreview_plan_has_no_gradle(self):
        config = cfg.load_config(PROJECTS / "ingreview.yaml")
        text = orch.render_plan(config, orch.build_plan(config, {})).lower()
        # render_plan shows repo/base/core; the gradle build cmd must not appear.
        self.assertNotIn("gradle", text)
        self.assertIn("ingreview", text)

    def test_render_is_deterministic(self):
        config = cfg.load_config(PROJECTS / "smartinventory.yaml")
        a = orch.render_plan(config, orch.build_plan(config, {}))
        b = orch.render_plan(config, orch.build_plan(config, {}))
        self.assertEqual(a, b)
        self.assertIn("s10  merge", a)


class TestRealExtensionFiles(unittest.TestCase):
    """If the ingreview extension files exist on disk, they must parse + plan."""

    def test_load_is_failsoft_when_absent(self):
        config = cfg.load_config(PROJECTS / "ingreview.yaml")
        # keel repo does not ship ingreview's .keel/extensions; fail-soft -> problems.
        loaded, problems = load_extensions(config, PROJECTS.parent, strict=False)
        self.assertEqual(loaded["tester"], [])
        self.assertTrue(problems)  # files live in the ingreview repo, not here


if __name__ == "__main__":
    unittest.main()
