"""Release-version drift checks for public install guidance."""

from __future__ import annotations

import re
import sys
import tomllib
import unittest
import unittest.mock
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

    def test_two_markers_in_one_file_are_two_markers(self):
        """Keyed by (path, pattern), not by path.

        Under path-only keying the second entry for a file overwrote the first, so
        a file carrying one correct and one stale version reported a single value
        and agreed with itself — the guard passing on precisely the state it
        exists to catch. `VISUAL_EDITS` names two files today; nothing stops it
        naming one file twice.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "keel-visual" / "src" / "keel_visual" / "__init__.py"
            marker.parent.mkdir(parents=True)
            marker.write_text('__version__ = "0.8.0"\nLEGACY_VERSION = "0.6.0"\n', encoding="utf-8")
            relative = "keel-visual/src/keel_visual/__init__.py"
            edits = (
                (relative, re.compile(r'(?m)^(__version__ = ")([^"]+)(")')),
                (relative, re.compile(r'(?m)^(LEGACY_VERSION = ")([^"]+)(")')),
            )

            with unittest.mock.patch.object(release_check, "VISUAL_EDITS", edits):
                problems = release_check.check_visual_markers(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("0.8.0", problems[0])
            self.assertIn("0.6.0", problems[0])

    def test_a_marker_pattern_that_breaks_the_group_convention_is_refused(self):
        """`VISUAL_EDITS` substitutes `\\g<1>{new}\\g<3>`, so group 2 is the version.

        This module reads that same list rather than restating it, which is only
        sound while the convention holds. A two-group pattern added there would
        make the guard compare the wrong substring, or raise mid-release — so it
        is checked, not assumed.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "keel-visual" / "pyproject.toml"
            marker.parent.mkdir(parents=True)
            marker.write_text('version = "0.8.0"\n', encoding="utf-8")
            edits = (("keel-visual/pyproject.toml", re.compile(r'(?m)^version = "([^"]+)"')),)

            with unittest.mock.patch.object(release_check, "VISUAL_EDITS", edits):
                problems = release_check.check_visual_markers(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("1 groups", problems[0])
            self.assertIn("(prefix)(version)(suffix)", problems[0])

    def test_the_real_marker_patterns_keep_the_group_convention(self):
        for relative, pattern in release_check.VISUAL_EDITS:
            with self.subTest(path=relative):
                self.assertEqual(pattern.groups, release_check.VISUAL_PATTERN_GROUPS)

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
        self.document = yaml.safe_load(self.workflow)

    def _verify_script(self) -> str:
        return "\n".join(step.get("run", "") for step in self.document["jobs"]["verify"]["steps"])

    @staticmethod
    def _code(script: str) -> str:
        """The script with comment lines removed.

        The same precaution `test_publish_formula_followup.py` takes: these steps
        explain in prose what they no longer do, and a plain `assertNotIn` matches
        that explanation happily — passing, or failing, on a comment.
        """
        return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))

    def test_the_release_guards_run_before_the_build(self):
        self.assertIn("scripts/release_check.py --tag", self.workflow)
        self.assertLess(
            self.workflow.index("scripts/release_check.py"),
            self.workflow.index("python -m build"),
            "the guards must run before anything is built or uploaded",
        )

    def test_the_build_job_pins_the_timestamp_normalizes_and_proves_it(self):
        """Named for what it checks: the *wiring* of the reproducibility guard.

        It does not assert that the build reproduces — that is a property of the
        pinned toolchain, provable only by running it, and the proof lives where
        it belongs: the build job builds twice on every release and fails if the
        digests differ. Asserting it here would need `python -m build` installed,
        take tens of seconds, and be the one test in this suite that is neither
        hermetic nor offline.

        The first cut of this test was called `test_the_build_is_reproducible`
        while asserting only that `SOURCE_DATE_EPOCH` appeared before
        `python -m build` — and the review that followed showed the build was
        *not* reproducible: with setuptools 84.0.0 the wheel is byte-identical and
        the sdist is not. A test named for a property it does not check is how
        that went unnoticed.

        The mechanism itself is tested in `tests/test_normalize_sdist.py`.
        """
        build = self.document["jobs"]["build-n-publish"]
        steps = [step.get("name", "") for step in build["steps"]]
        script = "\n".join(step.get("run", "") for step in build["steps"])

        self.assertIn("SOURCE_DATE_EPOCH", script)
        self.assertLess(
            script.index("SOURCE_DATE_EPOCH"),
            script.index("python -m build"),
            "the timestamp must be pinned before the build reads it",
        )
        # From the tagged commit, not from the clock: a value that changes per run
        # is not a fix, it is the same bug with more steps.
        self.assertIn("git log -1 --pretty=%ct", script)
        # The epoch is not sufficient — the sdist envelope has to be rewritten too.
        self.assertIn("scripts/normalize_sdist.py dist/*.tar.gz", script)
        # ...and everything downstream must see the normalized archive, so it runs
        # before the SBOM, the checksums, the attestation and both uploads.
        order = {name: index for index, name in enumerate(steps)}
        for later in (
            "Generate CycloneDX SBOM",
            "Generate checksums",
            "Attest build provenance",
            "Publish package to PyPI",
            "Create GitHub Release and Upload Packages",
        ):
            with self.subTest(step=later):
                self.assertLess(order["Normalize the sdist for reproducibility"], order[later])

    def test_the_release_proves_its_own_reproducibility(self):
        """The claim is enforced on the real toolchain, on every release."""
        build = self.document["jobs"]["build-n-publish"]
        verify = next(
            step
            for step in build["steps"]
            if step.get("name") == "Verify the build is reproducible"
        )

        # Build twice, normalize the rebuild the same way, compare both artifacts.
        self.assertIn("python -m build", verify["run"])
        self.assertIn("scripts/normalize_sdist.py", verify["run"])
        self.assertIn("sha256sum", verify["run"])
        self.assertIn("Build is not reproducible", verify["run"])
        # It must actually fail the job. A loop that reports and exits 0 is a
        # comment with extra steps.
        self.assertIn('[ "$failed" -eq 0 ]', verify["run"])
        # And it must compare every artifact, not just the wheel — the wheel was
        # already reproducible; the sdist is what was not.
        self.assertIn("for original in dist/*; do", verify["run"])

    def test_the_release_upload_does_not_replace_published_assets(self):
        """First upload wins on both sides, matching PyPI's `skip-existing`."""
        upload = [
            step
            for step in self.document["jobs"]["build-n-publish"]["steps"]
            if step.get("uses", "").startswith("softprops/action-gh-release@")
        ]

        self.assertEqual(len(upload), 1)
        self.assertIs(upload[0]["with"]["overwrite_files"], False)

    def test_the_verify_job_runs_after_the_publish_job(self):
        verify = self.document["jobs"]["verify"]

        self.assertEqual(verify["needs"], "build-n-publish")
        # Minimal permissions: read the release assets, file the report. Nothing
        # in this job publishes, so nothing here grants `contents: write`.
        self.assertEqual(verify["permissions"], {"contents": "read", "issues": "write"})

    def test_the_primary_digest_comparison_is_against_pypis_own_record(self):
        """PyPI is the source of truth for what was published, not the release.

        `skip-existing: true` means the files PyPI serves cannot be replaced by a
        later run, and `digests.sha256` in its JSON document is what it computed
        over those bytes. The GitHub Release's SHA256SUMS is a second record of
        the same thing, and a rebuilt one can disagree with it.
        """
        script = self._verify_script()

        self.assertIn("https://pypi.org/pypi/keel-workflow/${version}/json", script)
        self.assertIn(".digests.sha256", script)
        self.assertIn("sha256sum", script)

    def test_the_artifacts_are_fetched_without_an_unpinned_build_isolation(self):
        """`pip download --no-binary=:all:` builds the sdist's metadata.

        That pulls an unpinned setuptools into an isolated build env, on a
        release-verification path, to learn a filename the JSON document already
        states. The urls[] entries carry the download URL directly.
        """
        code = self._code(self._verify_script())

        self.assertNotIn("pip download", code)
        self.assertNotIn("--no-binary", code)
        self.assertIn(".urls[]", code)

    def test_both_distributions_must_be_served_before_anything_is_compared(self):
        """A wheel is indexed before the sdist; waiting on "something" is not enough."""
        script = self._verify_script()

        self.assertIn('[.urls[].packagetype] | unique | join(",")', script)
        self.assertIn('= "bdist_wheel,sdist"', script)
        # And the loop must still refuse to pass having compared one artifact.
        self.assertIn('if [ "$checked" -ne 2 ]; then', script)

    def test_the_index_wait_is_bounded_by_a_readable_number(self):
        """Assert the bound, not the loop's spelling."""
        env = self.document["jobs"]["verify"]["env"]
        attempts = int(env["PYPI_WAIT_ATTEMPTS"])
        seconds = int(env["PYPI_WAIT_SECONDS"])

        self.assertGreater(attempts, 1, "one attempt is not a retry")
        self.assertGreaterEqual(
            attempts * seconds, 60, "too short to outlast normal index propagation"
        )
        self.assertLessEqual(
            attempts * seconds, 900, "a job that waits this long reports nothing in time"
        )
        # The numbers are load-bearing rather than decorative: the loop reads them.
        script = self._verify_script()
        self.assertIn('for attempt in $(seq 1 "$PYPI_WAIT_ATTEMPTS"); do', script)
        self.assertIn('sleep "$PYPI_WAIT_SECONDS"', script)

    def test_the_installed_package_must_report_the_tag(self):
        """A wheel built from the wrong commit installs and runs perfectly."""
        script = self._verify_script()

        self.assertIn('if [ "$installed" != "keel ${version}" ]; then', script)
        self.assertIn("Version mismatch", script)

    def test_doctor_is_log_only_and_says_so(self):
        """Advisory by design: doctor warns about adapters that are correctly absent."""
        script = self._verify_script()

        self.assertIn("keel doctor", script)
        self.assertNotIn("doctor --strict", script)
        self.assertIn("log-only", script)

    def test_the_secondary_comparison_tolerates_a_rebuilt_checksum_file(self):
        """A release cut before the two fixes above can carry rebuilt digests.

        That is a defect in the release's bookkeeping, not evidence that the
        package PyPI serves is wrong — PyPI's own digest is checked first. So it
        warns rather than filing a `release-broken` issue against a healthy
        release.
        """
        script = self._verify_script()

        self.assertIn("gh release download", script)
        self.assertIn("SHA256SUMS", script)
        self.assertIn('if [ "$replaced" -eq 1 ]; then', script)
        self.assertIn("::warning title=SHA256SUMS was replaced after its first upload", script)
        # An artifact PyPI serves that the release never listed is still an error.
        self.assertIn("::error title=Not in SHA256SUMS", script)

    def test_the_tolerance_asks_whether_the_asset_was_replaced_not_how_old_it_is(self):
        """The obvious rule would downgrade every genuine mismatch to a warning.

        On the healthy v1.19.3 release PyPI uploaded at 07:28:47 and the GitHub
        Release assets at 07:28:58, because the release is created after the
        publish step in the same run. So "the SHA256SUMS asset is newer than the
        PyPI upload" is true of *every* normal release, and keying the tolerance on
        it would suppress the digest mismatch this job exists to report.

        GitHub bumps an asset's `updatedAt` only when it is replaced; on a first
        upload `createdAt == updatedAt`. That is the exact question, and it needs
        no threshold.
        """
        code = self._code(self._verify_script())

        self.assertIn('select(.name == "SHA256SUMS") | "\\(.createdAt) \\(.updatedAt)"', code)
        self.assertIn('if [ "$sums_updated" -gt "$sums_created" ]; then', code)
        # The rejected rule must not be what actually runs.
        self.assertNotIn("upload_time_iso_8601] | sort", code)

    def test_the_smoke_test_the_runbook_documented_actually_runs(self):
        # `grep -rn release_smoke .github/workflows/` found nothing, which is what
        # #1024 opened on.
        self.assertIn("scripts/release_smoke.py --requirement", self._verify_script())

    def test_a_failed_verify_files_a_deduped_release_broken_issue(self):
        steps = self.document["jobs"]["verify"]["steps"]
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

    def test_the_log_tail_is_masked_before_it_reaches_a_public_issue(self):
        """The constraint is stated in the step that writes the log, and enforced here."""
        steps = self.document["jobs"]["verify"]["steps"]
        failure_run = next(step["run"] for step in steps if step.get("if") == "failure()")
        verify_step = next(step for step in steps if "cross-check" in step.get("name", ""))

        self.assertIn("gh[pousr]|github_pat", failure_run)
        self.assertIn("redacted", failure_run)
        # And the step that writes the log carries the rule it must keep. Read from
        # the raw file: this one lives in a YAML comment above `run:`, which is
        # where a rule about the step as a whole belongs and which the parsed
        # document does not preserve.
        self.assertIn("nothing secret may reach $LOG", self.workflow)
        self.assertIn("cross-check the published digests", verify_step["name"])


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
