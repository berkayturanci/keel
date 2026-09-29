"""Unit tests for the pure half of the opt-in ``revert-check`` gate (#1289)."""

import unittest

from keel import revertcheck as rc


def _settings(**overrides):
    base = rc.resolve(None, build_cmd="make test", gate_timeout_s=600)
    values = {**base.__dict__, **overrides}
    return rc.Settings(**values)


#: Two separate edits to one file (zero context, as `git.revert_diff` asks for), a new
#: file, a deleted file, a test file and a README.
DIFF = "\n".join(
    [
        "diff --git a/src/calc.py b/src/calc.py",
        "index 1111111..2222222 100644",
        "--- a/src/calc.py",
        "+++ b/src/calc.py",
        "@@ -6 +6,3 @@ def sign(x):",
        "-    return 1",
        "+    if x < 0:",
        "+        return -1",
        "+    return 1",
        "@@ -9,0 +12,2 @@ def sign(x):",
        "+def double(x):",
        "+    return x * 2",
        "diff --git a/src/new.py b/src/new.py",
        "new file mode 100644",
        "index 0000000..3333333",
        "--- /dev/null",
        "+++ b/src/new.py",
        "@@ -0,0 +1 @@",
        "+VALUE = 1",
        "diff --git a/src/old.py b/src/old.py",
        "deleted file mode 100644",
        "index 4444444..0000000",
        "--- a/src/old.py",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-OLD = 1",
        "diff --git a/tests/test_calc.py b/tests/test_calc.py",
        "index 5555555..6666666 100644",
        "--- a/tests/test_calc.py",
        "+++ b/tests/test_calc.py",
        "@@ -1,0 +2 @@",
        "+    assert calc.sign(-1) == -1",
        "diff --git a/README.md b/README.md",
        "index 7777777..8888888 100644",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1 +1 @@",
        "-old",
        "+new",
        "",
    ]
)


class TestResolve(unittest.TestCase):
    def test_defaults_read_the_build_command_and_the_gate_timeout(self):
        s = rc.resolve(None, build_cmd="make test", gate_timeout_s=42)
        self.assertEqual(s.cmd, "make test")
        self.assertEqual(s.cmd_source, "knobs.build_gate_cmd")
        self.assertEqual(s.paths, ())
        self.assertEqual(s.unit, rc.UNIT_HUNK)
        self.assertEqual(s.max_changes, rc.DEFAULT_MAX_CHANGES)
        self.assertEqual(s.budget_s, rc.DEFAULT_BUDGET_S)
        self.assertEqual(s.run_timeout_s, 42)

    def test_the_knob_overrides_every_default(self):
        knob = {
            "cmd": "pytest tests/unit",
            "paths": ["src/**", " ", 3],
            "unit": "file",
            "max_changes": 3,
            "budget_s": 60,
        }
        s = rc.resolve(knob, build_cmd="make test", gate_timeout_s=600)
        self.assertEqual(s.cmd, "pytest tests/unit")
        self.assertEqual(s.cmd_source, "knobs.revert_check.cmd")
        self.assertEqual(s.paths, ("src/**",))
        self.assertEqual(s.unit, rc.UNIT_FILE)
        self.assertEqual((s.max_changes, s.budget_s), (3, 60))

    def test_a_blank_command_falls_back_and_no_command_is_none(self):
        s = rc.resolve({"cmd": "  "}, build_cmd="make test", gate_timeout_s=600)
        self.assertEqual((s.cmd, s.cmd_source), ("make test", "knobs.build_gate_cmd"))
        self.assertIsNone(rc.resolve({}, build_cmd=" ", gate_timeout_s=600).cmd)
        self.assertIsNone(rc.resolve(None, build_cmd=None, gate_timeout_s=600).cmd)

    def test_values_the_schema_would_refuse_read_as_defaults(self):
        knob = {"unit": "line", "max_changes": True, "budget_s": 0, "paths": "src/**"}
        s = rc.resolve(knob, build_cmd="x", gate_timeout_s=0)
        self.assertEqual(s.unit, rc.UNIT_HUNK)
        self.assertEqual(s.max_changes, rc.DEFAULT_MAX_CHANGES)
        self.assertEqual(s.budget_s, rc.DEFAULT_BUDGET_S)
        self.assertEqual(s.paths, ())
        self.assertEqual(s.run_timeout_s, 1)


class TestTestPaths(unittest.TestCase):
    def test_only_declared_test_paths_count(self):
        pack = {
            "test_groups": {
                "unit": {"paths": ["src/**", "tests/**"], "test_paths": ["tests/**"]},
                "b": {"paths": ["lib/**"]},
                "a": {"test_paths": ["spec/**", "tests/**"]},
                "junk": "not a group",
            }
        }
        self.assertEqual(rc.test_paths(pack), ("spec/**", "tests/**"))

    def test_selectors_alone_are_not_test_paths(self):
        # keel's own `unit` group selects `src/**`: read as tests, every production file
        # would be skipped.
        self.assertEqual(rc.test_paths({"test_groups": {"u": {"paths": ["src/**"]}}}), ())
        self.assertEqual(rc.test_paths({"test_groups": "x"}), ())
        self.assertEqual(rc.test_paths(None), ())


class TestIsProduction(unittest.TestCase):
    def test_tests_never_count_even_when_paths_match(self):
        self.assertFalse(rc.is_production("tests/t.py", tests=["tests/**"], paths=["**"]))

    def test_paths_select_when_set(self):
        self.assertTrue(rc.is_production("src/a.yaml", tests=[], paths=["src/**"]))
        self.assertFalse(rc.is_production("lib/a.py", tests=[], paths=["src/**"]))

    def test_the_default_is_a_source_suffix(self):
        self.assertTrue(rc.is_production("src/A.PY", tests=["tests/**"], paths=[]))
        self.assertFalse(rc.is_production("README.md", tests=["tests/**"], paths=[]))


class TestParseDiff(unittest.TestCase):
    def test_files_statuses_and_hunks(self):
        files = rc.parse_diff(DIFF)
        self.assertEqual(
            [(f.path, f.status, len(f.hunks)) for f in files],
            [
                ("src/calc.py", "modified", 2),
                ("src/new.py", "added", 1),
                ("src/old.py", "deleted", 1),
                ("tests/test_calc.py", "modified", 1),
                ("README.md", "modified", 1),
            ],
        )
        first, second = files[0].hunks
        self.assertEqual((first.added, first.removed, first.pure_addition), (3, 1, False))
        self.assertEqual((second.added, second.removed, second.pure_addition), (2, 0, True))
        self.assertEqual(second.lines, ("+def double(x):", "+    return x * 2"))

    def test_leading_text_is_skipped(self):
        self.assertEqual(rc.parse_diff("warning: something\n" + DIFF)[0].path, "src/calc.py")

    def test_a_name_with_a_space_or_quotes_is_read_as_the_name(self):
        text = (
            "diff --git a/my file.py b/my file.py\n"
            "--- a/my file.py\t\n+++ b/my file.py\t\n@@ -1 +1 @@\n-a\n+b\n"
            'diff --git "a/q\\"x.py" "b/q\\"x.py"\n'
            '--- "a/q\\"x.py"\n+++ "b/q\\"x.py"\n@@ -1 +1 @@\n-a\n+b\n'
        )
        self.assertEqual([f.path for f in rc.parse_diff(text)], ["my file.py", 'q\\"x.py'])

    def test_a_file_without_hunks_keeps_its_diff_git_name(self):
        text = (
            "diff --git a/bin/tool.sh b/bin/tool.sh\nold mode 100644\nnew mode 100755\n"
            "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
        )
        files = rc.parse_diff(text)
        self.assertEqual([(f.path, f.hunks) for f in files], [("bin/tool.sh", ()), ("img.png", ())])

    def test_hunk_bodies_are_consumed_by_their_counts(self):
        # A blank context line (diff.suppressBlankEmpty) and the no-newline marker, both
        # mid-hunk and after the last counted line, stay inside the hunk.
        text = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,3 +1,3 @@\n x\n\n-y\n\\ No newline at end of file\n+z\n"
            "\\ No newline at end of file\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-p\n+q\n"
        )
        a, b = rc.parse_diff(text)
        self.assertEqual(
            a.hunks[0].lines,
            (" x", "", "-y", "\\ No newline at end of file", "+z", "\\ No newline at end of file"),
        )
        self.assertEqual(b.path, "b.py")

    def test_an_unreadable_hunk_header_carries_no_body(self):
        text = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ nonsense\n+x\n"
        (only,) = rc.parse_diff(text)
        self.assertEqual((only.hunks[0].lines, only.hunks[0].added), ((), 0))


class TestPlanChanges(unittest.TestCase):
    def test_one_change_per_hunk_and_one_per_new_or_deleted_file(self):
        plan = rc.plan_changes(rc.parse_diff(DIFF), tests=["tests/**"], paths=[], unit="hunk")
        self.assertEqual(
            [(c.label, c.pure_addition) for c in plan.changes],
            [
                ("src/calc.py @@ -6 +6,3 @@", False),
                ("src/calc.py @@ -9,0 +12,2 @@", True),
                ("src/new.py (new file)", True),
                ("src/old.py (deleted file)", False),
            ],
        )
        self.assertEqual(plan.unrevertable, ())
        self.assertEqual(len(plan.touched), 5)
        # Each patch carries its file's header and only its own hunk.
        self.assertEqual(
            plan.changes[1].patch,
            "diff --git a/src/calc.py b/src/calc.py\nindex 1111111..2222222 100644\n"
            "--- a/src/calc.py\n+++ b/src/calc.py\n"
            "@@ -9,0 +12,2 @@ def sign(x):\n+def double(x):\n+    return x * 2\n",
        )

    def test_the_file_unit_reverts_a_whole_file(self):
        plan = rc.plan_changes(rc.parse_diff(DIFF), tests=["tests/**"], paths=[], unit="file")
        self.assertEqual(
            [(c.label, c.pure_addition) for c in plan.changes],
            [
                ("src/calc.py", False),
                ("src/new.py (new file)", True),
                ("src/old.py (deleted file)", False),
            ],
        )
        self.assertIn("@@ -9,0 +12,2 @@", plan.changes[0].patch)

    def test_a_production_file_with_no_hunk_is_unrevertable(self):
        text = "diff --git a/bin/tool.sh b/bin/tool.sh\nold mode 100644\nnew mode 100755\n"
        plan = rc.plan_changes(rc.parse_diff(text), tests=["tests/**"], paths=[], unit="hunk")
        self.assertEqual((plan.changes, plan.unrevertable), ((), ("bin/tool.sh",)))


class TestReadOutput(unittest.TestCase):
    def test_unittest_failures_are_assertions_and_errors_are_not(self):
        tally = rc.read_output(
            "Ran 7 tests in 0.1s\n\nFAILED (failures=2, errors=1, skipped=1, "
            "expected failures=1, unexpected successes=1)\n"
        )
        self.assertEqual(
            (tally.recognized, tally.ran, tally.assertions, tally.errors, tally.unclassified),
            (True, 7, 2, 2, 0),
        )

    def test_unittest_ok_and_two_suites_are_summed(self):
        tally = rc.read_output("Ran 1 test in 0s\n\nOK\nRan 2 tests in 0s\n\nOK (skipped=1)\n")
        self.assertEqual((tally.recognized, tally.ran, tally.assertions), (True, 3, 0))

    def test_unittests_failed_line_is_not_a_pytest_failure(self):
        # Regression: `FAILED (failures=1)` once matched the pytest short-summary pattern,
        # counting one phantom error beside the real assertion.
        tally = rc.read_output("Ran 1 test in 0s\n\nFAILED (failures=1)\n")
        self.assertEqual((tally.assertions, tally.errors), (1, 0))

    def test_pytest_reads_the_short_summary_for_the_cause(self):
        tally = rc.read_output(
            "=== short test summary info ===\n"
            "FAILED tests/t.py::test_a - assert 1 == 2\n"
            "FAILED tests/t.py::test_b - AssertionError: nope\n"
            "FAILED tests/t.py::test_c - Failed: explicit\n"
            "FAILED tests/t.py::test_d - NameError: name 'x' is not defined\n"
            "ERROR tests/u.py - ImportError: boom\n"
            "===== 4 failed, 3 passed, 1 error, 1 warning in 0.12s =====\n"
        )
        self.assertEqual(
            (tally.recognized, tally.ran, tally.assertions, tally.errors, tally.unclassified),
            (True, 7, 3, 2, 0),
        )

    def test_a_pytest_failure_nobody_describes_is_unclassified(self):
        tally = rc.read_output("FAILED t.py::a\n2 failed, 1 passed in 1.0s (0:00:01)\n")
        self.assertEqual((tally.assertions, tally.errors, tally.unclassified), (0, 1, 1))

    def test_no_tests_ran_is_recognised(self):
        tally = rc.read_output("============ no tests ran in 0.01s ============\n")
        self.assertEqual((tally.recognized, tally.ran), (True, 0))

    def test_other_output_is_not_recognised(self):
        self.assertFalse(rc.read_output("--- FAIL: TestX (0.00s)\nFAIL\n").recognized)


class TestClassify(unittest.TestCase):
    def test_each_result(self):
        cases = [
            ({"timed_out": True, "exit_ok": False, "output": ""}, rc.TIMED_OUT),
            ({"timed_out": False, "exit_ok": True, "output": ""}, rc.UNNOTICED),
            ({"exit_ok": False, "output": "Ran 1 test in 0s\nFAILED (failures=1)\n"}, rc.CAUGHT),
            ({"exit_ok": False, "output": "make: *** Error 2\n"}, rc.UNREADABLE),
            ({"exit_ok": False, "output": "Ran 1 test in 0s\nFAILED (errors=1)\n"}, rc.ERRORED),
            ({"exit_ok": False, "output": "1 failed in 0.1s\n"}, rc.UNREADABLE),
            ({"exit_ok": False, "output": "Ran 1 test in 0s\nOK\n"}, rc.UNREADABLE),
        ]
        for kwargs, expected in cases:
            kwargs.setdefault("timed_out", False)
            with self.subTest(kwargs=kwargs):
                result, why = rc.classify(**kwargs)
                self.assertEqual(result, expected)
                self.assertTrue(why)
        self.assertIn(
            "-rf", rc.classify(exit_ok=False, timed_out=False, output="1 failed in 1s")[1]
        )


class TestBaseline(unittest.TestCase):
    def test_only_a_readable_green_run_with_tests_anchors_the_check(self):
        green = "Ran 3 tests in 0s\n\nOK\n"
        self.assertIsNone(rc.baseline_problem(exit_ok=True, timed_out=False, output=green))
        cases = [
            ({"exit_ok": False, "timed_out": True, "output": ""}, "timed out"),
            ({"exit_ok": False, "timed_out": False, "output": green}, "fails on a clean"),
            ({"exit_ok": True, "timed_out": False, "output": "all good\n"}, "no unittest"),
            ({"exit_ok": True, "timed_out": False, "output": "Ran 0 tests in 0s\n"}, "ran no"),
        ]
        for kwargs, needle in cases:
            with self.subTest(needle=needle):
                self.assertIn(needle, rc.baseline_problem(**kwargs))


def _change(label, pure_addition=False):
    return rc.Change(label, label, f"patch {label}\n", pure_addition)


_GREEN = rc.RunResult(exit_ok=True, output="Ran 2 tests in 0s\n\nOK\n")
_CAUGHT = rc.RunResult(exit_ok=False, output="Ran 2 tests in 0s\nFAILED (failures=1)\n")
_CODE = rc.Reverted(True, "x = 1\n", "x = 2\n")


class _Clock:
    def __init__(self, step=0.0):
        self.now = 100.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


class _Scratch:
    """Fakes both I/O halves: which change is undone, and what the test run then says."""

    def __init__(self, reverted=None, runs=None):
        self.reverted = reverted or {}
        self.runs = runs or {}
        self.current = None
        self.calls = []

    def revert(self, change):
        self.current = change.label
        self.calls.append(("revert", change.label))
        return self.reverted.get(change.label, _CODE)

    def test(self, timeout):
        self.calls.append(("test", self.current, timeout))
        return self.runs.get(self.current, _CAUGHT)


def _execute(changes, settings=None, scratch=None, clock=None):
    scratch = scratch or _Scratch()
    report = rc.execute(
        changes,
        settings or _settings(),
        revert=scratch.revert,
        test=scratch.test,
        clock=clock or _Clock(),
    )
    return report, scratch


class TestExecute(unittest.TestCase):
    def test_a_baseline_problem_reverts_nothing(self):
        scratch = _Scratch(runs={None: rc.RunResult(exit_ok=False)})
        report, _ = _execute([_change("a")], scratch=scratch)
        self.assertIsNotNone(report.baseline)
        self.assertIn("fails on a clean", report.baseline)
        self.assertEqual(scratch.calls, [("test", None, 600)])

    def test_each_change_is_run_alone_and_classified(self):
        scratch = _Scratch(
            reverted={"c": rc.Reverted(False), "d": rc.Reverted(True, "x = 1\n", "x  =  1\n")},
            runs={None: _GREEN, "a": _CAUGHT, "b": _GREEN},
        )
        changes = [rc.Change(name, f"{name}.py", "p", False) for name in "abcd"]
        report, _ = _execute(changes, _settings(run_timeout_s=30), scratch)
        self.assertIsNone(report.baseline)
        self.assertEqual(
            [(r.change.label, r.result) for r in report.results],
            [("a", rc.CAUGHT), ("b", rc.UNNOTICED), ("c", rc.NOT_APPLIED), ("d", rc.INERT)],
        )
        # The tests run once for the baseline and once per change that has behaviour;
        # each run is limited by gate_timeout_s while the budget is larger.
        self.assertEqual(
            scratch.calls,
            [
                ("test", None, 30),
                ("revert", "a"),
                ("test", "a", 30),
                ("revert", "b"),
                ("test", "b", 30),
                ("revert", "c"),
                ("revert", "d"),
            ],
        )

    def test_a_result_knows_whether_its_change_only_adds(self):
        errored = rc.RunResult(exit_ok=False, output="Ran 1 test in 0s\nFAILED (errors=1)\n")
        scratch = _Scratch(
            reverted={
                "added": _CODE,
                "import": rc.Reverted(
                    True, "from m import a, b\nx = 1\n", "from m import a\nx = 1\n"
                ),
                "code": _CODE,
            },
            runs={None: _GREEN, "added": errored, "import": errored, "code": errored},
        )
        changes = [
            rc.Change("added", "added.py", "p", True),
            rc.Change("import", "import.py", "p", False),
            rc.Change("code", "code.py", "p", False),
        ]
        report, _ = _execute(changes, scratch=scratch)
        self.assertEqual(
            [(r.change.label, r.result, r.adds_only) for r in report.results],
            [
                ("added", rc.ERRORED, True),
                ("import", rc.ERRORED, True),
                ("code", rc.ERRORED, False),
            ],
        )

    def test_max_changes_leaves_the_rest_not_checked(self):
        scratch = _Scratch(runs={None: _GREEN})
        report, _ = _execute([_change("a"), _change("b")], _settings(max_changes=1), scratch)
        self.assertEqual([r.change.label for r in report.results], ["a"])
        ((change, why),) = report.not_checked
        self.assertEqual(change.label, "b")
        self.assertIn("max_changes (1)", why)

    def test_the_budget_bounds_every_run_and_then_stops(self):
        # Each clock read advances 40s against a 100s budget.
        scratch = _Scratch(runs={None: _GREEN})
        report, _ = _execute(
            [_change("a"), _change("b"), _change("c")],
            _settings(budget_s=100, run_timeout_s=600),
            scratch,
            _Clock(step=40.0),
        )
        self.assertEqual(
            [c for c in scratch.calls if c[0] == "test"], [("test", None, 60), ("test", "a", 20)]
        )
        self.assertEqual([r.change.label for r in report.results], ["a"])
        self.assertEqual([c.label for c, _ in report.not_checked], ["b", "c"])
        self.assertIn("budget_s budget (100s)", report.not_checked[0][1])


def _py(label, patch="p"):
    return rc.Change(label, f"{label}.py", patch, False)


class TestImportsOnly(unittest.TestCase):
    def test_only_an_import_differs(self):
        head = "import os\nfrom m import a, b\n\n\ndef f():\n    return a\n"
        self.assertTrue(
            rc.imports_only(_py("m"), head, "from m import a\n\n\ndef f():\n    return a\n")
        )

    def test_anything_else_is_not_imports_only(self):
        self.assertFalse(rc.imports_only(_py("m"), "import os\nx = 1\n", "x = 2\n"))
        self.assertFalse(rc.imports_only(_py("m"), "def (:\n", "def (:\n"))
        self.assertFalse(rc.imports_only(_py("m"), None, "x = 1\n"))
        self.assertFalse(rc.imports_only(_py("m"), "x = 1\n", None))
        ts = rc.Change("x", "a.ts", "p", False)
        self.assertFalse(rc.imports_only(ts, "import a from 'a'\n", "\n"))


class TestBehaviourFree(unittest.TestCase):
    def test_python_comments_docstrings_and_formatting_have_no_behaviour(self):
        head = (
            '"""Module doc."""\n\n# a comment\ndef f(x):\n    """Doc."""\n'
            '    y = x  # why\n    "attribute doc"\n    return y\n'
        )
        reverted = "def f(x):\n    y=x\n    return (y)\n"
        self.assertTrue(rc.behaviour_free(_py("m"), head, reverted))
        # An expression statement that is not a string is behaviour, and kept.
        self.assertTrue(rc.behaviour_free(_py("m"), "log(1)\n", "log( 1 )  # note\n"))
        self.assertFalse(rc.behaviour_free(_py("m"), "log(1)\n", "log(2)\n"))

    def test_python_code_changes_have_behaviour(self):
        self.assertFalse(rc.behaviour_free(_py("m"), "x = 1\n", "x = 2\n"))
        # A string that is not a bare statement is behaviour.
        self.assertFalse(rc.behaviour_free(_py("m"), 'X = "a"\n', 'X = "b"\n'))
        # Unparseable on either side: when in doubt, the tests run.
        self.assertFalse(rc.behaviour_free(_py("m"), "def (:\n", "def (:\n"))
        self.assertFalse(rc.behaviour_free(_py("m"), "x = 1\n", "x = \0\n"))

    def test_a_file_added_or_deleted_always_has_behaviour(self):
        self.assertFalse(rc.behaviour_free(_py("m"), None, ""))
        self.assertFalse(rc.behaviour_free(_py("m"), "", None))

    def test_other_languages_read_their_comment_markers(self):
        patch = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n-// old\n+  // new\n+\n"
        self.assertTrue(rc.behaviour_free(rc.Change("x", "src/a.ts", patch, False), "", ""))
        code = patch + "+const x = 1;\n"
        self.assertFalse(rc.behaviour_free(rc.Change("x", "src/a.ts", code, False), "", ""))
        # `#` is a comment in a shell script and code in C.
        hashes = "diff --git a/x b/x\n@@ -1 +1 @@\n-#include <a.h>\n+#include <b.h>\n"
        self.assertTrue(rc.behaviour_free(rc.Change("x", "run.sh", hashes, False), "", ""))
        self.assertFalse(rc.behaviour_free(rc.Change("x", "main.c", hashes, False), "", ""))
        # A suffix keel knows no marker for, or none at all, always runs.
        self.assertFalse(rc.behaviour_free(rc.Change("x", "a.zig", hashes, False), "", ""))
        self.assertFalse(rc.behaviour_free(rc.Change("x", "Makefile", hashes, False), "", ""))


class TestVerdict(unittest.TestCase):
    def test_precheck_order(self):
        missing = rc.precheck(_settings(cmd=None), tests=[], gates_green=False)
        self.assertTrue(missing.unconfigured)
        self.assertIn("knobs.revert_check.cmd", missing.findings[0].message)
        layout = rc.precheck(_settings(), tests=[], gates_green=False)
        self.assertTrue(layout.unconfigured)
        self.assertIn("test_paths", layout.findings[0].message)
        red = rc.precheck(_settings(), tests=["tests/**"], gates_green=False)
        self.assertIsNotNone(red, "red gates must stop the check before it spends a run")
        self.assertFalse(red.ok)
        self.assertFalse(red.unconfigured)
        self.assertEqual(red.findings[0].severity, "major")
        self.assertIsNone(rc.precheck(_settings(), tests=["tests/**"], gates_green=True))

    def test_no_production_change_is_skipped_never_ok(self):
        plan = rc.Plan((), (), ("README.md",))
        verdict = rc.judge(plan, None)
        self.assertTrue(verdict.skipped)
        self.assertEqual(verdict.findings[0].severity, "nit")
        self.assertIn("none of the 1 changed file(s)", verdict.findings[0].message)

    def test_an_unrevertable_file_is_named_as_a_suggestion(self):
        verdict = rc.judge(rc.Plan((), ("bin/tool.sh",), ("bin/tool.sh",)), None)
        self.assertTrue(verdict.ok)
        self.assertFalse(verdict.skipped)
        self.assertEqual(
            [(f.severity, f.message.split(":")[0]) for f in verdict.findings],
            [("minor", "bin/tool.sh")],
        )

    def test_a_baseline_problem_cannot_judge(self):
        verdict = rc.judge(rc.Plan((_change("a"),), (), ("a",)), rc.Report("it broke"))
        self.assertEqual((verdict.ok, verdict.unconfigured), (False, True))
        self.assertEqual(verdict.findings[0].message, "cannot judge: it broke")

    def test_every_change_must_be_caught(self):
        results = (
            rc.ChangeResult(_change("caught"), rc.CAUGHT, "1 test(s) failed"),
            rc.ChangeResult(_change("added"), rc.ERRORED, "1 errored", adds_only=True),
            rc.ChangeResult(_change("crashed"), rc.UNREADABLE, "no summary", adds_only=True),
            rc.ChangeResult(_change("modified"), rc.ERRORED, "1 errored"),
            rc.ChangeResult(_change("quiet"), rc.UNNOTICED, "passed"),
            rc.ChangeResult(_change("slow"), rc.TIMED_OUT, "timed out"),
        )
        not_checked = ((_change("late"), "budget"),)
        plan = rc.Plan(tuple(r.change for r in results) + (not_checked[0][0],), (), ())
        verdict = rc.judge(plan, rc.Report(None, results, not_checked))
        self.assertFalse(verdict.ok)
        self.assertEqual(
            [(f.severity, f.message.split(":")[0]) for f in verdict.findings],
            [
                ("nit", "added"),
                ("nit", "crashed"),
                ("major", "modified"),
                ("major", "quiet"),
                ("major", "slow"),
                ("major", "late"),
                (
                    "nit",
                    "1 of 7 production change(s) made a test fail as an assertion when "
                    "reverted alone",
                ),
            ],
        )
        self.assertIn("no test notices this change", verdict.findings[3].message)
        self.assertIn("no test failed as an assertion", verdict.findings[2].message)

    def test_all_caught_passes_with_a_count(self):
        results = (
            rc.ChangeResult(_change("a"), rc.CAUGHT, "x"),
            rc.ChangeResult(_change("b"), rc.ERRORED, "y", adds_only=True),
        )
        verdict = rc.judge(rc.Plan((), (), ()), rc.Report(None, results))
        self.assertTrue(verdict.ok)
        self.assertEqual([f.severity for f in verdict.findings], ["nit", "nit"])
        self.assertIn("1 of 2", verdict.findings[-1].message)
        self.assertNotIn("comments", verdict.findings[-1].message)

    def test_an_inert_change_is_counted_apart_and_never_blocks(self):
        results = (
            rc.ChangeResult(_change("a"), rc.CAUGHT, "x"),
            rc.ChangeResult(_change("doc"), rc.INERT, "only comments"),
        )
        verdict = rc.judge(rc.Plan((), (), ()), rc.Report(None, results))
        self.assertTrue(verdict.ok)
        self.assertEqual(
            [(f.severity, f.message) for f in verdict.findings],
            [
                (
                    "nit",
                    "1 of 1 production change(s) made a test fail as an assertion when "
                    "reverted alone; 1 changed only comments, docstrings or formatting and "
                    "were not run",
                )
            ],
        )


class CFamilyInertnessNeedsTheCommentsToBeTheOnlyDifference(unittest.TestCase):
    """A leading ``*`` is a comment only inside ``/* … */`` (gate finding on #1385)."""

    @staticmethod
    def _change(path: str, removed: str, added: str) -> rc.Change:
        patch = f"diff --git a/{path} b/{path}\n@@ -1 +1 @@\n-{removed}\n+{added}\n"
        return rc.Change("c", path, patch, False)

    def test_a_pointer_write_is_not_a_comment(self):
        change = self._change("main.c", "*p = 1;", "*p = 2;")
        head = "void f(int *p) {\n*p = 2;\n}\n"
        undone = "void f(int *p) {\n*p = 1;\n}\n"
        self.assertFalse(rc.behaviour_free(change, head, undone))

    def test_a_doc_comment_edit_is_inert(self):
        change = self._change("main.c", " * old words", " * new words")
        self.assertTrue(
            rc.behaviour_free(
                change, "/**\n * new words\n */\nint x;\n", "/**\n * old words\n */\nint x;\n"
            )
        )

    def test_a_comment_marker_inside_a_string_is_text(self):
        change = self._change("a.js", "// one", "// two")
        head = "const s = `\n// two\n`;\n"
        undone = "const s = `\n// one\n`;\n"
        self.assertFalse(rc.behaviour_free(change, head, undone))

    def test_a_line_comment_change_is_inert(self):
        change = self._change("a.go", "// a", "// b")
        self.assertTrue(rc.behaviour_free(change, "// b\nx := 1\n", "// a\nx := 1\n"))

    def test_the_stripper_keeps_strings_and_escapes(self):
        self.assertEqual(rc._strip_c_comments('a = "http://x"; // c\nb'), 'a = "http://x"; b')
        self.assertEqual(rc._strip_c_comments('a = "x\\"//y"; /* z */ b'), 'a = "x\\"//y"; b')
        self.assertEqual(rc._strip_c_comments("a /* never closed"), "a")
        self.assertEqual(rc._strip_c_comments("a // to the end"), "a")
        self.assertEqual(rc._strip_c_comments("'unterminated"), "'unterminated")
        self.assertEqual(rc._strip_c_comments("  x  \n\t y "), "x y")


if __name__ == "__main__":
    unittest.main()
