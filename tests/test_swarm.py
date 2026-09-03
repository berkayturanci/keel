"""Unit tests for Keel Swarm dependency analysis, tree rendering & status dashboard."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from keel import swarm as swarm_module
from keel import team as team_module
from keel.cli import main
from keel.config import Knobs, ProjectConfig
from keel.swarm import (
    AssignmentOverrides,
    Difficulty,
    IssueScope,
    SwarmCluster,
    SwarmRunState,
    SwarmWorkerStatus,
    _normalize_path,
    build_swarm_plan,
    extract_issue_scope,
    extract_predicted_paths,
    load_swarm_state,
    paths_intersect,
    render_swarm_plan_text,
    render_swarm_plan_tree,
    render_swarm_status_dashboard,
    resolve_cluster_assignment,
    resolve_swarm_state_dir,
    save_swarm_state,
    scopes_have_conflict,
    scopes_intersect,
    score_difficulty,
    ship_handoff_args,
    update_worker_state,
    worker_seed,
)
from keel.team import parse_team


class TestSwarmPathExtraction(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(_normalize_path(""), "")
        self.assertEqual(_normalize_path("./src/keel/swarm.py"), "src/keel/swarm.py")
        self.assertEqual(_normalize_path("/docs/assets/hero.svg,"), "docs/assets/hero.svg")
        self.assertEqual(_normalize_path("`src/keel/cli.py`"), "src/keel/cli.py")
        self.assertEqual(_normalize_path(" 'website/index.html' "), "website/index.html")
        self.assertEqual(_normalize_path("src\\keel\\swarm.py"), "src/keel/swarm.py")

    def test_extract_predicted_paths_backticks_and_text(self):
        text = """
        Let's modify `src/keel/swarm.py` and `tests/test_swarm.py`.
        Also check docs/proposals/keel-swarm.md and website/content.js.
        Ignore words like `...` or `.` or `function_name`.
        """
        paths = extract_predicted_paths(text)
        self.assertIn("src/keel/swarm.py", paths)
        self.assertIn("tests/test_swarm.py", paths)
        self.assertIn("docs/proposals/keel-swarm.md", paths)
        self.assertIn("website/content.js", paths)
        self.assertNotIn("...", paths)


class TestSwarmScopeExtraction(unittest.TestCase):
    def test_extract_issue_scope_with_declared_files(self):
        scope = extract_issue_scope(
            101,
            title="Add swarm plan command",
            body="Implements CLI command in `src/keel/cli.py`.",
            labels=["role:cli", "priority:high"],
            declared_files=["src/keel/swarm.py"],
        )
        self.assertEqual(scope.issue, 101)
        self.assertEqual(scope.role, "cli")
        self.assertIn("src/keel/swarm.py", scope.declared_files)
        self.assertIn("src/keel/cli.py", scope.predicted_files)
        self.assertIn("src/keel/swarm.py", scope.predicted_files)

    def test_extract_issue_scope_fallback_directories(self):
        # Visual fallback
        scope_vis = extract_issue_scope(102, title="Fix 3D orbit scene", labels=["area:visual"])
        self.assertIn("keel-visual/*", scope_vis.predicted_files)
        self.assertEqual(scope_vis.role, "visual")

        # Docs fallback
        scope_docs = extract_issue_scope(103, title="Update documentation", labels=["role:docs"])
        self.assertIn("docs/*", scope_docs.predicted_files)
        self.assertEqual(scope_docs.role, "docs")

        # Website fallback
        scope_web = extract_issue_scope(104, title="Update landing page", labels=["area:website"])
        self.assertIn("website/*", scope_web.predicted_files)

        # CLI fallback
        scope_cli = extract_issue_scope(105, title="Fix CLI flags", labels=["role:cli"])
        self.assertIn("src/keel/cli.py", scope_cli.predicted_files)

        # Generic fallback
        scope_gen = extract_issue_scope(106, title="Do something unknown")
        self.assertIn("scope/issue-106/*", scope_gen.predicted_files)
        self.assertEqual(scope_gen.role, "core")

    def test_extract_issue_scope_with_project_config(self):
        cfg = ProjectConfig(
            extends="base",
            core_version="^1.0",
            base_branch="main",
            knobs=Knobs(
                build_gate_cmd="make test",
                implementer_agents={"backend": "codex", "core": "claude"},
            ),
        )
        scope = extract_issue_scope(107, title="Backend fix", labels=["role:backend"], config=cfg)
        self.assertEqual(scope.role, "backend")

        scope_fallback = extract_issue_scope(
            108, title="Unknown role", labels=["role:nonexistent"], config=cfg
        )
        self.assertEqual(scope_fallback.role, "core")

        # Config without "core" in implementer_agents
        cfg_nocore = ProjectConfig(
            extends="base",
            core_version="^1.0",
            base_branch="main",
            knobs=Knobs(
                build_gate_cmd="make test",
                implementer_agents={"backend": "codex"},
            ),
        )
        scope_nocore = extract_issue_scope(
            109, title="Custom role", labels=["role:custom"], config=cfg_nocore
        )
        self.assertEqual(scope_nocore.role, "custom")

    def test_scopes_have_conflict_agrees_with_scopes_intersect(self):
        # One matcher, one normalizer: the boolean must never disagree with the tuple
        # it short-circuits — including on paths nobody normalized first, and on the
        # empty string, which the matcher rejects on either side.
        cases = [
            (("src/keel/swarm.py",), ("src/keel/swarm.py",)),
            (("src/keel/swarm.py",), ("website/content.js",)),
            (("src/keel",), ("src/keel/cli.py",)),
            (("src/keel/cli.py",), ("src/keel",)),
            (("src/*.py",), ("src/main.py",)),
            (("src/main.py",), ("src/*.py",)),
            (("*",), ("b",)),
            (("a",), ("*",)),
            (("docs/",), ("docs/assets/hero.svg",)),
            (("./src/keel/cli.py`",), ("/src/keel/",)),  # raw, never normalized
            (("", "*"), ("",)),
            (("",), ("",)),  # identical, but "" is never a conflict — not even with itself
            ((), ("src/keel/cli.py",)),
        ]
        for files_a, files_b in cases:
            a = IssueScope(1, predicted_files=files_a)
            b = IssueScope(2, predicted_files=files_b)
            with self.subTest(a=files_a, b=files_b):
                self.assertEqual(scopes_have_conflict(a, b), bool(scopes_intersect(a, b)))

    def test_build_swarm_plan_keeps_duplicate_issue_numbers_apart(self):
        # Two scopes with one issue number are still two scopes. Pre-normalizing into
        # a dict keyed by number compared the second's files for both, which moved
        # issue 7 into issue 5's wave (review of #1006).
        scopes = [
            IssueScope(5, predicted_files=("a.py",)),
            IssueScope(5, predicted_files=("b.py",)),
            IssueScope(7, predicted_files=("a.py",)),
        ]
        plan = build_swarm_plan(scopes, swarm_id="dup")
        self.assertEqual(plan.conflict_map, {5: (7,), 7: (5,)})

    def test_scopes_have_conflict_finds_a_shared_path_without_the_pair_loop(self):
        a = IssueScope(1, predicted_files=("docs/*", "src/keel/cli.py"))
        b = IssueScope(2, predicted_files=("website/app.js", "src/keel/cli.py"))
        self.assertTrue(scopes_have_conflict(a, b))
        self.assertTrue(scopes_have_conflict(a, IssueScope(3, predicted_files=("docs/x.md",))))
        c = IssueScope(4, predicted_files=("website/app.js",))
        self.assertFalse(scopes_have_conflict(a, c))


class TestSwarmPathIntersections(unittest.TestCase):
    def test_paths_intersect(self):
        self.assertFalse(paths_intersect("", "src/keel/swarm.py"))
        self.assertFalse(paths_intersect("src/keel/swarm.py", ""))
        self.assertTrue(paths_intersect("src/keel/swarm.py", "src/keel/swarm.py"))
        self.assertTrue(paths_intersect("*", "src/keel/swarm.py"))
        self.assertTrue(paths_intersect("src/keel/swarm.py", "*"))
        self.assertTrue(paths_intersect("docs/", "docs/assets/hero.svg"))
        self.assertTrue(paths_intersect("docs/assets/hero.svg", "docs/"))
        self.assertTrue(paths_intersect("*.py", "src/keel/swarm.py"))
        self.assertTrue(paths_intersect("src/keel/swarm.py", "src/keel/*.py"))
        self.assertFalse(paths_intersect("src/keel/swarm.py", "website/content.js"))

    def test_scopes_intersect(self):
        scope_a = IssueScope(issue=1, predicted_files=("src/keel/swarm.py", "tests/test_swarm.py"))
        scope_b = IssueScope(issue=2, predicted_files=("src/keel/swarm.py", "src/keel/cli.py"))
        scope_c = IssueScope(issue=3, predicted_files=("website/content.js",))

        self.assertEqual(scopes_intersect(scope_a, scope_b), ("src/keel/swarm.py",))
        self.assertEqual(scopes_intersect(scope_a, scope_c), ())


class TestSwarmPlanClustering(unittest.TestCase):
    def test_empty_swarm_plan(self):
        plan = build_swarm_plan([], swarm_id="swarm-empty-001")
        self.assertEqual(plan.swarm_id, "swarm-empty-001")
        self.assertEqual(plan.total_issues, 0)
        self.assertEqual(len(plan.waves), 0)
        d = plan.to_dict()
        self.assertEqual(d["total_issues"], 0)
        self.assertIn("0 issues", render_swarm_plan_tree(plan))

    def test_single_issue_plan(self):
        scope = IssueScope(issue=101, title="Single Issue", predicted_files=("docs/readme.md",))
        plan = build_swarm_plan([scope], swarm_id="swarm-single")
        self.assertEqual(plan.total_issues, 1)
        self.assertEqual(len(plan.waves), 1)
        self.assertTrue(plan.waves[0].eligible_direct_landing)
        self.assertEqual(plan.waves[0].mode, "orthogonal_parallel")
        self.assertEqual(len(plan.waves[0].clusters), 1)

        tree = render_swarm_plan_tree(plan)
        self.assertIn("Keel Swarm Plan — swarm-single", tree)
        self.assertIn("Wave 1", tree)
        self.assertIn("Direct Batch Landing", tree)

    def test_disjoint_multi_issue_plan(self):
        s1 = IssueScope(issue=1, title="Doc update", predicted_files=("docs/a.md",))
        s2 = IssueScope(issue=2, title="Web update", predicted_files=("website/b.js",))
        s3 = IssueScope(issue=3, title="CLI update", predicted_files=("src/keel/cli.py",))

        plan = build_swarm_plan([s1, s2, s3], swarm_id="swarm-disjoint")
        self.assertEqual(plan.total_issues, 3)
        self.assertEqual(len(plan.waves), 1)
        self.assertTrue(plan.waves[0].eligible_direct_landing)
        self.assertEqual(len(plan.waves[0].clusters), 3)
        self.assertEqual(plan.conflict_map, {1: (), 2: (), 3: ()})

    def test_overlapping_dependent_plan(self):
        s1 = IssueScope(issue=1, title="Core Swarm A", predicted_files=("src/keel/swarm.py",))
        s2 = IssueScope(
            issue=2,
            title="Core Swarm B",
            predicted_files=("src/keel/swarm.py", "src/keel/cli.py"),
        )
        s3 = IssueScope(issue=3, title="Web Update", predicted_files=("website/content.js",))
        s4 = IssueScope(issue=4, title="CLI Update", predicted_files=("src/keel/cli.py",))

        plan = build_swarm_plan([s1, s2, s3, s4], swarm_id="swarm-overlap")
        self.assertEqual(plan.total_issues, 4)
        self.assertEqual(len(plan.waves), 2)

        # Wave 1 should contain non-conflicting issues (e.g. 1 and 3 and 4)
        w1_issues = [c.issues[0] for c in plan.waves[0].clusters]
        self.assertIn(1, w1_issues)
        self.assertIn(3, w1_issues)
        self.assertIn(4, w1_issues)
        self.assertNotIn(2, w1_issues)

        # Wave 2 should contain issue 2 (which depends on 1 and 4)
        w2_issues = [c.issues[0] for c in plan.waves[1].clusters]
        self.assertEqual(w2_issues, [2])
        self.assertEqual(plan.waves[1].clusters[0].depends_on_issues, (1, 4))

        tree = render_swarm_plan_tree(plan)
        self.assertIn("Depends on: #1, #4", tree)

    def test_to_dict_and_render_text(self):
        s1 = IssueScope(
            issue=714,
            title="Proposal",
            predicted_files=("docs/proposals/keel-swarm.md",),
            role="docs",
        )
        s2 = IssueScope(
            issue=715,
            title="Clustering",
            predicted_files=("src/keel/swarm.py",),
            role="core",
        )
        s3 = IssueScope(
            issue=716,
            title="Runtime",
            predicted_files=("src/keel/swarm.py",),
            role="core",
        )

        plan = build_swarm_plan([s1, s2, s3], swarm_id="swarm-demo")
        d = plan.to_dict()
        self.assertEqual(d["swarm_id"], "swarm-demo")
        self.assertEqual(d["total_issues"], 3)
        self.assertIn("714", d["issue_scopes"])

        rendered = render_swarm_plan_text(plan)
        self.assertIn("keel swarm plan — swarm-demo", rendered)
        self.assertIn("Wave 1", rendered)
        self.assertIn("Wave 2", rendered)
        self.assertIn("depends on:", rendered)


class TestSwarmStateAndDashboard(unittest.TestCase):
    def test_render_swarm_status_dashboard_none(self):
        rendered = render_swarm_status_dashboard(None)
        self.assertIn("no active or recent swarm run found", rendered)

    def test_render_swarm_status_dashboard_active(self):
        w1 = SwarmWorkerStatus(
            cluster_id="cluster-1-715",
            issue=715,
            role="core",
            agent="gemini",
            model="gemini-2.5-pro",
            step="s4",
            status="running",
            updated_at="2026-08-15T00:40:00Z",
        )
        w2 = SwarmWorkerStatus(
            cluster_id="cluster-1-714",
            issue=714,
            role="docs",
            agent="claude",
            model="claude-3-7-sonnet",
            step="s10",
            status="merged",
        )
        w3 = SwarmWorkerStatus(
            cluster_id="cluster-2-716",
            issue=716,
            role="core",
            agent="codex",
            model="gpt-5",
            step="s0",
            status="queued",
        )
        state = SwarmRunState(
            swarm_id="swarm-20260815-test",
            total_workers=3,
            active_wave=1,
            workers=(w1, w2, w3),
            started_at="2026-08-15T00:30:00Z",
        )

        d = state.to_dict()
        self.assertEqual(d["swarm_id"], "swarm-20260815-test")
        self.assertEqual(len(d["workers"]), 3)

        rendered = render_swarm_status_dashboard(state)
        self.assertIn("Keel Swarm Live Status — swarm-20260815-test", rendered)
        self.assertIn("cluster-1-715", rendered)
        self.assertIn("[RUNNING ⚙️]", rendered)
        self.assertIn("[MERGED 🚢]", rendered)
        self.assertIn("[QUEUED ⏳]", rendered)

    def test_save_and_load_swarm_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resolve_swarm_state_dir(tmpdir)
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-101",
                issue=101,
                role="core",
                agent="gemini",
                model="pro",
                step="s8",
                status="passed",
            )
            state = SwarmRunState(
                swarm_id="swarm-roundtrip",
                total_workers=1,
                active_wave=1,
                workers=(w1,),
                started_at="2026-08-15T00:00:00Z",
            )
            saved_path = save_swarm_state(state, root=tmpdir)
            self.assertTrue(saved_path.exists())

            loaded = load_swarm_state("swarm-roundtrip", root=tmpdir)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.swarm_id, "swarm-roundtrip")
            self.assertEqual(loaded.workers[0].status, "passed")

            # Nonexistent
            self.assertIsNone(load_swarm_state("nonexistent", root=tmpdir))

            # Corrupt JSON file
            corrupt_file = Path(tmpdir) / ".keel" / "state" / "swarm" / "corrupt.json"
            corrupt_file.write_text("{bad json", encoding="utf-8")
            self.assertIsNone(load_swarm_state("corrupt", root=tmpdir))


class TestSwarmCLI(unittest.TestCase):
    def test_swarm_plan_cli_missing_config(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["swarm-plan", "nonexistent.yaml"])
        self.assertEqual(code, 1)
        self.assertIn("no such config", buf.getvalue())

    def test_swarm_plan_cli_invalid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("invalid_root_key: true\n")
            path = tf.name

        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                code = main(["swarm-plan", path])
            self.assertEqual(code, 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_swarm_plan_cli_success_text_tree_and_json(self):
        # Text mode with comma separated and invalid/duplicate parts
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "swarm-plan",
                    ".keel/project.yaml",
                    "--issues",
                    "#714,invalid,715,715,716",
                    "--swarm-id",
                    "swarm-test-123",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("keel swarm plan — swarm-test-123", buf.getvalue())

        # Tree mode
        buf_tree = io.StringIO()
        with redirect_stdout(buf_tree):
            code = main(
                [
                    "swarm-plan",
                    ".keel/project.yaml",
                    "--issues",
                    "714,715,716",
                    "--swarm-id",
                    "swarm-tree-123",
                    "--tree",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("Keel Swarm Plan — swarm-tree-123", buf_tree.getvalue())

        # JSON mode
        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            code = main(
                [
                    "swarm-plan",
                    ".keel/project.yaml",
                    "--issue",
                    "101",
                    "--issue",
                    "102",
                    "--declared-file",
                    "src/keel/swarm.py",
                    "--issue-label",
                    "role:core,area:swarm",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        parsed = json.loads(buf_json.getvalue())
        self.assertEqual(parsed["total_issues"], 2)
        self.assertIn("waves", parsed)

    def test_swarm_plan_cli_single_issue_from_flags(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "swarm-plan",
                    ".keel/project.yaml",
                    "--issue-title",
                    "Add swarm feature",
                    "--issue-body",
                    "Modify `src/keel/swarm.py`",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["total_issues"], 1)

    def test_swarm_plan_cli_no_issues_empty_plan(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "swarm-plan",
                    ".keel/project.yaml",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["total_issues"], 0)

    def test_swarm_status_cli_missing_and_invalid_config(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["swarm-status", "nonexistent.yaml"])
        self.assertEqual(code, 1)
        self.assertIn("no such config", buf.getvalue())

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("invalid_root_key: true\n")
            path = tf.name

        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                code = main(["swarm-status", path])
            self.assertEqual(code, 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_swarm_status_cli_empty_and_active_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # State directory exists but has no JSON files
            resolve_swarm_state_dir(tmpdir)
            buf_empty_dir = io.StringIO()
            with redirect_stdout(buf_empty_dir):
                code = main(["swarm-status", ".keel/project.yaml", "--root", tmpdir])
            self.assertEqual(code, 0)
            self.assertIn("no active or recent swarm run found", buf_empty_dir.getvalue())

            # Empty root directory without state dir
            tmp_fresh = tempfile.mkdtemp()
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main(["swarm-status", ".keel/project.yaml", "--root", tmp_fresh])
                self.assertEqual(code, 0)
                self.assertIn("no active or recent swarm run found", buf.getvalue())
            finally:
                import shutil

                shutil.rmtree(tmp_fresh, ignore_errors=True)

            # Empty root directory with --json
            buf_json = io.StringIO()
            with redirect_stdout(buf_json):
                code = main(["swarm-status", ".keel/project.yaml", "--root", tmpdir, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buf_json.getvalue()), {})

            # Save an active swarm run
            w1 = SwarmWorkerStatus(
                cluster_id="cluster-1-715",
                issue=715,
                role="core",
                status="running",
            )
            state = SwarmRunState(
                swarm_id="swarm-live-999",
                total_workers=1,
                workers=(w1,),
                started_at="2026-08-15T00:00:00Z",
            )
            save_swarm_state(state, root=tmpdir)

            # Auto-discovery
            buf_auto = io.StringIO()
            with redirect_stdout(buf_auto):
                code = main(["swarm-status", ".keel/project.yaml", "--root", tmpdir])
            self.assertEqual(code, 0)
            self.assertIn("swarm-live-999", buf_auto.getvalue())

            # Explicit ID with --json
            buf_id_json = io.StringIO()
            with redirect_stdout(buf_id_json):
                code = main(
                    [
                        "swarm-status",
                        ".keel/project.yaml",
                        "--root",
                        tmpdir,
                        "--swarm-id",
                        "swarm-live-999",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            parsed = json.loads(buf_id_json.getvalue())
            self.assertEqual(parsed["swarm_id"], "swarm-live-999")


def _config(team=None, tier3_globs=("src/keel/**",)) -> ProjectConfig:
    """A config whose only interesting parts are the tier globs and the team policy."""
    return ProjectConfig(
        extends="base",
        core_version="^1.0",
        base_branch="main",
        knobs=Knobs(
            build_gate_cmd="make test",
            tier3_globs=tuple(tier3_globs),
            docs_gate_paths=("docs/**",),
            team=parse_team(team),
        ),
    )


#: A backlog whose two issues touch disjoint trees, so the waves are fixed and any
#: change in staffing has to be visible without moving them.
BACKLOG = (
    IssueScope(
        issue=101,
        title="Rework the scheduler",
        labels=("priority:high", "role:core", "size:l"),
        predicted_files=("src/keel/a.py", "src/keel/b.py", "src/keel/c.py", "src/keel/d.py"),
        role="core",
    ),
    IssueScope(
        issue=102,
        title="Fix a typo",
        labels=("role:docs",),
        predicted_files=("docs/keel/cli.md",),
        role="docs",
    ),
)

BY_DIFFICULTY = {
    "implement": {"default": {"provider": "claude"}},
    "by_difficulty": {
        "easy": {"implement": {"provider": "ollama", "model": "qwen"}},
        "hard": {
            "lead": {"provider": "codex"},
            "implement": {"provider": "codex"},
            "effort": "high",
        },
    },
}


class TestDifficultyScoring(unittest.TestCase):
    """Difficulty is *how much work*, scored from inputs that already exist (#1017)."""

    def test_a_docs_one_liner_is_easy(self):
        difficulty = score_difficulty(
            (BACKLOG[1],), tier3_globs=("src/keel/**",), docs_globs=("docs/**",)
        )

        self.assertEqual(difficulty.band, "easy")
        self.assertEqual(difficulty.score, 0)
        self.assertEqual(difficulty.tier, 1)
        self.assertEqual(difficulty.signals, ())

    def test_a_wide_high_priority_core_change_is_hard(self):
        difficulty = score_difficulty(
            (BACKLOG[0],), tier3_globs=("src/keel/**",), docs_globs=("docs/**",)
        )

        self.assertEqual(difficulty.band, "hard")
        self.assertEqual(difficulty.tier, 3)
        self.assertEqual(difficulty.file_count, 4)
        self.assertEqual(
            difficulty.signals,
            (("tier-3", 4), ("files:4", 2), ("priority:high", 1), ("size:l", 2)),
        )
        self.assertEqual(difficulty.score, 9)

    def test_only_signals_that_moved_the_score_are_recorded(self):
        """A signal worth zero points is not evidence; recording it is recording noise."""
        difficulty = score_difficulty((IssueScope(issue=1, predicted_files=("a.py", "b.py")),))

        self.assertEqual(difficulty.signals, (("tier-2", 2), ("files:2", 1)))
        self.assertEqual(difficulty.band, "standard")

    def test_dependency_depth_counts_but_is_capped(self):
        """Past a few dependencies it is the same problem, not a worse one."""
        scope = IssueScope(issue=1, predicted_files=("a.py",))

        capped = score_difficulty((scope,), dependency_depth=9)
        negative = score_difficulty((scope,), dependency_depth=-3)

        self.assertEqual(capped.dependency_depth, 3)
        self.assertEqual(capped.signals, (("tier-2", 2), ("depends-on:3", 3)))
        self.assertEqual(negative.dependency_depth, 0)

    def test_a_cluster_is_scored_as_one_piece_of_work(self):
        """Three small issues handed to one lead are not three small pieces of work."""
        together = score_difficulty(BACKLOG, tier3_globs=("src/keel/**",))
        alone = score_difficulty((BACKLOG[1],), tier3_globs=("src/keel/**",))

        self.assertEqual(together.file_count, 5)
        self.assertGreater(together.score, alone.score)

    def test_labels_are_read_case_insensitively(self):
        scope = IssueScope(issue=1, labels=("Priority:Critical",), predicted_files=("a.py",))

        self.assertIn(("priority:critical", 2), score_difficulty((scope,)).signals)

    def test_the_band_boundaries_are_the_documented_ones(self):
        bands = [swarm_module.difficulty_band(score) for score in range(0, 8)]

        self.assertEqual(
            bands,
            ["easy", "easy", "easy", "standard", "standard", "standard", "hard", "hard"],
        )


class TestClusterAssignment(unittest.TestCase):
    """Per-cluster staffing, resolved by the same resolver every other command uses."""

    def plan(self, **kwargs):
        return build_swarm_plan(BACKLOG, swarm_id="swarm-test", **kwargs)

    def clusters(self, plan):
        return {c.cluster_id: c for wave in plan.waves for c in wave.clusters}

    def test_every_cluster_comes_out_scored_and_staffed(self):
        clusters = self.clusters(self.plan(config=_config()))

        for cluster in clusters.values():
            self.assertIsNotNone(cluster.difficulty)
            self.assertIsNotNone(cluster.assignment)
            self.assertEqual(cluster.assignment["difficulty"], cluster.difficulty.band)
            self.assertEqual(cluster.assignment["role"], cluster.role)
            self.assertEqual(cluster.assignment["tier"], cluster.difficulty.tier)

    def test_the_plan_is_deterministic_for_the_same_backlog(self):
        self.assertEqual(
            self.plan(config=_config()).to_dict(), self.plan(config=_config()).to_dict()
        )

    def test_changing_by_difficulty_changes_assignments_and_not_waves(self):
        """The acceptance criterion, asserted as one comparison.

        Scoring and staffing run after the partition and never feed back into it, so
        re-staffing a backlog can only move who runs a cluster.
        """
        plain = self.plan(config=_config())
        benched = self.plan(config=_config(team=BY_DIFFICULTY))

        waves = lambda plan: [  # noqa: E731
            [(c.cluster_id, c.issues, c.combined_scope) for c in wave.clusters]
            for wave in plan.waves
        ]
        self.assertEqual(waves(plain), waves(benched))
        self.assertNotEqual(
            [c.assignment["implementer"] for c in self.clusters(plain).values()],
            [c.assignment["implementer"] for c in self.clusters(benched).values()],
        )

    def test_the_hard_cluster_draws_the_strong_implementer_and_the_easy_one_the_cheap_model(self):
        clusters = self.clusters(self.plan(config=_config(team=BY_DIFFICULTY)))
        hard = clusters["cluster-1-101"]
        easy = clusters["cluster-1-102"]

        self.assertEqual(hard.difficulty.band, "hard")
        self.assertEqual(hard.assignment["implementer"]["provider"], "codex")
        self.assertEqual(hard.assignment["effort"], "high")
        self.assertEqual(hard.assignment["lead"]["provider"], "codex")
        self.assertEqual(easy.difficulty.band, "easy")
        self.assertEqual(easy.assignment["implementer"]["model"], "qwen")

    def test_per_run_overrides_reach_every_cluster(self):
        plan = self.plan(
            config=_config(team=BY_DIFFICULTY),
            overrides=AssignmentOverrides(
                delegate="agy:gemini-3.8-pro",
                review_delegates=("codex",),
                effort="low",
                team_profile="weekend",
                reviewers=1,
                host_agent="agy",
            ),
        )

        for cluster in self.clusters(plan).values():
            self.assertEqual(cluster.assignment["implementer"]["provider"], "agy")
            self.assertEqual(cluster.assignment["effort"], "low")
            self.assertEqual(cluster.assignment["reviewer_count"], 1)
            self.assertEqual(cluster.assignment["reviewers"][0]["provider"], "codex")
            self.assertTrue(any("--team" in w for w in cluster.assignment["warnings"]))
        # The lead is staffing, not an override: the hard band names one, the easy band
        # does not and falls through to the host the operator is running as.
        clusters = self.clusters(plan)
        self.assertEqual(clusters["cluster-1-101"].assignment["lead"]["provider"], "codex")
        self.assertEqual(clusters["cluster-1-102"].assignment["lead"]["provider"], "agy")

    def test_a_plan_built_without_a_config_still_scores_and_staffs(self):
        """`swarm-plan` has to answer even where no project policy was loaded."""
        cluster = self.clusters(self.plan())["cluster-1-101"]

        self.assertEqual(cluster.difficulty.tier, 2)
        self.assertFalse(cluster.assignment["configured"])
        self.assertEqual(cluster.assignment["lead"]["provider"], "claude")

    def test_resolve_cluster_assignment_defaults_to_an_unconfigured_policy(self):
        cluster = SwarmCluster("c1", (1,), "core", ("a.py",))
        difficulty = Difficulty(score=0, band="easy", tier=1, file_count=1, dependency_depth=0)

        assignment = resolve_cluster_assignment(cluster, difficulty)

        self.assertEqual(assignment["tier"], 1)
        self.assertEqual(assignment["reviewer_count"], 1)

    def test_overrides_serialise_for_the_published_contract(self):
        self.assertEqual(
            AssignmentOverrides(delegate="codex", review_delegates=("agy",)).to_dict(),
            {
                "delegate": "codex",
                "review_delegates": ["agy"],
                "effort": None,
                "team": None,
                "reviewers": None,
                "host_agent": "claude",
            },
        )


class TestShipHandoff(unittest.TestCase):
    """What a lead appends to its cluster's child ship runs."""

    def assignment(self, **kwargs):
        clusters = [
            c
            for wave in build_swarm_plan(BACKLOG, swarm_id="s", config=_config(**kwargs)).waves
            for c in wave.clusters
        ]
        return {c.cluster_id: c for c in clusters}

    def test_the_resolved_team_becomes_child_ship_flags(self):
        cluster = self.assignment(team=BY_DIFFICULTY)["cluster-1-101"]

        args = ship_handoff_args(cluster.assignment)

        self.assertIn("--delegate", args)
        self.assertEqual(args[args.index("--delegate") + 1], "codex")
        self.assertEqual(args[-2:], ("--role", "core"))

    def test_a_model_rides_along_on_the_delegate_token(self):
        cluster = self.assignment(team=BY_DIFFICULTY)["cluster-1-102"]

        self.assertIn("ollama:qwen", ship_handoff_args(cluster.assignment))

    def test_a_host_subagent_seat_is_left_to_the_adapter(self):
        """`keel ship --delegate` names a provider; spawning a subagent is the host's job."""
        team = {"implement": {"default": {"provider": "subagent:backend-developer"}}}
        cluster = self.assignment(team=team)["cluster-1-101"]

        self.assertNotIn("--delegate", ship_handoff_args(cluster.assignment))
        self.assertIn("--review-delegate", ship_handoff_args(cluster.assignment))

    def test_a_subagent_reviewer_slot_is_skipped_like_a_subagent_implementer(self):
        team = {
            "implement": {"default": {"provider": "codex"}},
            "review": {"default": [{"provider": "subagent:reviewer-a"}]},
        }
        cluster = self.assignment(team=team)["cluster-1-101"]

        self.assertNotIn("--review-delegate", ship_handoff_args(cluster.assignment))
        self.assertIn("--delegate", ship_handoff_args(cluster.assignment))

    def test_an_assignment_with_no_role_carries_no_role_flag(self):
        assignment = team_module.resolve_assignment(team_module.TeamPolicy(), tier=2)

        self.assertNotIn("--role", ship_handoff_args(assignment))

    def test_no_assignment_is_no_flags(self):
        self.assertEqual(ship_handoff_args(None), ())


class TestWorkerSeeding(unittest.TestCase):
    """The board reports the team the planner resolved, not the record's defaults."""

    def cluster(self, **kwargs):
        plan = build_swarm_plan(BACKLOG, swarm_id="s", config=_config(**kwargs))
        return plan.waves[0].clusters[0]

    def test_a_worker_is_seeded_from_its_cluster_assignment(self):
        worker = worker_seed(self.cluster(team=BY_DIFFICULTY), updated_at="2026-09-04T00:00:00Z")

        self.assertEqual(worker.agent, "codex")
        self.assertEqual(worker.model, "default")
        self.assertEqual(worker.lead, "codex")
        self.assertEqual(worker.difficulty, "hard")
        self.assertEqual(worker.step, "s0")

    def test_a_seat_with_a_model_reports_it(self):
        plan = build_swarm_plan(BACKLOG, swarm_id="s", config=_config(team=BY_DIFFICULTY))
        worker = worker_seed(plan.waves[0].clusters[1])

        self.assertEqual((worker.agent, worker.model), ("ollama", "qwen"))

    def test_an_unstaffed_cluster_falls_back_to_the_record_defaults(self):
        worker = worker_seed(SwarmCluster("c1", (7,), "core", ("a.py",)))

        self.assertEqual(
            (worker.agent, worker.model, worker.lead, worker.difficulty),
            ("claude", "default", "", ""),
        )

    def test_a_cluster_with_no_issues_seeds_issue_zero(self):
        self.assertEqual(worker_seed(SwarmCluster("c1", (), "core", ())).issue, 0)

    def test_a_status_update_keeps_the_lead_and_band(self):
        """The rebuild used to reset every field it did not name."""
        state = SwarmRunState(
            swarm_id="s",
            total_workers=1,
            workers=(worker_seed(self.cluster(team=BY_DIFFICULTY)),),
        )

        updated = update_worker_state(state, "cluster-1-101", step="s4", status="running")

        self.assertEqual(updated.workers[0].lead, "codex")
        self.assertEqual(updated.workers[0].difficulty, "hard")
        self.assertEqual(updated.workers[0].status, "running")

    def test_a_saved_state_round_trips_the_new_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = SwarmRunState(
                swarm_id="swarm-rt",
                total_workers=1,
                workers=(worker_seed(self.cluster(team=BY_DIFFICULTY)),),
            )
            save_swarm_state(state, root=tmpdir)

            loaded = load_swarm_state("swarm-rt", root=tmpdir)

        self.assertEqual(loaded.workers[0].lead, "codex")
        self.assertEqual(loaded.workers[0].difficulty, "hard")


class TestStaffedRendering(unittest.TestCase):
    """Both renderers describe one cluster the same way."""

    def plan(self):
        return build_swarm_plan(
            BACKLOG, swarm_id="swarm-render", config=_config(team=BY_DIFFICULTY)
        )

    def test_the_table_shows_the_band_and_the_team(self):
        rendered = render_swarm_plan_text(self.plan())

        self.assertIn("Difficulty: hard (score 9, tier 3, 4 file(s), depth 0)", rendered)
        self.assertIn("Team: lead codex → implementer codex@high", rendered)

    def test_the_tree_shows_the_same_rows_and_closes_its_branches(self):
        rendered = render_swarm_plan_tree(self.plan())

        self.assertIn("Difficulty: hard", rendered)
        # The team row is the last child of every cluster here, so it is the one that
        # closes the branch — which is the property the assembled-children rewrite buys.
        self.assertEqual(rendered.count("└── Team: lead "), 2)
        self.assertEqual(rendered.count("├── Difficulty: "), 2)

    def test_an_unstaffed_cluster_renders_without_the_rows(self):
        plan = build_swarm_plan(())
        cluster = SwarmCluster("c1", (1,), "core", ("a.py",))

        self.assertEqual(swarm_module.cluster_staffing_lines(cluster), [])
        self.assertIn("0 issues", render_swarm_plan_tree(plan))

    def test_a_jury_panel_renders_as_the_panel_rather_than_an_empty_bench(self):
        config = _config(team={"review": {"by_tier": {"3": "jury"}}})
        plan = build_swarm_plan(BACKLOG, swarm_id="s", config=config)

        self.assertIn("review jury", render_swarm_plan_text(plan))

    def test_seat_label_reports_an_unassigned_seat(self):
        self.assertEqual(swarm_module.seat_label(None), "unassigned")


if __name__ == "__main__":
    unittest.main()
