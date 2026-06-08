"""Unit tests for the keel CLI."""

import contextlib
import io
import json
import subprocess
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from keel import cli, runtime

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestVersion(unittest.TestCase):
    def test_version_subcommand(self):
        rc, out, _ = run(["version"])
        self.assertEqual(rc, 0)
        self.assertIn("keel", out)


class TestNoCommand(unittest.TestCase):
    def test_prints_help_and_returns_2(self):
        rc, out, _ = run([])
        self.assertEqual(rc, 2)
        self.assertIn("usage", out.lower())


class TestValidate(unittest.TestCase):
    def test_valid_configs(self):
        rc, out, _ = run(["validate", str(PROJECTS / "keel.yaml"),
                          str(PROJECTS / "example-android.yaml")])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("OK"), 2)

    def test_missing_file(self):
        rc, out, _ = run(["validate", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("MISSING", out)

    def test_invalid_config(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")  # missing required keys
            bad = f.name
        rc, out, _ = run(["validate", bad])
        self.assertEqual(rc, 1)
        self.assertIn("INVALID", out)

    def test_strict_extensions_missing_root(self):
        # example-flutter references extension files not present in this repo -> strict fail.
        rc, out, _ = run(["validate", str(PROJECTS / "example-flutter.yaml"),
                          "--root", str(REPO_ROOT)])
        self.assertEqual(rc, 1)
        self.assertIn("extensions", out)


class TestPlan(unittest.TestCase):
    def test_plan_renders_backbone(self):
        rc, out, err = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("s10  merge", out)
        self.assertIn("gate: build", out)
        self.assertIn("runtime capabilities", out)

    def test_plan_json_includes_capabilities(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT), "--json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("contract", data)
        self.assertIn("capabilities", data)
        self.assertIn("github_transport", data)
        self.assertIn("plan", data)
        self.assertEqual(data["contract"]["schema_version"], "keel.command-contract.v1")
        self.assertEqual(data["contract"]["command"], "ship")
        self.assertIn("review_merge_contract", data["contract"])

    def test_plan_json_resolves_review_jury_flags(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "ship", "--reviewers", "2", "--review-comments", "summary",
             "--jury", "--jury-advisory", "--json"]
        )
        self.assertEqual(rc, 0)
        review = json.loads(out)["contract"]["review_merge_contract"]
        self.assertEqual(review["reviewers"]["count"], 2)
        self.assertEqual(review["reviewers"]["source"], "override")
        self.assertEqual(review["posting"]["mode"], "summary")
        self.assertEqual(review["jury"]["mode"], "advisory")

    def test_plan_json_can_expose_other_command_graph(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "morning", "--json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "morning")
        self.assertTrue(data["contract"]["graph"])
        self.assertIn("gh", data["contract"]["optional_capabilities"])

    def test_plan_live_json_blocks_when_consent_missing(self):
        rc, out, err = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--live", "--json", "--target", "issue #82"]
        )
        self.assertEqual(rc, 1)
        data = json.loads(out)
        consent = data["contract"]["operator_consent"]
        self.assertEqual(consent["status"], "missing")
        self.assertTrue(consent["requires_operator_consent"])
        self.assertIn("filesystem", consent["missing_scope"])
        self.assertIn("operator consent", err)

    def test_plan_live_json_accepts_approved_scope(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--live", "--json", "--target", "issue #82",
             "--approve-scope", "filesystem,git,github", "--operator", "tester"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        consent = data["contract"]["operator_consent"]
        self.assertEqual(consent["status"], "approved")
        self.assertEqual(consent["consent_record"]["operator"], "tester")
        self.assertFalse(consent["consent_record"]["secret_values_recorded"])

    def test_plan_missing_config(self):
        rc, _, err = run(["plan", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_plan_reports_extension_problems_on_stderr(self):
        # example-flutter's extension files are not in this repo -> fail-soft warnings.
        rc, out, err = run(["plan", str(PROJECTS / "example-flutter.yaml"),
                            "--root", str(REPO_ROOT)])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)

    def test_plan_invalid_config(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad = f.name
        rc, _, err = run(["plan", bad])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


def _write_raw(text):
    import tempfile
    fd = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    fd.write(text)
    fd.close()
    return fd.name


def _write_config(build_cmd):
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
    )


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class TestRunGates(unittest.TestCase):
    def test_passing_gate(self):
        rc, out, _ = run(["run-gates", _write_config("'true'"), "--root", "."])
        self.assertEqual(rc, 0)
        self.assertIn("ok", out)
        self.assertIn("build", out)

    def test_failing_gate_blocks(self):
        rc, out, _ = run(["run-gates", _write_config("'false'"), "--root", "."])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        self.assertIn("BLOCKED", out)

    def test_missing_config(self):
        rc, _, err = run(["run-gates", "/no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        rc, _, err = run(["run-gates", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_unknown_builtin_gate(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc, _, err = run(["run-gates", p])
        self.assertEqual(rc, 1)
        self.assertIn("unknown built-in gate", err)

    def test_reports_extension_problem(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "extensions:\n  tester: [ghost.md]\nextensions_dir: .keel/extensions\n")
        rc, out, err = run(["run-gates", p, "--root", "/tmp"])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)


class TestPlanErrors(unittest.TestCase):
    def test_plan_unknown_builtin_gate(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc, _, err = run(["plan", p])
        self.assertEqual(rc, 1)
        self.assertIn("unknown built-in gate", err)

    def test_plan_invalid_config(self):
        rc, _, err = run(["plan", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


class TestWindow(unittest.TestCase):
    def test_configured(self):
        rc, out, _ = run(["window", str(PROJECTS / "example-android.yaml")])
        self.assertEqual(rc, 0)
        self.assertIn("merge window", out)
        self.assertIn("Etc/GMT-3", out)

    def test_not_configured(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "knobs:\n  build_gate_cmd: 'true'\n")
        rc, out, _ = run(["window", p])
        self.assertEqual(rc, 0)
        self.assertIn("no merge window", out)

    def test_missing_config(self):
        rc, _, err = run(["window", "/no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        rc, _, err = run(["window", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


class TestShip(unittest.TestCase):
    def test_clean_merges(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:  # non-git root -> no changed files
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel ship", out)
        self.assertIn("TIER-2", out)        # empty changeset -> default tier
        self.assertIn("DECISION", out.upper())
        self.assertIn("MERGE", out)
        self.assertIn("github        :", out)

    def test_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--dry-run", "--json", "--review-comments", "summary",
                              "--no-jury", "--reviewers", "1"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ship")
        self.assertTrue(data["contract"]["dry_run"])
        self.assertFalse(data["contract"]["side_effects"]["mutates_in_dry_run"])
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-dry-run")
        self.assertEqual(data["result"]["changed_file_count"], 0)
        self.assertEqual(data["result"]["assessment"]["merge"]["action"], "merge")
        review = data["result"]["assessment"]["review_merge_contract"]
        self.assertEqual(review["reviewers"]["count"], 1)
        self.assertEqual(review["posting"]["mode"], "summary")
        self.assertEqual(review["jury"]["mode"], "off")

    def test_ship_v2_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship-v2", _write_config("'true'"), "--root", d,
                              "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ship-v2")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")
        self.assertEqual(data["contract"]["workflow_profile"]["inherits"], "ship")
        self.assertEqual(
            data["contract"]["workflow_profile"]["step_overrides"]["s7"]["step"],
            "review",
        )
        self.assertIn("review_merge_contract", data["contract"])
        self.assertIn("result", data)

    def test_json_contract_matches_assessment_for_tier3_auto_jury(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _run_git(root, "init", "-b", "main")
            _run_git(root, "config", "user.email", "test@example.com")
            _run_git(root, "config", "user.name", "Test User")
            (root / "README.md").write_text("base\n", encoding="utf-8")
            _run_git(root, "add", "README.md")
            _run_git(root, "commit", "-m", "base")
            _run_git(root, "checkout", "-b", "feature")
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: ci\n", encoding="utf-8")
            _run_git(root, "add", ".github/workflows/ci.yml")
            _run_git(root, "commit", "-m", "change workflow")

            config = _write_raw(
                "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: tmp\n"
                "gates: [build, jury]\nknobs:\n  build_gate_cmd: 'true'\n"
                "  tier3_globs: ['.github/workflows/**']\n"
            )
            rc, out, _ = run(["ship", config, "--root", d, "--dry-run", "--json"])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        contract_review = data["contract"]["review_merge_contract"]
        assessment_review = data["result"]["assessment"]["review_merge_contract"]
        self.assertEqual(contract_review["reviewers"]["tier"], 3)
        self.assertEqual(contract_review["reviewers"]["count"], 3)
        self.assertEqual(contract_review["reviewers"]["source"], "risk-tier")
        self.assertEqual(contract_review["jury"]["mode"], "gating")
        self.assertEqual(contract_review, assessment_review)

    def test_ship_rejects_conflicting_live_and_dry_run_flags(self):
        rc, _, err = run(["ship", _write_config("'true'"), "--dry-run", "--live"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot be used together", err)

    def test_ship_live_json_blocks_before_running_gates_without_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'false'"), "--root", d,
                              "--live", "--json", "--target", "issue #82"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("contract", data)
        self.assertNotIn("result", data)
        self.assertEqual(data["contract"]["operator_consent"]["status"], "missing")
        self.assertIn("github", data["contract"]["operator_consent"]["missing_scope"])

    def test_ship_live_json_runs_after_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--live", "--json", "--target", "issue #82",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["mode"], "live")
        self.assertEqual(data["contract"]["operator_consent"]["status"], "approved")
        self.assertIn("result", data)

    def test_failing_gate_blocks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'false'"), "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("BLOCK", out)

    def test_missing_config(self):
        rc, _, err = run(["ship", "/no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        rc, _, err = run(["ship", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_bogus_gate_errors(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", p, "--root", d])
        self.assertEqual(rc, 1)
        self.assertTrue(err)

    def test_missing_required_capability_blocks_before_ship(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", p, "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

    def test_pr_ci_requires_transport_check_runs(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
            runtime.Capability("github-mcp", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, _, err = run(["ship", _write_config("'true'"), "--root", d, "--pr", "7"])
        self.assertEqual(rc, 1)
        self.assertIn("missing required GitHub transport capability: check_runs", err)

    def test_unloadable_extension_warned(self):
        import tempfile
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "extensions:\n  tester: [missing.md]\nextensions_dir: .keel/extensions\n")
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", p, "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)


class TestStandaloneCommands(unittest.TestCase):
    def test_implement_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--dry-run", "--json",
                              "--delegate", "codex"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "implement")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "standalone-step")
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-dry-run")
        self.assertIn("git", data["contract"]["required_capabilities"])
        self.assertEqual(data["result"]["target"], "issue #76")
        self.assertFalse(data["result"]["handoff"]["merges"])
        self.assertEqual(data["result"]["implementer"]["selected"], "codex")

    def test_implement_live_blocks_without_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["contract"]["operator_consent"]["status"], "missing")
        self.assertIn("filesystem", data["contract"]["operator_consent"]["missing_scope"])
        self.assertIn("result", data)

    def test_implement_live_accepts_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["mode"], "live")
        self.assertEqual(data["contract"]["operator_consent"]["status"], "approved")

    def test_ci_check_json_contract_is_read_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ci-check", _write_config("'true'"), "--root", d,
                              "--pr", "104", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ci-check")
        self.assertEqual(
            data["contract"]["workflow_profile"]["profile"],
            "standalone-diagnostic",
        )
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-read-only")
        self.assertFalse(data["contract"]["operator_consent"]["would_require_operator_consent"])
        self.assertEqual(data["result"]["target"], "PR #104")
        self.assertTrue(data["result"]["diagnostics"]["read_only"])
        self.assertTrue(data["result"]["routing"]["never_direct_merge"])

    def test_ci_check_does_not_inherit_project_mutation_requirements(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n"
                       "  ci_workflows:\n    ci: CI\n")
        rc, out, _ = run(["ci-check", p, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("release-publish", data["contract"]["required_capabilities"])
        self.assertFalse(data["contract"]["operator_consent"]["would_require_operator_consent"])

    def test_morning_json_contract_surfaces_health_reports_and_deferrals(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", False, "missing", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["morning", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d, "--since", "yesterday", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "morning")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "daily-brief")
        self.assertEqual(data["result"]["target"], "since yesterday")
        self.assertEqual(data["result"]["brief"]["reports"]["morning"]["path"],
                         "reports/morning/")
        self.assertEqual(data["result"]["brief"]["health_providers"][0]["status"],
                         "unavailable")
        self.assertEqual(data["result"]["brief"]["missing_optional_policy"],
                         "unavailable-not-success")

    def test_morning_does_not_inherit_project_mutation_requirements(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n"
                       "policy_pack:\n  name: x\n  health_providers:\n"
                       "    status:\n      kind: project-command\n"
                       "      command: .keel/health/status\n"
                       "      optional_capabilities: [shell]\n")
        rc, out, _ = run(["morning", p, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("release-publish", data["contract"]["required_capabilities"])
        self.assertIn("shell", data["contract"]["optional_capabilities"])
        self.assertFalse(data["result"]["execution"]["runs_project_health_commands"])

    def test_morning_requirement_ignores_non_map_health_provider(self):
        config = cli.cfg.ProjectConfig(
            extends="keel",
            core_version="^0.1",
            base_branch="main",
            knobs=cli.cfg.Knobs(build_gate_cmd="true"),
            policy_pack={
                "name": "edge",
                "health_providers": {
                    "invalid": "not-a-provider-map",
                    "valid": {
                        "kind": "external",
                        "required_capabilities": ["firebase"],
                    },
                },
            },
        )
        requirement = cli._morning_capability_requirement(config)
        self.assertEqual(requirement.required, ("firebase",))

    def test_wrap_json_contract_surfaces_session_reports_and_worktree_guard(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["wrap", str(PROJECTS / "example-flutter.yaml"),
                              "feat: finish session", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "wrap")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "session-wrap")
        self.assertEqual(data["result"]["target"], "feat: finish session")
        self.assertTrue(
            data["result"]["session"]["wrap"]["workspace_preflight"]
            ["must_run_from_linked_worktree"]
        )
        self.assertFalse(data["result"]["execution"]["creates_prs"])

    def test_overnight_json_contract_uses_ship_window_and_handoff(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["overnight", str(PROJECTS / "example-flutter.yaml"),
                              "6", "--max", "3", "--root", d, "--review-comments",
                              "summary", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "overnight")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "session-overnight")
        self.assertEqual(data["result"]["target"], "6h session (max 3)")
        self.assertTrue(
            data["result"]["session"]["overnight"]["mode_source"]["shared_with_ship"]
        )
        self.assertTrue(
            data["result"]["session"]["overnight"]["ship_handoff"]
            ["passes_operator_consent_scope"]
        )
        self.assertFalse(data["result"]["execution"]["merges"])

    def test_regression_json_contract_surfaces_scan_policy_and_issue_consent(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["regression", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d, "--scope", "changed", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "regression")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "scan-and-file")
        self.assertIn("scan_contract", data["contract"])
        self.assertIn("worktree", data["contract"]["required_capabilities"])
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-dry-run")
        self.assertEqual(data["result"]["target"], "scope changed")
        self.assertEqual(data["result"]["scan"]["areas"][0]["name"], "backend")
        self.assertTrue(
            data["result"]["scan"]["regression"]["scan_target"]["read_only_worktree"]
        )
        self.assertFalse(data["result"]["execution"]["writes_issues"])

    def test_review_all_day_json_contract_preserves_title_prefix_and_scope(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["review-all-day", str(PROJECTS / "example-flutter.yaml"),
                              "7", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "review-all-day")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "time-window-scan")
        self.assertEqual(data["result"]["target"], "7 day scan")
        self.assertEqual(
            data["result"]["scan"]["review_all_day"]["issue_creation"]["title_prefix"],
            "[review-all-day] ",
        )
        self.assertEqual(
            data["result"]["scan"]["review_all_day"]["span"]
            ["n_days_argument_covers_calendar_days"],
            "N+1",
        )
        self.assertFalse(data["result"]["execution"]["pushes"])

    def test_scan_commands_do_not_inherit_project_mutation_requirements(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n"
                       "policy_pack:\n  name: x\n  scan:\n"
                       "    areas:\n      core: ['src/**']\n")
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            rc, out, _ = run(["regression", p, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("release-publish", data["contract"]["required_capabilities"])
        self.assertIn("git", data["contract"]["required_capabilities"])

    def test_standalone_commands_reject_non_positive_targets(self):
        with self.assertRaises(SystemExit) as raised:
            run(["implement", _write_config("'true'"), "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_implement_rejects_conflicting_live_and_dry_run_flags(self):
        rc, _, err = run(["implement", _write_config("'true'"), "76",
                          "--dry-run", "--live"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot be used together", err)

    def test_implement_missing_and_invalid_config_errors(self):
        rc, _, err = run(["implement", "/no/such.yaml", "76"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

        rc, _, err = run(["implement", _write_raw("extends: keel\n"), "76"])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_implement_reports_extension_and_gate_errors(self):
        import tempfile
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "extensions:\n  tester: [missing.md]\nextensions_dir: .keel/extensions\n")
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["implement", p, "76", "--root", d, "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)

        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc, _, err = run(["implement", p, "76"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown built-in gate", err)

    def test_implement_blocks_on_missing_required_capability_and_bad_scope(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n")
        rc, _, err = run(["implement", p, "76"])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

        rc, _, err = run(["implement", _write_config("'true'"), "76",
                          "--live", "--approve-scope", "bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)

    def test_implement_human_output_and_missing_consent_message(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--delegate", "codex"])
        self.assertEqual(rc, 0)
        self.assertIn("keel implement", out)
        self.assertIn("worktree", out)
        self.assertIn("delegate", out)
        self.assertIn("never in standalone implement", out)

        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76", "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel implement", out)
        self.assertNotIn("delegate      :", out)

        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live",
                              "--approve-scope", "filesystem,git,github",
                              "--target", "extra context"])
        self.assertEqual(rc, 0)
        self.assertIn("issue #76 (extra context)", out)
        self.assertIn("live preflight contract", out)

        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live"])
        self.assertEqual(rc, 1)
        self.assertIn("Missing approved scope", err)

    def test_ci_check_human_output_with_optional_degradation_and_target(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["ci-check", _write_config("'true'"), "--root", d,
                              "--target", "current branch"])
        self.assertEqual(rc, 0)
        self.assertIn("keel ci-check", out)
        self.assertIn("current branch", out)
        self.assertIn("degraded opt.", out)
        self.assertIn("read-only", out)

    def test_morning_human_output_with_optional_degradation(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", False, "missing", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["morning", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel morning", out)
        self.assertIn("reports", out)
        self.assertIn("health", out)
        self.assertIn("unavailable", out)
        self.assertIn("degraded opt.", out)

    def test_morning_human_output_without_unavailable_provider(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["morning", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("health", out)
        self.assertNotIn("unavailable   :", out)

    def test_wrap_and_overnight_human_output(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, wrap_out, _ = run(["wrap", str(PROJECTS / "example-flutter.yaml"),
                                   "--root", d])
            rc2, overnight_out, _ = run(["overnight", str(PROJECTS / "example-flutter.yaml"),
                                         "2", "--root", d])
        self.assertEqual(rc, 0)
        self.assertEqual(rc2, 0)
        self.assertIn("keel wrap", wrap_out)
        self.assertIn("linked required=True", wrap_out)
        self.assertIn("ready PR", wrap_out)
        self.assertIn("keel overnight", overnight_out)
        self.assertIn("mode source", overnight_out)
        self.assertIn("no-night-merge", overnight_out)

    def test_scan_human_output(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["regression", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel regression", out)
        self.assertIn("areas", out)
        self.assertIn("issues only after consent", out)

        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, review_out, _ = run(["review-all-day", str(PROJECTS / "example-flutter.yaml"),
                                     "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel review-all-day", review_out)
        self.assertIn("[review-all-day] ", review_out)

    def test_standalone_target_combines_days_and_scope_when_present(self):
        target = cli._standalone_target(Namespace(
            issue=None,
            pr=None,
            since=None,
            scope="changed",
            days=7,
            target=None,
            title=None,
            hours=None,
        ))
        self.assertEqual(target, "7 day scan (scope changed)")

    def test_standalone_human_output_for_unknown_adapter_profile_falls_through(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            args = Namespace(
                dry_run=True,
                live=False,
                standalone_command="custom-adapter",
                path=_write_config("'true'"),
                root=d,
                pr=None,
                approve_scope=[],
                operator=None,
                target="custom target",
                json=False,
                delegate=None,
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli._cmd_standalone(args)
        self.assertEqual(rc, 0)
        self.assertIn("keel custom-adapter", out.getvalue())
        self.assertIn("custom target", out.getvalue())


class TestCapabilities(unittest.TestCase):
    def test_prints_runtime_report(self):
        rc, out, _ = run(["capabilities", "--root", "."])
        self.assertEqual(rc, 0)
        self.assertIn("keel capabilities", out)
        self.assertIn("shell", out)

    def test_json_report(self):
        rc, out, _ = run(["capabilities", "--root", ".", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"report"', out)
        self.assertIn('"github_transport"', out)
        self.assertIn('"capabilities"', out)

    def test_reports_mcp_transport_when_available(self):
        fake_report = runtime.CapabilityReport((
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            rc, out, _ = run(["capabilities"])
        self.assertEqual(rc, 0)
        self.assertIn("selected: mcp", out)
        self.assertIn("raw_actions_logs", out)

    def test_project_requirement_failure_returns_nonzero(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "knobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n")
        rc, out, _ = run(["capabilities", "--project", p])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", out)


class TestInit(unittest.TestCase):
    def test_scaffolds_and_validates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pubspec.yaml").write_text("name: app\n")
            rc, out, _ = run(["init", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("flutter", out)
            written = Path(d) / ".keel" / "project.yaml"
            self.assertTrue(written.exists())
            # the generated config must validate
            vrc, _, _ = run(["validate", str(written)])
            self.assertEqual(vrc, 0)

    def test_refuses_existing_without_force(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            keel = Path(d) / ".keel"
            keel.mkdir()
            (keel / "project.yaml").write_text("x")
            rc, _, err = run(["init", "--root", d])
            self.assertEqual(rc, 1)
            self.assertIn("already exists", err)

    def test_force_overwrites(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            keel = Path(d) / ".keel"
            keel.mkdir()
            (keel / "project.yaml").write_text("old")
            ext = keel / "extensions/local.md"
            ext.parent.mkdir()
            ext.write_text("extension\n")
            rc, _, err = run(["init", "--root", d, "--force"])
            self.assertEqual(rc, 0)
            self.assertIn("extensions/ was not touched", err)
            self.assertIn("extends: keel", (keel / "project.yaml").read_text())
            self.assertEqual(ext.read_text(), "extension\n")

    def test_wizard_mode(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, out, _ = run(["init", "--root", d, "--wizard"])
            self.assertEqual(rc, 0)
            written = (Path(d) / ".keel" / "project.yaml").read_text()
            self.assertIn("base_branch: develop", written)
            self.assertIn('merge_window: "09:00-18:00"', written)
            # validates
            vrc, _, _ = run(["validate", str(Path(d) / ".keel" / "project.yaml")])
            self.assertEqual(vrc, 0)


class TestSetup(unittest.TestCase):
    def test_scaffolds_installs_validates_and_plans(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
            rc, out, err = run(["setup", "--root", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel setup", out)
            self.assertIn("detected stack: python", out)
            self.assertIn("validate     : OK", out)
            self.assertIn("plan         :", out)
            self.assertTrue((Path(d) / ".keel/project.yaml").exists())
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_reuses_existing_config_without_force(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".keel/project.yaml"
            target.parent.mkdir()
            text = (
                "extends: keel\ncore_version: '^0.6'\nrepo: existing\nbase_branch: develop\n"
                "knobs:\n  build_gate_cmd: 'true'\n"
            )
            target.write_text(text)
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude"])
            self.assertEqual(rc, 0, err)
            self.assertIn("using existing", out)
            self.assertIn("extensions   : preserved", out)
            self.assertEqual(target.read_text(), text)
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertFalse((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_force_overwrites_config_and_adapters(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".keel/project.yaml"
            target.parent.mkdir()
            target.write_text("old")
            ext = Path(d) / ".keel/extensions/local.md"
            ext.parent.mkdir()
            ext.write_text("extension\n")
            adapter = Path(d) / ".claude/commands/keel/ship.md"
            adapter.parent.mkdir(parents=True)
            adapter.write_text("old")
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude", "--force"])
            self.assertEqual(rc, 0, err)
            self.assertIn("overwrote", out)
            self.assertIn("extensions   : preserved", out)
            self.assertIn(".keel/extensions/ will not be touched", err)
            self.assertIn("extends: keel", target.read_text())
            self.assertEqual(ext.read_text(), "extension\n")
            self.assertIn("keel-generated", adapter.read_text())

    def test_wizard_mode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, out, err = run(["setup", "--root", d, "--wizard"])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel setup wizard", out)
            written = (Path(d) / ".keel/project.yaml").read_text()
            self.assertIn("base_branch: develop", written)
            self.assertIn('merge_window: "09:00-18:00"', written)

    def test_invalid_existing_config_fails_validation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".keel/project.yaml"
            target.parent.mkdir()
            target.write_text("extends: keel\n")
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude"])
            self.assertEqual(rc, 1)
            self.assertIn("using existing", out)
            self.assertIn("validate     : failed", err)


class TestShipHotfix(unittest.TestCase):
    def _cfg(self):
        return _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
            "timezone: Europe/Istanbul\nmerge_window: '07:00-01:30'\n"
            "merge_window_mode: pause\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )

    def test_hotfix_flag_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", self._cfg(), "--root", d, "--hotfix"])
        # decision depends on the wall-clock window, but the flag must be accepted
        self.assertIn("keel ship", out)
        self.assertIn("DECISION", out.upper())


class TestGateRunner(unittest.TestCase):
    def test_command_branch_runs(self):
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "")
        ok, _ = run_gate(GateSpec("build", "command", "test", "block", run="true"))
        self.assertTrue(ok)

    def test_jury_branch_noop_without_diff(self):
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "")  # empty diff -> jury is a fail-soft no-op
        ok, findings = run_gate(GateSpec("jury", "builtin", "test", "block"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_run_gates_with_jury_gate(self):
        import tempfile
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build, jury]\nknobs:\n  build_gate_cmd: 'true'\n")
        with tempfile.TemporaryDirectory() as d:  # non-git root -> empty diff -> jury no-op
            rc, out, _ = run(["run-gates", p, "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("jury", out)


class TestInstallAdapter(unittest.TestCase):
    def test_installs_claude_commands(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "claude", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("/keel:", out)
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())

    def test_installs_skills(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "skills", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("keel-<command>", out)
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_unknown_target(self):
        rc, _, err = run(["install-adapter", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_force_reinstall(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "claude", "--root", d])
            rc, out, _ = run(["install-adapter", "claude", "--root", d, "--force"])
            self.assertEqual(rc, 0)
            self.assertIn("installed", out)

    def test_second_run_skips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "claude", "--root", d])
            rc, out, _ = run(["install-adapter", "claude", "--root", d])  # no --force
            self.assertEqual(rc, 0)
            self.assertIn("skipped", out)

    def test_install_all_surfaces(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("/keel:", out)
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_adapter_status_and_update_adapter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "all", "--root", d])
            ship = Path(d) / ".claude/commands/keel/ship.md"
            ship.unlink()

            rc, out, _ = run(["adapter-status", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("missing", out)
            self.assertIn("ship.md", out)

            rc, out, _ = run(["update-adapter", "all", "--root", d, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn("would-update", out)
            self.assertIn("dry-run: no adapter files were written", out)
            self.assertFalse(ship.exists())

            rc, out, _ = run(["update-adapter", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("updated", out)
            self.assertTrue(ship.exists())

    def test_installs_plugin_command_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "plugin", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("plugin command file(s) written", out)
            self.assertIn("/plugin install keel", out)
            self.assertTrue((Path(d) / "commands" / "ship.md").exists())
            # second run is a no-op (idempotent generator).
            rc, out, _ = run(["install-adapter", "plugin", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("0 plugin command file(s) written", out)

    def test_install_adapter_unknown_target_lists_plugin(self):
        rc, _, err = run(["install-adapter", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)
        self.assertIn("plugin", err)

    def test_adapter_status_unknown_target(self):
        rc, _, err = run(["adapter-status", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_update_adapter_unknown_target(self):
        rc, _, err = run(["update-adapter", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_sync_alias_updates_generated_adapters(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "all", "--root", d])
            ship = Path(d) / ".claude/commands/keel/ship.md"
            ship.unlink()

            rc, out, _ = run(["sync", "--root", d, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn("not upgraded by sync", out)
            self.assertIn("would-update", out)
            self.assertIn("dry-run: no adapter files were written", out)
            self.assertIn("keel validate .keel/project.yaml --root .", out)
            self.assertIn("keel plan .keel/project.yaml --root .", out)
            self.assertFalse(ship.exists())

            rc, out, _ = run(["sync", "--root", d, "--target", "claude"])
            self.assertEqual(rc, 0)
            self.assertIn("updated", out)
            self.assertTrue(ship.exists())

    def test_sync_failure_does_not_print_next_steps(self):
        out, err = io.StringIO(), io.StringIO()
        args = Namespace(target="codex", root=".", dry_run=False)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli._cmd_sync(args)
        self.assertEqual(rc, 1)
        self.assertIn("not upgraded by sync", out.getvalue())
        self.assertNotIn("keel validate", out.getvalue())
        self.assertIn("unknown target", err.getvalue())


class TestInstallLegacyWrappers(unittest.TestCase):
    def test_installs_selected_legacy_wrapper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = run([
                "install-legacy-wrappers",
                "all",
                "--root",
                d,
                "--command",
                "ship=ship",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("legacy wrapper", out)
            self.assertTrue((Path(d) / ".claude/commands/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/source-command-ship/SKILL.md").exists())

    def test_rejects_unknown_legacy_target(self):
        rc, _, err = run(["install-legacy-wrappers", "codex", "--command", "ship=ship"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_legacy_mapping_parser_rejects_malformed_values(self):
        with self.assertRaisesRegex(Exception, "use LEGACY=KEEL"):
            cli._parse_legacy_mapping("ship")
        with self.assertRaisesRegex(Exception, "must be non-empty"):
            cli._parse_legacy_mapping("ship=")

    def test_rejects_non_ready_mapping_from_parity_matrix(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            matrix = Path(d) / "matrix.md"
            matrix.write_text(
                "| Legacy command | Keel command | Status |\n"
                "|---|---|---|\n"
                "| `ship` | `/keel:ship` | `in-progress` |\n",
                encoding="utf-8",
            )
            rc, _, err = run([
                "install-legacy-wrappers",
                "claude",
                "--root",
                d,
                "--parity-matrix",
                str(matrix),
                "--command",
                "ship=ship",
            ])
            self.assertEqual(rc, 1)
            self.assertIn("not parity-ready", err)

    def test_missing_parity_matrix_is_a_blocker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run([
                "install-legacy-wrappers",
                "claude",
                "--root",
                d,
                "--parity-matrix",
                str(Path(d) / "missing.md"),
                "--command",
                "ship=ship",
            ])
            self.assertEqual(rc, 1)
            self.assertIn("parity matrix not found", err)


class TestParser(unittest.TestCase):
    def test_subcommands_present(self):
        parser = cli.build_parser()
        # argparse stores subparser choices on the subparsers action.
        actions = [a for a in parser._actions if a.dest == "command"]
        self.assertTrue(actions)
        self.assertGreaterEqual(set(actions[0].choices),
                                {"version", "validate", "plan", "run-gates", "window", "ship",
                                 "ship-v2", "implement", "ci-check", "morning", "capabilities",
                                 "wrap", "overnight", "init",
                                 "setup",
                                 "install-adapter",
                                 "adapter-status", "update-adapter", "sync", "project-commands",
                                 "install-legacy-wrappers"})


if __name__ == "__main__":
    unittest.main()
