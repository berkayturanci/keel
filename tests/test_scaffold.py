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


def _scripted(answers):
    """An ``ask`` seam replaying ``answers`` in order, then accepting every default."""
    remaining = iter(answers)
    return lambda _prompt, default: next(remaining, default)


def _answering(replies):
    """An ``ask`` seam keyed on prompt text: first matching key wins, else the default."""

    def ask(prompt, default):
        for key, value in replies.items():
            if key in prompt:
                return value(prompt, default) if callable(value) else value
        return default

    return ask


class TestWizard(unittest.TestCase):
    def test_sets_window_and_validates(self):
        import yaml

        text = scaffold.wizard(
            "python",
            _scripted(
                ["develop", "y", "Etc/GMT-3", "09:00-18:00", "agent", "pytest", "ruff check ."]
            ),
            repo="demo",
        )
        config = cfg.parse_config(yaml.safe_load(text))
        self.assertEqual(config.base_branch, "develop")
        self.assertEqual(config.timezone, "Etc/GMT-3")
        self.assertEqual(config.merge_window, "09:00-18:00")
        self.assertEqual(config.consent_mode, "agent")
        self.assertEqual(config.knobs.build_gate_cmd, "pytest")
        self.assertIn("--wizard", text)

    def test_blank_answers_skip_optional(self):
        import yaml

        text = scaffold.wizard(
            "generic",
            _answering({"Configure a merge window": "n", "Lint": ""}),
            repo="demo",
        )
        config = cfg.parse_config(yaml.safe_load(text))
        self.assertIsNone(config.timezone)
        self.assertIsNone(config.merge_window)
        self.assertEqual(config.gates, ("build",))

    def test_invalid_consent_mode_rejected(self):
        with self.assertRaises(ValueError):
            scaffold.wizard("python", _answering({"Consent mode": "maybe"}), repo="demo")


class TestWizardMergeWindowPair(unittest.TestCase):
    """`timezone` + `merge_window` are one all-or-nothing decision in the wizard (#1082).

    They are all-or-nothing in `parse_config` since #1076, so the property under test is
    the same on every path: whatever the operator answers, the scaffolded config parses.
    """

    def _run(self, replies):
        import yaml

        warnings = []
        text = scaffold.wizard("python", _answering(replies), repo="demo", notify=warnings.append)
        return cfg.parse_config(yaml.safe_load(text)), warnings

    def test_yes_with_valid_values_writes_both(self):
        config, warnings = self._run(
            {
                "Configure a merge window": "y",
                "Timezone": "Etc/GMT-3",
                "Merge window": "09:00-18:00",
            }
        )
        self.assertEqual(config.timezone, "Etc/GMT-3")
        self.assertEqual(config.merge_window, "09:00-18:00")
        self.assertEqual(warnings, [])

    def test_blank_yes_keeps_the_defaults(self):
        # Enter through the wizard still scaffolds the night no-merge window it always did.
        config, _ = self._run({"Lint": ""})
        self.assertEqual(config.timezone, "Europe/Istanbul")
        self.assertEqual(config.merge_window, "07:00-01:30")

    def test_no_writes_neither_key(self):
        config, warnings = self._run({"Configure a merge window": "no"})
        self.assertIsNone(config.timezone)
        self.assertIsNone(config.merge_window)
        self.assertEqual(warnings, [])

    def test_unparsable_answer_to_the_gate_means_no(self):
        config, _ = self._run({"Configure a merge window": "later"})
        self.assertIsNone(config.timezone)
        self.assertIsNone(config.merge_window)

    def test_bad_timezone_is_reported_then_asked_again(self):
        config, warnings = self._run(
            {
                "Configure a merge window": "y",
                "Timezone": _scripted(["Definitely/Nowhere", "Etc/GMT-3"]),
                "Merge window": "09:00-18:00",
            }
        )
        self.assertEqual(config.timezone, "Etc/GMT-3")
        self.assertEqual(config.merge_window, "09:00-18:00")
        # The wording is the validator's own, so prompt and ConfigError cannot drift.
        self.assertEqual(warnings, [cfg.timezone_issue("Definitely/Nowhere")])

    def test_bad_window_is_reported_then_asked_again(self):
        config, warnings = self._run(
            {
                "Configure a merge window": "y",
                "Timezone": "Etc/GMT-3",
                "Merge window": _scripted(["29:00-01:00", "09:00-18:00"]),
            }
        )
        self.assertEqual(config.merge_window, "09:00-18:00")
        self.assertEqual(warnings, [cfg.merge_window_issue("29:00-01:00")])

    def test_timezone_never_answered_drops_the_pair(self):
        config, warnings = self._run(
            {"Configure a merge window": "y", "Timezone": "Definitely/Nowhere"}
        )
        self.assertIsNone(config.timezone)
        self.assertIsNone(config.merge_window)
        self.assertIn("all-or-nothing", warnings[-1])

    def test_window_never_answered_drops_the_zone_too(self):
        config, warnings = self._run(
            {
                "Configure a merge window": "y",
                "Timezone": "Etc/GMT-3",
                "Merge window": "29:00-01:00",
            }
        )
        self.assertIsNone(config.timezone)
        self.assertIsNone(config.merge_window)
        self.assertIn("all-or-nothing", warnings[-1])

    def test_a_wizard_with_no_notify_still_survives_a_bad_answer(self):
        import yaml

        text = scaffold.wizard(
            "python",
            _answering({"Timezone": _scripted(["Definitely/Nowhere", "Etc/GMT-3"])}),
            repo="demo",
        )
        self.assertEqual(cfg.parse_config(yaml.safe_load(text)).timezone, "Etc/GMT-3")


class TestRenderConfig(unittest.TestCase):
    def test_minimal_validates(self):
        import yaml

        text = scaffold.render_config(repo="x")
        self.assertEqual(cfg.parse_config(yaml.safe_load(text)).repo, "x")
        self.assertEqual(cfg.parse_config(yaml.safe_load(text)).consent_mode, "explicit")

    def test_invalid_consent_mode_rejected(self):
        with self.assertRaises(ValueError):
            scaffold.render_config(repo="x", consent_mode="maybe")

    def test_half_a_merge_window_pair_is_refused(self):
        # #1076 made the pair all-or-nothing in parse_config; the scaffolder must not be
        # able to write a file that validation refuses, from the wizard or anywhere else.
        for kwargs in ({"timezone": "Etc/GMT-3"}, {"merge_window": "09:00-18:00"}):
            with self.subTest(**kwargs), self.assertRaises(ValueError) as caught:
                scaffold.render_config(repo="x", **kwargs)
            self.assertIn("all-or-nothing", str(caught.exception))

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
