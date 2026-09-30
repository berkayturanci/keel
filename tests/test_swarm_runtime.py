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

from keel import swarm_worker
from keel.cli import build_parser, main
from keel.delegate import RunPlan
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
    LiveRun,
    build_brief_path,
    build_worktree_path,
    create_swarm_worktree,
    default_runner,
    execute_cluster_worker,
    remove_swarm_worktree,
    run_swarm_orchestration,
)

#: swarm-plan/-run/-land read every named issue with `gh issue view` (#1274). The suite
#: is offline (AGENTS.md), so every test gets an unreadable issue unless it patches its
#: own; the scope then comes from the flags, or is `*`.
_ISSUE_STUB = patch(
    "keel.github.issue_facts",
    return_value=CommandResult(False, 1, "stubbed: the suite never runs gh"),
)


def setUpModule():
    _ISSUE_STUB.start()


FULL_SCOPES = ("filesystem", "git", "github")


def _clusters(plan):
    return [c for wave in plan.waves for c in wave.clusters]


class _LiveIo:
    """Recording stand-ins for a live worker's three I/O seams (#1400).

    ``implement`` records the plan it was handed, the environment and the brief, and can
    fail, or raise for a cluster whose brief names a given issue; ``push``/``open_pr``
    record their arguments and succeed unless told otherwise.
    """

    def __init__(self, *, implement_ok=True, raise_for=None, push_ok=True, pr_ok=True):
        self.implement_ok, self.raise_for = implement_ok, raise_for
        self.push_ok, self.pr_ok = push_ok, pr_ok
        self.implemented: list[tuple[RunPlan, dict, str]] = []
        self.pushes: list[tuple[str, str, str]] = []
        self.prs: list[tuple[str, str, str, str]] = []

    def implement(self, plan, env):
        brief = Path(plan.prompt_path).read_text(encoding="utf-8")
        self.implemented.append((plan, env, brief))
        if self.raise_for is not None and f"#{self.raise_for}:" in brief:
            raise RuntimeError(f"boom in {self.raise_for}")
        if not self.implement_ok:
            return {"ok": False, "error_code": "nonzero-exit", "error": "codex exited 2"}
        return {"ok": True, "text": "done"}

    def push(self, remote, commit, ref, cwd):
        self.pushes.append((remote, commit, ref))
        return CommandResult(self.push_ok, 0 if self.push_ok else 1, "" if self.push_ok else "no")

    def open_pr(self, title, body, base, head, cwd):
        self.prs.append((title, body, base, head))
        if not self.pr_ok:
            return CommandResult(False, 1, "gh: not authenticated")
        return CommandResult(True, 0, f"https://github.com/o/r/pull/{len(self.prs)}\n")


def _live(plan, root, io, *, scopes=FULL_SCOPES, clusters=None) -> LiveRun:
    root = Path(root).resolve()
    delegation = swarm_worker.ConsentDelegation(
        swarm_id=plan.swarm_id,
        clusters=tuple(c.cluster_id for c in _clusters(plan)) if clusters is None else clusters,
        scopes=tuple(scopes),
        operator="ops",
        source="flag",
        mode="explicit",
        delegated_at="2026-09-30T12:00:00Z",
        consent_record={"operator": "ops"},
    )
    dispatches = {
        c.cluster_id: RunPlan(
            provider="codex",
            vendor="codex",
            role="implement",
            transport="cli",
            prompt_path=str(build_brief_path(plan.swarm_id, c.cluster_id, root)),
            cwd=str(build_worktree_path(plan.swarm_id, c.cluster_id, root)),
            attribution={"system": "codex:gpt-5"},
        )
        for c in _clusters(plan)
    }
    return LiveRun(
        consent=delegation,
        dispatches=dispatches,
        issue_scopes=plan.issue_scopes,
        seat_sources={c.cluster_id: "flag:--delegate" for c in _clusters(plan)},
        implement=io.implement,
        push=io.push,
        open_pr=io.open_pr,
    )


class _Git:
    """A recording runner for a live worker's git and gate commands.

    ``worktree add`` makes the directory; ``status`` reports a change unless ``clean``;
    ``rev-parse`` answers ``base`` until the worktree has been committed in, then a head
    named after it; ``fail`` names a command word whose call fails.
    """

    def __init__(self, *, clean=False, fail=None, gates_ok=True):
        self.clean, self.fail, self.gates_ok = clean, fail, gates_ok
        self.calls: list[list[str]] = []
        self.committed: set[str] = set()

    def __call__(self, cmd, cwd):
        self.calls.append(list(cmd))
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[5]).mkdir(parents=True, exist_ok=True)
        if self.fail is not None and self.fail in cmd:
            return CommandResult(False, 1, f"{self.fail} failed")
        if "run-gates" in cmd:
            return CommandResult(self.gates_ok, 0 if self.gates_ok else 1, "BLOCKED - build")
        if cmd[:2] == ["git", "status"]:
            return CommandResult(True, 0, "" if self.clean else " M src/a.py\n")
        if cmd[:2] == ["git", "commit"]:
            self.committed.add(str(cwd))
        if cmd[:2] == ["git", "rev-parse"]:
            head = f"head-{Path(cwd).name}" if str(cwd) in self.committed else "base"
            return CommandResult(True, 0, head + "\n")
        return CommandResult(True, 0, "ok")

    def ran(self, word):
        return [c for c in self.calls if word in c]


def tearDownModule():
    _ISSUE_STUB.stop()


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

            git = _Git()

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "worktree" in cmd and "remove" in cmd:
                    removed.append(cmd[-1])
                return git(cmd, cwd)

            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    result = run_swarm_orchestration(
                        plan,
                        ".keel/project.yaml",
                        root=tmpdir,
                        dry_run=False,
                        live=_live(plan, tmpdir, _LiveIo(raise_for=302)),
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

            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                live=_live(plan, tmpdir, _LiveIo()),
                max_workers=1,
                runner=_Git(),
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

            io_ = _LiveIo()
            result = run_swarm_orchestration(
                plan,
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                live=_live(plan, tmpdir, io_),
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
            self.assertEqual(io_.implemented, [], "no implementer runs without a worktree")

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
        # #1276: each wave record carries the mode of the plan it ran under. Wave 2
        # depended only on the failed #1, so the rebalanced plan frees it; wave 3
        # still waits on #2.
        self.assertEqual(
            [
                (r["wave_index"], r["mode"], r["eligible_direct_landing"])
                for r in result.wave_results
            ],
            [
                (1, "orthogonal_parallel", True),
                (2, "orthogonal_parallel", True),
                (3, "sequential_dependent", False),
            ],
        )


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
                live=_live(plan, tmpdir, _LiveIo()),
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

        git = _Git()

        def runner(cmd: list[str], cwd: Path) -> CommandResult:
            if "run-gates" in cmd:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    # Serialised: the partner never arrived. Report it as a failed
                    # cluster so the count below fails as an assertion.
                    return CommandResult(ok=False, code=1, output="ran alone")
            return git(cmd, cwd)

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
                live=_live(plan, tmpdir, _LiveIo()),
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

    def test_a_live_swarm_run_without_consent_starts_nothing(self):
        """#1400: without the operator's consent a live run is refused before any issue is
        read or any worker starts, and the refusal names the scopes it is missing."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("keel.swarm_runtime.run_swarm_orchestration") as orchestrate,
            patch(
                "keel.github.issue_facts", return_value=CommandResult(False, 1, "stubbed")
            ) as read_issue,
            patch.dict(os.environ, {}, clear=False) as environ,
        ):
            for name in swarm_worker.CONSENT_ENV_VARS:
                environ.pop(name, None)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    ["swarm-run", ".keel/project.yaml", "--root", tmpdir, "--issues", "7", "--live"]
                )

        self.assertEqual(code, 1)
        self.assertIn("swarm-run --live is refused: operator consent required", err.getvalue())
        self.assertIn("Missing approved scope: filesystem, git, github", err.getvalue())
        orchestrate.assert_not_called()
        read_issue.assert_not_called()
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


class TheWorkerTimeoutFollowsTheProject(unittest.TestCase):
    """#1279 item 1: the child `keel ship` was killed after a literal 300 s, and it runs
    the gate suite in a dry run too, so a suite the project allows ten minutes failed
    every cluster with `code=124` on a dry `swarm-run`."""

    def _plan(self):
        return build_swarm_plan(
            [IssueScope(issue=601, title="T", predicted_files=("src/a.py",))],
            swarm_id="swarm-timeout",
        )

    def _config_with_budgets(self, tmpdir: str) -> Path:
        text = Path(".keel/project.yaml").read_text(encoding="utf-8")
        self.assertIn("\nknobs:\n", text)
        self.assertNotIn("gate_timeout_s", text)
        path = Path(tmpdir) / "project.yaml"
        path.write_text(
            text.replace("\nknobs:\n", "\nknobs:\n  gate_timeout_s: 900\n  jury_timeout_s: 120\n"),
            encoding="utf-8",
        )
        return path

    def _swarm_run_timeout(self, *extra: str) -> int | None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config_with_budgets(tmpdir)
            with patch("keel.swarm_runtime.run_swarm_orchestration") as orchestrate:
                orchestrate.return_value.status = "success"
                orchestrate.return_value.to_dict.return_value = {}
                with redirect_stdout(io.StringIO()):
                    main(
                        ["swarm-run", str(config), "--root", tmpdir, "--issues", "7", "--json"]
                        + list(extra)
                    )
        return orchestrate.call_args.kwargs.get("timeout_s")

    def test_the_timeout_reaches_the_child_process(self):
        """End to end, with keel's own runner: the budget is what `subprocess.run` gets."""
        seen: list[tuple[list[str], object]] = []

        def fake_run(cmd, **kwargs):
            seen.append((cmd, kwargs.get("timeout")))
            return subprocess.CompletedProcess(cmd, 0, stdout="{}")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("keel.swarm_runtime.subprocess.run", side_effect=fake_run):
                result = run_swarm_orchestration(
                    self._plan(),
                    ".keel/project.yaml",
                    root=tmpdir,
                    dry_run=True,
                    base_branch="main",
                    timeout_s=1234,
                )
        self.assertEqual(result.status, "success")
        ships = [timeout for cmd, timeout in seen if "ship" in cmd]
        self.assertEqual(ships, [1234])

    def test_the_runner_keeps_its_own_default_for_other_commands(self):
        """canary and swarm_landing call `default_runner(cmd, cwd)` for git; that stays 300."""
        with patch(
            "keel.swarm_runtime.subprocess.run",
            return_value=subprocess.CompletedProcess(["git"], 0, stdout=""),
        ) as run:
            default_runner(["git", "status"], Path("."))
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_the_default_is_the_projects_gate_plus_jury_budget(self):
        from keel import config as cfg
        from keel.swarm import worker_timeout_s

        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = cfg.load_config(str(self._config_with_budgets(tmpdir)))
        self.assertEqual(worker_timeout_s(loaded), 1020)
        self.assertEqual(self._swarm_run_timeout(), 1020)

    def test_the_flag_overrides_the_derived_default(self):
        self.assertEqual(self._swarm_run_timeout("--worker-timeout", "42"), 42)

    def test_an_invalid_worker_timeout_is_refused(self):
        parser = build_parser()
        for bad in ("0", "-5", "abc", "1.5"):
            with self.subTest(bad=bad), redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as raised:
                    parser.parse_args(["swarm-run", ".keel/project.yaml", "--worker-timeout", bad])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--worker-timeout", err.getvalue())

    def _worker(self, result: CommandResult) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            return execute_cluster_worker(
                ".keel/project.yaml",
                601,
                Path(tmpdir),
                Path(tmpdir) / "wt",
                runner=lambda cmd, cwd: result,
                timeout_s=77,
            )

    def test_a_worker_that_runs_out_is_reported_timed_out(self):
        res = self._worker(
            CommandResult(ok=False, code=124, output="gate 1 of 4 ...\n", timed_out=True)
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], 124)
        self.assertTrue(res["timed_out"])
        self.assertTrue(res["output"].startswith("gate 1 of 4 ...\n"))
        self.assertTrue(res["output"].endswith("legitimately needs longer."))
        self.assertIn("timed out after 77s", res["output"])

    def test_a_silent_timeout_still_says_why(self):
        res = self._worker(CommandResult(ok=False, code=124, output="", timed_out=True))
        self.assertTrue(res["output"].startswith("keel ship timed out after 77s"))

    def test_a_failing_child_is_not_called_a_timeout(self):
        res = self._worker(CommandResult(ok=False, code=1, output="FAIL test_x"))
        self.assertFalse(res["timed_out"])
        self.assertEqual(res["output"], "FAIL test_x")

    def test_the_state_file_records_the_timeout(self):
        def runner(cmd: list[str], cwd: Path) -> CommandResult:
            return CommandResult(ok=False, code=124, output="", timed_out=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_swarm_orchestration(
                self._plan(),
                ".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                runner=runner,
                base_branch="main",
                timeout_s=55,
            )
            state = load_swarm_state("swarm-timeout", root=tmpdir)
        cluster = next(iter(result.wave_results[0]["cluster_results"].values()))
        self.assertTrue(cluster["timed_out"])
        self.assertEqual(result.status, "failed")
        self.assertIsNotNone(state)
        self.assertIn("timed out after 55s", state.workers[0].details)


class ALiveWorkerImplementsItsCluster(unittest.TestCase):
    """#1400: a live worker is the cluster's implementer seat in the cluster's worktree,
    then a commit, the project's gates, a push and one pull request per cluster — and a
    worker that fails a stage leaves everything after it undone, with the reason."""

    def _plan(self, *issues):
        return build_swarm_plan(
            [IssueScope(issue=n, title=f"T{n}", predicted_files=(f"src/{n}.py",)) for n in issues],
            swarm_id="swarm-live",
        )

    def _run(self, plan, tmpdir, io_, git, **live):
        return run_swarm_orchestration(
            plan,
            "projects/x.yaml",
            root=tmpdir,
            dry_run=False,
            live=_live(plan, tmpdir, io_, **live),
            max_workers=2,
            runner=git,
            base_branch="main",
        )

    def _only(self, result):
        (res,) = result.wave_results[0]["cluster_results"].values()
        return res

    def test_each_cluster_is_implemented_committed_gated_pushed_and_opened(self):
        plan = self._plan(701, 702)
        io_, git = _LiveIo(), _Git()
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"KEEL_APPROVE_SCOPE": "github", "KEEL_OPERATOR": "ops"}),
        ):
            result = self._run(plan, tmpdir, io_, git)
            state = load_swarm_state("swarm-live", root=tmpdir)
            briefs_outside = all(
                not Path(p.prompt_path).is_relative_to(Path(p.cwd)) for p, _e, _b in io_.implemented
            )

        clusters = {c.cluster_id: c for c in _clusters(plan)}
        self.assertEqual(result.status, "success")
        self.assertEqual(len(clusters), 2)
        # The seat that was planned is the seat that ran, once per cluster, in its worktree.
        self.assertEqual(
            sorted(Path(p.cwd).name for p, _e, _b in io_.implemented), sorted(clusters)
        )
        self.assertTrue(briefs_outside, "a brief inside the worktree would be committed")
        for _plan, env, brief in io_.implemented:
            self.assertNotIn("KEEL_APPROVE_SCOPE", env)
            self.assertNotIn("KEEL_OPERATOR", env)
            self.assertIn("Do not commit, push, open a pull request", brief)
        commits = git.ran("commit")
        self.assertEqual(len(commits), 2)
        self.assertTrue(all("Refs #70" in c[-1] for c in commits))
        gates = git.ran("run-gates")
        self.assertEqual(len(gates), 2)
        self.assertEqual(gates[0][4], "projects/x.yaml")
        self.assertEqual(gates[0][-3:], ["--phases", "guard,test", "--defer-jury"])
        self.assertEqual(
            sorted(io_.pushes),
            sorted(
                ("origin", f"head-{cid}", f"refs/heads/swarm/swarm-live/{cid}") for cid in clusters
            ),
        )
        self.assertEqual(
            sorted((base, head) for _t, _b, base, head in io_.prs),
            sorted(("main", f"swarm/swarm-live/{cid}") for cid in clusters),
        )
        for _title, body, _base, _head in io_.prs:
            self.assertIn("delegated by `ops`", body)
            self.assertIn("seat from `flag:--delegate`", body)
        urls = sorted(r["pr_url"] for r in result.wave_results[0]["cluster_results"].values())
        self.assertEqual(urls, ["https://github.com/o/r/pull/1", "https://github.com/o/r/pull/2"])
        # The delegation is recorded in the run state, and each worker's scopes with it.
        self.assertEqual(state.consent["operator"], "ops")
        self.assertEqual(state.consent["clusters"], sorted(clusters))
        self.assertEqual({w.scopes for w in state.workers}, {FULL_SCOPES})
        self.assertEqual({(w.status, w.step) for w in state.workers}, {("passed", "s6")})
        self.assertEqual(result.consent, state.consent)
        rendered = render_swarm_run_result(result)
        self.assertIn("consent       : delegated by ops (filesystem, git, github)", rendered)
        self.assertIn("pull request https://github.com/o/r/pull/", rendered)

    def _one(self, io_, git, **live):
        plan = self._plan(711)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run(plan, tmpdir, io_, git, **live)
        return result, self._only(result)

    def test_a_failed_implementer_commits_pushes_and_opens_nothing(self):
        io_, git = _LiveIo(implement_ok=False), _Git()
        result, res = self._one(io_, git)
        self.assertEqual((result.status, res["stage"]), ("failed", "implement"))
        self.assertIn("the implementer codex:gpt-5 failed (nonzero-exit)", res["output"])
        self.assertEqual((git.ran("commit"), git.ran("run-gates")), ([], []))
        self.assertEqual((io_.pushes, io_.prs), ([], []))
        self.assertIn("stopped at implement", render_swarm_run_result(result))

    def test_a_red_gate_leaves_the_branch_unpushed(self):
        io_, git = _LiveIo(), _Git(gates_ok=False)
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "gates")
        self.assertEqual(res["commit"], "head-" + Path(io_.implemented[0][0].cwd).name)
        self.assertIn("the branch is not pushed", res["output"])
        self.assertIn("BLOCKED", res["output"])
        self.assertEqual((io_.pushes, io_.prs), ([], []))

    def test_an_implementer_that_changed_nothing_has_nothing_to_push(self):
        io_, git = _LiveIo(), _Git(clean=True)
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "implement")
        self.assertIn("changed nothing in the worktree", res["output"])
        self.assertEqual((git.ran("commit"), io_.pushes), ([], []))

    def test_an_implementer_that_committed_itself_is_pushed_as_it_is(self):
        git = _Git(clean=True)

        class Committing(_LiveIo):
            def implement(self, plan, env):
                git.committed.add(plan.cwd)
                return super().implement(plan, env)

        io_ = Committing()
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "done")
        self.assertEqual(git.ran("commit"), [], "keel adds no commit of its own")
        self.assertEqual(len(io_.pushes), 1)

    def test_a_failing_git_step_stops_at_the_commit(self):
        for word, said in (("status", "git status failed"), ("-A", "git add failed")):
            with self.subTest(step=word):
                io_ = _LiveIo()
                _result, res = self._one(io_, _Git(fail=word))
                self.assertEqual(res["stage"], "commit")
                self.assertIn(said, res["output"])
                self.assertEqual(io_.pushes, [])

    def test_a_rejected_push_opens_no_pull_request(self):
        io_ = _LiveIo(push_ok=False)
        _result, res = self._one(io_, _Git())
        self.assertEqual(res["stage"], "push")
        self.assertIn("git push origin swarm/swarm-live/", res["output"])
        self.assertEqual(io_.prs, [])

    def test_a_pull_request_that_cannot_be_opened_says_the_branch_is_pushed(self):
        io_ = _LiveIo(pr_ok=False)
        _result, res = self._one(io_, _Git())
        self.assertEqual(res["stage"], "pull_request")
        self.assertIn("is pushed, but gh pr create failed: gh: not authenticated", res["output"])
        self.assertEqual(len(io_.pushes), 1)

    def test_a_worker_never_widens_the_scopes_it_was_handed(self):
        """Handed `filesystem,git` only, the worker does not start: it could commit and
        push, but opening its pull request needs `github`, which it was not given."""
        io_, git = _LiveIo(), _Git()
        _result, res = self._one(io_, git, scopes=("filesystem", "git"))
        self.assertEqual(res["stage"], "consent")
        self.assertIn("pull_request needs the github consent scope", res["output"])
        self.assertEqual(res["scopes"], ["filesystem", "git"])
        self.assertEqual((git.calls, io_.implemented, io_.pushes, io_.prs), ([], [], [], []))

    def test_a_cluster_outside_the_delegation_is_handed_nothing(self):
        io_, git = _LiveIo(), _Git()
        _result, res = self._one(io_, git, clusters=())
        self.assertEqual((res["stage"], res["scopes"]), ("consent", []))
        self.assertEqual((git.calls, io_.implemented), ([], []))

    def test_there_is_no_live_run_without_a_delegation_or_a_worktree(self):
        plan = self._plan(721)
        with tempfile.TemporaryDirectory() as tmpdir:
            for kwargs in ({}, {"live": _live(plan, tmpdir, _LiveIo()), "create_worktrees": False}):
                with self.subTest(kwargs=sorted(kwargs)), self.assertRaises(ValueError):
                    run_swarm_orchestration(
                        plan, "p.yaml", root=tmpdir, dry_run=False, base_branch="main", **kwargs
                    )

    def test_a_stored_scope_is_only_ever_what_was_written(self):
        """A record from before #1400 has no `scopes`; a malformed one grants nothing."""
        workers = [
            {"cluster_id": "a", "issue": 1},
            {"cluster_id": "b", "issue": 2, "scopes": "github"},
            {"cluster_id": "c", "issue": 3, "scopes": [7, "git"]},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
            state_dir.mkdir(parents=True)
            (state_dir / "old.json").write_text(
                json.dumps({"swarm_id": "old", "workers": workers, "consent": "yes"}),
                encoding="utf-8",
            )
            state = load_swarm_state("old", root=tmpdir)
        self.assertEqual([w.scopes for w in state.workers], [(), (), ("git",)])
        self.assertIsNone(state.consent)

    def test_a_dry_run_dispatches_nothing(self):
        plan = self._plan(731)
        io_, git = _LiveIo(), _Git()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_swarm_orchestration(
                plan, "p.yaml", root=tmpdir, dry_run=True, runner=git, base_branch="main"
            )
            state = load_swarm_state("swarm-live", root=tmpdir)
        self.assertEqual(io_.implemented, [])
        self.assertEqual([c[3] for c in git.calls], ["ship"])
        self.assertIn("--dry-run", git.calls[0])
        self.assertIsNone(result.consent)
        self.assertIsNone(state.consent)
        self.assertEqual(state.workers[0].scopes, ())


class TheLiveWorkersDefaultSeamsAreKeelsOwn(unittest.TestCase):
    """The three I/O seams default to the helpers every other keel command uses."""

    def _plan(self):
        return RunPlan(
            provider="codex", vendor="codex", role="implement", transport="cli", prompt_path="/b"
        )

    def test_the_implementer_runs_through_delegate_runs_executor_with_the_given_env(self):
        from keel import swarm_runtime

        with patch("keel.delegaterun.execute", return_value={"ok": True}) as execute:
            self.assertEqual(
                swarm_runtime._default_implement(self._plan(), {"A": "1"}), {"ok": True}
            )
        self.assertEqual(execute.call_args.kwargs["_run"].keywords, {"env": {"A": "1"}})

    def test_push_and_pull_request_are_the_git_and_github_wrappers(self):
        from keel import swarm_runtime

        with (
            patch("keel.git.push_commit", return_value="pushed") as push,
            patch("keel.github.open_pr", return_value="opened") as open_pr,
        ):
            self.assertEqual(
                swarm_runtime._default_push("origin", "abc", "refs/heads/b", Path("/w")), "pushed"
            )
            self.assertEqual(
                swarm_runtime._default_open_pr("t", "b", "main", "h", Path("/w")), "opened"
            )
        push.assert_called_once_with("origin", "abc", "refs/heads/b", cwd="/w")
        open_pr.assert_called_once_with("t", "b", "main", "h", cwd="/w")

    def test_the_default_runner_passes_the_environment_through(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("keel.swarm_runtime.subprocess.run", side_effect=fake_run):
            default_runner(["true"], Path("."), env={"B": "2"})
        self.assertEqual(seen["env"], {"B": "2"})


class ALiveSwarmRunDelegatesTheOperatorsConsent(unittest.TestCase):
    """#1400: `swarm-run --live` obtains consent exactly as every live keel command does,
    refuses without it, and hands each worker the delegation and its planned seat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environ = patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in swarm_worker.CONSENT_ENV_VARS:
            os.environ.pop(name, None)

    def _config(self, implement: str | None = None) -> str:
        text = Path(".keel/project.yaml").read_text(encoding="utf-8")
        if implement is not None:
            self.assertIn('"subagent:backend-developer"', text)
            text = text.replace('"subagent:backend-developer"', implement)
        path = Path(self.tmp.name) / "project.yaml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _swarm_run(self, *extra, config=None):
        argv = [
            "swarm-run",
            config or self._config(),
            "--root",
            self.tmp.name,
            "--issues",
            "7,8",
            "--issue-scope",
            "7=src/a.py",
            "--issue-scope",
            "8=src/b.py",
            "--swarm-id",
            "swarm-cli-live",
            "--live",
            *extra,
        ]
        err = io.StringIO()
        with patch("keel.swarm_runtime.run_swarm_orchestration") as orchestrate:
            orchestrate.return_value.status = "success"
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(argv)
        return code, err.getvalue(), orchestrate

    def test_consent_given_hands_each_worker_the_parents_scopes_and_seat(self):
        code, err, orchestrate = self._swarm_run(
            "--approve-scope",
            "filesystem,git,github,secrets",
            "--operator",
            "alice",
            "--delegate",
            "codex:gpt-5",
        )
        self.assertEqual(code, 0, err)
        plan = orchestrate.call_args.args[0]
        kwargs = orchestrate.call_args.kwargs
        live = kwargs["live"]
        clusters = tuple(c.cluster_id for c in _clusters(plan))
        self.assertFalse(kwargs["dry_run"])
        self.assertEqual(len(clusters), 2)
        self.assertEqual(
            (live.consent.operator, live.consent.source, live.consent.mode),
            ("alice", "flag", "explicit"),
        )
        self.assertEqual(live.consent.scopes, FULL_SCOPES, "secrets is not handed down")
        self.assertEqual(
            (live.consent.swarm_id, live.consent.clusters), ("swarm-cli-live", clusters)
        )
        self.assertTrue(live.consent.delegated_at)
        root = Path(self.tmp.name).resolve()
        for cid in clusters:
            with self.subTest(cluster=cid):
                self.assertEqual(swarm_worker.worker_scopes(live.consent, cid), FULL_SCOPES)
                seat = live.dispatches[cid]
                self.assertEqual(
                    (seat.provider, seat.model, seat.role), ("codex", "gpt-5", "implement")
                )
                self.assertEqual(seat.cwd, str(build_worktree_path("swarm-cli-live", cid, root)))
                self.assertEqual(
                    seat.prompt_path, str(build_brief_path("swarm-cli-live", cid, root))
                )
                self.assertEqual(live.seat_sources[cid], "flag:--delegate")

    def test_the_configured_implementer_is_dispatched_and_delegate_overrides_it(self):
        config = self._config(implement="codex")
        consent_flags = ("--approve-scope", "filesystem,git,github", "--operator", "alice")
        for extra, provider, source in (
            ((), "codex", "team.implement.by_role.core"),
            (("--delegate", "claude"), "claude", "flag:--delegate"),
        ):
            with self.subTest(provider=provider):
                code, err, orchestrate = self._swarm_run(*consent_flags, *extra, config=config)
                self.assertEqual(code, 0, err)
                live = orchestrate.call_args.kwargs["live"]
                self.assertEqual({p.provider for p in live.dispatches.values()}, {provider})
                self.assertEqual(set(live.seat_sources.values()), {source})

    def test_a_seat_keel_cannot_dispatch_stops_every_worker(self):
        code, err, orchestrate = self._swarm_run(
            "--approve-scope", "filesystem,git,github", "--operator", "alice"
        )
        self.assertEqual(code, 1)
        self.assertIn("cannot dispatch every cluster's implementer seat", err)
        self.assertIn("'subagent:backend-developer'", err)
        orchestrate.assert_not_called()

    def test_consent_the_worker_could_not_be_handed_is_refused(self):
        for extra, said in (
            (("--consent-mode", "agent"), "approves no scope a worker can be handed"),
            (("--approve-scope", "filesystem,git,github"), "pass --operator NAME"),
            (("--approve-scope", "filesystem,git"), "Missing approved scope: github"),
        ):
            with self.subTest(extra=extra):
                code, err, orchestrate = self._swarm_run(*extra, "--delegate", "codex")
                self.assertEqual(code, 1)
                self.assertIn(said, err)
                orchestrate.assert_not_called()

    def test_standing_consent_from_the_environment_is_delegated_with_its_source(self):
        os.environ.update(
            KEEL_CONSENT_MODE="standing",
            KEEL_APPROVE_SCOPE="filesystem,git,github",
            KEEL_OPERATOR="bob",
        )
        code, err, orchestrate = self._swarm_run("--delegate", "codex")
        self.assertEqual(code, 0, err)
        consent = orchestrate.call_args.kwargs["live"].consent
        self.assertEqual(
            (consent.operator, consent.source, consent.mode), ("bob", "env", "standing")
        )

    def test_standing_scopes_without_an_operator_are_refused(self):
        os.environ.update(KEEL_CONSENT_MODE="standing", KEEL_APPROVE_SCOPE="git")
        code, err, orchestrate = self._swarm_run("--delegate", "codex")
        self.assertEqual(code, 1)
        self.assertIn("KEEL_OPERATOR is required", err)
        orchestrate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
