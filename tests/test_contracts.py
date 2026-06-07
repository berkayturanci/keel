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
        self.assertEqual(contract["operator_consent"]["status"], "not-required-dry-run")
        self.assertEqual(
            contract["operator_consent"]["consent_scope"],
            ["filesystem", "git", "github"],
        )
        self.assertTrue(contract["operator_consent"]["would_require_operator_consent"])
        self.assertFalse(contract["operator_consent"]["requires_operator_consent"])

    def test_live_contract_records_approved_consent_scope(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
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
            dry_run=False,
            approved_consent_scopes=("filesystem", "git", "github"),
            operator="operator",
            target="issue #82",
        )
        self.assertEqual(contract["mode"], "live")
        self.assertEqual(contract["operator_consent"]["status"], "approved")
        self.assertEqual(
            contract["operator_consent"]["delegated_agent_scope"]["approved_mutation_scopes"],
            ["filesystem", "git", "github"],
        )
        self.assertFalse(contract["operator_consent"]["consent_record"]["secret_values_recorded"])

    def test_coverage_contract_requires_local_worktree_consent(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()
        contract = contracts.build_command_contract(
            command="coverage",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
            dry_run=False,
            approved_consent_scopes=("github",),
        )
        self.assertEqual(
            contract["operator_consent"]["consent_scope"],
            ["filesystem", "git", "github"],
        )
        self.assertEqual(contract["operator_consent"]["status"], "missing")
        self.assertEqual(
            contract["operator_consent"]["missing_scope"],
            ["filesystem", "git"],
        )

    def test_capability_requirements_extend_consent_scope(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {
            "pre-merge": [parse_extension(
                "---\nid: release\nslot: pre-merge\nkind: command\nrun: true\n"
                "required_capabilities: [release-publish]\n"
                "optional_capabilities: [secret-access, production-adjacent]\n"
                "on_fail: block\n---\n",
                source="release.md",
            )],
        }
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement(
            required=("release-publish",),
            optional=("secret-access", "production-adjacent"),
        )
        contract = contracts.build_command_contract(
            command="ship",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
            dry_run=False,
            approved_consent_scopes=("filesystem", "git", "github"),
        )
        self.assertEqual(
            contract["operator_consent"]["consent_scope"],
            ["filesystem", "git", "github", "secrets", "release", "production-adjacent"],
        )
        self.assertEqual(
            contract["operator_consent"]["missing_scope"],
            ["secrets", "release", "production-adjacent"],
        )

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
