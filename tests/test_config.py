"""Unit tests for keel project-config loading + validation."""

import copy
import unittest
from pathlib import Path

from keel import config as cfg

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"

VALID = {
    "extends": "keel",
    "core_version": "^0.1",
    "base_branch": "main",
    "knobs": {"build_gate_cmd": "make test"},
}


class TestMergeWindowMode(unittest.TestCase):
    def test_default_is_freeze(self):
        self.assertEqual(cfg.parse_config(copy.deepcopy(VALID)).merge_window_mode, "freeze")

    def test_pause_parsed(self):
        data = copy.deepcopy(VALID)
        data["merge_window_mode"] = "pause"
        self.assertEqual(cfg.parse_config(data).merge_window_mode, "pause")

    def test_invalid_mode_rejected(self):
        data = copy.deepcopy(VALID)
        data["merge_window_mode"] = "nope"
        with self.assertRaises(cfg.ConfigError):
            cfg.parse_config(data)


class TestSeedConfigs(unittest.TestCase):
    """Every shipped projects/*.yaml must be valid against the schema."""

    def test_all_seed_configs_load(self):
        files = sorted(PROJECTS_DIR.glob("*.yaml"))
        self.assertTrue(files, "no seed configs found")
        for path in files:
            with self.subTest(config=path.name):
                config = cfg.load_config(path)
                self.assertEqual(config.extends, "keel")
                self.assertTrue(config.knobs.build_gate_cmd)

    def test_example_flutter_has_no_android_leak(self):
        config = cfg.load_config(PROJECTS_DIR / "example-flutter.yaml")
        blob = (config.knobs.build_gate_cmd + " " + (config.knobs.lint_cmd or "")
                + " " + " ".join(config.knobs.tier3_globs)
                + " " + " ".join(config.knobs.implementer_agents.values())).lower()
        for foreign in ("gradle", "kotlin", "realm", "android"):
            self.assertNotIn(foreign, blob, f"foreign token {foreign!r} leaked in")

    def test_example_flutter_registers_design_parity_lego(self):
        config = cfg.load_config(PROJECTS_DIR / "example-flutter.yaml")
        self.assertIn("design-parity.md", config.slot("tester"))
        self.assertIn("design-parity-gate.md", config.slot("pre-merge"))


class TestParse(unittest.TestCase):
    def test_minimal_valid(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        self.assertEqual(config.base_branch, "main")
        self.assertEqual(config.extensions_dir, cfg.DEFAULT_EXTENSIONS_DIR)
        self.assertEqual(config.gates, ())
        self.assertEqual(config.knobs.required_capabilities, ())
        self.assertEqual(config.knobs.optional_capabilities, ())

    def test_capability_knobs_parse(self):
        data = copy.deepcopy(VALID)
        data["knobs"]["required_capabilities"] = ["shell"]
        data["knobs"]["optional_capabilities"] = ["gh"]
        config = cfg.parse_config(data)
        self.assertEqual(config.knobs.required_capabilities, ("shell",))
        self.assertEqual(config.knobs.optional_capabilities, ("gh",))

    def test_unknown_capability_rejected(self):
        bad = copy.deepcopy(VALID)
        bad["knobs"]["required_capabilities"] = ["bogus"]
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("unknown capability", str(ctx.exception))

    def test_non_dict_rejected(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(["not", "a", "dict"], source="x.yaml")
        self.assertIn("expected an object", str(ctx.exception))

    def test_missing_required(self):
        bad = copy.deepcopy(VALID)
        del bad["base_branch"]
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("base_branch", str(ctx.exception))

    def test_unknown_top_level_key_rejected(self):
        bad = copy.deepcopy(VALID)
        bad["bogus"] = 1
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("unknown property 'bogus'", str(ctx.exception))

    def test_extends_must_be_keel(self):
        bad = copy.deepcopy(VALID)
        bad["extends"] = "something-else"
        with self.assertRaises(cfg.ConfigError):
            cfg.parse_config(bad)

    def test_bad_merge_window_pattern(self):
        bad = copy.deepcopy(VALID)
        bad["merge_window"] = "7-1"
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("merge_window", str(ctx.exception))

    def test_unknown_extension_slot_rejected(self):
        bad = copy.deepcopy(VALID)
        bad["extensions"] = {"not-a-slot": ["x.md"]}
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("not-a-slot", str(ctx.exception))

    def test_knobs_require_build_gate_cmd(self):
        bad = copy.deepcopy(VALID)
        bad["knobs"] = {"lint_cmd": "x"}
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("build_gate_cmd", str(ctx.exception))


class TestSlots(unittest.TestCase):
    def test_slot_accessor_empty(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        self.assertEqual(config.slot("tester"), ())

    def test_slot_rejects_unknown_name(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        with self.assertRaises(KeyError):
            config.slot("nope")


class TestConfigHash(unittest.TestCase):
    def test_deterministic(self):
        a = cfg.parse_config(copy.deepcopy(VALID))
        b = cfg.parse_config(copy.deepcopy(VALID))
        self.assertEqual(cfg.config_hash(a), cfg.config_hash(b))

    def test_changes_with_content(self):
        a = cfg.parse_config(copy.deepcopy(VALID))
        other = copy.deepcopy(VALID)
        other["base_branch"] = "develop"
        b = cfg.parse_config(other)
        self.assertNotEqual(cfg.config_hash(a), cfg.config_hash(b))

    def test_key_order_independent(self):
        reordered = {
            "knobs": {"build_gate_cmd": "make test"},
            "base_branch": "main",
            "core_version": "^0.1",
            "extends": "keel",
        }
        self.assertEqual(
            cfg.config_hash(cfg.parse_config(copy.deepcopy(VALID))),
            cfg.config_hash(cfg.parse_config(reordered)),
        )


if __name__ == "__main__":
    unittest.main()
