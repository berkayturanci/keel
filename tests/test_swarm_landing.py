"""Unit tests for Keel Swarm orthogonal batch landing & drift self-healing engine."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from keel.cli import main
from keel.runner import CommandResult
from keel.swarm import (
    IssueScope,
    SwarmCluster,
    SwarmPlan,
    SwarmRunState,
    SwarmWave,
    SwarmWorkerStatus,
    build_swarm_plan,
    evaluate_wave_landing_mode,
    render_swarm_landing_result,
    save_swarm_state,
)
from keel.swarm_landing import (
    is_safe_declarative_chunk,
    land_wave_clusters,
    merge_cluster_branch,
    parse_conflict_hunks,
    rebase_and_heal_cluster_branch,
    resolve_adjacent_conflict,
    resolve_conflict_content,
)


class TestConflictHealing(unittest.TestCase):
    def test_is_safe_declarative_chunk(self):
        self.assertTrue(is_safe_declarative_chunk(["   ", "import os", "from sys import path"]))
        self.assertFalse(is_safe_declarative_chunk(["x = 1"]))

    def test_parse_conflict_hunks(self):
        sample = """
header line
<<<<<<< HEAD
import os
import sys
=======
import json
import math
>>>>>>> feat/new-feature
footer line
"""
        hunks = parse_conflict_hunks(sample)
        self.assertEqual(len(hunks), 1)
        self.assertIn("import os", hunks[0]["ours"])
        self.assertIn("import json", hunks[0]["theirs"])

    def test_resolve_adjacent_conflict_empty_keeps_a_declarative_side(self):
        # One branch added an import, the other added nothing there. Safe, and
        # the case the empty-side path exists for.
        self.assertEqual(resolve_adjacent_conflict("", "import sys\n"), "import sys\n")
        self.assertEqual(resolve_adjacent_conflict("import os\n", ""), "import os\n")

    def test_an_empty_side_does_not_wave_arbitrary_code_through(self):
        """A delete-versus-modify conflict prints an empty side (#798).

        This used to return the non-empty side untouched, so `swarm-land` would
        write a deleted function back and stage it — the caller acts on the
        result without review, so refusing is the only way a human sees it.

        The previous test passed `"theirs\\n"` and `"ours\\n"`: single bare words
        that happen to be safe declarative content, so the assertion held while
        the branch waved anything through.
        """
        body = "def critical_auth_check(user):\n    return user.is_admin\n"
        self.assertIsNone(resolve_adjacent_conflict("", body))
        self.assertIsNone(resolve_adjacent_conflict(body, ""))

    def test_resolve_adjacent_conflict_disjoint_and_overlap(self):
        ours = "import os\n"
        theirs = "import sys\n"
        res = resolve_adjacent_conflict(ours, theirs)
        self.assertEqual(res, "import os\nimport sys\n")

        # Overlapping conflict cannot resolve safely
        ours_dup = "x = 1\n"
        theirs_dup = "x = 2\n"
        self.assertIsNone(resolve_adjacent_conflict(ours_dup, theirs_dup))

    def test_declarative_chunks_bullets_quotes_and_comments(self):
        ours = "- feature A\n* feature B\n// comment\n/* block */\n\n\"entry1\",\n'entry2',\n"
        theirs = "- feature C\n\"entry3\",\n"
        res = resolve_adjacent_conflict(ours, theirs)
        self.assertIsNotNone(res)
        self.assertIn("feature A", res)
        self.assertIn("feature C", res)

    def test_resolve_adjacent_conflict_duplicate_import(self):
        ours = "import os\n"
        theirs = "import os\n"
        self.assertIsNone(resolve_adjacent_conflict(ours, theirs))

    def test_resolve_conflict_content(self):
        clean = "def foo():\n    return 42\n"
        self.assertEqual(resolve_conflict_content(clean), clean)

        conflict_resolvable = (
            "header\n"
            "<<<<<<< HEAD\n"
            "import os\n"
            "=======\n"
            "import sys\n"
            ">>>>>>> branch\n"
            "footer\n"
        )
        resolved = resolve_conflict_content(conflict_resolvable)
        self.assertIsNotNone(resolved)
        self.assertIn("import os", resolved)
        self.assertIn("import sys", resolved)
        self.assertNotIn("<<<<<<<", resolved)

        conflict_unresolvable = (
            "header\n"
            "<<<<<<< HEAD\n"
            "val = 1\n"
            "=======\n"
            "val = 2\n"
            ">>>>>>> branch\n"
        )
        self.assertIsNone(resolve_conflict_content(conflict_unresolvable))


class TestSwarmLandingPureLogic(unittest.TestCase):
    def test_evaluate_wave_landing_mode_single_and_disjoint(self):
        c1 = SwarmCluster(
            cluster_id="c1", issues=(101,), role="core", combined_scope=("src/a.py",)
        )
        w_single = SwarmWave(
            wave_index=1,
            mode="orthogonal_parallel",
            eligible_direct_landing=True,
            clusters=(c1,),
        )
        dec_single = evaluate_wave_landing_mode(w_single, {})
        self.assertTrue(dec_single.eligible)
        self.assertEqual(dec_single.mode, "direct_batch")
        self.assertEqual(dec_single.reason, "single_cluster")
        d_dict = dec_single.to_dict()
        self.assertEqual(d_dict["mode"], "direct_batch")

        # Disjoint multi-cluster
        c2 = SwarmCluster(
            cluster_id="c2", issues=(102,), role="docs", combined_scope=("docs/a.md",)
        )
        w_multi = SwarmWave(
            wave_index=1,
            mode="orthogonal_parallel",
            eligible_direct_landing=True,
            clusters=(c1, c2),
        )
        dec_multi = evaluate_wave_landing_mode(
            w_multi, {"c1": ["src/a.py"], "c2": ["docs/a.md"]}
        )
        self.assertTrue(dec_multi.eligible)
        self.assertEqual(dec_multi.mode, "direct_batch")
        self.assertEqual(dec_multi.reason, "orthogonal_diff_trees")

    def test_evaluate_wave_landing_mode_overlapping_diffs(self):
        c1 = SwarmCluster(
            cluster_id="c1", issues=(101,), role="core", combined_scope=("src/common.py",)
        )
        c2 = SwarmCluster(
            cluster_id="c2", issues=(102,), role="core", combined_scope=("src/common.py",)
        )
        w_overlap = SwarmWave(
            wave_index=1,
            mode="sequential_dependent",
            eligible_direct_landing=False,
            clusters=(c1, c2),
        )
        dec_overlap = evaluate_wave_landing_mode(
            w_overlap, {"c1": ["src/common.py"], "c2": ["src/common.py"]}
        )
        self.assertFalse(dec_overlap.eligible)
        self.assertEqual(dec_overlap.mode, "sequential_funnel")
        self.assertEqual(dec_overlap.reason, "overlapping_diff_trees")

    def test_render_swarm_landing_result(self):
        from keel.swarm import SwarmLandingResult

        res_ok = SwarmLandingResult(
            swarm_id="swarm-ok",
            wave_index=1,
            mode="direct_batch",
            landed_clusters=("c1", "c2"),
            healed_clusters=(),
            failed_clusters=(),
            status="success",
        )
        out_ok = render_swarm_landing_result(res_ok)
        self.assertIn("keel swarm land — swarm-ok (wave 1)", out_ok)
        self.assertIn("status  : success ✓", out_ok)
        self.assertIn("landed  : c1, c2", out_ok)
        self.assertIn("healed  : none", out_ok)

        res_partial = SwarmLandingResult(
            swarm_id="swarm-partial",
            wave_index=2,
            mode="sequential_funnel",
            landed_clusters=("c1",),
            healed_clusters=("c1",),
            failed_clusters=("c2",),
            status="partial_failure",
        )
        out_partial = render_swarm_landing_result(res_partial)
        self.assertIn("status  : partial_failure ⚠️", out_partial)
        self.assertIn("healed  : c1", out_partial)
        self.assertIn("failed  : c2", out_partial)


class TestSwarmLandingThinIO(unittest.TestCase):
    def test_rebase_and_heal_cluster_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)
            calls: list[list[str]] = []

            # Clean rebase
            def mock_clean_runner(cmd: list[str], cwd: Path) -> CommandResult:
                calls.append(cmd)
                return CommandResult(ok=True, code=0, output="rebased")

            ok, reason = rebase_and_heal_cluster_branch(
                p_root, "branch-1", runner=mock_clean_runner
            )
            self.assertTrue(ok)
            self.assertEqual(reason, "clean_rebase")

            # Conflicting rebase without resolvable files
            def mock_conflict_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "status" in cmd:
                    return CommandResult(ok=True, code=0, output="")
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="aborted")

            ok_c, reason_c = rebase_and_heal_cluster_branch(
                p_root, "branch-2", runner=mock_conflict_runner
            )
            self.assertFalse(ok_c)
            self.assertEqual(reason_c, "conflict_detected")

            # Self-healing rebase on resolvable conflict file
            conflict_file = p_root / "src" / "feature.py"
            conflict_file.parent.mkdir(parents=True, exist_ok=True)
            conflict_file.write_text(
                "import os\n<<<<<<< HEAD\nimport sys\n=======\nimport json\n>>>>>>> feat/b\n",
                encoding="utf-8",
            )

            def mock_heal_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "status" in cmd:
                    return CommandResult(ok=True, code=0, output="UU src/feature.py")
                if "--continue" in cmd:
                    return CommandResult(ok=True, code=0, output="rebased")
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            ok_h, reason_h = rebase_and_heal_cluster_branch(
                p_root, "branch-heal", runner=mock_heal_runner
            )
            self.assertTrue(ok_h)
            self.assertEqual(reason_h, "self_healed_rebase")
            self.assertNotIn("<<<<<<<", conflict_file.read_text(encoding="utf-8"))

            # Self-healing rebase where rebase --continue fails
            conflict_file.write_text(
                "import os\n<<<<<<< HEAD\nimport sys\n=======\nimport json\n>>>>>>> feat/b\n",
                encoding="utf-8",
            )

            def mock_continue_fail_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "status" in cmd:
                    return CommandResult(ok=True, code=0, output="UU src/feature.py")
                if "--continue" in cmd:
                    return CommandResult(ok=False, code=1, output="continue failed")
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            ok_cf, reason_cf = rebase_and_heal_cluster_branch(
                p_root, "branch-fail-cont", runner=mock_continue_fail_runner
            )
            self.assertFalse(ok_cf)
            self.assertEqual(reason_cf, "conflict_detected")

            # Self-healing rebase where file does not exist on disk
            def mock_missing_file_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "status" in cmd:
                    return CommandResult(ok=True, code=0, output="UU src/nonexistent.py")
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            ok_mf, reason_mf = rebase_and_heal_cluster_branch(
                p_root, "branch-missing", runner=mock_missing_file_runner
            )
            self.assertFalse(ok_mf)
            self.assertEqual(reason_mf, "conflict_detected")

            # Self-healing rebase where conflict cannot be resolved
            unresolvable_file = p_root / "src" / "unresolvable.py"
            unresolvable_file.write_text(
                "val = 1\n<<<<<<< HEAD\nval = 2\n=======\nval = 3\n>>>>>>> feat/c\n",
                encoding="utf-8",
            )

            def mock_unresolvable_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "status" in cmd:
                    return CommandResult(ok=True, code=0, output="UU src/unresolvable.py")
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            ok_un, reason_un = rebase_and_heal_cluster_branch(
                p_root, "branch-unres", runner=mock_unresolvable_runner
            )
            self.assertFalse(ok_un)
            self.assertEqual(reason_un, "conflict_detected")

            # Self-healing rebase where reading file triggers OSError / is dir
            dir_conflict = p_root / "src" / "dir_conflict"
            dir_conflict.mkdir(parents=True, exist_ok=True)

            def mock_oserror_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "status" in cmd:
                    return CommandResult(ok=True, code=0, output="UU src/dir_conflict")
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            ok_oe, reason_oe = rebase_and_heal_cluster_branch(
                p_root, "branch-oserror", runner=mock_oserror_runner
            )
            self.assertFalse(ok_oe)
            self.assertEqual(reason_oe, "conflict_detected")

    def test_merge_cluster_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_root = Path(tmpdir)

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=True, code=0, output="merged")

            ok = merge_cluster_branch(p_root, "branch-1", runner=mock_runner)
            self.assertTrue(ok)

    def test_land_wave_clusters_dry_run_and_nonexistent_wave(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-land-test")

            # Nonexistent wave
            res_none = land_wave_clusters(
                plan, wave_index=99, project_yaml=".keel/project.yaml", root=tmpdir
            )
            self.assertEqual(res_none.status, "failed")

            # Dry run wave 1
            res_dry = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
            )
            self.assertEqual(res_dry.status, "success")
            self.assertEqual(len(res_dry.landed_clusters), 1)

    def test_land_wave_clusters_live_direct_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-live-batch")

            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-101", issue=101, role="core", status="passed"
            )
            w2 = SwarmWorkerStatus(
                cluster_id="cluster-1-102", issue=102, role="core", status="passed"
            )
            st = SwarmRunState(swarm_id="swarm-live-batch", total_workers=2, workers=(w1, w2))
            save_swarm_state(st, root=tmpdir)

            # Runner where 101 succeeds and 102 fails
            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "cluster-1-101" in " ".join(cmd):
                    return CommandResult(ok=True, code=0, output="merged")
                if "cluster-1-102" in " ".join(cmd):
                    return CommandResult(ok=False, code=1, output="merge rejected")
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_runner,
            )
            self.assertEqual(res.status, "partial_failure")
            self.assertIn("cluster-1-101", res.landed_clusters)
            self.assertIn("cluster-1-102", res.failed_clusters)

    def test_land_wave_clusters_live_without_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-no-state")

            def mock_ok_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_ok_runner,
            )
            self.assertEqual(res.status, "success")

            def mock_fail_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=False, code=1, output="fail")

            res_fail = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_fail_runner,
            )
            self.assertEqual(res_fail.status, "failed")

    def test_land_wave_clusters_live_sequential_funnel_healing_and_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            c1 = SwarmCluster(
                cluster_id="cluster-1-1", issues=(1,), role="core", combined_scope=("src/c.py",)
            )
            c2 = SwarmCluster(
                cluster_id="cluster-1-2", issues=(2,), role="core", combined_scope=("src/c.py",)
            )
            w1 = SwarmWave(
                wave_index=1,
                mode="sequential_dependent",
                eligible_direct_landing=False,
                clusters=(c1, c2),
            )
            plan = SwarmPlan(swarm_id="swarm-funnel", total_issues=2, waves=(w1,))

            w_st1 = SwarmWorkerStatus(
                cluster_id="cluster-1-1", issue=1, role="core", status="passed"
            )
            w_st2 = SwarmWorkerStatus(
                cluster_id="cluster-1-2", issue=2, role="core", status="passed"
            )
            st = SwarmRunState(swarm_id="swarm-funnel", total_workers=2, workers=(w_st1, w_st2))
            save_swarm_state(st, root=tmpdir)

            # Mock diff map forcing sequential funnel
            diff_map = {"cluster-1-1": ["src/c.py"], "cluster-1-2": ["src/c.py"]}

            # Case 1: Rebase ok and merge ok
            def mock_heal_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=True, code=0, output="rebased/merged")

            res_heal = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map=diff_map,
                runner=mock_heal_runner,
            )
            self.assertEqual(res_heal.status, "success")
            self.assertEqual(len(res_heal.healed_clusters), 2)

            # Case 2: Rebase fails with conflict
            def mock_conflict_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "rebase" in cmd and "--abort" not in cmd:
                    return CommandResult(ok=False, code=1, output="conflict")
                return CommandResult(ok=True, code=0, output="ok")

            res_conf = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map=diff_map,
                runner=mock_conflict_runner,
            )
            self.assertEqual(res_conf.status, "failed")

            # Case 3: Rebase ok but merge fails
            def mock_merge_fail_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "merge" in cmd:
                    return CommandResult(ok=False, code=1, output="merge failed")
                return CommandResult(ok=True, code=0, output="ok")

            res_mf = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map=diff_map,
                runner=mock_merge_fail_runner,
            )
            self.assertEqual(res_mf.status, "failed")

            # Case 4: Sequential funnel without state
            with tempfile.TemporaryDirectory() as tmp_nostate:
                res_nostate_ok = land_wave_clusters(
                    plan,
                    wave_index=1,
                    project_yaml=".keel/project.yaml",
                    root=tmp_nostate,
                    dry_run=False,
                    pr_diff_map=diff_map,
                    runner=mock_heal_runner,
                )
                self.assertEqual(res_nostate_ok.status, "success")

                res_nostate_fail = land_wave_clusters(
                    plan,
                    wave_index=1,
                    project_yaml=".keel/project.yaml",
                    root=tmp_nostate,
                    dry_run=False,
                    pr_diff_map=diff_map,
                    runner=mock_conflict_runner,
                )
                self.assertEqual(res_nostate_fail.status, "failed")

                res_nostate_mf = land_wave_clusters(
                    plan,
                    wave_index=1,
                    project_yaml=".keel/project.yaml",
                    root=tmp_nostate,
                    dry_run=False,
                    pr_diff_map=diff_map,
                    runner=mock_merge_fail_runner,
                )
                self.assertEqual(res_nostate_mf.status, "failed")


class TestSwarmLandCLI(unittest.TestCase):
    def test_swarm_land_cli_missing_and_invalid_config(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["swarm-land", "nonexistent.yaml"])
        self.assertEqual(code, 1)
        self.assertIn("no such config", buf.getvalue())

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("invalid_root_key: true\n")
            path = tf.name

        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                code = main(["swarm-land", path])
            self.assertEqual(code, 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_swarm_land_cli_dry_run_and_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-714", issue=714, role="docs", status="passed"
            )
            st = SwarmRunState(swarm_id="swarm-land-cli", total_workers=1, workers=(w1,))
            save_swarm_state(st, root=tmpdir)

            # Auto-discovery & text output
            buf_text = io.StringIO()
            with redirect_stdout(buf_text):
                code = main([
                    "swarm-land",
                    ".keel/project.yaml",
                    "--root",
                    tmpdir,
                    "--issues",
                    "#714,bad,714",
                    "--issue",
                    "714",
                ])
            self.assertEqual(code, 0)
            self.assertIn("keel swarm land — swarm-land-cli", buf_text.getvalue())

            # JSON mode with explicit swarm-id and flags
            buf_json = io.StringIO()
            with redirect_stdout(buf_json):
                code_json = main([
                    "swarm-land",
                    ".keel/project.yaml",
                    "--root",
                    tmpdir,
                    "--swarm-id",
                    "swarm-land-cli",
                    "--issue-title",
                    "Docs update",
                    "--json",
                ])
            self.assertEqual(code_json, 0)
            data = json.loads(buf_json.getvalue())
            self.assertEqual(data["status"], "success")

            # Empty issues branch & state dir without json files
            state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
            for f in state_dir.glob("*.json"):
                f.unlink()

            buf_empty = io.StringIO()
            with redirect_stdout(buf_empty):
                code_empty = main([
                    "swarm-land",
                    ".keel/project.yaml",
                    "--root",
                    tmpdir,
                ])
            self.assertEqual(code_empty, 1)

            with tempfile.TemporaryDirectory() as tmp_fresh:
                buf_no_state = io.StringIO()
                with redirect_stdout(buf_no_state):
                    code_no_state = main([
                        "swarm-land",
                        ".keel/project.yaml",
                        "--root",
                        tmp_fresh,
                    ])
                self.assertEqual(code_no_state, 1)


if __name__ == "__main__":
    unittest.main()
