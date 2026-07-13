"""Tests for the release version bumper (`scripts/release_bump.py`).

The script lives under ``scripts/`` (a maintenance tool, not part of the
``keel`` package), so it is outside the coverage gate; these tests still pin its
behaviour against a fixture tree — especially that it rewrites *every* file a
release must touch and leaves historical version prose alone.
"""

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_bump.py"
_spec = importlib.util.spec_from_file_location("release_bump", _SCRIPT)
release_bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_bump)


def _fixture(root: Path, old: str) -> None:
    """Write a minimal tree carrying the version markers a release touches."""
    (root / "src" / "keel").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".codex-plugin").mkdir()
    (root / "website").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "keel-workflow"\nversion = "{old}"\n', encoding="utf-8")
    (root / "src" / "keel" / "__init__.py").write_text(
        f'"""doc."""\n__version__ = "{old}"\n', encoding="utf-8")
    (root / ".claude-plugin" / "plugin.json").write_text(
        f'{{\n  "name": "keel",\n  "version": "{old}"\n}}\n', encoding="utf-8")
    (root / ".codex-plugin" / "plugin.json").write_text(
        f'{{\n  "name": "keel",\n  "version": "{old}"\n}}\n', encoding="utf-8")
    # README carries the pinned install AND a historical mention that must survive.
    (root / "README.md").write_text(
        f'pip install "git+https://github.com/berkayturanci/keel@v{old}"\n'
        f'Since **{old}**, the board stamps merged.\n', encoding="utf-8")
    (root / "website" / "index.html").write_text(
        f'<span class="ver" data-version>v{old}</span>\n'
        f'<code>pip install "git+https://github.com/berkayturanci/keel@v{old}"</code>\n',
        encoding="utf-8")
    (root / ".github" / "workflows" / "keel-ship.yml").write_text(
        f'# (`pip install "git+https://github.com/berkayturanci/keel@v{old}"`)\n',
        encoding="utf-8")


class TestBump(unittest.TestCase):
    def test_rewrites_all_release_files_and_preserves_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            old, changed = release_bump.bump(root, "1.8.0")
            self.assertEqual(old, "1.7.0")
            self.assertEqual(sorted(changed), [
                ".claude-plugin/plugin.json",
                ".codex-plugin/plugin.json",
                ".github/workflows/keel-ship.yml",
                "README.md",
                "pyproject.toml",
                "src/keel/__init__.py",
                "website/index.html",
            ])
            self.assertIn('version = "1.8.0"',
                          (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.8.0"',
                          (root / "src" / "keel" / "__init__.py").read_text(encoding="utf-8"))
            self.assertIn('"version": "1.8.0"',
                          (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertIn('"version": "1.8.0"',
                          (root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertIn("keel@v1.8.0", readme)
            self.assertNotIn("keel@v1.7.0", readme)
            # Historical prose is untouched.
            self.assertIn("Since **1.7.0**", readme)
            site = (root / "website" / "index.html").read_text(encoding="utf-8")
            self.assertIn(">v1.8.0<", site)
            self.assertIn("keel@v1.8.0", site)
            self.assertNotIn("1.7.0", site)

    def test_current_version_reads_pyproject(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "2.0.1")
            self.assertEqual(release_bump.current_version(root), "2.0.1")

    def test_current_version_missing_raises(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "pyproject.toml").write_text("[project]\nname='x'\n")
            with self.assertRaises(ValueError):
                release_bump.current_version(Path(tmp))

    def test_invalid_version_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            with self.assertRaises(ValueError):
                release_bump.bump(root, "1.8")

    def test_same_version_is_noop(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            old, changed = release_bump.bump(root, "1.7.0")
            self.assertEqual(old, "1.7.0")
            self.assertEqual(changed, [])

    def test_file_without_version_token_is_skipped(self):
        # A file that does not carry the old version token is left alone and is
        # absent from the changed list (e.g. a workflow that doesn't pin a tag).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            (root / ".github" / "workflows" / "keel-ship.yml").write_text(
                "# no pinned install here\n", encoding="utf-8")
            _old, changed = release_bump.bump(root, "1.8.0")
            self.assertNotIn(".github/workflows/keel-ship.yml", changed)
            self.assertIn("pyproject.toml", changed)

    def test_main_reports_changes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            rc = release_bump.main(["1.8.0", "--root", str(root)])
            self.assertEqual(rc, 0)

    def test_main_noop_returns_zero(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            self.assertEqual(release_bump.main(["1.7.0", "--root", str(root)]), 0)

    def test_main_bad_version_returns_1(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root, "1.7.0")
            self.assertEqual(release_bump.main(["nope", "--root", str(root)]), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
