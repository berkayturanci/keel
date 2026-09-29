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


def _git_bytes(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout


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


def _repo(
    root: Path, files: dict[str, str], *, base_calc: str = _BASE_CALC, autocrlf: str = ""
) -> Path:
    """`main` holds the base; `feat` (checked out) adds ``files`` on top of it.

    Files are written with the line endings their text holds (``newline=""``), and
    ``autocrlf`` pins ``core.autocrlf`` when given, so a CRLF test commits CRLF on Windows too.
    """
    _git(root, "init", "-q", "-b", "main")
    settings = [
        ("user.email", "t@example.com"),
        ("user.name", "T"),
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
    ]
    if autocrlf:
        settings.append(("core.autocrlf", autocrlf))
    for key, value in settings:
        _git(root, "config", key, value)
    (root / "pkg").mkdir()
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text(base_calc, encoding="utf-8", newline="")
    (root / "tests" / "test_calc.py").write_text(_BASE_TEST, encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feat")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
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

    def test_an_inherited_git_dir_cannot_reach_the_operators_checkout(self):
        """Codex, round 9: run from a git hook (``GIT_DIR`` set) or under ``GIT_WORK_TREE``,
        the scratch tree's ``reset --hard`` and ``clean -fdx`` acted on the operator's
        checkout, discarding its edits and untracked files."""
        root = _repo(
            self.root,
            {"pkg/calc.py": _FEATURE_CALC, "tests/test_calc.py": _BASE_TEST + _SIGN_TEST},
        )
        edited = _FEATURE_CALC + "\n# work in progress\n"
        (root / "pkg" / "calc.py").write_text(edited, encoding="utf-8")
        (root / "notes.txt").write_text("untracked\n", encoding="utf-8")
        env = {
            "GIT_DIR": str(root / ".git"),
            "GIT_WORK_TREE": str(root),
            "GIT_INDEX_FILE": str(root / ".git" / "index"),
        }
        with patch.dict(os.environ, env):
            outcome = _outcome(root)
        self.assertEqual((root / "pkg" / "calc.py").read_text(encoding="utf-8"), edited)
        self.assertEqual((root / "notes.txt").read_text(encoding="utf-8"), "untracked\n")
        self.assertIn("pkg/calc.py", _git(root, "status", "--porcelain"))
        majors = [m for s, m in _messages(outcome) if s == "major"]
        self.assertEqual(len(majors), 1)
        self.assertTrue(majors[0].startswith("pkg/calc.py @@ -6,0 +9,4 @@: no test notices"))

    def test_a_crlf_source_reverts_byte_for_byte(self):
        """Codex, round 9: the diff was read in text mode, which drops ``\\r``, so a CRLF
        file's reverse patch no longer matched ``HEAD`` and every change in it was
        reported *could not undo*."""
        crlf = {"pkg/calc.py": _FEATURE_CALC.replace("\n", "\r\n")}
        root = _repo(
            self.root,
            {**crlf, "tests/test_calc.py": _BASE_TEST + _SIGN_TEST},
            base_calc=_BASE_CALC.replace("\n", "\r\n"),
            autocrlf="false",
        )
        self.assertIn(b"\r\n", _git_bytes(root, "show", "HEAD:pkg/calc.py"))
        outcome = _outcome(root)
        messages = [m for _s, m in _messages(outcome)]
        self.assertFalse(any("could not undo" in m for m in messages), messages)
        majors = [m for s, m in _messages(outcome) if s == "major"]
        self.assertEqual(len(majors), 1)
        self.assertTrue(majors[0].startswith("pkg/calc.py @@ -6,0 +9,4 @@: no test notices"))

    def test_the_suite_does_not_see_the_repository_variables(self):
        probe = f"\"{sys.executable}\" -c \"import os; print(os.environ.get('GIT_DIR', 'unset'))\""
        with patch.dict(os.environ, {"GIT_DIR": str(self.root)}):
            result = cli._revert_tester(str(self.root), probe)(60)
        self.assertEqual((result.exit_ok, result.output.strip()), (True, "unset"))

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
        # Three separate changes, each undone: the whitespace fix (which only applies
        # because `apply.whitespace` is pinned), the tested guard and the untested `double`.
        self.assertEqual(
            [m.split(":")[0] for m in majors],
            ["pkg/calc.py @@ -2 +2 @@", "pkg/calc.py @@ -6,0 +9,4 @@"],
            _messages(outcome),
        )
        self.assertTrue(all("no test notices this change" in m for m in majors))
        self.assertIn("1 of 3 production change(s)", _messages(outcome)[-1][1])

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
        self.assertTrue(
            found[1][1].startswith("pkg/extra.py (new file): noticed only as a missing name")
        )

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
        self.assertTrue(
            found[0][1].startswith("pkg/calc.py @@ -1 +1 @@: noticed only as a missing name")
        )

    def test_a_comment_or_docstring_change_is_tested(self):
        # Nothing is skipped as harmless: both edits are undone and the suite runs for each.
        documented = '"""Calc."""\n' + _BASE_CALC
        commented = '"""Calculator."""\n' + _BASE_CALC.replace(
            "    return 1\n", "    return 1  # always positive\n"
        )
        root = _repo(self.root, {"pkg/calc.py": commented}, base_calc=documented)
        with patch("keel.cli.run_command", wraps=cli.run_command) as runs:
            outcome = _outcome(root)
        self.assertEqual(runs.call_count, 3)  # the baseline, then one run per change
        self.assertFalse(outcome.ok)
        self.assertEqual(
            [m.split(":")[0] for s, m in _messages(outcome) if s == "major"],
            ["pkg/calc.py @@ -1 +1 @@", "pkg/calc.py @@ -7 +7 @@"],
        )
        self.assertIn("0 of 2 production change(s)", _messages(outcome)[-1][1])

    def test_a_comment_only_change_is_tested_and_says_why(self):
        commented = _BASE_CALC.replace("def sign(x):\n", "# sign of x\ndef sign(x):\n")
        root = _repo(self.root, {"pkg/calc.py": commented})
        with patch("keel.cli.run_command", wraps=cli.run_command) as runs:
            outcome = _outcome(root)
        self.assertEqual(runs.call_count, 2)
        self.assertFalse(outcome.ok)
        major = _messages(outcome)[0]
        self.assertEqual(major[0], "major")
        self.assertIn("no test notices this change", major[1])
        self.assertIn("it looks comment-only, and keel tests every change", major[1])

    def test_a_latin_1_source_reverts_byte_for_byte(self):
        """Review round on 1d1326a4: a strict UTF-8 write of the patch raised
        UnicodeEncodeError on the surrogate git's Latin-1 byte decodes to."""
        root = _repo(self.root, {"README.md": "enc\n"})
        cookie = b"# -*- coding: latin-1 -*-\n"
        (root / "pkg" / "enc.py").write_bytes(cookie + b"VALUE = '\xe9'\n")
        (root / "tests" / "test_enc.py").write_text(
            "import unittest\n\nfrom pkg import enc\n\n\nclass E(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(enc.VALUE, '\\u00e8')\n",
            encoding="utf-8",
        )
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "enc base")
        _git(root, "branch", "-f", "main", "HEAD")
        (root / "pkg" / "enc.py").write_bytes(cookie + b"VALUE = '\xe8'\n")
        _git(root, "commit", "-qam", "enc")
        try:
            outcome = _outcome(root)
        except UnicodeError as exc:  # the old strict write, measured
            self.fail(f"the revert crashed instead of applying: {exc!r}")
        self.assertTrue(outcome.ok, _messages(outcome))
        self.assertIn("1 of 1 production change(s)", _messages(outcome)[-1][1])

    @unittest.skipIf(os.name == "nt", "git on Windows records no executable bit")
    def test_a_mode_change_is_checked_apart_from_the_content(self):
        """Review round on ac298e19: a test of the executable bit caught every hunk."""
        base = "A = 1\n\n\n\n\nB = 1\n"
        root = _repo(
            self.root,
            {
                "tests/test_tool.py": (
                    "import os\nimport unittest\n\n\nclass M(unittest.TestCase):\n"
                    "    def test_executable(self):\n"
                    "        self.assertTrue(os.access('pkg/tool.py', os.X_OK))\n"
                )
            },
        )
        (root / "pkg" / "tool.py").write_text(base, encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "tool base")
        _git(root, "branch", "-f", "main", "HEAD")
        (root / "pkg" / "tool.py").write_text(base.replace("= 1", "= 2"), encoding="utf-8")
        (root / "pkg" / "tool.py").chmod(0o755)
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "tool changes")
        outcome = _outcome(root)
        found = _messages(outcome)
        self.assertFalse(outcome.ok, found)
        self.assertEqual(
            [m.split(":")[0] for s, m in found if s == "major"],
            ["pkg/tool.py @@ -1 +1 @@", "pkg/tool.py @@ -6 +6 @@"],
        )
        self.assertIn("1 of 3 production change(s)", found[-1][1])

    def test_a_baseline_that_reports_a_failure_cannot_judge(self):
        """`suite1; suite2` exits with suite2's status while suite1 fails an assertion."""
        root = _repo(
            self.root,
            {
                "pkg/calc.py": _FEATURE_CALC,
                "tests/test_red.py": (
                    "import unittest\n\n\nclass R(unittest.TestCase):\n"
                    "    def test_red(self):\n        self.assertEqual(1, 2)\n"
                ),
            },
        )
        both = (
            f'"{sys.executable}" -c "import subprocess, sys; '
            "subprocess.call([sys.executable, '-m', 'unittest', 'tests.test_red']); "
            "sys.exit(subprocess.call([sys.executable, '-m', 'unittest', 'tests.test_calc']))\""
        )
        outcome = _outcome(root, _config(cmd=both))
        self.assertEqual((outcome.ok, outcome.unconfigured), (False, True), _messages(outcome))
        self.assertIn("reports failing tests", outcome.findings[0].message)

    def test_each_added_function_must_be_noticed_on_its_own(self):
        """Sweep after ac298e19: a new module undone whole was "noticed" by any import."""
        root = _repo(
            self.root,
            {
                "pkg/extra.py": "def used():\n    return 1\n\n\ndef untested():\n    return 2\n",
                "tests/test_extra.py": (
                    "import unittest\n\nfrom pkg import extra\n\n\nclass E(unittest.TestCase):\n"
                    "    def test_used(self):\n        self.assertEqual(extra.used(), 1)\n"
                ),
            },
        )
        outcome = _outcome(root)
        found = _messages(outcome)
        self.assertFalse(outcome.ok, found)
        self.assertEqual(
            [(s, m.split(":")[0]) for s, m in found[:2]],
            [("nit", "pkg/extra.py @@ -0,0 +1,4 @@"), ("major", "pkg/extra.py @@ -4,0 +5,2 @@")],
        )
        self.assertIn("no test notices this change", found[1][1])

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
