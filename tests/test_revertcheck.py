"""Unit tests for the pure half of the opt-in ``revert-check`` gate (#1289)."""

import unittest
import unittest.mock

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

    def test_a_binary_production_file_is_unrevertable(self):
        text = (
            "diff --git a/lib/a.so.py b/lib/a.so.py\n"
            "Binary files a/lib/a.so.py and b/lib/a.so.py differ\n"
        )
        plan = rc.plan_changes(rc.parse_diff(text), tests=["tests/**"], paths=[], unit="hunk")
        self.assertEqual((plan.changes, plan.unrevertable), ((), ("lib/a.so.py",)))

    def test_a_mode_change_is_its_own_change(self):
        """Review round on ac298e19: the mode rode along with every content hunk."""
        text = (
            "diff --git a/bin/tool.sh b/bin/tool.sh\nold mode 100644\nnew mode 100755\n"
            "index 1111111..2222222\n--- a/bin/tool.sh\n+++ b/bin/tool.sh\n"
            "@@ -1 +1 @@\n-A=1\n+A=2\n@@ -5 +5 @@\n-B=1\n+B=2\n"
            "diff --git a/bin/only.sh b/bin/only.sh\nold mode 100644\nnew mode 100755\n"
        )
        for unit in ("hunk", "file"):
            with self.subTest(unit=unit):
                plan = rc.plan_changes(rc.parse_diff(text), tests=["tests/**"], paths=[], unit=unit)
                labels = [c.label for c in plan.changes]
                self.assertEqual(labels[0], "bin/only.sh (mode 100644 -> 100755)")
                self.assertEqual(labels[1], "bin/tool.sh (mode 100644 -> 100755)")
                self.assertEqual(
                    plan.changes[1].patch,
                    "diff --git a/bin/tool.sh b/bin/tool.sh\nold mode 100644\nnew mode 100755\n",
                )
                self.assertEqual(len(labels), 4 if unit == "hunk" else 3)
                for change in plan.changes[2:]:
                    self.assertNotIn("mode", change.patch)
                self.assertEqual(plan.unrevertable, ())


class TestAddedCodeIsSplitIntoBlocks(unittest.TestCase):
    """Sweep after ac298e19: one test calling one of three added functions "noticed" all
    three when the hunk (or the new file) was undone whole."""

    ADDED = "\n".join(
        [
            "diff --git a/src/n.py b/src/n.py",
            "new file mode 100644",
            "index 0000000..1111111",
            "--- /dev/null",
            "+++ b/src/n.py",
            "@@ -0,0 +1,8 @@",
            "+import os",
            "+",
            "+def f():",
            "+    return 1",
            "+",
            "+@decorated",
            "+def g():",
            "+    return 2",
            "\\ No newline at end of file",
            "diff --git a/src/x.py b/src/x.py",
            "--- a/src/x.py",
            "+++ b/src/x.py",
            "@@ -1,0 +2,6 @@ a",
            "+",
            "+def h():",
            "+    return 3",
            "+",
            "+def k():",
            "+    return 4",
            "",
        ]
    )

    def test_each_top_level_block_is_a_change(self):
        plan = rc.plan_changes(rc.parse_diff(self.ADDED), tests=[], paths=[], unit="hunk")
        self.assertEqual(
            [c.label for c in plan.changes],
            [
                "src/n.py @@ -0,0 +1,2 @@",
                "src/n.py @@ -2,0 +3,3 @@",
                "src/n.py @@ -5,0 +6,3 @@",
                "src/x.py @@ -1,0 +2,4 @@",
                "src/x.py @@ -5,0 +6,2 @@",
            ],
        )
        self.assertTrue(all(c.pure_addition for c in plan.changes))
        # A new file's block is removed from the file, which stays: a plain header.
        self.assertEqual(
            plan.changes[2].patch,
            "diff --git a/src/n.py b/src/n.py\n--- a/src/n.py\n+++ b/src/n.py\n"
            "@@ -5,0 +6,3 @@\n+@decorated\n+def g():\n+    return 2\n"
            "\\ No newline at end of file\n",
        )
        self.assertEqual(
            plan.changes[3].patch,
            "diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
            "@@ -1,0 +2,4 @@\n+\n+def h():\n+    return 3\n+\n",
        )

    def test_methods_added_to_a_class_are_separate_blocks(self):
        text = (
            "diff --git a/k.py b/k.py\n--- a/k.py\n+++ b/k.py\n@@ -3,0 +4,6 @@ class K:\n"
            "+    def a(self):\n+        return 1\n+\n+    def b(self):\n"
            "+        return 2\n+\n"
        )
        plan = rc.plan_changes(rc.parse_diff(text), tests=[], paths=[], unit="hunk")
        self.assertEqual(
            [c.label for c in plan.changes], ["k.py @@ -3,0 +4,3 @@", "k.py @@ -6,0 +7,3 @@"]
        )

    def test_one_block_stays_one_change(self):
        one = "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"
        one += "@@ -0,0 +1,2 @@\n+def f():\n+    return 1\n"
        (change,) = rc.plan_changes(rc.parse_diff(one), tests=[], paths=[], unit="hunk").changes
        self.assertEqual(change.label, "a.py (new file)")
        self.assertIn("new file mode", change.patch)
        # The file unit keeps a new file whole only when it is one block, too.
        plan = rc.plan_changes(rc.parse_diff(self.ADDED), tests=[], paths=[], unit="file")
        self.assertEqual(len(plan.changes), 4)

    def test_an_unreadable_header_is_not_split(self):
        hunk = rc.Hunk("@@ nonsense", ("+a", "+", "+b"), 3, 0)
        self.assertEqual(rc._blocks(hunk, "n.py"), [])

    @staticmethod
    def _labels(path, added, header="@@ -0,0 +1,{n} @@"):
        body = "".join(f"+{line}\n" for line in added)
        text = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        text += header.format(n=len(added)) + "\n" + body
        plan = rc.plan_changes(rc.parse_diff(text), tests=[], paths=["*"], unit="hunk")
        return [c.label for c in plan.changes]

    def test_definitions_split_without_a_blank_line(self):
        """Codex, round 8: with no blank line between them, ``used`` and ``untested`` were
        one change, and a test of ``used`` passed ``untested``."""
        added = ["def used():", "    return 1", "def untested():", "    return 2"]
        self.assertEqual(
            self._labels("n.py", added), ["n.py @@ -0,0 +1,2 @@", "n.py @@ -2,0 +3,2 @@"]
        )

    def test_a_blank_line_inside_a_string_does_not_split_it(self):
        """Codex, round 8: the blank line and the column-0 ``world`` split one literal in
        two, and each half's revert was a syntax error that blocked a tested constant."""
        added = ['VALUE = """hello', "", "world", '"""', "", "def f():", "    return VALUE"]
        self.assertEqual(
            self._labels("n.py", added), ["n.py @@ -0,0 +1,5 @@", "n.py @@ -5,0 +6,2 @@"]
        )

    def test_statements_between_definitions_are_one_block(self):
        added = ["A = 1", "B = 2", "@d", "class K:", "    x = A", "C = B", "D = C"]
        self.assertEqual(
            self._labels("n.py", added),
            ["n.py @@ -0,0 +1,2 @@", "n.py @@ -2,0 +3,3 @@", "n.py @@ -5,0 +6,2 @@"],
        )

    def test_what_cannot_be_parsed_stays_one_change(self):
        cases = {
            # not Python: a boundary cannot be read from the text
            "n.js": ["function a() {", "  return 1", "}", "", "function b() {", "  return 2", "}"],
            # part of an expression
            "p.py": ["    1,", "", "    2,"],
            # a continuation left of the rest of the hunk
            "q.py": ["    def a(self):", '        return """', "x", '"""', "    def b(self):"],
            # one tab-indented line among space-indented ones
            "r.py": ["    def a(self):", "        pass", "\tdef b(self):", "\t\tpass"],
            # a null byte
            "s.py": ["def a():", "    return '\x00'", "def b():", "    pass"],
            # only blank lines
            "t.py": ["", ""],
        }
        for path, added in cases.items():
            with self.subTest(path=path):
                self.assertEqual(len(self._labels(path, added)), 1)

    def test_a_mode_only_change_does_not_look_comment_only(self):
        """agy, round 8: no changed line made ``all()`` vacuously true."""
        change = rc.Change("t.sh", "t.sh", "diff --git a/t.sh b/t.sh\nold mode 100644\n", False)
        self.assertFalse(rc.looks_comment_only(change))


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

    def test_a_pytest_node_id_may_contain_spaces(self):
        """Review finding on #1385: `\\S+` dropped these, turning a caught change unreadable."""
        tally = rc.read_output(
            "FAILED tests/test_x.py::test_value[hello world] - assert 1 == 2\n"
            "FAILED tests/my tests.py::test_b - AssertionError: no\n"
            "FAILED tests/test_x.py::test_c[a b] - NameError: name 'x' is not defined\n"
            "FAILED tests/test_x.py::test_d[c d]\n"
            "4 failed in 0.20s\n"
        )
        self.assertEqual((tally.assertions, tally.errors, tally.unclassified), (2, 2, 0))
        self.assertEqual(
            rc.classify(
                exit_ok=False,
                timed_out=False,
                output="FAILED t.py::v[hello world] - assert 1 == 2\n1 failed in 0.1s\n",
            )[0],
            rc.CAUGHT,
        )

    def test_failing_test_ids_are_read(self):
        tally = rc.read_output(
            "FAIL: test_a (t.T.test_a)\nERROR: test_b (t.T.test_b)\n"
            "FAILED t.py::c[x y] - assert 0\nFAILED t.py::d - KeyError: 1\nERROR t.py - boom\n"
        )
        self.assertEqual(tally.assertion_ids, {"test_a (t.T.test_a)", "t.py::c[x y]"})
        self.assertEqual(
            tally.failure_ids,
            {"test_a (t.T.test_a)", "test_b (t.T.test_b)", "t.py::c[x y]", "t.py::d", "t.py"},
        )
        self.assertTrue(tally.failed)
        self.assertFalse(rc.read_output("Ran 1 test in 0s\n\nOK\n").failed)

    def test_a_pytest_timeout_is_not_an_assertion(self):
        tally = rc.read_output(
            "FAILED t.py::slow - Failed: Timeout >10.0s\n"
            "FAILED t.py::fail - Failed: expected\n2 failed in 11s\n"
        )
        self.assertEqual((tally.assertions, tally.errors), (1, 1))

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


class OnlyANewAssertionFailureCounts(unittest.TestCase):
    """Review round on ac298e19: a failure the baseline already had certified a revert."""

    def test_an_assertion_the_baseline_already_had_does_not_catch(self):
        output = "FAIL: test_a (t.T.test_a)\nRan 2 tests in 0s\nFAILED (failures=1)\n"
        old = frozenset({"test_a (t.T.test_a)"})
        result, why = rc.classify(
            exit_ok=False, timed_out=False, output=output, baseline_failed=old
        )
        self.assertEqual(result, rc.UNNOTICED)
        self.assertIn("also fail without the revert", why)
        self.assertEqual(rc.classify(exit_ok=False, timed_out=False, output=output)[0], rc.CAUGHT)
        newer = output.replace("FAIL: test_a", "FAIL: test_c (t.T.test_c)\nFAIL: test_a")
        self.assertEqual(
            rc.classify(exit_ok=False, timed_out=False, output=newer, baseline_failed=old)[0],
            rc.CAUGHT,
        )

    def test_a_baseline_that_reports_failures_cannot_anchor(self):
        # `suite1; suite2`: suite1 already fails an assertion, suite2 passes, exit 0.
        for output in (
            "FAIL: test_a (s1.T.test_a)\nRan 1 test in 0s\n\nFAILED (failures=1)\n"
            "Ran 3 tests in 0s\n\nOK\n",
            "Ran 3 tests in 0s\n\nFAILED (errors=1)\nRan 1 test in 0s\n\nOK\n",
            "1 failed, 3 passed in 0.1s\n",
            "3 passed, 1 error in 0.1s\n",
        ):
            with self.subTest(output=output):
                problem = rc.baseline_problem(exit_ok=True, timed_out=False, output=output)
                self.assertIsNotNone(problem)
                self.assertIn("reports failing tests", problem)

    def test_execute_hands_the_baseline_failures_to_classify(self):
        seen = []
        original = rc.classify

        def spy(**kwargs):
            seen.append(kwargs.get("baseline_failed"))
            return original(**kwargs)

        scratch = _Scratch(runs={None: _GREEN})
        with unittest.mock.patch.object(rc, "classify", side_effect=spy):
            _execute([_change("a")], scratch=scratch)
        self.assertEqual(seen, [frozenset()])


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
_CODE = rc.Reverted(True)


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
            reverted={"c": rc.Reverted(False), "d": rc.Reverted(True)},
            runs={None: _GREEN, "a": _CAUGHT, "b": _GREEN},
        )
        changes = [rc.Change(name, f"{name}.py", "p", False) for name in "abcd"]
        report, _ = _execute(changes, _settings(run_timeout_s=30), scratch)
        self.assertIsNone(report.baseline)
        self.assertEqual(
            [(r.change.label, r.result) for r in report.results],
            [("a", rc.CAUGHT), ("b", rc.UNNOTICED), ("c", rc.NOT_APPLIED), ("d", rc.CAUGHT)],
        )
        # The tests run once for the baseline and once per change that was undone — `d`
        # changes only formatting and is tested all the same; each run is limited by
        # gate_timeout_s while the budget is larger.
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
                ("test", "d", 30),
            ],
        )

    def test_no_change_is_skipped_for_looking_harmless(self):
        """Every undone change is tested — the "inert" shortcut is gone (#1289 review).

        Each of these once skipped its run, and each changes behaviour: a `//go:embed`
        directive, a Python encoding cookie, and a re-wrap that moves `f_lineno`.
        """
        repros = {
            "embed": (
                "a.go",
                "//go:embed new.txt\nvar s string\n",
                "//go:embed old.txt\nvar s string\n",
                "-//go:embed old.txt\n+//go:embed new.txt",
            ),
            "cookie": (
                "m.py",
                "# coding: utf-8\nS = 'é'\n",
                "# coding: latin-1\nS = 'é'\n",
                "-# coding: latin-1\n+# coding: utf-8",
            ),
            "rewrap": (
                "w.py",
                "L = (\n    inspect.currentframe().f_lineno\n)\n",
                "L = (inspect.currentframe().f_lineno\n\n)\n",
                "-L = (inspect.currentframe().f_lineno\n-\n"
                "+L = (\n+    inspect.currentframe().f_lineno",
            ),
            "comment": ("c.py", "x = 1  # new\n", "x = 1  # old\n", "-# old\n+# new"),
        }
        changes = [
            rc.Change(name, path, f"diff --git a/x b/x\n@@ -1 +1 @@\n{body}\n", False)
            for name, (path, _head, _undone, body) in repros.items()
        ]
        scratch = _Scratch(
            reverted={name: rc.Reverted(True) for name in repros},
            runs={None: _GREEN, **dict.fromkeys(repros, _GREEN)},
        )
        report, _ = _execute(changes, scratch=scratch)
        tested = [call[1] for call in scratch.calls if call[0] == "test" and call[1]]
        self.assertEqual(tested, list(repros))
        self.assertEqual({r.result for r in report.results}, {rc.UNNOTICED})
        verdict = rc.judge(rc.Plan(tuple(changes), (), ()), report)
        self.assertFalse(verdict.ok)
        self.assertIn("it looks comment-only", verdict.findings[-2].message)

    def test_a_result_knows_whether_its_change_only_adds(self):
        missing = rc.RunResult(
            exit_ok=False,
            output=(
                "ERROR: test_x (t.T.test_x)\nTraceback (most recent call last):\n"
                '  File "t.py", line 3, in test_x\n    f()\n'
                "NameError: name 'f' is not defined\n\nRan 1 test in 0s\nFAILED (errors=1)\n"
            ),
        )
        behaviour = rc.RunResult(
            exit_ok=False,
            output=(
                'Traceback (most recent call last):\n  File "t.py", line 3\n'
                "KeyError: 'é'\n\nRan 1 test in 0s\nFAILED (errors=1)\n"
            ),
        )
        imports = "diff --git a/x b/x\n@@ -1 +1 @@\n-from m import a\n+from m import a, b\n"
        cookie = "diff --git a/x b/x\n@@ -1 +1 @@\n-# coding: latin-1\n+# coding: utf-8\n"
        changes = [
            rc.Change("added", "added.py", "p", True),
            rc.Change("import", "import.py", imports, False),
            rc.Change("code", "code.py", "p", False),
            rc.Change("added-keyerror", "k.py", "p", True),
            rc.Change("cookie", "c.py", cookie, False),
        ]
        scratch = _Scratch(
            runs={
                None: _GREEN,
                "added": missing,
                "import": missing,
                "code": missing,
                "added-keyerror": behaviour,
                "cookie": behaviour,
            },
        )
        report, _ = _execute(changes, scratch=scratch)
        self.assertEqual(
            [(r.change.label, r.result, r.adds_only) for r in report.results],
            [
                ("added", rc.ERRORED, True),
                ("import", rc.ERRORED, True),
                # A modification's errors never read as an addition's.
                ("code", rc.ERRORED, False),
                # Review round on 1d1326a4: an addition (or a cookie) whose revert raises
                # a KeyError changed behaviour; only a missing name is lenient.
                ("added-keyerror", rc.ERRORED, False),
                ("cookie", rc.ERRORED, False),
            ],
        )
        verdict = rc.judge(rc.Plan(tuple(changes), (), ()), report)
        self.assertFalse(verdict.ok)
        self.assertEqual(
            [f.severity for f in verdict.findings],
            ["nit", "nit", "major", "major", "major", "nit"],
        )

    def test_max_changes_leaves_the_rest_not_checked(self):
        scratch = _Scratch(runs={None: _GREEN})
        report, _ = _execute([_change("a"), _change("b")], _settings(max_changes=1), scratch)
        self.assertEqual([r.change.label for r in report.results], ["a"])
        ((change, why),) = report.not_checked
        self.assertEqual(change.label, "b")
        self.assertIn("max_changes (1)", why)

    def test_the_budget_bounds_every_run_and_then_stops(self):
        # Each clock read advances 20s against a 100s budget: `b` is reverted and then
        # found out of budget before its run, `c` before its revert.
        scratch = _Scratch(runs={None: _GREEN})
        report, _ = _execute(
            [_change("a"), _change("b"), _change("c")],
            _settings(budget_s=100, run_timeout_s=600),
            scratch,
            _Clock(step=20.0),
        )
        self.assertEqual(
            [c for c in scratch.calls if c[0] == "test"], [("test", None, 80), ("test", "a", 40)]
        )
        self.assertEqual([r.change.label for r in report.results], ["a"])
        self.assertEqual([c.label for c, _ in report.not_checked], ["b", "c"])
        self.assertIn("budget_s budget (100s)", report.not_checked[0][1])

    def test_the_budget_counts_the_revert_itself(self):
        """Review round on 17155a82: the run's limit was the budget left *before* the revert."""
        now = [0.0]

        class Slow(_Scratch):
            def revert(self, change):
                now[0] += 11.0  # resetting, cleaning and applying took 11 s
                return super().revert(change)

        scratch = Slow(runs={None: _GREEN})
        report, _ = _execute([_change("a")], _settings(budget_s=10), scratch, clock=lambda: now[0])
        self.assertEqual([c for c in scratch.calls if c[0] == "test"], [("test", None, 10)])
        self.assertEqual(report.results, ())
        ((change, why),) = report.not_checked
        self.assertEqual(change.label, "a")
        self.assertIn("budget_s budget (10s) ran out", why)
        self.assertFalse(rc.judge(rc.Plan((change,), (), ()), report).ok)


def _py(label, patch="p"):
    return rc.Change(label, f"{label}.py", patch, False)


def _diff(path, *lines):
    return rc.Change(
        "x", path, "diff --git a/x b/x\n@@ -1 +1 @@\n" + "\n".join(lines) + "\n", False
    )


class TestImportsOnly(unittest.TestCase):
    """Read from the change's own lines (review round on 1d1326a4)."""

    def test_whole_import_lines_are_imports_only(self):
        for lines in (
            ("+import os",),
            ("-import os",),
            ("-import os.path as p", "+import os.path as p, sys"),
            ("-from m import a", "+from m import a, b as c", "+"),
            ("+from . import x",),
            ("+from ..pkg.mod import (a, b,)",),
            ("+from m import *",),
        ):
            with self.subTest(lines=lines):
                self.assertTrue(rc.imports_only(_diff("m.py", *lines)))

    def test_anything_else_is_not_imports_only(self):
        for lines in (
            # codex's repro: an encoding cookie is not an import, whatever the AST says.
            ("-# coding: latin-1", "+# coding: utf-8"),
            ("+import os", "+# coding: utf-8"),
            # A multi-line import's member line, and its opening line.
            ("+    b,",),
            ("+from m import (",),
            ("+import os  # noqa",),
            ("+import os; x = 1",),
            ("+x = 1",),
            ("+",),
        ):
            with self.subTest(lines=lines):
                self.assertFalse(rc.imports_only(_diff("m.py", *lines)))
        self.assertFalse(rc.imports_only(_diff("a.ts", "+import a from 'a'")))


class TestMissingNamesOnly(unittest.TestCase):
    TB = 'Traceback (most recent call last):\n  File "t.py", line 1, in f\n    g()\n'

    def test_missing_names_are_lenient(self):
        for exc in (
            "NameError: name 'g' is not defined",
            "builtins.NameError: name 'g' is not defined",
            "UnboundLocalError: cannot access local variable 'v'",
            "ImportError: cannot import name 'g' from 'm'",
            "ModuleNotFoundError: No module named 'm'",
            "AttributeError: module 'm' has no attribute 'g'",
            "AttributeError: partially initialized module 'm' has no attribute 'g'",
            "AttributeError: type object 'K' has no attribute 'g'",
        ):
            with self.subTest(exc=exc):
                self.assertTrue(rc.missing_names_only(self.TB + exc + "\n"))
        self.assertTrue(rc.missing_names_only("FAILED t.py::a - NameError: name 'g'\n"))

    def test_any_other_error_is_behaviour(self):
        for output in (
            self.TB + "KeyError: 'é'\n",
            self.TB + "AttributeError: 'NoneType' object has no attribute 'g'\n",
            self.TB + "tests.Boom: custom\n",
            self.TB + "StopIteration\n",
            self.TB + "NameError: x\n\nDuring handling\n\n" + self.TB + "TypeError: y\n",
            "FAILED t.py::a - TypeError: no\n",
            "make: *** [test] Error 2\n",
        ):
            with self.subTest(output=output):
                self.assertFalse(rc.missing_names_only(output))


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

    def test_an_unrevertable_file_blocks_as_not_checked(self):
        # The gate never certifies what it did not check (sweep after 1d1326a4).
        verdict = rc.judge(rc.Plan((), ("bin/tool.sh",), ("bin/tool.sh",)), None)
        self.assertFalse(verdict.ok)
        self.assertFalse(verdict.skipped)
        self.assertEqual(
            [(f.severity, f.message.split(":")[0]) for f in verdict.findings],
            [("major", "bin/tool.sh")],
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

    def test_an_unnoticed_comment_only_change_says_why_it_was_tested(self):
        comment = rc.Change(
            "run.sh @@", "run.sh", "diff --git a/x b/x\n@@ -1 +1 @@\n-# a\n+# b\n", False
        )
        code = rc.Change("run.sh @@", "run.sh", "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n", False)
        other = rc.Change(
            "f.c", "f.c", "diff --git a/x b/x\n@@ -1 +1 @@\n-*p = 1;\n+*p = 2;\n", False
        )
        results = tuple(rc.ChangeResult(c, rc.UNNOTICED, "passed") for c in (comment, code, other))
        verdict = rc.judge(rc.Plan((), (), ()), rc.Report(None, results))
        self.assertFalse(verdict.ok)
        first, second, third = (f.message for f in verdict.findings[:3])
        self.assertIn("it looks comment-only, and keel tests every change", first)
        self.assertIn("encoding cookies, compiler directives, line numbers", first)
        self.assertIn("knobs.revert_check.paths", first)
        self.assertNotIn("comment-only", second)
        self.assertNotIn("comment-only", third)
        self.assertEqual({f.severity for f in verdict.findings[:3]}, {"major"})

    def test_all_caught_passes_with_a_count(self):
        results = (
            rc.ChangeResult(_change("a"), rc.CAUGHT, "x"),
            rc.ChangeResult(_change("b"), rc.ERRORED, "y", adds_only=True),
        )
        verdict = rc.judge(rc.Plan((), (), ()), rc.Report(None, results))
        self.assertTrue(verdict.ok)
        self.assertEqual([f.severity for f in verdict.findings], ["nit", "nit"])
        self.assertIn("1 of 2", verdict.findings[-1].message)


class TheDiffMustCarryNoContext(unittest.TestCase):
    def test_a_hunk_with_context_is_refused(self):
        widened = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,3 +1,3 @@\n-x = 1\n y = 2\n+x = 3\n"
        )
        problem = rc.context_problem(rc.parse_diff(widened))
        self.assertIsNotNone(problem)
        self.assertIn("a.py", problem)
        self.assertIn("GIT_DIFF_OPTS", problem)
        self.assertIsNone(rc.context_problem(rc.parse_diff(DIFF)))

    def test_changes_are_ordered_by_path_whatever_git_printed(self):
        files = rc.parse_diff(DIFF)
        plan = rc.plan_changes(tuple(reversed(files)), tests=["tests/**"], paths=[], unit="hunk")
        self.assertEqual(
            [c.path for c in plan.changes],
            ["src/calc.py", "src/calc.py", "src/new.py", "src/old.py"],
        )


if __name__ == "__main__":
    unittest.main()
