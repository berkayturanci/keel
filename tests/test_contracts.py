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
        self.assertTrue(all(step["profile_step"] == "standard" for step in graph))

    def test_ship_v2_uses_backbone_with_compound_overrides(self):
        graph = contracts.command_graph("ship-v2")
        by_id = {step["step_id"]: step for step in graph}
        self.assertEqual(by_id["s4"]["profile_step"], "compound")
        self.assertEqual(by_id["s7"]["profile_step"], "compound")
        self.assertEqual(by_id["s9"]["profile_step"], "compound")
        self.assertEqual(by_id["s11"]["profile_step"], "compound")
        self.assertEqual(by_id["s10"]["profile_step"], "standard")

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
        self.assertEqual(contract["project"]["policy_pack"]["name"], "example-android-web")
        self.assertIn("payment-flow", [
            rule["id"] for rule in contract["project"]["policy_pack"]["risk_rules"]
        ])
        self.assertIn("build", [gate["id"] for gate in contract["gates"]])
        self.assertEqual(contract["extension_hooks"]["tester"][0]["id"], "smoke")
        self.assertTrue(any(
            command["name"] == "android-build" for command in contract["project_commands"]
        ))
        self.assertIn("shell", contract["required_capabilities"])
        self.assertEqual(contract["github_transport"]["transport"], "mcp")
        self.assertFalse(contract["side_effects"]["mutates_in_dry_run"])
        self.assertEqual(contract["operator_consent"]["status"], "not-required-dry-run")
        self.assertEqual(
            contract["operator_consent"]["consent_scope"],
            ["filesystem", "git", "github"],
        )
        self.assertEqual(contract["review_merge_contract"]["posting"]["mode"], "inline")
        self.assertEqual(
            contract["review_merge_contract"]["reviewers"]["project_additions"],
            ["Apply extra scrutiny to local data and payment-flow changes."],
        )
        self.assertIn("Testing", contract["review_merge_contract"]["reviewers"]
                      ["required_sections"])
        self.assertEqual(contract["review_merge_contract"]["jury"]["mode"], "off")
        self.assertTrue(contract["operator_consent"]["would_require_operator_consent"])
        self.assertFalse(contract["operator_consent"]["requires_operator_consent"])

    def test_contract_resolves_review_flags_for_ship_like_commands(self):
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
            reviewer_override=2,
            review_comments="summary",
            jury=True,
            jury_advisory=True,
        )

        review = contract["review_merge_contract"]
        self.assertEqual(review["reviewers"]["count"], 2)
        self.assertEqual(review["reviewers"]["source"], "override")
        self.assertEqual(review["posting"]["mode"], "summary")
        self.assertEqual(review["jury"]["mode"], "advisory")
        self.assertTrue(review["jury"]["configured_gate"])
        self.assertTrue(review["merge_gate"]["final_mergeability_recheck_inside_lock"])

    def test_ship_v2_contract_exposes_first_class_compound_profile(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()
        contract = contracts.build_command_contract(
            command="ship-v2",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )

        profile = contract["workflow_profile"]
        self.assertEqual(profile["profile"], "compound")
        self.assertEqual(profile["inherits"], "ship")
        self.assertTrue(profile["first_class_variant"])
        self.assertGreaterEqual(
            set(profile["shared_primitives"]),
            {
                "select",
                "branch",
                "worktree",
                "guard",
                "classify",
                "ci",
                "test",
                "merge_window",
                "merge_lock",
                "merge",
                "capture_marker",
                "close",
            },
        )
        self.assertEqual(profile["step_overrides"]["s4"]["step"], "implement")
        self.assertEqual(profile["step_overrides"]["s7"]["step"], "review")
        self.assertEqual(profile["step_overrides"]["s9"]["step"], "fixloop")
        self.assertEqual(profile["step_overrides"]["s11"]["step"], "capture")
        self.assertIn("review_merge_contract", contract)

    def test_project_command_contract_has_graph_capabilities_and_side_effects(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("adb", False, "missing", "test"),
            runtime.Capability("browser", False, "missing", "test"),
        ))
        requirement = runtime.CapabilityRequirement(
            required=("shell", "adb"),
            optional=("browser",),
        )
        contract = contracts.build_command_contract(
            command="ui-test",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(contract["graph"][0]["source"], "project_command")
        self.assertEqual(contract["graph"][0]["step_id"], "project-command:ui-test")
        self.assertIn("adb", contract["required_capabilities"])
        self.assertIn("browser", contract["optional_capabilities"])
        self.assertIn("report_write", contract["side_effects"]["declared"])

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
