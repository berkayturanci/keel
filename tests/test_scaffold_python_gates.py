"""A scaffolded Python project gets gates it can actually run (#1297).

`keel setup` detected `stack: python` and wrote `build_gate_cmd: "make test"`
whatever the project held, so a Python project with no Makefile scaffolded a gate
that BLOCKs every `keel ship` with ``make: *** No rule to make target `test'``. It
also wrote `lint_cmd: "ruff check ."` whether or not ruff was part of the project.

The build command now follows the project: its Makefile's `test` target if there is
one, pytest if the project configures *or depends on* it, and otherwise `unittest`,
which ships with Python. The dependency case matters: most pytest projects keep no
pytest config, and `unittest discover` finds none of their plain `def test_…`
functions — from Python 3.12 it exits 5 ("NO TESTS RAN"), the same blocked gate by
another route. Lint is ruff only when the project configures ruff.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from keel import scaffold  # noqa: E402


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        patcher = mock.patch.object(scaffold.shutil, "which", return_value="/usr/bin/python")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def reset(self) -> None:
        """Empty the project, for a subtest that needs a clean one."""
        for child in self.root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def gates(self):
        t = scaffold.template_for("python", self.root)
        return t["build"], t["lint"]


class TheBuildGateFollowsTheProject(_Project):
    def test_no_makefile_and_no_pytest_uses_unittest(self):
        self.assertEqual(self.gates()[0], "python -m unittest discover")

    def test_a_makefile_with_a_test_target_is_used(self):
        self.write("Makefile", "build:\n\techo b\n\ntest:\n\tpython -m pytest\n")
        self.assertEqual(self.gates()[0], "make test")

    def test_a_makefile_without_a_test_target_is_not(self):
        """The counterweight: a Makefile alone is not a `test` target."""
        self.write("Makefile", "build:\n\techo b\n")
        self.assertEqual(self.gates()[0], "python -m unittest discover")

    def test_a_pytest_config_file_means_pytest(self):
        self.write("pytest.ini", "[pytest]\n")
        self.assertEqual(self.gates()[0], "python -m pytest")

    def test_pytest_configured_in_pyproject_means_pytest(self):
        self.write("pyproject.toml", '[project]\nname = "x"\n\n[tool.pytest.ini_options]\n')
        self.assertEqual(self.gates()[0], "python -m pytest")

    def test_pytest_as_a_mere_dependency_means_pytest(self):
        """The case that decides the default: no config, just the dependency."""
        self.write("requirements-dev.txt", "pytest>=8\nruff\n")
        self.assertEqual(self.gates()[0], "python -m pytest")

    def test_a_conftest_under_tests_means_pytest(self):
        self.write("tests/conftest.py", "")
        self.assertEqual(self.gates()[0], "python -m pytest")

    def test_python3_is_written_where_there_is_no_python(self):
        """macOS and Debian outside a venv have only `python3`."""
        with mock.patch.object(scaffold.shutil, "which", return_value=None):
            self.assertEqual(self.gates()[0], "python3 -m unittest discover")


class AnUnreadableFileIsNoEvidence(_Project):
    """Scaffolding never crashes on a file it cannot read; it just learns nothing."""

    def test_an_unreadable_makefile_is_not_a_test_target(self):
        self.write("Makefile", "test:\n\tpytest\n")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            self.assertFalse(scaffold._makefile_has_target(self.root, "test"))

    def test_an_unreadable_manifest_mentions_nothing(self):
        self.write("requirements.txt", "pytest\n")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            self.assertIsNone(scaffold._read_text(self.root / "requirements.txt"))
            self.assertFalse(scaffold._uses_pytest(self.root))

    def test_a_pyproject_that_is_not_toml_is_no_evidence(self):
        self.write("pyproject.toml", "[tool.ruff\npytest = \n")
        self.assertEqual(self.gates(), ("python -m unittest discover", None))


class TheLintGateFollowsTheProject(_Project):
    def test_no_ruff_config_means_no_lint_gate(self):
        self.assertIsNone(self.gates()[1])

    def test_ruff_configured_in_pyproject_means_ruff(self):
        self.write("pyproject.toml", '[project]\nname = "x"\n\n[tool.ruff]\nline-length = 100\n')
        self.assertEqual(self.gates()[1], "ruff check .")

    def test_a_ruff_toml_means_ruff(self):
        self.write("ruff.toml", "line-length = 100\n")
        self.assertEqual(self.gates()[1], "ruff check .")


class AMakefileRuleIsARule(_Project):
    """#1301 review: the Makefile is read for rules, not for the word `test:`."""

    def test_a_variable_assignment_is_not_a_target(self):
        for assignment in (
            "test := pytest",
            "test ::= pytest",
            "test:::= pytest",
            "test ?= pytest",
        ):
            with self.subTest(assignment):
                self.write("Makefile", f"{assignment}\nall:\n\techo hi\n")
                self.assertEqual(self.gates()[0], "python -m unittest discover")

    def test_a_multi_target_rule_names_test(self):
        self.write("Makefile", "check test:\n\tpython -m pytest\n")
        self.assertEqual(self.gates()[0], "make test")

    def test_a_double_colon_rule_and_an_indented_one_count(self):
        for makefile in ("test::\n\tpytest\n", "ifdef CI\n  test:\n\tpytest\nendif\n"):
            with self.subTest(makefile):
                self.write("Makefile", makefile)
                self.assertEqual(self.gates()[0], "make test")

    def test_a_recipe_line_is_not_a_rule(self):
        self.write("Makefile", "build:\n\ttest: x\n")
        self.assertEqual(self.gates()[0], "python -m unittest discover")

    def test_a_prerequisite_named_test_is_not_a_rule(self):
        self.write("Makefile", ".PHONY: test\nall: test\n")
        self.assertEqual(self.gates()[0], "python -m unittest discover")


class PytestIsFoundWhereProjectsDeclareIt(_Project):
    """#1301 review: the common places, and not the word inside another name."""

    def assert_pytest(self, expected=True):
        want = "python -m pytest" if expected else "python -m unittest discover"
        self.assertEqual(self.gates()[0], want)

    def test_test_requirements_and_a_requirements_directory(self):
        for name in ("test-requirements.txt", "requirements/test.txt", "dev-requirements.in"):
            with self.subTest(name):
                self.reset()
                self.write(name, "pytest-cov>=5\n")
                self.assert_pytest()

    def test_the_underscore_spelling_of_a_plugin(self):
        """PEP 503: `pytest_cov` and `pytest-cov` are one requirement (#1301 lead, round 2)."""
        for name, text in (
            ("requirements.txt", "pytest_cov\n"),
            ("pyproject.toml", '[project]\nname = "x"\ndependencies = ["pytest_cov"]\n'),
            ("Pipfile", '[dev-packages]\npytest_cov = "*"\n'),
        ):
            with self.subTest(name):
                self.reset()
                self.write(name, text)
                self.assert_pytest()

    def test_nested_requirements_and_a_noxfile(self):
        for name, text in (
            ("requirements/dev.in", "pytest\n"),
            ("requirements/test/base.txt", "pytest>=8\n"),
            ("noxfile.py", 'def tests(session):\n    session.install("pytest")\n'),
        ):
            with self.subTest(name):
                self.reset()
                self.write(name, text)
                self.assert_pytest()

    def test_a_conftest_under_test(self):
        self.write("test/conftest.py", "")
        self.assert_pytest()

    def test_poetry_and_dependency_groups(self):
        for body in (
            '[tool.poetry.group.dev.dependencies]\npytest = "^8"\n',
            '[dependency-groups]\ndev = ["pytest (>=8)"]\n',
            '[project.optional-dependencies]\ntest = ["pytest[testing]>=8"]\n',
        ):
            with self.subTest(body):
                self.write("pyproject.toml", '[project]\nname = "x"\n' + body)
                self.assert_pytest()

    def test_the_word_inside_another_name_is_not_pytest(self):
        """The ruff plugin that lints pytest style is not pytest."""
        self.write(
            "pyproject.toml",
            '[project]\nname = "x"\ndependencies = ["flake8-pytest-style"]\n\n'
            "[tool.ruff.lint.flake8-pytest-style]\nfixture-parentheses = false\n",
        )
        self.assert_pytest(False)

    def test_the_word_inside_another_name_is_not_pytest_in_a_text_file_either(self):
        self.write("requirements-dev.txt", "flake8-pytest-style\nruff\n")
        self.assert_pytest(False)

    def test_a_commented_out_requirement_is_not_one(self):
        self.write("requirements.txt", "requests\n# pytest\n")
        self.assert_pytest(False)

    def test_setup_cfg_and_tox_sections(self):
        for name, text in (
            ("setup.cfg", "[tool:pytest]\n"),
            ("tox.ini", "[testenv]\ndeps = pytest\n"),
        ):
            with self.subTest(name):
                self.reset()
                self.write(name, text)
                self.assert_pytest()


class RuffIsATableNotAString(_Project):
    def test_a_commented_table_and_a_longer_name_are_not_ruff(self):
        for body in ("# [tool.ruff]\n", "[tool.ruffle]\nx = 1\n"):
            with self.subTest(body):
                self.write("pyproject.toml", '[project]\nname = "x"\n' + body)
                self.assertIsNone(self.gates()[1])

    def test_a_dotted_ruff_table_is_ruff(self):
        self.write("pyproject.toml", '[project]\nname = "x"\n\n[tool.ruff.lint]\nselect = ["E"]\n')
        self.assertEqual(self.gates()[1], "ruff check .")


class TheUnittestGateFindsTheTests(_Project):
    """#1301 review: `unittest discover` recurses only into packages, so from the root
    it finds nothing in a `tests/` directory without an `__init__.py` and exits 5."""

    _TEST = (
        "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
    )

    def run_gate(self):
        build = self.gates()[0]
        argv = [sys.executable, *build.split()[1:]]
        return subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=60)

    def test_a_tests_directory_that_is_not_a_package_is_named(self):
        self.write("tests/test_a.py", self._TEST)
        self.assertEqual(self.gates()[0], "python -m unittest discover -s tests")
        run = self.run_gate()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Ran 1 test", run.stderr)

    def test_the_singular_test_directory_too(self):
        self.write("test/test_a.py", self._TEST)
        self.assertEqual(self.gates()[0], "python -m unittest discover -s test")
        self.assertEqual(self.run_gate().returncode, 0)

    def test_a_tests_package_keeps_the_plain_command(self):
        """The counterweight: a package is found from the root, and naming it with
        `-s` would import its modules as top-level ones and break relative imports."""
        self.write("tests/__init__.py", "")
        self.write("tests/helper.py", "X = 1\n")
        self.write("tests/test_a.py", "from .helper import X\n" + self._TEST)
        self.assertEqual(self.gates()[0], "python -m unittest discover")
        run = self.run_gate()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Ran 1 test", run.stderr)


class TheRenderedConfigCarriesThem(_Project):
    def test_default_config_writes_the_resolved_gates(self):
        text = scaffold.default_config("python", root=self.root)
        self.assertIn('build_gate_cmd: "python -m unittest discover"', text)
        self.assertNotIn("lint_cmd", text)
        self.assertNotIn("make test", text)

    def test_auto_detect_config_writes_them_too(self):
        text, meta = scaffold.auto_detect_config(self.root)
        self.assertEqual(meta["build_cmd"], "python -m unittest discover")
        self.assertIn('build_gate_cmd: "python -m unittest discover"', text)

    def test_the_wizard_offers_them_as_its_defaults(self):
        asked = {}

        def ask(prompt, default):
            asked[prompt] = default
            return ""

        scaffold.wizard("python", ask, root=self.root)
        self.assertEqual(asked["Build/test command"], "python -m unittest discover")
        self.assertEqual(asked["Lint command (blank to skip)"], "")

    def test_without_a_root_the_template_is_unchanged(self):
        """Callers that pass no project keep the stack template."""
        self.assertEqual(scaffold.template_for("python")["build"], "make test")

    def test_other_stacks_are_not_touched(self):
        self.assertEqual(scaffold.template_for("rust", self.root)["build"], "cargo test")


if __name__ == "__main__":
    unittest.main()
