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
            text = (Path(d) / ".claude/commands/keel/ship.md").read_text(encoding="utf-8")
            body, marker = install._split_marker(text)
            self.assertIn("# /keel:ship", body)
            self.assertEqual(marker["surface"], "claude")
            self.assertEqual(marker["command"], "ship")

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
            body, marker = install._split_marker(sk.read_text(encoding="utf-8"))
            meta = yaml.safe_load(body.split("---")[1])
            self.assertEqual(meta["name"], "keel-ship")
            self.assertEqual(marker["surface"], "skills")
            self.assertEqual(marker["command"], "ship")

    def test_skips_existing_then_force(self):
        with tempfile.TemporaryDirectory() as d:
            install.install("skills", d)
            installed, skipped = install.install("skills", d)
            self.assertEqual(installed, [])
            self.assertIn("keel-ship", skipped)
            again, _ = install.install("skills", d, force=True)
            self.assertIn("keel-ship", again)


class TestLegacyWrappers(unittest.TestCase):
    def test_parity_ready_commands_reads_matrix_statuses(self):
        matrix = """
| Legacy command | Keel command | Status |
|---|---|---|
| `bad` | `/keel:bad` |
| `ship` | `/keel:ship` | `parity-proven` |
| `wrap` | `/keel:wrap` | `in-progress` |
| `custom` | `/keel:triage` | `deferred` |
"""
        self.assertEqual(install.parity_ready_commands(matrix), {"ship", "triage"})

    def test_default_legacy_mappings_are_one_to_one(self):
        mappings = install.default_legacy_mappings()
        self.assertEqual(mappings["ship"], "ship")
        self.assertEqual(mappings["morning"], "morning")
        self.assertIn("review-cycle", mappings)

    def test_renders_thin_claude_wrapper(self):
        body = install.render_legacy_claude_wrapper("ship", "ship")
        self.assertIn("# /ship", body)
        self.assertIn("`/keel:ship`", body)
        self.assertIn("--live --json", body)
        self.assertIn("dry-run", body)
        self.assertIn("jury/no-jury", body)
        self.assertIn("review-comment mode", body)
        self.assertIn("PR targeting", body)
        self.assertIn("Do not duplicate", body)

    def test_renders_thin_skill_wrapper(self):
        body = install.render_legacy_skill_wrapper("ship", "ship")
        meta, skill_body = install._split_frontmatter(body)
        self.assertEqual(meta["name"], "source-command-ship")
        self.assertIn("keel-ship", skill_body)
        self.assertIn("Preserve the user's original issue or PR target", skill_body)
        self.assertIn("Delegate to the `keel-ship` skill", skill_body)

    def test_installs_legacy_wrappers_only_for_ready_commands(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "not parity-ready"):
                install.install_legacy_wrappers(
                    "claude",
                    d,
                    mappings={"ship": "ship", "wrap": "wrap"},
                    ready_commands={"ship"},
                )

            installed, skipped = install.install_legacy_wrappers(
                "claude",
                d,
                mappings={"ship": "ship"},
                ready_commands={"ship"},
            )
            self.assertEqual(installed, ["ship.md"])
            self.assertEqual(skipped, [])
            path = Path(d) / ".claude/commands/ship.md"
            body, marker = install._split_marker(path.read_text(encoding="utf-8"))
            self.assertIn("`/keel:ship`", body)
            self.assertEqual(marker["surface"], "legacy-claude")
            self.assertEqual(marker["command"], "ship")

    def test_legacy_wrapper_validation_rejects_bad_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(KeyError, "codex"):
                install.install_legacy_wrappers("codex", d, mappings={"ship": "ship"})
            with self.assertRaisesRegex(ValueError, "non-empty"):
                install.install_legacy_wrappers("claude", d, mappings={"": "ship"})
            with self.assertRaisesRegex(ValueError, "unknown keel command"):
                install.install_legacy_wrappers("claude", d, mappings={"ship": "missing"})

    def test_installs_all_legacy_wrapper_surfaces_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            results = install.install_all_legacy_wrappers(
                root,
                mappings={"ship": "ship"},
                ready_commands={"ship"},
            )
            self.assertEqual(results["claude"], (["ship.md"], []))
            self.assertEqual(results["skills"], (["source-command-ship"], []))
            self.assertTrue((root / ".claude/commands/ship.md").exists())
            self.assertTrue((root / ".agents/skills/source-command-ship/SKILL.md").exists())

            (root / ".claude/commands/ship.md").write_text("local legacy body\n",
                                                            encoding="utf-8")
            skipped = install.install_all_legacy_wrappers(
                root,
                mappings={"ship": "ship"},
                ready_commands={"ship"},
            )
            self.assertEqual(skipped["claude"], ([], ["ship.md"]))
            self.assertEqual((root / ".claude/commands/ship.md").read_text(encoding="utf-8"),
                             "local legacy body\n")

            forced = install.install_all_legacy_wrappers(
                root,
                mappings={"ship": "ship"},
                ready_commands={"ship"},
                force=True,
            )
            self.assertEqual(forced["claude"], (["ship.md"], []))
            self.assertIn("`/keel:ship`",
                          (root / ".claude/commands/ship.md").read_text(encoding="utf-8"))

    def test_legacy_wrappers_remain_consumer_neutral(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all_legacy_wrappers(
                root,
                mappings={"ship": "ship"},
                ready_commands={"ship"},
            )
            offenders: list[str] = []
            for path in sorted(
                list((root / ".claude").rglob("*.md"))
                + list((root / ".agents").rglob("SKILL.md"))
            ):
                body, _metadata = install._split_marker(path.read_text(encoding="utf-8"))
                text = body.lower()
                for term in CONSUMER_SPECIFIC_TERMS:
                    if term in text:
                        offenders.append(f"{path.relative_to(root)}: {term}")

            self.assertEqual(offenders, [])


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

                    claude_body, claude_marker = install._split_marker(
                        claude.read_text(encoding="utf-8")
                    )
                    self.assertEqual(claude_body, source_text.rstrip() + "\n")
                    self.assertEqual(claude_marker["source_sha256"], install._sha256(source_text))
                    skill_meta, skill_body = install._split_frontmatter(
                        install._split_marker(skill.read_text(encoding="utf-8"))[0]
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
                body, _metadata = install._split_marker(path.read_text(encoding="utf-8"))
                text = body.lower()
                for term in CONSUMER_SPECIFIC_TERMS:
                    if term in text:
                        offenders.append(f"{path.relative_to(root)}: {term}")

            self.assertEqual(offenders, [])

    def test_adapter_status_reports_current_missing_unknown_and_modified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all(root)
            current = install.adapter_status("all", root)
            self.assertTrue(all(row.status == "current"
                                for rows in current.values() for row in rows))

            (root / install.CLAUDE_DIR / "ship.md").unlink()
            (root / install.SKILLS_DIR / "keel-wrap" / "SKILL.md").write_text(
                "hand-written local wrapper\n", encoding="utf-8"
            )
            triage = root / install.CLAUDE_DIR / "triage.md"
            triage.write_text(triage.read_text(encoding="utf-8").replace(
                "# /keel:triage", "# locally edited /keel:triage"
            ), encoding="utf-8")

            rows = install.adapter_status("all", root)
            by_key = {(row.surface, row.name): row for rs in rows.values() for row in rs}
            self.assertEqual(by_key[("claude", "ship.md")].status, "missing")
            self.assertEqual(by_key[("skills", "keel-wrap")].status, "unknown")
            self.assertEqual(by_key[("claude", "triage.md")].status, "locally-modified")

    def test_update_adapter_dry_run_and_write_preserve_local_files(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src_dir:
            root = Path(d)
            src = Path(src_dir)
            for source in install.ADAPTERS.glob("*.md"):
                src.joinpath(source.name).write_text(source.read_text(encoding="utf-8"),
                                                    encoding="utf-8")
            install.install_all(root, _src=src)

            ship_source = src / "ship.md"
            ship_source.write_text(
                ship_source.read_text(encoding="utf-8") + "\nExtra packaged line.\n",
                encoding="utf-8",
            )
            project_yaml = root / ".keel/project.yaml"
            extension = root / ".keel/extensions/local.md"
            wrapper = root / ".agents/skills/project-command/SKILL.md"
            for path, text in (
                (project_yaml, "project config\n"),
                (extension, "project extension\n"),
                (wrapper, "local wrapper\n"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

            dry = install.update_adapters("all", root, dry_run=True, _src=src)
            by_key = {(row.surface, row.name): row for rows in dry.values() for row in rows}
            self.assertEqual(by_key[("claude", "ship.md")].status, "would-update")
            self.assertNotIn("Extra packaged line.",
                             (root / install.CLAUDE_DIR / "ship.md").read_text(encoding="utf-8"))

            updated = install.update_adapters("all", root, _src=src)
            by_key = {(row.surface, row.name): row for rows in updated.values() for row in rows}
            self.assertEqual(by_key[("claude", "ship.md")].status, "updated")
            self.assertEqual(by_key[("skills", "keel-ship")].status, "updated")
            self.assertIn("Extra packaged line.",
                          (root / install.CLAUDE_DIR / "ship.md").read_text(encoding="utf-8"))
            self.assertEqual(project_yaml.read_text(encoding="utf-8"), "project config\n")
            self.assertEqual(extension.read_text(encoding="utf-8"), "project extension\n")
            self.assertEqual(wrapper.read_text(encoding="utf-8"), "local wrapper\n")

    def test_update_adapter_refuses_unknown_and_locally_modified_files(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src_dir:
            root = Path(d)
            src = Path(src_dir)
            for source in install.ADAPTERS.glob("*.md"):
                src.joinpath(source.name).write_text(source.read_text(encoding="utf-8"),
                                                    encoding="utf-8")
            install.install_all(root, _src=src)
            (root / install.CLAUDE_DIR / "ship.md").write_text(
                "local command without marker\n", encoding="utf-8"
            )
            skill = root / install.SKILLS_DIR / "keel-ship" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8").replace(
                "# keel-ship", "# locally edited keel-ship"
            ), encoding="utf-8")

            ship_source = src / "ship.md"
            ship_source.write_text(ship_source.read_text(encoding="utf-8") + "\nUpdated.\n",
                                   encoding="utf-8")
            rows = install.update_adapters("all", root, _src=src)
            by_key = {(row.surface, row.name): row for rs in rows.values() for row in rs}
            self.assertEqual(by_key[("claude", "ship.md")].status, "unknown")
            self.assertEqual(by_key[("skills", "keel-ship")].status, "locally-modified")
            self.assertEqual((root / install.CLAUDE_DIR / "ship.md").read_text(encoding="utf-8"),
                             "local command without marker\n")
            self.assertIn("# locally edited keel-ship", skill.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
