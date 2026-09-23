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

from keel.cli import build_parser, main
from keel.runner import CommandResult
from keel.swarm import (
    CHILD_OUTPUT_TAIL_CHARS,
    IssueScope,
    SwarmPlan,
    SwarmRunState,
    SwarmWorkerStatus,
    build_swarm_plan,
    load_swarm_state,
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

    def test_a_worker_with_no_staffing_flags_runs_the_bare_ship_argv(self):
        """An unstaffed cluster hands down nothing rather than an empty flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)
            calls: list[list[str]] = []

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                calls.append(cmd)
                return CommandResult(ok=True, code=0, output="{}")

            execute_cluster_worker(
                ".keel/project.yaml",
                715,
                p_root,
                p_root / "missing",
                dry_run=False,
                extra_args=[],
                runner=mock_runner,
            )

            self.assertEqual(calls[0][-2:], ["--json", "--live"])

    def _argv(self, *, dry_run: bool) -> list[str]:
        calls: list[list[str]] = []

        def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
            calls.append(cmd)
            return CommandResult(ok=True, code=0, output="{}")

        with tempfile.TemporaryDirectory() as tmpdir:
            execute_cluster_worker(
                ".keel/project.yaml",
                7,
                Path(tmpdir),
                Path(tmpdir) / "wt",
                dry_run=dry_run,
                runner=mock_runner,
            )
        return calls[0]

    def test_a_live_worker_runs_a_live_child(self):
        """#1269: leaving out `--dry-run` never meant `--live`; every live-only path in
        `keel ship` is gated on `args.live`, so a live swarm ran dry assessments."""
        argv = self._argv(dry_run=False)
        self.assertIn("--live", argv)
        self.assertNotIn("--dry-run", argv)
        self.assertTrue(build_parser().parse_args(argv[3:]).live)

    def test_a_dry_worker_stays_dry(self):
        """The counterweight: a dry swarm never hands a child `--live`."""
        argv = self._argv(dry_run=True)
        self.assertIn("--dry-run", argv)
        self.assertNotIn("--live", argv)
        parsed = build_parser().parse_args(argv[3:])
        self.assertFalse(parsed.live)
        self.assertTrue(parsed.dry_run)


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
                base_branch="main",
            )

            self.assertEqual(result.swarm_id, "swarm-orch-test")
            self.assertEqual(result.passed_count, 1)
            self.assertEqual(result.failed_count, 1)
            self.assertEqual(result.status, "partial_failure")
            self.assertEqual(len(result.wave_results), 1)

            rendered = render_swarm_run_result(result)
            self.assertIn("keel swarm run — swarm-orch-test", rendered)
            self.assertIn("partial_failure", rendered)

    def test_a_worker_that_raises_is_a_failed_cluster_not_a_failed_run(self):
        """#1271: `future.result()` re-raised a worker's exception unguarded, so one bad
        cluster ended the run, discarded the others' results and left them `running`
        in the state file, with no way for swarm-status to tell they were dead."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=301, title="Fine", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=302, title="Raises", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-raises")
            removed: list[str] = []

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "worktree" in cmd and "add" in cmd:
                    Path(cmd[5]).mkdir(parents=True, exist_ok=True)
                if "worktree" in cmd and "remove" in cmd:
                    removed.append(cmd[-1])
                if "ship" in cmd and "302" in cmd:
                    raise RuntimeError("boom in 302")
                return CommandResult(ok=True, code=0, output="passed")

            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    result = run_swarm_orchestration(
                        plan,
                        ".keel/project.yaml",
                        root=tmpdir,
                        dry_run=False,
                        max_workers=2,
                        runner=mock_runner,
                        create_worktrees=True,
                        base_branch="main",
                    )
            except RuntimeError as exc:
                raise AssertionError(f"a worker's exception ended the run: {exc}") from exc
            # The traceback is kept: the failure may be keel's own bug.
            self.assertIn("Traceback", err.getvalue())
            self.assertIn("RuntimeError: boom in 302", err.getvalue())

            self.assertEqual((result.passed_count, result.failed_count), (1, 1))
            self.assertEqual(result.status, "partial_failure")
            outputs = [r["output"] for r in result.wave_results[0]["cluster_results"].values()]
            self.assertTrue(any("worker raised RuntimeError: boom in 302" in o for o in outputs))
            state = load_swarm_state("swarm-raises", root=tmpdir)
            assert state is not None
            self.assertNotIn("running", {w.status for w in state.workers})
            self.assertEqual(len(removed), 2, "the raising worker's worktree must be removed too")

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
                base_branch="main",
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
                base_branch="main",
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
                base_branch="main",
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
                    base_branch="main",
                )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failed_count, 1)
            # Second wave had only c2 (issue 101), which was pruned by rebalance, so only 1 wave ran
            self.assertEqual(len(result.wave_results), 1)


class TheStoredChildOutputIsBounded(unittest.TestCase):
    """#1280: every cluster's whole child stdout went into `wave_results` (re-emitted by
    `swarm-run --json`), and a failing cluster's went into the state file too."""

    def test_passing_and_failing_outputs_keep_only_the_tail(self):
        big = "noise\n" * 2000  # 12 000 chars, well over the cap
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=401, title="Passes", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=402, title="Fails", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-big")

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "401" in cmd:
                    return CommandResult(ok=True, code=0, output=big + "ALL GREEN")
                return CommandResult(ok=False, code=1, output=big + "FATAL: gate failed")

            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                max_workers=2,
                runner=mock_runner,
                create_worktrees=False,
                base_branch="main",
            )
            outputs = {
                r["issue"]: r["output"]
                for w in result.wave_results
                for r in w["cluster_results"].values()
            }
            for issue, tail in ((401, "ALL GREEN"), (402, "FATAL: gate failed")):
                with self.subTest(issue=issue):
                    self.assertLess(len(outputs[issue]), CHILD_OUTPUT_TAIL_CHARS + 200)
                    self.assertTrue(outputs[issue].endswith(tail))
                    self.assertIn("earlier chars of child output dropped", outputs[issue])

            state = load_swarm_state("swarm-big", root=tmpdir)
            assert state is not None
            failed = next(w for w in state.workers if w.issue == 402)
            self.assertLess(len(failed.details), CHILD_OUTPUT_TAIL_CHARS + 200)
            self.assertTrue(failed.details.endswith("FATAL: gate failed"))
            self.assertIn("earlier chars of child output dropped", failed.details)


class AFailedWaveDoesNotSkipTheNext(unittest.TestCase):
    """#1268: the loop walked waves by position, and rebalancing after a failure drops
    that wave from the plan — so the position counter then stepped past the next,
    unrelated wave, which never ran and stayed `queued`. A distinct issue per wave:
    reusing one issue in two waves hides the skip."""

    def test_every_later_wave_still_runs(self):
        scopes = [
            IssueScope(issue=n, title=f"T{n}", predicted_files=("src/a.py",)) for n in (1, 2, 3)
        ]
        plan = build_swarm_plan(scopes, swarm_id="swarm-skip")
        self.assertEqual([len(w.clusters) for w in plan.waves], [1, 1, 1], "fixture: one wave each")
        ran: list[int] = []

        def runner(cmd: list[str], cwd: Path) -> CommandResult:
            issue = int(cmd[cmd.index("--issue") + 1])
            ran.append(issue)
            return CommandResult(ok=issue != 1, code=0 if issue != 1 else 1, output="")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                runner=runner,
                create_worktrees=False,
                base_branch="main",
            )
            state = load_swarm_state("swarm-skip", root=tmpdir)

        self.assertEqual(ran, [1, 2, 3], "a wave was skipped after the failure")
        self.assertEqual((result.passed_count, result.failed_count), (2, 1))
        assert state is not None
        self.assertNotIn("queued", {w.status for w in state.workers})


class TestSwarmPureStateHelpers(unittest.TestCase):
    def test_a_failed_issue_leaves_no_edge_behind(self):
        """#1277: the dropped issue stayed in every survivor's depends_on_issues, in
        conflict_map and in issue_scopes, so the plan asserted a dependency on work it
        no longer held."""
        scopes = [
            IssueScope(issue=10, title="A", predicted_files=("src/a.py",)),
            IssueScope(issue=11, title="B", predicted_files=("src/a.py",)),
            IssueScope(issue=14, title="E", predicted_files=("src/a.py",)),
            IssueScope(issue=12, title="C", predicted_files=("src/c.py",)),
            IssueScope(issue=13, title="D", predicted_files=("src/c.py",)),
        ]
        plan = build_swarm_plan(scopes, swarm_id="swarm-edges")
        deps = {c.issues[0]: c.depends_on_issues for w in plan.waves for c in w.clusters}
        self.assertIn(10, deps[11], "fixture: 11 must depend on 10 to begin with")
        self.assertIn(12, deps[13], "fixture: 13 must depend on 12")

        after = rebalance_swarm_plan(plan, failed_issue=10)
        deps = {c.issues[0]: c.depends_on_issues for w in after.waves for c in w.clusters}
        self.assertNotIn(10, deps)
        self.assertNotIn(10, deps[11])
        self.assertIn(12, deps[13], "an unrelated edge must survive")
        # 14 depended on both 10 and 11: only the failed edge goes, not the list.
        self.assertIn(11, deps[14])
        self.assertNotIn(10, deps[14])
        self.assertNotIn(10, after.conflict_map)
        self.assertFalse(any(10 in others for others in after.conflict_map.values()))
        self.assertNotIn(10, after.issue_scopes)
        self.assertIn(11, after.issue_scopes)

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


class TheWorktreesBranchFromTheConfiguredBase(unittest.TestCase):
    """#1262: the worktree was always branched from `main` while landing targets
    `config.base_branch`, so a `develop` project's clusters grew on the wrong history."""

    def test_the_worktree_is_branched_from_the_base_it_is_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = build_swarm_plan(
                [IssueScope(issue=401, title="A", predicted_files=("src/a.py",))],
                swarm_id="swarm-develop",
            )
            adds: list[list[str]] = []

            def runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "worktree" in cmd and "add" in cmd:
                    adds.append(cmd)
                    Path(cmd[5]).mkdir(parents=True, exist_ok=True)
                return CommandResult(ok=True, code=0, output="ok")

            run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=runner,
                create_worktrees=True,
                base_branch="develop",
            )
            self.assertEqual(len(adds), 1)
            self.assertEqual(adds[0][-1], "develop")

    def test_there_is_no_silent_default(self):
        """Required, as it is for `land_wave_clusters`: a default is how `main` crept in."""
        plan = build_swarm_plan([], swarm_id="swarm-none")
        with self.assertRaises(TypeError):
            run_swarm_orchestration(plan, ".keel/project.yaml", dry_run=True)  # type: ignore[call-arg]

    def test_swarm_run_hands_the_configured_base_down(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            text = Path(".keel/project.yaml").read_text(encoding="utf-8")
            self.assertIn("base_branch: main\n", text)
            config = Path(tmpdir) / "project.yaml"
            config.write_text(
                text.replace("base_branch: main\n", "base_branch: develop\n"), encoding="utf-8"
            )
            with patch("keel.swarm_runtime.run_swarm_orchestration") as orchestrate:
                orchestrate.return_value.status = "success"
                orchestrate.return_value.to_dict.return_value = {}
                with redirect_stdout(io.StringIO()):
                    main(["swarm-run", str(config), "--root", tmpdir, "--issues", "7", "--json"])
            self.assertEqual(orchestrate.call_args.kwargs.get("base_branch"), "develop")


class ChildrenThatShareACheckoutRunOneAtATime(unittest.TestCase):
    """#1288: a dry run creates no worktrees, so every child runs its gate suite in the
    operator's own checkout — and they ran `--max-workers` at a time, four `.coverage`
    writers in one tree. Children without a worktree now run one at a time."""

    def _plan(self):
        return build_swarm_plan(
            [
                IssueScope(issue=n, title=f"T{n}", predicted_files=(f"src/{n}.py",))
                for n in (501, 502, 503)
            ],
            swarm_id="swarm-shared",
        )

    def test_a_dry_run_never_overlaps_two_children(self):
        import threading
        import time

        lock, active, peak = threading.Lock(), [0], [0]

        def runner(cmd: list[str], cwd: Path) -> CommandResult:
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return CommandResult(ok=True, code=0, output="ok")

        with tempfile.TemporaryDirectory() as tmpdir:
            run_swarm_orchestration(
                self._plan(),
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                max_workers=4,
                runner=runner,
                base_branch="main",
            )
        self.assertEqual(peak[0], 1)

    def test_isolated_workers_still_run_in_parallel(self):
        """The counterweight: two workers with their own worktrees must be able to
        meet — if they were serialised, the barrier would time out."""
        import threading

        barrier = threading.Barrier(2, timeout=5)

        def runner(cmd: list[str], cwd: Path) -> CommandResult:
            if "worktree" in cmd and "add" in cmd:
                Path(cmd[5]).mkdir(parents=True, exist_ok=True)
            elif "ship" in cmd:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    # Serialised: the partner never arrived. Report it as a failed
                    # cluster so the count below fails as an assertion.
                    return CommandResult(ok=False, code=1, output="ran alone")
            return CommandResult(ok=True, code=0, output="ok")

        plan = build_swarm_plan(
            [
                IssueScope(issue=n, title=f"T{n}", predicted_files=(f"src/{n}.py",))
                for n in (601, 602)
            ],
            swarm_id="swarm-isolated",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                max_workers=2,
                runner=runner,
                create_worktrees=True,
                base_branch="main",
            )
        self.assertEqual(result.passed_count, 2)


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

    def test_a_live_swarm_run_is_refused_before_anything_starts(self):
        """#1304 lead: with `--live` forwarded, every worker stops at `keel ship
        --live`'s operator-consent gate, which swarm-run cannot satisfy for a child —
        so a live run is refused up front, with the reason, instead of failing every
        cluster and leaving `swarm/<id>` branches behind."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("keel.swarm_runtime.run_swarm_orchestration") as orchestrate,
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    ["swarm-run", ".keel/project.yaml", "--root", tmpdir, "--issues", "7", "--live"]
                )

        self.assertEqual(code, 1)
        self.assertIn("swarm-run --live is refused", err.getvalue())
        self.assertIn("operator consent", err.getvalue())
        self.assertIn("issues/1281", err.getvalue())
        orchestrate.assert_not_called()
        self.assertEqual(out.getvalue(), "")

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
