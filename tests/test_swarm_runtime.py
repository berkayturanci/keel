"""Unit tests for Keel Swarm isolated multi-worktree execution & runtime orchestration."""

from __future__ import annotations

import datetime
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from keel import ledger, swarm_worker
from keel import swarm_runtime as swarm_runtime_module
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
from keel.swarm_landing import land_wave_clusters
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
    """Recording stand-ins for a live worker's four I/O seams (#1400).

    ``implement`` records the plan it was handed, the environment and the brief, and can
    fail, or raise for a cluster whose brief names a given issue; ``push``/``open_pr``/
    ``post_comment`` record their arguments and succeed unless told otherwise.
    """

    def __init__(
        self, *, implement_ok=True, raise_for=None, push_ok=True, pr_ok=True, comment_ok=True
    ):
        self.implement_ok, self.raise_for = implement_ok, raise_for
        self.push_ok, self.pr_ok, self.comment_ok = push_ok, pr_ok, comment_ok
        self.implemented: list[tuple[RunPlan, dict, str]] = []
        self.pushes: list[tuple[str, str, str]] = []
        self.push_argvs: list[list[str]] = []
        self.prs: list[tuple[str, str, str, str]] = []
        self.comments: list[tuple[str, int, str]] = []
        #: Every seam call, in order: what the ordering tests read.
        self.events: list[str] = []

    def implement(self, plan, env):
        brief = Path(plan.prompt_path).read_text(encoding="utf-8")
        self.implemented.append((plan, env, brief))
        if self.raise_for is not None and f"#{self.raise_for}:" in brief:
            raise RuntimeError(f"boom in {self.raise_for}")
        if not self.implement_ok:
            return {"ok": False, "error_code": "nonzero-exit", "error": "codex exited 2"}
        return {"ok": True, "text": "done"}

    def push(self, argv, cwd):
        # `git -c … push --no-verify <url> <commit>:<ref>`: recorded as (url, commit, ref).
        self.push_argvs.append(list(argv))
        commit, ref = argv[-1].split(":", 1)
        self.pushes.append((argv[-2], commit, ref))
        return CommandResult(self.push_ok, 0 if self.push_ok else 1, "" if self.push_ok else "no")

    def open_pr(self, title, body, base, head, cwd):
        self.events.append("open_pr")
        self.prs.append((title, body, base, head))
        if not self.pr_ok:
            return CommandResult(False, 1, "gh: not authenticated")
        return CommandResult(True, 0, f"https://github.com/o/r/pull/{len(self.prs)}\n")

    def post_comment(self, owner_repo, number, body, cwd):
        self.events.append("post_comment")
        self.comments.append((owner_repo, number, body))
        if not self.comment_ok:
            return CommandResult(False, 1, "HTTP 403: Resource not accessible")
        return CommandResult(True, 0, '{"id": 1}')


def _live(plan, root, io, *, scopes=FULL_SCOPES, clusters=None, ledger_path=None) -> LiveRun:
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
        ledger_path=ledger_path,
        implement=io.implement,
        push=io.push,
        open_pr=io.open_pr,
        post_comment=io.post_comment,
    )


class _Git:
    """A recording runner for a live worker's git and gate commands.

    ``worktree add`` makes the directory; ``status`` reports a change unless ``clean``;
    ``rev-parse`` answers ``base`` until the worktree has been committed in, then a head
    named after it; ``fail`` names a command word whose call fails.
    """

    #: What `git remote get-url --push` answers: the URL a worker pushes to.
    PUSH_URL = "https://forge.example.invalid/o/r.git"

    def __init__(self, *, clean=False, fail=None, gates_ok=True, config=None):
        self.clean, self.fail, self.gates_ok = clean, fail, gates_ok
        self.calls: list[list[str]] = []
        self.committed: set[str] = set()
        #: `git config --list` answers, in turn; the last one repeats.
        self.config = list(config or ["local\tfile:.git/config\tcore.bare=false"])

    def __call__(self, cmd, cwd):
        self.calls.append(list(cmd))
        # keel's own steps after the implementer carry `-c key=value` pairs up front.
        while len(cmd) > 2 and cmd[0] == "git" and cmd[1] == "-c":
            cmd = [cmd[0], *cmd[3:]]
        if self.fail is not None and self.fail in cmd:
            return CommandResult(False, 1, f"{self.fail} failed")
        if "--absolute-git-dir" in cmd:
            return CommandResult(True, 0, "/r/.git/worktrees/w\n/r/.git\n/r/.git/hooks\n")
        if cmd[:3] == ["git", "config", "--list"]:
            answer = self.config.pop(0) if len(self.config) > 1 else self.config[0]
            return CommandResult(True, 0, answer + "\n")
        if cmd[:3] == ["git", "remote", "get-url"]:
            return CommandResult(True, 0, self.PUSH_URL + "\n")
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[5]).mkdir(parents=True, exist_ok=True)
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
            # #1278: the passing worker's worktree goes; the raising one's seat had run, so
            # its worktree is kept for inspection, named in the result and the state file.
            self.assertEqual(len(removed), 1)
            raised = next(w for w in state.workers if w.status == "failed")
            kept = build_worktree_path("swarm-raises", raised.cluster_id, root=Path(tmpdir))
            self.assertEqual(raised.worktree, str(kept.resolve()))
            self.assertTrue(kept.exists())
            self.assertIn(f"kept for inspection at {kept.resolve()}", raised.details)

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
        # To the URL `origin` had before the implementer ran, never to the remote's name.
        self.assertEqual(
            sorted(io_.pushes),
            sorted(
                (_Git.PUSH_URL, f"head-{cid}", f"refs/heads/swarm/swarm-live/{cid}")
                for cid in clusters
            ),
        )
        get_url = git.ran("get-url")
        self.assertEqual(len(get_url), 2)
        self.assertEqual(get_url[0][-2:], ["--push", "origin"])
        # keel's own push and commit: no hooks, no fsmonitor, no signing, no verify.
        for argv in io_.push_argvs + commits:
            self.assertEqual(argv[1], "-c")
            self.assertTrue(argv[2].startswith("core.hooksPath="), argv)
            self.assertEqual(
                argv[3:7], ["-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false"]
            )
            self.assertIn("--no-verify", argv)
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
        # The number of the pull request each worker opened: what swarm-land merges (#1287).
        self.assertEqual({w.pull_request for w in state.workers}, {1, 2})
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

    def test_a_config_change_while_the_implementer_ran_stops_before_the_commit(self):
        a = "local\tfile:.git/config\tremote.origin.url=https://forge.example.invalid/o/r.git"
        b = "local\tfile:.git/config\tremote.origin.url=https://evil.example.invalid/x.git"
        io_, git = _LiveIo(), _Git(config=[a, b])
        result, res = self._one(io_, git)
        self.assertEqual((result.status, res["stage"]), ("failed", "tamper"))
        self.assertIn("changed while the implementer ran", res["output"])
        self.assertIn("local remote.origin.url", res["output"])
        self.assertNotIn("evil", res["output"])
        self.assertEqual((git.ran("-A"), git.ran("commit"), git.ran("run-gates")), ([], [], []))
        self.assertEqual((io_.pushes, io_.prs), ([], []))
        self.assertIn("stopped at tamper", render_swarm_run_result(result))

    def test_a_config_change_while_the_gates_ran_stops_before_the_push(self):
        a, b = "local\tfile:.git/config\tcore.bare=false", "local\tfile:.git/config\tx.y=z"
        io_, git = _LiveIo(), _Git(config=[a, a, b])
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "tamper")
        self.assertIn(
            "while the gates ran (git config changed: local core.bare, local x.y)", res["output"]
        )
        self.assertEqual(len(git.ran("run-gates")), 1)
        self.assertEqual(res["commit"], "head-" + Path(io_.implemented[0][0].cwd).name)
        self.assertEqual((io_.pushes, io_.prs), ([], []))

    def test_a_setup_that_can_no_longer_be_read_is_a_change(self):
        class Unreadable(_Git):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def __call__(self, cmd, cwd):
                if "--absolute-git-dir" in cmd:
                    self.reads += 1
                    if self.reads > 1:
                        return CommandResult(False, 128, "fatal: not a git repository")
                return super().__call__(cmd, cwd)

        io_, git = _LiveIo(), Unreadable()
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "tamper")
        self.assertIn("the git setup could no longer be read", res["output"])
        self.assertEqual((git.ran("commit"), io_.pushes), ([], []))

    def test_a_setup_that_cannot_be_read_before_starts_nothing(self):
        io_, git = _LiveIo(), _Git(fail="--absolute-git-dir")
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "worktree")
        self.assertIn("could not be read", res["output"])
        self.assertEqual((io_.implemented, io_.pushes), ([], []))

    def test_a_remote_without_a_push_url_starts_nothing(self):
        io_, git = _LiveIo(), _Git(fail="get-url")
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "push")
        self.assertIn("the push URL of remote 'origin' could not be read", res["output"])
        self.assertEqual((io_.implemented, io_.pushes), ([], []))

    def test_a_head_that_does_not_descend_from_the_base_is_not_pushed(self):
        io_, git = _LiveIo(), _Git(fail="merge-base")
        _result, res = self._one(io_, git)
        self.assertEqual(res["stage"], "tamper")
        self.assertIn("does not descend from base", res["output"])
        self.assertEqual((git.ran("run-gates"), io_.pushes), ([], []))

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

    def test_there_is_no_live_run_without_a_delegation(self):
        plan = self._plan(721)
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaises(ValueError):
            run_swarm_orchestration(plan, "p.yaml", root=tmpdir, dry_run=False, base_branch="main")

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

    def test_push_and_pull_request_are_keels_runner_and_github_wrapper(self):
        from keel import swarm_runtime

        with (
            patch("keel.runner.run_argv", return_value="pushed") as push,
            patch("keel.github.open_pr", return_value="opened") as open_pr,
        ):
            self.assertEqual(swarm_runtime._default_push(["git", "push"], Path("/w")), "pushed")
            self.assertEqual(
                swarm_runtime._default_open_pr("t", "b", "main", "h", Path("/w")), "opened"
            )
        # str(Path), not a literal: on Windows the separator is a backslash.
        worktree = str(Path("/w"))
        push.assert_called_once_with(["git", "push"], cwd=worktree)
        open_pr.assert_called_once_with("t", "b", "main", "h", cwd=worktree)

    def test_keels_own_push_and_pull_request_inherit_the_operators_environment(self):
        """The push and ``gh pr create`` are keel's, made after the implementer exited:
        they pass no ``env``, so they run with the operator's credentials."""
        from keel import swarm_runtime

        with (
            patch("keel.runner.run_argv", return_value="pushed") as push,
            patch("keel.github.run_argv", return_value="opened") as open_pr,
        ):
            swarm_runtime._default_push(["git", "push", "u", "abc:refs/heads/b"], Path("/w"))
            swarm_runtime._default_open_pr("t", "b", "main", "h", Path("/w"))
        self.assertEqual(push.call_args.args[0], ["git", "push", "u", "abc:refs/heads/b"])
        self.assertEqual(open_pr.call_args.args[0][:3], ["gh", "pr", "create"])
        self.assertNotIn("env", push.call_args.kwargs)
        self.assertNotIn("env", open_pr.call_args.kwargs)


class AClusterPullRequestArmsTheEvidenceGate(unittest.TestCase):
    """Found on the first end-to-end live run: a cluster PR carried no keel signal that arms
    keel merge's evidence gate — `swarm/<id>/<cluster>` matches no ship-branch pattern — so
    `swarm-land` held it as "evidence gate is not enforced". The worker now posts the
    ship-provenance comment a live `keel ship` run posts, right after the PR is opened."""

    SWARM = "swarm-arm"

    def _plan(self):
        return build_swarm_plan(
            [IssueScope(issue=741, title="T741", predicted_files=("src/741.py",))],
            swarm_id=self.SWARM,
        )

    def _run(self, io_, git=None):
        plan = self._plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            self.root = tmpdir
            result = run_swarm_orchestration(
                plan,
                "projects/x.yaml",
                root=tmpdir,
                dry_run=False,
                live=_live(plan, tmpdir, io_),
                runner=git or _Git(),
                base_branch="main",
            )
            state = load_swarm_state(self.SWARM, root=tmpdir)
        (cluster,) = _clusters(plan)
        (res,) = result.wave_results[0]["cluster_results"].values()
        return cluster, result, res, state

    def test_the_pull_request_carries_the_comment_keel_ship_posts(self):
        from keel import artifacts, cli, evidence

        io_ = _LiveIo()
        cluster, result, res, _state = self._run(io_)

        cid = cluster.cluster_id
        run_id = f"{self.SWARM}/{cid}"
        # The body keel ship renders (`artifact_bodies.ship_provenance`), for this cluster:
        # the seat's attribution verbatim, the issue, and the head keel pushed.
        expected = artifacts.render_ship_provenance(
            run_id=run_id,
            issue=741,
            head_sha=f"head-{cid}",
            implementer_attribution={"system": "codex:gpt-5"},
        )
        self.assertEqual(io_.comments, [("o/r", 1, expected)])
        (_repo, _number, body) = io_.comments[0]
        # What `keel post-comment --run-id` would send is this body unchanged: its
        # `run-id:` line already makes a re-post find and edit it.
        self.assertEqual(cli._with_run_id_marker(body, run_id), body)
        self.assertEqual((res["stage"], res["ok"], res["provenance_posted"]), ("done", True, True))
        self.assertEqual((result.status, result.warnings), ("success", ()))
        # keel merge reads it back as a keel run: the gate is armed by the comment, where
        # the branch alone leaves it unarmed — the finding.
        branch = f"swarm/{self.SWARM}/{cid}"
        posted = {"body": body, "author_association": "OWNER"}
        armed = evidence.gate_decision([], "keel:gate", head_ref=branch, pr_comments=[posted])
        bare = evidence.gate_decision([], "keel:gate", head_ref=branch, pr_comments=[])
        self.assertEqual((armed["enforced"], armed["reason"]), (True, "ship-provenance-comment"))
        self.assertEqual((bare["enforced"], bare["reason"]), (False, "no-ship-provenance"))

    def test_it_is_posted_after_the_pull_request_opens_and_before_the_worker_is_done(self):
        test = self

        class Watching(_LiveIo):
            """Reads the state file as the comment is posted — what swarm-status reads."""

            def post_comment(self, owner_repo, number, body, cwd):
                self.during = load_swarm_state(test.SWARM, root=test.root)
                return super().post_comment(owner_repo, number, body, cwd)

        io_ = Watching()
        _cluster, _result, res, state = self._run(io_)

        self.assertEqual(io_.events, ["open_pr", "post_comment"])
        (during,) = io_.during.workers
        self.assertEqual((during.status, during.stage), ("running", "pull_request"))
        (after,) = state.workers
        self.assertEqual((after.status, after.stage, after.pull_request), ("passed", "done", 1))
        self.assertTrue(res["provenance_posted"])

    def test_a_failed_post_keeps_the_pull_request_and_says_it_will_be_held(self):
        io_, git = _LiveIo(comment_ok=False), _Git()
        cluster, result, res, state = self._run(io_, git)

        self.assertEqual(len(io_.comments), 1)
        # The pull request stands: the worker passed, its branch is not deleted.
        self.assertEqual((result.status, res["ok"], res["stage"]), ("success", True, "done"))
        self.assertEqual(res["pr_url"], "https://github.com/o/r/pull/1")
        self.assertFalse(res["provenance_posted"])
        self.assertFalse(res["branch_deleted"])
        self.assertEqual([c for c in git.calls if "-D" in c], [])
        # Reported, in the worker record, the run's warnings and the state file.
        (warning,) = res["warnings"]
        self.assertIn("the ship-provenance comment was not posted on", warning)
        self.assertIn("HTTP 403: Resource not accessible", warning)
        self.assertIn('"evidence gate is not enforced"', warning)
        self.assertEqual(result.warnings, (f"{cluster.cluster_id}: {warning}",))
        self.assertIn(warning, res["output"])
        self.assertIn(f"warning       : {cluster.cluster_id}: ", render_swarm_run_result(result))
        (worker,) = state.workers
        self.assertEqual((worker.status, worker.pull_request), ("passed", 1))
        self.assertIn("ship-provenance comment was not posted", worker.details)

    def test_a_pull_request_url_keel_cannot_read_is_reported_not_guessed(self):
        class Unreadable(_LiveIo):
            def open_pr(self, title, body, base, head, cwd):
                super().open_pr(title, body, base, head, cwd)
                return CommandResult(True, 0, "")

        io_ = Unreadable()
        _cluster, result, res, _state = self._run(io_)
        self.assertEqual(io_.comments, [])
        self.assertEqual((result.status, res["provenance_posted"]), ("success", False))
        (warning,) = res["warnings"]
        self.assertIn("not posted on the pull request (gh pr create printed no", warning)

    def test_the_settles_warnings_join_the_workers_rather_than_replace_them(self):
        merged = swarm_runtime_module._with_settlement(
            {"output": "o", "warnings": ["unstamped"]},
            {"worktree": None, "worktree_state": "remove-failed", "warnings": ["kept"]},
            "b",
        )
        self.assertEqual(merged["warnings"], ["unstamped", "kept"])

    def test_the_repository_is_read_off_the_url_gh_printed(self):
        for url, repo in (
            ("https://github.com/o/r/pull/12", "o/r"),
            (" https://ghe.example.invalid/org/repo/pull/3/\n", "org/repo"),
            ("https://github.com/o/r/pull/0", None),
            ("https://github.com/o/r/issues/1", None),
            ("https://github.com/o/r/x/pull/1", None),
            ("Created pull request", None),
            ("", None),
        ):
            with self.subTest(url=url):
                self.assertEqual(swarm_worker.pull_request_repo(url), repo)

    def test_the_default_seam_is_keels_own_comment_post_with_the_operators_env(self):
        with patch("keel.github.run_argv", return_value="posted") as run_argv:
            self.assertEqual(
                swarm_runtime_module._default_post_comment("o/r", 5, "B", Path("/w")), "posted"
            )
        argv = run_argv.call_args.args[0]
        self.assertEqual(
            argv, ["gh", "api", "repos/o/r/issues/5/comments", "-X", "POST", "-F", "body=B"]
        )
        # str(Path), not a literal: on Windows the separator is a backslash.
        self.assertEqual(run_argv.call_args.kwargs["cwd"], str(Path("/w")))
        self.assertNotIn("env", run_argv.call_args.kwargs)


#: The operator's environment for the forge tests: consent, forge tokens, a provider key.
_FORGE_ENV = {
    "KEEL_APPROVE_SCOPE": "filesystem,git,github",
    "KEEL_OPERATOR": "ops",
    "GH_TOKEN": "gho_operator_token",
    "GITHUB_TOKEN": "ghp_operator_token",
    "GH_ENTERPRISE_TOKEN": "ghe_operator_token",
    "GITHUB_ENTERPRISE_TOKEN": "ghe_operator_github_token",
    "ANTHROPIC_API_KEY": "sk-ant-provider",
}


def _git_cli(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class KeelsOwnGitStepsAgainstARealRepository(unittest.TestCase):
    """#1400: the worktree shares the operator's repository, and the implementer can write
    its config and hooks. Measured against a real git, offline, with local repositories:
    keel's own commit and push run none of the repository's hooks, fsmonitor or signing
    program, and a change to the git setup while the implementer or the gates ran stops
    the worker at ``tamper`` with nothing pushed — anywhere."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        empty = self.tmp / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        environ = patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        # Nothing of the machine's own git setup — or a hook's GIT_DIR — reaches the test.
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            del os.environ[key]
        os.environ.update(
            GIT_CONFIG_GLOBAL=str(empty),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@example.invalid",
        )
        self.remote, self.evil = self.tmp / "remote.git", self.tmp / "evil.git"
        self.repo = self.tmp / "repo"
        for bare in (self.remote, self.evil):
            self._ok(["init", "-q", "--bare", str(bare)], self.tmp)
        self._ok(["init", "-q", "-b", "main", str(self.repo)], self.tmp)
        (self.repo / "README").write_text("base\n", encoding="utf-8")
        self._ok(["add", "README"], self.repo)
        self._ok(["commit", "-q", "-m", "base"], self.repo)
        self._ok(["remote", "add", "origin", str(self.remote)], self.repo)
        # The operator's own hooks and exec-capable config, there before the run: keel's
        # own steps after the implementer run none of them. `ran.log` records any that do.
        self.ran = self.tmp / "ran.log"
        for name in ("pre-commit", "commit-msg", "post-commit", "pre-push", "post-rewrite"):
            self._script(self.repo / ".git" / "hooks" / name, name)
        fsmonitor = self._script(self.tmp / "fsmonitor.sh", "fsmonitor")
        gpg = self._script(self.tmp / "gpg.sh", "gpg", code=1)
        for key, value in (
            ("core.fsmonitor", fsmonitor),
            ("commit.gpgsign", "true"),
            ("gpg.program", gpg),
        ):
            self._ok(["config", key, value], self.repo)
        self.plan = build_swarm_plan(
            [IssueScope(issue=741, title="T741", predicted_files=("src/741.py",))],
            swarm_id="swarm-real",
        )
        (self.cluster,) = _clusters(self.plan)
        self.ref = f"refs/heads/swarm/swarm-real/{self.cluster.cluster_id}"
        #: What the implementer, and then the gates, do to the repository besides the work.
        self.tamper = None
        self.during_gates = None

    def _ok(self, args, cwd):
        result = _git_cli(args, cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _script(self, path, name, code=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!/bin/sh\necho {name} >> '{self.ran.as_posix()}'\nexit {code}\n", encoding="utf-8"
        )
        path.chmod(0o755)
        return path.as_posix()

    def _implement(self, plan, env):
        # What ran before the implementer (cutting the worktree) was the operator's own git.
        self.ran.unlink(missing_ok=True)
        (Path(plan.cwd) / "work.txt").write_text("work\n", encoding="utf-8")
        if self.tamper is not None:
            self.tamper(Path(plan.cwd))
        return {"ok": True}

    def _runner(self, cmd, cwd):
        if "run-gates" in cmd:
            if self.during_gates is not None:
                self.during_gates(Path(cwd))
            return CommandResult(True, 0, "gates passed")
        return default_runner(cmd, cwd, env=swarm_worker.worker_env(os.environ))

    def _run(self):
        from keel.swarm_runtime import _default_push, execute_live_cluster_worker

        io_ = _LiveIo()
        live = replace(
            _live(self.plan, self.repo, io_), implement=self._implement, push=_default_push
        )
        return execute_live_cluster_worker(
            self.cluster,
            swarm_id=self.plan.swarm_id,
            root=self.repo,
            worktree_dir=build_worktree_path(
                self.plan.swarm_id, self.cluster.cluster_id, self.repo
            ),
            project_yaml="projects/x.yaml",
            base_branch="main",
            live=live,
            runner=self._runner,
        )

    def _pushed(self, bare):
        result = _git_cli(["rev-parse", "--verify", "-q", self.ref], bare)
        return result.stdout.strip() if result.returncode == 0 else None

    def _ran(self):
        return self.ran.read_text(encoding="utf-8").split() if self.ran.exists() else []

    @unittest.skipIf(os.name == "nt", "the control runs POSIX hook scripts")
    def test_the_fixtures_hooks_do_run_for_a_plain_git(self):
        self._ok(
            ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "c"], self.repo
        )
        self.assertIn("pre-commit", self._ran())
        self.assertIn("post-commit", self._ran())

    def test_keels_commit_and_push_run_no_hook_fsmonitor_or_signing_program(self):
        res = self._run()
        self.assertEqual((res["ok"], res["stage"]), (True, "done"), res["output"])
        self.assertEqual(self._ran(), [])
        self.assertEqual(self._pushed(self.remote), res["commit"])
        commit = self._ok(["cat-file", "commit", res["commit"]], self.repo).stdout
        self.assertNotIn("gpgsig", commit)

    def _assert_tampered(self, during, *names):
        res = self._run()
        self.assertEqual((res["ok"], res["stage"]), (False, "tamper"), res["output"])
        self.assertIn(f"changed while {during}", res["output"])
        for name in names:
            self.assertIn(name, res["output"])
        self.assertNotIn(self.evil.as_posix(), res["output"])
        self.assertEqual((self._pushed(self.remote), self._pushed(self.evil)), (None, None))
        self.assertEqual(self._ran(), [])
        return res

    def test_a_hook_the_implementer_plants_stops_the_worker(self):
        hooks = self.repo / ".git" / "hooks"
        self.tamper = lambda _wt: self._script(hooks / "post-merge", "planted")
        self._assert_tampered("the implementer ran", "hook post-merge added")

    def test_a_hook_the_implementer_changes_stops_the_worker(self):
        hooks = self.repo / ".git" / "hooks"
        self.tamper = lambda _wt: self._script(hooks / "pre-push", "planted")
        self._assert_tampered("the implementer ran", "hook pre-push changed")

    def test_a_push_url_the_implementer_sets_stops_the_worker(self):
        self.tamper = lambda wt: self._ok(["config", "remote.origin.pushurl", str(self.evil)], wt)
        self._assert_tampered("the implementer ran", "local remote.origin.pushurl")

    def test_a_push_rewrite_the_implementer_sets_stops_the_worker(self):
        key = f"url.{self.evil.as_posix()}.pushInsteadOf"
        self.tamper = lambda wt: self._ok(["config", key, str(self.remote)], wt)
        self._assert_tampered("the implementer ran", "local url.<url>.pushinsteadof")

    def test_a_hooks_path_the_implementer_sets_stops_the_worker(self):
        planted = self.tmp / "planted-hooks"
        self.tamper = lambda wt: self._ok(["config", "core.hooksPath", str(planted)], wt)
        self._assert_tampered("the implementer ran", "local core.hookspath", "hooks directory")

    def test_a_change_to_a_file_the_config_includes_stops_the_worker(self):
        included = self.tmp / "included.cfg"
        included.write_text("[keel]\n\tprobe = 1\n", encoding="utf-8")
        self._ok(["config", "include.path", str(included)], self.repo)

        def append(_wt):
            with included.open("a", encoding="utf-8") as handle:
                handle.write(f"[core]\n\tsshCommand = {self.evil.as_posix()}\n")

        self.tamper = append
        self._assert_tampered("the implementer ran", "core.sshcommand")

    def test_a_config_change_while_the_gates_ran_stops_the_push(self):
        self.during_gates = lambda wt: self._ok(
            ["config", "remote.origin.pushurl", str(self.evil)], wt
        )
        res = self._assert_tampered("the gates ran", "local remote.origin.pushurl")
        # The commit was made — before the gates — and stays local.
        self.assertTrue(res["commit"])


class OnlyKeelReachesTheRemote(unittest.TestCase):
    """#1400: the implementer seat runs without the operator's forge credentials and with
    git locked out of the remote; keel's own git commands and gates keep the operator's
    environment (less consent), and keel's push and pull request are what reach the
    forge."""

    def _plan(self):
        return build_swarm_plan(
            [IssueScope(issue=731, title="T731", predicted_files=("src/731.py",))],
            swarm_id="swarm-forge",
        )

    def test_the_implementer_runs_without_the_forge(self):
        plan = self._plan()
        io_, git = _LiveIo(), _Git()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, _FORGE_ENV):
            result = run_swarm_orchestration(
                plan,
                "projects/x.yaml",
                root=tmpdir,
                dry_run=False,
                live=_live(plan, tmpdir, io_),
                runner=git,
                base_branch="main",
            )
            root = Path(tmpdir).resolve()

        self.assertEqual(result.status, "success")
        ((_plan, env, _brief),) = io_.implemented
        (cluster,) = _clusters(plan)
        # Compared as sets of names, so a failure never prints the environment's values.
        leaked = set(swarm_worker.FORGE_TOKEN_ENV_VARS + swarm_worker.CONSENT_ENV_VARS) & set(env)
        self.assertEqual(leaked, set())
        secrets = {"gho_operator_token", "ghp_operator_token", "ghe_operator_token"}
        self.assertEqual(secrets & set(env.values()), set())
        self.assertEqual(
            env["GH_CONFIG_DIR"],
            str(
                root
                / ".keel"
                / "state"
                / "swarm"
                / "swarm-forge"
                / (cluster.cluster_id + ".no-gh-login")
            ),
        )
        self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"]), ("0", ""))
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "protocol.allow")
        self.assertEqual(env["GIT_CONFIG_VALUE_1"], "never")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-provider")
        # keel pushed and opened the pull request after the implementer, through its seams.
        self.assertEqual(len(io_.pushes), 1)
        self.assertEqual(len(io_.prs), 1)

    def test_keels_own_git_and_gates_run_without_the_forge_or_the_lockdown(self):
        """keel's local git steps and the gates — which run the implementer's code — hold
        no forge token (``keel run-gates --phases guard,test --defer-jury`` reads nothing
        from GitHub), but keep the operator's own git: they are not locked out of it."""
        plan = self._plan()
        (cluster,) = _clusters(plan)
        io_, git = _LiveIo(), _Git()
        envs: list[tuple[list[str], dict]] = []

        def recording_runner(cmd, cwd, timeout_s=0, env=None):
            envs.append((list(cmd), env))
            return git(cmd, cwd)

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, _FORGE_ENV),
            patch("keel.swarm_runtime.default_runner", side_effect=recording_runner),
        ):
            from keel.swarm_runtime import execute_live_cluster_worker

            root = Path(tmpdir).resolve()
            res = execute_live_cluster_worker(
                cluster,
                swarm_id=plan.swarm_id,
                root=root,
                worktree_dir=build_worktree_path(plan.swarm_id, cluster.cluster_id, root),
                project_yaml="projects/x.yaml",
                base_branch="main",
                live=_live(plan, root, io_),
            )

        self.assertTrue(res["ok"], res["output"])
        self.assertTrue(any("run-gates" in cmd for cmd, _env in envs))
        self.assertTrue(any("commit" in cmd for cmd, _env in envs))
        for cmd, env in envs:
            # Compared as names, so a failure never prints the environment's values.
            leaked = set(swarm_worker.FORGE_TOKEN_ENV_VARS + swarm_worker.CONSENT_ENV_VARS)
            self.assertEqual(leaked & set(env), set(), cmd)
            self.assertEqual(env.get("ANTHROPIC_API_KEY"), "sk-ant-provider", cmd)
            # Not the implementer's lockdown: keel's own git is the operator's git.
            self.assertNotIn(".no-gh-login", env.get("GH_CONFIG_DIR", ""), cmd)
            self.assertNotEqual(env.get("GIT_CONFIG_KEY_1"), "protocol.allow", cmd)

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

    _CONSENT = ("--approve-scope", "filesystem,git,github", "--operator", "alice")

    def _ledger(self) -> Path:
        return Path(self.tmp.name).resolve() / ".keel" / "state" / "run-ledger.jsonl"

    def test_the_delegation_is_in_the_run_ledger_before_any_worker_starts(self):
        seen: list[list[dict]] = []

        def orchestrate(plan, **kwargs):
            # What the ledger holds at the moment the workers would start.
            seen.append(ledger.read_records(self._ledger(), kinds=ledger.KNOWN_RECORD_TYPES))
            return MagicMock(status="success")

        argv = [
            "swarm-run",
            self._config(),
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
            *self._CONSENT,
            "--delegate",
            "codex",
        ]
        err = io.StringIO()
        with (
            patch("keel.swarm_runtime.run_swarm_orchestration", side_effect=orchestrate) as run,
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            code = main(argv)
        self.assertEqual(code, 0, err.getvalue())
        live = run.call_args.kwargs["live"]
        self.assertEqual(live.ledger_path, self._ledger())
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1, "one delegated record, before any worker starts")
        record = seen[0][0]
        self.assertEqual(
            {k: record[k] for k in ("record_type", "event", "swarm_id", "operator", "scopes")},
            {
                "record_type": "consent_delegation",
                "event": "delegated",
                "swarm_id": "swarm-cli-live",
                "operator": "alice",
                "scopes": list(FULL_SCOPES),
            },
        )
        self.assertEqual(record["clusters"], list(live.consent.clusters))
        self.assertEqual(record["delegated_at"], live.consent.delegated_at)
        self.assertIsNone(record["pull_request"])
        self.assertIn("consent delegation recorded in", err.getvalue())

    def test_a_delegation_that_cannot_be_recorded_starts_nothing(self):
        # The ledger's path is a directory: the append fails, and no worker starts.
        self._ledger().mkdir(parents=True)
        code, err, orchestrate = self._swarm_run(*self._CONSENT, "--delegate", "codex")
        self.assertEqual(code, 1)
        self.assertIn("the consent delegation could not be written to the run ledger", err)
        orchestrate.assert_not_called()

    def test_a_ledger_path_outside_the_root_starts_nothing(self):
        config = Path(self._config())
        text = config.read_text(encoding="utf-8")
        self.assertIn("  reports:\n", text)
        config.write_text(
            text.replace("  reports:\n", "  reports:\n    run_ledger: '../escape.jsonl'\n", 1),
            encoding="utf-8",
        )
        code, err, orchestrate = self._swarm_run(
            *self._CONSENT, "--delegate", "codex", config=str(config)
        )
        self.assertEqual(code, 1, err)
        self.assertIn("swarm-run --live is refused: run ledger path escapes", err)
        orchestrate.assert_not_called()


class EachClusterPullRequestIsInTheRunLedger(unittest.TestCase):
    """#1400: a worker's open pull request is recorded as the delegation's `pull_request`
    event — one line per cluster, appended, never a rewrite of the `delegated` line."""

    def _plan(self, *issues):
        return build_swarm_plan(
            [IssueScope(issue=n, title=f"T{n}", predicted_files=(f"src/{n}.py",)) for n in issues],
            swarm_id="swarm-led",
        )

    def _run(self, plan, tmpdir, io_, *, ledger_path):
        """The run's result, or a raise out of the run as a value — so a ledger error that
        escaped would fail these tests as an assertion, not as an error."""
        try:
            return run_swarm_orchestration(
                plan,
                "projects/x.yaml",
                root=tmpdir,
                dry_run=False,
                live=_live(plan, tmpdir, io_, ledger_path=ledger_path),
                max_workers=2,
                runner=_Git(),
                base_branch="main",
            )
        except Exception as exc:  # noqa: BLE001 - reported as the run's result
            return SimpleNamespace(status=f"raised {type(exc).__name__}: {exc}", warnings=())

    def test_each_open_pull_request_is_appended_with_its_number_branch_and_head(self):
        plan = self._plan(801, 802)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".keel" / "state" / "run-ledger.jsonl"
            path.parent.mkdir(parents=True)
            delegated = ledger.build_consent_delegation_record(
                _live(plan, tmpdir, _LiveIo()).consent.to_dict(), recorded_at="t0"
            )
            ledger.append_record(path, delegated)
            first_line = path.read_text(encoding="utf-8")
            result = self._run(plan, tmpdir, _LiveIo(), ledger_path=path)
            text = path.read_text(encoding="utf-8")
            records = ledger.parse_records(text, kinds=ledger.KNOWN_RECORD_TYPES)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.warnings, ())
        self.assertTrue(text.startswith(first_line), "the delegated line is never rewritten")
        self.assertEqual(records[0], delegated)
        opened = sorted(records[1:], key=lambda r: r["pull_request"]["cluster"])
        clusters = sorted(c.cluster_id for c in _clusters(plan))
        self.assertEqual([r["event"] for r in opened], ["pull_request", "pull_request"])
        self.assertEqual([r["pull_request"]["cluster"] for r in opened], clusters)
        cluster_results = result.wave_results[0]["cluster_results"]
        for record in opened:
            cid = record["pull_request"]["cluster"]
            with self.subTest(cluster=cid):
                pr = record["pull_request"]
                self.assertEqual(pr["branch"], f"swarm/swarm-led/{cid}")
                self.assertEqual(pr["head_sha"], f"head-{cid}")
                self.assertEqual(pr["url"], cluster_results[cid]["pr_url"])
                self.assertEqual(pr["number"], int(pr["url"].rsplit("/", 1)[1]))
                self.assertEqual((record["operator"], record["swarm_id"]), ("ops", "swarm-led"))
                self.assertEqual(record["clusters"], clusters)

    def test_a_worker_that_opened_no_pull_request_records_nothing(self):
        plan = self._plan(811)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-ledger.jsonl"
            result = self._run(plan, tmpdir, _LiveIo(pr_ok=False), ledger_path=path)
            written = path.exists()
        self.assertEqual(result.status, "failed")
        self.assertFalse(written)

    def test_an_unreadable_pull_request_url_is_a_warning_not_a_failure(self):
        class _NoUrl(_LiveIo):
            def open_pr(self, title, body, base, head, cwd):
                super().open_pr(title, body, base, head, cwd)
                return CommandResult(True, 0, "created\n")

        plan = self._plan(821)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-ledger.jsonl"
            result = self._run(plan, tmpdir, _NoUrl(), ledger_path=path)
            written = path.exists()
        self.assertEqual(result.status, "success")
        self.assertFalse(written)
        ledger_warnings = [w for w in result.warnings if "not in the run ledger" in w]
        self.assertEqual(len(ledger_warnings), 1, result.warnings)
        self.assertIn("printed no pull request number", ledger_warnings[0])
        self.assertIn("will report no delegated consent for it", ledger_warnings[0])
        self.assertNotIn("branch", ledger_warnings[0])

    def test_a_ledger_that_cannot_be_written_is_a_warning_not_a_failure(self):
        plan = self._plan(831)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-ledger.jsonl"
            path.mkdir()
            result = self._run(plan, tmpdir, _LiveIo(), ledger_path=path)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.warnings), 1, result.warnings)
        self.assertIn("https://github.com/o/r/pull/1 is not in the run ledger", result.warnings[0])
        self.assertIn("Error", result.warnings[0])

    def test_a_record_the_ledger_refuses_is_a_warning_not_a_failure(self):
        plan = self._plan(841)
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(
                swarm_runtime_module.ledger,
                "build_consent_delegation_record",
                side_effect=ledger.LedgerError("pull_request.head_sha must be a non-blank string"),
            ),
        ):
            path = Path(tmpdir) / "run-ledger.jsonl"
            result = self._run(plan, tmpdir, _LiveIo(), ledger_path=path)
            written = path.exists()
        self.assertFalse(written)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.warnings), 1, result.warnings)
        self.assertIn("LedgerError: pull_request.head_sha must be", result.warnings[0])

    def test_no_ledger_path_records_nothing(self):
        plan = self._plan(851)
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(swarm_runtime_module.ledger, "append_record") as append,
        ):
            result = self._run(plan, tmpdir, _LiveIo(), ledger_path=None)
        self.assertEqual((result.status, result.warnings), ("success", ()))
        append.assert_not_called()


# ---------------------------------------------------------------------------------------
# #1278: worktree hygiene — honest removal, settlement by outcome, prune, orphan recovery.
# ---------------------------------------------------------------------------------------


class _NoRmtree:
    """``shutil.rmtree`` in swarm_runtime replaced by a no-op: a directory that cannot be
    removed, without depending on permissions (which Windows and root do not honour)."""

    def __enter__(self):
        self._patch = patch("keel.swarm_runtime.shutil.rmtree")
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def _answer(ok_for=(), fail_for=(), output="git said no"):
    """A runner that fails every command naming a word in ``fail_for`` and records all."""
    calls: list[list[str]] = []

    def run(cmd, cwd):
        calls.append(list(cmd))
        failed = any(word in cmd for word in fail_for)
        return CommandResult(not failed, 1 if failed else 0, output if failed else "")

    return run, calls


class RemovalReportsTheTruth(unittest.TestCase):
    """#1278: `remove_swarm_worktree` returned True unconditionally, and its caller
    discarded the answer, so a worktree that could not be removed was never mentioned."""

    def test_a_directory_that_survives_is_reported_and_its_registration_pruned(self):
        with tempfile.TemporaryDirectory() as tmpdir, _NoRmtree():
            wt = Path(tmpdir) / "wt"
            wt.mkdir()
            run, calls = _answer(fail_for=("remove",))
            self.assertFalse(remove_swarm_worktree(Path(tmpdir), wt, runner=run))
            self.assertEqual(calls[-1], ["git", "worktree", "prune"])

    def test_what_git_leaves_on_disk_is_removed_and_nothing_pruned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wt = Path(tmpdir) / "wt"
            (wt / "sub").mkdir(parents=True)
            run, calls = _answer()
            self.assertTrue(remove_swarm_worktree(Path(tmpdir), wt, runner=run))
            self.assertFalse(wt.exists())
            self.assertEqual(len(calls), 1)

    def test_a_failed_removal_is_a_warning_not_a_deleted_branch(self):
        from keel.swarm_runtime import settle_live_worktree

        remove = swarm_worker.worktree_disposal(
            ok=False, worktree_created=True, implementer_ran=False
        )
        with tempfile.TemporaryDirectory() as tmpdir, _NoRmtree():
            wt = Path(tmpdir) / "wt"
            wt.mkdir()
            run, calls = _answer(fail_for=("remove",))
            got = settle_live_worktree(Path(tmpdir), wt, "swarm/s/c", remove, runner=run)
        self.assertEqual(got["worktree_state"], "remove-failed")
        self.assertEqual(got["worktree"], str(wt))
        self.assertFalse(got["branch_deleted"])
        self.assertIn("could not be removed", got["warnings"][0])
        self.assertNotIn("branch", [c[1] for c in calls])

    def test_a_branch_git_will_not_delete_is_a_warning(self):
        from keel.swarm_runtime import settle_live_worktree

        remove = swarm_worker.worktree_disposal(
            ok=False, worktree_created=True, implementer_ran=False
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            run, calls = _answer(fail_for=("branch",))
            got = settle_live_worktree(Path(tmpdir), Path(tmpdir) / "wt", "swarm/s/c", remove, run)
        self.assertEqual((got["worktree_state"], got["worktree"]), ("removed", None))
        self.assertEqual(calls[-1], ["git", "branch", "-D", "swarm/s/c"])
        self.assertEqual(
            got["warnings"], ["the branch swarm/s/c could not be deleted: git said no"]
        )

    def test_the_orchestration_records_a_failed_removal(self):
        s1 = IssueScope(issue=761, title="T", predicted_files=("src/761.py",))
        plan = build_swarm_plan([s1], swarm_id="swarm-stuck")
        with tempfile.TemporaryDirectory() as tmpdir, _NoRmtree():
            result = run_swarm_orchestration(
                plan,
                "projects/x.yaml",
                root=tmpdir,
                dry_run=False,
                live=_live(plan, tmpdir, _LiveIo()),
                runner=_Git(fail="remove"),
                base_branch="main",
            )
            state = load_swarm_state("swarm-stuck", root=tmpdir)
            (cid,) = [c.cluster_id for c in _clusters(plan)]
            stuck = str(build_worktree_path("swarm-stuck", cid, Path(tmpdir).resolve()))
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.warnings), 1)
        self.assertTrue(result.warnings[0].startswith(f"{cid}: the worktree {stuck}"))
        self.assertIn(f"warning       : {cid}: the worktree", render_swarm_run_result(result))
        (worker,) = state.workers
        self.assertEqual((worker.worktree, worker.pushed), (stuck, True))
        self.assertEqual(result.to_dict()["warnings"], list(result.warnings))


class AWorkerThatRaisesIsStillSettled(unittest.TestCase):
    """#1278: the worktree is settled whichever way the worker ends. One that raises before
    its seat ran (here, writing the brief) had nothing to keep: both are removed."""

    def test_a_raise_before_the_seat_removes_the_worktree_and_the_branch(self):
        s1 = IssueScope(issue=781, title="T", predicted_files=("src/781.py",))
        plan = build_swarm_plan([s1], swarm_id="swarm-brief")
        (cid,) = [c.cluster_id for c in _clusters(plan)]
        git, io_ = _Git(), _LiveIo()
        with tempfile.TemporaryDirectory() as tmpdir:
            # A directory where the brief goes: writing it raises.
            build_brief_path("swarm-brief", cid, Path(tmpdir).resolve()).mkdir(parents=True)
            with redirect_stderr(io.StringIO()):
                result = run_swarm_orchestration(
                    plan,
                    "projects/x.yaml",
                    root=tmpdir,
                    dry_run=False,
                    live=_live(plan, tmpdir, io_),
                    runner=git,
                    base_branch="main",
                )
            left = (Path(tmpdir) / ".keel" / "worktrees").exists()
        (res,) = result.wave_results[0]["cluster_results"].values()
        self.assertIn("worker raised", res["output"])
        self.assertEqual(io_.implemented, [])
        self.assertEqual(len(git.ran("remove")), 1)
        self.assertEqual(git.calls[-1], ["git", "branch", "-D", f"swarm/swarm-brief/{cid}"])
        self.assertFalse(left)


class TheWorkerRecordRoundTrips(unittest.TestCase):
    def test_worktree_and_pushed_survive_a_save_an_update_and_a_load(self):
        from keel.swarm import save_swarm_state

        seed = SwarmWorkerStatus(cluster_id="c", issue=1, role="core")
        state = SwarmRunState(swarm_id="rt", total_workers=1, workers=(seed,))
        state = update_worker_state(state, "c", worktree="/w/rt/c", pushed=True)
        state = update_worker_state(state, "c", status="failed")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_swarm_state(state, root=tmpdir)
            (worker,) = load_swarm_state("rt", root=tmpdir).workers
            self.assertEqual((worker.worktree, worker.pushed), ("/w/rt/c", True))
            path = Path(tmpdir) / ".keel" / "state" / "swarm" / "rt.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["workers"][0].update(worktree=None, pushed="yes")
            path.write_text(json.dumps(data), encoding="utf-8")
            (worker,) = load_swarm_state("rt", root=tmpdir).workers
        # Only a written `true` is a push: `--clean` keeps a pushed branch on its word.
        self.assertEqual((worker.worktree, worker.pushed), ("", False))


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class WorktreesAreSettledAgainstARealRepository(unittest.TestCase):
    """#1278, measured against a real git, offline: what a live worker leaves behind, the
    prune that lets a crashed run's id be reused, and `swarm-status --orphans/--clean`."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        empty = self.tmp / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        environ = patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            del os.environ[key]
        os.environ.update(
            GIT_CONFIG_GLOBAL=str(empty),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@example.invalid",
        )
        self.remote, self.repo = self.tmp / "remote.git", self.tmp / "repo"
        self._git("init", "-q", "--bare", str(self.remote), cwd=self.tmp)
        self._git("init", "-q", "-b", "main", str(self.repo), cwd=self.tmp)
        (self.repo / "README").write_text("base\n", encoding="utf-8")
        self._git("add", "README")
        self._git("commit", "-q", "-m", "base")
        self._git("remote", "add", "origin", str(self.remote))
        self.base = self.repo / ".keel" / "worktrees"
        self.gates_ok = True

    def _git(self, *args, cwd=None):
        result = _git_cli(list(args), cwd or self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _implement(self, plan, env):
        (Path(plan.cwd) / "work.txt").write_text("work\n", encoding="utf-8")
        return {"ok": True}

    def _runner(self, cmd, cwd):
        if "run-gates" in cmd:
            return CommandResult(self.gates_ok, 0 if self.gates_ok else 1, "gates")
        return default_runner(cmd, cwd, env=swarm_worker.worker_env(os.environ))

    def _plan(self, swarm_id, issue=751):
        plan = build_swarm_plan(
            [IssueScope(issue=issue, title=f"T{issue}", predicted_files=(f"src/{issue}.py",))],
            swarm_id=swarm_id,
        )
        (cluster,) = _clusters(plan)
        return plan, cluster.cluster_id

    def _run(self, plan):
        from keel.swarm_runtime import _default_push

        live = replace(
            _live(plan, self.repo, _LiveIo()), implement=self._implement, push=_default_push
        )
        result = run_swarm_orchestration(
            plan,
            "projects/x.yaml",
            root=self.repo,
            dry_run=False,
            live=live,
            runner=self._runner,
            base_branch="main",
        )
        (res,) = result.wave_results[0]["cluster_results"].values()
        return result, res

    def _branches(self):
        return set(self._git("for-each-ref", "--format=%(refname:short)", "refs/heads/").split())

    def _registered(self):
        listed = self._git("worktree", "list", "--porcelain")
        return {Path(e.path).resolve() for e in swarm_worker.parse_worktree_list(listed)}

    def test_a_successful_worker_keeps_its_branch_and_leaves_no_directory(self):
        plan, cid = self._plan("swarm-ok")
        result, res = self._run(plan)
        self.assertEqual((result.status, res["worktree_state"]), ("success", "removed"))
        self.assertIn(f"swarm/swarm-ok/{cid}", self._branches())
        self.assertEqual(self._registered(), {self.repo})
        # The per-run directory, and `.keel/worktrees` with it, go once nothing is in them.
        self.assertFalse(self.base.exists())
        (worker,) = load_swarm_state("swarm-ok", root=self.repo).workers
        self.assertEqual((worker.worktree, worker.pushed), ("", True))
        self.assertEqual(result.warnings, ())

    def test_a_worker_that_failed_after_its_seat_ran_keeps_its_worktree(self):
        self.gates_ok = False
        plan, cid = self._plan("swarm-red")
        result, res = self._run(plan)
        kept = self.base / "swarm-red" / cid
        self.assertEqual((res["stage"], res["worktree_state"]), ("gates", "kept"))
        self.assertTrue((kept / "work.txt").exists(), "the seat's work must still be there")
        self.assertIn(kept, self._registered())
        self.assertIn(f"swarm/swarm-red/{cid}", self._branches())
        self.assertIn(f"kept for inspection at {kept}", res["output"])
        (worker,) = load_swarm_state("swarm-red", root=self.repo).workers
        self.assertEqual((worker.worktree, worker.pushed), (str(kept), False))
        self.assertIn("--clean", worker.details)

    def test_a_worker_that_failed_before_its_seat_ran_removes_what_it_cut(self):
        self._git("remote", "remove", "origin")
        plan, cid = self._plan("swarm-early")
        _result, res = self._run(plan)
        self.assertEqual((res["stage"], res["worktree_state"]), ("push", "removed"))
        self.assertTrue(res["branch_deleted"])
        self.assertNotIn(f"swarm/swarm-early/{cid}", self._branches())
        self.assertEqual(self._registered(), {self.repo})
        self.assertFalse(self.base.exists())

    def test_a_crashed_runs_stale_registration_does_not_stop_the_same_id(self):
        """A run killed, its directory then deleted: the registration stays, and `git
        worktree add -B` of the same branch refused ("already used by worktree")."""
        plan, cid = self._plan("swarm-again")
        stale = self.base / "swarm-again" / cid
        self._git("worktree", "add", "-q", "-B", f"swarm/swarm-again/{cid}", str(stale), "main")
        shutil.rmtree(stale)
        result, res = self._run(plan)
        self.assertEqual((result.status, res.get("stage")), ("success", "done"), res["output"])

    def test_a_worktree_that_cannot_be_created_is_left_alone_and_named(self):
        """Not created by this worker — here, a previous run's kept worktree — so nothing
        is removed, and the reason says how to clean it."""
        self.gates_ok = False
        plan, cid = self._plan("swarm-twice")
        self._run(plan)
        _result, res = self._run(plan)
        self.assertEqual((res["stage"], res["worktree_state"]), ("worktree", "none"))
        self.assertIn("--swarm-id swarm-twice --clean", res["output"])
        self.assertTrue((self.base / "swarm-twice" / cid / "work.txt").exists())

    def _status(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["swarm-status", ".keel/project.yaml", "--root", str(self.repo), *extra])
        return code, out.getvalue(), err.getvalue()

    def _leftovers(self, *extra):
        code, out, _err = self._status("--orphans", "--json", *extra)
        self.assertEqual(code, 0)
        return {x["target"]: (x["kind"], x["action"]) for x in json.loads(out)["leftovers"]}

    def _scene(self):
        """Leftovers of every kind, and things that are not keel's, side by side."""
        from keel.swarm import save_swarm_state

        self.gates_ok = False
        red, red_cid = self._plan("swarm-red", issue=752)
        self._run(red)  # completed; its failed worker's worktree and branch are kept
        crashed = self.base / "swarm-crashed" / "cluster-1-9"
        self._git("worktree", "add", "-q", "-b", "swarm/swarm-crashed/cluster-1-9", str(crashed))
        save_swarm_state(
            SwarmRunState(
                swarm_id="swarm-crashed",
                total_workers=1,
                workers=(SwarmWorkerStatus(cluster_id="cluster-1-9", issue=9, role="core"),),
            ),
            root=self.repo,
        )
        save_swarm_state(
            SwarmRunState(
                swarm_id="swarm-pr",
                total_workers=2,
                workers=(
                    SwarmWorkerStatus(cluster_id="c5", issue=5, role="core", pull_request=5),
                    SwarmWorkerStatus(cluster_id="c6", issue=6, role="core", pushed=True),
                ),
                completed_at="2026-09-30T00:00:00Z",
            ),
            root=self.repo,
        )
        for branch in ("swarm/swarm-pr/c5", "swarm/swarm-pr/c6", "swarm/nostate/c1"):
            self._git("branch", branch)
        # A cluster directory git no longer knows: a finished run's, so it goes.
        (self.base / "swarm-pr" / "c7" / "partial").mkdir(parents=True)
        # Not keel's: a worktree outside its paths, one inside them at another depth, and
        # branches outside its namespace or of another shape.
        self._git("worktree", "add", "-q", "-b", "feature/x", str(self.tmp / "other"))
        deep = self.base / "deep" / "a" / "b"
        self._git("worktree", "add", "-q", "-b", "deep-branch", str(deep))
        self._git("branch", "swarm/deep/a/b")
        (self.base / "swarm-empty").mkdir(parents=True)
        (self.base / "stray.txt").write_text("not a run\n", encoding="utf-8")
        stale = self.base / "swarm-stale" / "c1"
        self._git("worktree", "add", "-q", "-b", "swarm/swarm-stale/c1", str(stale))
        shutil.rmtree(stale)
        return red_cid, crashed, deep, stale

    def test_orphans_are_listed_and_only_keels_own_are_cleaned(self):
        red_cid, crashed, deep, stale = self._scene()
        red_wt = self.base / "swarm-red" / red_cid
        listed = self._leftovers()
        self.assertEqual(listed[str(red_wt)], ("worktree", "remove"))
        self.assertEqual(listed[f"swarm/swarm-red/{red_cid}"], ("branch", "remove"))
        self.assertEqual(listed[str(crashed)], ("worktree", "keep"))
        self.assertEqual(listed["swarm/swarm-crashed/cluster-1-9"], ("branch", "keep"))
        self.assertEqual(listed["swarm/swarm-pr/c5"], ("branch", "keep"))
        self.assertEqual(listed["swarm/swarm-pr/c6"], ("branch", "keep"))
        self.assertEqual(listed["swarm/nostate/c1"], ("branch", "keep"))
        self.assertEqual(listed[str(self.base / "swarm-empty")], ("directory", "remove"))
        self.assertEqual(listed[str(self.base / "swarm-pr" / "c7")], ("directory", "remove"))
        self.assertEqual(listed[str(stale)][0], "registration")
        for other in (
            "feature/x",
            "deep-branch",
            "swarm/deep/a/b",
            str(deep),
            str(self.base / "deep"),
        ):
            with self.subTest(not_keels=other):
                self.assertNotIn(other, listed)
        self.assertNotIn(str(self.base / "deep" / "a"), listed)

        code, out, _err = self._status("--clean", "--json")
        report = json.loads(out)
        self.assertEqual((code, report["cleaned"], report["failed"]), (0, True, []))
        self.assertFalse(red_wt.exists())
        self.assertFalse((self.base / "swarm-red").exists())
        self.assertFalse((self.base / "swarm-empty").exists())
        self.assertFalse((self.base / "swarm-pr").exists())
        self.assertNotIn(stale, self._registered())
        branches = self._branches()
        self.assertNotIn(f"swarm/swarm-red/{red_cid}", branches)
        for kept in (
            "swarm/swarm-crashed/cluster-1-9",
            "swarm/swarm-pr/c5",
            "swarm/swarm-pr/c6",
            "swarm/nostate/c1",
            "feature/x",
            "deep-branch",
            "swarm/deep/a/b",
        ):
            with self.subTest(kept=kept):
                self.assertIn(kept, branches)
        self.assertTrue(crashed.exists())
        self.assertTrue((deep / "README").exists())
        self.assertTrue((self.tmp / "other" / "README").exists())
        self.assertTrue((self.base / "stray.txt").exists())

        # Naming the unfinished run is the operator's word that it is not running.
        code, out, _err = self._status("--clean", "--swarm-id", "swarm-crashed")
        self.assertEqual(code, 0, out)
        self.assertFalse(crashed.exists())
        self.assertNotIn("swarm/swarm-crashed/cluster-1-9", self._branches())
        self.assertIn("removed 2, failed 0", out)

    def test_the_text_listing_says_what_clean_would_do(self):
        code, out, _err = self._status("--orphans")
        self.assertEqual(code, 0)
        self.assertIn("nothing: no swarm worktree", out)
        self._scene()
        code, out, _err = self._status("--orphans", "--swarm-id", "swarm-crashed")
        self.assertEqual(code, 0)
        self.assertIn("keel swarm leftovers — swarm run swarm-crashed", out)
        self.assertIn("remove worktree     swarm-crashed/cluster-1-9", out)
        self.assertIn("--clean removes the ones marked remove", out)
        code, out, _err = self._status("--orphans", "--swarm-id", "nostate")
        self.assertNotIn("--clean removes", out)
        self.assertIn("keep   branch       nostate/c1", out)

    def test_git_that_cannot_list_is_an_error(self):
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(
                ["swarm-status", ".keel/project.yaml", "--root", str(plain), "--orphans", "--json"]
            )
        self.assertEqual((code, json.loads(out.getvalue())["error_code"]), (1, "git-failed"))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["swarm-status", ".keel/project.yaml", "--root", str(plain), "--clean"])
        self.assertEqual((code, out.getvalue()), (1, ""))
        self.assertIn("swarm-status: git could not list", err.getvalue())

    def test_a_dry_worker_that_raises_names_no_worktree(self):
        """Only a live worker has a worktree to keep; a dry one runs in the checkout."""
        s1 = IssueScope(issue=771, title="T", predicted_files=("src/771.py",))
        plan = build_swarm_plan([s1], swarm_id="swarm-dry-raise")

        def raising(cmd, cwd):
            raise RuntimeError("dry boom")

        with redirect_stderr(io.StringIO()):
            result = run_swarm_orchestration(
                plan, "projects/x.yaml", root=self.repo, runner=raising, base_branch="main"
            )
        (res,) = result.wave_results[0]["cluster_results"].values()
        self.assertIn("worker raised RuntimeError: dry boom", res["output"])
        self.assertNotIn("worktree", res)


class CleaningReportsWhatItCouldNotRemove(unittest.TestCase):
    """#1278: every removal `--clean` attempts is checked, and a failure is named."""

    def test_each_kind_of_failure_is_reported(self):
        from keel.swarm_runtime import clean_swarm_leftovers

        def item(kind, target, cluster="c"):
            return swarm_worker.SwarmLeftover(kind, "s", cluster, target, "remove", "r")

        with tempfile.TemporaryDirectory() as tmpdir, _NoRmtree():
            root = Path(tmpdir)
            wt, loose, full = root / "wt", root / "loose", root / ".keel" / "worktrees" / "s"
            for d in (wt, loose, full):
                d.mkdir(parents=True)
            (full / "left.txt").write_text("x", encoding="utf-8")
            kept = swarm_worker.SwarmLeftover("branch", "s", "k", "swarm/s/k", "keep", "pushed")
            run, calls = _answer(fail_for=("prune", "remove", "branch"))
            removed, failed = clean_swarm_leftovers(
                root,
                (
                    item("registration", "/gone"),
                    item("worktree", str(wt)),
                    item("directory", str(loose)),
                    item("branch", "swarm/s/c"),
                    item("directory", str(full), cluster=""),
                    kept,
                ),
                runner=run,
            )
        self.assertEqual(removed, [])
        self.assertEqual(
            [(x.kind, why) for x, why in failed],
            [
                ("registration", "git worktree prune failed: git said no"),
                ("worktree", "the directory is still there"),
                ("directory", "the directory is still there"),
                ("branch", "git said no"),
                ("directory", "the directory is not empty"),
            ],
        )
        self.assertNotIn(["git", "branch", "-D", "swarm/s/k"], calls)

    def test_a_ref_listing_that_fails_is_the_error(self):
        from keel.swarm_runtime import find_swarm_leftovers

        run, _calls = _answer(fail_for=("for-each-ref",), output="refs broke")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(
                find_swarm_leftovers(tmpdir, runner=run),
                ((), "git could not list the worktrees or branches: refs broke"),
            )

    def test_a_failed_clean_exits_1_and_names_the_failure(self):
        failed = swarm_worker.SwarmLeftover("branch", "s", "c", "swarm/s/c", "remove", "r")
        out = io.StringIO()
        with (
            patch("keel.swarm_runtime.find_swarm_leftovers", return_value=((failed,), "")),
            patch("keel.swarm_runtime.clean_swarm_leftovers", return_value=([], [(failed, "no")])),
            redirect_stdout(out),
        ):
            code = main(["swarm-status", ".keel/project.yaml", "--clean"])
        self.assertEqual(code, 1)
        self.assertIn("removed 0, failed 1", out.getvalue())
        self.assertIn("failed branch swarm/s/c: no", out.getvalue())


def _parse(stamp: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(stamp)


class AWorkerReportsItsWaveStageAndTime(unittest.TestCase):
    """#1280 item 2: a worker record had no wave, no stage and no times, so `step` jumped
    `s0 → s4 → s10`, the board could not group by wave, and a run in flight showed nothing
    of how far each worker had got."""

    def _plan(self, *issues, overlap=False):
        return build_swarm_plan(
            [
                IssueScope(
                    issue=n,
                    title=f"T{n}",
                    predicted_files=("src/shared.py",) if overlap else (f"src/{n}.py",),
                )
                for n in issues
            ],
            swarm_id="swarm-progress",
        )

    def _live_run(self, plan, tmpdir, io_, git):
        return run_swarm_orchestration(
            plan,
            "projects/x.yaml",
            root=tmpdir,
            dry_run=False,
            live=_live(plan, tmpdir, io_),
            max_workers=2,
            runner=git,
            base_branch="main",
        )

    def test_a_live_worker_records_each_stage_as_it_enters_it(self):
        plan = self._plan(801)
        (cluster,) = _clusters(plan)
        seen: list[str] = []
        real_save = swarm_runtime_module.save_swarm_state

        def recording_save(state, root="."):
            (worker,) = state.workers
            if not seen or seen[-1] != worker.stage:
                seen.append(worker.stage)
            return real_save(state, root=root)

        class _Watching(_LiveIo):
            """Reads the state file while the implementer runs — what swarm-status reads."""

            def implement(self, plan_, env):
                self.during = load_swarm_state("swarm-progress", root=tmpdir)
                return super().implement(plan_, env)

        io_ = _Watching()
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(swarm_runtime_module, "save_swarm_state", recording_save),
        ):
            result = self._live_run(plan, tmpdir, io_, _Git())
            state = load_swarm_state("swarm-progress", root=tmpdir)

        self.assertEqual(result.status, "success")
        self.assertEqual(
            seen,
            [
                "",
                "consent",
                "worktree",
                "implement",
                "tamper",
                "commit",
                "gates",
                "push",
                "pull_request",
                "done",
            ],
        )
        # Every stage it entered is one the worker module names, in its order.
        self.assertEqual([s for s in swarm_worker.STAGES if s in seen], seen[1:])
        # In flight, the file on disk says where the worker is and since when.
        (during,) = io_.during.workers
        self.assertEqual(
            (during.status, during.stage, during.finished_at), ("running", "implement", "")
        )
        self.assertTrue(during.started_at)
        assert state is not None
        (worker,) = state.workers
        self.assertEqual(
            (worker.cluster_id, worker.wave, worker.stage), (cluster.cluster_id, 1, "done")
        )
        self.assertLessEqual(_parse(worker.started_at), _parse(worker.finished_at))
        self.assertIsNotNone(worker.elapsed_s())

    def test_a_worker_that_stops_records_the_stage_that_stopped_it(self):
        # An implementer that changed nothing is found at `commit` but stopped as
        # `implement`: the record says what the result says, not the last stage entered.
        for git, stopped in ((_Git(gates_ok=False), "gates"), (_Git(clean=True), "implement")):
            plan = self._plan(802)
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as tmpdir:
                self._live_run(plan, tmpdir, _LiveIo(), git)
                state = load_swarm_state("swarm-progress", root=tmpdir)
                assert state is not None
                (worker,) = state.workers
                self.assertEqual((worker.status, worker.stage), ("failed", stopped))
                self.assertTrue(worker.finished_at)

    def test_a_worker_that_raises_keeps_the_last_stage_it_entered(self):
        plan = self._plan(803)
        with tempfile.TemporaryDirectory() as tmpdir, redirect_stderr(io.StringIO()):
            self._live_run(plan, tmpdir, _LiveIo(raise_for=803), _Git())
            state = load_swarm_state("swarm-progress", root=tmpdir)
        assert state is not None
        (worker,) = state.workers
        self.assertEqual((worker.status, worker.stage), ("failed", "implement"))
        self.assertTrue(worker.finished_at)

    def test_the_record_says_both_where_the_worker_stopped_and_what_it_left(self):
        """#1278 settles the worktree and #1280 records the stage; one record carries both.
        A worker that failed after its seat ran — returned or raised — keeps its worktree
        and names the stage; one that opened its pull request leaves none, and says done."""
        cases = (
            ("gates", _LiveIo(), _Git(gates_ok=False), "failed", True, False),
            # Stopped as `implement` after entering `commit`: the stage is the result's.
            ("implement", _LiveIo(), _Git(clean=True), "failed", True, False),
            ("implement", _LiveIo(raise_for=804), _Git(), "failed", True, False),
            ("done", _LiveIo(), _Git(), "passed", False, True),
        )
        for stage, io_, git, status, kept, pushed in cases:
            plan = self._plan(804)
            (cluster,) = _clusters(plan)
            with (
                self.subTest(stage=stage),
                tempfile.TemporaryDirectory() as tmpdir,
                redirect_stderr(io.StringIO()),
            ):
                self._live_run(plan, tmpdir, io_, git)
                state = load_swarm_state("swarm-progress", root=tmpdir)
                wt_path = build_worktree_path(
                    plan.swarm_id, cluster.cluster_id, Path(tmpdir).resolve()
                )
                assert state is not None
                (worker,) = state.workers
                self.assertEqual((worker.status, worker.stage, worker.wave), (status, stage, 1))
                self.assertTrue(worker.started_at and worker.finished_at)
                self.assertEqual(worker.pushed, pushed)
                self.assertEqual(worker.worktree, str(wt_path) if kept else "")
                self.assertEqual(wt_path.exists(), kept)

    def test_a_dry_worker_is_running_only_once_it_starts_and_each_records_its_wave(self):
        """Each wave's workers were all marked `running` when the wave began. A dry run's
        workers take turns, so the second read as running — with no start time — while it
        was still waiting for the first."""
        orthogonal = self._plan(811, 812)
        stacked = self._plan(821, 822, overlap=True)
        self.assertEqual(len(orthogonal.waves), 1)
        self.assertEqual(len(stacked.waves), 2)
        for plan, waves in ((orthogonal, [1, 1]), (stacked, [1, 2])):
            others_when_first_ran: list[str] = []

            def runner(cmd, cwd, plan=plan, others=others_when_first_ran):
                if not others:
                    state = load_swarm_state("swarm-progress", root=tmpdir)
                    issue = int(cmd[cmd.index("--issue") + 1])
                    others.extend(w.status for w in state.workers if w.issue != issue)
                return CommandResult(True, 0, "ok")

            with self.subTest(waves=waves), tempfile.TemporaryDirectory() as tmpdir:
                run_swarm_orchestration(
                    plan,
                    ".keel/project.yaml",
                    root=tmpdir,
                    dry_run=True,
                    max_workers=4,
                    runner=runner,
                    base_branch="main",
                )
                state = load_swarm_state("swarm-progress", root=tmpdir)
                assert state is not None
                self.assertEqual(others_when_first_ran, ["queued"])
                self.assertEqual([w.wave for w in state.workers], waves)
                for w in state.workers:
                    # A dry run's child reports no stages, but it does start and end.
                    self.assertEqual((w.status, w.stage), ("passed", ""))
                    self.assertLessEqual(_parse(w.started_at), _parse(w.finished_at))


class NoParameterIsReachableOnlyFromTestsWithoutSayingSo(unittest.TestCase):
    """#1280 item 7: parameters no production caller passes. `create_worktrees` could only
    ever agree with `dry_run`, and `pr_diff_map` could only relabel a landing's mode, so
    both are gone; each `runner` is kept, and says it is a seam for tests."""

    def test_the_dead_parameters_are_gone_and_the_seams_are_named(self):
        self.assertNotIn("create_worktrees", inspect.signature(run_swarm_orchestration).parameters)
        self.assertNotIn("pr_diff_map", inspect.signature(land_wave_clusters).parameters)
        for fn in (run_swarm_orchestration, land_wave_clusters):
            self.assertIn("runner", inspect.signature(fn).parameters)
            self.assertIn("``runner`` is an injection seam for tests", fn.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
