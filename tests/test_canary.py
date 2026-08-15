"""Unit tests for Keel Canary & Automated Rollback Guard."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from keel.canary import (
    CanaryResult,
    RollbackResult,
    execute_rollback,
    render_canary_result,
    render_rollback_result,
    run_canary_guard,
)
from keel.cli import main
from keel.runner import CommandResult

TEST_CONFIG_YAML = """extends: keel
core_version: '^1.0'
owner: test
repo: test
base_branch: main
knobs:
  build_gate_cmd: make test
"""


class TestCanaryPureLogic(unittest.TestCase):
    def test_canary_result_serialization_and_rendering(self):
        res_pass = CanaryResult(
            target="PR #100",
            passed=True,
            status="healthy",
            health_output="all 50 tests passed",
            reverted=False,
            details="verification passed",
        )
        d = res_pass.to_dict()
        self.assertEqual(d["status"], "healthy")
        self.assertTrue(d["passed"])

        rendered_pass = render_canary_result(res_pass)
        self.assertIn("keel canary-guard — target: PR #100", rendered_pass)
        self.assertIn("status        : healthy ✓", rendered_pass)
        self.assertIn("reverted      : no", rendered_pass)
        self.assertIn("all 50 tests passed", rendered_pass)

        res_fail_reverted = CanaryResult(
            target="commit-abc",
            passed=False,
            status="regression_detected",
            health_output="failure in auth_test",
            reverted=True,
            revert_commit="commit-def",
            details="reverted automatically",
        )
        rendered_fail = render_canary_result(res_fail_reverted)
        self.assertIn("status        : regression_detected ❌", rendered_fail)
        self.assertIn("reverted      : yes (commit-def)", rendered_fail)

        res_reverted_no_sha = CanaryResult(
            target="commit-abc",
            passed=False,
            status="regression_detected",
            health_output="",
            reverted=True,
            revert_commit=None,
        )
        self.assertIn("reverted      : yes", render_canary_result(res_reverted_no_sha))

    def test_rollback_result_serialization_and_rendering(self):
        res_ok = RollbackResult(target_sha="sha123", success=True, revert_sha="sha456")
        d_ok = res_ok.to_dict()
        self.assertEqual(d_ok["revert_sha"], "sha456")
        rendered_ok = render_rollback_result(res_ok)
        self.assertIn("keel rollback — target: sha123", rendered_ok)
        self.assertIn("status        : success ✓", rendered_ok)
        self.assertIn("revert commit : sha456", rendered_ok)

        res_fail = RollbackResult(
            target_sha="sha123", success=False, revert_sha=None, error="merge conflict on revert"
        )
        rendered_fail = render_rollback_result(res_fail)
        self.assertIn("status        : failed ❌", rendered_fail)
        self.assertIn("error         : merge conflict on revert", rendered_fail)


class TestCanaryThinIO(unittest.TestCase):
    def test_execute_rollback_success_merge_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "revert" in cmd:
                    return CommandResult(ok=True, code=0, output="reverted")
                if "rev-parse" in cmd:
                    return CommandResult(ok=True, code=0, output="revert-sha-999\n")
                return CommandResult(ok=True, code=0, output="ok")

            res = execute_rollback("merge-sha-1", root=p_root, runner=mock_runner)
            self.assertTrue(res.success)
            self.assertEqual(res.revert_sha, "revert-sha-999")

    def test_execute_rollback_success_fallback_single_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "-m" in cmd:
                    return CommandResult(ok=False, code=1, output="commit is not a merge")
                if "revert" in cmd:
                    return CommandResult(ok=True, code=0, output="reverted single commit")
                if "rev-parse" in cmd:
                    return CommandResult(ok=False, code=1, output="")
                return CommandResult(ok=True, code=0, output="ok")

            res = execute_rollback("single-sha-1", root=p_root, runner=mock_runner)
            self.assertTrue(res.success)
            self.assertIsNone(res.revert_sha)

    def test_execute_rollback_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)

            def mock_fail_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "revert" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict on revert")
                return CommandResult(ok=True, code=0, output="aborted")

            res = execute_rollback("bad-sha", root=p_root, runner=mock_fail_runner)
            self.assertFalse(res.success)
            self.assertIn("conflict on revert", res.error)

    def test_run_canary_guard_healthy_pr_and_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / ".keel" / "project.yaml"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(TEST_CONFIG_YAML, encoding="utf-8")

            def mock_healthy_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=True, code=0, output="all checks green")

            res_pr = run_canary_guard(
                str(cfg_path),
                pr_number=42,
                root=tmpdir,
                runner=mock_healthy_runner,
            )
            self.assertTrue(res_pr.passed)
            self.assertEqual(res_pr.target, "PR #42")
            self.assertEqual(res_pr.status, "healthy")

            res_commit = run_canary_guard(
                str(cfg_path),
                commit_sha="sha-abc",
                root=tmpdir,
                runner=mock_healthy_runner,
            )
            self.assertTrue(res_commit.passed)
            self.assertEqual(res_commit.target, "sha-abc")

    def test_run_canary_guard_failure_without_revert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / ".keel" / "project.yaml"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(TEST_CONFIG_YAML, encoding="utf-8")

            def mock_unhealthy_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=False, code=2, output="server crash")

            res = run_canary_guard(
                str(cfg_path),
                root=tmpdir,
                auto_revert=False,
                runner=mock_unhealthy_runner,
            )
            self.assertFalse(res.passed)
            self.assertEqual(res.status, "regression_detected")
            self.assertFalse(res.reverted)

    def test_run_canary_guard_failure_with_auto_revert_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / ".keel" / "project.yaml"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(TEST_CONFIG_YAML, encoding="utf-8")

            # Auto revert success
            def mock_revert_ok_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "sh" in cmd:
                    return CommandResult(ok=False, code=1, output="500 internal error")
                if "rev-parse" in cmd:
                    return CommandResult(ok=True, code=0, output="revert-sha-123\n")
                return CommandResult(ok=True, code=0, output="reverted")

            res_ok = run_canary_guard(
                str(cfg_path),
                commit_sha="bad-merge-sha",
                root=tmpdir,
                auto_revert=True,
                runner=mock_revert_ok_runner,
            )
            self.assertFalse(res_ok.passed)
            self.assertTrue(res_ok.reverted)
            self.assertEqual(res_ok.revert_commit, "revert-sha-123")

            # Auto revert rollback failure
            def mock_revert_fail_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "sh" in cmd:
                    return CommandResult(ok=False, code=1, output="failed")
                if "revert" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            res_fail = run_canary_guard(
                str(cfg_path),
                commit_sha="bad-merge-sha",
                root=tmpdir,
                auto_revert=True,
                runner=mock_revert_fail_runner,
            )
            self.assertFalse(res_fail.passed)
            self.assertFalse(res_fail.reverted)
            self.assertIn("rollback attempt failed", res_fail.details)


class TestCanaryCLI(unittest.TestCase):
    def test_cli_canary_and_rollback_commands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / ".keel" / "project.yaml"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(TEST_CONFIG_YAML, encoding="utf-8")

            # Invalid config returns 1
            code_bad = main(["canary", "nonexistent.yaml"])
            self.assertEqual(code_bad, 1)

            # Successful canary dry run
            buf = io.StringIO()
            with redirect_stdout(buf):
                code_canary = main(["canary", str(cfg_path), "--health-cmd", "true"])
            self.assertEqual(code_canary, 0)
            self.assertIn("keel canary-guard", buf.getvalue())

            # Canary JSON output
            buf_json = io.StringIO()
            with redirect_stdout(buf_json):
                code_json = main(["canary", str(cfg_path), "--health-cmd", "true", "--json"])
            self.assertEqual(code_json, 0)
            data = json.loads(buf_json.getvalue())
            self.assertEqual(data["status"], "healthy")

            # Failing canary
            with redirect_stdout(io.StringIO()):
                code_fail = main(["canary", str(cfg_path), "--health-cmd", "false"])
            self.assertEqual(code_fail, 1)

            # Rollback command CLI (dry run / mock)
            buf_rb = io.StringIO()
            with redirect_stdout(buf_rb):
                code_rb = main(["rollback", "bad-sha", "--root", tmpdir])
            self.assertIn("keel rollback — target: bad-sha", buf_rb.getvalue())
            self.assertEqual(code_rb, 1)  # fails since git repo not init in tmpdir

            # Rollback JSON output
            buf_rb_json = io.StringIO()
            with redirect_stdout(buf_rb_json):
                code_rb_json = main(["rollback", "bad-sha", "--root", tmpdir, "--json"])
            self.assertEqual(code_rb_json, 1)
            rb_data = json.loads(buf_rb_json.getvalue())
            self.assertEqual(rb_data["target_sha"], "bad-sha")
            self.assertFalse(rb_data["success"])

            # Successful rollback mock
            from unittest.mock import patch

            with patch("keel.canary.execute_rollback") as mock_rb:
                mock_rb.return_value = RollbackResult(
                    target_sha="good-sha", success=True, revert_sha="rev-123"
                )
                buf_ok = io.StringIO()
                with redirect_stdout(buf_ok):
                    code_ok = main(["rollback", "good-sha"])
                self.assertEqual(code_ok, 0)
                self.assertIn("status        : success ✓", buf_ok.getvalue())


if __name__ == "__main__":
    unittest.main()
