"""Release-version drift checks for public install guidance."""

from __future__ import annotations

import re
import sys
import tomllib
import unittest
from pathlib import Path

from keel import __version__

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_bump  # noqa: E402  (path-inserted maintenance script)

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

    def test_no_site_surface_carries_a_stale_version_string(self):
        # `release_bump` used to find the *current* version literally, so a file that had
        # already fallen behind contained neither the old nor the new string, was silently
        # skipped, and stayed behind forever — docs.html, coverage.html and content.js sat
        # at v1.6.5 for four releases, then v1.8.2 for three more, each time relying on a
        # runbook line asking a human to catch it. The script now matches by shape; this
        # is what tells us if that stops working.
        expected = f"v{__version__}"
        # Derived from the script's own table rather than restated here: a fifth surface
        # added to `_SITE_PATTERNS` and forgotten in a hand-written list would be exactly
        # the unguarded file this test exists to prevent.
        for rel, pattern, _template in release_bump._SITE_PATTERNS:
            with self.subTest(path=rel, pattern=pattern):
                path = REPO_ROOT / rel
                self.assertTrue(path.exists(), f"{rel} is in _SITE_PATTERNS but missing")
                found = re.findall(pattern, path.read_text(encoding="utf-8"))
                self.assertTrue(found, f"pattern matched nothing in {rel}")
                # Some patterns match the surrounding token (`keel@v1.2.3`); compare on
                # the version itself.
                versions = {match.rsplit("@", 1)[-1] for match in found}
                self.assertEqual(versions, {expected})
        # Deliberately per-token rather than a blanket file scan: release-note prose
        # legitimately names older lines ("v1.2.1 line"), and a blanket scan would either
        # fail on that or have to be weakened until it caught nothing.

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

    def test_homebrew_formula_matches_the_project(self):
        """The formula drifted seven releases and misstated the licence (#774).

        None of it was caught, because nothing compared the formula to the project
        it installs. A placeholder checksum only fails for whoever runs
        `brew install`; a wrong licence never fails at all.
        """
        formula = (REPO_ROOT / "Formula" / "keel.rb").read_text(encoding="utf-8")

        self.assertIn(f"/tags/v{__version__}.tar.gz", formula,
                      "formula url must point at the current release tag")
        digest = re.search(r'sha256 "([0-9a-f]{64})"', formula)
        self.assertIsNotNone(digest, "formula must carry a sha256")
        self.assertNotEqual(digest.group(1), "0" * 64,
                            "formula sha256 is still the placeholder; brew would refuse it")
        # Read the licence from the project rather than hard-coding it here, so the
        # test cannot drift into asserting the wrong thing either.
        declared = re.search(r'license = "([^"]+)"',
                             (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIsNotNone(declared)
        self.assertIn(f'license "{declared.group(1)}"', formula,
                      "formula licence must match pyproject")

    def test_publish_workflow_uses_hash_locked_release_tools(self):
        workflow = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        lockfile = (
            REPO_ROOT / ".github/requirements/publish-tools.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("--require-hashes", workflow)
        self.assertIn("-r .github/requirements/publish-tools.txt", workflow)
        self.assertNotIn("python -m pip install build", workflow)
        self.assertNotIn("python -m pip install cyclonedx-bom", workflow)
        # Pinned + hashed, but deliberately not pinned to a *specific* version here:
        # Dependabot watches this file (#664), so asserting the exact version would turn
        # every routine bump red and train people to edit the test to make a security
        # update pass. What must hold is that each tool is present, exactly pinned with
        # ==, and carries at least one sha256.
        #
        # "At least one" is not slack: Dependabot writes a hash per artifact — a wheel
        # and an sdist — as continuation lines, so requiring exactly one rejected the
        # first real bump it produced (build 1.3.0 -> 1.5.0) and reintroduced the very
        # breakage this assertion was written to prevent.
        for tool in ("build", "cyclonedx-bom", "PyYAML", "setuptools"):
            with self.subTest(tool=tool):
                pinned = re.search(
                    rf"^{re.escape(tool)}==\S+"
                    rf"(?: \\\n    --hash=sha256:[0-9a-f]{{64}})+$",
                    lockfile,
                    re.MULTILINE,
                )
                self.assertIsNotNone(pinned, f"{tool} must be == pinned with a sha256")
        self.assertIn("python -m build --no-isolation", workflow)
        self.assertIn("python -m pip install --no-deps dist/*.whl", workflow)
        self.assertNotIn("python -m pip install dist/*.whl", workflow)
        self.assertGreaterEqual(lockfile.count("--hash=sha256:"), 31)


if __name__ == "__main__":
    unittest.main()
