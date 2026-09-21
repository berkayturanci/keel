"""`keel gc` and the gitignore self-heal must not follow a symlink out of the repo (#1247).

A cloned or fork checkout can commit `.keel/scratch` as a symlink (the shipped ignore entry
`scratch/` matches directories only, so `git add -A` stages the link), or `.keel/.gitignore` as
one. `keel gc` then recursively deletes the symlink target — files outside the repo — and the
gitignore self-heal appends keel's ignore lines to whatever the link points at.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from keel import workspace


@unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
class TestScratchSymlinkContainment(unittest.TestCase):
    def _root_with_scratch_link(self, target: Path) -> Path:
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        (root / ".keel").mkdir()
        (root / ".keel" / "scratch").symlink_to(target, target_is_directory=True)
        return root

    def test_clean_scratch_refuses_a_symlinked_scratch(self):
        with TemporaryDirectory() as outside:
            victim = Path(outside) / "precious.txt"
            victim.write_text("keep me", encoding="utf-8")
            root = self._root_with_scratch_link(Path(outside))
            with self.assertRaises(OSError):
                workspace.clean_scratch(root)
            self.assertTrue(victim.exists(), "gc deleted a file outside the repo")

    def test_scratch_entries_does_not_list_through_a_symlink(self):
        with TemporaryDirectory() as outside:
            (Path(outside) / "secret.txt").write_text("x", encoding="utf-8")
            root = self._root_with_scratch_link(Path(outside))
            self.assertEqual(workspace.scratch_entries(root), [])

    def test_a_symlinked_child_is_unlinked_not_followed(self):
        with TemporaryDirectory() as outside:
            victim = Path(outside) / "precious.txt"
            victim.write_text("keep me", encoding="utf-8")
            d = TemporaryDirectory()
            self.addCleanup(d.cleanup)
            root = Path(d.name)
            scratch = root / ".keel" / "scratch"
            scratch.mkdir(parents=True)
            (scratch / "link").symlink_to(Path(outside), target_is_directory=True)
            (scratch / "real.tmp").write_text("junk", encoding="utf-8")
            removed = workspace.clean_scratch(root)
            self.assertTrue(victim.exists(), "cleaning a symlinked child deleted the target")
            self.assertEqual(list(scratch.iterdir()), [])  # scratch emptied
            self.assertIn("link", removed)

    def test_ordinary_scratch_still_cleans(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        scratch = root / ".keel" / "scratch"
        (scratch / "sub").mkdir(parents=True)
        (scratch / "sub" / "a.txt").write_text("x", encoding="utf-8")
        (scratch / "b.txt").write_text("y", encoding="utf-8")
        removed = workspace.clean_scratch(root)
        self.assertEqual(sorted(removed), ["b.txt", "sub"])
        self.assertEqual(list(scratch.iterdir()), [])


@unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
class TestGitignoreSymlinkContainment(unittest.TestCase):
    def test_a_symlinked_gitignore_is_not_written_through(self):
        with TemporaryDirectory() as outside:
            victim = Path(outside) / "target"
            victim.write_text("original\n", encoding="utf-8")
            d = TemporaryDirectory()
            self.addCleanup(d.cleanup)
            keel = Path(d.name) / ".keel"
            keel.mkdir()
            (keel / ".gitignore").symlink_to(victim)
            changed = workspace.ensure_runtime_gitignore(keel)
            self.assertFalse(changed)
            self.assertEqual(victim.read_text(encoding="utf-8"), "original\n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
