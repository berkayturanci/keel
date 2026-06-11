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


class TestWizard(unittest.TestCase):
    def test_sets_window_and_validates(self):
        import yaml
        answers = iter([
            "develop",
            "Etc/GMT-3",
            "09:00-18:00",
            "agent",
            "pytest",
            "ruff check .",
        ])

        def ask(prompt, default):
            return next(answers)

        text = scaffold.wizard("python", ask, repo="demo")
        config = cfg.parse_config(yaml.safe_load(text))
        self.assertEqual(config.base_branch, "develop")
        self.assertEqual(config.timezone, "Etc/GMT-3")
        self.assertEqual(config.merge_window, "09:00-18:00")
        self.assertEqual(config.consent_mode, "agent")
        self.assertEqual(config.knobs.build_gate_cmd, "pytest")
        self.assertIn("--wizard", text)

    def test_blank_answers_skip_optional(self):
        import yaml

        def ask(prompt, default):
            if any(k in prompt for k in ("Timezone", "Merge window", "Lint")):
                return ""  # user clears optional fields
            return default

        text = scaffold.wizard("generic", ask, repo="demo")
        config = cfg.parse_config(yaml.safe_load(text))
        self.assertIsNone(config.timezone)
        self.assertIsNone(config.merge_window)
        self.assertEqual(config.gates, ("build",))

    def test_invalid_consent_mode_rejected(self):
        def ask(prompt, default):
            if "Consent mode" in prompt:
                return "maybe"
            return default

        with self.assertRaises(ValueError):
            scaffold.wizard("python", ask, repo="demo")


class TestRenderConfig(unittest.TestCase):
    def test_minimal_validates(self):
        import yaml
        text = scaffold.render_config(repo="x")
        self.assertEqual(cfg.parse_config(yaml.safe_load(text)).repo, "x")
        self.assertEqual(cfg.parse_config(yaml.safe_load(text)).consent_mode, "explicit")

    def test_invalid_consent_mode_rejected(self):
        with self.assertRaises(ValueError):
            scaffold.render_config(repo="x", consent_mode="maybe")

    def test_interpolated_scalars_cannot_inject_yaml_keys(self):
        import yaml

        text = scaffold.render_config(
            repo="demo\nplatform: injected",
            base_branch="main\nconsent_mode: standing",
            platform="python\nextensions: {pwned: true}",
            build_cmd="pytest\nextensions_dir: /tmp/pwned",
            lint_cmd="ruff check .\ngates: [build]",
            timezone="Europe/Istanbul\nrepo: changed",
            merge_window="07:00-01:30\nconsent_mode: agent",
            tier3_globs=("src/**/*.py\nrepo: changed",),
            generator="keel init\nrepo: changed",
        )
        data = yaml.safe_load(text)

        self.assertEqual(data["repo"], "demo\nplatform: injected")
        self.assertEqual(data["base_branch"], "main\nconsent_mode: standing")
        self.assertEqual(data["platform"], "python\nextensions: {pwned: true}")
        self.assertEqual(data["consent_mode"], "explicit")
        self.assertEqual(data["knobs"]["build_gate_cmd"], "pytest\nextensions_dir: /tmp/pwned")
        self.assertEqual(data["knobs"]["lint_cmd"], "ruff check .\ngates: [build]")
        self.assertEqual(data["timezone"], "Europe/Istanbul\nrepo: changed")
        self.assertEqual(data["merge_window"], "07:00-01:30\nconsent_mode: agent")
        self.assertEqual(data["knobs"]["tier3_globs"], ["src/**/*.py\nrepo: changed"])
        self.assertNotIn("pwned", data["extensions"])


if __name__ == "__main__":
    unittest.main()
