"""Unit tests for the `keel init` scaffolder (detection + template validity)."""

import tempfile
import unittest
from pathlib import Path

from keel import config as cfg
from keel import scaffold


def _root_with(*markers):
    d = tempfile.mkdtemp()
    for m in markers:
        (Path(d) / m).write_text("x", encoding="utf-8")
    return d


class TestDetectStack(unittest.TestCase):
    def test_flutter(self):
        self.assertEqual(scaffold.detect_stack(_root_with("pubspec.yaml")), "flutter")

    def test_python_pyproject(self):
        self.assertEqual(scaffold.detect_stack(_root_with("pyproject.toml")), "python")

    def test_python_setup(self):
        self.assertEqual(scaffold.detect_stack(_root_with("setup.py")), "python")

    def test_node(self):
        self.assertEqual(scaffold.detect_stack(_root_with("package.json")), "node")

    def test_android(self):
        self.assertEqual(scaffold.detect_stack(_root_with("build.gradle.kts")), "android")

    def test_generic_when_no_marker(self):
        self.assertEqual(scaffold.detect_stack(_root_with()), "generic")


class TestDefaultConfig(unittest.TestCase):
    def test_every_stack_validates(self):
        for stack in ("flutter", "python", "node", "android", "generic"):
            text = scaffold.default_config(stack, repo="demo")
            import yaml
            data = yaml.safe_load(text)
            # parse_config raises on any schema error
            config = cfg.parse_config(data, source=f"<{stack}>")
            self.assertEqual(config.extends, "keel")
            self.assertEqual(config.repo, "demo")

    def test_generic_has_no_lint_gate(self):
        text = scaffold.default_config("generic")
        self.assertIn("gates: [build]", text)

    def test_flutter_has_lint_gate(self):
        text = scaffold.default_config("flutter")
        self.assertIn("gates: [build, lint]", text)
        self.assertIn("flutter analyze", text)

    def test_unknown_stack_falls_back_generic(self):
        self.assertIn("gates: [build]", scaffold.default_config("cobol"))


if __name__ == "__main__":
    unittest.main()
