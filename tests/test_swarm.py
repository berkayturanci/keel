"""Unit tests for Keel Swarm dependency analysis, tree rendering & status dashboard."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from keel import swarm as swarm_module
from keel import team as team_module
from keel.cli import main
from keel.config import Knobs, ProjectConfig
from keel.runner import CommandResult
from keel.swarm import (
    CHILD_OUTPUT_TAIL_CHARS,
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
    safe_role,
    save_swarm_state,
    scopes_have_conflict,
    scopes_intersect,
    score_difficulty,
    ship_handoff_args,
    tail_child_output,
    update_worker_state,
    worker_seed,
)
from keel.team import parse_team

#: swarm-plan/-run/-land read every named issue with `gh issue view` (#1274). The suite
#: is offline (AGENTS.md), so every test gets an unreadable issue unless it patches its
#: own, as the issue-scope tests below do.
_ISSUE_STUB = patch(
    "keel.github.issue_facts",
    return_value=CommandResult(False, 1, "stubbed: the suite never runs gh"),
)


def setUpModule():
    _ISSUE_STUB.start()


def tearDownModule():
    _ISSUE_STUB.stop()


class TestSwarmPathExtraction(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(_normalize_path(""), "")
        self.assertEqual(_normalize_path("./src/keel/swarm.py"), "src/keel/swarm.py")
        self.assertEqual(_normalize_path("/docs/assets/hero.svg,"), "docs/assets/hero.svg")
        self.assertEqual(_normalize_path("`src/keel/cli.py`"), "src/keel/cli.py")
        self.assertEqual(_normalize_path(" 'website/index.html' "), "website/index.html")
        self.assertEqual(_normalize_path("src\\keel\\swarm.py"), "src/keel/swarm.py")

    def test_normalizing_twice_changes_nothing(self):
        """#1279: the plan normalises twice, and a second pass ate the `)` the first
        exposed. Every spelling here must be a fixed point after one pass."""
        import itertools

        cores = [
            "docs/(draft)/",
            "(x)/y",
            "src/a.py",
            ".github/workflows/ci.yml",
            "a/(b",
            "((a/b))",
        ]
        wraps = [("", ""), ("(", ")"), ("`", "`"), ("'", "'."), ("", ","), ("", ");"), (" ", " ")]
        for core, (head, tail) in itertools.product(cores, wraps):
            once = _normalize_path(head + core + tail)
            with self.subTest(raw=head + core + tail):
                self.assertEqual(_normalize_path(once), once)

    def test_brackets_that_belong_to_the_path_are_kept(self):
        self.assertEqual(_normalize_path("docs/(draft)/"), "docs/(draft)")
        self.assertEqual(_normalize_path("(x)/y"), "(x)/y")
        self.assertEqual(_normalize_path("(docs/draft)/"), "(docs/draft)")
        self.assertEqual(_normalize_path("(src/a.py"), "src/a.py")
        self.assertEqual(_normalize_path("src/a.py);"), "src/a.py")

    def test_a_dot_directory_keeps_its_dot(self):
        """A leading dot was stripped as punctuation: `.github/…` became `github/…`."""
        self.assertEqual(_normalize_path(".github/workflows/ci.yml"), ".github/workflows/ci.yml")
        self.assertEqual(_normalize_path("`.keel/project.yaml`."), ".keel/project.yaml")
        self.assertEqual(
            extract_predicted_paths("Edit .github/workflows/ci.yml."), [".github/workflows/ci.yml"]
        )

    def test_a_bracketed_directory_overlaps_the_files_inside_it(self):
        """The scenario: stored as `docs/(draft)` but matched as `docs/(draft`, so a
        second issue under that directory was declared orthogonal to the first — and,
        in the review of the first fix, the same through `(docs/draft)/`."""
        for directory, inside in (
            ("docs/(draft)/", "docs/(draft)/x.md"),
            ("(docs/draft)/", "(docs/draft)/y.md"),
            ("(docs\\draft)\\", "(docs\\draft)\\y.md"),
        ):
            with self.subTest(directory):
                a = extract_issue_scope(1, title="", body="", labels=(), declared_files=[directory])
                b = extract_issue_scope(2, title="", body="", labels=(), declared_files=[inside])
                plan = build_swarm_plan([a, b], swarm_id="swarm-draft")
                self.assertEqual(len(plan.waves), 2, "they share a directory and must serialise")

    def test_normalizing_is_a_fixed_point_on_generated_spellings(self):
        """#1311 review: a 200k fuzz found normpath exposing new end punctuation after
        the strip (`(docs/draft)/` → `(docs/draft)` → `docs/draft`). Seeded, so a
        failure names a reproducible spelling."""
        import random

        rng = random.Random(7)
        alphabet = "()./\\ `'a,;:"
        for _ in range(20000):
            raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9)))
            once = _normalize_path(raw)
            self.assertEqual(_normalize_path(once), once, f"not a fixed point: {raw!r}")

    def test_a_long_run_of_exposed_punctuation_still_ends_canonical(self):
        """Each round peels one exposed end; a fixed round cap returned a non-canonical
        value for 64+ layers (#1311 review)."""
        raw = "a" + ",/" * 200
        once = _normalize_path(raw)
        self.assertEqual(once, "a")
        self.assertEqual(_normalize_path(once), once)

    def test_a_final_dot_segment_is_a_path_step(self):
        self.assertEqual(_normalize_path("src/a/.."), "src")
        self.assertEqual(_normalize_path("src/a/."), "src/a")
        self.assertEqual(_normalize_path("src\\a\\.."), "src")
        self.assertEqual(_normalize_path("..."), "")

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

        # Nothing describes it: everything (#1274), not a private `scope/issue-106/*`
        scope_gen = extract_issue_scope(106, title="Do something unknown")
        self.assertEqual(scope_gen.predicted_files, ("*",))
        self.assertEqual(scope_gen.scope_source, "default")
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

    def test_a_held_worker_has_its_own_badge(self):
        """#1280: `held` — landing kept the cluster back for missing review evidence —
        is in the status vocabulary but had no badge, so it fell through to the
        generic upper-cased `[HELD]` the board draws for a status it does not know."""
        held = SwarmWorkerStatus(
            cluster_id="cluster-1-102", issue=102, role="core", step="s10", status="held"
        )
        state = SwarmRunState(swarm_id="swarm-held", total_workers=1, workers=(held,))
        rendered = render_swarm_status_dashboard(state)
        self.assertIn("[HELD ⏸️]", rendered)
        self.assertNotIn("[HELD]", rendered)

    def test_a_status_the_board_does_not_know_still_renders(self):
        """The counterweight: the generic fallback stays for anything unnamed."""
        odd = SwarmWorkerStatus(cluster_id="c", issue=1, role="core", status="paused")
        state = SwarmRunState(swarm_id="swarm-odd", total_workers=1, workers=(odd,))
        self.assertIn("[PAUSED]", render_swarm_status_dashboard(state))

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

    def test_state_that_parses_but_has_the_wrong_shape_is_unreadable_not_fatal(self):
        """#1273: only unparseable JSON was handled; JSON of the wrong shape raised
        out of swarm-status, the recovery tool."""
        shapes = {
            "a worker that is not an object": '{"workers": ["not-a-dict"]}',
            "null workers": '{"workers": null}',
            "not an object": "[1, 2, 3]",
            "a worker field of the wrong type": '{"workers": [{"issue": [1]}]}',
            "a null count": '{"total_workers": null, "workers": []}',
            "an infinite count": '{"total_workers": 1e999, "workers": []}',
            "an infinite issue": '{"workers": [{"issue": 1e999}]}',
        }
        for label, text in shapes.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmpdir:
                state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
                state_dir.mkdir(parents=True)
                (state_dir / "odd.json").write_text(text, encoding="utf-8")
                out, err = io.StringIO(), io.StringIO()
                try:
                    loaded = load_swarm_state("odd", root=tmpdir)
                    with redirect_stdout(out), redirect_stderr(err):
                        code = main(["swarm-status", ".keel/project.yaml", "--root", tmpdir])
                except Exception as exc:  # noqa: BLE001 - the defect is that it raises
                    self.fail(f"{type(exc).__name__} escaped: {exc}")
                self.assertIsNone(loaded)
                # Unreadable is not "nothing in flight": it fails the gate (#1280) and
                # does not print the empty board that says no run exists.
                self.assertEqual(code, 1)
                self.assertIn("odd.json is not the shape keel writes", err.getvalue())
                self.assertNotIn("no active or recent swarm run found", out.getvalue())

    def test_a_well_formed_state_draws_no_warning(self):
        """The counterweight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = SwarmRunState(swarm_id="fine", total_workers=0, active_wave=1, workers=())
            save_swarm_state(state, root=tmpdir)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                main(["swarm-status", ".keel/project.yaml", "--root", tmpdir])
            self.assertEqual(err.getvalue(), "")


class TheChildOutputIsBounded(unittest.TestCase):
    """#1280: a swarm stored each child's whole `keel ship --json` output."""

    def test_output_within_the_cap_is_kept_verbatim(self):
        self.assertEqual(tail_child_output(""), "")
        exact = "x" * CHILD_OUTPUT_TAIL_CHARS
        self.assertEqual(tail_child_output(exact), exact)

    def test_longer_output_keeps_the_tail_and_says_how_much_was_dropped(self):
        # The head and the tail differ, so a helper that kept the head fails here.
        text = "H" * 1000 + "x" * (CHILD_OUTPUT_TAIL_CHARS - 5) + "ERROR"
        capped = tail_child_output(text)
        marker, _, kept = capped.partition("\n")
        self.assertEqual(kept, text[-CHILD_OUTPUT_TAIL_CHARS:])
        self.assertTrue(kept.endswith("ERROR"))
        self.assertNotIn("H", kept)
        self.assertEqual(
            marker,
            f"[keel: 1000 earlier chars of child output dropped; last "
            f"{CHILD_OUTPUT_TAIL_CHARS} kept]",
        )


class SwarmStatusIsAGate(unittest.TestCase):
    """#1280: `swarm-status` exited 0 for every outcome and `--json` printed `{}` both for
    "no run" and for "the run exists but cannot be read", so it could not gate anything."""

    def _run(self, root: str, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["swarm-status", ".keel/project.yaml", "--root", root, *extra])
        return code, out.getvalue(), err.getvalue()

    def _unreadable(self, root: str, name: str = "torn") -> None:
        state_dir = Path(root) / ".keel" / "state" / "swarm"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"{name}.json").write_text("{bad json", encoding="utf-8")

    def test_nothing_in_flight_is_an_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(self._run(tmpdir, "--json")[:2], (0, "{}\n"))

    def test_an_unreadable_newest_state_fails_and_json_does_not_say_no_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._unreadable(tmpdir)
            code, out, err = self._run(tmpdir, "--json")
            self.assertEqual(code, 1)
            self.assertIn("torn.json is not the shape keel writes", err)
            payload = json.loads(out)
            self.assertNotEqual(payload, {})
            self.assertEqual(
                (payload.get("error_code"), payload.get("swarm_id")), ("unreadable-state", "torn")
            )
            self.assertIn("torn.json", payload.get("error", ""))

    def test_a_state_file_that_cannot_be_opened_is_unreadable_not_a_crash(self):
        # A directory named `<id>.json` exists but cannot be read — the portable stand-in
        # for a file with no read permission (chmod does not bind root or Windows). The
        # docs promise the unreadable-state object, not a traceback.
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".keel" / "state" / "swarm" / "locked.json").mkdir(parents=True)
            try:
                loaded = load_swarm_state("locked", root=tmpdir)
                code, out, err = self._run(tmpdir, "--json", "--swarm-id", "locked")
            except OSError as exc:  # the defect is that it escapes
                raise AssertionError(f"{type(exc).__name__} escaped: {exc}") from exc
            self.assertIsNone(loaded)
            self.assertEqual(code, 1)
            self.assertIn("locked.json", err)
            self.assertEqual(json.loads(out).get("error_code"), "unreadable-state")

    def test_an_explicit_unreadable_state_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._unreadable(tmpdir)
            code, out, _ = self._run(tmpdir, "--swarm-id", "torn")
            self.assertEqual(code, 1)
            self.assertEqual(out, "")

    def test_an_explicit_swarm_id_with_no_state_fails_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Another, readable run exists: asking for a specific one that does not
            # must not fall back to "nothing in flight" or to the other run.
            save_swarm_state(
                SwarmRunState(swarm_id="other", total_workers=0, workers=()), root=tmpdir
            )
            code, out, err = self._run(tmpdir, "--swarm-id", "swarm-gone")
            self.assertEqual(code, 1)
            self.assertIn("swarm-gone", err)
            self.assertEqual(out, "")

            code, out, err = self._run(tmpdir, "--swarm-id", "swarm-gone", "--json")
            self.assertEqual(code, 1)
            payload = json.loads(out)
            self.assertEqual(
                (payload.get("error_code"), payload.get("swarm_id")),
                ("unknown-swarm", "swarm-gone"),
            )
            self.assertIn("swarm-gone", err)

    def test_the_help_states_the_exit_codes(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["swarm-status", "--help"])
        self.assertIn("exit codes", out.getvalue())
        self.assertIn("--swarm-id names no run", out.getvalue())


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
                    "--issue-scope",
                    "101=src/keel/swarm.py",
                    "--issue-scope",
                    "102=docs/keel/swarm.md",
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

    def test_dependency_depth_is_recorded_raw_and_only_its_points_are_capped(self):
        """Past a few dependencies it is the same problem, not a worse one — but a
        cluster sitting on nine earlier issues is still not one sitting on three, and
        clamping the recorded depth threw that away where a reader would have seen it."""
        scope = IssueScope(issue=1, predicted_files=("a.py",))

        deep = score_difficulty((scope,), dependency_depth=9)
        at_cap = score_difficulty((scope,), dependency_depth=3)
        negative = score_difficulty((scope,), dependency_depth=-3)

        self.assertEqual(deep.dependency_depth, 9)
        self.assertEqual(deep.signals, (("tier-2", 2), ("depends-on:9", 3)))
        self.assertEqual(deep.score, at_cap.score)
        self.assertEqual(negative.dependency_depth, 0)

    def test_the_planner_scores_a_cluster_from_the_cluster_own_issue_list(self):
        """The scorer's contract is "how much work is *this cluster*", and the planner
        reads the cluster's `issues` rather than the issue its loop happens to be on."""
        cluster = SwarmCluster("c1", (102, 101), "core", ())
        scopes = {scope.issue: scope for scope in BACKLOG}

        self.assertEqual(
            [scope.issue for scope in swarm_module.cluster_scopes(cluster, scopes)], [102, 101]
        )

    def test_an_issue_the_planner_never_analysed_is_skipped_not_faked(self):
        """An invented empty scope would quietly lower the band of a cluster whose
        largest issue went missing."""
        cluster = SwarmCluster("c1", (101, 999), "core", ())
        scopes = {scope.issue: scope for scope in BACKLOG}

        self.assertEqual(
            [scope.issue for scope in swarm_module.cluster_scopes(cluster, scopes)], [101]
        )

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
        self.assertEqual(args[args.index("--role") + 1], "core")
        # The bench rides along so the child reproduces the parent's resolution rather
        # than deriving a different team from config alone.
        self.assertEqual(args[args.index("--effort") + 1], "high")

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

    def test_a_role_that_would_parse_as_a_flag_is_dropped_and_reported(self):
        """`role:--live` on one issue would break argparse in every child of the batch.

        There is no quoting that stops `--live` being a flag, so it is dropped — and said
        out loud in `assignment.warnings`, which is where a lead is told to look.
        """
        cluster = SwarmCluster("c1", (1,), "--live", ("a.py",))
        difficulty = Difficulty(score=0, band="easy", tier=1, file_count=1, dependency_depth=0)

        assignment = resolve_cluster_assignment(cluster, difficulty)

        self.assertNotIn("--role", ship_handoff_args(assignment))
        self.assertIn("is not passed to the child ship", assignment["warnings"][-1])

    def test_the_safe_role_class_admits_ordinary_labels_and_refuses_argv_bait(self):
        for role in ("core", "docs", "keel-visual", "api.v2", "role_1"):
            with self.subTest(role=role):
                self.assertEqual(safe_role(role), (role, ()))
        for role in ("--live", "-x", "a b", "core;rm", "", None):
            with self.subTest(role=role):
                self.assertIsNone(safe_role(role)[0])

    def test_the_warning_is_deterministic(self):
        self.assertEqual(safe_role("--live")[1], safe_role("--live")[1])

    def test_a_clean_role_adds_no_warning(self):
        cluster = SwarmCluster("c1", (1,), "core", ("a.py",))
        difficulty = Difficulty(score=0, band="easy", tier=1, file_count=1, dependency_depth=0)

        self.assertEqual(resolve_cluster_assignment(cluster, difficulty)["warnings"], [])


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


class TheScopeDeclaration(unittest.TestCase):
    """`Scope:` in an issue body is the issue's own statement of what it touches (#1274)."""

    parse = staticmethod(swarm_module.parse_scope_declaration)

    def test_a_scope_line_lists_globs(self):
        body = "Fix the planner.\n\nScope: src/keel/swarm*.py, `docs/keel/swarm.md`\n"
        self.assertEqual(self.parse(body), ("src/keel/swarm*.py", "docs/keel/swarm.md"))

    def test_a_scope_heading_takes_the_bullet_list_under_it(self):
        body = (
            "## Scope\n\n- `src/keel/cli.py` — the flags\n* tests/test_swarm.py\n\n"
            "The rest is prose about src/other.py\n- src/not-scope.py\n"
        )
        self.assertEqual(self.parse(body), ("src/keel/cli.py", "tests/test_swarm.py"))

    def test_the_list_ends_at_the_next_heading(self):
        self.assertEqual(self.parse("### scope ###\n- a/b.py\n## Notes\n- c/d.py\n"), ("a/b.py",))

    def test_fenced_code_is_never_a_declaration(self):
        body = "```\nScope: src/x.py\n## Scope\n- src/y.py\n```\nScope: docs/z.md\n"
        self.assertEqual(self.parse(body), ("docs/z.md",))
        # A fence also ends a heading's list: the bullet after it is prose again.
        self.assertEqual(self.parse("## Scope\n~~~\nx\n~~~\n- src/late.py\n"), ())

    def test_declarations_add_up_and_repeat_once(self):
        self.assertEqual(self.parse("Scope: a/b.py\nscope: a/b.py, c/*\n"), ("a/b.py", "c/*"))

    def test_a_body_that_declares_nothing(self):
        for body in ("", "Scope:", "Scope: the planner", "Scope: a/..", "No scope here."):
            with self.subTest(body=body):
                self.assertEqual(self.parse(body), ())


def _areas_config(areas) -> ProjectConfig:
    return ProjectConfig(
        extends="base",
        core_version="^1.0",
        base_branch="main",
        knobs=Knobs(build_gate_cmd="make test"),
        policy_pack={"scan": {"areas": areas}},
    )


class AreaLabelsMapThroughTheProjectsAreas(unittest.TestCase):
    """`area:<name>` reads `policy_pack.scan.areas`, the mapping the scan commands use."""

    def test_the_mapping_is_the_projects_scan_areas(self):
        config = _areas_config({"docs": ["docs/**", "README.md"], "bad": "not-a-list"})
        self.assertEqual(swarm_module.scope_areas(config), {"docs": ("docs/**", "README.md")})

    def test_no_mapping_reads_as_empty(self):
        self.assertEqual(swarm_module.scope_areas(None), {})
        self.assertEqual(swarm_module.scope_areas(_areas_config(["docs/**"])), {})
        no_scan = replace(_areas_config({}), policy_pack={"scan": "docs"})
        self.assertEqual(swarm_module.scope_areas(no_scan), {})

    def test_only_mapped_areas_contribute(self):
        areas = {"docs": ("docs/**", "README.md"), "blank": ("", "a/b")}
        self.assertEqual(
            swarm_module.area_label_scope(("area:docs", "area:unknown", "role:core"), areas),
            ("docs/**", "README.md"),
        )
        self.assertEqual(swarm_module.area_label_scope(("area:blank",), areas), ("a/b",))

    def test_an_area_label_is_a_scope_source(self):
        scope = extract_issue_scope(
            7, title="Docs pass", labels=["area:docs"], config=_areas_config({"docs": ["docs/**"]})
        )
        self.assertEqual(scope.predicted_files, ("docs/**",))
        self.assertEqual(scope.scope_source, "area-label")


class TheIssueScopeOverride(unittest.TestCase):
    parse = staticmethod(swarm_module.parse_issue_scope_override)

    def test_a_valid_override(self):
        self.assertEqual(self.parse("12=src/a.py, docs/*"), (12, ("src/a.py", "docs/*")))
        self.assertEqual(self.parse("#7=a/b.py,a/b.py"), (7, ("a/b.py",)))

    def test_a_malformed_override_says_why(self):
        for text, reason in (
            ("12", "expected N=glob"),
            ("x=a.py", "positive integer"),
            ("0=a.py", "positive integer"),
            ("²=a.py", "positive integer"),
            ("12=", "at least one glob"),
            ("12= , ", "at least one glob"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, reason):
                self.parse(text)


class IssueFactsFromJson(unittest.TestCase):
    parse = staticmethod(swarm_module.issue_facts_from_json)

    def test_the_three_fields(self):
        payload = json.dumps({"title": "T", "body": "B", "labels": [{"name": "area:docs"}]})
        self.assertEqual(self.parse(payload), ("T", "B", ("area:docs",)))

    def test_a_reply_that_is_not_an_object_is_unreadable(self):
        self.assertIsNone(self.parse("not json"))
        self.assertIsNone(self.parse("[]"))

    def test_a_field_of_the_wrong_type_reads_as_empty(self):
        payload = json.dumps(
            {"title": 3, "body": None, "labels": [{"name": "a"}, {"name": 2}, "x"]}
        )
        self.assertEqual(self.parse(payload), ("", "", ("a",)))
        self.assertEqual(self.parse(json.dumps({"labels": "a"})), ("", "", ()))


class TheScopeResolvesStrongestFirst(unittest.TestCase):
    def test_the_body_declaration_wins_over_paths_named_in_prose(self):
        scope = extract_issue_scope(
            1, body="Touches `src/keel/cli.py` today.\nScope: src/keel/swarm.py"
        )
        self.assertEqual(scope.predicted_files, ("src/keel/swarm.py",))
        self.assertEqual(scope.scope_source, "issue-body")

    def test_the_override_wins_over_the_body(self):
        scope = extract_issue_scope(1, body="Scope: src/keel/swarm.py", scope=["docs/x.md"])
        self.assertEqual(scope.predicted_files, ("docs/x.md",))
        self.assertEqual(scope.scope_source, "override")

    def test_declared_files_add_to_a_declared_scope(self):
        scope = extract_issue_scope(1, body="Scope: a/b.py", declared_files=["tests/t.py"])
        self.assertEqual(scope.predicted_files, ("a/b.py", "tests/t.py"))

    def test_a_path_the_text_names_is_a_guess_kept_beside_everything(self):
        named = extract_issue_scope(1, body="See `a/b.py`")
        self.assertEqual(named.predicted_files, ("*", "a/b.py"))
        self.assertEqual(named.scope_source, "default")
        hinted = extract_issue_scope(2, labels=["area:visual"])
        self.assertEqual(hinted.predicted_files, ("*", "keel-visual/*"))

    def test_a_declared_file_is_the_operators_statement(self):
        scope = extract_issue_scope(1, body="See `a/b.py`", declared_files=["c/d.py"])
        self.assertEqual(scope.predicted_files, ("a/b.py", "c/d.py"))
        self.assertEqual(scope.scope_source, "declared-file")


class ScopeLessIssuesConflict(unittest.TestCase):
    """The safe direction (#1274): an issue nobody described is never assumed disjoint."""

    def test_two_issues_that_declare_nothing_run_one_after_the_other(self):
        plan = build_swarm_plan(
            [extract_issue_scope(1, title="Refactor"), extract_issue_scope(2, title="Tidy")],
            swarm_id="s",
        )
        self.assertEqual(plan.conflict_map, {1: (2,), 2: (1,)})
        self.assertEqual([[c.issues for c in w.clusters] for w in plan.waves], [[(1,)], [(2,)]])

    def test_the_wave_a_scope_less_issue_is_pushed_into_is_refused_by_landing(self):
        # With #1276, the later wave depends on the earlier one, so swarm-land refuses it
        # until wave 1 lands and the rest is re-planned — slow, never a collision.
        plan = build_swarm_plan(
            [extract_issue_scope(1, body="Scope: docs/a.md"), extract_issue_scope(2)],
            swarm_id="s",
        )
        self.assertEqual(
            [(w.mode, w.eligible_direct_landing) for w in plan.waves],
            [("orthogonal_parallel", True), ("sequential_dependent", False)],
        )
        self.assertEqual(plan.waves[1].clusters[0].depends_on_issues, (1,))

    def test_issues_that_only_mention_disjoint_paths_still_serialise(self):
        # The #1274 failure: two issues naming different files in prose, both of which end
        # up editing `src/keel/cli.py`. A mention is not a declaration.
        plan = build_swarm_plan(
            [
                extract_issue_scope(1, body="The bug is in `src/keel/swarm.py`."),
                extract_issue_scope(2, body="Reword `docs/keel/swarm.md`."),
            ],
            swarm_id="s",
        )
        self.assertEqual(plan.conflict_map, {1: (2,), 2: (1,)})
        self.assertEqual(len(plan.waves), 2)

    def test_a_scope_less_issue_waits_for_scoped_ones(self):
        plan = build_swarm_plan(
            [
                extract_issue_scope(1, body="Scope: docs/a.md"),
                extract_issue_scope(2, body="Scope: src/b.py"),
                extract_issue_scope(3, title="Mystery"),
            ],
            swarm_id="s",
        )
        self.assertEqual(
            [[c.issues for c in w.clusters] for w in plan.waves], [[(1,), (2,)], [(3,)]]
        )


def _issue_reader(replies):
    """A `gh issue view` stand-in. A dict is the issue's JSON, a string a raw reply,
    ``False`` a failure with nothing said, and a missing issue a 404."""

    def fake(issue, *, cwd=None, fields="title,labels", _run=None):
        fake.calls.append((int(issue), fields))
        reply = replies.get(int(issue))
        if reply is False:
            return CommandResult(False, 7, "")
        if reply is None:
            return CommandResult(False, 1, "", stderr="gh: HTTP 404: Not Found\nmore\n")
        payload = reply if isinstance(reply, str) else json.dumps(reply)
        return CommandResult(True, 0, payload, stdout=payload)

    fake.calls = []
    return fake


_REPLIES = {
    101: {"title": "Planner", "body": "Scope: src/keel/swarm.py", "labels": []},
    102: {"title": "Docs", "body": "## Scope\n- docs/keel/swarm.md\n", "labels": []},
    103: {"title": "Mystery", "body": "Something is off.", "labels": []},
}


def _cli(argv, replies=_REPLIES):
    reader = _issue_reader(replies)
    out, err = io.StringIO(), io.StringIO()
    with patch("keel.github.issue_facts", reader), redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue(), reader.calls


def _waves(plan):
    return [[c.issues[0] for c in w.clusters] for w in plan.waves]


class SwarmCommandsReadEachIssue(unittest.TestCase):
    """swarm-plan, -run and -land plan each issue from what that issue says (#1274)."""

    def test_swarm_plan_reads_each_issue_once_and_plans_its_own_scope(self):
        rc, out, err, calls = _cli(
            ["swarm-plan", ".keel/project.yaml", "--issues", "101,102,103", "--json"]
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls, [(n, "title,body,labels") for n in (101, 102, 103)])
        parsed = json.loads(out)
        scopes = {
            k: (v["predicted_files"], v["scope_source"]) for k, v in parsed["issue_scopes"].items()
        }
        self.assertEqual(
            scopes,
            {
                "101": (["src/keel/swarm.py"], "issue-body"),
                "102": (["docs/keel/swarm.md"], "issue-body"),
                "103": (["*"], "default"),
            },
        )
        waves = [[c["issues"][0] for c in w["clusters"]] for w in parsed["waves"]]
        self.assertEqual(waves, [[101, 102], [103]])
        self.assertIn("issue #103 declares no scope", err)
        self.assertNotIn("#101", err)

    def test_an_unreadable_issue_is_planned_as_everything_with_a_warning(self):
        replies = {**_REPLIES, 104: "not json", 105: False}
        rc, out, err, _ = _cli(
            ["swarm-plan", ".keel/project.yaml", "--issues", "101,102,104,105,106", "--json"],
            replies,
        )
        self.assertEqual(rc, 0, err)
        scopes = json.loads(out)["issue_scopes"]
        for issue in ("104", "105", "106"):
            self.assertEqual(scopes[issue]["predicted_files"], ["*"])
        self.assertIn("could not read issue #104 (the reply was not a JSON object)", err)
        self.assertIn("could not read issue #105 (exit 7)", err)
        self.assertIn("could not read issue #106 (gh: HTTP 404: Not Found)", err)
        self.assertIn("issue #106 declares no scope", err)

    def test_the_override_wins_over_what_the_issue_says(self):
        rc, out, err, _ = _cli(
            [
                "swarm-plan",
                ".keel/project.yaml",
                "--issues",
                "101,102",
                "--issue-scope",
                "101=docs/keel/*",
                "--json",
            ]
        )
        self.assertEqual(rc, 0, err)
        parsed = json.loads(out)
        self.assertEqual(parsed["issue_scopes"]["101"]["predicted_files"], ["docs/keel/*"])
        self.assertEqual(parsed["issue_scopes"]["101"]["scope_source"], "override")
        self.assertEqual(parsed["conflict_map"], {"101": [102], "102": [101]})

    def test_repeated_overrides_for_one_issue_add_up(self):
        rc, out, err, _ = _cli(
            [
                "swarm-plan",
                ".keel/project.yaml",
                "--issue",
                "103",
                "--issue-scope",
                "103=a/b.py",
                "--issue-scope",
                "103=c/d.py",
                "--json",
            ]
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(
            json.loads(out)["issue_scopes"]["103"]["predicted_files"], ["a/b.py", "c/d.py"]
        )
        self.assertEqual(err, "")

    def test_swarm_run_plans_each_issues_own_scope(self):
        captured = {}

        def fake_run(plan, **_kwargs):
            captured["plan"] = plan
            return swarm_module.SwarmRunResult(
                swarm_id=plan.swarm_id,
                status="success",
                total_workers=0,
                passed_count=0,
                failed_count=0,
                dry_run=True,
            )

        with patch("keel.swarm_runtime.run_swarm_orchestration", side_effect=fake_run):
            rc, _out, err, calls = _cli(
                ["swarm-run", ".keel/project.yaml", "--issues", "101,102,103", "--json"]
            )
        self.assertEqual(rc, 0, err)
        self.assertEqual([n for n, _ in calls], [101, 102, 103])
        self.assertEqual(_waves(captured["plan"]), [[101, 102], [103]])

    def test_swarm_land_plans_each_issues_own_scope(self):
        captured = {}

        def fake_land(plan, **_kwargs):
            captured["plan"] = plan
            return swarm_module.SwarmLandingResult(
                swarm_id=plan.swarm_id,
                wave_index=1,
                mode="direct_batch",
                landed_clusters=(),
                healed_clusters=(),
                failed_clusters=(),
                status="success",
            )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("keel.swarm_landing.land_wave_clusters", side_effect=fake_land),
        ):
            rc, _out, err, calls = _cli(
                [
                    "swarm-land",
                    ".keel/project.yaml",
                    "--root",
                    tmpdir,
                    "--swarm-id",
                    "s",
                    "--issues",
                    "101,102,103",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0, err)
        self.assertEqual([n for n, _ in calls], [101, 102, 103])
        self.assertEqual(_waves(captured["plan"]), [[101, 102], [103]])

    def test_one_issue_flags_are_refused_beside_several(self):
        for flag in (
            ["--declared-file", "src/a.py"],
            ["--issue-title", "t"],
            ["--issue-body", "Scope: a/b.py"],
            ["--issue-label", "area:docs"],
        ):
            with self.subTest(flag=flag[0]):
                rc, _out, err, calls = _cli(
                    ["swarm-plan", ".keel/project.yaml", "--issues", "101,102", *flag]
                )
                self.assertEqual(rc, 1)
                self.assertIn("describe one issue, and 2 were named", err)
                self.assertEqual(calls, [])

    def test_swarm_run_and_swarm_land_refuse_before_doing_anything(self):
        for command in ("swarm-run", "swarm-land"):
            with (
                self.subTest(command=command),
                patch("keel.swarm_runtime.run_swarm_orchestration", side_effect=AssertionError),
                patch("keel.swarm_landing.land_wave_clusters", side_effect=AssertionError),
            ):
                rc, _out, err, calls = _cli(
                    [command, ".keel/project.yaml", "--issues", "101", "--issue-scope", "9=a.py"]
                )
                self.assertEqual(rc, 1)
                self.assertIn("--issue-scope names #9", err)
                self.assertEqual(calls, [])

    def test_one_issue_flags_still_describe_a_single_issue(self):
        rc, out, err, calls = _cli(
            [
                "swarm-plan",
                ".keel/project.yaml",
                "--issue",
                "103",
                "--issue-body",
                "Scope: src/a.py",
                "--issue-label",
                "priority:high",
                "--json",
            ],
            {103: {"title": "Mystery", "body": "", "labels": [{"name": "size:l"}]}},
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(calls), 1)
        scope = json.loads(out)["issue_scopes"]["103"]
        self.assertEqual(scope["predicted_files"], ["src/a.py"])
        self.assertEqual(scope["title"], "Mystery")
        self.assertEqual(scope["labels"], ["priority:high", "size:l"])

    def test_an_override_for_an_issue_not_named_is_refused(self):
        rc, _out, err, calls = _cli(
            ["swarm-plan", ".keel/project.yaml", "--issues", "101", "--issue-scope", "999=a.py"]
        )
        self.assertEqual(rc, 1)
        self.assertIn("--issue-scope names #999", err)
        self.assertEqual(calls, [])

    def test_a_malformed_override_is_a_usage_error(self):
        with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            main(["swarm-plan", ".keel/project.yaml", "--issue", "1", "--issue-scope", "1="])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("at least one glob", err.getvalue())


class AWavesLandingModeFollowsItsDependencies(unittest.TestCase):
    """#1276 (part 1): the mode was ``len(current_wave_issues) > 0``, true of every wave,
    so a wave that exists *because* it overlaps an earlier one still claimed direct
    batch landing onto a base that wave had just moved."""

    def _chain(self) -> swarm_module.SwarmPlan:
        # Three issues on one file: one wave each, each depending on every earlier one.
        scopes = [
            IssueScope(issue=n, title=f"T{n}", predicted_files=("src/a.py",)) for n in (1, 2, 3)
        ]
        plan = build_swarm_plan(scopes, swarm_id="swarm-chain")
        deps = [w.clusters[0].depends_on_issues for w in plan.waves]
        self.assertEqual(deps, [(), (1,), (1, 2)], "fixture: a dependency chain")
        return plan

    def test_the_first_wave_is_orthogonal(self):
        first = self._chain().waves[0]
        self.assertEqual((first.mode, first.eligible_direct_landing), ("orthogonal_parallel", True))

    def test_a_later_wave_with_a_dependency_is_sequential(self):
        plan = self._chain()
        modes = [(w.mode, w.eligible_direct_landing) for w in plan.waves[1:]]
        self.assertEqual(modes, [("sequential_dependent", False)] * 2)
        self.assertEqual(plan.to_dict()["waves"][1]["mode"], "sequential_dependent")
        self.assertIn(
            "Wave 2 [sequential_dependent] — dependent on an earlier wave — swarm-land "
            "refuses it; land the earlier wave, then re-plan:",
            render_swarm_plan_text(plan),
        )
        tree = render_swarm_plan_tree(plan)
        self.assertIn(
            "⏳ Wave 2 [sequential_dependent] — Dependent — refused until re-planned", tree
        )
        self.assertIn("Direct Landing Waves: 1 ", tree)

    def test_no_renderer_says_a_dependent_wave_funnels(self):
        """swarm-land refuses a dependent wave (#1276), so a renderer that labels it a
        funnel promises a rebase the CLI never performs."""
        plan = self._chain()
        for name, text in (
            ("text", render_swarm_plan_text(plan)),
            ("tree", render_swarm_plan_tree(plan)),
        ):
            with self.subTest(renderer=name):
                dependent = [ln for ln in text.splitlines() if "sequential_dependent" in ln]
                self.assertEqual(len(dependent), 2, text)
                self.assertFalse([ln for ln in dependent if "funnel" in ln.lower()], text)
                self.assertNotIn("funnel", text.lower())

    def test_a_later_wave_without_a_dependency_is_orthogonal(self):
        # Issue 1 fails: wave 2 loses its only dependency, wave 3 still waits on #2.
        after = swarm_module.rebalance_swarm_plan(self._chain(), failed_issue=1)
        modes = {w.wave_index: (w.mode, w.eligible_direct_landing) for w in after.waves}
        self.assertEqual(
            modes,
            {2: ("orthogonal_parallel", True), 3: ("sequential_dependent", False)},
        )
        free = SwarmCluster(cluster_id="c", issues=(9,), role="core", combined_scope=("b.py",))
        self.assertEqual(swarm_module.wave_landing_mode(4, [free]), ("orthogonal_parallel", True))

    def test_a_single_cluster_dependent_wave_is_refused_not_funneled(self):
        """The owner's call on #1276: the funnel's overlap check is fed no real diffs
        from the CLI (#1266), so a dependent wave is refused rather than rebased."""
        plan = self._chain()
        first = swarm_module.evaluate_wave_landing_mode(plan.waves[0], {})
        self.assertEqual((first.mode, first.reason), ("direct_batch", "single_cluster"))
        later = swarm_module.evaluate_wave_landing_mode(plan.waves[1], {})
        self.assertEqual(
            (later.mode, later.eligible, later.reason),
            ("refused", False, "depends_on_earlier_wave"),
        )
        # Even a supplied diff map that would funnel an orthogonal wave does not
        # reopen the funnel for a dependent one.
        overlapping = {c.cluster_id: ["src/a.py"] for c in plan.waves[1].clusters}
        self.assertEqual(
            swarm_module.evaluate_wave_landing_mode(plan.waves[1], overlapping).mode, "refused"
        )

    def test_the_refusal_names_the_dependencies_and_the_way_through(self):
        refusal = swarm_module.render_dependent_wave_refusal(self._chain().waves[2])
        self.assertIn("wave 3 depends on issues landed by an earlier wave (#1, #2);", refusal)
        self.assertIn("its branches were cut before that landing", refusal)
        self.assertIn("Land the earlier wave, then re-plan", refusal)
        self.assertIn("keel swarm-plan / swarm-run", refusal)
        self.assertIn("until #1266 feeds it real diffs", refusal)
        self.assertIn("nothing was checked out or merged", refusal)

    def test_a_hand_built_dependent_wave_without_dependencies_still_reads(self):
        c = SwarmCluster(cluster_id="c", issues=(9,), role="core", combined_scope=("b.py",))
        wave = swarm_module.SwarmWave(
            wave_index=2, mode="sequential_dependent", eligible_direct_landing=False, clusters=(c,)
        )
        refusal = swarm_module.render_dependent_wave_refusal(wave)
        self.assertTrue(
            refusal.startswith("wave 2 depends on issues landed by an earlier wave; "), refusal
        )


if __name__ == "__main__":
    unittest.main()
