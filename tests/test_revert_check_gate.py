"""The opt-in `revert-check` gate end to end: real git, a real test command (#1289).

Each fixture is a tiny repository — `pkg/calc.py` and a unittest suite under `tests/` — with
`main` as the base and a feature branch checked out. The gate reverts the branch's
production changes one at a time in a scratch worktree and runs the suite with this
interpreter, so what these tests pin is the whole path: the diff, the worktree, `git apply
-R`, the run, the reading of its output, and the cleanup.
"""

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

from keel import cli, gates, revertcheck, tdd
from keel import config as cfg
from keel.gates import GateOutcome, GateSpec
from keel.runner import CommandResult

#: The suite, run with this interpreter. Double quotes group in sh and cmd.exe alike.
_CMD = f'"{sys.executable}" -m unittest discover -s tests'

_BASE_CALC = "def add(a, b):\n    return a + b\n\n\ndef sign(x):\n    return 1\n"
_BASE_TEST = (
    "import unittest\n\nfrom pkg import calc\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(calc.add(1, 2), 3)\n"
)
#: `sign` gains a guard (tested below) and, three lines further down, an untested
#: `double`. With git's default three context lines the two edits share one hunk.
_FEATURE_CALC = (
    "def add(a, b):\n    return a + b\n\n\n"
    "def sign(x):\n    if x < 0:\n        return -1\n    return 1\n\n\n"
    "def double(x):\n    return x * 3\n"
)
_SIGN_TEST = "\n    def test_sign(self):\n        self.assertEqual(calc.sign(-5), -1)\n"

_SPEC = GateSpec(revertcheck.GATE_ID, "builtin", "pre-merge", "block")


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _config(cmd=_CMD, test_paths=("tests/**",), knob=None):
    group = {"command": "x", "paths": ["pkg/**", "tests/**"]}
    if test_paths:
        group["test_paths"] = list(test_paths)
    knobs = {"build_gate_cmd": cmd}
    if knob is not None:
        knobs["revert_check"] = knob
    return cfg.parse_config(
        {
            "extends": "keel",
            "core_version": "^0.1",
            "base_branch": "main",
            "gates": ["build", revertcheck.GATE_ID],
            "knobs": knobs,
            "policy_pack": {"name": "t", "test_groups": {"unit": group}},
        }
    )


def _repo(root: Path, files: dict[str, str], *, base_calc: str = _BASE_CALC) -> Path:
    """`main` holds the base; `feat` (checked out) adds ``files`` on top of it."""
    _git(root, "init", "-q", "-b", "main")
    for key, value in (
        ("user.email", "t@example.com"),
        ("user.name", "T"),
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
    ):
        _git(root, "config", key, value)
    (root / "pkg").mkdir()
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text(base_calc, encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text(_BASE_TEST, encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feat")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feat")
    return root


def _outcome(root, config=None, *, gates_green=True):
    return cli._revert_check_outcome(_SPEC, config or _config(), str(root), gates_green=gates_green)


def _messages(outcome):
    return [(f.severity, f.message) for f in outcome.findings]


class TestRevertCheckOnARealRepository(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_change_no_test_notices_is_named_and_blocks(self):
        root = _repo(
            self.root,
            {"pkg/calc.py": _FEATURE_CALC, "tests/test_calc.py": _BASE_TEST + _SIGN_TEST},
        )
        outcome = _outcome(root)
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.unconfigured)
        majors = [m for s, m in _messages(outcome) if s == "major"]
        # Only `double` is unguarded; the `sign` guard's revert fails `test_sign`. One
        # merged hunk (git's default context) would have passed on `test_sign` alone.
        self.assertEqual(len(majors), 1)
        self.assertTrue(majors[0].startswith("pkg/calc.py @@ -6,0 +9,4 @@: no test notices"))
        self.assertIn("1 of 2 production change(s)", _messages(outcome)[-1][1])

    def test_the_users_git_config_cannot_merge_or_rename_changes(self):
        """Review round on 07030c52: `--unified=0` does not override `diff.interHunkContext`,
        and at 10000 it merged `sign`'s guard and the untested `double` into one change,
        which `test_sign` then passed."""
        # `add`'s trailing blanks are removed on the branch, so undoing that change writes
        # them back — which `apply.whitespace=error` refuses unless the call pins it.
        root = _repo(
            self.root,
            {"pkg/calc.py": _FEATURE_CALC, "tests/test_calc.py": _BASE_TEST + _SIGN_TEST},
            base_calc=_BASE_CALC.replace("return a + b\n", "return a + b  \n"),
        )
        for key, value in (
            ("diff.interHunkContext", "10000"),
            ("diff.context", "10"),
            ("diff.noprefix", "true"),
            ("diff.mnemonicPrefix", "true"),
            ("diff.relative", "true"),
            ("diff.suppressBlankEmpty", "true"),
            ("diff.algorithm", "patience"),
            ("diff.renames", "copies"),
            ("diff.external", "false"),
            ("diff.submodule", "log"),
            ("core.quotePath", "true"),
            ("color.ui", "always"),
            ("color.diff", "always"),
            ("apply.whitespace", "error"),
            ("apply.ignoreWhitespace", "change"),
        ):
            _git(root, "config", key, value)
        with patch.dict(os.environ, {"GIT_DIFF_OPTS": "--unified=5"}):
            outcome = _outcome(root)
        majors = [m for s, m in _messages(outcome) if s == "major"]
        self.assertEqual(len(majors), 1, _messages(outcome))
        self.assertTrue(majors[0].startswith("pkg/calc.py @@ -6,0 +9,4 @@: no test notices"))
        self.assertIn("1 of 2 production change(s)", _messages(outcome)[-1][1])
        self.assertIn(
            "1 changed only comments, docstrings or formatting", _messages(outcome)[-1][1]
        )

    def test_a_diff_with_context_cannot_judge(self):
        root = _repo(self.root, {"pkg/calc.py": _FEATURE_CALC})
        widened = (
            "diff --git a/pkg/calc.py b/pkg/calc.py\n--- a/pkg/calc.py\n+++ b/pkg/calc.py\n"
            "@@ -5,2 +5,3 @@\n def sign(x):\n+    y = 1\n     return 1\n"
        )
        with patch("keel.git.revert_diff", return_value=widened):
            outcome = _outcome(root)
        self.assertEqual((outcome.ok, outcome.unconfigured), (False, True))
        self.assertIn("the diff carries context lines", outcome.findings[0].message)

    def test_every_change_caught_passes_and_leaves_no_trace(self):
        feature = _FEATURE_CALC.replace("\n\n\ndef double(x):\n    return x * 3\n", "\n")
        root = _repo(
            self.root, {"pkg/calc.py": feature, "tests/test_calc.py": _BASE_TEST + _SIGN_TEST}
        )
        outcome = _outcome(root)
        self.assertTrue(outcome.ok, _messages(outcome))
        self.assertEqual(
            _messages(outcome),
            [
                (
                    "nit",
                    "1 of 1 production change(s) made a test fail as an assertion when "
                    "reverted alone",
                )
            ],
        )
        # The operator's checkout is untouched, and the scratch worktree is gone.
        self.assertEqual(_git(root, "status", "--porcelain", "--ignored"), "")
        listing = _git(root, "worktree", "list", "--porcelain").splitlines()
        self.assertEqual(len([line for line in listing if line.startswith("worktree ")]), 1)

    def test_an_error_proves_an_addition_but_not_a_modification(self):
        root = _repo(
            self.root,
            {
                # A modification whose test can only error once it is reverted: the old
                # signature takes no third argument.
                "pkg/calc.py": _BASE_CALC.replace(
                    "def add(a, b):\n    return a + b\n",
                    "def add(a, b, c=0):\n    return a + b + c\n",
                ),
                # An added module: without it the test module cannot import.
                "pkg/extra.py": "VALUE = 2\n",
                "tests/test_calc.py": _BASE_TEST
                + "\n    def test_three(self):\n        self.assertEqual(calc.add(1, 2, 3), 6)\n",
                "tests/test_extra.py": (
                    "import unittest\n\nfrom pkg.extra import VALUE\n\n\n"
                    "class E(unittest.TestCase):\n"
                    "    def test_value(self):\n        self.assertEqual(VALUE, 2)\n"
                ),
            },
        )
        outcome = _outcome(root)
        self.assertFalse(outcome.ok)
        found = _messages(outcome)
        self.assertEqual([s for s, _ in found], ["major", "nit", "nit"])
        self.assertTrue(found[0][1].startswith("pkg/calc.py @@ -1,2 +1,2 @@: no test failed"))
        self.assertIn("errored and none failed as an assertion", found[0][1])
        self.assertTrue(found[1][1].startswith("pkg/extra.py (new file): noticed only as an error"))

    def test_a_name_added_to_an_import_is_an_addition(self):
        base = "from math import floor\n\n\n" + _BASE_CALC
        feature = (
            base.replace("from math import floor\n", "from math import floor, sqrt\n")
            + "\n\ndef root(x):\n    return sqrt(x)\n"
        )
        test = (
            _BASE_TEST + "\n    def test_root(self):\n        self.assertEqual(calc.root(9), 3)\n"
        )
        root = _repo(
            self.root, {"pkg/calc.py": feature, "tests/test_calc.py": test}, base_calc=base
        )
        outcome = _outcome(root)
        self.assertTrue(outcome.ok, _messages(outcome))
        found = _messages(outcome)
        self.assertEqual(found[0][0], "nit")
        self.assertTrue(found[0][1].startswith("pkg/calc.py @@ -1 +1 @@: noticed only as an error"))

    def test_a_comment_or_docstring_change_is_not_run(self):
        commented = '"""Calculator."""\n\n\n' + _BASE_CALC.replace(
            "    return 1\n", "    return 1  # always positive\n"
        )
        root = _repo(self.root, {"pkg/calc.py": commented})
        with patch("keel.cli.run_command", wraps=cli.run_command) as runs:
            outcome = _outcome(root)
        self.assertTrue(outcome.ok, _messages(outcome))
        self.assertEqual(runs.call_count, 1)  # the baseline, and nothing reverted was run
        self.assertEqual(
            _messages(outcome),
            [
                (
                    "nit",
                    "0 of 0 production change(s) made a test fail as an assertion when "
                    "reverted alone; 2 changed only comments, docstrings or formatting and "
                    "were not run",
                )
            ],
        )

    def test_a_suite_red_on_a_clean_head_cannot_judge(self):
        root = _repo(
            self.root,
            {"pkg/calc.py": _FEATURE_CALC, "tests/test_calc.py": _BASE_TEST + _SIGN_TEST},
        )
        # Committed tests that fail on HEAD itself: nothing a revert does can be read.
        (root / "tests" / "test_red.py").write_text(
            "import unittest\n\n\nclass R(unittest.TestCase):\n"
            "    def test_red(self):\n        self.assertTrue(False)\n",
            encoding="utf-8",
        )
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "red")
        outcome = _outcome(root)
        self.assertEqual((outcome.ok, outcome.unconfigured), (False, True))
        self.assertIn("fails on a clean checkout of HEAD", outcome.findings[0].message)

    def test_a_docs_only_branch_is_skipped(self):
        root = _repo(self.root, {"README.md": "docs\n"})
        outcome = _outcome(root)
        self.assertEqual((outcome.ok, outcome.skipped), (True, True))
        self.assertIn("nothing to revert", outcome.findings[0].message)

    def test_a_failed_revert_is_not_a_pass(self):
        root = _repo(self.root, {"pkg/calc.py": _FEATURE_CALC})
        refused = CommandResult(False, 1, "error: patch failed")
        for target, value in (
            ("keel.git.apply_reverse", refused),
            ("keel.git.reset_clean", False),
        ):
            with self.subTest(target=target), patch(target, return_value=value):
                outcome = _outcome(root)
                self.assertFalse(outcome.ok)
                self.assertIn("git apply -R could not undo it", outcome.findings[0].message)

    def test_a_scratch_worktree_that_cannot_be_made_cannot_judge(self):
        root = _repo(self.root, {"pkg/calc.py": _FEATURE_CALC})
        for output, expected in (
            ("fatal: disk full\n", "fatal: disk full"),
            ("", "git worktree add failed"),
        ):
            failed = CommandResult(False, 128, output)
            with (
                self.subTest(output=output),
                patch("keel.git.worktree_add_detached", return_value=failed),
            ):
                outcome = _outcome(root)
                self.assertEqual((outcome.ok, outcome.unconfigured), (False, True))
                self.assertIn(f"scratch worktree: {expected}", outcome.findings[0].message)

    def test_an_unreadable_diff_cannot_judge(self):
        root = _repo(self.root, {"pkg/calc.py": _FEATURE_CALC})
        with patch("keel.git.revert_diff", return_value=None):
            outcome = _outcome(root)
        self.assertEqual((outcome.ok, outcome.unconfigured), (False, True))
        self.assertIn(
            "could not read the diff between refs/heads/main", outcome.findings[0].message
        )

    def test_the_prechecks_come_before_any_git(self):
        with patch("keel.git.revert_diff") as diff:
            no_layout = _outcome(self.root, _config(test_paths=()))
            red = _outcome(self.root, gates_green=False)
        diff.assert_not_called()
        self.assertTrue(no_layout.unconfigured)
        self.assertIn("test_paths", no_layout.findings[0].message)
        self.assertEqual((red.ok, red.unconfigured), (False, False))
        self.assertIn("guard and test gates are red", red.findings[0].message)


class TestTheGateIsWiredIntoTheGateRun(unittest.TestCase):
    def _specs(self):
        return (
            GateSpec("build", "command", "test", "block", run="x"),
            _SPEC,
            GateSpec(tdd.GATE_ID, "builtin", "test", "block"),
        )

    def test_it_is_planned_opt_in_at_pre_merge_and_deferred(self):
        config = _config()
        specs = gates.plan_gates(config, {})
        self.assertEqual(
            [(s.id, s.kind, s.phase) for s in specs],
            [("build", "command", "test"), (revertcheck.GATE_ID, "builtin", "pre-merge")],
        )
        now, later = gates.split_deferred(specs)
        self.assertEqual([s.id for s in later], [revertcheck.GATE_ID])
        self.assertIn(revertcheck.GATE_ID, gates.BUILTIN_GATES)
        # Not listed, not planned: it is opt-in.
        plain = cfg.parse_config({**_raw(), "gates": ["build"]})
        self.assertNotIn(revertcheck.GATE_ID, [s.id for s in gates.plan_gates(plain, {})])

    def test_both_deferred_gates_read_the_other_gates_verdict(self):
        seen = []

        def fake_revert(spec, config, root, *, gates_green):
            seen.append(("revert", gates_green))
            return GateOutcome(spec.id, True)

        def fake_order(spec, config, root, *, gates_green):
            seen.append(("order", gates_green))
            return GateOutcome(spec.id, True), "result"

        for build_ok in (True, False):
            with (
                patch("keel.cli._revert_check_outcome", side_effect=fake_revert),
                patch("keel.cli._tdd_order_outcome", side_effect=fake_order),
            ):
                outcomes, result = cli._run_planned_gates(
                    self._specs(), lambda spec, ok=build_ok: (ok, []), config=None, root="."
                )
            self.assertEqual(
                [o.gate for o in outcomes], ["build", revertcheck.GATE_ID, tdd.GATE_ID]
            )
            self.assertEqual(result, "result")
        self.assertEqual(
            seen,
            [("revert", True), ("order", True), ("revert", False), ("order", False)],
        )

    def test_a_scope_that_excludes_one_deferred_gate_still_runs_the_other(self):
        """`--phases guard,test` defers `revert-check` (pre-merge) and still judges
        `tdd-order` (test). The first version returned as soon as the first deferred gate
        was out of scope, which dropped the second — the dogfood run found no test for it."""

        def fake_order(spec, config, root, *, gates_green):
            return GateOutcome(spec.id, True), "result"

        with (
            patch("keel.cli._revert_check_outcome") as never,
            patch("keel.cli._tdd_order_outcome", side_effect=fake_order),
        ):
            outcomes, result = cli._run_planned_gates(
                self._specs(),
                lambda spec: (True, []),
                config=None,
                root=".",
                phases=frozenset({"guard", "test"}),
            )
        never.assert_not_called()
        by_gate = {o.gate: o for o in outcomes}
        self.assertEqual(list(by_gate), ["build", revertcheck.GATE_ID, tdd.GATE_ID])
        self.assertTrue(by_gate[revertcheck.GATE_ID].not_run)
        self.assertFalse(by_gate[tdd.GATE_ID].not_run)
        self.assertEqual(result, "result")

    def test_the_s4_loop_scope_reports_it_not_run(self):
        with patch("keel.cli._revert_check_outcome") as never:
            outcomes, _ = cli._run_planned_gates(
                self._specs()[:2],
                lambda spec: (True, []),
                config=None,
                root=".",
                phases=frozenset({"guard", "test"}),
            )
        never.assert_not_called()
        check = {o.gate: o for o in outcomes}[revertcheck.GATE_ID]
        self.assertTrue(check.not_run)

    def test_run_gates_prints_the_unnoticed_change_and_exits_one(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = _repo(Path(d), {"pkg/calc.py": _FEATURE_CALC})
            (root / ".keel").mkdir()
            project = root / ".keel" / "project.yaml"
            project.write_text(json.dumps(_raw()), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            cwd = os.getcwd()
            try:
                os.chdir(root)
                with redirect_stdout(out), redirect_stderr(err):
                    rc = cli.main(["run-gates", str(project), "--root", str(root)])
            finally:
                os.chdir(cwd)
        self.assertEqual(rc, 1, err.getvalue())
        text = out.getvalue()
        self.assertIn("FAIL  revert-check", text)
        self.assertIn("pkg/calc.py @@ -5,0 +6,2 @@: no test notices this change", text)


def _raw():
    return {
        "extends": "keel",
        "core_version": "^0.1",
        "base_branch": "main",
        "repo": "tmp",
        "gates": ["build", revertcheck.GATE_ID],
        "knobs": {"build_gate_cmd": _CMD},
        "policy_pack": {
            "name": "t",
            "test_groups": {
                "unit": {"command": "x", "paths": ["pkg/**"], "test_paths": ["tests/**"]}
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
