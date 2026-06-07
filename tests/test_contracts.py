"""Tests for structured command contracts."""

import unittest
from pathlib import Path

from keel import config as cfg
from keel import contracts, github_transport, orchestrator, runtime
from keel.extensions import load_extensions, parse_extension

PROJECTS = Path(__file__).resolve().parent.parent / "projects"


class TestCommandGraph(unittest.TestCase):
    def test_ship_uses_backbone_graph(self):
        graph = contracts.command_graph("ship")
        self.assertEqual(graph[0]["step_id"], "s0")
        self.assertEqual(graph[-1]["step_id"], "s12")
        self.assertTrue(any(step["slot"] == "reviewers" for step in graph))

    def test_adapter_steps_are_exposed(self):
        graph = contracts.command_graph("morning")
        ids = [step["step_id"] for step in graph]
        self.assertIn("step-0", ids)
        self.assertTrue(any("Shipped" in step["step_name"] for step in graph))

    def test_available_commands_include_major_adapters(self):
        commands = contracts.available_commands()
        for command in ("ship", "ship-v2", "morning", "pr-loop", "review-cycle", "wrap"):
            self.assertIn(command, commands)


class TestBuildCommandContract(unittest.TestCase):
    def test_contract_contains_project_hooks_gates_and_capabilities(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {
            "tester": [parse_extension(
                "---\nid: smoke\nslot: tester\nkind: command\nrun: true\n"
                "required_capabilities: [shell]\n---\n",
                source="smoke.md",
            )],
        }
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        requirement = runtime.CapabilityRequirement(required=("shell",), optional=("gh",))
        evaluation = runtime.evaluate(requirement, report)
        transport = github_transport.resolve(report)

        contract = contracts.build_command_contract(
            command="ship",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=evaluation,
            transport=transport,
            extension_problems=("missing optional fixture",),
        )

        self.assertEqual(contract["schema_version"], contracts.SCHEMA_VERSION)
        self.assertEqual(contract["command"], "ship")
        self.assertEqual(contract["project"]["repo"], "example-android")
        self.assertIn("config_hash", contract["project"])
        self.assertIn("build", [gate["id"] for gate in contract["gates"]])
        self.assertEqual(contract["extension_hooks"]["tester"][0]["id"], "smoke")
        self.assertIn("shell", contract["required_capabilities"])
        self.assertEqual(contract["github_transport"]["transport"], "mcp")
        self.assertFalse(contract["side_effects"]["mutates_in_dry_run"])

    def test_real_extension_absence_is_representable(self):
        config = cfg.load_config(PROJECTS / "example-flutter.yaml")
        loaded, problems = load_extensions(config, PROJECTS.parent, strict=False)
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()
        contract = contracts.build_command_contract(
            command="ship",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
            extension_problems=tuple(problems),
        )
        self.assertTrue(contract["extension_problems"])


if __name__ == "__main__":
    unittest.main()
