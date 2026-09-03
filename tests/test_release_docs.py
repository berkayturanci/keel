"""Release-version drift checks for public install guidance."""

from __future__ import annotations

import re
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from keel import __version__

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_check  # noqa: E402  (path-inserted maintenance script)
import release_surfaces  # noqa: E402

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

    def test_no_release_surface_carries_a_stale_version_string(self):
        # `release_bump` used to find the *current* version literally, so a file that had
        # already fallen behind contained neither the old nor the new string, was silently
        # skipped, and stayed behind forever — docs.html, coverage.html and content.js sat
        # at v1.6.5 for four releases, then v1.8.2 for three more, each time relying on a
        # runbook line asking a human to catch it.
        #
        # Delegated to `release_check.check_surfaces` rather than restated here: that is
        # the function `make release-check` and publish.yml's build job run before
        # anything is uploaded (#1024), and a second implementation of the same rule is
        # the thing that would drift. What this adds is that it runs on every pull
        # request, not only at tag time.
        result = release_check.check_surfaces(REPO_ROOT)

        self.assertEqual(result.problems, [])
        # Deliberately per-token rather than a blanket file scan: release-note prose
        # legitimately names older lines ("v1.2.1 line"), and a blanket scan would either
        # fail on that or have to be weakened until it caught nothing.

    def test_the_release_surface_table_still_covers_the_site(self):
        """Vacuity: an emptied table would make the check above pass on anything."""
        covered = {surface.path for surface in release_surfaces.RELEASE_SURFACES}

        for expected in (
            "pyproject.toml",
            "src/keel/__init__.py",
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
            "README.md",
            "website/index.html",
            "website/docs.html",
            "website/coverage.html",
            "website/content.js",
        ):
            with self.subTest(path=expected):
                self.assertIn(expected, covered)

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

        self.assertIn(
            f"/tags/v{__version__}.tar.gz",
            formula,
            "formula url must point at the current release tag",
        )
        digest = re.search(r'sha256 "([0-9a-f]{64})"', formula)
        self.assertIsNotNone(digest, "formula must carry a sha256")
        self.assertNotEqual(
            digest.group(1),
            "0" * 64,
            "formula sha256 is still the placeholder; brew would refuse it",
        )
        # Read the licence from the project rather than hard-coding it here, so the
        # test cannot drift into asserting the wrong thing either.
        declared = re.search(
            r'license = "([^"]+)"', (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertIsNotNone(declared)
        self.assertIn(
            f'license "{declared.group(1)}"', formula, "formula licence must match pyproject"
        )

    def test_keel_visual_version_markers_agree(self):
        """keel-visual shipped `__version__ = "0.6.0"` as 0.8.0 (#796).

        Core has never drifted because `metadata["project"]["version"] ==
        __version__` is asserted above and `release_bump.py` rewrites both. This
        file had neither, so the second package fell two releases behind on a
        value that is public API — `keel_visual.__version__` is importable, and
        it is what a bug report would quote.

        The rule itself lives in `release_check.check_visual_markers`, which reads
        the file list from `release_bump.VISUAL_EDITS` — the list the bumper
        rewrites. One implementation, run here on every pull request and again in
        publish.yml before a tag builds anything (#1024): the guard used to run
        only in PR CI, which is not the phase the drift shipped in.

        Read from the files rather than imported, so the assertion holds whether
        or not keel-visual is installed in the environment running the suite.
        """
        result = release_check.check_visual_markers(REPO_ROOT)

        self.assertEqual(result.problems, [])

    def test_homebrew_formula_vendors_every_runtime_dependency(self):
        """The formula installed a keel that could not start (#787).

        `test_homebrew_formula_matches_the_project` above compares the formula's
        *identity* to the project — tag, checksum, licence — and all three agreed
        while `brew install` produced a virtualenv that died on `import yaml`
        before printing anything.

        Homebrew's `std_pip_args` is `--no-deps --no-binary=:all:`, not
        negotiable, so pip never resolves dependencies from PyPI: each one has to
        be vendored as a `resource` stanza. That is the invariant here, and it is
        checkable offline — no network, no `brew` binary.
        """
        formula = (REPO_ROOT / "Formula" / "keel.rb").read_text(encoding="utf-8")
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        # Homebrew runs on macOS and Linux only, so a Windows-marked dependency
        # (tzdata) is correctly absent. Anything unconditional must be vendored.
        required = {
            re.match(r"[A-Za-z0-9._-]+", spec).group(0).lower()
            for spec in project["project"]["dependencies"]
            if "sys_platform" not in spec
        }
        self.assertTrue(required, "no unconditional runtime dependency found to check")

        vendored = {name.lower() for name in re.findall(r'resource "([^"]+)"', formula)}
        self.assertEqual(
            set(),
            required - vendored,
            "every runtime dependency must be a resource; brew installs with --no-deps",
        )

        # A resource without a pinned sdist is as broken as a missing one: brew
        # builds with --no-binary=:all:, so a wheel url would fail the build.
        for block in re.findall(r'resource "[^"]+" do(.*?)\n  end', formula, re.S):
            with self.subTest(block=block.strip()[:60]):
                self.assertRegex(block, r'url "https://\S+\.tar\.gz"')
                self.assertRegex(block, r'sha256 "[0-9a-f]{64}"')

    def test_publish_workflow_uses_hash_locked_release_tools(self):
        workflow = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        lockfile = (REPO_ROOT / ".github/requirements/publish-tools.txt").read_text(
            encoding="utf-8"
        )

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


def _lockstep_fixture(root: Path, declared: str, changelog: str, package: str | None = None):
    """A minimal tree carrying only what the lockstep guards read."""
    (root / "src" / "keel").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "keel-workflow"\nversion = "{declared}"\n', encoding="utf-8"
    )
    (root / "src" / "keel" / "__init__.py").write_text(
        f'"""keel."""\n\n__version__ = "{package or declared}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


def _visual_fixture(root: Path, pyproject: str, marker: str) -> None:
    (root / "keel-visual" / "src" / "keel_visual").mkdir(parents=True)
    (root / "keel-visual" / "pyproject.toml").write_text(
        f'[project]\nname = "keel-visual"\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    (root / "keel-visual" / "src" / "keel_visual" / "__init__.py").write_text(
        f'"""keel-visual."""\n\n__version__ = "{marker}"\n', encoding="utf-8"
    )


class TestTheReleaseGuardsRunBeforeAnythingIsUploaded(unittest.TestCase):
    """#1024: every check below existed as prose, or as a test that ran too late.

    `scripts/release_smoke.py` was documented in the runbook and wired into no
    workflow. "Confirm the PyPI digests match the GitHub Release" was a sentence.
    Nothing compared the CHANGELOG's top section to `__version__` — #979 was
    caught by a review, not by CI. And the keel-visual marker guard ran on pull
    requests only, which is not the phase its drift shipped in.
    """

    def test_the_changelog_top_released_section_names_the_declared_version(self):
        self.assertEqual(release_check.check_changelog(REPO_ROOT).problems, [])

    def test_the_declared_version_is_in_lockstep(self):
        self.assertEqual(release_check.check_declared_version(REPO_ROOT).problems, [])

    def test_a_changelog_never_renamed_from_unreleased_is_refused(self):
        """The acceptance criterion, stated as a fixture.

        The tree declares 1.20.0, the notes for it are still under
        `## [Unreleased]`, and the top *released* section is therefore the
        previous release. Tagging this publishes notes headed "Unreleased" to
        PyPI, where they cannot be corrected.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lockstep_fixture(
                root,
                "1.20.0",
                "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- a thing\n\n"
                "## [1.19.3] - 2026-09-02\n\n### Added\n- an earlier thing\n",
            )

            problems = release_check.check_changelog(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("1.19.3", problems[0])
            self.assertIn("1.20.0", problems[0])
            self.assertIn("Unreleased", problems[0])

    def test_a_correctly_promoted_changelog_passes(self):
        """The other direction: an empty `## [Unreleased]` above the cut is fine.

        That is the state a release leaves behind, and a guard that failed on it
        would fire on the one behaviour it exists to protect.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lockstep_fixture(
                root,
                "1.20.0",
                "# Changelog\n\n## [Unreleased]\n\n## [1.20.0] - 2026-09-03\n\n"
                "### Fixed\n- a thing\n\n## [1.19.3] - 2026-09-02\n",
            )

            self.assertEqual(release_check.check_changelog(root).problems, [])

    def test_a_changelog_with_no_released_section_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lockstep_fixture(root, "1.20.0", "# Changelog\n\n## [Unreleased]\n\n- a thing\n")

            problems = release_check.check_changelog(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("no released section", problems[0])

    def test_a_changelog_with_no_headings_at_all_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lockstep_fixture(root, "1.20.0", "# Changelog\n\nnothing structured here\n")

            problems = release_check.check_changelog(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("no `## [...]` section headings", problems[0])

    def test_a_version_declared_twice_and_bumped_once_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lockstep_fixture(
                root, "1.20.0", "# Changelog\n\n## [1.20.0] - 2026-09-03\n", package="1.19.3"
            )

            problems = release_check.check_declared_version(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("1.20.0", problems[0])
            self.assertIn("1.19.3", problems[0])
            self.assertIn("release-bump", problems[0])

    def test_diverged_keel_visual_markers_are_refused(self):
        """#796, as a fixture: `__version__` two releases behind its pyproject."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _visual_fixture(root, "0.8.0", "0.6.0")

            problems = release_check.check_visual_markers(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("0.8.0", problems[0])
            self.assertIn("0.6.0", problems[0])
            self.assertIn("--package keel-visual", problems[0])

    def test_agreeing_keel_visual_markers_pass_on_their_own_version_line(self):
        """keel-visual is not compared to core: it has its own version line."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _visual_fixture(root, "0.8.0", "0.8.0")

            self.assertEqual(release_check.check_visual_markers(root).problems, [])

    def test_a_missing_keel_visual_marker_is_a_failure_not_a_skip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _visual_fixture(root, "0.8.0", "0.8.0")
            (root / "keel-visual" / "src" / "keel_visual" / "__init__.py").write_text(
                '"""keel-visual."""\n', encoding="utf-8"
            )

            problems = release_check.check_visual_markers(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("carries no version marker", problems[0])

    def test_the_tag_must_name_the_declared_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _lockstep_fixture(root, "1.20.0", "# Changelog\n\n## [1.20.0] - 2026-09-03\n")

            self.assertEqual(release_check.check_tag(root, "v1.20.0").problems, [])
            self.assertEqual(len(release_check.check_tag(root, "v1.19.3").problems), 1)
            self.assertEqual(len(release_check.check_tag(root, "1.20.0").problems), 1)

    def test_run_checks_adds_the_tag_guard_only_when_a_tag_is_given(self):
        names = [check.name for check in release_check.run_checks(REPO_ROOT)]
        with_tag = [check.name for check in release_check.run_checks(REPO_ROOT, f"v{__version__}")]

        self.assertNotIn("tag", names)
        self.assertEqual(with_tag, [*names, "tag"])

    def test_the_whole_tree_passes_its_own_release_check(self):
        failures = {
            check.name: check.problems
            for check in release_check.run_checks(REPO_ROOT, f"v{__version__}")
            if not check.ok
        }

        self.assertEqual(failures, {})


class TestThePublishWorkflowRunsTheGuards(unittest.TestCase):
    """A guard nothing invokes is prose. These assert the wiring, not the logic."""

    def setUp(self):
        self.workflow = (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")

    def test_the_release_guards_run_before_the_build(self):
        self.assertIn("scripts/release_check.py --tag", self.workflow)
        self.assertLess(
            self.workflow.index("scripts/release_check.py"),
            self.workflow.index("python -m build"),
            "the guards must run before anything is built or uploaded",
        )

    def test_the_verify_job_runs_after_the_publish_job(self):
        document = yaml.safe_load(self.workflow)
        verify = document["jobs"]["verify"]

        self.assertEqual(verify["needs"], "build-n-publish")
        # Minimal permissions: read the release assets, file the report. Nothing
        # in this job publishes, so nothing here grants `contents: write`.
        self.assertEqual(verify["permissions"], {"contents": "read", "issues": "write"})

    def test_the_verify_job_does_every_check_the_runbook_promises(self):
        steps = "\n".join(
            step.get("run", "") for step in yaml.safe_load(self.workflow)["jobs"]["verify"]["steps"]
        )

        # The smoke test that was documented for several releases while no
        # workflow ran it — `grep -rn release_smoke .github/workflows/` found
        # nothing, which is what #1024 opened on.
        self.assertIn("scripts/release_smoke.py --requirement", steps)
        self.assertIn("keel version", steps)
        self.assertIn("keel doctor", steps)
        self.assertIn("pip download --no-deps", steps)
        self.assertIn("--no-binary=:all:", steps)  # the sdist; pip prefers the wheel
        self.assertIn("gh release download", steps)
        self.assertIn("SHA256SUMS", steps)
        self.assertIn("sha256sum", steps)
        # Bounded, not open-ended: a job that waits forever reports nothing.
        self.assertIn("for attempt in $(seq 1 20); do", steps)

    def test_a_failed_verify_files_a_deduped_release_broken_issue(self):
        steps = yaml.safe_load(self.workflow)["jobs"]["verify"]["steps"]
        failure_steps = [step for step in steps if step.get("if") == "failure()"]

        self.assertEqual(len(failure_steps), 1)
        run = failure_steps[0]["run"]
        self.assertIn('title="release-broken: ${TAG}"', run)
        # Deduped: a re-run comments on the open report rather than filing a second.
        self.assertIn("gh issue list", run)
        self.assertIn("gh issue comment", run)
        self.assertIn("gh issue create", run)
        # The log tail is the whole point — an issue saying only "it failed"
        # sends the reader back to the run it was supposed to replace.
        self.assertIn('tail -n 100 "$LOG"', run)


class TestTheRunbookCannotGoStale(unittest.TestCase):
    def setUp(self):
        self.runbook = (REPO_ROOT / "docs/keel/release.md").read_text(encoding="utf-8")

    def test_the_runbook_names_no_current_release_version(self):
        """`docs/keel/release.md` said 1.19.0 while the tree was at 1.19.3.

        The line was hand-maintained and enforced by nothing, so it was wrong
        more often than right — at `1.8.2` for three releases, then `1.19.0` for
        three more. It is gone; what replaces it is the command that answers the
        question. A pinned `keel-workflow==x.y.z` anywhere in this file would be
        the same claim in a new place.
        """
        pinned = re.findall(r"keel-workflow==\d+\.\d+\.\d+", self.runbook)

        self.assertEqual(pinned, [], "the runbook must not pin a release version")

    def test_the_runbook_says_how_to_read_the_real_state(self):
        self.assertIn("python -m pip index versions keel-workflow", self.runbook)
        self.assertIn("make release-check", self.runbook)

    def test_the_runbook_documents_the_post_publish_verification(self):
        self.assertIn("release-broken", self.runbook)
        self.assertIn("scripts/release_smoke.py", self.runbook)


class TestContributingDocumentsTheGuards(unittest.TestCase):
    def test_contributing_points_at_the_release_check(self):
        text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

        self.assertIn("make release-check", text)
        self.assertIn("scripts/release_surfaces.py", text)


if __name__ == "__main__":
    unittest.main()
