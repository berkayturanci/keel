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
    load_swarm_state,
    render_swarm_landing_result,
    save_swarm_state,
)
from keel.swarm_landing import (
    EvidenceCheck,
    _restore_pin,
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
        theirs = '- feature C\n"entry3",\n'
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
            "header\n<<<<<<< HEAD\nimport os\n=======\nimport sys\n>>>>>>> branch\nfooter\n"
        )
        resolved = resolve_conflict_content(conflict_resolvable)
        self.assertIsNotNone(resolved)
        self.assertIn("import os", resolved)
        self.assertIn("import sys", resolved)
        self.assertNotIn("<<<<<<<", resolved)

        conflict_unresolvable = "header\n<<<<<<< HEAD\nval = 1\n=======\nval = 2\n>>>>>>> branch\n"
        self.assertIsNone(resolve_conflict_content(conflict_unresolvable))


class TestSwarmLandingPureLogic(unittest.TestCase):
    def test_evaluate_wave_landing_mode_single_and_disjoint(self):
        c1 = SwarmCluster(cluster_id="c1", issues=(101,), role="core", combined_scope=("src/a.py",))
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
        dec_multi = evaluate_wave_landing_mode(w_multi, {"c1": ["src/a.py"], "c2": ["docs/a.md"]})
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
        self.assertNotIn("held", out_ok)

        res_held = SwarmLandingResult(
            swarm_id="swarm-held",
            wave_index=1,
            mode="direct_batch",
            landed_clusters=("c1",),
            healed_clusters=(),
            failed_clusters=(),
            status="partial_failure",
            held_clusters=(("c2", "PR #10: missing evidence: review-verdict-1"),),
        )
        out_held = render_swarm_landing_result(res_held)
        self.assertIn("held    : review evidence missing — not landed", out_held)
        self.assertIn("c2: PR #10: missing evidence: review-verdict-1", out_held)

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
                plan,
                wave_index=99,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                evidence_checker=None,
                base_branch="main",
            )
            self.assertEqual(res_none.status, "failed")

            # Dry run wave 1
            res_dry = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                evidence_checker=None,
                base_branch="main",
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
                evidence_checker=None,
                base_branch="main",
            )
            self.assertEqual(res.status, "partial_failure")
            self.assertIn("cluster-1-101", res.landed_clusters)
            self.assertIn("cluster-1-102", res.failed_clusters)

    def test_land_wave_clusters_holds_clusters_without_review_evidence(self):
        """#828: a cluster whose evidence does not verify is held, never merged.

        Held is not failed — the code is intact, the independent-review
        contract is simply unsatisfied — but it degrades the wave status the
        same way, because "success" must mean "everything landed"."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-evid")

            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-101", issue=101, role="core", status="passed"
            )
            w2 = SwarmWorkerStatus(
                cluster_id="cluster-1-102", issue=102, role="core", status="passed"
            )
            st = SwarmRunState(swarm_id="swarm-evid", total_workers=2, workers=(w1, w2))
            save_swarm_state(st, root=tmpdir)

            merged_branches: list[str] = []

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=True, code=0, output="a" * 40)
                if "merge" in cmd:
                    merged_branches.append(cmd[-2])
                return CommandResult(ok=True, code=0, output="ok")

            def checker(branch: str) -> tuple[bool, str]:
                if "cluster-1-101" in branch:
                    return EvidenceCheck(True, "PR #9: evidence verified", "a" * 40)
                return EvidenceCheck(False, "PR #10: missing evidence: review-verdict-1")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_runner,
                evidence_checker=checker,
                base_branch="main",
            )
            self.assertIn("cluster-1-101", res.landed_clusters)
            self.assertEqual(
                res.held_clusters,
                (("cluster-1-102", "PR #10: missing evidence: review-verdict-1"),),
            )
            self.assertNotIn("cluster-1-102", res.failed_clusters)
            # the held cluster's branch must never have reached git merge
            self.assertFalse(any("cluster-1-102" in b for b in merged_branches))
            self.assertEqual(res.status, "partial_failure")
            # the worker state records the hold with its reason
            reloaded = load_swarm_state("swarm-evid", root=tmpdir)
            held_worker = next(w for w in reloaded.workers if w.cluster_id == "cluster-1-102")
            self.assertEqual(held_worker.status, "held")
            self.assertIn("review evidence", held_worker.details)
            # serialization carries the held pair
            self.assertEqual(
                res.to_dict()["held_clusters"],
                [["cluster-1-102", "PR #10: missing evidence: review-verdict-1"]],
            )

    def test_funnel_holds_when_the_heal_authored_unreviewed_content(self):
        """The jury's major: a rebase that *resolved conflicts* writes bytes
        nobody reviewed, so it must hold. A clean replay of the reviewed
        commits does not — otherwise funnel mode, which exists precisely
        because the base moved, could never land anything."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-funnel-heal")
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-101", issue=101, role="core", status="passed"
            )
            save_swarm_state(
                SwarmRunState(swarm_id="swarm-funnel-heal", total_workers=1, workers=(w1,)),
                root=tmpdir,
            )
            merged: list[str] = []

            def healing_runner(cmd: list[str], cwd: Path) -> CommandResult:
                joined = " ".join(cmd)
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=True, code=0, output="a" * 40)
                if cmd[:2] == ["git", "rebase"] and "--continue" not in joined:
                    return CommandResult(ok=False, code=1, output="CONFLICT")
                if "status" in joined:
                    return CommandResult(ok=True, code=0, output="UU src/shared.py")
                if "merge" in cmd:
                    merged.append(cmd[-2])
                return CommandResult(ok=True, code=0, output="ok")

            conflicted = Path(tmpdir) / "src"
            conflicted.mkdir(parents=True, exist_ok=True)
            (conflicted / "shared.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map={
                    "cluster-1-101": ["src/shared.py"],
                    "cluster-1-102": ["src/shared.py"],
                },
                runner=healing_runner,
                resolver=lambda _text: "resolved\n",
                evidence_checker=lambda b: EvidenceCheck(True, "verified", "a" * 40),
                base_branch="main",
            )
            self.assertTrue(res.held_clusters, res)
            reason = res.held_clusters[0][1]
            self.assertIn("resolved conflicts", reason)
            self.assertIn("rebase and re-review the PR", reason)
            self.assertIn("restored to the reviewed commit", reason)
            self.assertEqual(merged, [])
            reloaded = load_swarm_state("swarm-funnel-heal", root=tmpdir)
            self.assertTrue(any(w.status == "held" for w in reloaded.workers))

    def test_funnel_reconfirms_the_pin_before_the_rebase_touches_the_branch(self):
        """The jury's second major: the pre-lock check is network-bound, and on
        the funnel path the rebase itself voids the pin — so the re-read must
        happen before the rebase, on this arm too, not only on direct-batch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-funnel-drift")
            touched: list[str] = []

            def drifted_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if cmd[:2] == ["git", "rev-parse"]:
                    # a commit landed on the branch after the evidence check
                    return CommandResult(ok=True, code=0, output="9" * 40)
                if cmd[:2] == ["git", "rebase"] or "merge" in cmd:
                    touched.append(" ".join(cmd))
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map={
                    "cluster-1-101": ["src/shared.py"],
                    "cluster-1-102": ["src/shared.py"],
                },
                runner=drifted_runner,
                evidence_checker=lambda b: EvidenceCheck(True, "verified", "a" * 40),
                base_branch="main",
            )
            self.assertEqual(res.mode, "sequential_funnel")
            self.assertTrue(res.held_clusters)
            self.assertIn("branch tip moved", res.held_clusters[0][1])
            self.assertEqual(touched, [], "the rebase must not run on a drifted branch")

    def test_restore_pin_reports_every_outcome_honestly(self):
        """The operator must learn the branch was touched, whatever happened."""
        calls: list[list[str]] = []

        def ok_runner(cmd: list[str], cwd: Path) -> CommandResult:
            calls.append(cmd)
            return CommandResult(ok=True, code=0, output="")

        def failing_runner(cmd: list[str], cwd: Path) -> CommandResult:
            return CommandResult(ok=False, code=1, output="denied")

        sha = "f" * 40
        self.assertIn(
            "restored to the reviewed commit",
            _restore_pin(Path("/tmp"), "swarm/x/c1", sha, ok_runner),
        )
        self.assertEqual(calls[0][:2], ["git", "update-ref"])
        self.assertIn("refs/heads/swarm/x/c1", calls[0])
        self.assertIn(
            "could not be reset",
            _restore_pin(Path("/tmp"), "swarm/x/c1", sha, failing_runner),
        )
        self.assertIn(
            "no pinned commit to restore",
            _restore_pin(Path("/tmp"), "swarm/x/c1", None, ok_runner),
        )

    def test_funnel_clean_rebase_lands_with_the_gate_on(self):
        """The design's load-bearing positive case: a clean replay of the
        reviewed commits must land. If `clean_rebase` is ever renamed, funnel
        mode silently becomes a permanent no-op — this is what catches that."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-clean-rebase")
            sha = "d" * 40
            merged: list[str] = []

            def clean_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=True, code=0, output=sha)
                if "merge" in cmd:
                    merged.append(cmd[-2])
                # `git rebase` succeeds -> rebase_and_heal returns "clean_rebase"
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map={
                    "cluster-1-101": ["src/shared.py"],
                    "cluster-1-102": ["src/shared.py"],
                },
                runner=clean_runner,
                evidence_checker=lambda b: EvidenceCheck(True, "verified", sha),
                base_branch="main",
            )
            self.assertEqual(res.mode, "sequential_funnel")
            self.assertEqual(res.held_clusters, ())
            self.assertTrue(res.landed_clusters, "a clean rebase must land")
            self.assertTrue(merged)

    def test_dry_run_predicts_holds_instead_of_promising_a_landing(self):
        """A preview that ignores the gate over-promises: it is what a driver
        reads to decide whether to attempt the wave."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-dry-predict")

            def checker(branch: str):
                if "101" in branch:
                    return EvidenceCheck(True, "verified", "a" * 40)
                return EvidenceCheck(False, "missing evidence: review-verdict-1")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                evidence_checker=checker,
                base_branch="main",
            )
            self.assertIn("cluster-1-101", res.landed_clusters)
            self.assertEqual(len(res.held_clusters), 1)
            self.assertIn("would hold:", res.held_clusters[0][1])
            self.assertIn("review-verdict-1", res.held_clusters[0][1])
            self.assertEqual(res.status, "partial_failure")

            # an unusable answer is predicted as a hold too, never as landable
            res_bad = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=True,
                evidence_checker=lambda _b: None,
                base_branch="main",
            )
            self.assertEqual(res_bad.landed_clusters, ())
            self.assertIn("unusable answer", res_bad.held_clusters[0][1])

    def test_an_answer_without_a_pinned_commit_is_refused(self):
        """A pass the merge cannot re-confirm is not a usable answer.

        The previous version of this test was named for a guarantee and then
        asserted its absence: a bare pair yielded head_sha=None, _pin_drifted
        returned early, and the drift window silently reopened while the suite
        stayed green. Fail closed instead."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-bare-pair")
            merged: list[str] = []

            def runner(cmd: list[str], cwd: Path) -> CommandResult:
                if "merge" in cmd:
                    merged.append(cmd[-2])
                return CommandResult(ok=True, code=0, output="ok")

            for answer, expected in (
                ((True, "verified"), "unusable answer"),
                (True, "unusable answer"),
                (None, "unusable answer"),
                ((True, "verified", "a" * 40, "extra"), "unusable answer"),
                # a plain 3-tuple is accepted and coerced, but a pass with no
                # pinned commit is still refused
                (EvidenceCheck(True, "verified", None), "without a pinned commit"),
                ((True, "verified", ""), "without a pinned commit"),
            ):
                with self.subTest(answer=type(answer).__name__):
                    res = land_wave_clusters(
                        plan,
                        wave_index=1,
                        project_yaml=".keel/project.yaml",
                        root=tmpdir,
                        dry_run=False,
                        runner=runner,
                        evidence_checker=lambda _b, a=answer: a,
                        base_branch="main",
                    )
                    self.assertTrue(res.held_clusters, res)
                    self.assertIn(expected, res.held_clusters[0][1])
            self.assertEqual(merged, [], "nothing may land on an unusable answer")

    def test_base_branch_must_be_stated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-nobase")
            with self.assertRaises(TypeError) as ctx:
                land_wave_clusters(
                    plan,
                    wave_index=1,
                    project_yaml=".keel/project.yaml",
                    root=tmpdir,
                    dry_run=True,
                    evidence_checker=None,
                )
            self.assertIn("explicit base_branch", str(ctx.exception))

    def test_direct_batch_reconfirms_the_pin_inside_the_lock(self):
        """The pre-lock check is network-bound; the tip can move before the
        merge. A cheap local re-read closes that window."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-pin-window")
            merged: list[str] = []

            def moving_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=True, code=0, output="b" * 40)
                if "merge" in cmd:
                    merged.append(cmd[-2])
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=moving_runner,
                evidence_checker=lambda b: EvidenceCheck(True, "verified", "a" * 40),
                base_branch="main",
            )
            self.assertTrue(res.held_clusters)
            self.assertIn("branch tip moved", res.held_clusters[0][1])
            self.assertEqual(merged, [])

    def test_pin_reconfirm_covers_unreadable_tip_and_stateful_hold(self):
        """The lock-time re-read must fail closed when git cannot answer, and
        record the hold in worker state when there is state to record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-pin-unreadable")
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-101", issue=101, role="core", status="passed"
            )
            save_swarm_state(
                SwarmRunState(swarm_id="swarm-pin-unreadable", total_workers=1, workers=(w1,)),
                root=tmpdir,
            )

            def blind_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=False, code=1, output="")
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=blind_runner,
                evidence_checker=lambda b: EvidenceCheck(True, "verified", "a" * 40),
                base_branch="main",
            )
            self.assertIn("cannot re-read the branch tip", res.held_clusters[0][1])
            reloaded = load_swarm_state("swarm-pin-unreadable", root=tmpdir)
            self.assertTrue(any(w.status == "held" for w in reloaded.workers))

    def test_pin_reconfirm_passes_when_the_tip_is_unchanged(self):
        """The happy path: pinned sha still current -> the merge proceeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-pin-ok")
            sha = "c" * 40

            def steady_runner(cmd: list[str], cwd: Path) -> CommandResult:
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=True, code=0, output=sha + "\n")
                return CommandResult(ok=True, code=0, output="ok")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=steady_runner,
                evidence_checker=lambda b: EvidenceCheck(True, "verified", sha),
                base_branch="main",
            )
            self.assertEqual(res.held_clusters, ())
            self.assertIn("cluster-1-101", res.landed_clusters)

    def test_funnel_heal_hold_without_state_is_still_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            s2 = IssueScope(issue=102, title="B", predicted_files=("src/b.py",))
            plan = build_swarm_plan([s1, s2], swarm_id="swarm-heal-nostate")

            def healing_runner(cmd: list[str], cwd: Path) -> CommandResult:
                joined = " ".join(cmd)
                if cmd[:2] == ["git", "rev-parse"]:
                    return CommandResult(ok=True, code=0, output="a" * 40)
                if cmd[:2] == ["git", "rebase"] and "--continue" not in joined:
                    return CommandResult(ok=False, code=1, output="CONFLICT")
                if "status" in joined:
                    return CommandResult(ok=True, code=0, output="UU src/shared.py")
                return CommandResult(ok=True, code=0, output="ok")

            d = Path(tmpdir) / "src"
            d.mkdir(parents=True, exist_ok=True)
            (d / "shared.py").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")

            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                pr_diff_map={
                    "cluster-1-101": ["src/shared.py"],
                    "cluster-1-102": ["src/shared.py"],
                },
                runner=healing_runner,
                resolver=lambda _t: "resolved\n",
                evidence_checker=lambda b: EvidenceCheck(True, "verified", "a" * 40),
                base_branch="main",
            )
            self.assertTrue(res.held_clusters)
            self.assertIn("resolved conflicts", res.held_clusters[0][1])

    def test_base_branch_flows_into_both_merge_and_rebase(self):
        """The jury's blocking major: verifying a PR against config.base_branch
        while merging into a hardcoded main blesses a diff that lands
        elsewhere."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-base")
            seen: list[str] = []

            def recording_runner(cmd: list[str], cwd: Path) -> CommandResult:
                seen.append(" ".join(cmd))
                return CommandResult(ok=True, code=0, output="ok")

            land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=recording_runner,
                evidence_checker=None,
                base_branch="develop",
            )
            self.assertTrue(
                any("checkout develop" in c for c in seen),
                f"merge must target the configured base: {seen}",
            )
            self.assertFalse(any("checkout main" in c for c in seen), seen)

    def test_land_wave_clusters_requires_an_explicit_evidence_choice(self):
        """Omitting the argument must raise; None is the typed opt-out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-explicit")
            with self.assertRaises(TypeError) as ctx:
                land_wave_clusters(
                    plan,
                    wave_index=1,
                    project_yaml=".keel/project.yaml",
                    root=tmpdir,
                    dry_run=True,
                    base_branch="main",
                )
            self.assertIn("explicit evidence_checker", str(ctx.exception))

    def test_evidence_checks_run_before_the_merge_lock_is_taken(self):
        """Network-bound checks must not hold the global merge lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-lock-order")
            order: list[str] = []

            def checker(branch: str) -> tuple[bool, str]:
                order.append("check")
                return False, "no open PR for the cluster branch"

            def mock_runner(cmd: list[str], cwd: Path) -> CommandResult:
                order.append("git")
                return CommandResult(ok=True, code=0, output="ok")

            land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_runner,
                evidence_checker=checker,
                base_branch="main",
            )
            # held before any git work happened at all
            self.assertEqual(order, ["check"])

    def test_land_wave_clusters_all_held_is_failed_and_none_checker_skips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = IssueScope(issue=101, title="A", predicted_files=("src/a.py",))
            plan = build_swarm_plan([s1], swarm_id="swarm-evid2")

            def mock_ok_runner(cmd: list[str], cwd: Path) -> CommandResult:
                return CommandResult(ok=True, code=0, output="ok")

            # every cluster held -> the wave cannot claim any success
            res = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_ok_runner,
                evidence_checker=lambda _b: (False, "no open PR for the cluster branch"),
                base_branch="main",
            )
            self.assertEqual(res.status, "failed")
            self.assertEqual(res.landed_clusters, ())

            # checker=None preserves the legacy behavior byte for byte
            res2 = land_wave_clusters(
                plan,
                wave_index=1,
                project_yaml=".keel/project.yaml",
                root=tmpdir,
                dry_run=False,
                runner=mock_ok_runner,
                evidence_checker=None,
                base_branch="main",
            )
            self.assertIn("cluster-1-101", res2.landed_clusters)
            self.assertEqual(res2.held_clusters, ())

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
                evidence_checker=None,
                base_branch="main",
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
                evidence_checker=None,
                base_branch="main",
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
                evidence_checker=None,
                base_branch="main",
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
                evidence_checker=None,
                base_branch="main",
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
                evidence_checker=None,
                base_branch="main",
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
                    evidence_checker=None,
                    base_branch="main",
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
                    evidence_checker=None,
                    base_branch="main",
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
                    evidence_checker=None,
                    base_branch="main",
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

    def test_swarm_land_cli_review_knob_off_logs_and_skips(self):
        """#828: skipping review must be a visible, configured exception."""
        import unittest.mock as mock

        from keel import cli as cli_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-714", issue=714, role="docs", status="passed"
            )
            st = SwarmRunState(swarm_id="swarm-knob", total_workers=1, workers=(w1,))
            save_swarm_state(st, root=tmpdir)

            captured: dict[str, object] = {}

            def fake_land(plan, **kwargs):
                captured.update(kwargs)
                from keel.swarm import SwarmLandingResult

                return SwarmLandingResult(
                    swarm_id=plan.swarm_id,
                    wave_index=1,
                    mode="direct_batch",
                    landed_clusters=(),
                    healed_clusters=(),
                    failed_clusters=(),
                    status="failed",
                )

            with (
                mock.patch("keel.swarm_landing.land_wave_clusters", side_effect=fake_land),
                mock.patch.object(cli_mod.cfg, "load_config") as load_cfg,
            ):
                config = cli_mod.cfg.ProjectConfig(
                    extends="keel",
                    core_version="^0.7",
                    base_branch="main",
                    knobs=cli_mod.cfg.Knobs(build_gate_cmd="true", swarm_review_evidence=False),
                )
                load_cfg.return_value = config
                buf_out, buf_err = io.StringIO(), io.StringIO()
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    main(
                        [
                            "swarm-land",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue",
                            "714",
                            "--swarm-id",
                            "swarm-knob",
                            "--live",
                            "--json",
                        ]
                    )
            self.assertIsNone(captured["evidence_checker"])
            # the opt-out is loud on stderr and must never corrupt --json stdout
            self.assertIn("swarm review evidence: OFF by config", buf_err.getvalue())
            json.loads(buf_out.getvalue())

            # knob on (default) -> a callable checker is passed
            with (
                mock.patch("keel.swarm_landing.land_wave_clusters", side_effect=fake_land),
                mock.patch.object(cli_mod.cfg, "load_config") as load_cfg,
            ):
                config_on = cli_mod.cfg.ProjectConfig(
                    extends="keel",
                    core_version="^0.7",
                    base_branch="main",
                    knobs=cli_mod.cfg.Knobs(build_gate_cmd="true"),
                )
                load_cfg.return_value = config_on
                with redirect_stdout(io.StringIO()):
                    main(
                        [
                            "swarm-land",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue",
                            "714",
                            "--swarm-id",
                            "swarm-knob",
                            "--live",
                        ]
                    )
            self.assertTrue(callable(captured["evidence_checker"]))

            # knob off + dry run -> no checker, and no banner either (the
            # opt-out is announced when it actually applies to a landing)
            with (
                mock.patch("keel.swarm_landing.land_wave_clusters", side_effect=fake_land),
                mock.patch.object(cli_mod.cfg, "load_config") as load_cfg,
            ):
                load_cfg.return_value = config
                buf_err_dry = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(buf_err_dry):
                    main(
                        [
                            "swarm-land",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue",
                            "714",
                            "--swarm-id",
                            "swarm-knob",
                        ]
                    )
            self.assertIsNone(captured["evidence_checker"])
            self.assertEqual(buf_err_dry.getvalue(), "")

            # dry run with the knob on -> the checker IS built, so the
            # preview can report what a live run would hold (read-only)
            with (
                mock.patch("keel.swarm_landing.land_wave_clusters", side_effect=fake_land),
                mock.patch.object(cli_mod.cfg, "load_config") as load_cfg,
            ):
                load_cfg.return_value = config_on
                with redirect_stdout(io.StringIO()):
                    main(
                        [
                            "swarm-land",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue",
                            "714",
                            "--swarm-id",
                            "swarm-knob",
                        ]
                    )
            self.assertTrue(callable(captured["evidence_checker"]))

    def test_swarm_land_cli_exits_nonzero_when_clusters_are_held(self):
        """Automation keys on the exit code: refusing to land unreviewed code
        must not read as success."""
        import unittest.mock as mock

        from keel.swarm import SwarmLandingResult

        with tempfile.TemporaryDirectory() as tmpdir:
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-714", issue=714, role="docs", status="passed"
            )
            save_swarm_state(
                SwarmRunState(swarm_id="swarm-exit", total_workers=1, workers=(w1,)),
                root=tmpdir,
            )

            def fake_land(plan, **kwargs):
                return SwarmLandingResult(
                    swarm_id=plan.swarm_id,
                    wave_index=1,
                    mode="direct_batch",
                    landed_clusters=("cluster-1-714",),
                    healed_clusters=(),
                    failed_clusters=(),
                    status="partial_failure",
                    held_clusters=(("cluster-1-999", "missing evidence"),),
                )

            with mock.patch("keel.swarm_landing.land_wave_clusters", side_effect=fake_land):
                with redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            "swarm-land",
                            ".keel/project.yaml",
                            "--root",
                            tmpdir,
                            "--issue",
                            "714",
                            "--swarm-id",
                            "swarm-exit",
                            "--live",
                        ]
                    )
            # one cluster landed, so status is partial_failure (exit 0 before);
            # a held cluster must still fail the command
            self.assertEqual(code, 1)

    def test_swarm_land_evidence_checker_paths(self):
        """Every fail-closed arm of the default checker, plus the pass."""
        import argparse
        import unittest.mock as mock

        from keel import cli as cli_mod
        from keel.runner import CommandResult as RunResult

        args = argparse.Namespace(root=".", path=".keel/project.yaml")
        config = cli_mod.cfg.ProjectConfig(
            extends="keel",
            core_version="^0.7",
            base_branch="main",
            knobs=cli_mod.cfg.Knobs(build_gate_cmd="true"),
        )
        check = cli_mod._swarm_land_evidence_checker(args, config)

        captured_cmd: list[list[str]] = []

        def lookup(stdout: str, ok: bool = True):
            def _run(cmd, **kwargs):
                captured_cmd.append(list(cmd))
                return RunResult(ok=ok, code=0 if ok else 1, output=stdout, stdout=stdout)

            return mock.patch.object(cli_mod, "run_argv", side_effect=_run)

        # The jury's major: a CommandResult that carries the payload in
        # `output` with `stdout` empty must not degrade to "no open PR" — that
        # would hold every cluster forever behind a misleading reason, and the
        # fail-closed design makes the breakage look like correct behaviour.
        def output_only(cmd, **kwargs):
            payload = '[{"number": 7, "state": "OPEN"}]'
            return RunResult(ok=True, code=0, output=payload, stdout="")

        with (
            mock.patch.object(cli_mod, "run_argv", side_effect=output_only),
            mock.patch.object(
                cli_mod,
                "_verify_merge_evidence",
                return_value={"enforced": False, "verification": {"status": "pass"}},
            ),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn(
            "gate is not armed",
            reason,
            "the lookup must read `output` when `stdout` is empty, not fall "
            f"through to 'no open PR' (got: {reason})",
        )

        # transport failure
        with lookup("boom", ok=False):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("PR lookup failed", reason)
        # the lookup is base-filtered so a retargeted PR can never verify a
        # diff the wave will not land
        self.assertIn("--base", captured_cmd[0])
        self.assertIn("main", captured_cmd[0])

        # invalid JSON
        with lookup("not json"):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("invalid JSON", reason)

        # JSON but not a list -> treated as no PR, held
        with lookup('{"number": 7}'):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("no open PR", reason)

        # no PR at all
        with lookup("[]"):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("no open PR", reason)

        # already merged -> honest retry reason, still held
        with lookup('[{"number": 7, "state": "MERGED"}]'):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("already merged", reason)

        # ambiguous: two open PRs for one branch
        with lookup('[{"number": 7, "state": "OPEN"}, {"number": 8, "state": "OPEN"}]'):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("ambiguous: 2 open PRs", reason)

        # verification raises -> held with the exception type, never a crash
        with (
            lookup('[{"number": 7, "state": "OPEN"}]'),
            mock.patch.object(cli_mod, "_verify_merge_evidence", side_effect=KeyError("boom")),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("errored: KeyError", reason)

        # gate not armed
        with (
            lookup('[{"number": 7, "state": "OPEN"}]'),
            mock.patch.object(
                cli_mod,
                "_verify_merge_evidence",
                return_value={"enforced": False, "verification": {"status": "pass"}},
            ),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("not armed", reason)

        # verdicts missing
        with (
            lookup('[{"number": 7, "state": "OPEN"}]'),
            mock.patch.object(
                cli_mod,
                "_verify_merge_evidence",
                return_value={
                    "enforced": True,
                    "verification": {"status": "fail", "missing": ["review-verdict-1"]},
                },
            ),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("missing evidence: review-verdict-1", reason)

        SHA = "a" * 40

        def lookup_and_rev(pr_json: str, rev_stdout: str, rev_ok: bool = True):
            def _run(cmd, **kwargs):
                if cmd[:2] == ["git", "rev-parse"]:
                    return RunResult(
                        ok=rev_ok, code=0 if rev_ok else 1, output=rev_stdout, stdout=rev_stdout
                    )
                return RunResult(ok=True, code=0, output=pr_json, stdout=pr_json)

            return mock.patch.object(cli_mod, "run_argv", side_effect=_run)

        verified = {
            "enforced": True,
            "verification": {"status": "pass"},
            "head_sha": SHA,
        }

        # local tip drifted from the reviewed head -> held
        with (
            lookup_and_rev('[{"number": 7, "state": "OPEN"}]', "b" * 40),
            mock.patch.object(cli_mod, "_verify_merge_evidence", return_value=verified),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("is not the reviewed PR head", reason)

        # local tip unresolvable -> held
        with (
            lookup_and_rev('[{"number": 7, "state": "OPEN"}]', "", rev_ok=False),
            mock.patch.object(cli_mod, "_verify_merge_evidence", return_value=verified),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)
        self.assertIn("cannot resolve the local branch tip", reason)

        # pass: verification green AND local tip == reviewed head
        with (
            lookup_and_rev('[{"number": 7, "state": "OPEN"}]', SHA + "\n"),
            mock.patch.object(cli_mod, "_verify_merge_evidence", return_value=verified),
        ):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertTrue(ok)
        self.assertEqual(reason, f"PR #7: evidence verified at {'a' * 12}")

    def test_swarm_land_evidence_checker_real_verification_contract(self):
        """PYLON-9's blocker: mocking _verify_merge_evidence in every arm hid a
        namespace that made it raise on every real call, holding every cluster
        forever. This arm runs the REAL verification with only the gh transport
        mocked: the outcome must be an evidence verdict, never an "errored"
        hold — that is the seam contract."""
        import argparse
        import unittest.mock as mock

        from keel import cli as cli_mod
        from keel.runner import CommandResult as RunResult

        args = argparse.Namespace(root=".", path=".keel/project.yaml")
        config = cli_mod.cfg.ProjectConfig(
            extends="keel",
            core_version="^0.7",
            base_branch="main",
            owner="acme",
            repo="widgets",
            knobs=cli_mod.cfg.Knobs(build_gate_cmd="true"),
        )
        check = cli_mod._swarm_land_evidence_checker(args, config)

        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "pr list" in joined:
                out = '[{"number": 7, "state": "OPEN"}]'
            else:
                # every downstream gh fetch sees an empty-but-valid answer
                out = "[]" if "--paginate" in joined or "list" in joined else "{}"
            return RunResult(ok=True, code=0, output=out, stdout=out)

        with mock.patch.object(cli_mod, "run_argv", side_effect=fake_run):
            ok, reason = check("swarm/x/c1")[:2]
        self.assertFalse(ok)  # empty artifacts can never satisfy the contract
        self.assertNotIn(
            "errored",
            reason,
            "the real verification path raised instead of returning a verdict "
            "— the constructed namespace has drifted from keel merge's own "
            f"defaults (reason: {reason})",
        )

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
                code = main(
                    [
                        "swarm-land",
                        ".keel/project.yaml",
                        "--root",
                        tmpdir,
                        "--issues",
                        "#714,bad,714",
                        "--issue",
                        "714",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("keel swarm land — swarm-land-cli", buf_text.getvalue())

            # JSON mode with explicit swarm-id and flags
            buf_json = io.StringIO()
            with redirect_stdout(buf_json):
                code_json = main(
                    [
                        "swarm-land",
                        ".keel/project.yaml",
                        "--root",
                        tmpdir,
                        "--swarm-id",
                        "swarm-land-cli",
                        "--issue-title",
                        "Docs update",
                        "--json",
                    ]
                )
            self.assertEqual(code_json, 0)
            data = json.loads(buf_json.getvalue())
            # the gate is on, so a dry run in a repo with no cluster PRs
            # predicts holds — the preview doing its job, not a failure
            self.assertIn(data["status"], ("success", "partial_failure"))

            # Empty issues branch & state dir without json files
            state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
            for f in state_dir.glob("*.json"):
                f.unlink()

            buf_empty = io.StringIO()
            with redirect_stdout(buf_empty):
                code_empty = main(
                    [
                        "swarm-land",
                        ".keel/project.yaml",
                        "--root",
                        tmpdir,
                    ]
                )
            self.assertEqual(code_empty, 1)

            with tempfile.TemporaryDirectory() as tmp_fresh:
                buf_no_state = io.StringIO()
                with redirect_stdout(buf_no_state):
                    code_no_state = main(
                        [
                            "swarm-land",
                            ".keel/project.yaml",
                            "--root",
                            tmp_fresh,
                        ]
                    )
                self.assertEqual(code_no_state, 1)


if __name__ == "__main__":
    unittest.main()
