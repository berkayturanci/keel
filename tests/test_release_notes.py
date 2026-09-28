"""Each GitHub Release opens with plain-language highlights (#1342).

The v1.24.x releases were GitHub's generated "What's Changed" list only — pull
request titles. `scripts/release_notes.py` reads the highlight bullets a released
CHANGELOG section opens with and renders the top of the release body;
`release_check.py` refuses a release whose section has none, using the same
parser. These tests hold the parser, the renderer, the command line `publish.yml`
calls, and the guard.

``scripts/`` is maintenance tooling outside the coverage gate, so these tests are
what hold it. The workflow wiring is pinned in `tests/test_publish_release_chain.py`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_notes = _load("release_notes")
release_check = _load("release_check")

GOOD = (
    "# Changelog\n\n## [Unreleased]\n\n"
    "## [1.25.0] - 2026-10-01\n\n"
    "- Reviews that never answered now stop the merge.\n"
    "- Installing no longer needs a second\n"
    "  package on Windows.\n\n"
    "### Fixed\n- a thing (#1)\n\n"
    "## [1.24.2] - 2026-09-23\n\n### Fixed\n- an older thing\n"
)


def _changelog(preamble: str, version: str = "1.25.0") -> str:
    return f"# Changelog\n\n## [{version}] - 2026-10-01\n\n{preamble}\n### Fixed\n- x\n"


class TheHighlightsAreTheBulletsAboveTheFirstSection(unittest.TestCase):
    def test_a_well_formed_section_yields_its_bullets(self):
        highlights, problems = release_notes.parse_highlights(GOOD, "1.25.0")
        self.assertEqual(problems, [])
        self.assertEqual(
            highlights,
            [
                "Reviews that never answered now stop the merge.",
                "Installing no longer needs a second package on Windows.",
            ],
        )

    def test_the_section_list_below_is_not_read_as_highlights(self):
        """`- a thing (#1)` sits under `### Fixed`, so it is an entry, not a highlight."""
        highlights, _ = release_notes.parse_highlights(GOOD, "1.25.0")
        self.assertNotIn("a thing (#1)", highlights)

    def test_the_next_version_ends_the_preamble(self):
        """A section with no `###` at all must not borrow the next release's bullets."""
        text = "## [1.25.0] - 2026-10-01\n\n- ours\n\n## [1.24.2] - 2026-09-23\n\n- theirs\n"
        highlights, problems = release_notes.parse_highlights(text, "1.25.0")
        self.assertEqual(highlights, ["ours"])
        self.assertEqual(problems, [])

    def test_a_section_without_highlights_is_refused(self):
        _, problems = release_notes.parse_highlights(_changelog(""), "1.25.0")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("0 highlight line(s)", problems[0])

    def test_more_than_three_is_refused(self):
        four = "".join(f"- highlight {n}\n" for n in range(4))
        _, problems = release_notes.parse_highlights(_changelog(four), "1.25.0")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("4 highlight line(s)", problems[0])

    def test_one_to_three_are_accepted(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                bullets = "".join(f"- highlight {n}\n" for n in range(count))
                highlights, problems = release_notes.parse_highlights(_changelog(bullets), "1.25.0")
                self.assertEqual(problems, [])
                self.assertEqual(len(highlights), count)

    def test_a_paragraph_above_the_first_section_is_refused_not_dropped(self):
        """1.17.0 and 1.18.0 opened with prose. Under this convention prose there
        would never reach the release, so it is named instead of skipped."""
        text = _changelog("This release is mostly about truth.\n\n- one highlight\n")
        highlights, problems = release_notes.parse_highlights(text, "1.25.0")
        self.assertEqual(highlights, ["one highlight"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("This release is mostly about truth.", problems[0])

    def test_an_indented_line_before_any_bullet_is_refused(self):
        _, problems = release_notes.parse_highlights(
            _changelog("  stray indent\n- one\n"), "1.25.0"
        )
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("stray indent", problems[0])

    def test_a_missing_section_is_its_own_problem(self):
        _, problems = release_notes.parse_highlights(GOOD, "9.9.9")
        self.assertEqual(problems, ["CHANGELOG.md has no `## [9.9.9]` section"])


class TheBodyOpensWithTheHighlights(unittest.TestCase):
    def test_render(self):
        body = release_notes.render(["First.", "Second."], "owner/keel", "v1.25.0")
        self.assertEqual(
            body,
            "## Highlights\n\n- First.\n- Second.\n\n"
            "Full notes: [CHANGELOG.md]"
            "(https://github.com/owner/keel/blob/v1.25.0/CHANGELOG.md)\n",
        )


class TheCommandLinePublishYmlCalls(unittest.TestCase):
    def _run(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = release_notes.main(list(argv))
        return code, err.getvalue()

    def test_it_writes_the_body_for_the_tagged_version(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CHANGELOG.md").write_text(GOOD, encoding="utf-8")
            out = root / "notes.md"
            code, _ = self._run(
                "--tag", "v1.25.0", "--repo", "o/r", "--out", str(out), "--root", str(root)
            )
            self.assertEqual(code, 0)
            body = out.read_text(encoding="utf-8")
            self.assertTrue(body.startswith("## Highlights\n\n- Reviews that never answered"))
            self.assertIn("/o/r/blob/v1.25.0/CHANGELOG.md", body)

    def test_a_section_without_highlights_exits_one_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CHANGELOG.md").write_text(GOOD, encoding="utf-8")
            out = root / "notes.md"
            code, err = self._run(
                "--tag", "v1.24.2", "--repo", "o/r", "--out", str(out), "--root", str(root)
            )
            self.assertEqual(code, 1)
            self.assertIn("0 highlight line(s)", err)
            self.assertFalse(out.exists())

    def test_a_malformed_tag_exits_one(self):
        with TemporaryDirectory() as tmp:
            code, err = self._run(
                "--tag", "main", "--repo", "o/r", "--out", str(Path(tmp) / "n"), "--root", tmp
            )
            self.assertEqual(code, 1)
            self.assertIn("not of the form vX.Y.Z", err)

    def test_a_missing_changelog_exits_one_rather_than_raising(self):
        with TemporaryDirectory() as tmp:
            code, _ = self._run(
                "--tag", "v1.25.0", "--repo", "o/r", "--out", str(Path(tmp) / "n"), "--root", tmp
            )
            self.assertEqual(code, 1)


def _release_tree(root: Path, declared: str, changelog: str) -> None:
    (root / "src" / "keel").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "keel-workflow"\nversion = "{declared}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


class TheReleaseCheckRefusesASectionWithoutHighlights(unittest.TestCase):
    def test_the_guard_is_one_of_the_release_checks(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "1.24.2", GOOD)
            (root / "src" / "keel" / "__init__.py").write_text(
                '__version__ = "1.24.2"\n', encoding="utf-8"
            )
            names = [check.name for check in release_check.run_checks(root)]
        self.assertIn("release highlights", names)

    def test_a_section_with_highlights_passes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "1.25.0", GOOD)
            self.assertEqual(release_check.check_highlights(root).problems, [])

    def test_a_section_without_highlights_is_refused(self):
        bare = GOOD.replace(
            "- Reviews that never answered now stop the merge.\n"
            "- Installing no longer needs a second\n"
            "  package on Windows.\n\n",
            "",
        )
        self.assertNotEqual(bare, GOOD, "the fixture edit matched nothing")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "1.25.0", bare)
            problems = release_check.check_highlights(root).problems
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("`## [1.25.0]` opens with 0 highlight line(s)", problems[0])

    def test_a_release_from_before_the_convention_is_not_rewritten(self):
        """1.24.2 shipped without highlights; the tree between releases, whose top
        section is that release, must still pass its own `make release-check`."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "1.24.2", GOOD)
            self.assertEqual(release_check.check_highlights(root).problems, [])


class OnlyReleasesAfterTheConventionNeedHighlights(unittest.TestCase):
    def test_the_boundary(self):
        self.assertEqual(release_notes.LAST_WITHOUT_HIGHLIGHTS, (1, 24, 2))
        cases = {
            "1.24.2": False,
            "1.24.1": False,
            "0.99.0": False,
            "1.24.3": True,
            "1.25.0": True,
            "2.0.0": True,
            "1.100.0": True,
            "Unreleased": False,
            "1.25": False,
        }
        for version, expected in cases.items():
            with self.subTest(version=version):
                self.assertIs(release_notes.requires_highlights(version), expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
