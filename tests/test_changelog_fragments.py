"""Changelog fragments (#1515): collection, ordering, merge, refusal, preview."""

from __future__ import annotations

import importlib.util
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fragments = _load("changelog_fragments")
release_check = _load("release_check")
release_bump = _load("release_bump")

HEAD = "# Changelog\n\n## [Unreleased]\n\n"
OLD = "## [1.0.0] - 2026-01-01\n\n- old highlight\n\n### Fixed\n- **old** (#1).\n"


def _tree(root: Path, unreleased: str = "", frags: dict[str, str] | None = None) -> None:
    (root / "CHANGELOG.md").write_text(HEAD + unreleased + OLD, encoding="utf-8")
    if frags is not None:
        (root / "changelog.d").mkdir()
        for name, text in frags.items():
            (root / "changelog.d" / name).write_text(text, encoding="utf-8")


class TestScan(unittest.TestCase):
    def test_no_directory_is_no_fragments(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(([], []), fragments.scan(Path(tmp)))

    def test_sorted_by_issue_number_not_by_name(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"100.fixed.md": "- b\n", "9.fixed.md": "- a\n"})
            found, problems = fragments.scan(root)
            self.assertEqual([], problems)
            self.assertEqual([9, 100], [f.issue for f in found])

    def test_readme_and_gitkeep_are_not_fragments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"README.md": "prose", ".gitkeep": ""})
            self.assertEqual(([], []), fragments.scan(root))

    def test_malformed_names_and_content_are_all_reported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(
                root,
                frags={
                    "abc.fixed.md": "- x\n",
                    "12.bogus.md": "- x\n",
                    "13.fixed.md": "just prose\n",
                    "14.fixed.md": "\n",
                    "notes.txt": "- x\n",
                },
            )
            found, problems = fragments.scan(root)
            self.assertEqual([], found)
            joined = "\n".join(problems)
            self.assertEqual(5, len(problems))
            self.assertIn("abc.fixed.md is not named", joined)
            self.assertIn("unknown type 'bogus'", joined)
            self.assertIn("13.fixed.md must open with", joined)
            self.assertIn("14.fixed.md must open with", joined)
            self.assertIn("notes.txt is not named", joined)


class TestCollection(unittest.TestCase):
    def test_groups_by_heading_in_canonical_order_and_sorts_by_issue(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(
                root,
                frags={
                    "30.fixed.md": "- fix 30\n",
                    "5.fixed.md": "- fix 5\n",
                    "20.added.md": "- add 20\n",
                    "7.security.md": "- sec 7\n",
                    "8.changed.md": "- chg 8\n  continued\n",
                },
            )
            collection = fragments.plan_collection(root, "1.1.0", "2026-02-02")
            section = collection.changelog.split("## [1.1.0]")[1].split("## [1.0.0]")[0]
            self.assertEqual(
                " - 2026-02-02\n\n### Added\n- add 20\n\n### Changed\n- chg 8\n  continued\n\n"
                "### Fixed\n- fix 5\n- fix 30\n\n### Security\n- sec 7\n\n",
                section,
            )
            self.assertTrue(collection.changelog.startswith(HEAD + "## [1.1.0]"))
            self.assertIn("## [1.0.0] - 2026-01-01", collection.changelog)

    def test_merges_with_existing_unreleased_entries_and_keeps_preamble(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(
                root,
                unreleased="- a highlight\n\n### Fixed\n- already here\n\n### Tests\n- t\n\n",
                frags={"3.fixed.md": "- from fragment\n", "4.added.md": "- new\n"},
            )
            text = fragments.plan_collection(root, "1.1.0", "2026-02-02").changelog
            section = text.split("## [1.1.0]")[1].split("## [1.0.0]")[0]
            self.assertEqual(
                " - 2026-02-02\n\n- a highlight\n\n### Added\n- new\n\n"
                "### Fixed\n- already here\n- from fragment\n\n### Tests\n- t\n\n",
                section,
            )
            # Unreleased is emptied, not removed.
            self.assertTrue(text.startswith(HEAD + "## [1.1.0]"))

    def test_unreleased_alone_is_still_cut_into_the_section(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, unreleased="### Added\n- hand written\n\n")
            text = fragments.plan_collection(root, "1.1.0", "2026-02-02").changelog
            self.assertIn("## [1.1.0] - 2026-02-02\n\n### Added\n- hand written\n", text)

    def test_nothing_to_collect_is_none(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            self.assertIsNone(fragments.plan_collection(root, "1.1.0", "2026-02-02"))

    def test_no_changelog_is_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(fragments.plan_collection(Path(tmp), "1.1.0", "2026-02-02"))

    def test_an_existing_section_is_not_collected_again(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"3.fixed.md": "- later\n"})
            self.assertIsNone(fragments.plan_collection(root, "1.0.0", "2026-02-02"))

    def test_malformed_fragment_refuses_planning(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"x.fixed.md": "- a\n"})
            with self.assertRaises(ValueError):
                fragments.plan_collection(root, "1.1.0", "2026-02-02")

    def test_missing_unreleased_heading_refuses(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CHANGELOG.md").write_text(OLD, encoding="utf-8")
            with self.assertRaises(ValueError):
                fragments.plan_collection(root, "1.1.0", "2026-02-02")

    def test_unreleased_as_the_last_section_is_found(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Added\n- x", encoding="utf-8"
            )
            text = fragments.plan_collection(root, "1.1.0", "2026-02-02").changelog
            self.assertIn("## [1.1.0] - 2026-02-02\n\n### Added\n- x\n", text)

    def test_apply_writes_and_deletes_only_collected_fragments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"3.fixed.md": "- a\n", "README.md": "keep"})
            collection = fragments.plan_collection(root, "1.1.0", "2026-02-02")
            # Planning writes nothing.
            self.assertTrue((root / "changelog.d" / "3.fixed.md").exists())
            changed = fragments.apply_collection(root, collection)
            self.assertEqual(["CHANGELOG.md", "changelog.d/3.fixed.md"], changed)
            self.assertFalse((root / "changelog.d" / "3.fixed.md").exists())
            self.assertTrue((root / "changelog.d" / "README.md").exists())
            self.assertIn("## [1.1.0]", (root / "CHANGELOG.md").read_text(encoding="utf-8"))


class TestPreview(unittest.TestCase):
    def test_prints_only_the_new_section(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"3.fixed.md": "- a\n"})
            text = fragments.preview(root, "next", "2026-02-02")
            self.assertEqual("## [next] - 2026-02-02\n\n### Fixed\n- a\n", text)
            # Preview does not consume the fragment.
            self.assertTrue((root / "changelog.d" / "3.fixed.md").exists())

    def test_empty_says_so(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            self.assertIn("nothing to release", fragments.preview(root, "next", "d"))

    def test_main_prints_and_refuses_malformed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, frags={"3.fixed.md": "- a\n"})
            out = StringIO()
            with redirect_stdout(out):
                code = fragments.main(["--root", str(root), "--date", "2026-02-02"])
            self.assertEqual(0, code)
            self.assertIn("## [next] - 2026-02-02", out.getvalue())
            with redirect_stdout(StringIO()):
                fragments.main(["--root", str(root)])  # default date: today
            (root / "changelog.d" / "bad.md").write_text("x", encoding="utf-8")
            err = StringIO()
            with redirect_stderr(err):
                code = fragments.main(["--root", str(root)])
            self.assertEqual(1, code)
            self.assertIn("changelog-preview failed", err.getvalue())


class TestSectionEntries(unittest.TestCase):
    def test_counts_bullets_under_headings_not_highlights(self):
        text = (
            "## [1.0.0] - d\n\n- highlight\n\n### Fixed\n- a\n- b\n\n## [0.9.0]\n### Added\n- z\n"
        )
        self.assertEqual(2, fragments.section_entries(text, "1.0.0"))

    def test_missing_section_is_none_and_empty_is_zero(self):
        self.assertIsNone(fragments.section_entries("## [1.0.0]\n", "2.0.0"))
        self.assertEqual(0, fragments.section_entries("## [1.0.0]\n- hl\n", "1.0.0"))


def _release_tree(root: Path, changelog: str) -> None:
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "keel-workflow"\nversion = "1.1.0"\n', encoding="utf-8"
    )


class TestReleaseCheck(unittest.TestCase):
    def test_empty_section_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "## [Unreleased]\n\n## [1.1.0] - d\n\n- highlight\n")
            check = release_check.check_entries(root)
            self.assertFalse(check.ok)
            self.assertIn("no entries", check.problems[0])

    def test_missing_section_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "## [Unreleased]\n")
            self.assertIn("no `## [1.1.0]`", release_check.check_entries(root).problems[0])

    def test_section_with_entries_passes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "## [1.1.0] - d\n\n- hl\n\n### Fixed\n- a\n")
            self.assertTrue(release_check.check_entries(root).ok)

    def test_malformed_fragment_name_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "## [1.1.0] - d\n")
            (root / "changelog.d").mkdir()
            (root / "changelog.d" / "abc.fixed.md").write_text("- a\n", encoding="utf-8")
            check = release_check.check_fragments(root)
            self.assertFalse(check.ok)
            (root / "changelog.d" / "abc.fixed.md").rename(root / "changelog.d" / "12.fixed.md")
            self.assertTrue(release_check.check_fragments(root).ok)


class TestRunChecksWiring(unittest.TestCase):
    def test_both_new_guards_run_in_the_release_check(self):
        from unittest import mock

        names = ("declared_version", "changelog", "highlights", "surfaces", "visual_markers")
        patches = [
            mock.patch.object(release_check, f"check_{n}", return_value=release_check.Check(n, []))
            for n in names
        ]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_tree(root, "## [1.1.0] - d\n")
            (root / "changelog.d").mkdir()
            (root / "changelog.d" / "bad.md").write_text("x", encoding="utf-8")
            for patch in patches:
                patch.start()
                self.addCleanup(patch.stop)
            checks = {c.name: c for c in release_check.run_checks(root)}
        self.assertFalse(checks["changelog entries"].ok)
        self.assertFalse(checks["changelog fragments"].ok)


def _bump_tree(root: Path, frags: dict[str, str]) -> None:
    (root / "src" / "keel").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "keel-workflow"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    (root / "src" / "keel" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    _tree(root, frags=frags)


class TestBumpCollects(unittest.TestCase):
    def test_bump_cuts_the_section_and_deletes_the_fragments(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _bump_tree(root, {"3.fixed.md": "- a\n"})
            out = StringIO()
            with redirect_stdout(out):
                code = release_bump.main(["1.1.0", "--root", str(root), "--date", "2026-02-02"])
            self.assertEqual(0, code)
            self.assertIn("updated CHANGELOG.md", out.getvalue())
            self.assertIn("updated changelog.d/3.fixed.md", out.getvalue())
            text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn("## [1.1.0] - 2026-02-02\n\n### Fixed\n- a\n", text)
            self.assertFalse((root / "changelog.d" / "3.fixed.md").exists())
            self.assertIn('version = "1.1.0"', (root / "pyproject.toml").read_text())

    def test_default_date_is_today(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _bump_tree(root, {"3.fixed.md": "- a\n"})
            with redirect_stdout(StringIO()):
                release_bump.main(["1.1.0", "--root", str(root)])
            heading = (root / "CHANGELOG.md").read_text().split("## [1.1.0] - ")[1][:10]
            self.assertRegex(heading, r"^\d{4}-\d{2}-\d{2}$")

    def test_malformed_fragment_refuses_the_bump_and_leaves_the_tree_alone(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _bump_tree(root, {"3.fixed.md": "- a\n", "oops.md": "- b\n"})
            err = StringIO()
            with redirect_stderr(err):
                code = release_bump.main(["1.1.0", "--root", str(root)])
            self.assertEqual(1, code)
            self.assertIn("oops.md", err.getvalue())
            self.assertIn('version = "1.0.0"', (root / "pyproject.toml").read_text())
            self.assertTrue((root / "changelog.d" / "3.fixed.md").exists())
            self.assertNotIn("1.1.0", (root / "CHANGELOG.md").read_text())

    def test_invalid_version_does_not_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _bump_tree(root, {"3.fixed.md": "- a\n"})
            with redirect_stderr(StringIO()):
                code = release_bump.main(["nope", "--root", str(root)])
            self.assertEqual(1, code)
            self.assertTrue((root / "changelog.d" / "3.fixed.md").exists())


if __name__ == "__main__":
    unittest.main()
