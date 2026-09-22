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
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            self.assertFalse(scaffold._file_mentions(self.root / "pyproject.toml", "pytest"))


class TheLintGateFollowsTheProject(_Project):
    def test_no_ruff_config_means_no_lint_gate(self):
        self.assertIsNone(self.gates()[1])

    def test_ruff_configured_in_pyproject_means_ruff(self):
        self.write("pyproject.toml", '[project]\nname = "x"\n\n[tool.ruff]\nline-length = 100\n')
        self.assertEqual(self.gates()[1], "ruff check .")

    def test_a_ruff_toml_means_ruff(self):
        self.write("ruff.toml", "line-length = 100\n")
        self.assertEqual(self.gates()[1], "ruff check .")


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
