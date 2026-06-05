"""Unit tests for `keel install-adapter` (packaged command adapters → two surfaces)."""

import tempfile
import unittest
from pathlib import Path

import yaml

from keel import install


class TestAdapterNames(unittest.TestCase):
    def test_ships_the_portable_commands(self):
        names = install.adapter_names()
        for expected in ("ship.md", "regression.md", "review-cycle.md", "morning.md"):
            self.assertIn(expected, names)
        self.assertGreaterEqual(len(names), 10)


class TestInstallClaude(unittest.TestCase):
    def test_installs_native_commands(self):
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

    def test_unknown_surface_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(KeyError):
                install.install("codex", d)


class TestRenderSkill(unittest.TestCase):
    def test_wraps_adapter_as_skill(self):
        adapter = (
            '---\ndescription: "Do the thing"\nargument-hint: "[x]"\n---\n\n'
            "# /keel:thing\nBody here.\n"
        )
        out = install.render_skill(adapter, "thing")
        meta, body = install._split_frontmatter(out)
        self.assertEqual(meta["name"], "keel-thing")
        self.assertEqual(meta["description"], "Do the thing")
        self.assertIn("# keel-thing", body)
        self.assertIn("Body here.", body)
        self.assertIn("`.keel/project.yaml`", body)

    def test_no_frontmatter_falls_back(self):
        out = install.render_skill("# just a body\n", "bare")
        meta, _ = install._split_frontmatter(out)
        self.assertEqual(meta["name"], "keel-bare")
        self.assertEqual(meta["description"], "keel bare workflow")

    def test_unterminated_frontmatter_is_treated_as_body(self):
        # starts with --- but has no closing fence → no frontmatter
        meta, body = install._split_frontmatter("---\nnot closed\nstill body\n")
        self.assertEqual(meta, {})
        self.assertIn("not closed", body)

    def test_non_dict_frontmatter_yields_empty_meta(self):
        meta, _ = install._split_frontmatter("---\n- a\n- b\n---\nbody\n")
        self.assertEqual(meta, {})

    def test_description_with_quotes_stays_valid_yaml(self):
        adapter = '---\ndescription: He said "hi" to all\n---\nbody\n'
        out = install.render_skill(adapter, "q")
        meta, _ = install._split_frontmatter(out)
        self.assertEqual(meta["description"], 'He said "hi" to all')


class TestInstallSkills(unittest.TestCase):
    def test_installs_one_shared_skill_set(self):
        with tempfile.TemporaryDirectory() as d:
            installed, skipped = install.install("skills", d)
            self.assertIn("keel-ship", installed)
            self.assertEqual(skipped, [])
            sk = Path(d) / ".agents/skills/keel-ship/SKILL.md"
            self.assertTrue(sk.exists())
            meta = yaml.safe_load(sk.read_text().split("---")[1])
            self.assertEqual(meta["name"], "keel-ship")

    def test_skips_existing_then_force(self):
        with tempfile.TemporaryDirectory() as d:
            install.install("skills", d)
            installed, skipped = install.install("skills", d)
            self.assertEqual(installed, [])
            self.assertIn("keel-ship", skipped)
            again, _ = install.install("skills", d, force=True)
            self.assertIn("keel-ship", again)


class TestInstallAll(unittest.TestCase):
    def test_installs_both_surfaces(self):
        with tempfile.TemporaryDirectory() as d:
            results = install.install_all(d)
            self.assertEqual(set(results), set(install.TARGETS))
            self.assertIn("ship.md", results["claude"][0])
            self.assertIn("keel-ship", results["skills"][0])
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
