"""The bump and the release check must read one table (#1024).

Before this, "every place a release must touch" was a list inside
``release_bump.py`` that only the bumper could read. Nothing asserted the same
list at tag time, so a surface could be registered for the rewrite and guarded by
nothing — or, as happened for seven releases, guarded by a runbook sentence asking
a human to look.

The table now lives in ``scripts/release_surfaces.py`` and both directions
project from it. These tests pin that projection, and then prove the round trip
end to end on a fixture tree: bump to a new version, and the check that runs in
``publish.yml`` before anything is uploaded must find nothing to complain about.
A surface reachable by one and not the other fails here.

``scripts/`` is maintenance tooling outside the coverage gate, so these tests are
what hold it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_surfaces = _load("release_surfaces")
release_bump = _load("release_bump")
release_check = _load("release_check")

OLD = "1.2.3"
NEW = "4.5.6"

#: One fixture file per surface path, carrying the token *in the context its
#: pattern needs* — two of them match behind a lookbehind, which a generically
#: generated fixture would silently fail to exercise.
#:
#: Deliberately hand-written and then checked for completeness below: a surface
#: added to the table with no fixture line fails
#: ``test_every_surface_is_exercised_by_the_fixture`` rather than quietly going
#: untested, which is the exact failure mode this whole table exists to remove.
FIXTURE: dict[str, str] = {
    "pyproject.toml": '[project]\nname = "keel-workflow"\nversion = "{v}"\n',
    "src/keel/__init__.py": '"""keel."""\n\n__version__ = "{v}"\n',
    ".claude-plugin/plugin.json": '{{\n  "name": "keel",\n  "version": "{v}"\n}}\n',
    ".codex-plugin/plugin.json": '{{\n  "name": "keel",\n  "version": "{v}"\n}}\n',
    ".cursor-plugin/plugin.json": '{{\n  "name": "keel",\n  "version": "{v}"\n}}\n',
    # The historical prose must survive the bump; the pinned install must not.
    "README.md": (
        'pip install "git+https://github.com/berkayturanci/keel@v{v}"\n'
        "Since **1.6.5**, the board stamps merged.\n"
    ),
    ".github/workflows/keel-ship.yml": (
        '# (`pip install "git+https://github.com/berkayturanci/keel@v{v}"`)\n'
    ),
    "docs/keel/cutover.md": (
        'Pin the cutover: `pip install "git+https://github.com/berkayturanci/keel@v{v}"`\n'
    ),
    "website/index.html": (
        '<span class="ver" data-version>v{v}</span>\n'
        '<code>pip install "git+https://github.com/berkayturanci/keel@v{v}"</code>\n'
    ),
    "website/docs.html": '<span class="ver" data-version>v{v}</span>\n',
    "website/coverage.html": '<span class="ver" data-version>v{v}</span>\n',
    "website/content.js": (
        "export const content = {{\n"
        '  hero: {{ version: "v{v}",\n'
        '    installAlt: "pip install \\"git+https://github.com/berkayturanci/keel@v{v}\\"" }},\n'
        "}};\n"
    ),
}


def _write_fixture(root: Path, version: str) -> None:
    for relative, template in FIXTURE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template.format(v=version), encoding="utf-8")


class TestTheTableIsTheSingleSource(unittest.TestCase):
    def test_there_are_surfaces_to_check(self):
        """Vacuity: an empty table would pass every assertion below."""
        self.assertGreater(len(release_surfaces.RELEASE_SURFACES), 5)

    def test_the_bumps_two_projections_partition_the_table(self):
        """Every surface is rewritten exactly one way, and none is orphaned."""
        literal = {surface.path for surface in release_surfaces.literal_surfaces()}
        shape = {surface.path for surface in release_surfaces.shape_surfaces()}
        every = {surface.path for surface in release_surfaces.RELEASE_SURFACES}

        self.assertEqual(every, literal | shape)
        self.assertEqual(
            len(release_surfaces.literal_surfaces()) + len(release_surfaces.shape_surfaces()),
            len(release_surfaces.RELEASE_SURFACES),
        )

    def test_the_bumps_literal_edits_come_from_the_table(self):
        edits = release_bump._edits(OLD, NEW)
        expected = [
            (surface.path, surface.token.format(version=OLD), surface.token.format(version=NEW))
            for surface in release_surfaces.literal_surfaces()
        ]

        self.assertEqual(edits, expected)

    def test_the_bumps_site_patterns_come_from_the_table(self):
        expected = tuple(
            (surface.path, surface.pattern, surface.token.replace("{version}", "{new}"))
            for surface in release_surfaces.shape_surfaces()
        )

        self.assertEqual(release_bump._SITE_PATTERNS, expected)

    def test_every_surface_is_exercised_by_the_fixture(self):
        missing = sorted(
            {surface.path for surface in release_surfaces.RELEASE_SURFACES} - set(FIXTURE)
        )
        self.assertEqual(
            missing,
            [],
            "a release surface with no fixture file is untested by the round trip below",
        )

    def test_every_surfaces_pattern_finds_its_own_token(self):
        """The check reads ``pattern``; the bump writes ``token``. They must agree."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root, OLD)
            for surface in release_surfaces.RELEASE_SURFACES:
                with self.subTest(path=surface.path, pattern=surface.pattern):
                    text = (root / surface.path).read_text(encoding="utf-8")
                    found = release_surfaces.versions_in(surface, text)
                    self.assertTrue(found, f"{surface.pattern} matched nothing in {surface.path}")
                    self.assertEqual(set(found), {OLD})


class TheCursorManifestMatchesTheSchemaItTargets(unittest.TestCase):
    """A listing manifest is only listing metadata if it uses the host's fields (#1139).

    The sibling change in ai-jury shipped `displayName` and no `logo`, and said in three
    places that `displayName` was what stopped the plugin rendering as a bare repository
    slug. It is not a Cursor field. Cursor's plugin reference documents the optional set as
    `description`, `version`, `author`, `homepage`, `repository`, `license`, `keywords`,
    `logo`, `rules`, `agents`, `skills`, `commands`, `hooks`, `mcpServers`, `variables` —
    and `logo` is the listing asset: *"Relative path to a logo file in the repo … Relative
    paths resolve to raw.githubusercontent.com URLs."* A gate reviewer read the page and
    the diff and found the claim was in neither.

    The GUI listing cannot be watched from a CLI. What can be asserted is that the keys
    shipped are keys the host documents, that the logo is a file this repository has, and
    that the manifest really mirrors the Claude one it says it mirrors — including the
    `skills` path, which keeps it inside #1137's one-root-`skills/` rule.
    """

    MANIFEST = ".cursor-plugin/plugin.json"
    #: https://cursor.com/docs/reference/plugins — "Required fields" / "Optional fields".
    REQUIRED = {"name"}
    OPTIONAL = {
        "description",
        "version",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "logo",
        "rules",
        "agents",
        "skills",
        "commands",
        "hooks",
        "mcpServers",
        "variables",
    }

    def setUp(self):
        self.root = Path(release_surfaces.__file__).resolve().parents[1]
        self.manifest = json.loads((self.root / self.MANIFEST).read_text(encoding="utf-8"))

    def test_every_key_is_one_the_host_documents(self):
        unknown = sorted(set(self.manifest) - self.REQUIRED - self.OPTIONAL)

        self.assertEqual(
            [], unknown, f"not Cursor plugin manifest fields, so the host ignores them: {unknown}"
        )

    def test_the_listing_asset_is_the_one_the_host_reads(self):
        logo = self.manifest.get("logo")

        self.assertIsInstance(logo, str, "no logo, so the listing has no image to show")
        self.assertFalse(logo.startswith(("http://", "https://", "/")), logo)
        self.assertTrue((self.root / logo).is_file(), f"{logo} is not in this repository")

    def test_it_mirrors_the_claude_manifest_it_claims_to(self):
        claude = json.loads(
            (self.root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        missing = sorted(set(claude) - set(self.manifest))

        self.assertEqual(
            [], missing, f"the Claude manifest declares these and this does not: {missing}"
        )

    def test_and_names_the_same_skills_directory(self):
        """#1137: one root `skills/` for every agent, named by every manifest."""
        claude = json.loads(
            (self.root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )

        self.assertEqual(self.manifest["skills"], claude["skills"])


class EveryPluginManifestIsARegisteredSurface(unittest.TestCase):
    """A manifest that names a version and is not in the table goes stale silently (#1139).

    keel ships `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`, and both are
    registered. The ecosystem repositories keel is measured against ship eight to twelve —
    `.cursor-plugin/`, `.agy/`, `.kimi-plugin/`, `.grok-plugin/`, … — and each is one small
    JSON file, which is exactly the shape of thing that gets added without being wired into
    the release.

    The failure would be invisible for one release and then permanent: the new manifest
    keeps the version it was born with while everything else moves, and `agy plugin list`
    or the Cursor marketplace reports a keel that has not existed for months. `keel doctor`
    does not check it, and nothing else reads these files.

    So the rule is discovery-based rather than a second list: whatever manifest exists in
    the tree must be in `RELEASE_SURFACES`. Adding `.cursor-plugin/plugin.json` without a
    table entry fails here, at the point of adding it.
    """

    #: Where a per-agent plugin manifest lives, by the convention every one of these
    #: ecosystems uses: a dot-directory at the repository root holding `plugin.json`.
    _MANIFEST_GLOB = ".*/plugin.json"

    def _manifests(self) -> list[str]:
        root = Path(release_surfaces.__file__).resolve().parents[1]
        found = sorted(
            path.relative_to(root).as_posix()
            for path in root.glob(self._MANIFEST_GLOB)
            if ".git/" not in path.as_posix()
        )
        self.assertTrue(found, "no plugin manifests found — the glob no longer matches")
        return found

    def test_the_known_manifests_are_still_found(self):
        """Vacuity: a glob that stopped matching would make the check below pass.

        A *subset* assertion on purpose — adding `.cursor-plugin/plugin.json` should fail
        exactly one test, the registration one, with a message that says what to do. A
        equality here would fail two and send the reader to the glob instead.
        """
        self.assertLessEqual(
            {".claude-plugin/plugin.json", ".codex-plugin/plugin.json"}, set(self._manifests())
        )

    def test_every_manifest_in_the_tree_is_registered(self):
        registered = {surface.path for surface in release_surfaces.RELEASE_SURFACES}
        unregistered = [path for path in self._manifests() if path not in registered]

        self.assertEqual(
            [],
            unregistered,
            "these plugin manifests name a version and are not in RELEASE_SURFACES, so the "
            "release bump will not touch them and they will report a stale keel:\n"
            + "\n".join(unregistered),
        )

    def test_and_every_registered_manifest_still_exists(self):
        """The mirror: a table entry for a deleted file makes `--check-surfaces` read a
        file that is not there, which `required=False` turns into silence rather than a
        failure."""
        root = Path(release_surfaces.__file__).resolve().parents[1]
        registered = [
            surface.path
            for surface in release_surfaces.RELEASE_SURFACES
            if surface.path.endswith("plugin.json")
        ]
        missing = [path for path in registered if not (root / path).is_file()]

        self.assertEqual([], missing, f"registered but absent: {missing}")


class TestBumpThenCheck(unittest.TestCase):
    """The round trip: what the bump writes is what the release check accepts."""

    def test_a_bumped_tree_passes_the_surface_check(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root, OLD)

            old, changed = release_bump.bump(root, NEW)

            self.assertEqual(old, OLD)
            self.assertEqual(
                sorted(changed),
                sorted(FIXTURE),
                "the bump must reach every surface the check will read",
            )
            result = release_check.check_surfaces(root)
            self.assertEqual(result.problems, [])
            self.assertTrue(result.ok)

    def test_the_bump_leaves_historical_prose_alone(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root, OLD)
            release_bump.bump(root, NEW)

            self.assertIn("Since **1.6.5**", (root / "README.md").read_text(encoding="utf-8"))

    def test_one_surface_left_behind_is_named(self):
        """The failure the guard exists for, one surface at a time.

        ``pyproject.toml`` is excluded because it is the *reference*: the check
        reads the declared version from it, so reverting that one file does not
        leave it behind — it moves the whole tree, and every other surface is
        then correctly reported as ahead. That direction is covered by
        ``test_reverting_the_declared_version_reports_every_other_surface``.
        """
        for surface in release_surfaces.RELEASE_SURFACES:
            if surface.path == "pyproject.toml":
                continue
            with self.subTest(path=surface.path):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _write_fixture(root, NEW)
                    # Re-write just this one file at the old version: the state a
                    # half-applied bump leaves behind.
                    (root / surface.path).write_text(
                        FIXTURE[surface.path].format(v=OLD), encoding="utf-8"
                    )

                    problems = release_check.check_surfaces(root).problems

                    self.assertTrue(problems, f"a stale {surface.path} was not caught")
                    self.assertTrue(
                        all(surface.path in problem for problem in problems),
                        f"the problem report does not name {surface.path}: {problems}",
                    )

    def test_reverting_the_declared_version_reports_every_other_surface(self):
        """pyproject.toml is the reference, so a stale one is loud, not silent."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root, NEW)
            (root / "pyproject.toml").write_text(
                FIXTURE["pyproject.toml"].format(v=OLD), encoding="utf-8"
            )

            problems = release_check.check_surfaces(root).problems

            reported = {problem.split(" declares ")[0] for problem in problems}
            self.assertEqual(
                reported,
                {surface.path for surface in release_surfaces.RELEASE_SURFACES}
                - {"pyproject.toml"},
            )

    def test_a_missing_surface_is_a_failure_not_a_skip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root, NEW)
            (root / "website" / "content.js").unlink()

            problems = release_check.check_surfaces(root).problems

            self.assertEqual(len(problems), 2, problems)
            for problem in problems:
                self.assertIn("website/content.js", problem)
                self.assertIn("does not exist", problem)

    def test_a_surface_whose_token_vanished_is_a_failure_not_a_pass(self):
        """A renamed token makes every version comparison vacuously true."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fixture(root, NEW)
            (root / "website" / "docs.html").write_text(
                "<span>no marker</span>\n", encoding="utf-8"
            )

            problems = release_check.check_surfaces(root).problems

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("website/docs.html", problems[0])
            self.assertIn("carries no", problems[0])


class TestTheCommandLine(unittest.TestCase):
    """`make release-check` and publish.yml both read the exit code, not the text."""

    def _exit_code(self, *argv: str) -> int:
        """Run the CLI, keeping its report out of the suite's output."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return release_check.main(list(argv))

    def _complete_fixture(self, root: Path, version: str) -> None:
        _write_fixture(root, version)
        (root / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-09-03\n\n"
            "### Fixed\n- a thing\n",
            encoding="utf-8",
        )
        (root / "keel-visual" / "src" / "keel_visual").mkdir(parents=True)
        (root / "keel-visual" / "pyproject.toml").write_text(
            '[project]\nname = "keel-visual"\nversion = "0.8.0"\n', encoding="utf-8"
        )
        (root / "keel-visual" / "src" / "keel_visual" / "__init__.py").write_text(
            '"""keel-visual."""\n\n__version__ = "0.8.0"\n', encoding="utf-8"
        )

    def test_an_agreeing_tree_exits_zero(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._complete_fixture(root, NEW)

            self.assertEqual(self._exit_code("--root", str(root)), 0)
            self.assertEqual(self._exit_code("--root", str(root), "--tag", f"v{NEW}"), 0)

    def test_a_stale_surface_exits_one(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._complete_fixture(root, NEW)
            (root / "website" / "docs.html").write_text(
                FIXTURE["website/docs.html"].format(v=OLD), encoding="utf-8"
            )

            self.assertEqual(self._exit_code("--root", str(root)), 1)

    def test_a_tag_that_does_not_name_the_declared_version_exits_one(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._complete_fixture(root, NEW)

            self.assertEqual(self._exit_code("--root", str(root), "--tag", f"v{OLD}"), 1)

    def test_an_unreadable_tree_exits_one_rather_than_raising(self):
        """A crash in the guard is a release that stops for the wrong reason."""
        with TemporaryDirectory() as tmp:
            self.assertEqual(self._exit_code("--root", str(Path(tmp) / "nope")), 1)


if __name__ == "__main__":
    unittest.main()
