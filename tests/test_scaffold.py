"""Unit tests for the `keel init` scaffolder (detection + template validity)."""

import atexit
import itertools
import tempfile
import unittest
from pathlib import Path

from keel import config as cfg
from keel import scaffold

# Module-level scratch directory holding the per-call root dirs created by
# ``_root_with``. Cleaned at process exit so the suite leaves no stray temp dirs.
_TMP = tempfile.TemporaryDirectory()
atexit.register(_TMP.cleanup)
_TMP_COUNTER = itertools.count()


def _root_with(*markers):
    d = Path(_TMP.name) / f"root-{next(_TMP_COUNTER)}"
    d.mkdir()
    for m in markers:
        (d / m).write_text("x", encoding="utf-8")
    return str(d)


class TestDetectStack(unittest.TestCase):
    def test_flutter(self):
        self.assertEqual(scaffold.detect_stack(_root_with("pubspec.yaml")), "flutter")

    def test_python_pyproject(self):
        self.assertEqual(scaffold.detect_stack(_root_with("pyproject.toml")), "python")

    def test_python_setup(self):
        self.assertEqual(scaffold.detect_stack(_root_with("setup.py")), "python")

    def test_python_requirements(self):
        self.assertEqual(scaffold.detect_stack(_root_with("requirements.txt")), "python")

    def test_python_pipfile(self):
        self.assertEqual(scaffold.detect_stack(_root_with("Pipfile")), "python")

    def test_node(self):
        self.assertEqual(scaffold.detect_stack(_root_with("package.json")), "node")

    def test_android(self):
        self.assertEqual(scaffold.detect_stack(_root_with("build.gradle.kts")), "android")

    def test_android_groovy(self):
        self.assertEqual(scaffold.detect_stack(_root_with("build.gradle")), "android")

    def test_rust(self):
        self.assertEqual(scaffold.detect_stack(_root_with("Cargo.toml")), "rust")

    def test_go(self):
        self.assertEqual(scaffold.detect_stack(_root_with("go.mod")), "go")

    def test_java_maven(self):
        self.assertEqual(scaffold.detect_stack(_root_with("pom.xml")), "java")

    def test_generic_when_no_marker(self):
        self.assertEqual(scaffold.detect_stack(_root_with()), "generic")


class TestDetectBaseBranch(unittest.TestCase):
    def test_detects_develop_from_head(self):
        root = Path(_root_with())
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/develop\n", encoding="utf-8")
        self.assertEqual(scaffold.detect_base_branch(root), "develop")

    def test_detects_master_from_head(self):
        root = Path(_root_with())
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
        self.assertEqual(scaffold.detect_base_branch(root), "master")

    def test_detects_trunk_from_head(self):
        root = Path(_root_with())
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/trunk\n", encoding="utf-8")
        self.assertEqual(scaffold.detect_base_branch(root), "trunk")

    def test_defaults_to_main_when_no_git(self):
        self.assertEqual(scaffold.detect_base_branch(_root_with()), "main")

    def test_defaults_to_main_when_detached_or_feature_branch(self):
        root = Path(_root_with())
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/feat/my-feature\n", encoding="utf-8")
        self.assertEqual(scaffold.detect_base_branch(root), "main")

    def test_defaults_to_main_when_detached_sha(self):
        root = Path(_root_with())
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("d0eeca895b6878\n", encoding="utf-8")
        self.assertEqual(scaffold.detect_base_branch(root), "main")

    def test_defaults_to_main_when_head_unreadable(self):
        root = Path(_root_with())
        git_dir = root / ".git"
        git_dir.mkdir()
        # Create HEAD as a directory so read_text() raises IsADirectoryError (OSError)
        (git_dir / "HEAD").mkdir()
        self.assertEqual(scaffold.detect_base_branch(root), "main")


class TestAutoDetectConfig(unittest.TestCase):
    def test_auto_detect_rust(self):
        root = Path(_root_with("Cargo.toml"))
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        text, meta = scaffold.auto_detect_config(root, repo="rust-app")
        self.assertEqual(meta["stack"], "rust")
        self.assertEqual(meta["platform"], "rust")
        self.assertEqual(meta["base_branch"], "main")
        self.assertEqual(meta["build_cmd"], "cargo test")
        self.assertEqual(meta["lint_cmd"], "cargo clippy")
        self.assertIn("cargo test", text)
        self.assertIn("cargo clippy", text)

    def test_auto_detect_go(self):
        root = Path(_root_with("go.mod"))
        text, meta = scaffold.auto_detect_config(root, repo="go-service")
        self.assertEqual(meta["stack"], "go")
        self.assertEqual(meta["platform"], "go")
        self.assertEqual(meta["build_cmd"], "go test ./...")
        self.assertEqual(meta["lint_cmd"], "golangci-lint run")


class TestDefaultConfig(unittest.TestCase):
    def test_every_stack_validates(self):
        for stack in ("flutter", "python", "node", "android", "rust", "go", "java", "generic"):
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
