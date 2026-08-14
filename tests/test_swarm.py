"""Unit tests for Keel Swarm dependency analysis & conflict clustering."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from keel.cli import main
from keel.config import Knobs, ProjectConfig
from keel.swarm import (
    IssueScope,
    _normalize_path,
    build_swarm_plan,
    extract_issue_scope,
    extract_predicted_paths,
    paths_intersect,
    render_swarm_plan_text,
    scopes_intersect,
)


class TestSwarmPathExtraction(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(_normalize_path(""), "")
        self.assertEqual(_normalize_path("./src/keel/swarm.py"), "src/keel/swarm.py")
        self.assertEqual(_normalize_path("/docs/assets/hero.svg,"), "docs/assets/hero.svg")
        self.assertEqual(_normalize_path("`src/keel/cli.py`"), "src/keel/cli.py")
        self.assertEqual(_normalize_path(" 'website/index.html' "), "website/index.html")

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

    def test_single_issue_plan(self):
        scope = IssueScope(issue=101, title="Single Issue", predicted_files=("docs/readme.md",))
        plan = build_swarm_plan([scope], swarm_id="swarm-single")
        self.assertEqual(plan.total_issues, 1)
        self.assertEqual(len(plan.waves), 1)
        self.assertTrue(plan.waves[0].eligible_direct_landing)
        self.assertEqual(plan.waves[0].mode, "orthogonal_parallel")
        self.assertEqual(len(plan.waves[0].clusters), 1)

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

    def test_swarm_plan_cli_success_text_and_json(self):
        # Text mode with comma separated and invalid/duplicate parts
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "swarm-plan",
                ".keel/project.yaml",
                "--issues", "#714,invalid,715,715,716",
                "--swarm-id", "swarm-test-123",
            ])
        self.assertEqual(code, 0)
        self.assertIn("keel swarm plan — swarm-test-123", buf.getvalue())

        # JSON mode
        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            code = main([
                "swarm-plan",
                ".keel/project.yaml",
                "--issue", "101",
                "--issue", "102",
                "--declared-file", "src/keel/swarm.py",
                "--issue-label", "role:core,area:swarm",
                "--json",
            ])
        self.assertEqual(code, 0)
        parsed = json.loads(buf_json.getvalue())
        self.assertEqual(parsed["total_issues"], 2)
        self.assertIn("waves", parsed)

    def test_swarm_plan_cli_single_issue_from_flags(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "swarm-plan",
                ".keel/project.yaml",
                "--issue-title", "Add swarm feature",
                "--issue-body", "Modify `src/keel/swarm.py`",
                "--json",
            ])
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["total_issues"], 1)

    def test_swarm_plan_cli_no_issues_empty_plan(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "swarm-plan",
                ".keel/project.yaml",
                "--json",
            ])
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["total_issues"], 0)


if __name__ == "__main__":
    unittest.main()
