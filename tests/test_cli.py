"""Unit tests for the keel CLI."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from keel import cli, install, ledger, runtime, ship, stepverifier
from keel.runner import CommandResult


def _ok_result(output):
    return CommandResult(True, 0, output)


def _fail_result(output):
    return CommandResult(False, 1, output)

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent


def _trusted_comment(body):
    return {"body": body, "author_association": "MEMBER"}


def _write_json_fixture(path, value):
    Path(_write_raw(json.dumps(value))).replace(path)


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
        self.assertEqual(data["contract"]["capture"]["schema_version"],
                         "keel.capture.v1")
        self.assertTrue(data["contract"]["capture"]["fail_soft"]["enabled"])
        self.assertEqual(data["contract"]["run_ledger"]["schema_version"],
                         "keel.run-ledger.v1")
        self.assertEqual(data["contract"]["checkpoint"]["schema_version"],
                         "keel.checkpoint.v1")
        self.assertEqual(data["contract"]["evidence"]["schema_version"], "keel.evidence.v1")
        self.assertIn("pull_request_body", data["contract"]["evidence"]["not_accepted"])

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

    def test_plan_json_exposes_evidence_requirements_from_review_flags(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "ship", "--reviewers", "2", "--jury", "--json"]
        )
        self.assertEqual(rc, 0)
        evidence_contract = json.loads(out)["contract"]["evidence"]
        ids = [item["id"] for item in evidence_contract["required"]]
        self.assertEqual(ids, [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
            "jury-verdict",
        ])

    def test_plan_json_includes_issue_intake(self):
        body = (
            "## Problem\nAgents need issue readiness.\n\n"
            "## Deliverable\nExpose a structured intake record.\n\n"
            "## Acceptance criteria\n"
            "- Dry-run JSON includes readiness.\n"
        )
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "ship", "--issue-title", "Add intake", "--issue-body", body,
             "--issue-label", "enhancement,workflow", "--json"]
        )
        self.assertEqual(rc, 0)
        intake = json.loads(out)["contract"]["issue_intake"]
        self.assertEqual(intake["status"], "ready")
        self.assertTrue(intake["provided"])
        self.assertEqual(intake["ledger_record"]["readiness"], "ready")

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


def _write_config_with_ledger(
    build_cmd,
    ledger_path="state/runs.jsonl",
    extra_policy_pack_lines: list[str] | None = None,
):
    extra = "\n".join(extra_policy_pack_lines or [])
    if extra:
        extra = f"\n{extra}\n"
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        f"    run_ledger: {ledger_path!r}\n"
        f"{extra}"
    )


def _write_config_with_checkpoint(build_cmd, checkpoint_path="state/checkpoint.json"):
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        f"    checkpoint: {checkpoint_path!r}\n"
    )


def _write_config_with_state_paths(
    build_cmd,
    ledger_path="state/runs.jsonl",
    checkpoint_path="state/checkpoint.json",
):
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        f"    run_ledger: {ledger_path!r}\n"
        f"    checkpoint: {checkpoint_path!r}\n"
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


class TestStatePathErrors(unittest.TestCase):
    def assertFriendlyStateError(self, argv, expected):
        rc, out, err = run(argv)
        self.assertEqual(rc, 1)
        self.assertIn(expected, err)
        self.assertNotIn("Traceback", out + err)

    def test_plan_validates_configured_state_paths(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad_ledger = _write_config_with_state_paths(
                "'true'",
                ledger_path="/tmp/runs.jsonl",
            )
            self.assertFriendlyStateError(
                ["plan", bad_ledger, "--root", d, "--json"],
                "invalid ledger path: run ledger path must be relative",
            )

            bad_checkpoint = _write_config_with_state_paths(
                "'true'",
                checkpoint_path="/tmp/checkpoint.json",
            )
            self.assertFriendlyStateError(
                ["plan", bad_checkpoint, "--root", d, "--json"],
                "invalid checkpoint path: checkpoint path must be relative",
            )

    def test_ledger_backed_commands_report_invalid_paths_without_traceback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths(
                "'true'",
                ledger_path="/tmp/runs.jsonl",
            )
            cases = [
                ["ledger", config, "--root", d],
                ["capture-verify", config, "--root", d, "--merged-pr", "1"],
                ["capture-reconcile", config, "--root", d, "--merged-pr", "1"],
                ["status", config, "--root", d],
                [
                    "ship", config, "--root", d, "--dry-run", "--json",
                    "--capture-status", "applied",
                ],
            ]
            for argv in cases:
                with self.subTest(command=argv[0]):
                    self.assertFriendlyStateError(
                        argv,
                        "invalid ledger path: run ledger path must be relative",
                    )

    def test_checkpoint_backed_commands_report_invalid_paths_without_traceback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths(
                "'true'",
                checkpoint_path="/tmp/checkpoint.json",
            )
            cases = [
                ["checkpoint", config, "--root", d],
                ["resume", config, "--root", d],
                ["status", config, "--root", d],
            ]
            for argv in cases:
                with self.subTest(command=argv[0]):
                    self.assertFriendlyStateError(
                        argv,
                        "invalid checkpoint path: checkpoint path must be relative",
                    )


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
        self.assertFalse(data["result"]["run_ledger"]["appended"])
        self.assertEqual(data["result"]["run_ledger"]["record"]["record_type"], "ship_run")
        self.assertEqual(data["result"]["assessment"]["merge"]["action"], "merge")
        self.assertEqual(data["result"]["issue_intake"]["status"], "needs-input")
        review = data["result"]["assessment"]["review_merge_contract"]
        self.assertEqual(review["reviewers"]["count"], 1)
        self.assertEqual(review["posting"]["mode"], "summary")
        self.assertEqual(review["jury"]["mode"], "off")

    def test_ship_live_blocks_non_ready_issue_before_gates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'false'"), "--root", d,
                              "--live", "--json", "--issue-title", "Ambiguous work",
                              "--issue-body", "Maybe improve this later.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("contract", data)
        self.assertNotIn("result", data)
        intake = data["contract"]["issue_intake"]
        self.assertEqual(intake["status"], "needs-input")
        self.assertFalse(intake["can_mutate_code"])
        self.assertTrue(intake["questions"])

    def test_ship_live_human_blocks_non_ready_issue_before_gates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_config("'false'"), "--root", d,
                              "--live", "--issue-title", "Ambiguous work",
                              "--issue-body", "Maybe improve this later.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        self.assertIn("issue intake: needs-input", err)
        self.assertIn("question:", err)

    def test_ship_human_ready_issue_omits_question_count(self):
        import tempfile
        body = (
            "## Problem\nAgents need issue readiness.\n\n"
            "## Deliverable\nExpose intake status.\n\n"
            "## Acceptance criteria\n"
            "- Human output shows readiness.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--issue-title", "Add intake", "--issue-body", body])
        self.assertEqual(rc, 0)
        self.assertIn("intake        : ready", out)
        self.assertNotIn("questions", out)

    def test_ship_compound_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--compound", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ship")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")
        self.assertEqual(data["contract"]["workflow_profile"]["inherits"], "ship")
        self.assertEqual(
            data["contract"]["workflow_profile"]["step_overrides"]["s7"]["step"],
            "review",
        )
        self.assertIn("review_merge_contract", data["contract"])
        self.assertIn("result", data)
        # The graph marks the four overridden backbone steps as compound.
        compound_steps = {
            row["step_id"]
            for row in data["contract"]["graph"]
            if row["profile_step"] == "compound"
        }
        self.assertEqual(compound_steps, {"s4", "s7", "s9", "s11"})

    def test_ship_profile_compound_alias_matches_compound_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--profile", "compound", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")

    def test_ship_default_profile_is_standard(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "standard")
        self.assertTrue(all(
            row["profile_step"] == "standard"
            for row in data["contract"]["graph"]
        ))

    def test_ship_compound_composes_with_jury(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--compound", "--jury", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")
        self.assertTrue(data["contract"]["review_merge_contract"]["jury"]["enabled"])

    def test_ship_v2_is_not_a_subcommand(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            cli.main(["ship-v2", "x.yaml", "--dry-run", "--json"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("invalid choice: 'ship-v2'", err.getvalue())

    def test_plan_compound_profile_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["plan", _write_config("'true'"), "--root", d,
                              "--command", "ship", "--profile", "compound", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")

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

    def test_ship_live_can_append_structured_ledger_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--run-id", "RUN-140",
                              "--issue", "140", "--pull-request", "160",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--implementer", "codex:gpt-5",
                              "--reviewer-agent", "reviewer-a:gpt-5",
                              "--reviewer-agent", "reviewer-b:claude",
                              "--tester", "tester:gpt-5-mini",
                              "--host-agent", "claude",
                              "--transport", "mcp",
                              "--profile", "compound",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            ledger_path = Path(d) / "state" / "runs.jsonl"
            rc_read, out_read, _ = run(["ledger", config, "--root", d, "--json"])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["result"]["run_ledger"]["appended"])
        self.assertEqual(data["result"]["run_ledger"]["path"], str(ledger_path.resolve()))
        self.assertEqual(data["result"]["run_ledger"]["record"]["issue"]["number"], 140)
        self.assertEqual(rc_read, 0)
        read = json.loads(out_read)
        self.assertEqual(read["record_count"], 1)
        self.assertEqual(read["records"][0]["run_id"], "RUN-140")
        self.assertEqual(read["records"][0]["capture"]["status"], "skipped")
        self.assertEqual(read["records"][0]["capture"]["marker_reason"], "no-policy")
        self.assertEqual(read["records"][0]["capture"]["marker"],
                         "compound-learning: pr=160 status=skipped:no-policy")
        self.assertEqual(read["records"][0]["actors"]["implementer"], "codex:gpt-5")
        self.assertEqual(read["records"][0]["actors"]["reviewers"],
                         ["reviewer-a:gpt-5", "reviewer-b:claude"])
        run_context = read["records"][0]["run_context"]
        self.assertEqual(run_context["host_agent"], "claude")
        self.assertEqual(run_context["transport"], "mcp")
        self.assertEqual(run_context["profile"], "compound")
        # jury_mode is derived from the resolved review contract.
        self.assertIn(run_context["jury_mode"], ("off", "advisory", "gating"))
        # consent is derived from the resolved operator-consent contract.
        self.assertEqual(run_context["consent"]["status"], "approved")
        self.assertEqual(read["records"][0]["redaction"]["status"], "applied")

    def test_ship_ledger_transport_defaults_to_resolved_transport(self):
        # With no --transport flag, the record carries the resolved transport.
        # Patch capability detection so `gh` resolves deterministically offline.
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", True, "ok", "test"),
            runtime.Capability("gh-auth", True, "ok", "test"),
            runtime.Capability("github-mcp", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--run-id", "RUN-141",
                              "--issue", "141", "--pull-request", "161",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            rc_read, out_read, _ = run(["ledger", config, "--root", d, "--json"])

        data = json.loads(out)
        self.assertTrue(data["result"]["run_ledger"]["appended"])
        self.assertEqual(rc_read, 0)
        run_context = json.loads(out_read)["records"][0]["run_context"]
        self.assertEqual(run_context["transport"], "gh")
        self.assertIsNone(run_context["host_agent"])
        self.assertEqual(run_context["profile"], "standard")
        self.assertEqual(
            data["result"]["run_ledger"]["warnings"],
            ["missing host_agent in live run context"],
        )

    def test_ship_live_append_strict_run_context_blocks_missing_host_agent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--run-id", "RUN-264",
                              "--issue", "264", "--pull-request", "275",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--strict-run-context",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            ledger_path = Path(d) / "state" / "runs.jsonl"

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("missing host_agent", data["error"])
        self.assertFalse(ledger_path.exists())

    def test_ship_live_append_strict_run_context_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live",
                              "--append-ledger", "--run-id", "RUN-264-H",
                              "--issue", "264", "--pull-request", "275",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--strict-run-context",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])

        self.assertEqual(rc, 1)
        self.assertIn("missing host_agent", err)

    def test_ship_human_append_warns_on_missing_host_agent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live",
                              "--append-ledger", "--run-id", "RUN-265",
                              "--issue", "265", "--pull-request", "276",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])

        self.assertEqual(rc, 0)
        self.assertIn("run context   : warning: missing host_agent", out)

    def test_ship_json_uses_learning_policy_in_ledger_record(self):
        import tempfile
        body = (
            "## Problem\nLearning policy must affect ship records.\n\n"
            "## Deliverable\nEmit the configured learning decision.\n\n"
            "## Acceptance criteria\n"
            "- JSON contains create-learning.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture:",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                    "      reason: new invariant",
                ],
            )
            rc, out, _ = run([
                "ship", config, "--root", d, "--dry-run", "--json",
                "--issue-title", "Release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "181",
                "--capture-status", "applied",
            ])

        self.assertEqual(rc, 0)
        learning = json.loads(out)["result"]["run_ledger"]["record"]["capture"]["learning"]
        self.assertEqual(learning["decision"], "create-learning")
        self.assertEqual(learning["reason"], "new invariant")
        self.assertTrue(learning["durable_artifact"])

    def test_ship_json_uses_existing_ledger_and_issue_context_for_learning_dedupe(self):
        import tempfile
        body = (
            "## Problem\nLearning dedupe needs issue context.\n\n"
            "## Deliverable\nUse title and labels in the fingerprint.\n\n"
            "## Acceptance criteria\n"
            "- Same title and labels duplicate.\n"
            "- Different title does not duplicate.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture:",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                ],
            )
            first = [
                "ship", config, "--root", d, "--live", "--append-ledger", "--json",
                "--run-id", "RUN-FIRST",
                "--issue-title", "Release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "181",
                "--capture-status", "applied",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester",
            ]
            rc_first, _, _ = run(first)
            second = [
                "ship", config, "--root", d, "--dry-run", "--json",
                "--issue-title", "Release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "182",
                "--capture-status", "applied",
            ]
            rc_second, out_second, _ = run(second)
            different_title = [
                "ship", config, "--root", d, "--dry-run", "--json",
                "--issue-title", "Different release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "183",
                "--capture-status", "applied",
            ]
            rc_third, out_third, _ = run(different_title)

        self.assertEqual(rc_first, 0)
        self.assertEqual(rc_second, 0)
        duplicate = json.loads(out_second)["result"]["run_ledger"]["record"]["capture"]["learning"]
        self.assertEqual(duplicate["decision"], "duplicate")
        self.assertEqual(duplicate["duplicate_of"], "RUN-FIRST")
        self.assertEqual(rc_third, 0)
        not_duplicate = json.loads(out_third)["result"]["run_ledger"]["record"]["capture"][
            "learning"
        ]
        self.assertEqual(not_duplicate["decision"], "create-learning")
        self.assertNotIn("duplicate_of", not_duplicate)

    def test_ship_json_blocks_on_malformed_existing_ledger_before_record_build(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            ledger_path = Path(d) / "state" / "runs.jsonl"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_text("{", encoding="utf-8")
            rc, _, err = run([
                "ship", config, "--root", d, "--dry-run", "--json",
                "--capture-status", "applied",
            ])

        self.assertEqual(rc, 1)
        self.assertIn("invalid ledger", err)

    def test_ship_live_append_redacts_capture_record_before_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: private-host",
                    "        pattern: 'internal\\.example\\.test'",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--capture-status", "skipped",
                              "--capture-reason",
                              "Bearer abcdefghijklmnopqrstuvwxyz at internal.example.test",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            rc_read, out_read, _ = run(["ledger", config, "--root", d, "--json"])

        self.assertEqual(rc, 0)
        self.assertEqual(rc_read, 0)
        written = json.loads(out_read)["records"][0]
        serialized = json.dumps(written, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("internal.example.test", serialized)
        self.assertEqual(written["redaction"]["redaction_count"], 2)
        self.assertEqual(json.loads(out)["result"]["run_ledger"]["record"], written)

    def test_ship_live_append_skips_write_when_redaction_policy_invalid(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--capture-status", "skipped",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            ledger_path = Path(d) / "state" / "runs.jsonl"

        self.assertEqual(rc, 1)
        self.assertIn("capture redaction policy invalid", json.loads(out)["error"])
        self.assertFalse(ledger_path.exists())

    def test_ship_live_append_reports_invalid_redaction_policy_in_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, _, err = run(["ship", config, "--root", d, "--live",
                              "--append-ledger", "--capture-status", "skipped",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])

        self.assertEqual(rc, 1)
        self.assertIn("capture redaction policy invalid", err)

    def test_ship_json_invalid_redaction_policy_uses_default_redaction_for_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--dry-run", "--json",
                              "--capture-status", "skipped",
                              "--capture-reason",
                              "Bearer abcdefghijklmnopqrstuvwxyz should not leak"])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        record = data["result"]["run_ledger"]["record"]
        serialized = json.dumps(record, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertEqual(record["redaction"]["status"], "partial")
        self.assertEqual(record["redaction"]["reason"], "invalid-policy")
        self.assertEqual(record["redaction"]["redaction_count"], 1)

    def test_ship_json_invalid_redaction_policy_keeps_valid_project_redactions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: private-host",
                    "        pattern: 'internal\\.example\\.test'",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--dry-run", "--json",
                              "--capture-status", "skipped",
                              "--capture-reason",
                              "Bearer abcdefghijklmnopqrstuvwxyz at internal.example.test"])

        self.assertEqual(rc, 0)
        record = json.loads(out)["result"]["run_ledger"]["record"]
        serialized = json.dumps(record, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("internal.example.test", serialized)
        self.assertEqual(record["redaction"]["status"], "partial")
        self.assertEqual(record["redaction"]["redaction_count"], 2)

    def test_ship_live_append_requires_capture_status(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live", "--json",
                              "--append-ledger",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["error"],
                         "--capture-status is required when --live --append-ledger is used")

    def test_ship_live_append_requires_capture_status_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live", "--append-ledger",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        self.assertIn("--capture-status is required", err)

    def test_ship_rejects_invalid_capture_status_before_command_runs(self):
        parser = cli.build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args([
                "ship",
                _write_config_with_ledger("'true'"),
                "--capture-status",
                "skipped:not-allowed",
            ])

    def test_ledger_reads_missing_file_as_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ledger", _write_config_with_ledger("'true'"),
                              "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], "missing")
        self.assertEqual(data["records"], [])
        self.assertEqual(data["capture_health"]["status"], "clean")

    def test_ledger_human_output_and_limit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            for run_id in ("RUN-1", "RUN-2"):
                rc, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                "--run-id", run_id,
                                "--pull-request", "160",
                                "--capture-status", "skipped",
                                "--approve-scope", "filesystem,git,github",
                                "--operator", "tester"])
                self.assertEqual(rc, 0)
            rc, out, _ = run(["ledger", config, "--root", d, "--limit", "1"])
            rc_json, out_json, _ = run(["ledger", config, "--root", d,
                                        "--limit", "1", "--json"])

        self.assertEqual(rc, 0)
        self.assertIn("keel ledger", out)
        self.assertIn("records       : 1", out)
        self.assertIn("capture       : clean", out)
        self.assertIn("capture gaps  : 0", out)
        self.assertEqual(rc_json, 0)
        read = json.loads(out_json)
        self.assertEqual(read["records"][0]["run_id"], "RUN-2")
        self.assertEqual(read["capture_health"]["counts"]["skipped"], 1)

    def test_capture_verify_reports_complete_from_ledger_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "skipped",
                                 "--capture-reason", "no capture hook configured",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160", "--json"])

        self.assertEqual(rc_ship, 0)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "complete")
        self.assertEqual(data["verification"]["results"][0]["marker"],
                         "compound-learning: pr=160 status=skipped:no-policy")

    def test_capture_verify_reports_missing_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160", "--json"])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "incomplete")
        self.assertEqual(data["verification"]["results"][0]["status"], "missing")

    def test_capture_verify_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "applied",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160"])

        self.assertEqual(rc_ship, 0)
        self.assertEqual(rc, 0)
        self.assertIn("keel capture-verify", out)
        self.assertIn("ok  PR #160", out)

    def test_capture_verify_reports_config_and_ledger_errors(self):
        import tempfile
        rc_missing, _, err_missing = run(["capture-verify", "/no/such.yaml",
                                          "--merged-pr", "160"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["capture-verify", _write_raw("extends: keel\n"),
                                          "--merged-pr", "160"])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad, _, err_bad = run(["capture-verify", config, "--root", d,
                                      "--merged-pr", "160"])

        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid ledger", err_bad)

    def test_capture_verify_requires_a_merged_pr_source(self):
        config = _write_config_with_ledger("'true'")
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["capture-verify", config, "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("provide --merged-pr or --from-transport", err)

    def _ship_applied(self, config, root, pr, *, artifact=None):
        argv = ["ship", config, "--root", root, "--live", "--append-ledger",
                "--pull-request", str(pr),
                "--capture-status", "applied",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester"]
        if artifact is not None:
            argv += ["--capture-artifact", artifact]
        rc, _, _ = run(argv)
        self.assertEqual(rc, 0)

    def test_capture_verify_back_compat_merged_pr_path_passes(self):
        # Legacy offline path: applied capture, no artifact, only --merged-pr.
        # Reconcile is not activated, so the historic pass/fail is preserved.
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160)
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["merged_pr_source"]["source"], "args")
        self.assertNotIn("reconcile", data)

    def test_capture_verify_transport_derivation_catches_omitted_pr(self):
        # The agent only passes PR 160, but the transport says 160 AND 161 merged.
        # 161 has no capture marker, so derivation surfaces the omission.
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs", return_value=_ok_result(
                    json.dumps([{"number": 160}, {"number": 161}]))):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["merged_pr_source"]["source"], "transport")
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertIn("missing-marker", types)
        self.assertIn(161, [f["pr"] for f in data["reconcile"]["findings"]])

    def test_capture_verify_applied_without_artifact_fails_under_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160)  # no artifact
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture), "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["merged_pr_source"]["source"], "transport-fixture")
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertEqual(types, ["applied-without-artifact"])

    def test_capture_verify_applied_with_artifact_passes_under_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            fixture = Path(d) / "merged.json"
            # A junk entry (no/invalid number) is ignored by the fixture reader.
            _write_json_fixture(fixture, [{"number": 160}, {"note": "no number"},
                                          {"number": 0}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture)])
        self.assertEqual(rc, 0)
        self.assertIn("reconcile: ok", out)

    def test_capture_verify_deferred_without_artifact_ok_under_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "deferred",
                                 "--capture-reason", "queued for later",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            self.assertEqual(rc_ship, 0)
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["reconcile"]["ok"])

    def test_capture_verify_reviewer_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "applied",
                                 "--capture-artifact", "artifacts/160.md",
                                 "--reviewer-agent", "agent-a",
                                 "--reviewer-agent", "agent-b",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            self.assertEqual(rc_ship, 0)
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture),
                              "--verdict-count", "160=1", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertEqual(types, ["reviewer-count-mismatch"])

    def test_capture_verify_transport_failure_is_fail_soft(self):
        # Transport query fails; --merged-pr still provides the set.
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_fail_result("gh offline")):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged_pr_source"]["transport_failed"])

    def test_capture_verify_transport_empty_with_no_override_errors(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            with patch.object(cli.github, "merged_prs",
                              return_value=_ok_result("[]")):
                rc, _, err = run(["capture-verify", config, "--root", d, "--from-transport"])
        self.assertEqual(rc, 1)
        self.assertIn("no merged PRs derived", err)

    def test_capture_verify_transport_bad_json_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_ok_result("not json")):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged_pr_source"]["transport_failed"])

    def test_capture_verify_transport_non_list_json_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_ok_result("{}")):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged_pr_source"]["transport_failed"])

    def test_capture_verify_merged_since_narrows_search(self):
        captured = {}

        def fake_merged_prs(*, search=None, cwd=None):
            captured["search"] = search
            return _ok_result(json.dumps([{"number": 160}]))

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs", side_effect=fake_merged_prs):
                rc, _, _ = run(["capture-verify", config, "--root", d,
                                "--from-transport", "--merged-since", "2026-06-01"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["search"], "merged:>=2026-06-01")

    def test_capture_verify_human_output_shows_reconcile_findings(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160)  # no artifact
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture)])
        self.assertEqual(rc, 1)
        self.assertIn("merged-PR source: transport-fixture", out)
        self.assertIn("reconcile PR #160", out)
        self.assertIn("applied-without-artifact", out)

    def test_capture_verify_bad_merged_prs_json_errors(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            fixture = Path(d) / "merged.json"
            fixture.write_text("{}", encoding="utf-8")
            rc, _, err = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture)])
        self.assertEqual(rc, 1)
        self.assertIn("must contain a JSON array", err)

    def _config_owner_repo_ledger(self):
        return _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "owner: acme\nrepo: tmp\ngates: [build]\n"
            "knobs:\n  build_gate_cmd: 'true'\n"
            "policy_pack:\n  name: tmp\n  reports:\n"
            "    run_ledger: 'state/runs.jsonl'\n"
        )

    def _write_ledger_record(self, root, pr, *, reviewers):
        record = {
            "schema_version": "keel.run-ledger.v1",
            "record_type": "ship_run",
            "pull_request": {"number": pr},
            "capture": {
                "marker": f"compound-learning: pr={pr} status=applied",
                "artifact": f"artifacts/{pr}.md",
            },
            "actors": {"reviewers": reviewers},
        }
        path = Path(root) / "state" / "runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    def test_capture_verify_live_verdict_fetch_detects_mismatch(self):
        def fake_run(argv, **kwargs):
            endpoint = argv[-1]
            if endpoint.endswith("/issues/160/comments"):
                return CommandResult(True, 0, json.dumps([[
                    {"body": "keel.review-verdict.v1\nreviewer: a\nLGTM",
                     "author_association": "MEMBER"},
                ]]))
            if endpoint.endswith("/pulls/160/reviews"):
                return CommandResult(True, 0, json.dumps([[]]))
            return CommandResult(False, 1, "unexpected endpoint")

        with tempfile.TemporaryDirectory() as d:
            config = self._config_owner_repo_ledger()
            self._write_ledger_record(d, 160, reviewers=["agent-a", "agent-b"])
            with patch.object(cli.github, "merged_prs",
                              return_value=_ok_result(json.dumps([{"number": 160}]))), \
                    patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertEqual(types, ["reviewer-count-mismatch"])
        self.assertEqual(data["reconcile"]["results"][0]["posted_verdicts"], 1)

    def test_capture_verify_live_verdict_fetch_failure_is_advisory(self):
        def fake_run(argv, **kwargs):
            return CommandResult(False, 1, "gh offline")

        with tempfile.TemporaryDirectory() as d:
            config = self._config_owner_repo_ledger()
            self._write_ledger_record(d, 160, reviewers=["agent-a", "agent-b"])
            with patch.object(cli.github, "merged_prs",
                              return_value=_ok_result(json.dumps([{"number": 160}]))), \
                    patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--from-transport", "--json"])
        # Verdict fetch failed for PR 160, so the reviewer cross-check is skipped.
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsNone(data["reconcile"]["results"][0]["posted_verdicts"])

    def test_verdict_count_arg_rejects_bad_values(self):
        config = _write_config_with_ledger("'true'")
        with tempfile.TemporaryDirectory() as d:
            for bad in ("noequals", "x=1", "1=y", "0=1", "1=-1"):
                with self.assertRaises(SystemExit) as ctx:
                    run(["capture-verify", config, "--root", d,
                         "--merged-pr", "1", "--verdict-count", bad])
                self.assertEqual(ctx.exception.code, 2, bad)

    def test_evidence_verify_passes_from_offline_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nReviewer A LGTM"),
                _trusted_comment("keel.jury-verdict.v1\nAI Jury LGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            _write_json_fixture(reviews, [
                _trusted_comment("keel.review-verdict.v1\nReviewer B LGTM"),
            ])
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:codex",
                "--reviewers", "2",
                "--jury",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["issue"], 212)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["counts"]["review_verdict"], 2)

    def test_evidence_verify_missing_attribution_label_fails_when_gated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertTrue(any(
            f["id"] == "attribution-label"
            for f in data["verification"]["findings"]
        ))

    def _run_distinct_vendor_verify(self, *, flag, vendor_b):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: a\nvendor: claude\nLGTM"
                ),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            _write_json_fixture(reviews, [
                _trusted_comment(
                    f"keel.review-verdict.v1\nreviewer: b\nvendor: {vendor_b}\nLGTM"
                ),
            ])
            argv = [
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:claude",
                "--reviewers", "2",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--json",
            ]
            if flag:
                argv.append("--require-distinct-vendors")
            return run(argv)

    def test_evidence_verify_require_distinct_vendors_flag_blocks_duplicates(self):
        rc, out, _ = self._run_distinct_vendor_verify(flag=True, vendor_b="claude")
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertTrue(
            any(f["id"] == "review-vendor-distinctness"
                for f in data["verification"]["findings"])
        )

    def test_evidence_verify_require_distinct_vendors_flag_passes_distinct(self):
        rc, out, _ = self._run_distinct_vendor_verify(flag=True, vendor_b="codex")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_duplicate_vendors_pass_without_flag(self):
        rc, out, _ = self._run_distinct_vendor_verify(flag=False, vendor_b="claude")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_enforces_closure_fidelity_against_ledger(self):
        from keel import closure, ledger

        record = {
            "schema_version": "keel.run-ledger.v1",
            "record_type": "ship_run",
            "target": "issue #212",
            "actors": {"implementer": "codex", "reviewers": [], "tester": "codex"},
            "pull_request": {"number": 300},
            "changes": {"file_count": 1, "files": ["src/keel/evidence.py"]},
            "capture": {"status": "applied"},
            "run_id": "RUN-212",
            "run_context": {
                "host_agent": "codex", "transport": "gh", "profile": "standard",
                "jury_mode": "off",
                "consent": {"status": "approved", "scopes": ["git"]},
            },
        }
        canonical = closure.render_closure_comment(record)
        tampered = canonical.replace("codex", "intruder")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            ledger_jsonl = root / "ledger.jsonl"
            ledger_jsonl.write_text(ledger.encode_record(record), encoding="utf-8")
            _write_json_fixture(pr_comments, [_trusted_comment(tampered)])
            _write_json_fixture(issue_comments, [_trusted_comment(canonical)])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            args = [
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:codex",
                "--reviewers", "1",
                "--deferral", "review",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--ledger-jsonl", str(ledger_jsonl),
                "--json",
            ]
            rc_fail, out_fail, _ = run(args)
            # The matching canonical body on the PR passes (fidelity satisfied).
            _write_json_fixture(pr_comments, [_trusted_comment(canonical)])
            rc_ok, out_ok, _ = run(args)

        data_fail = json.loads(out_fail)
        self.assertEqual(rc_fail, 1)
        self.assertEqual(data_fail["verification"]["status"], "fail")
        self.assertIn("closure-comment-pr", data_fail["verification"]["missing"])
        pr_result = next(
            item for item in data_fail["verification"]["results"]
            if item["id"] == "closure-comment-pr"
        )
        self.assertEqual(
            pr_result["reason"],
            "closure comment does not match the ship_run ledger record",
        )
        data_ok = json.loads(out_ok)
        self.assertEqual(rc_ok, 0)
        self.assertEqual(data_ok["verification"]["status"], "pass")

    def test_evidence_verify_invalid_ledger_jsonl_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bad = root / "ledger.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--pr-comments-json", _write_raw("[]"),
                "--issue-comments-json", _write_raw("[]"),
                "--pr-reviews-json", _write_raw("[]"),
                "--pr-body-file", _write_raw("Closes #212"),
                "--ledger-jsonl", str(bad),
                "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", err)

    def test_evidence_verify_rejects_body_and_assessment_as_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            pr_comments.write_text(json.dumps([
                {"body": "### \U0001f6a2 keel ship\nReviewer verdict LGTM"},
            ]), encoding="utf-8")
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            body.write_text(
                "Closes #212\n<!-- keel.closure-comment.v1 -->\n"
                "keel.review-verdict.v1\nLGTM",
                encoding="utf-8",
            )
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertEqual(data["verification"]["counts"]["review_verdict"], 0)
        self.assertEqual(data["verification"]["missing"], [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
        ])

    def test_evidence_verify_ship_assessment_arms_gate_without_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                {
                    "author_association": "NONE",
                    "user": {"login": "github-actions[bot]"},
                    "body": "### \U0001f6a2 keel ship\nstatus: pass",
                },
            ])
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #322", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "324",
                "--reviewers", "2",
                "--head-ref", "issue-322-reveal",
                "--changed-file", "website/index.html",
                "--changed-file", "website/site.webmanifest",
                "--changed-file", "website/workspace.css",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["gate"]["reason"], "ship-assessment-comment")
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertEqual(data["verification"]["missing"], [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
        ])

    def test_evidence_verify_human_output_and_dry_run(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "keel:ship",
            "--dry-run",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("keel evidence-verify", out)
        self.assertIn("required      : 0", out)

    def test_evidence_verify_enforces_from_ship_branch_without_label(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--reviewers", "1",
                "--pr-label", "agent:claude",
                "--head-ref", "fix/issue-266-evidence-arming",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["gate"]["reason"], "ship-branch")
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_waiver_label_is_the_disarm_path(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "fix/issue-266-evidence-arming",
            "--pr-label", "keel:evidence-waived",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--json",
        ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["enforced"])
        self.assertTrue(data["gate"]["waived"])
        self.assertEqual(data["gate"]["reason"], "operator-waiver-label")
        self.assertEqual(data["verification"]["required_count"], 0)

    def test_evidence_verify_waiver_human_output_reports_note(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "fix/issue-266-evidence-arming",
            "--pr-label", "keel:evidence-waived",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false (operator-waiver-label)", out)
        self.assertIn("required      : 0", out)
        self.assertIn("note          : evidence gate disarmed by operator waiver label", out)

    def test_evidence_verify_hand_authored_pr_without_provenance_is_ungated(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "docs/readme-polish",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false (no-ship-provenance)", out)
        self.assertIn("required      : 0", out)
        self.assertIn("note          : evidence gate not enforced", out)

    def test_evidence_verify_fixture_keeps_explicit_issue(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--issue", "99",
            "--dry-run",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--pr-body-file", _write_raw("Closes #212"),
            "--json",
        ])

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["issue"], 99)

    def test_evidence_verify_fixture_uses_explicit_issue_without_body_infer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: b\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--issue", "99",
                "--reviewers", "2",
                "--pr-label", "agent:claude",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["issue"], 99)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_human_output_lists_missing_and_deferrals(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--deferral", "review",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
            ])

        self.assertEqual(rc, 1)
        self.assertIn("missing       : closure-comment-issue", out)
        self.assertIn("review-verdict-1 (deferred)", out)

    def test_evidence_verify_reports_config_and_artifact_errors(self):
        rc_missing, _, err_missing = run([
            "evidence-verify", "/no/such.yaml", "--pr", "1", "--json",
        ])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run([
            "evidence-verify", _write_raw("extends: keel\n"), "--pr", "1", "--json",
        ])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            bad_json = Path(d) / "bad.json"
            bad_json.write_text("{}", encoding="utf-8")
            rc_bad, _, err_bad = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--pr", "1",
                "--pr-comments-json", str(bad_json),
            ])
        self.assertEqual(rc_bad, 1)
        self.assertIn("must contain a JSON array of objects", err_bad)

        rc_repo, _, err_repo = run([
            "evidence-verify", _write_config("'true'"), "--pr", "1",
        ])
        self.assertEqual(rc_repo, 1)
        self.assertIn("project config must define owner and repo", err_repo)

    def test_evidence_verify_live_fetch_uses_gh_artifacts(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output=json.dumps({
                    "body": "Closes #212",
                    "head": {"sha": "abc123", "ref": "fix/issue-266-evidence-arming"},
                    "labels": [{"name": "keel:ship"}, {"name": "agent:claude"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return Namespace(ok=True, output=json.dumps([
                    [{"filename": "src/keel/evidence.py"}],
                ]))
            if endpoint.endswith("/issues/300/comments"):
                return Namespace(ok=True, output=json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                    _trusted_comment("keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"),
                ]))
            if endpoint.endswith("/pulls/300/reviews"):
                return Namespace(ok=True, output=json.dumps([
                    {
                        "body": "keel.review-verdict.v1\nreviewer: b\nLGTM",
                        "commit_id": "abc123",
                        "author_association": "MEMBER",
                    },
                ]))
            if endpoint.endswith("/issues/212/comments"):
                return Namespace(ok=True, output=json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                ]))
            return Namespace(ok=False, output="unexpected endpoint")

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--reviewers", "2",
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertTrue(data["enforced"])
        self.assertEqual(data["pr_labels"], ["keel:ship", "agent:claude"])
        self.assertEqual(data["head_sha"], "abc123")
        self.assertEqual(data["changed_files"], ["src/keel/evidence.py"])
        self.assertTrue(any(argv[:3] == ["gh", "api", "--paginate"] for argv in calls))

    def test_evidence_verify_live_fetch_derives_tier3_requirements(self):
        def fake_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output=json.dumps({
                    "body": "Closes #212",
                    "head": {"sha": "abc123"},
                    "labels": [{"name": "keel:ship"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return Namespace(ok=True, output=json.dumps([
                    [{"filename": ".github/workflows/keel-ship.yml"}],
                ]))
            if endpoint.endswith("/issues/300/comments"):
                return Namespace(ok=True, output=json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                    _trusted_comment("keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"),
                    _trusted_comment("keel.review-verdict.v1\nreviewer: b\nhead: abc123\nLGTM"),
                ]))
            if endpoint.endswith("/pulls/300/reviews"):
                return Namespace(ok=True, output="[]")
            if endpoint.endswith("/issues/212/comments"):
                return Namespace(ok=True, output=json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                ]))
            return Namespace(ok=False, output="unexpected endpoint")

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["changed_files"], [".github/workflows/keel-ship.yml"])
        self.assertEqual(data["verification"]["missing"], [
            "review-verdict-3",
            "jury-verdict",
        ])

    def test_evidence_verify_offline_changed_files_and_head_sha(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: b\nhead: abc123\nLGTM"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: c\nhead: abc123\nLGTM"),
                _trusted_comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:claude",
                "--changed-file", ".github/workflows/keel-ship.yml",
                "--head-sha", "abc123",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["required_count"], 6)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_live_fetch_uses_explicit_issue_without_body_infer(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output=json.dumps({
                    "body": "Closes #212",
                    "labels": [{"name": "keel:ship"}, {"name": "agent:claude"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return Namespace(ok=True, output=json.dumps([
                    [{"filename": "src/keel/evidence.py"}],
                ]))
            if endpoint.endswith("/issues/300/comments"):
                return Namespace(ok=True, output=json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                    _trusted_comment("keel.review-verdict.v1\nReviewer A LGTM"),
                    _trusted_comment("keel.review-verdict.v1\nReviewer B LGTM"),
                ]))
            if endpoint.endswith("/pulls/300/reviews"):
                return Namespace(ok=True, output="[]")
            if endpoint.endswith("/issues/99/comments"):
                return Namespace(ok=True, output=json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                ]))
            return Namespace(ok=False, output="unexpected endpoint")

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--issue", "99",
                "--reviewers", "2",
                "--json",
            ])

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["issue"], 99)
        self.assertTrue(any("/issues/99/comments" in argv[-1] for argv in calls))
        self.assertFalse(any("/issues/212/comments" in argv[-1] for argv in calls))

    def test_evidence_verify_reports_live_gh_errors_and_bad_shapes(self):
        def failing_run(_argv, **_kw):
            return Namespace(ok=False, output="no auth")

        with patch("keel.cli.run_argv", side_effect=failing_run):
            rc_fail, _, err_fail = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_fail, 1)
        self.assertIn("gh api repos/berkayturanci/example-android/pulls/300 failed", err_fail)

        def bad_object_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output="[]")
            return Namespace(ok=True, output="[]")

        with patch("keel.cli.run_argv", side_effect=bad_object_run):
            rc_obj, _, err_obj = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_obj, 1)
        self.assertIn("did not return a JSON object", err_obj)

        def bad_list_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output=json.dumps({"body": ""}))
            return Namespace(ok=True, output="{}")

        with patch("keel.cli.run_argv", side_effect=bad_list_run):
            rc_list, _, err_list = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_list, 1)
        self.assertIn("did not return a JSON array", err_list)

        def failing_paginated_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output=json.dumps({"body": ""}))
            return Namespace(ok=False, output="page failed")

        with patch("keel.cli.run_argv", side_effect=failing_paginated_run):
            rc_page, _, err_page = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_page, 1)
        self.assertIn("page failed", err_page)

    def test_evidence_verify_live_fetch_allows_missing_linked_issue(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return Namespace(ok=True, output=json.dumps({
                    "body": "Refs #212",
                    "labels": [{"name": "keel:ship"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return Namespace(ok=True, output=json.dumps([
                    [{"filename": "src/keel/evidence.py"}],
                ]))
            return Namespace(ok=True, output="[]")

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIsNone(data["issue"])
        self.assertFalse(any("/issues/212/comments" in argv[-1] for argv in calls))

    def test_evidence_verify_without_gate_label_is_not_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            pr_comments.write_text("[]", encoding="utf-8")
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--reviewers", "2",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["enforced"])
        self.assertEqual(data["gate_label"], "keel:ship")
        self.assertEqual(data["pr_labels"], [])
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["required_count"], 0)

    def test_evidence_verify_without_provenance_human_output_reports_reason(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--reviewers", "1",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false", out)
        self.assertIn("no-ship-provenance", out)

    def test_evidence_verify_with_label_is_fail_closed(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "keel:ship",
            "--reviewers", "1",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--json",
        ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["verification"]["status"], "fail")
        # Fail for the right reason: the gate is enforced and reports the
        # missing evidence, not an unrelated error.
        self.assertGreaterEqual(data["verification"]["required_count"], 1)
        self.assertTrue(data["verification"]["missing"])

    def test_evidence_verify_gate_label_override(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "ship-me",
            "--gate-label", "ship-me",
            "--reviewers", "1",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--json",
        ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["gate_label"], "ship-me")
        self.assertTrue(data["enforced"])
        self.assertEqual(data["pr_labels"], ["ship-me"])

    def test_label_names_handles_absent_and_malformed_labels(self):
        # A live PR payload may omit `labels`, send null, or carry malformed
        # entries; _label_names must degrade to a clean list of name strings.
        self.assertEqual(cli._label_names(None), [])
        self.assertEqual(cli._label_names("nope"), [])
        self.assertEqual(
            cli._label_names([{"name": "keel:ship"}, {"no_name": 1}, "x", {"name": 7}]),
            ["keel:ship"],
        )


def _scope_ledger(declared_files, *, pr=300):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "pull_request": {"number": pr},
        "changes": {"file_count": 1, "files": ["a.py"]},
        "declared": {"file_count": len(declared_files), "files": list(declared_files)},
    }
    return ledger.encode_record(record)


class TestScopeVerify(unittest.TestCase):
    def _run(self, *, ledger_text, changed, deferral=None):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(ledger_text, encoding="utf-8")
            argv = [
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl), "--json",
            ]
            for path in changed:
                argv += ["--changed-file", path]
            if deferral is not None:
                argv += ["--deferral", deferral]
            return run(argv)

    def test_in_scope_diff_passes(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]), changed=["a.py"]
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["scope_creep"], [])

    def test_scope_creep_fails_and_lists_unexpected_file(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]), changed=["a.py", "unrelated.py"]
        )
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertEqual(data["verification"]["scope_creep"], ["unrelated.py"])

    def test_docs_paths_are_exempt(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]),
            changed=["a.py", "docs/keel/cli.md"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["docs_exempt"], ["docs/keel/cli.md"])

    def test_no_declared_record_is_advisory_pass(self):
        rc, out, _ = self._run(ledger_text="", changed=["a.py", "x.py"])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["verification"]["advisory"])
        self.assertEqual(data["verification"]["note"], "no declared scope recorded")

    def test_scope_waived_deferral_is_honored(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]),
            changed=["a.py", "unrelated.py"],
            deferral="scope-waived",
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertTrue(data["verification"]["waived"])

    def test_human_output_lists_creep_and_docs(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_scope_ledger(["a.py"]), encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl),
                "--changed-file", "a.py", "--changed-file", "docs/x.md",
                "--changed-file", "unrelated.py",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("keel scope-verify — fail", out)
        self.assertIn("scope-creep   : unrelated.py", out)
        self.assertIn("docs-exempt   : docs/x.md", out)

    def test_human_output_advisory_note(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text("", encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl), "--changed-file", "a.py",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("note          : no declared scope recorded", out)

    def test_human_output_waived_note(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_scope_ledger(["a.py"]), encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl),
                "--changed-file", "a.py", "--changed-file", "unrelated.py",
                "--deferral", "scope-waived",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("note          : scope creep waived by operator deferral", out)

    def test_human_output_clean_in_scope_pass(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_scope_ledger(["a.py"]), encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl), "--changed-file", "a.py",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("keel scope-verify — pass", out)
        self.assertIn("in-scope      : 1 file(s)", out)
        self.assertNotIn("scope-creep", out)

    def test_reads_configured_ledger_under_root_when_no_fixture(self):
        # With no --ledger-jsonl, scope-verify reads the configured run ledger
        # under --root; a fresh root has no ledger → advisory pass.
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", d, "--pr", "300", "--dry-run",
                "--changed-file", "a.py", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["verification"]["advisory"])

    def test_invalid_ledger_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "ledger.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(bad), "--changed-file", "a.py", "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", err)

    def test_missing_config_reports_error(self):
        rc, _, err = run([
            "scope-verify", "no-such.yaml", "--pr", "300", "--dry-run",
            "--changed-file", "a.py",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        rc, _, err = run([
            "scope-verify", bad, "--pr", "300", "--dry-run", "--changed-file", "a.py",
        ])
        self.assertEqual(rc, 1)

    def test_artifact_value_error_reports(self):
        # A live (non-dry-run, no-fixture) fetch on a config missing owner/repo
        # raises ValueError from _owner_repo and is surfaced cleanly.
        bad = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, _, err = run([
            "scope-verify", bad, "--root", str(REPO_ROOT), "--pr", "300", "--json",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)


class TestVerifyBranch(unittest.TestCase):
    BASE = [
        "verify-branch", str(PROJECTS / "example-android.yaml"),
        "--root", str(REPO_ROOT), "--pr", "300", "--offline",
    ]

    def test_clean_offline_passes(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "t", "--base-distance", "0",
            "--worktree-path", "/repo/worktrees/i", "--repo-root", "/repo",
            "--linked-worktree", "true",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["verdict"], "ok")
        self.assertEqual(data["verification"]["base_branch"], "develop")

    def test_stale_base_fails(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "old", "--base-distance", "9", "--tolerance", "5",
            "--linked-worktree", "true", "--worktree-path", "/repo/wt",
            "--repo-root", "/repo",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["verdict"], "stale")
        self.assertIn("base is stale", data["verification"]["note"])

    def test_allow_stale_base_downgrades_to_advisory_pass(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "old", "--base-distance", "9", "--tolerance", "5",
            "--allow-stale-base", "--linked-worktree", "true",
            "--worktree-path", "/repo/wt", "--repo-root", "/repo",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["verdict"], "stale")
        self.assertTrue(data["verification"]["allow_stale_base"])
        self.assertIn("advisory", data["verification"]["note"])

    def test_contaminated_primary_checkout_fails(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "t", "--base-distance", "0",
            "--worktree-path", "/repo", "--repo-root", "/repo",
            "--linked-worktree", "false",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["verdict"], "contaminated")
        self.assertIn("primary checkout", data["verification"]["note"])

    def test_ci_no_worktree_skips_isolation(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "t", "--base-distance", "0",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")

    def test_human_output_renders_summary(self):
        rc, out, _ = run(self.BASE + [
            "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "old", "--base-distance", "9", "--tolerance", "5",
            "--worktree-path", "/repo/wt", "--repo-root", "/repo",
            "--linked-worktree", "true",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("keel verify-branch — fail", out)
        self.assertIn("base          : origin/develop", out)
        self.assertIn("verdict       : stale", out)
        self.assertIn("base-distance : 9 (tolerance 5)", out)
        self.assertIn("isolation     : ok", out)

    def test_live_path_gathers_facts_via_gh_and_git(self):
        # A non-offline run resolves the PR head via gh and the ancestry +
        # worktree facts via the git wrappers. We stub run_argv for both surfaces.
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                body = json.dumps({"head": {"sha": "headsha", "ref": "feature/x"}})
                return Namespace(ok=True, output=body)
            if argv[:2] == ["git", "rev-parse"]:
                return Namespace(ok=True, output="tipsha\n")
            if argv[:2] == ["git", "merge-base"]:
                return Namespace(ok=True, output="tipsha\n")
            if argv[:2] == ["git", "rev-list"]:
                return Namespace(ok=True, output="0\n")
            if argv[:3] == ["git", "worktree", "list"]:
                porcelain = (
                    "worktree /repo\nHEAD aaa\nbranch refs/heads/develop\n\n"
                    "worktree /repo/worktrees/i\nHEAD bbb\nbranch refs/heads/feature/x\n"
                )
                return Namespace(ok=True, output=porcelain)
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["head_ref"], "feature/x")
        self.assertEqual(data["verification"]["verdict"], "ok")
        self.assertTrue(data["verification"]["isolation"]["is_linked_worktree"])

    def test_live_worktree_list_failure_skips_isolation(self):
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                body = json.dumps({"head": {"sha": "h", "ref": "feature/x"}})
                return Namespace(ok=True, output=body)
            if argv[:2] == ["git", "rev-parse"]:
                return Namespace(ok=True, output="tip\n")
            if argv[:2] == ["git", "merge-base"]:
                return Namespace(ok=True, output="tip\n")
            if argv[:2] == ["git", "rev-list"]:
                return Namespace(ok=True, output="0\n")
            if argv[:3] == ["git", "worktree", "list"]:
                return Namespace(ok=False, output="")
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")

    def test_live_branch_not_checked_out_skips_isolation(self):
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                body = json.dumps({"head": {"sha": "h", "ref": "feature/absent"}})
                return Namespace(ok=True, output=body)
            if argv[:2] == ["git", "rev-parse"]:
                return Namespace(ok=True, output="tip\n")
            if argv[:2] == ["git", "merge-base"]:
                return Namespace(ok=True, output="tip\n")
            if argv[:2] == ["git", "rev-list"]:
                return Namespace(ok=True, output="0\n")
            if argv[:3] == ["git", "worktree", "list"]:
                return Namespace(ok=True, output="worktree /repo\n")
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")

    def test_live_head_without_ref_skips_isolation(self):
        # gh returns a head SHA but no ref → no branch to locate locally.
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                return Namespace(ok=True, output=json.dumps({"head": {"sha": "h"}}))
            if argv[:2] == ["git", "rev-parse"]:
                return Namespace(ok=True, output="tip\n")
            if argv[:2] == ["git", "merge-base"]:
                return Namespace(ok=True, output="tip\n")
            if argv[:2] == ["git", "rev-list"]:
                return Namespace(ok=True, output="0\n")
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")
        self.assertEqual(data["verification"]["ancestry"]["verdict"], "ok")

    def test_live_missing_owner_repo_reports_cleanly(self):
        bad = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, _, err = run([
            "verify-branch", bad, "--root", str(REPO_ROOT), "--pr", "300", "--json",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)

    def test_missing_config_reports_error(self):
        rc, _, err = run([
            "verify-branch", "no-such.yaml", "--pr", "300", "--offline",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        rc, _, _ = run([
            "verify-branch", bad, "--pr", "300", "--offline",
        ])
        self.assertEqual(rc, 1)

    def test_negative_tolerance_rejected(self):
        with self.assertRaisesRegex(cli.argparse.ArgumentTypeError, "non-negative"):
            cli._nonnegative_int("-1")
        self.assertEqual(cli._nonnegative_int("0"), 0)

    def test_human_output_fully_clean_has_no_note(self):
        # All facts resolved + ok → no note line printed (the no-note branch).
        rc, out, _ = run(self.BASE + [
            "--head-sha", "h", "--base-tip-sha", "t", "--merge-base-sha", "t",
            "--base-distance", "0", "--worktree-path", "/repo/wt",
            "--repo-root", "/repo", "--linked-worktree", "true",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("keel verify-branch — pass", out)
        self.assertIn("base-distance : 0 (tolerance 5)", out)
        self.assertNotIn("note", out)

    def test_human_output_omits_distance_when_base_unresolved(self):
        # No base facts → base_distance is None, so no distance line is printed.
        rc, out, _ = run(self.BASE + ["--head-sha", "h"])
        self.assertEqual(rc, 0)
        self.assertNotIn("base-distance", out)
        self.assertIn("isolation     : n/a", out)


class TestVerifyBranchFactGathering(unittest.TestCase):
    """Direct unit coverage for the live fact-gathering seam branches."""

    def _args(self, **kw):
        base = dict(
            path=str(PROJECTS / "example-android.yaml"), root="/repo", pr=300,
            offline=False, head_sha=None, head_ref=None, base_tip_sha=None,
            merge_base_sha=None, base_distance=None, worktree_path=None,
            repo_root=None, linked_worktree=None,
        )
        base.update(kw)
        return Namespace(**base)

    def test_supplied_facts_short_circuit_live_calls(self):
        # Ancestry facts pre-supplied + no head_ref → every `is None` guard takes
        # its False side and the worktree lookup is skipped, so no git/gh call runs.
        def boom(*a, **k):
            raise AssertionError("no live call expected")

        args = self._args(
            head_sha="h", base_tip_sha="t", merge_base_sha="t", base_distance=0,
            worktree_path="/repo/wt", repo_root="/repo", linked_worktree="true",
        )
        with patch("keel.cli._gh_json", side_effect=boom), \
             patch("keel.git.run_argv", side_effect=boom):
            facts = cli._gather_branch_facts(args, "develop")
        self.assertEqual(facts["head_sha"], "h")
        self.assertEqual(facts["base_distance"], 0)
        self.assertTrue(facts["is_linked_worktree"])

    def test_head_without_ref_skips_local_worktree_lookup(self):
        # head_ref stays None → the `head_ref is not None` guard is False and the
        # worktree lookup is skipped without calling git for it.
        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "rev-parse"]:
                return Namespace(ok=True, output="t\n")
            raise AssertionError(f"unexpected {argv}")

        args = self._args(head_sha="h", merge_base_sha="t", base_distance=0)
        with patch("keel.git.run_argv", side_effect=fake_run):
            facts = cli._gather_branch_facts(args, "develop")
        self.assertIsNone(facts["is_linked_worktree"])
        self.assertEqual(facts["base_tip_sha"], "t")

    def test_local_facts_do_not_override_supplied_worktree_path(self):
        # worktree_path/is_linked supplied as flags; a live worktree lookup still
        # runs (head_ref set) but the supplied values win the `or`/`is None` guards.
        def fake_run(argv, **kwargs):
            if argv[:3] == ["git", "worktree", "list"]:
                porcelain = (
                    "worktree /repo\nbranch refs/heads/develop\n\n"
                    "worktree /repo/wt-auto\nbranch refs/heads/feature/x\n"
                )
                return Namespace(ok=True, output=porcelain)
            raise AssertionError(f"unexpected {argv}")

        args = self._args(
            head_sha="h", head_ref="feature/x", base_tip_sha="t",
            merge_base_sha="t", base_distance=0,
            worktree_path="/supplied/wt", repo_root="/supplied",
            linked_worktree="false",
        )
        with patch("keel.git.run_argv", side_effect=fake_run):
            facts = cli._gather_branch_facts(args, "develop")
        self.assertEqual(facts["worktree_path"], "/supplied/wt")
        self.assertEqual(facts["repo_root"], "/supplied")
        self.assertFalse(facts["is_linked_worktree"])

    def test_empty_porcelain_returns_none(self):
        self.assertEqual(cli._parse_worktree_porcelain(""), [])
        with patch("keel.git.run_argv",
                   side_effect=lambda *a, **k: Namespace(ok=True, output="\n")):
            self.assertIsNone(cli._local_worktree_facts("feature/x", cwd="/repo"))


class TestStepVerifyAndRunControls(unittest.TestCase):
    def test_step_verify_passes_with_handoff_and_evidence(self):
        handoff = stepverifier.build_handoff(
            step_id="s7",
            evidence_ids=["review-verdict-1"],
        )
        evidence_report = {
            "results": [{"id": "review-verdict-1", "ok": True}],
        }
        rc, out, _ = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
            "--reviewers", "1",
            "--json",
        ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["required_evidence"], ["review-verdict-1"])

    def test_step_verify_human_pass_without_missing(self):
        handoff = stepverifier.build_handoff(step_id="s4")
        evidence_report = {"results": []}
        rc, out, _ = run([
            "step-verify",
            "--step", "s4",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("keel step-verify", out)
        self.assertIn("required      : 0", out)

    def test_step_verify_fails_closed_for_bad_handoff_and_bad_json_shape(self):
        rc_bad, out_bad, _ = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw(json.dumps({"step_id": "s7"})),
            "--evidence-report", _write_raw(json.dumps({"results": []})),
            "--reviewers", "1",
            "--json",
        ])
        rc_shape, _, err_shape = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw("[]"),
            "--evidence-report", _write_raw(json.dumps({"results": []})),
            "--reviewers", "1",
        ])

        self.assertEqual(rc_bad, 1)
        self.assertEqual(json.loads(out_bad)["verification"]["status"], "fail")
        self.assertEqual(rc_shape, 1)
        self.assertIn("must contain a JSON object", err_shape)

    def test_runcontrols_appends_event_and_halts_on_cap(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            rc_first, out_first, _ = run([
                "runcontrols", str(events),
                "--slot", "fixloop",
                "--action", "fix",
                "--json",
            ])
            rc_halt, out_halt, _ = run([
                "runcontrols", str(events),
                "--slot", "fixloop",
                "--action", "fix",
                "--step-cap", "fixloop=1",
                "--json",
            ])

            stored = json.loads(events.read_text(encoding="utf-8"))

        self.assertEqual(rc_first, 0)
        self.assertEqual(json.loads(out_first)["run_controls"]["status"], "pass")
        self.assertEqual(rc_halt, 1)
        self.assertEqual(json.loads(out_halt)["run_controls"]["status"], "halt")
        self.assertEqual(len(stored), 2)

    def test_runcontrols_dry_run_and_invalid_step_cap(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            rc_dry, out_dry, _ = run([
                "runcontrols", str(events),
                "--slot", "tester",
                "--dry-run",
                "--json",
            ])
            rc_bad, _, err_bad = run([
                "runcontrols", str(events),
                "--step-cap", "bad",
            ])

            exists = events.exists()

        self.assertEqual(rc_dry, 0)
        self.assertFalse(json.loads(out_dry)["appended"])
        self.assertFalse(exists)
        self.assertEqual(rc_bad, 1)
        self.assertIn("--step-cap must use SLOT=N", err_bad)

    def test_runcontrols_human_halt_event_json_and_step_cap_errors(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            event = Path(d) / "event.json"
            events.write_text(json.dumps([
                {"slot": "fixloop", "action": "fix"},
            ]), encoding="utf-8")
            event.write_text(json.dumps({"slot": "fixloop", "action": "fix"}), encoding="utf-8")
            rc_human, out_human, _ = run([
                "runcontrols", str(events),
                "--event-json", str(event),
                "--step-cap", "fixloop=1",
            ])
            rc_bad_int, _, err_bad_int = run([
                "runcontrols", str(events),
                "--step-cap", "fixloop=nope",
            ])
            rc_bad_empty, _, err_bad_empty = run([
                "runcontrols", str(events),
                "--step-cap", "=0",
            ])

        self.assertEqual(rc_human, 1)
        self.assertIn("halt", out_human)
        self.assertEqual(rc_bad_int, 1)
        self.assertIn("positive integer", err_bad_int)
        self.assertEqual(rc_bad_empty, 1)
        self.assertIn("N > 0", err_bad_empty)

    def test_runcontrols_records_soft_failure_event(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            rc, out, _ = run([
                "runcontrols", str(events),
                "--slot", "tester",
                "--soft-failure",
                "--json",
            ])

        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["event"]["soft_failure"])

    def test_runcontrols_human_pass_without_event(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            events.write_text("[]", encoding="utf-8")
            rc, out, _ = run(["runcontrols", str(events)])

        self.assertEqual(rc, 0)
        self.assertIn("keel runcontrols", out)
        self.assertIn("events        : 0", out)

    def test_ship_ledger_stamps_run_controls_and_blocks_on_halt(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            events.write_text(json.dumps([
                {"slot": "fixloop", "action": "fix"},
                {"slot": "fixloop", "action": "fix"},
            ]), encoding="utf-8")
            rc, out, _ = run([
                "ship", _write_config("'true'"),
                "--root", d,
                "--run-events-file", str(events),
                "--max-rounds", "1",
                "--json",
            ])

        self.assertEqual(rc, 1)
        record = json.loads(out)["result"]["run_ledger"]["record"]
        self.assertEqual(record["run_controls"]["status"], "halt")
        self.assertEqual(record["run_controls"]["summary"]["event_count"], 2)

    def test_ship_invalid_run_events_file_and_human_halt(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            events.write_text("{}", encoding="utf-8")
            rc_bad, _, err_bad = run([
                "ship", _write_config("'true'"),
                "--root", d,
                "--run-events-file", str(events),
            ])
            events.write_text(json.dumps([
                {"slot": "fixloop", "action": "fix"},
                {"slot": "fixloop", "action": "fix"},
            ]), encoding="utf-8")
            rc_halt, out_halt, _ = run([
                "ship", _write_config("'true'"),
                "--root", d,
                "--run-events-file", str(events),
                "--max-rounds", "1",
            ])

        self.assertEqual(rc_bad, 1)
        self.assertIn("must contain a JSON array", err_bad)
        self.assertEqual(rc_halt, 1)
        self.assertIn("run controls  : halt", out_halt)

    def test_step_verify_human_missing_and_unknown_step(self):
        handoff = stepverifier.build_handoff(step_id="s7")
        evidence_report = {"results": []}
        rc_missing, out_missing, _ = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
            "--reviewers", "1",
        ])
        rc_unknown, _, err_unknown = run([
            "step-verify",
            "--step", "s404",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
        ])

        self.assertEqual(rc_missing, 1)
        self.assertIn("missing", out_missing)
        self.assertIn("required      : 1", out_missing)
        self.assertEqual(rc_unknown, 1)
        self.assertIn("unknown backbone step", err_unknown)


class TestCoreMerge(unittest.TestCase):
    def test_claim_and_release_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rc_claim, out_claim, _ = run([
                "claim", "--root", d, "--owner", "agent-a", "--json", "merge",
            ])
            rc_release, out_release, _ = run([
                "release", "--root", d, "--owner", "agent-a", "--json", "merge",
            ])

        self.assertEqual(rc_claim, 0)
        self.assertEqual(json.loads(out_claim)["status"], "granted")
        self.assertEqual(rc_release, 0)
        self.assertEqual(json.loads(out_release)["status"], "released")

    def test_claim_denial_human_output_reports_holder(self):
        with tempfile.TemporaryDirectory() as d:
            rc_success, out_success, _ = run(["claim", "--root", d, "--owner", "agent-a", "ci"])
            run(["claim", "--root", d, "--owner", "agent-a", "merge"])
            rc, out, _ = run(["claim", "--root", d, "--owner", "agent-b", "merge"])

        self.assertEqual(rc_success, 0)
        self.assertIn("granted", out_success)
        self.assertEqual(rc, 1)
        self.assertIn("denied", out)
        self.assertIn("holder: agent-a", out)

    def test_release_non_owner_human_output_reports_holder(self):
        with tempfile.TemporaryDirectory() as d:
            run(["claim", "--root", d, "--owner", "agent-a", "ci"])
            rc_success, out_success, _ = run(["release", "--root", d, "--owner", "agent-a", "ci"])
            run(["claim", "--root", d, "--owner", "agent-a", "merge"])
            rc, out, _ = run(["release", "--root", d, "--owner", "agent-b", "merge"])

        self.assertEqual(rc_success, 0)
        self.assertIn("released", out_success)
        self.assertEqual(rc, 1)
        self.assertIn("not-owner", out)
        self.assertIn("holder: agent-a", out)

    def test_claim_and_release_human_output_omit_empty_holder(self):
        claim_result = Namespace(
            status="granted",
            resource="merge",
            owner="agent-a",
            holder=None,
            path="/tmp/merge.lock",
            granted=True,
            as_dict=lambda: {},
        )
        release_result = Namespace(
            status="missing",
            resource="merge",
            owner="agent-a",
            holder=None,
            path="/tmp/merge.lock",
            granted=False,
            as_dict=lambda: {},
        )
        with patch("keel.cli.lock.claim_resource", return_value=claim_result):
            rc_claim, out_claim, _ = run(["claim", "--owner", "agent-a", "merge"])
        with patch("keel.cli.lock.release_resource", return_value=release_result):
            rc_release, out_release, _ = run(["release", "--owner", "agent-a", "merge"])

        self.assertEqual(rc_claim, 0)
        self.assertNotIn("holder:", out_claim)
        self.assertEqual(rc_release, 0)
        self.assertNotIn("holder:", out_release)

    def test_worktree_remove_rejects_unregistered_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with patch("keel.cli.git.worktree_list",
                       return_value=Namespace(ok=True, output=f"worktree {root}\n")):
                rc, _, err = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc, 1)
        self.assertIn("not a registered git worktree", err)

    def test_worktree_remove_reports_list_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with patch("keel.cli.git.worktree_list",
                       return_value=Namespace(ok=False, output="bad list")):
                rc, _, err = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc, 1)
        self.assertIn("bad list", err)

    def test_worktree_remove_rejects_repo_root(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["worktree-remove", "--root", d, d])

        self.assertEqual(rc, 1)
        self.assertIn("nested under the repository root", err)

    def test_worktree_remove_calls_git_after_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with (
                patch("keel.cli.git.worktree_list",
                      return_value=Namespace(ok=True, output=f"worktree {worktree}\n")),
                patch("keel.cli.git.worktree_remove",
                      return_value=Namespace(ok=True, code=0, output="")),
            ):
                rc, out, _ = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc, 0)
        self.assertIn("removed", out)

    def test_worktree_remove_json_and_failed_git_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with (
                patch("keel.cli.git.worktree_list",
                      return_value=Namespace(ok=True, output=f"worktree {worktree}\n")),
                patch("keel.cli.git.worktree_remove",
                      return_value=Namespace(ok=False, code=1, output="remove failed")),
            ):
                rc_json, out_json, _ = run([
                    "worktree-remove", "--root", d, "--json", str(worktree),
                ])
                rc_human, out_human, _ = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc_json, 1)
        self.assertFalse(json.loads(out_json)["removed"])
        self.assertEqual(rc_human, 1)
        self.assertIn("remove failed", out_human)

    def test_merge_reports_missing_and_invalid_config(self):
        rc_missing, _, err_missing = run([
            "merge", str(Path("missing.yaml")), "--pr", "1",
            "--approve-scope", "filesystem,git,github",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad = f.name
        rc_bad, _, err_bad = run([
            "merge", bad, "--pr", "1", "--approve-scope", "filesystem,git,github",
        ])

        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)
        self.assertEqual(rc_bad, 1)
        self.assertIn("missing required", err_bad)

    def test_merge_blocks_missing_capability_and_missing_consent(self):
        missing_report = runtime.CapabilityReport((
            runtime.Capability("git", False, "missing", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=missing_report):
            rc_cap, _, err_cap = run([
                "merge", str(PROJECTS / "keel.yaml"), "--pr", "123",
            ])
        with patch("keel.cli.runtime.detect", return_value=_merge_capability_report()):
            rc_consent, _, err_consent = run([
                "merge", str(PROJECTS / "keel.yaml"), "--pr", "123",
            ])

        self.assertEqual(rc_cap, 1)
        self.assertIn("missing required", err_cap)
        self.assertEqual(rc_consent, 1)
        self.assertIn("operator consent required", err_consent)

    def test_merge_blocks_when_escalation_scope_is_not_approved(self):
        fake_report = _merge_capability_report()
        argv = _merge_args(json_out=True)
        argv.extend([
            "--risk-tier", "tier-3",
            "--trust-signal", "low",
            "--escalation-side-effect", "secret_access",
        ])
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.lock.claim_resource") as claim_mock,
        ):
            rc, out, _ = run(argv)

        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reason"], "operator escalation required")
        self.assertEqual(data["missing_scope"], ["secrets"])
        self.assertTrue(data["escalation"]["operator_required"])
        claim_mock.assert_not_called()

    def test_merge_human_blocks_when_escalation_scope_is_not_approved(self):
        fake_report = _merge_capability_report()
        argv = _merge_args()
        argv.extend(["--escalation-side-effect", "secret_access"])
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            rc, _, err = run(argv)

        self.assertEqual(rc, 1)
        self.assertIn("operator escalation required", err)
        self.assertIn("secrets", err)

    def test_merge_reports_extension_problem_and_consent_env_error(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.load_extensions", return_value=({}, ["broken-extension"])),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "FAILURE"}],
                  })),
        ):
            rc_ext, _, err_ext = run(_merge_args())
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch.dict("os.environ", {"KEEL_APPROVE_SCOPE": "filesystem,git,github"}, clear=True),
        ):
            rc_env, _, err_env = run([
                "merge", str(PROJECTS / "keel.yaml"), "--pr", "123",
                "--consent-mode", "standing",
            ])

        self.assertEqual(rc_ext, 1)
        self.assertIn("extension not loaded", err_ext)
        self.assertEqual(rc_env, 1)
        self.assertIn("KEEL_OPERATOR", err_env)

    def test_merge_blocks_red_ci_before_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "FAILURE"}],
                  })),
            patch("keel.cli._verify_merge_evidence") as evidence_mock,
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["ci"]["state"], "fail")
        evidence_mock.assert_not_called()

    def test_merge_blocks_closed_window_snapshot_error_and_dirty_state(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=False),
        ):
            rc_window, out_window, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=Namespace(ok=False, output="gh failed")),
        ):
            rc_snapshot, out_snapshot, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "DIRTY",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
        ):
            rc_dirty, out_dirty, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc_window, 1)
        self.assertIn("merge window is closed", json.loads(out_window)["reason"])
        self.assertEqual(rc_snapshot, 1)
        self.assertIn("unable to read", json.loads(out_snapshot)["reason"])
        self.assertEqual(rc_dirty, 1)
        self.assertIn("DIRTY", json.loads(out_dirty)["reason"])

    def test_merge_hotfix_records_window_bypass(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
        ):
            argv = _merge_args(json_out=True, dry_run=True)
            argv.append("--hotfix")
            rc, out, _ = run(argv)

        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["window"]["bypassed"])

    def test_merge_blocks_pending_ci_and_non_enforced_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"status": "IN_PROGRESS"}],
                  })),
        ):
            rc_pending, out_pending, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": False,
                "verification": {"status": "pass", "missing": []},
            }),
        ):
            rc_evidence, out_evidence, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc_pending, 1)
        self.assertEqual(json.loads(out_pending)["ci"]["state"], "pending")
        self.assertEqual(rc_evidence, 1)
        self.assertIn("not enforced", json.loads(out_evidence)["reason"])

    def test_merge_blocks_missing_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "fail", "missing": ["review-verdict-1"]},
            }),
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc, 1)
        self.assertIn("missing evidence", json.loads(out)["reason"])

    def test_merge_allows_projects_without_configured_window(self):
        fake_report = _merge_capability_report()
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "extends: keel\n"
                "core_version: '^1.0'\n"
                "base_branch: main\n"
                "knobs:\n"
                "  build_gate_cmd: make test\n"
            )
            path = f.name
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
        ):
            rc, out, _ = run([
                "merge", path,
                "--root", str(REPO_ROOT),
                "--pr", "123",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester",
                "--dry-run",
                "--json",
            ])

        self.assertEqual(rc, 0)
        self.assertIsNone(json.loads(out)["window"])

    def test_merge_blocks_when_lock_is_already_claimed(self):
        fake_report = _merge_capability_report()
        with tempfile.TemporaryDirectory() as d:
            cli.lock.claim_resource(cli._lock_root(d), "merge", owner="other")
            with patch("keel.cli.runtime.detect", return_value=fake_report):
                rc, out, _ = run(_merge_args(root=d, json_out=True))

        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["lock"]["status"], "denied")

    def test_merge_dry_run_passes_after_lock_window_ci_and_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
            patch("keel.cli.github.merge_pr") as merge_mock,
        ):
            rc, out, _ = run(_merge_args(json_out=True, dry_run=True))

        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(out)["merged"])
        self.assertTrue(json.loads(out)["gates_sha"]["matched"])
        merge_mock.assert_not_called()

    def test_merge_executes_and_reports_gh_merge_failure(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
            patch("keel.cli.github.merge_pr",
                  return_value=Namespace(ok=False, output="merge failed")),
        ):
            rc_fail, out_fail, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
            patch("keel.cli.github.merge_pr",
                  return_value=Namespace(ok=True, output="merged")),
        ):
            rc_ok, out_ok, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc_fail, 1)
        self.assertEqual(json.loads(out_fail)["reason"], "gh merge failed")
        self.assertEqual(rc_ok, 0)
        self.assertTrue(json.loads(out_ok)["merged"])

    def _gates_record(self, *, pr, head_sha, ok=True, blocked=False, error=None):
        return {
            "schema_version": ledger.LEDGER_SCHEMA_VERSION,
            "record_type": ledger.RECORD_TYPE_SHIP_RUN,
            "run_id": f"RUN-{pr}",
            "pull_request": {"number": pr},
            "git": {"head_sha": head_sha},
            "verdict": {"blocked": blocked},
            "gates": [{"gate": "build", "ok": ok, "skipped": False, "error": error}],
        }

    def _merge_with_gates(self, records):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "head-new",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=records),
            patch("keel.cli.github.merge_pr",
                  return_value=Namespace(ok=True, output="merged")),
        ):
            return run(_merge_args(json_out=True))

    def test_merge_requires_gates_pass_for_current_head(self):
        records = [self._gates_record(pr=123, head_sha="head-new")]
        rc, out, _ = self._merge_with_gates(records)

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged"])
        self.assertTrue(data["gates_sha"]["matched"])
        self.assertEqual(data["gates_sha"]["head_sha"], "head-new")

    def test_merge_refuses_when_gates_pass_is_for_stale_head(self):
        records = [self._gates_record(pr=123, head_sha="head-old")]
        rc, out, _ = self._merge_with_gates(records)

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertFalse(data["merged"])
        self.assertFalse(data["gates_sha"]["matched"])
        self.assertIn("no gates-pass recorded for the current head head-new", data["reason"])

    def test_merge_refuses_when_no_gates_record_exists(self):
        rc, out, _ = self._merge_with_gates([])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("no gates-pass recorded for the current head head-new", data["reason"])

    def test_merge_refuses_when_gates_record_failed(self):
        records = [
            self._gates_record(pr=123, head_sha="head-new", ok=False, blocked=True),
        ]
        rc, out, _ = self._merge_with_gates(records)

        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["gates_sha"]["matched"])

    def test_merge_hotfix_bypasses_gates_sha_requirement(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "head-new",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records") as read_mock,
            patch("keel.cli.github.merge_pr",
                  return_value=Namespace(ok=True, output="merged")),
        ):
            argv = _merge_args(json_out=True)
            argv.append("--hotfix")
            rc, out, _ = run(argv)

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged"])
        self.assertTrue(data["gates_sha"]["bypassed"])
        self.assertEqual(data["gates_sha"]["reason"], "hotfix")
        read_mock.assert_not_called()

    def test_merge_reports_invalid_ledger_during_gates_check(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "head-new",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records",
                  side_effect=ledger.LedgerError("bad line")),
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", json.loads(out)["reason"])

    def test_merge_human_output_prints_gates_sha(self):
        args = Namespace(json=False, pr=7)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._finish_merge(args, {"gates_sha": {"bypassed": False, "matched": True}},
                              "merged", code=0)
        self.assertIn("gates-sha: matched", out.getvalue())

        out_bypass = io.StringIO()
        with contextlib.redirect_stdout(out_bypass):
            cli._finish_merge(args, {"gates_sha": {"bypassed": True}}, "merged", code=0)
        self.assertIn("gates-sha: bypassed (hotfix)", out_bypass.getvalue())

        out_nomatch = io.StringIO()
        with contextlib.redirect_stdout(out_nomatch):
            cli._finish_merge(args, {"gates_sha": {"bypassed": False, "matched": False}},
                              "blocked", code=1)
        self.assertIn("gates-sha: no-match", out_nomatch.getvalue())

    def test_merge_human_output_prints_ci_and_evidence(self):
        args = Namespace(json=False, pr=7)
        payload = {
            "lock": {"status": "granted"},
            "ci": {"state": "pass"},
            "evidence": {"verification": {"status": "pass"}},
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli._finish_merge(args, payload, "ok", code=0)

        self.assertEqual(rc, 0)
        self.assertIn("evidence: pass", out.getvalue())

    def test_merge_human_output_handles_sparse_payload(self):
        args = Namespace(json=False, pr=7)
        payload = {"lock": None, "ci": None, "evidence": {"verification": "bad"}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli._finish_merge(args, payload, "blocked", code=1)

        self.assertEqual(rc, 1)
        self.assertIn("blocked", out.getvalue())

    def test_merge_snapshot_and_ci_rollup_edges(self):
        with patch(
            "keel.cli.github.pr_merge_snapshot",
            return_value=Namespace(ok=True, output="{"),
        ):
            with self.assertRaisesRegex(ValueError, "not JSON"):
                cli._merge_snapshot(1, cwd=".")
        pending = cli._ci_rollup_state(["bad", {"status": "PENDING"}])
        empty = cli._ci_rollup_state([])

        self.assertEqual(pending["state"], "pending")
        self.assertEqual(empty["reason"], "no-checks")

    def test_merge_ci_rollup_skipped_matches_ship_assessment(self):
        rollup = cli._ci_rollup_state([{"conclusion": "SKIPPED"}])

        self.assertTrue(ship.ci_passing("SKIPPED"))
        self.assertEqual(rollup["state"], "pass")

    def test_verify_merge_evidence_uses_live_artifacts_and_tier(self):
        config = cli.cfg.load_config(PROJECTS / "keel.yaml")
        args = Namespace(
            pr=123,
            issue=265,
            root=str(REPO_ROOT),
            reviewers=1,
            review_comments="summary",
            jury=False,
            no_jury=True,
            jury_advisory=False,
            gate_label=None,
        )
        artifact = {
            "pr_body": "Closes #265",
            "pr_comments": [
                {
                    "body": "<!-- keel.review-verdict.v1 -->\nreviewer: a\nhead: abc\nLGTM",
                    "author_association": "OWNER",
                },
                {
                    "body": "<!-- keel.closure-comment.v1 -->",
                    "author_association": "OWNER",
                },
            ],
            "issue_comments": [
                {"body": "<!-- keel.closure-comment.v1 -->", "author_association": "OWNER"},
            ],
            "pr_reviews": [],
            "issue": 265,
            "head_sha": "abc",
            "changed_files": ["src/keel/cli.py"],
            "pr_labels": ["keel:ship", "agent:claude"],
        }
        with patch("keel.cli._load_evidence_artifacts", return_value=artifact):
            report = cli._verify_merge_evidence(args, config)

        self.assertTrue(report["enforced"])
        self.assertEqual(report["verification"]["status"], "pass")


    def test_capture_reconcile_plans_missing_marker_actions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run([
                "capture-reconcile", config, "--root", d,
                "--merged-pr", "160", "--linked-issue", "160=42", "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        reconcile = data["reconcile"]
        self.assertEqual(data["contract"]["schema_version"], "keel.capture-reconcile.v1")
        self.assertTrue(data["no_mutations"])
        self.assertEqual(reconcile["status"], "actionable")
        self.assertEqual(reconcile["results"][0]["marker"],
                         "compound-learning: pr=160 status=skipped:no-policy")
        self.assertEqual(reconcile["results"][0]["actions"][-1]["type"],
                         "close-linked-issue")

    def test_capture_reconcile_human_output_lists_dry_run_actions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["capture-reconcile", config, "--root", d,
                              "--merged-pr", "160", "--linked-issue", "160=42"])

        self.assertEqual(rc, 0)
        self.assertIn("DRY-RUN: emit-capture-marker", out)
        self.assertIn("close-linked-issue:issue-42:pr-160", out)

    def test_capture_reconcile_human_output_and_blocked_exit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"schema_version":"keel.run-ledger.v1","record_type":"ship_run",'
                '"pull_request":{"number":160},"capture":{"marker":"not a marker"}}\n',
                encoding="utf-8",
            )
            rc, out, _ = run(["capture-reconcile", config, "--root", d,
                              "--merged-pr", "160"])

        self.assertEqual(rc, 1)
        self.assertIn("keel capture-reconcile", out)
        self.assertIn("PR #160  invalid", out)

    def test_capture_reconcile_reports_bad_mapping_and_reconcile_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            with patch("keel.cli.capture.reconcile_session",
                       side_effect=cli.capture.CaptureError("bad reconcile")):
                rc_error, _, err_error = run([
                    "capture-reconcile", config, "--root", d,
                    "--merged-pr", "160",
                ])

        with self.assertRaisesRegex(cli.argparse.ArgumentTypeError, "linked issue mapping"):
            cli._parse_pr_issue_mapping("bad")
        with self.assertRaisesRegex(cli.argparse.ArgumentTypeError, "linked issue mapping"):
            cli._parse_pr_issue_mapping("160=0")
        self.assertEqual(rc_error, 1)
        self.assertIn("bad reconcile", err_error)

    def test_capture_reconcile_reports_config_and_ledger_errors(self):
        import tempfile
        rc_missing, _, err_missing = run(["capture-reconcile", "/no/such.yaml",
                                          "--merged-pr", "160"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["capture-reconcile", _write_raw("extends: keel\n"),
                                          "--merged-pr", "160"])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad, _, err_bad = run(["capture-reconcile", config, "--root", d,
                                      "--merged-pr", "160"])

        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid ledger", err_bad)

    def test_ledger_reports_missing_invalid_config_and_bad_file(self):
        import tempfile
        rc_missing, _, err_missing = run(["ledger", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["ledger", _write_raw("extends: keel\n")])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad, _, err_bad = run(["ledger", config, "--root", d])
        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid ledger", err_bad)

    def test_status_json_reads_checkpoint_and_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            rc_checkpoint, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-148",
                "--checkpoint-command", "overnight",
                "--step", "s6",
                "--issue-queue", "148",
                "--issue-queue", "146",
                "--active-issue", "148",
                "--pull-request", "168",
                "--branch", "feature/issue-148-progress-status",
                "--worktree", "worktrees/issue-148",
            ])
            rc_ship, _, _ = run([
                "ship", config, "--root", d, "--live", "--append-ledger",
                "--issue", "147",
                "--pull-request", "167",
                "--capture-status", "applied",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester",
            ])
            rc, out, _ = run(["status", config, "--root", d, "--json"])

        self.assertEqual(rc_checkpoint, 0)
        self.assertEqual(rc_ship, 0)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["contract"]["schema_version"], "keel.progress-status.v1")
        self.assertEqual(payload["snapshot"]["status"], "waiting")
        self.assertEqual(payload["snapshot"]["current"]["wait_reason"], "ci")
        self.assertEqual(payload["snapshot"]["history"]["counts"]["shipped"], 1)
        self.assertEqual(payload["snapshot"]["capture_health"]["status"], "clean")
        self.assertEqual(payload["snapshot"]["next"]["issue"], 146)

    def test_status_human_output_no_active_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            rc, out, _ = run(["status", config, "--root", d])

        self.assertEqual(rc, 0)
        self.assertIn("keel status", out)
        self.assertIn("no-active-run", out)
        self.assertIn("capture       : clean", out)
        self.assertIn("capture gaps  : 0", out)
        self.assertIn("next          : -", out)

    def test_status_reports_missing_invalid_config_checkpoint_and_ledger(self):
        import tempfile
        rc_missing, _, err_missing = run(["status", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["status", _write_raw("extends: keel\n")])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            checkpoint_path = Path(d) / "state" / "checkpoint.json"
            checkpoint_path.parent.mkdir(parents=True)
            checkpoint_path.write_text("{", encoding="utf-8")
            rc_bad_checkpoint, _, err_bad_checkpoint = run(["status", config, "--root", d])

        self.assertEqual(rc_bad_checkpoint, 1)
        self.assertIn("invalid checkpoint", err_bad_checkpoint)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            ledger_path = Path(d) / "state" / "runs.jsonl"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_text("{", encoding="utf-8")
            rc_bad_ledger, _, err_bad_ledger = run(["status", config, "--root", d])

        self.assertEqual(rc_bad_ledger, 1)
        self.assertIn("invalid ledger", err_bad_ledger)

    def test_checkpoint_write_read_and_resume_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc, out, _ = run([
                "checkpoint", config, "--root", d, "--write", "--json",
                "--run-id", "RUN-149",
                "--checkpoint-command", "ship",
                "--step", "s6",
                "--target", "issue #149",
                "--issue-queue", "149",
                "--active-issue", "149",
                "--branch", "feat/issue-149-resume",
                "--worktree", "worktrees/issue-149",
                "--pull-request", "170",
                "--head-sha", "abc123",
                "--completed-step", "s0",
                "--completed-step", "s1",
                "--last-check", "ci",
                "--stop-reason", "waiting on CI",
            ])
            checkpoint_path = Path(d) / "state" / "checkpoint.json"
            rc_read, out_read, _ = run(["checkpoint", config, "--root", d, "--json"])
            rc_resume, out_resume, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "open",
                "--live-worktree-state", "present",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["path"], str(checkpoint_path.resolve()))
        self.assertEqual(data["checkpoint"]["position"]["current_step"], "s6")
        self.assertEqual(data["checkpoint"]["resume"]["action"], "recheck-ci")
        self.assertEqual(rc_read, 0)
        read = json.loads(out_read)
        self.assertEqual(read["status"], "present")
        self.assertEqual(read["checkpoint"]["run_id"], "RUN-149")
        self.assertEqual(rc_resume, 0)
        plan = json.loads(out_resume)["resume_plan"]
        self.assertEqual(plan["status"], "waiting-on-ci")
        self.assertEqual(plan["next_step"], "s6")
        self.assertEqual(plan["resume_action"], "recheck-ci")

    def test_resume_after_merge_before_capture(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s10",
                "--pull-request", "170",
                "--merge-state", "merged",
                "--capture-state", "not-started",
                "--close-state", "not-started",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "merged",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 0)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")
        self.assertEqual(plan["resume_action"], "run-or-verify-capture")

    def test_resume_after_merge_ignores_missing_worktree(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s10",
                "--worktree", "worktrees/issue-149",
                "--pull-request", "170",
                "--merge-state", "merged",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "merged",
                "--live-worktree-state", "missing",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 0)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")

    def test_resume_ambiguous_live_state_returns_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s7",
                "--worktree", "worktrees/issue-149",
                "--pull-request", "170",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-worktree-state", "missing",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 1)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "ambiguous")
        self.assertFalse(plan["can_resume"])
        self.assertTrue(plan["warnings"])

    def test_resume_closed_unmerged_pr_returns_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s7",
                "--pull-request", "170",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "closed",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 1)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "ambiguous")
        self.assertIn("closed", plan["reason"])

    def test_checkpoint_human_missing_present_and_resume_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_missing, out_missing, _ = run(["checkpoint", config, "--root", d])
            rc_resume_missing, out_resume_missing, _ = run(["resume", config, "--root", d])
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s2",
            ])
            rc_present, out_present, _ = run(["checkpoint", config, "--root", d])
            rc_resume, out_resume, _ = run(["resume", config, "--root", d])

        self.assertEqual(rc_missing, 0)
        self.assertIn("missing", out_missing)
        self.assertEqual(rc_resume_missing, 0)
        self.assertIn("no-checkpoint", out_resume_missing)
        self.assertEqual(rc_write, 0)
        self.assertEqual(rc_present, 0)
        self.assertIn("RUN-149", out_present)
        self.assertIn("safe boundary", out_present)
        self.assertEqual(rc_resume, 0)
        self.assertIn("ready", out_resume)
        self.assertIn("ensure-branch-and-worktree", out_resume)

    def test_checkpoint_reports_missing_invalid_config_and_bad_file(self):
        import tempfile
        rc_missing, _, err_missing = run(["checkpoint", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_checkpoint_invalid, _, err_checkpoint_invalid = run([
            "checkpoint", _write_raw("extends: keel\n")
        ])
        self.assertEqual(rc_checkpoint_invalid, 1)
        self.assertIn("invalid keel config", err_checkpoint_invalid)

        rc_resume_missing, _, err_resume_missing = run(["resume", "/no/such.yaml"])
        self.assertEqual(rc_resume_missing, 1)
        self.assertIn("no such config", err_resume_missing)

        rc_invalid, _, err_invalid = run(["resume", _write_raw("extends: keel\n")])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            path = Path(d) / "state" / "checkpoint.json"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad_checkpoint, _, err_bad_checkpoint = run(["checkpoint", config, "--root", d])
            rc_bad_resume, _, err_bad_resume = run(["resume", config, "--root", d])
            with patch("keel.cli.checkpoint.build_checkpoint_record",
                       side_effect=cli.checkpoint.CheckpointError("cannot checkpoint")):
                rc_bad_write, _, err_bad_write = run([
                    "checkpoint", config, "--root", d, "--write",
                    "--run-id", "RUN-149",
                    "--step", "s0",
                ])

        self.assertEqual(rc_bad_checkpoint, 1)
        self.assertIn("invalid checkpoint", err_bad_checkpoint)
        self.assertEqual(rc_bad_resume, 1)
        self.assertIn("invalid checkpoint", err_bad_resume)
        self.assertEqual(rc_bad_write, 1)
        self.assertIn("cannot checkpoint", err_bad_write)

    def test_resume_human_ambiguous_prints_warning(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s7",
                "--worktree", "worktrees/issue-149",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d,
                "--live-worktree-state", "missing",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 1)
        self.assertIn("ambiguous", out)
        self.assertIn("warning", out)

    def test_ship_human_append_ledger_dry_run_message(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--append-ledger"])
        self.assertEqual(rc, 0)
        self.assertIn("run ledger", out)
        self.assertIn("ledger append : dry-run/no-live", out)

    def test_ship_live_json_allows_ready_issue_after_consent(self):
        import tempfile
        body = (
            "## Problem\nAgents need issue readiness.\n\n"
            "## Deliverable\nExpose intake status.\n\n"
            "## Acceptance criteria\n"
            "- Live preflight continues for ready issues.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--live", "--json", "--issue-title", "Add intake",
                              "--issue-body", body,
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["issue_intake"]["status"], "ready")
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

    def test_implement_live_json_blocks_non_ready_issue(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json",
                              "--issue-title", "Ambiguous implement",
                              "--issue-body", "TBD.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["contract"]["issue_intake"]["status"], "needs-input")
        self.assertIn("result", data)

    def test_implement_live_json_allows_ready_issue(self):
        import tempfile
        body = (
            "## Problem\nImplementers need intake context.\n\n"
            "## Deliverable\nExpose readiness to implement.\n\n"
            "## Acceptance criteria\n"
            "- Ready implement preflight succeeds.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json",
                              "--issue-title", "Implement intake",
                              "--issue-body", body,
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["issue_intake"]["status"], "ready")
        self.assertIn("result", data)

    def test_implement_live_human_blocks_non_ready_issue(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live",
                              "--issue-title", "Ambiguous implement",
                              "--issue-body", "TBD.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        self.assertIn("issue intake: needs-input", err)
        self.assertIn("question:", err)

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

    def test_work_block_json_contract_surfaces_shared_queue_primitive(self):
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
            rc, out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                              "146", "172", "--max", "2", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "work-block")
        self.assertEqual(
            data["contract"]["workflow_profile"]["profile"],
            "session-work-block-daytime",
        )
        self.assertEqual(data["result"]["target"], "issues #146, #172 (max 2)")
        self.assertEqual(data["result"]["session"]["work_block"]["mode"], "daytime")
        self.assertTrue(
            data["result"]["session"]["daytime"]["ship_handoff"]
            ["refreshes_readiness_between_issues"]
        )
        self.assertIn("pr_open_not_merged",
                      data["result"]["session"]["work_block"]
                      ["final_report"]["outcome_buckets"])

    def test_work_block_queue_selector_target(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                              "--queue", "priority", "--max", "3", "--target",
                              "daytime block", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["result"]["target"], "queue priority (max 3) (daytime block)")

    def test_work_block_queue_selector_without_max(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                              "--queue", "priority", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["result"]["target"], "queue priority")

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

    def test_wrap_work_block_and_overnight_human_output(self):
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
            rc_work, work_out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                                        "146", "--root", d])
            rc2, overnight_out, _ = run(["overnight", str(PROJECTS / "example-flutter.yaml"),
                                         "2", "--root", d])
        self.assertEqual(rc, 0)
        self.assertEqual(rc_work, 0)
        self.assertEqual(rc2, 0)
        self.assertIn("keel wrap", wrap_out)
        self.assertIn("linked required=True", wrap_out)
        self.assertIn("ready PR", wrap_out)
        self.assertIn("keel work-block", work_out)
        self.assertIn("ship per issue", work_out)
        self.assertIn("needs-input", work_out)
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
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "explicit", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, out, _ = run(["init", "--root", d, "--wizard"])
            self.assertEqual(rc, 0)
            written = (Path(d) / ".keel" / "project.yaml").read_text()
            import yaml

            config = yaml.safe_load(written)
            self.assertEqual(config["base_branch"], "develop")
            self.assertEqual(config["merge_window"], "09:00-18:00")
            self.assertEqual(config["consent_mode"], "explicit")
            # validates
            vrc, _, _ = run(["validate", str(Path(d) / ".keel" / "project.yaml")])
            self.assertEqual(vrc, 0)

    def test_wizard_rejects_invalid_consent_mode_before_write(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "maybe", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, _, err = run(["init", "--root", d, "--wizard"])
            self.assertEqual(rc, 1)
            self.assertIn("unknown consent mode", err)
            self.assertFalse((Path(d) / ".keel" / "project.yaml").exists())


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
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "explicit", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, out, err = run(["setup", "--root", d, "--wizard"])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel setup wizard", out)
            written = (Path(d) / ".keel/project.yaml").read_text()
            import yaml

            config = yaml.safe_load(written)
            self.assertEqual(config["base_branch"], "develop")
            self.assertEqual(config["merge_window"], "09:00-18:00")
            self.assertEqual(config["consent_mode"], "explicit")

    def test_wizard_rejects_invalid_consent_mode_before_setup_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "maybe", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, _, err = run(["setup", "--root", d, "--wizard"])
            self.assertEqual(rc, 1)
            self.assertIn("unknown consent mode", err)
            self.assertFalse((Path(d) / ".keel" / "project.yaml").exists())

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


class TestAdapterStatusOrphans(unittest.TestCase):
    def _seed_orphans(self, d):
        root = Path(d)
        install.install_all(root)
        # class (a): a stale-marker skill for a removed command.
        stale = root / ".agents/skills/keel-ship-v2"
        stale.mkdir(parents=True)
        ship = (install.ADAPTERS / "ship.md").read_text(encoding="utf-8")
        (stale / "SKILL.md").write_text(
            install._with_marker("skills", "ship-v2", ship, ship), encoding="utf-8")
        # class (b): a marker-less plugin command body.
        (root / "commands").mkdir(exist_ok=True)
        (root / "commands" / "mystery.md").write_text("# mystery\n", encoding="utf-8")
        return root

    def test_reports_stale_marker_orphan_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["adapter-status", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("orphan", out)
            self.assertIn("ship-v2", out)
            # marker-less surface stays silent without the opt-in flag.
            self.assertNotIn("mystery.md", out)
            self.assertIn("--include-unmanaged", out)

    def test_include_unmanaged_flag_reports_marker_less(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            self.assertIn("unmanaged", out)
            self.assertIn("mystery.md", out)
            self.assertIn("advisory only", out)
            self.assertNotIn("pass --include-unmanaged", out)

    def test_json_output_includes_orphans(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["adapter-status", "all", "--root", d,
                              "--include-unmanaged", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertIn("adapters", payload)
            self.assertIn("orphans", payload)
            cats = {o["category"] for o in payload["orphans"]}
            self.assertEqual(cats, {"orphan", "unmanaged"})

    def test_clean_project_reports_no_orphans(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(Path(d))
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            self.assertNotIn("orphan", out)
            self.assertNotIn("unmanaged", out)

    def test_declared_project_only_command_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all(root)
            (root / "commands").mkdir(exist_ok=True)
            (root / "commands" / "house-rules.md").write_text("# house\n", encoding="utf-8")
            keel_dir = root / ".keel"
            keel_dir.mkdir()
            (keel_dir / "project.yaml").write_text(
                "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                "policy_pack:\n  name: tmp\n"
                "  project_commands:\n"
                "    house-rules:\n      command: .keel/commands/house-rules\n",
                encoding="utf-8",
            )
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            self.assertNotIn("house-rules.md", out)

    def test_project_only_invalid_config_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all(root)
            (root / "commands").mkdir(exist_ok=True)
            (root / "commands" / "mystery.md").write_text("# mystery\n", encoding="utf-8")
            keel_dir = root / ".keel"
            keel_dir.mkdir()
            # parses as YAML but fails schema validation → ConfigError → fail-soft.
            (keel_dir / "project.yaml").write_text("not_a_keel: config\n", encoding="utf-8")
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            # invalid config → empty project-only set, so the marker-less file still surfaces.
            self.assertIn("mystery.md", out)

    def test_sync_prints_orphan_heads_up(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["sync", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("run keel adapter-status for details", out)

    def test_sync_clean_project_has_no_heads_up(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(Path(d))
            rc, out, _ = run(["sync", "--root", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("run keel adapter-status for details", out)


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
                                 "claim", "release", "merge", "worktree-remove",
                                 "implement", "ci-check", "morning", "capabilities",
                                 "wrap", "overnight", "init",
                                 "setup",
                                 "install-adapter",
                                 "adapter-status", "update-adapter", "sync", "project-commands",
                                 "install-legacy-wrappers", "post-comment"})
        # ship-v2 was removed in favour of the ship --compound profile flag.
        self.assertNotIn("ship-v2", set(actions[0].choices))


class TestPostComment(unittest.TestCase):
    def test_post_comment_reports_missing_and_invalid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad_config = f.name
        body = _body_file("<!-- keel.issue-update.v1 -->\n")

        rc_missing, _, err_missing = run([
            "post-comment", "missing.yaml",
            "--target", "issue:1",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        rc_bad, _, err_bad = run([
            "post-comment", bad_config,
            "--target", "issue:1",
            "--artifact", "issue-update",
            "--body-file", body,
        ])

        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)
        self.assertEqual(rc_bad, 1)
        self.assertIn("missing required", err_bad)

    def test_post_comment_rejects_invalid_target_and_unreadable_body(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")
        rc_target, _, err_target = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "comment:1",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        rc_body, _, err_body = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "issue:1",
            "--artifact", "issue-update",
            "--body-file", "/tmp/keel-missing-body.md",
        ])

        self.assertEqual(rc_target, 1)
        self.assertIn("issue:<number> or pr:<number>", err_target)
        self.assertEqual(rc_body, 1)
        self.assertIn("cannot read --body-file", err_body)

    def test_post_comment_rejects_missing_marker(self):
        body = _body_file("## Missing marker\n")
        rc, _, err = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "issue:247",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("must contain marker", err)
        self.assertIn("keel.issue-update.v1", err)

    def test_post_comment_rejects_literal_body_file_placeholder(self):
        body = _body_file("@/tmp/report.md <!-- keel.issue-update.v1 -->")
        rc, _, err = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "issue:247",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("literal @/path", err)

    def test_post_comment_blocks_missing_capability_and_missing_owner_repo(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")
        missing_report = runtime.CapabilityReport((
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=missing_report):
            rc_cap, _, err_cap = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:1",
                "--artifact", "issue-update",
                "--body-file", body,
            ])

        config = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with patch("keel.cli.runtime.detect", return_value=_merge_capability_report()):
            rc_owner, _, err_owner = run([
                "post-comment", config,
                "--target", "issue:1",
                "--artifact", "issue-update",
                "--body-file", body,
            ])

        self.assertEqual(rc_cap, 1)
        self.assertIn("missing required", err_cap)
        self.assertEqual(rc_owner, 1)
        self.assertIn("must define owner and repo", err_owner)

    def test_post_comment_reports_comment_list_failure(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")

        def failing_list(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return Namespace(ok=False, output="rate limited")
            return Namespace(ok=False, output=f"unexpected {argv}")

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=failing_list),
        ):
            rc, _, err = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
            ])
        self.assertEqual(rc, 1)
        self.assertIn("rate limited", err)

    def test_post_comment_posts_marked_body_via_selected_transport(self):
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return Namespace(ok=True, output="[]")
            if argv[:3] == ["gh", "api", "repos/berkayturanci/keel/issues/247/comments"]:
                self.assertIn("-f", argv)
                body_arg = next(item for item in argv if item.startswith("body="))
                self.assertIn("keel.issue-update.v1", body_arg)
                self.assertNotIn("--body", argv)
                return Namespace(ok=True, output=json.dumps({
                    "id": 42,
                    "html_url": "https://github.example/comment/42",
                }))
            return Namespace(ok=False, output=f"unexpected {argv}")

        body = _body_file("<!-- keel.issue-update.v1 -->\n\nrun-id: abc\n")
        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, out, err = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
                "--json",
            ])

        self.assertEqual((rc, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["action"], "posted")
        self.assertEqual(data["transport"], "gh")
        self.assertEqual(data["comment_id"], 42)
        self.assertTrue(calls)

    def test_post_comment_reports_mutation_failure_and_non_json_success(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")

        def failing_post(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return Namespace(ok=True, output="[]")
            return Namespace(ok=False, output="")

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=failing_post),
            patch("keel.github.run_argv", side_effect=failing_post),
        ):
            rc_fail, _, err_fail = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
            ])

        def text_post(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return Namespace(ok=True, output="[]")
            return Namespace(ok=True, output="created")

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=text_post),
            patch("keel.github.run_argv", side_effect=text_post),
        ):
            rc_text, out_text, err_text = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
                "--json",
            ])

        def list_response_post(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return Namespace(ok=True, output="[]")
            return Namespace(ok=True, output="[]")

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=list_response_post),
            patch("keel.github.run_argv", side_effect=list_response_post),
        ):
            rc_list, out_list, err_list = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
                "--json",
            ])

        self.assertEqual(rc_fail, 1)
        self.assertIn("gh comment mutation failed", err_fail)
        self.assertEqual((rc_text, err_text), (0, ""))
        self.assertEqual(json.loads(out_text)["action"], "posted")
        self.assertEqual((rc_list, err_list), (0, ""))
        self.assertEqual(json.loads(out_list)["action"], "posted")

    def test_post_comment_edits_existing_same_run_comment(self):
        existing = [{
            "id": 99,
            "body": "<!-- keel.closure-comment.v1 -->\nrun-id: run-1\nold",
        }]
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return Namespace(ok=True, output=json.dumps([existing]))
            if argv[:3] == ["gh", "api", "repos/berkayturanci/keel/issues/comments/99"]:
                self.assertIn("PATCH", argv)
                return Namespace(ok=True, output=json.dumps({"id": 99}))
            return Namespace(ok=False, output=f"unexpected {argv}")

        body = _body_file("<!-- keel.closure-comment.v1 -->\n\nrun-id: run-1\nnew\n")
        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, out, err = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "pr:123",
                "--artifact", "closure-comment",
                "--body-file", body,
                "--run-id", "run-1",
                "--json",
            ])

        self.assertEqual((rc, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["action"], "edited")
        self.assertEqual(data["comment_id"], 99)

    def test_post_comment_dry_run_and_bad_existing_comment_id(self):
        body = _body_file("<!-- keel.closure-comment.v1 -->\n\nrun-id: run-1\n")
        existing = [{"id": "bad", "body": "<!-- keel.closure-comment.v1 -->\nrun-id: run-1"}]

        def fake_list(argv, **_kwargs):
            self.assertEqual(argv[:4], ["gh", "api", "--paginate", "--slurp"])
            return Namespace(ok=True, output=json.dumps([existing]))

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=fake_list),
        ):
            rc_dry, out_dry, err_dry = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "pr:123",
                "--artifact", "closure-comment",
                "--body-file", body,
                "--run-id", "run-1",
                "--dry-run",
            ])
            rc_bad, _, err_bad = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "pr:123",
                "--artifact", "closure-comment",
                "--body-file", body,
                "--run-id", "run-1",
            ])

        self.assertEqual((rc_dry, err_dry), (0, ""))
        self.assertIn("keel post-comment", out_dry)
        self.assertIn("comment       : bad", out_dry)
        self.assertEqual(rc_bad, 1)
        self.assertIn("missing an integer id", err_bad)

    def test_post_comment_match_helpers_cover_skip_paths(self):
        comments = [
            {"id": 1, "body": ""},
            {"id": 2, "body": "<!-- keel.issue-update.v1 -->\nrun-id: other"},
            {"id": 3, "body": "<!-- keel.issue-update.v1 -->\n<!-- keel.run-id: abc -->"},
        ]
        self.assertIsNone(cli._find_comment_match(
            comments, marker="<!-- keel.issue-update.v1 -->", run_id=None
        ))
        match = cli._find_comment_match(
            comments, marker="<!-- keel.issue-update.v1 -->", run_id="abc"
        )
        self.assertEqual(match["id"], 3)
        payload = {"target": "raw", "artifact": "issue-update", "transport": "gh"}
        out, err = io.StringIO(), io.StringIO()
        args = Namespace(json=False)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli._finish_post_comment(args, payload, code=0)
        self.assertEqual(rc, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("raw", out.getvalue())


def _merge_capability_report():
    return runtime.CapabilityReport((
        runtime.Capability("shell", True, "ok", "test"),
        runtime.Capability("git", True, "ok", "test"),
        runtime.Capability("worktree", True, "ok", "test"),
        runtime.Capability("gh", True, "ok", "test"),
        runtime.Capability("gh-auth", True, "ok", "test"),
    ))


def _body_file(text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
        return f.name


def _merge_args(*, root: str | None = None, json_out: bool = False, dry_run: bool = False):
    argv = [
        "merge", str(PROJECTS / "keel.yaml"),
        "--root", root or str(REPO_ROOT),
        "--pr", "123",
        "--approve-scope", "filesystem,git,github",
        "--operator", "tester",
    ]
    if json_out:
        argv.append("--json")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _json_result(payload: dict):
    return Namespace(ok=True, output=json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
