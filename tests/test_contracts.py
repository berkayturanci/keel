"""Tests for structured command contracts."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from keel import config as cfg
from keel import contracts, gates, github_transport, orchestrator, runtime
from keel.extensions import load_extensions, parse_extension

PROJECTS = Path(__file__).resolve().parent.parent / "projects"


class TestCommandGraph(unittest.TestCase):
    def test_ship_uses_backbone_graph(self):
        graph = contracts.command_graph("ship")
        self.assertEqual(graph[0]["step_id"], "s0")
        self.assertEqual(graph[-1]["step_id"], "s12")
        self.assertTrue(any(step["slot"] == "reviewers" for step in graph))
        self.assertTrue(all(step["profile_step"] == "standard" for step in graph))

    def test_ship_compound_profile_uses_backbone_with_compound_overrides(self):
        graph = contracts.command_graph("ship", profile="compound")
        by_id = {step["step_id"]: step for step in graph}
        self.assertEqual(by_id["s4"]["profile_step"], "compound")
        self.assertEqual(by_id["s7"]["profile_step"], "compound")
        self.assertEqual(by_id["s9"]["profile_step"], "compound")
        self.assertEqual(by_id["s11"]["profile_step"], "compound")
        self.assertEqual(by_id["s10"]["profile_step"], "standard")

    def test_ship_standard_profile_has_no_overrides(self):
        graph = contracts.command_graph("ship", profile="standard")
        self.assertTrue(all(step["profile_step"] == "standard" for step in graph))

    def test_adapter_steps_are_exposed(self):
        graph = contracts.command_graph("morning")
        ids = [step["step_id"] for step in graph]
        self.assertIn("step-0", ids)
        self.assertTrue(any("Shipped" in step["step_name"] for step in graph))

    def test_available_commands_include_major_adapters(self):
        commands = contracts.available_commands()
        for command in ("ship", "morning", "pr-loop", "review-cycle", "wrap"):
            self.assertIn(command, commands)
        self.assertNotIn("ship-v2", commands)


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
        test_step = next(
            step for step in contract["backbone_plan"] if step["step_id"] == "s8"
        )
        tester_slot = next(
            slot for slot in test_step["extension_slots"] if slot["name"] == "tester"
        )
        self.assertEqual(tester_slot["customization"], "add-only")
        self.assertEqual(tester_slot["failure_mode"], "blocking-capable")
        self.assertEqual(tester_slot["hook_count"], 1)
        self.assertTrue(any(
            command["name"] == "android-build" for command in contract["project_commands"]
        ))
        self.assertIn("shell", contract["required_capabilities"])
        self.assertEqual(contract["github_transport"]["transport"], "mcp")
        self.assertEqual(contract["run_ledger"]["schema_version"], "keel.run-ledger.v1")
        self.assertEqual(contract["run_ledger"]["path"], ".keel/state/run-ledger.jsonl")
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
        self.assertEqual(
            contract["closure_comment"]["schema_version"], "keel.closure-comment.v1"
        )
        self.assertEqual(contract["evidence"]["schema_version"], "keel.evidence.v1")
        self.assertTrue(contract["evidence"]["fail_closed"])
        self.assertEqual(contract["run_controls"]["schema_version"], "keel.run-controls.v1")
        self.assertTrue(contract["run_controls"]["fail_closed"])
        self.assertIn("fixloop", contract["run_controls"]["step_caps"]["overrides"])
        self.assertEqual(
            contract["step_verification"]["schema_version"],
            "keel.step-verification.v1",
        )
        self.assertTrue(contract["step_verification"]["no_premature_termination"])
        review_step = next(
            step for step in contract["step_verification"]["steps"]
            if step["step_id"] == "s7"
        )
        self.assertEqual(review_step["required_evidence"], [
            "review-verdict-1",
            "review-verdict-2",
        ])
        self.assertEqual(contract["artifact_renderers"]["schema_version"], "keel.artifacts.v1")
        self.assertEqual(
            contract["artifact_renderers"]["adapter_rule"],
            "post rendered markdown verbatim when available",
        )
        self.assertEqual(contract["closure_comment"]["heading"], "Ship outcome")
        self.assertIn("issue_intake", contract)
        self.assertEqual(contract["issue_intake"]["status"], "needs-input")
        self.assertFalse(contract["issue_intake"]["can_mutate_code"])
        self.assertTrue(contract["operator_consent"]["would_require_operator_consent"])
        self.assertFalse(contract["operator_consent"]["requires_operator_consent"])

    def test_ship_contract_records_ready_issue_intake(self):
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
            issue_title="Add workflow readiness gate",
            issue_body=(
                "## Problem\nAgents need to know when work can start.\n\n"
                "## Deliverable\nAdd a structured readiness gate.\n\n"
                "## Acceptance criteria\n"
                "- Dry-run output includes readiness.\n"
                "- Needs-input issues do not mutate code.\n"
            ),
            issue_labels=("enhancement",),
        )

        intake = contract["issue_intake"]
        self.assertEqual(intake["status"], "ready")
        self.assertTrue(intake["provided"])
        self.assertTrue(intake["can_mutate_code"])
        self.assertEqual(intake["ledger_record"]["readiness"], "ready")
        self.assertEqual(intake["work_block_policy"]["non_ready_statuses"],
                         ["needs-input", "blocked", "out-of-scope"])

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
        evidence_ids = [item["id"] for item in contract["evidence"]["required"]]
        self.assertEqual(evidence_ids, [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
        ])

    def test_ship_compound_contract_exposes_first_class_compound_profile(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()
        contract = contracts.build_command_contract(
            command="ship",
            profile="compound",
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

    def test_ship_standard_contract_has_standard_profile_and_same_side_effects(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()

        def _contract(profile):
            return contracts.build_command_contract(
                command="ship",
                profile=profile,
                config=config,
                loaded=loaded,
                plan=plan,
                requirement=requirement,
                evaluation=runtime.evaluate(requirement, report),
                transport=github_transport.resolve(report),
            )

        standard = _contract("standard")
        compound = _contract("compound")
        self.assertEqual(standard["workflow_profile"]["profile"], "standard")
        # Same backbone, same safety primitives: the compound profile only swaps the
        # four step overrides, never the declared side effects.
        self.assertEqual(standard["side_effects"], compound["side_effects"])

    def test_standalone_command_profiles_are_first_class(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()

        implement = contracts.build_command_contract(
            command="implement",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(implement["workflow_profile"]["profile"], "standalone-step")
        self.assertEqual(implement["workflow_profile"]["inherits"], "ship.s4")
        self.assertTrue(implement["workflow_profile"]["first_class_variant"])
        self.assertIn("worktree", implement["workflow_profile"]["shared_primitives"])
        self.assertNotIn("merge", implement["side_effects"]["declared"])

        ci_check = contracts.build_command_contract(
            command="ci-check",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
            dry_run=False,
        )
        self.assertEqual(ci_check["workflow_profile"]["profile"], "standalone-diagnostic")
        self.assertTrue(ci_check["workflow_profile"]["first_class_variant"])
        self.assertIn("read_only", ci_check["workflow_profile"]["shared_primitives"])
        self.assertFalse(ci_check["operator_consent"]["would_require_operator_consent"])

        morning = contracts.build_command_contract(
            command="morning",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(morning["workflow_profile"]["profile"], "daily-brief")
        self.assertTrue(morning["workflow_profile"]["first_class_variant"])
        self.assertIn("health_providers", morning["workflow_profile"]["shared_primitives"])
        self.assertIn("morning_contract", morning)
        self.assertIn("run_ledger", morning["morning_contract"])

        wrap = contracts.build_command_contract(
            command="wrap",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=runtime.CapabilityRequirement(required=("git", "worktree")),
            evaluation=runtime.evaluate(
                runtime.CapabilityRequirement(required=("git", "worktree")),
                runtime.CapabilityReport((
                    runtime.Capability("git", True, "ok", "test"),
                    runtime.Capability("worktree", True, "ok", "test"),
                )),
            ),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(wrap["workflow_profile"]["profile"], "session-wrap")
        self.assertTrue(wrap["workflow_profile"]["first_class_variant"])
        self.assertIn("session_contract", wrap)
        self.assertIn("run_ledger", wrap["session_contract"])

        work_block = contracts.build_command_contract(
            command="work-block",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=runtime.CapabilityRequirement(required=("git", "worktree")),
            evaluation=runtime.evaluate(
                runtime.CapabilityRequirement(required=("git", "worktree")),
                runtime.CapabilityReport((
                    runtime.Capability("git", True, "ok", "test"),
                    runtime.Capability("worktree", True, "ok", "test"),
                )),
            ),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(
            work_block["workflow_profile"]["profile"],
            "session-work-block-daytime",
        )
        self.assertEqual(work_block["workflow_profile"]["inherits"], "ship")
        self.assertIn("review_merge_contract", work_block)
        self.assertEqual(work_block["run_controls"]["schema_version"], "keel.run-controls.v1")
        self.assertIn("work_block", work_block["session_contract"])
        self.assertEqual(work_block["session_contract"]["work_block"]["mode"], "daytime")
        self.assertIn("needs_input",
                      work_block["session_contract"]["work_block"]
                      ["final_report"]["outcome_buckets"])

        overnight = contracts.build_command_contract(
            command="overnight",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=runtime.CapabilityRequirement(required=("git", "worktree")),
            evaluation=runtime.evaluate(
                runtime.CapabilityRequirement(required=("git", "worktree")),
                runtime.CapabilityReport((
                    runtime.Capability("git", True, "ok", "test"),
                    runtime.Capability("worktree", True, "ok", "test"),
                )),
            ),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(overnight["workflow_profile"]["profile"], "session-overnight")
        self.assertEqual(overnight["workflow_profile"]["inherits"], "ship")
        self.assertIn("review_merge_contract", overnight)
        self.assertEqual(overnight["run_controls"]["schema_version"], "keel.run-controls.v1")
        self.assertIn("run_ledger", overnight["session_contract"])
        self.assertEqual(overnight["session_contract"]["work_block"]["mode"], "overnight")

        pr_loop = contracts.build_command_contract(
            command="pr-loop",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(pr_loop["workflow_profile"]["profile"], "feedback-loop")
        self.assertEqual(pr_loop["workflow_profile"]["inherits"], "ship.s6-s9")
        self.assertIn("reviewer_isolation", pr_loop["workflow_profile"]["shared_primitives"])
        self.assertIn("feedback_workflow", pr_loop)
        self.assertEqual(pr_loop["feedback_workflow"]["completion"]["merge"], "handoff")
        self.assertNotIn("merge", pr_loop["side_effects"]["declared"])

        ship = contracts.build_command_contract(
            command="ship",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )
        self.assertIn("merge", ship["side_effects"]["declared"])

        review_cycle = contracts.build_command_contract(
            command="review-cycle",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=requirement,
            evaluation=runtime.evaluate(requirement, report),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(review_cycle["workflow_profile"]["profile"], "review-feedback")
        self.assertEqual(review_cycle["workflow_profile"]["inherits"], "ship.s7-s9")
        self.assertIn("completion_marker", review_cycle["workflow_profile"]
                      ["shared_primitives"])
        self.assertIn("feedback_workflow", review_cycle)

        regression = contracts.build_command_contract(
            command="regression",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=runtime.CapabilityRequirement(required=("git", "worktree")),
            evaluation=runtime.evaluate(
                runtime.CapabilityRequirement(required=("git", "worktree")),
                runtime.CapabilityReport((
                    runtime.Capability("git", True, "ok", "test"),
                    runtime.Capability("worktree", True, "ok", "test"),
                )),
            ),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(regression["workflow_profile"]["profile"], "scan-and-file")
        self.assertTrue(regression["workflow_profile"]["first_class_variant"])
        self.assertIn("scan_contract", regression)
        self.assertIn("dedupe", regression["workflow_profile"]["shared_primitives"])

        review_all_day = contracts.build_command_contract(
            command="review-all-day",
            config=config,
            loaded=loaded,
            plan=plan,
            requirement=runtime.CapabilityRequirement(required=("git",)),
            evaluation=runtime.evaluate(
                runtime.CapabilityRequirement(required=("git",)),
                runtime.CapabilityReport((runtime.Capability("git", True, "ok", "test"),)),
            ),
            transport=github_transport.resolve(report),
        )
        self.assertEqual(review_all_day["workflow_profile"]["profile"], "time-window-scan")
        self.assertTrue(review_all_day["workflow_profile"]["first_class_variant"])
        self.assertIn("scan_contract", review_all_day)
        self.assertIn("issue_prefix", review_all_day["workflow_profile"]["shared_primitives"])

    def test_feedback_workflow_policy_preserves_command_specific_review_loops(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.6",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="make test"),
            policy_pack={
                "name": "feedback",
                "workflow_policies": {
                    "pr-loop": {
                        "posting_mode": "summary",
                        "reviewer_isolation": {"codename_prefix": "PROJECT-PR"},
                    },
                    "review-cycle": {
                        "posting_owner": "reviewer",
                        "completion": {"marker": "project-review-complete"},
                    },
                },
            },
        )

        pr_loop = contracts.feedback_workflow_as_dict(config, "pr-loop")
        self.assertEqual(pr_loop["posting_mode"], "summary")
        self.assertEqual(pr_loop["reviewer_isolation"]["codename_prefix"], "PROJECT-PR")
        self.assertTrue(pr_loop["reviewer_isolation"]["shared_with_ship"])
        self.assertTrue(pr_loop["ci"]["green_required_to_exit"])
        self.assertEqual(pr_loop["completion"]["merge"], "handoff")

        review_cycle = contracts.feedback_workflow_as_dict(config, "review-cycle")
        self.assertEqual(review_cycle["posting_owner"], "reviewer")
        self.assertEqual(review_cycle["completion"]["marker"], "project-review-complete")
        self.assertTrue(review_cycle["completion"]["marker_after_summary"])
        self.assertTrue(review_cycle["review"]["severity_histogram_source_of_truth"])

    def test_feedback_workflow_merge_copies_list_values(self):
        base = {"focuses": ["review"], "nested": {"sources": ["review-comments"]}}
        override = {"focuses": ["ci"], "nested": {"sources": ["issue-comments"]}}

        merged = contracts._deep_merge(base, override)

        self.assertEqual(merged["focuses"], ["ci"])
        self.assertEqual(merged["nested"]["sources"], ["issue-comments"])
        self.assertIsNot(merged["focuses"], override["focuses"])
        self.assertIsNot(merged["nested"]["sources"], override["nested"]["sources"])

    def test_standalone_result_records_are_deterministic(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        transport = github_transport.resolve(runtime.CapabilityReport(()))

        implement = contracts.standalone_result_as_dict(
            command="implement",
            config=config,
            target="issue #76",
            delegate="codex",
            transport=transport,
        )
        self.assertEqual(implement["target"], "issue #76")
        self.assertEqual(implement["base_branch"], config.base_branch)
        self.assertEqual(implement["branch_pattern"], "feature/issue-76-<slug>")
        self.assertEqual(implement["worktree_path_pattern"], "worktrees/issue-76")
        self.assertFalse(implement["handoff"]["merges"])
        self.assertEqual(implement["implementer"]["selected"], "codex")

        ci_check = contracts.standalone_result_as_dict(
            command="ci-check",
            config=config,
            target="PR #104",
            transport=transport,
        )
        self.assertTrue(ci_check["diagnostics"]["read_only"])
        self.assertEqual(ci_check["diagnostics"]["proposed_fix_count"], 1)
        self.assertTrue(ci_check["routing"]["never_direct_merge"])

        missing_shell = runtime.evaluate(
            runtime.CapabilityRequirement(optional=("shell",)),
            runtime.CapabilityReport((runtime.Capability("shell", False, "missing", "test"),)),
        )
        morning = contracts.standalone_result_as_dict(
            command="morning",
            config=config,
            transport=transport,
            evaluation=missing_shell,
        )
        self.assertEqual(morning["brief"]["health_providers"][0]["status"], "unavailable")
        self.assertEqual(
            morning["brief"]["health_providers"][0]["missing_optional_capabilities"],
            ["shell"],
        )
        self.assertEqual(morning["brief"]["deferral_queue"]["status"], "configured")
        self.assertEqual(morning["brief"]["run_ledger"]["path"], ".keel/state/run-ledger.jsonl")
        self.assertFalse(morning["execution"]["runs_project_health_commands"])

        wrap = contracts.standalone_result_as_dict(
            command="wrap",
            config=config,
            target="PR title",
            transport=transport,
        )
        self.assertEqual(wrap["session"]["wrap"]["pull_request"]["base_branch"],
                         config.base_branch)
        self.assertTrue(
            wrap["session"]["wrap"]["workspace_preflight"]["must_run_from_linked_worktree"]
        )
        self.assertFalse(wrap["execution"]["creates_prs"])
        self.assertEqual(wrap["session"]["run_ledger"]["path"], ".keel/state/run-ledger.jsonl")

        work_block = contracts.standalone_result_as_dict(
            command="work-block",
            config=config,
            target="issues #146, #172",
            transport=transport,
        )
        self.assertEqual(work_block["session"]["work_block"]["mode"], "daytime")
        self.assertTrue(
            work_block["session"]["daytime"]["ship_handoff"]
            ["refreshes_readiness_between_issues"]
        )
        self.assertFalse(work_block["execution"]["merges"])

        overnight = contracts.standalone_result_as_dict(
            command="overnight",
            config=config,
            target="8h session",
            transport=transport,
        )
        self.assertTrue(overnight["session"]["overnight"]["mode_source"]["shared_with_ship"])
        self.assertTrue(
            overnight["session"]["overnight"]["ship_handoff"]["passes_operator_consent_scope"]
        )
        self.assertFalse(overnight["execution"]["merges"])

        pr_loop = contracts.standalone_result_as_dict(
            command="pr-loop",
            config=config,
            target="PR #73",
            transport=transport,
        )
        self.assertEqual(pr_loop["target"], "PR #73")
        self.assertEqual(pr_loop["feedback_workflow"]["posting_mode"], "summary")
        self.assertTrue(pr_loop["feedback_workflow"]["ci"]["green_required_to_exit"])
        self.assertFalse(pr_loop["execution"]["posts_comments"])

        review_cycle = contracts.standalone_result_as_dict(
            command="review-cycle",
            config=config,
            target="PR #73",
            transport=transport,
        )
        self.assertEqual(
            review_cycle["feedback_workflow"]["completion"]["marker"],
            "review-cycle-complete",
        )
        self.assertEqual(review_cycle["feedback_workflow"]["completion"]["merge"], "never")
        self.assertFalse(review_cycle["execution"]["merges"])

        regression = contracts.standalone_result_as_dict(
            command="regression",
            config=config,
            target="scope full",
            transport=transport,
        )
        self.assertEqual(regression["scan"]["dedupe"]["near_text_similarity"], 0.6)
        self.assertTrue(regression["scan"]["regression"]["scan_target"]
                        ["clean_tree_preflight"])
        self.assertEqual(regression["scan"]["regression"]["issue_creation"]["route_to"],
                         "ship")
        self.assertFalse(regression["execution"]["writes_issues"])

        review_all_day = contracts.standalone_result_as_dict(
            command="review-all-day",
            config=config,
            target="1 day scan",
            transport=transport,
        )
        self.assertEqual(
            review_all_day["scan"]["review_all_day"]["issue_creation"]["title_prefix"],
            "[review-all-day] ",
        )
        self.assertEqual(
            review_all_day["scan"]["review_all_day"]["strategy"]["batch_threshold"],
            5,
        )
        self.assertTrue(review_all_day["scan"]["write_safety"]["orchestrator_only_writes"])

        default_target = contracts.standalone_result_as_dict(
            command="implement",
            config=config,
        )
        self.assertEqual(default_target["branch_pattern"], "feature/issue-selected-issue-<slug>")

        named_target = contracts.standalone_result_as_dict(
            command="implement",
            config=config,
            target="Backlog item",
        )
        self.assertEqual(named_target["worktree_path_pattern"], "worktrees/issue-backlog-item")

        unknown = contracts.standalone_result_as_dict(
            command="unknown",
            config=config,
            target="custom",
        )
        self.assertEqual(unknown, {"command": "unknown", "target": "custom"})

    def test_morning_contract_is_project_extensible(self):
        android = cfg.load_config(PROJECTS / "example-android.yaml")
        flutter = cfg.load_config(PROJECTS / "example-flutter.yaml")
        report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        evaluation = runtime.evaluate(
            runtime.CapabilityRequirement(optional=("shell", "gh", "gh-auth")),
            report,
        )

        android_brief = contracts.morning_contract_as_dict(
            config=android,
            evaluation=evaluation,
            transport=github_transport.resolve(report),
        )
        flutter_brief = contracts.morning_contract_as_dict(
            config=flutter,
            evaluation=evaluation,
            transport=github_transport.resolve(report),
        )

        self.assertEqual(android_brief["health_providers"][0]["name"], "app-health")
        self.assertEqual(flutter_brief["health_providers"][0]["name"], "release")
        self.assertEqual(flutter_brief["reports"]["morning"]["path"], "reports/morning/")
        self.assertNotEqual(
            android_brief["health_providers"][0]["command"],
            flutter_brief["health_providers"][0]["command"],
        )

    def test_morning_contract_provider_edge_cases(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.1",
            base_branch="main",
            repo="edge",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            policy_pack={
                "name": "edge",
                "health_providers": {
                    "required-down": {
                        "kind": "external",
                        "required_capabilities": ["firebase"],
                    },
                    "missing-command": {
                        "kind": "project-command",
                    },
                    "unknown-yet": {
                        "kind": "external",
                        "optional_capabilities": ["shell"],
                    },
                },
                "reports": {
                    "priorities": "reports/priorities.md",
                    "deferrals": "reports/deferrals.md",
                },
            },
        )
        capability_check = runtime.evaluate(
            runtime.CapabilityRequirement(required=("firebase",), optional=("shell",)),
            runtime.CapabilityReport((
                runtime.Capability("firebase", False, "missing", "test"),
                runtime.Capability("shell", True, "ok", "test"),
            )),
        )

        brief = contracts.morning_contract_as_dict(config=config, evaluation=capability_check)
        providers = {provider["name"]: provider for provider in brief["health_providers"]}
        self.assertEqual(providers["required-down"]["status"], "blocked")
        self.assertEqual(providers["missing-command"]["status"], "unavailable")
        self.assertEqual(providers["unknown-yet"]["status"], "available")
        self.assertEqual(brief["deferral_queue"]["status"], "configured")
        self.assertIn(
            {"id": "priorities_report", "path": "reports/priorities.md"},
            brief["priority_sources"],
        )

        no_capability_check = contracts.morning_contract_as_dict(config=config, evaluation=None)
        providers = {
            provider["name"]: provider
            for provider in no_capability_check["health_providers"]
        }
        self.assertEqual(providers["required-down"]["missing_required_capabilities"], [])

    def test_session_contract_uses_project_reports_and_deferrals(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.1",
            base_branch="main",
            repo="edge",
            timezone="Europe/Istanbul",
            merge_window="07:00-01:30",
            knobs=cfg.Knobs(build_gate_cmd="true", sot_doc="AGENTS.md"),
            policy_pack={
                "name": "edge",
                "risk_rules": [{"id": "release-risk", "paths": ["release/**"]}],
                "reports": {
                    "session": "reports/session/",
                    "overnight": "reports/overnight/",
                    "deferrals": "reports/deferrals.md",
                },
            },
        )

        wrap = contracts.session_contract_as_dict(command="wrap", config=config)
        overnight = contracts.session_contract_as_dict(command="overnight", config=config)

        self.assertEqual(wrap["reports"]["session"]["path"], "reports/session/")
        self.assertEqual(wrap["deferral_queue"]["status"], "configured")
        self.assertEqual(wrap["wrap"]["recap"]["path"], "reports/session/")
        self.assertEqual(overnight["overnight"]["report"]["night_path"],
                         "reports/overnight/")
        self.assertIn("release-risk", overnight["project_policy_sources"]["risk_rules"])
        self.assertIn("three-consecutive-unresolved-ci-failures",
                      overnight["overnight"]["stop_conditions"])

        base_only = contracts.session_contract_as_dict(command="other", config=config)
        self.assertIn("reports", base_only)
        self.assertNotIn("wrap", base_only)
        self.assertNotIn("overnight", base_only)

    def test_scan_contract_unknown_command_returns_base_contract_only(self):
        config = cfg.load_config(PROJECTS / "example-flutter.yaml")

        scan = contracts.scan_contract_as_dict(command="other", config=config)

        self.assertIn("dedupe", scan)
        self.assertNotIn("regression", scan)
        self.assertNotIn("review_all_day", scan)

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

    def test_reporting_commands_expose_parity_contracts(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement(optional=("gh", "gh-auth"))

        expectations = {
            "coverage": {
                "profile": "reporting",
                "prefix": "COVERAGE-<PR>-",
                "idempotency": {
                    "scope": "one-comment-per-pr",
                    "existing_comment": "update-in-place",
                    "update_unavailable": "do-not-post-duplicate",
                },
                "effect": "labels",
                "primitive": "codename_anchor",
            },
            "deps-audit": {
                "profile": "reporting",
                "prefix": "DEPS-AUDIT-<DATE>-",
                "idempotency": {
                    "scope": "append-per-run",
                    "find_tracking_issue_by_exact_title": "deps-audit: <DATE>",
                },
                "effect": "issue_write",
                "primitive": "dedupe",
            },
            "flake-audit": {
                "profile": "reporting",
                "prefix": "FLAKE-AUDIT-<DATE>-",
                "idempotency": {
                    "scope": "one-issue-per-flake",
                    "dedupe_issue_title": "flaky test: <fully.qualified.name>",
                },
                "effect": "comments",
                "primitive": "ship_handoff",
            },
        }

        for command, expected in expectations.items():
            with self.subTest(command=command):
                contract = contracts.build_command_contract(
                    command=command,
                    config=config,
                    loaded=loaded,
                    plan=plan,
                    requirement=requirement,
                    evaluation=runtime.evaluate(requirement, report),
                    transport=github_transport.resolve(report),
                )
                profile = contract["workflow_profile"]
                reporting = contract["reporting_contract"]

                self.assertEqual(profile["profile"], expected["profile"])
                self.assertTrue(profile["first_class_variant"])
                self.assertIn(expected["primitive"], profile["shared_primitives"])
                self.assertEqual(reporting["codename_prefix"], expected["prefix"])
                self.assertFalse(reporting["dry_run"]["mutates"])
                self.assertTrue(reporting["dry_run"]["prints_planned_writes"])
                self.assertEqual(reporting["handoff"]["fixes_route_to"], "ship")
                self.assertFalse(reporting["handoff"]["auto_applies_fixes"])
                self.assertIn(expected["effect"], contract["side_effects"]["declared"])
                for key, value in expected["idempotency"].items():
                    self.assertEqual(reporting["idempotency"][key], value)

    def test_reporting_contract_preserves_command_specific_safety_rules(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        loaded = {}
        plan = orchestrator.build_plan(config, loaded)
        report = runtime.CapabilityReport(())
        requirement = runtime.CapabilityRequirement()

        by_command = {
            command: contracts.build_command_contract(
                command=command,
                config=config,
                loaded=loaded,
                plan=plan,
                requirement=requirement,
                evaluation=runtime.evaluate(requirement, report),
                transport=github_transport.resolve(report),
            )["reporting_contract"]
            for command in ("coverage", "deps-audit", "flake-audit")
        }

        self.assertEqual(
            by_command["coverage"]["labels"],
            {
                "regression": "coverage-regression",
                "operation": "idempotent-add-or-remove",
            },
        )
        self.assertEqual(by_command["coverage"]["degradation"]["unwired_tool"], "skip-area")
        self.assertEqual(
            by_command["coverage"]["degradation"]["coverage_command_failure"],
            "fatal",
        )

        self.assertEqual(
            by_command["deps-audit"]["tracking_issue_title"],
            "deps-audit: <DATE>",
        )
        self.assertEqual(
            by_command["deps-audit"]["degradation"]["per_ecosystem_failure"],
            "skipped-section",
        )

        self.assertEqual(
            by_command["flake-audit"]["classification"]["rule"],
            "across-run-disagreement-only",
        )
        self.assertEqual(by_command["flake-audit"]["classification"]["minimum_failures"], 3)
        self.assertEqual(
            by_command["flake-audit"]["classification"]["consistent_failures"],
            "real-bug-not-flake",
        )
        self.assertEqual(
            by_command["flake-audit"]["degradation"]["artifact_unavailable"],
            "run-level-limitations-section",
        )

    def test_reporting_contract_unknown_command_returns_base_metadata(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")

        reporting = contracts.reporting_contract_as_dict(
            command="unknown-report",
            config=config,
        )

        self.assertEqual(reporting["command"], "unknown-report")
        self.assertEqual(reporting["base_branch"], config.base_branch)
        self.assertEqual(reporting["timezone"], config.timezone)
        self.assertEqual(reporting["policy_source"], "policy_pack + adapter arguments")
        self.assertFalse(reporting["dry_run"]["mutates"])
        self.assertFalse(reporting["handoff"]["auto_applies_fixes"])

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


class TestShipResultClosureComment(unittest.TestCase):
    def _assessment(self):
        merge = SimpleNamespace(action="merge", reason="all gates passed")
        return SimpleNamespace(
            tier=2,
            reviewers=2,
            window_open=True,
            ci_ok=None,
            merge=merge,
            halted=False,
            bypassed_window=False,
            review_contract={},
        )

    def _verdict(self):
        return SimpleNamespace(blocked=False, counts={"blocker": 0}, findings=[])

    def test_closure_comment_rendered_from_ledger_record(self):
        record = {
            "actors": {"implementer": "codex (gpt-5)", "reviewers": [], "tester": None},
            "issue": {"number": 1},
            "pull_request": {"number": 190},
            "head_sha": "abc123",
            "changes": {"file_count": 0, "files": []},
            "capture": {"status": "applied", "reason": None},
            "run_id": "RUN-1",
            "target": "issue #1",
        }
        result = contracts.ship_result_as_dict(
            changed_files=[],
            outcomes=[],
            verdict=self._verdict(),
            assessment=self._assessment(),
            run_ledger={"record": record},
        )
        self.assertIn("## Ship outcome", result["closure_comment"])
        self.assertIn("- **Implementer:** codex (gpt-5)", result["closure_comment"])
        self.assertIn("## Summary", result["artifact_bodies"]["pr_body"])
        self.assertIn("Closes #1", result["artifact_bodies"]["pr_body"])
        self.assertIn("keel.review-verdict.v1",
                      result["artifact_bodies"]["review_verdict_template"])
        self.assertIn("head: abc123", result["artifact_bodies"]["review_verdict_template"])
        self.assertIn("keel.jury-verdict.v1",
                      result["artifact_bodies"]["jury_verdict_template"])
        self.assertIn("<!-- keel.extension-result.v1 -->",
                      result["artifact_bodies"]["extension_result_template"])

    def test_artifact_bodies_summarize_skipped_gates_and_docs(self):
        result = contracts.ship_result_as_dict(
            changed_files=["docs/keel/cli.md"],
            outcomes=[
                gates.GateOutcome("build", True, skipped=True),
                gates.GateOutcome("lint", False, error="lint failed"),
            ],
            verdict=self._verdict(),
            assessment=self._assessment(),
        )

        pr_body = result["artifact_bodies"]["pr_body"]
        self.assertIn("build: skipped", pr_body)
        self.assertIn("lint: failed (lint failed)", pr_body)
        self.assertIn("Updated docs: `docs/keel/cli.md`", pr_body)

    def test_closure_comment_none_without_record(self):
        result = contracts.ship_result_as_dict(
            changed_files=[],
            outcomes=[],
            verdict=self._verdict(),
            assessment=self._assessment(),
            run_ledger={"appended": False},
        )
        self.assertIsNone(result["closure_comment"])
        self.assertIn("Refs #<issue-number>", result["artifact_bodies"]["pr_body"])

    def test_closure_comment_none_without_ledger(self):
        result = contracts.ship_result_as_dict(
            changed_files=[],
            outcomes=[],
            verdict=self._verdict(),
            assessment=self._assessment(),
        )
        self.assertIsNone(result["closure_comment"])
        self.assertIn("reviewer: reviewer", result["artifact_bodies"]["review_verdict_template"])


if __name__ == "__main__":
    unittest.main()
