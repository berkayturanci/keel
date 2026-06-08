"""Unit tests for keel project-config loading + validation."""

import copy
import unittest
from pathlib import Path

from keel import config as cfg

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"
DOGFOOD_CONFIG = Path(__file__).resolve().parent.parent / ".keel/project.yaml"

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

    def test_seed_policy_packs_represent_project_policy(self):
        for name in ("keel.yaml", "example-flutter.yaml", "example-android.yaml"):
            with self.subTest(config=name):
                config = cfg.load_config(PROJECTS_DIR / name)
                pack = config.policy_pack
                self.assertTrue(pack["name"])
                self.assertIn("labels", pack)
                self.assertIn("risk_rules", pack)
                self.assertIn("test_groups", pack)
                self.assertIn("docs", pack)
                self.assertIn("health_providers", pack)
                self.assertIn("scan", pack)
                self.assertIn("command_routing", pack)

    def test_keel_seed_matches_dogfood_config(self):
        seed = (PROJECTS_DIR / "keel.yaml").read_text(encoding="utf-8")
        dogfood = DOGFOOD_CONFIG.read_text(encoding="utf-8")
        self.assertEqual(seed, dogfood)

    def test_example_android_declares_project_commands(self):
        config = cfg.load_config(PROJECTS_DIR / "example-android.yaml")
        self.assertIn("project_commands", config.policy_pack)
        self.assertIn("android-build", config.policy_pack["project_commands"])


class TestParse(unittest.TestCase):
    def test_minimal_valid(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        self.assertEqual(config.base_branch, "main")
        self.assertEqual(config.extensions_dir, cfg.DEFAULT_EXTENSIONS_DIR)
        self.assertEqual(config.consent_mode, "explicit")
        self.assertEqual(config.gates, ())
        self.assertEqual(config.knobs.required_capabilities, ())
        self.assertEqual(config.knobs.optional_capabilities, ())
        self.assertEqual(config.policy_pack, {})
        self.assertEqual(config.automation.approved_scopes, ())
        self.assertIsNone(config.automation.operator)

    def test_automation_standing_approval_parses(self):
        data = copy.deepcopy(VALID)
        data["automation"] = {
            "approved_scopes": ["filesystem", "git", "github"],
            "operator": "automation:nightly",
        }
        config = cfg.parse_config(data)
        self.assertEqual(config.automation.approved_scopes, ("filesystem", "git", "github"))
        self.assertEqual(config.automation.operator, "automation:nightly")

    def test_consent_mode_parses(self):
        data = copy.deepcopy(VALID)
        data["consent_mode"] = "agent"
        self.assertEqual(cfg.parse_config(data).consent_mode, "agent")

    def test_invalid_consent_mode_rejected(self):
        bad = copy.deepcopy(VALID)
        bad["consent_mode"] = "maybe"
        with self.assertRaises(cfg.ConfigError):
            cfg.parse_config(bad)

    def test_automation_unknown_scope_parses_for_consent_preflight(self):
        bad = copy.deepcopy(VALID)
        bad["automation"] = {"approved_scopes": ["filesystem", "bogus"]}
        config = cfg.parse_config(bad)
        self.assertEqual(config.automation.approved_scopes, ("bogus", "filesystem"))

    def test_policy_pack_parses_project_policy_contract(self):
        data = copy.deepcopy(VALID)
        data["policy_pack"] = {
            "name": "example-service",
            "labels": {
                "status": ["status:backlog", "status:done"],
                "role": ["app", "service"],
            },
            "status_transitions": {"done": "status:done"},
            "risk_rules": [{
                "id": "data-migration",
                "paths": ["migrations/**"],
                "required_gates": ["migration-check"],
                "review_additions": ["Check rollback safety."],
                "docs_required": True,
            }],
            "test_groups": {
                "app": {
                    "command": "./tools/test-app",
                    "paths": ["src/app/**"],
                    "reports": ["reports/app-tests/"],
                    "required_capabilities": ["shell"],
                },
            },
            "docs": {
                "required_paths": ["docs/**"],
                "allow_none_reasons": ["No operator-facing behavior changed."],
                "impact_required": True,
            },
            "health_providers": {
                "service": {"kind": "project-command", "command": ".keel/health/service"},
            },
            "scan": {
                "areas": {"app": ["src/app/**"], "migrations": ["migrations/**"]},
                "active_branch_patterns": ["feature/**", "fix/**"],
                "issue_labels": {
                    "regression": ["type:bug"],
                    "review-all-day": ["review-finding"],
                },
                "near_text_similarity": 0.6,
                "batch_threshold": 5,
                "large_diff_max_bytes": 200000,
            },
            "command_routing": {
                "smoke": {
                    "agent_role": "app",
                    "paths": ["src/app/**"],
                    "required_capabilities": ["shell"],
                    "side_effects": ["report_write"],
                    "dry_run_safe": True,
                },
            },
            "project_commands": {
                "device-smoke": {
                    "command": ".keel/commands/device-smoke",
                    "description": "Run device smoke checks.",
                    "agent_role": "app",
                    "paths": ["src/app/**"],
                    "required_capabilities": ["shell", "adb"],
                    "optional_capabilities": ["browser", "firebase"],
                    "side_effects": ["report_write"],
                    "dry_run_safe": False,
                },
            },
            "reports": {"morning": "reports/morning/"},
            "review": {
                "additions": ["Check rollout notes."],
                "required_sections": ["Testing", "Docs Impact"],
            },
        }
        config = cfg.parse_config(data)
        self.assertEqual(config.policy_pack["name"], "example-service")
        self.assertEqual(config.policy_pack["labels"]["role"], ["app", "service"])
        self.assertEqual(config.policy_pack["risk_rules"][0]["id"], "data-migration")
        self.assertEqual(config.policy_pack["test_groups"]["app"]["command"],
                         "./tools/test-app")
        self.assertEqual(config.policy_pack["scan"]["areas"]["app"], ["src/app/**"])
        self.assertEqual(config.policy_pack["project_commands"]["device-smoke"]["command"],
                         ".keel/commands/device-smoke")

    def test_policy_pack_required_fields_fail_validation(self):
        bad = copy.deepcopy(VALID)
        bad["policy_pack"] = {"labels": {"status": ["status:done"]}}
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("policy_pack", str(ctx.exception))
        self.assertIn("name", str(ctx.exception))

    def test_policy_pack_rejects_unknown_nested_fields(self):
        bad = copy.deepcopy(VALID)
        bad["policy_pack"] = {
            "name": "example",
            "risk_rules": [{"id": "risk", "paths": ["src/**"], "bogus": True}],
        }
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("bogus", str(ctx.exception))

    def test_policy_pack_capabilities_use_runtime_vocabulary(self):
        bad = copy.deepcopy(VALID)
        bad["policy_pack"] = {
            "name": "example",
            "test_groups": {"app": {"command": "./test", "required_capabilities": ["bogus"]}},
        }
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(bad)
        self.assertIn("policy_pack.test_groups.app.required_capabilities", str(ctx.exception))
        self.assertIn("unknown capability", str(ctx.exception))

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

    def test_changes_with_policy_pack_content(self):
        a = cfg.parse_config(copy.deepcopy(VALID))
        other = copy.deepcopy(VALID)
        other["policy_pack"] = {"name": "example"}
        b = cfg.parse_config(other)
        self.assertNotEqual(cfg.config_hash(a), cfg.config_hash(b))

    def test_automation_scope_order_independent(self):
        a = copy.deepcopy(VALID)
        b = copy.deepcopy(VALID)
        a["automation"] = {"approved_scopes": ["github", "filesystem", "git"]}
        b["automation"] = {"approved_scopes": ["filesystem", "git", "github"]}
        self.assertEqual(cfg.config_hash(cfg.parse_config(a)), cfg.config_hash(cfg.parse_config(b)))

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
