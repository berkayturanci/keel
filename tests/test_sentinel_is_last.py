"""``if __name__ == "__main__"`` is the last thing in a test file (#1141).

``unittest.main()`` calls ``sys.exit()``. A class defined *below* that block is therefore
never reached when the file is run as a script — the class is not skipped, it does not
exist yet — while ``unittest discover``, which imports the module, sees all of it. So the
file passes in CI and quietly omits tests in the loop a person actually uses:

    PYTHONPATH=src python tests/test_cli.py -k delegate

Five files were in that state, losing 54 tests between them: ``test_cli.py`` (38, the
delegate CLI wiring and the TDD-order gate), ``test_api_delegate.py`` (5),
``test_ship.py`` (3), ``test_runner.py`` (2) and ``keel-visual``'s ``test_runstate.py``
(6). A test that does not run cannot fail, so the hole reports success.

It is written down here rather than remembered because it kept happening: three separate
changes in one week each added a class below the sentinel, and each was caught by a
reviewer rather than by anything mechanical.

The rule is about *reachability*, not tidiness, so what it asserts is the consequence: a
file's standalone test count must equal its imported one. That is expensive to run for
every file, so the cheap structural check is the gate and the docstring records the
measurement.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: Every tree in this repository that holds a unittest suite.
TEST_DIRS = (REPO_ROOT / "tests", REPO_ROOT / "keel-visual" / "tests")


def _sentinel(tree: ast.Module) -> ast.If | None:
    """The ``if __name__ == "__main__":`` statement of a parsed module, if it has one."""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and any(
                isinstance(comparator, ast.Constant) and comparator.value == "__main__"
                for comparator in test.comparators
            )
        ):
            return node
    return None


class TheRunnerSentinelIsTheLastStatement(unittest.TestCase):
    def _files(self) -> list[Path]:
        found = [path for directory in TEST_DIRS for path in sorted(directory.rglob("test_*.py"))]
        self.assertTrue(found, "no test files found — TEST_DIRS is wrong")
        return found

    def test_nothing_is_defined_after_it(self):
        """Parsed, not grepped: a `__main__` string in a docstring or a comment is not a
        sentinel, and a sentinel indented inside a class is not a module-level one."""
        stranded: list[str] = []
        for path in self._files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            sentinel = _sentinel(tree)
            if sentinel is None:
                continue
            after = [
                node
                for node in tree.body
                if node.lineno > sentinel.lineno
                and isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            for node in after:
                stranded.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name} is defined "
                    f"after the runner sentinel at line {sentinel.lineno}"
                )
        self.assertEqual(
            [],
            stranded,
            "unittest.main() exits, so running these files as a script never reaches "
            "these definitions:\n" + "\n".join(stranded),
        )

    def test_the_check_reads_every_test_tree(self):
        """A new suite under a directory this does not know about is unguarded."""
        for directory in TEST_DIRS:
            with self.subTest(directory=directory.name):
                self.assertTrue(directory.is_dir(), f"{directory} is gone — update TEST_DIRS")

    def test_a_sentinel_below_a_class_is_still_found(self):
        """The finder's own shape, on a module that has the defect."""
        tree = ast.parse(
            "import unittest\n"
            "class A(unittest.TestCase):\n    pass\n"
            'if __name__ == "__main__":\n    unittest.main()\n'
            "class B(unittest.TestCase):\n    pass\n"
        )
        sentinel = _sentinel(tree)

        self.assertIsNotNone(sentinel)
        after = [n for n in tree.body if n.lineno > sentinel.lineno and isinstance(n, ast.ClassDef)]
        self.assertEqual([n.name for n in after], ["B"])

    def test_a_main_string_that_is_not_a_sentinel_is_not_one(self):
        tree = ast.parse('"""talks about __main__."""\nx = "__main__"\n')

        self.assertIsNone(_sentinel(tree))


if __name__ == "__main__":
    unittest.main()
