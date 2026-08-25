"""Unit tests for Keel Swarm isolated multi-worktree execution & runtime orchestration."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from keel.cli import main
from keel.runner import CommandResult
from keel.swarm import (
    IssueScope,
    SwarmPlan,
    SwarmRunState,
    SwarmWorkerStatus,
    build_swarm_plan,
    rebalance_swarm_plan,
    render_swarm_run_result,
    update_worker_state,
)
from keel.swarm_runtime import (
    build_worktree_path,
    create_swarm_worktree,
    default_runner,
    execute_cluster_worker,
    remove_swarm_worktree,
    run_swarm_orchestration,
)


class TestSwarmRuntimeHelpers(unittest.TestCase):
    def test_default_runner_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_tmp = Path(tmpdir)
            res_ok = default_runner([sys.executable, "-c", "print('swarm-ok')"], p_tmp)
            self.assertTrue(res_ok.ok)
            self.assertEqual(res_ok.code, 0)
            self.assertIn("swarm-ok", res_ok.output)

            res_fail = default_runner([sys.executable, "-c", "import sys; sys.exit(3)"], p_tmp)
            self.assertFalse(res_fail.ok)
            self.assertEqual(res_fail.code, 3)

    def test_default_runner_timeout_and_exception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_tmp = Path(tmpdir)

            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd="test", timeout=300, output="timed-out-out"
                ),
            ):
                res_to = default_runner(["any"], p_tmp)
                self.assertFalse(res_to.ok)
                self.assertEqual(res_to.code, 124)
                self.assertTrue(res_to.timed_out)
                self.assertEqual(res_to.output, "timed-out-out")

            with patch("subprocess.run", side_effect=OSError("binary not found")):
                res_err = default_runner(["any"], p_tmp)
                self.assertFalse(res_err.ok)
                self.assertEqual(res_err.code, 1)
                self.assertIn("binary not found", res_err.output)

    def test_build_worktree_path(self):
        p = build_worktree_path("swarm-100", "cluster-1-715", root="/tmp/repo")
        self.assertEqual(p, Path("/tmp/repo/.keel/worktrees/swarm-100/cluster-1-715"))

    def test_create_and_remove_swarm_worktree_mocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)
            wt_path = p_root / ".keel" / "worktrees" / "swarm-test" / "cluster-1"

            calls: list[list[str]] = []

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                calls.append(cmd)
                return CommandResult(ok=True, code=0, output="worktree added")

            ok = create_swarm_worktree(p_root, wt_path, "swarm/branch-1", runner=mock_runner)
            self.assertTrue(ok)
            self.assertEqual(len(calls), 1)
            self.assertIn("worktree", calls[0])

            # Remove success
            ok_rem = remove_swarm_worktree(p_root, wt_path, runner=mock_runner)
            self.assertTrue(ok_rem)
            self.assertEqual(len(calls), 2)
            self.assertIn("remove", calls[1])

            # Remove fail with directory cleanup
            wt_path.mkdir(parents=True, exist_ok=True)

            def mock_fail_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=False, code=1, output="git error")

            ok_fallback = remove_swarm_worktree(p_root, wt_path, runner=mock_fail_runner)
            self.assertTrue(ok_fallback)
            self.assertFalse(wt_path.exists())

    def test_execute_cluster_worker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)
            wt_dir = p_root / "wt"
            wt_dir.mkdir(parents=True, exist_ok=True)

            calls: list[list[str]] = []

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                calls.append(cmd)
                return CommandResult(ok=True, code=0, output='{"decision": "MERGE"}')

            res = execute_cluster_worker(
                ".keel/project.yaml",
                715,
                p_root,
                wt_dir,
                dry_run=True,
                role="core",
                extra_args=["--jury"],
                runner=mock_runner,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["issue"], 715)
            self.assertIn("--dry-run", calls[0])
            self.assertIn("--jury", calls[0])


class TestSwarmOrchestration(unittest.TestCase):
    def test_orchestration_success_and_fail_soft_rebalance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="Task A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="Task B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-orch-test")

            # Mock runner that succeeds for 101 and fails for 102
            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "101" in cmd:
                    return CommandResult(ok=True, code=0, output="success")
                return CommandResult(ok=False, code=1, output="test error in 102")

            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                max_workers=2,
                runner=mock_runner,
                create_worktrees=False,
            )

            self.assertEqual(result.swarm_id, "swarm-orch-test")
            self.assertEqual(result.passed_count, 1)
            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.status, "partial_failure")
            self.assertEqual(len(result.wave_results), 1)

            rendered = render_swarm_run_result(result)
            self.assertIn("keel swarm run — swarm-orch-test", rendered)
            self.assertIn("partial_failure", rendered)

    def test_orchestration_all_passed_live_worktrees(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=201, title="Task 1", predicted_files=("src/1.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-all-pass")

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "worktree" in cmd and "add" in cmd:
                    wt_dir = Path(cmd[5])
                    wt_dir.mkdir(parents=True, exist_ok=True)
                return CommandResult(ok=True, code=0, output="passed")

            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                max_workers=1,
                runner=mock_runner,
                create_worktrees=True,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.passed_count, 1)
            self.assertEqual(result.failed_count, 0)

    def test_orchestration_fails_when_worktree_creation_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=301, title="Task Worktree Fail", predicted_files=("src/fail.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-fail-wt")

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "worktree" in cmd:
                    return CommandResult(ok=False, code=1, output="git worktree add failed")
                return CommandResult(ok=True, code=0, output="passed")

            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                max_workers=1,
                runner=mock_runner,
                create_worktrees=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failed_count, 1)
            cluster_res = result.wave_results[0]["cluster_results"]
            first_val = list(cluster_res.values())[0]
            self.assertFalse(first_val["ok"])
            self.assertIn("failed to create isolated worktree", first_val["output"])

    def test_orchestration_empty_plan_and_empty_wave(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from keel.swarm import SwarmWave

            empty_wave = SwarmWave(
                wave_index=1,
                mode="orthogonal_parallel",
                eligible_direct_landing=True,
                clusters=(),
            )
            plan = SwarmPlan(
                swarm_id="swarm-empty",
                total_issues=0,
                waves=(empty_wave,),
            )
            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
            )
            self.assertEqual(result.status, "success")
            self.assertEqual(result.total_workers, 0)

    def test_orchestration_rebalance_drops_subsequent_wave_on_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from keel.swarm import SwarmCluster, SwarmWave

            c1 = SwarmCluster(cluster_id="c1", issues=(101,), role="core", combined_scope=("a.py",))
            c2 = SwarmCluster(cluster_id="c2", issues=(101,), role="core", combined_scope=("a.py",))
            w1 = SwarmWave(
                wave_index=1,
                mode="orthogonal_parallel",
                eligible_direct_landing=True,
                clusters=(c1,),
            )
            w2 = SwarmWave(
                wave_index=2,
                mode="orthogonal_parallel",
                eligible_direct_landing=True,
                clusters=(c2,),
            )
            plan = SwarmPlan(
                swarm_id="swarm-rebalance",
                total_issues=2,
                waves=(w1, w2),
            )

            mock_fail = {"ok": False, "issue": 101, "role": "core", "code": 1, "output": "fail"}
            with patch("keel.swarm_runtime.execute_cluster_worker", return_value=mock_fail):
                result = run_swarm_orchestration(
                    plan,
                    ".keel/project.yaml",
                    root=tmpdir,
                    dry_run=True,
                )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failed_count, 1)
            # Second wave had only c2 (issue 101), which was pruned by rebalance, so only 1 wave ran
            self.assertEqual(len(result.wave_results), 1)


class TestSwarmPureStateHelpers(unittest.TestCase):
    def test_rebalance_and_update_worker_state(self):
        s1 = IssueScope(issue=1, title="A", predicted_files=("src/a.py",))
        s2 = IssueScope(issue=2, title="B", predicted_files=("src/a.py",))
        plan = build_swarm_plan([s1, s2], swarm_id="swarm-rebal")
        self.assertEqual(len(plan.waves), 2)

        rebalanced = rebalance_swarm_plan(plan, failed_issue=1)
        self.assertEqual(len(rebalanced.waves), 1)
        self.assertEqual(rebalanced.waves[0].clusters[0].issues, (2,))

        # update_worker_state matching and non-matching

        w1 = SwarmWorkerStatus(cluster_id="c1", issue=1, role="core")
        w2 = SwarmWorkerStatus(cluster_id="c2", issue=2, role="docs")
        st = SwarmRunState(swarm_id="s1", total_workers=2, workers=(w1, w2))
        st_updated = update_worker_state(st, "c1", status="passed")
        self.assertEqual(st_updated.workers[0].status, "passed")
        self.assertEqual(st_updated.workers[1].status, "queued")


class TestSwarmRunCLI(unittest.TestCase):
    def test_swarm_run_cli_missing_and_invalid_config(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["swarm-run", "nonexistent.yaml"])
        self.assertEqual(code, 1)
        self.assertIn("no such config", buf.getvalue())

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("invalid_root_key: true\n")
            path = tf.name

        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                code = main(["swarm-run", path])
            self.assertEqual(code, 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_swarm_run_cli_dry_run_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Run dry run with mock runner
            mock_res = {"ok": True, "issue": 101, "role": "core", "code": 0, "output": "ok"}
            with patch("keel.swarm_runtime.execute_cluster_worker", return_value=mock_res):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main(
                        [
                            "swarm-run",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issues",
                            "#101,invalid,102,102",
                            "--swarm-id",
                            "swarm-cli-test",
                            "--tree",
                        ]
                    )
                self.assertEqual(code, 0)
                out = buf.getvalue()
                self.assertIn("Keel Swarm Plan — swarm-cli-test", out)
                self.assertIn("keel swarm run — swarm-cli-test", out)

                # No issues passed (empty plan)
                buf_empty = io.StringIO()
                with redirect_stdout(buf_empty):
                    code_empty = main(
                        [
                            "swarm-run",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                        ]
                    )
                self.assertEqual(code_empty, 0)

                # JSON output
                buf_json = io.StringIO()
                with redirect_stdout(buf_json):
                    code_json = main(
                        [
                            "swarm-run",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue",
                            "101",
                            "--json",
                        ]
                    )
                self.assertEqual(code_json, 0)
                data = json.loads(buf_json.getvalue())
                self.assertEqual(data["status"], "success")

    def test_swarm_run_cli_single_issue_from_flags_and_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_fail = {
                "ok": False,
                "issue": 1,
                "role": "core",
                "code": 1,
                "output": "failed",
            }
            with patch("keel.swarm_runtime.execute_cluster_worker", return_value=mock_fail):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main(
                        [
                            "swarm-run",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue-title",
                            "Failing task",
                            "--issue-body",
                            "Details here",
                        ]
                    )
                self.assertEqual(code, 1)
                self.assertIn("status        : failed", buf.getvalue())

    def test_swarm_run_cli_partial_failure_returns_exit_code_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:

            def mock_worker(*args, **kwargs):
                issue = kwargs.get("issue")
                if issue == 101:
                    return {"ok": True, "issue": 101, "role": "core", "code": 0, "output": "ok"}
                return {"ok": False, "issue": 102, "role": "core", "code": 1, "output": "fail"}

            with patch("keel.swarm_runtime.execute_cluster_worker", side_effect=mock_worker):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main(
                        [
                            "swarm-run",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issues",
                            "101,102",
                        ]
                    )
                self.assertEqual(code, 1)
                self.assertIn("status        : partial_failure", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
