"""Unit tests for `keel install-adapter` (packaged command adapters → two surfaces)."""

import tempfile
import unittest
from pathlib import Path

import yaml

from keel import install

CONSUMER_SPECIFIC_TERMS = (
    "smartinventory",
    "eventoid",
    "firebase",
    "realm",
    "billing",
    "crashlytics",
    "play console",
    "adb",
    "espresso",
    "kover",
    "gradle",
)


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

    def test_generated_surface_contract_for_every_packaged_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            results = install.install_all(root)
            adapters = install.adapter_names()
            commands = [Path(name).stem for name in adapters]

            self.assertEqual(results["claude"], (adapters, []))
            self.assertEqual(results["skills"], ([f"keel-{cmd}" for cmd in commands], []))

            claude_files = sorted((root / install.CLAUDE_DIR).glob("*.md"))
            skill_files = sorted((root / install.SKILLS_DIR).glob("keel-*/SKILL.md"))
            self.assertEqual([p.name for p in claude_files], adapters)
            self.assertEqual({p.parent.name for p in skill_files}, {f"keel-{c}" for c in commands})

            for adapter in adapters:
                with self.subTest(adapter=adapter):
                    command = Path(adapter).stem
                    source = install.ADAPTERS / adapter
                    source_text = source.read_text(encoding="utf-8")
                    source_meta, source_body = install._split_frontmatter(source_text)

                    claude = root / install.CLAUDE_DIR / adapter
                    skill = root / install.SKILLS_DIR / f"keel-{command}" / "SKILL.md"

                    self.assertEqual(claude.read_text(encoding="utf-8"), source_text)
                    skill_meta, skill_body = install._split_frontmatter(
                        skill.read_text(encoding="utf-8")
                    )
                    self.assertEqual(skill_meta["name"], f"keel-{command}")
                    self.assertEqual(skill_meta["description"], source_meta["description"])
                    self.assertEqual(set(skill_meta), {"name", "description"})
                    self.assertNotIn("argument-hint", skill_meta)
                    self.assertNotIn("allowed-tools", skill_meta)
                    self.assertIn(source_body.strip().splitlines()[0], skill_body)
                    self.assertIn("`.keel/project.yaml`", skill_body)
                    self.assertGreater(len(skill_body.strip()), len(source_body.strip()))

    def test_generated_surfaces_are_idempotent_and_force_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = install.install_all(root)
            commands = [Path(name).stem for name in install.adapter_names()]
            self.assertTrue(first["claude"][0])
            self.assertTrue(first["skills"][0])

            claude_ship = root / install.CLAUDE_DIR / "ship.md"
            skill_ship = root / install.SKILLS_DIR / "keel-ship" / "SKILL.md"
            claude_ship.write_text("local claude edit\n", encoding="utf-8")
            skill_ship.write_text("local skill edit\n", encoding="utf-8")

            second = install.install_all(root)
            self.assertEqual(second["claude"], ([], install.adapter_names()))
            self.assertEqual(second["skills"], ([], [f"keel-{cmd}" for cmd in commands]))
            self.assertEqual(claude_ship.read_text(encoding="utf-8"), "local claude edit\n")
            self.assertEqual(skill_ship.read_text(encoding="utf-8"), "local skill edit\n")

            forced = install.install_all(root, force=True)
            self.assertEqual(forced["claude"], (install.adapter_names(), []))
            self.assertEqual(forced["skills"], ([f"keel-{cmd}" for cmd in commands], []))
            self.assertNotEqual(claude_ship.read_text(encoding="utf-8"), "local claude edit\n")
            self.assertNotEqual(skill_ship.read_text(encoding="utf-8"), "local skill edit\n")

    def test_generated_surfaces_remain_consumer_neutral(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all(root)
            offenders: list[str] = []
            for path in sorted(
                list((root / ".claude").rglob("*.md"))
                + list((root / ".agents").rglob("SKILL.md"))
            ):
                text = path.read_text(encoding="utf-8").lower()
                for term in CONSUMER_SPECIFIC_TERMS:
                    if term in text:
                        offenders.append(f"{path.relative_to(root)}: {term}")

            self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
