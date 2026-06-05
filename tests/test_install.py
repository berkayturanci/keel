"""Unit tests for `keel install-adapter` (packaged command adapters)."""

import tempfile
import unittest
from pathlib import Path

from keel import install


class TestAdapterNames(unittest.TestCase):
    def test_ships_the_portable_commands(self):
        names = install.adapter_names()
        for expected in ("ship.md", "regression.md", "review-cycle.md", "morning.md"):
            self.assertIn(expected, names)
        self.assertGreaterEqual(len(names), 10)


class TestInstall(unittest.TestCase):
    def test_installs_into_claude_dir(self):
        with tempfile.TemporaryDirectory() as d:
            installed, skipped = install.install("claude", d)
            self.assertIn("ship.md", installed)
            self.assertEqual(skipped, [])
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())

    def test_skips_existing_then_force(self):
        with tempfile.TemporaryDirectory() as d:
            install.install("claude", d)
            installed, skipped = install.install("claude", d)
            self.assertEqual(installed, [])
            self.assertIn("ship.md", skipped)
            again, _ = install.install("claude", d, force=True)
            self.assertIn("ship.md", again)

    def test_per_agent_target_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            install.install("codex", d)
            self.assertTrue((Path(d) / ".codex/prompts/keel/ship.md").exists())
            install.install("agy", d)
            self.assertTrue((Path(d) / ".agents/keel/ship.md").exists())

    def test_unknown_agent_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyError):
                install.install("nope", d)


if __name__ == "__main__":
    unittest.main()
