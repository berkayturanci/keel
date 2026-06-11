"""Release-version drift checks for public install guidance."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from keel import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONED_GIT_INSTALL_RE = re.compile(
    r'pip install "git\+https://github\.com/berkayturanci/keel@(?P<tag>v[0-9]+\.[0-9]+\.[0-9]+)"'
)


class TestReleaseDocs(unittest.TestCase):
    def test_python_package_and_project_metadata_versions_match(self):
        metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["version"], __version__)

    def test_public_git_install_examples_match_current_release_tag(self):
        expected_tag = f"v{__version__}"
        paths = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "website/index.html",
            REPO_ROOT / ".github/workflows/keel-ship.yml",
        ]

        for path in paths:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                text = path.read_text(encoding="utf-8")
                tags = [match.group("tag") for match in VERSIONED_GIT_INSTALL_RE.finditer(text)]
                self.assertTrue(tags, f"expected at least one versioned git install in {path}")
                self.assertEqual(tags, [expected_tag])

    def test_public_v1_surfaces_do_not_carry_stale_roadmap_claims(self):
        public_paths = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "website/index.html",
            REPO_ROOT / "docs/keel/comparison.md",
            REPO_ROOT / "docs/keel/github-actions.md",
            REPO_ROOT / "docs/keel/release.md",
        ]
        stale_phrases = [
            "latest production PyPI release before the `1.0.0` tag",
            "`keel-workflow==0.9.0`",
            "First-class durable post-merge learning capture is planned",
            "capture hooks today, durable learning capture planned",
            "planned learning capture",
            "folded into <code>/keel:ship</code> as extensions",
            "once `keel ship --execute` lands",
        ]

        for path in public_paths:
            text = path.read_text(encoding="utf-8")
            for phrase in stale_phrases:
                with self.subTest(path=str(path.relative_to(REPO_ROOT)), phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_keel_ship_workflow_quotes_dispatch_arguments(self):
        text = (REPO_ROOT / ".github/workflows/keel-ship.yml").read_text(encoding="utf-8")

        self.assertIn("ARGS=(.keel/project.yaml --root .)", text)
        self.assertIn('ARGS+=(--pr "$PR")', text)
        self.assertIn('keel ship "${ARGS[@]}" | tee ship.txt', text)
        self.assertNotIn("keel ship $ARGS", text)

    def test_publish_workflow_uses_hash_locked_release_tools(self):
        workflow = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        lockfile = (
            REPO_ROOT / ".github/requirements/publish-tools.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("--require-hashes", workflow)
        self.assertIn("-r .github/requirements/publish-tools.txt", workflow)
        self.assertNotIn("python -m pip install build", workflow)
        self.assertNotIn("python -m pip install cyclonedx-bom", workflow)
        self.assertIn("build==1.3.0 \\", lockfile)
        self.assertIn("cyclonedx-bom==7.2.1 \\", lockfile)
        self.assertIn("PyYAML==6.0.3 \\", lockfile)
        self.assertIn("setuptools==80.9.0 \\", lockfile)
        self.assertIn("python -m build --no-isolation", workflow)
        self.assertIn("python -m pip install --no-deps dist/*.whl", workflow)
        self.assertNotIn("python -m pip install dist/*.whl", workflow)
        self.assertGreaterEqual(lockfile.count("--hash=sha256:"), 31)


if __name__ == "__main__":
    unittest.main()
