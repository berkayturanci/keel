"""Unit tests for keel project-config loading + validation."""

import copy
import unittest
from pathlib import Path

from keel import config as cfg
from keel import contracts

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


class TestEvidenceGateLabel(unittest.TestCase):
    def test_default_label(self):
        self.assertEqual(
            cfg.parse_config(copy.deepcopy(VALID)).knobs.evidence_gate_label, "keel:ship"
        )

    def test_custom_label_parsed(self):
        data = copy.deepcopy(VALID)
        data["knobs"]["evidence_gate_label"] = "ship-me"
        self.assertEqual(cfg.parse_config(data).knobs.evidence_gate_label, "ship-me")

    def test_empty_label_rejected(self):
        # The legacy arming label is still accepted as an arming signal; keep it
        # non-empty so its configured meaning remains deterministic.
        data = copy.deepcopy(VALID)
        data["knobs"]["evidence_gate_label"] = ""
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
            "capture": {
                "enabled": True,
                "mode": "extension",
                "learning": {
                    "enabled": True,
                    "mode": "create-learning",
                    "reason": "new invariant",
                    "dedupe": {"enabled": True},
                },
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
        self.assertEqual(config.policy_pack["capture"]["learning"]["mode"],
                         "create-learning")

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

    def test_evidence_gate_label_defaults_and_parses(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        self.assertEqual(config.knobs.evidence_gate_label, "keel:ship")

        data = copy.deepcopy(VALID)
        data["knobs"]["evidence_gate_label"] = "ship-me"
        overridden = cfg.parse_config(data)
        self.assertEqual(overridden.knobs.evidence_gate_label, "ship-me")

    def test_evidence_gate_label_changes_config_hash(self):
        base = cfg.parse_config(copy.deepcopy(VALID))
        data = copy.deepcopy(VALID)
        data["knobs"]["evidence_gate_label"] = "ship-me"
        changed = cfg.parse_config(data)
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_require_distinct_vendors_defaults_off_and_parses(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        self.assertFalse(config.knobs.evidence_require_distinct_vendors)

        data = copy.deepcopy(VALID)
        data["knobs"]["evidence_require_distinct_vendors"] = True
        overridden = cfg.parse_config(data)
        self.assertTrue(overridden.knobs.evidence_require_distinct_vendors)

    def test_require_distinct_vendors_changes_config_hash(self):
        base = cfg.parse_config(copy.deepcopy(VALID))
        data = copy.deepcopy(VALID)
        data["knobs"]["evidence_require_distinct_vendors"] = True
        changed = cfg.parse_config(data)
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_require_distinct_vendors_rejects_non_boolean(self):
        bad = copy.deepcopy(VALID)
        bad["knobs"]["evidence_require_distinct_vendors"] = "yes"
        with self.assertRaises(cfg.ConfigError):
            cfg.parse_config(bad)

    def test_gate_timeout_defaults_to_600_and_parses(self):
        config = cfg.parse_config(copy.deepcopy(VALID))
        self.assertEqual(config.knobs.gate_timeout_s, 600)  # today's behaviour preserved

        data = copy.deepcopy(VALID)
        data["knobs"]["gate_timeout_s"] = 3600
        self.assertEqual(cfg.parse_config(data).knobs.gate_timeout_s, 3600)

    def test_gate_timeout_changes_config_hash(self):
        base = cfg.parse_config(copy.deepcopy(VALID))
        data = copy.deepcopy(VALID)
        data["knobs"]["gate_timeout_s"] = 3600
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(cfg.parse_config(data)))

    def test_gate_timeout_rejects_non_integer(self):
        bad = copy.deepcopy(VALID)
        bad["knobs"]["gate_timeout_s"] = "3600"
        with self.assertRaises(cfg.ConfigError):
            cfg.parse_config(bad)

    def test_jury_timeout_defaults_to_600_and_parses(self):
        self.assertEqual(cfg.parse_config(copy.deepcopy(VALID)).knobs.jury_timeout_s, 600)
        data = copy.deepcopy(VALID)
        data["knobs"]["jury_timeout_s"] = 2400
        self.assertEqual(cfg.parse_config(data).knobs.jury_timeout_s, 2400)

    def test_jury_timeout_is_independent_of_gate_timeout(self):
        # The jury is a cross-vendor agent CLI, not a test command; raising one budget
        # must not move the other.
        data = copy.deepcopy(VALID)
        data["knobs"]["jury_timeout_s"] = 2400
        config = cfg.parse_config(data)
        self.assertEqual(config.knobs.jury_timeout_s, 2400)
        self.assertEqual(config.knobs.gate_timeout_s, 600)

    def test_jury_timeout_default_hashes_like_the_explicit_value(self):
        # Omitting the knob and writing 600 must be the same config, or every project
        # that has not adopted the knob gets a spurious cache/determinism churn.
        data = copy.deepcopy(VALID)
        data["knobs"]["jury_timeout_s"] = 600
        self.assertEqual(cfg.config_hash(cfg.parse_config(copy.deepcopy(VALID))),
                         cfg.config_hash(cfg.parse_config(data)))

    def test_jury_timeout_changes_config_hash(self):
        base = cfg.parse_config(copy.deepcopy(VALID))
        data = copy.deepcopy(VALID)
        data["knobs"]["jury_timeout_s"] = 2400
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(cfg.parse_config(data)))

    def test_jury_timeout_rejects_invalid_values(self):
        for value in (0, -1, "600", True):
            bad = copy.deepcopy(VALID)
            bad["knobs"]["jury_timeout_s"] = value
            with self.assertRaises(cfg.ConfigError, msg=repr(value)):
                cfg.parse_config(bad)

    def test_gate_timeout_rejects_zero_and_negative(self):
        for value in (0, -1):
            bad = copy.deepcopy(VALID)
            bad["knobs"]["gate_timeout_s"] = value
            with self.assertRaises(cfg.ConfigError):
                cfg.parse_config(bad)

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


class TestDelegateProfiles(unittest.TestCase):
    """`knobs.delegate_profiles` — generic delegate vendors (issue #659)."""

    @staticmethod
    def _with(profiles: object) -> dict:
        data = copy.deepcopy(VALID)
        data["knobs"]["delegate_profiles"] = profiles
        return data

    def test_absent_by_default(self):
        self.assertEqual(cfg.parse_config(copy.deepcopy(VALID)).knobs.delegate_profiles, {})

    def test_full_profile_parses(self):
        config = cfg.parse_config(self._with({
            "cursor": {
                "vendor": "cli",
                "command": "cursor-agent",
                "prompt_mode": "arg",
                "model": "composer-1",
            },
        }))
        profile = config.knobs.delegate_profiles["cursor"]
        self.assertEqual(profile, cfg.DelegateProfile(vendor="cli", command="cursor-agent",
                                              prompt_mode="arg", model="composer-1"))

    def test_prompt_mode_defaults_to_stdin(self):
        # The existing "pipe via stdin" guidance stays the norm; `arg` is the opt-in.
        config = cfg.parse_config(self._with({
            "gemini-cli": {"vendor": "cli", "command": "gemini"},
        }))
        profile = config.knobs.delegate_profiles["gemini-cli"]
        self.assertEqual(profile.prompt_mode, cfg.DEFAULT_PROMPT_MODE)
        self.assertEqual(profile.prompt_mode, "stdin")
        self.assertIsNone(profile.model)

    def test_explicit_null_model_parses(self):
        config = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "model": None},
        }))
        self.assertIsNone(config.knobs.delegate_profiles["cursor"].model)

    def test_unknown_vendor_rejected(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({"router": {"vendor": "openai-compatible"}}))
        message = str(ctx.exception)
        self.assertIn("unknown delegate vendor 'openai-compatible'", message)
        self.assertIn("valid: cli", message)

    def test_cli_without_command_rejected(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({"cursor": {"vendor": "cli"}}))
        self.assertIn("requires a non-empty 'command'", str(ctx.exception))

    def test_cli_with_empty_command_rejected(self):
        # minLength in the schema and the semantic check both refuse an empty command.
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({"cursor": {"vendor": "cli", "command": ""}}))
        self.assertIn("requires a non-empty 'command'", str(ctx.exception))

    def test_invalid_prompt_mode_rejected(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({
                "cursor": {"vendor": "cli", "command": "cursor-agent", "prompt_mode": "pipe"},
            }))
        message = str(ctx.exception)
        self.assertIn("invalid prompt_mode 'pipe'", message)
        self.assertIn("valid: stdin, arg", message)

    def test_shadowing_a_builtin_vendor_rejected(self):
        for name in ("claude", "codex", "agy", "ollama", "anthropic-api", "openai-api"):
            with self.subTest(name=name):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(self._with({
                        name: {"vendor": "cli", "command": "whatever"},
                    }))
                message = str(ctx.exception)
                self.assertIn(f"profile name {name!r} shadows a built-in delegate vendor", message)
                self.assertIn("rename the profile", message)

    def test_name_with_a_colon_rejected(self):
        """``--delegate`` splits on the first colon, so such a name is unreachable.

        ``--delegate cursor:pro`` would resolve to profile ``cursor`` with model
        ``pro`` — never to a profile literally named ``cursor:pro``. Accepting the
        name would leave a config entry that silently never runs.
        """
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({
                "cursor:pro": {"vendor": "cli", "command": "cursor-agent"},
            }))
        message = str(ctx.exception)
        self.assertIn("may not contain ':'", message)
        self.assertIn("could never be selected", message)

    def test_blank_name_rejected(self):
        for name in ("", "   "):
            with self.subTest(name=name):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(self._with({
                        name: {"vendor": "cli", "command": "cursor-agent"},
                    }))
                self.assertIn("may not be empty or blank", str(ctx.exception))

    def test_non_string_key_reported_not_crashed(self):
        """YAML keys are not necessarily strings, and the schema never checks key types.

        SafeLoader resolves a bare `on:` to True, `2:` to 2 and `~:` to None. Reaching
        the string checks with one of those raised an uncaught AttributeError straight
        out of `keel validate`.
        """
        for key, kind in ((True, "bool"), (2, "int"), (None, "NoneType")):
            with self.subTest(key=key):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(self._with({
                        key: {"vendor": "cli", "command": "cursor-agent"},
                    }))
                message = str(ctx.exception)
                self.assertIn(f"is {kind}, not a string", message)
                self.assertIn("quote the key", message)

    def test_args_carry_standing_flags(self):
        """A real CLI needs standing flags; `command` is one executable, not a shell line.

        The proposal's own field report drove `cursor-agent -p --model X --force`.
        Without `args` an operator would have to fold `-p --force` into `command`,
        which keel would then treat as a single filename.
        """
        parsed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent",
                       "args": ["-p", "--force"]},
            "plain": {"vendor": "cli", "command": "gemini"},
        }))
        profiles = parsed.knobs.delegate_profiles
        self.assertEqual(profiles["cursor"].args, ("-p", "--force"))
        self.assertEqual(profiles["plain"].args, ())

    def test_args_change_the_config_hash(self):
        base = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
        }))
        changed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"]},
        }))
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_review_args_separate_the_reviewer_from_the_implementer(self):
        """s7 wants findings only, but `args` carries write-enabling implementer flags.

        `cursor-agent`'s `--force` approves edits non-interactively — exactly wrong for a
        reviewer. keel cannot enforce read-only on an arbitrary CLI, so this is the
        operator's lever; `role_args` is where the choice is made.
        """
        parsed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent",
                       "args": ["-p", "--force"], "review_args": ["-p"]},
            "shared": {"vendor": "cli", "command": "gemini", "args": ["-p"]},
        }))
        profiles = parsed.knobs.delegate_profiles
        self.assertEqual(profiles["cursor"].role_args(), ("-p", "--force"))
        self.assertEqual(profiles["cursor"].role_args(review=True), ("-p",))
        # Unset falls back rather than emptying, so existing profiles keep working.
        self.assertIsNone(profiles["shared"].review_args)
        self.assertEqual(profiles["shared"].role_args(review=True), ("-p",))

    def test_empty_review_args_is_not_the_same_as_unset(self):
        parsed = cfg.parse_config(self._with({
            "bare": {"vendor": "cli", "command": "x", "args": ["-p"], "review_args": []},
        }))
        self.assertEqual(parsed.knobs.delegate_profiles["bare"].role_args(review=True), ())

    def test_no_profiles_does_not_appear_in_the_hashed_form(self):
        """An added optional field must not rotate config_hash for projects without it.

        Emitting `"delegate_profiles": {}` unconditionally changed the hash of every
        existing config, and every other hash test asserts only *relative* differences,
        so nothing would have caught it.
        """
        plain = cfg.parse_config(copy.deepcopy(VALID))
        self.assertNotIn("delegate_profiles", cfg._canonical(plain)["knobs"])
        self.assertNotIn("delegate_profiles", contracts.project_as_dict(plain)["knobs"])

    def test_the_hashed_form_and_the_published_contract_agree(self):
        """One helper feeds both, so the two serialisations cannot drift apart."""
        parsed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"]},
        }))
        self.assertEqual(
            cfg._canonical(parsed)["knobs"]["delegate_profiles"],
            contracts.project_as_dict(parsed)["knobs"]["delegate_profiles"],
        )

    def test_review_args_change_the_config_hash(self):
        base = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"]},
        }))
        changed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"],
                       "review_args": []},
        }))
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_profile_named_after_its_vendor_rejected(self):
        """A profile called `cli` makes every attribution field say the same nothing."""
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({
                "cli": {"vendor": "cli", "command": "cursor-agent"},
            }))
        message = str(ctx.exception)
        self.assertIn("would make attribution ambiguous", message)
        self.assertIn("e.g. 'cursor'", message)

    def test_model_arg_defaults_and_overrides(self):
        parsed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
            "weird": {"vendor": "cli", "command": "weirdcli", "model_arg": "-m"},
        }))
        profiles = parsed.knobs.delegate_profiles
        # Without a way to spell model selection, the documented precedence would be
        # unimplementable for an arbitrary CLI.
        self.assertEqual(profiles["cursor"].model_arg, "--model")
        self.assertEqual(profiles["weird"].model_arg, "-m")

    def test_model_arg_changes_the_config_hash(self):
        base = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
        }))
        changed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "model_arg": "-m"},
        }))
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_missing_vendor_left_to_the_schema(self):
        # No semantic "unknown vendor None" noise on top of the schema's required-field error.
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({"cursor": {"command": "cursor-agent"}}))
        message = str(ctx.exception)
        self.assertIn("missing required property 'vendor'", message)
        self.assertNotIn("unknown delegate vendor", message)

    def test_non_object_profile_left_to_the_schema(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({"cursor": "cursor-agent"}))
        message = str(ctx.exception)
        self.assertIn("expected type object", message)
        self.assertNotIn("unknown delegate vendor", message)

    def test_non_object_block_left_to_the_schema(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with(["cursor"]))
        self.assertIn("knobs.delegate_profiles: expected type object", str(ctx.exception))

    def test_unknown_profile_field_rejected(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({
                "cursor": {"vendor": "cli", "command": "cursor-agent", "endpoint": "http://x"},
            }))
        self.assertIn("unknown property 'endpoint'", str(ctx.exception))

    def test_round_trips_through_the_project_contract(self):
        data = self._with({
            "gemini-cli": {"vendor": "cli", "command": "gemini", "prompt_mode": "arg"},
            "cursor": {"vendor": "cli", "command": "cursor-agent", "model": "composer-1"},
        })
        serialised = contracts.project_as_dict(cfg.parse_config(data))["knobs"]
        profiles = serialised["delegate_profiles"]
        self.assertEqual(list(profiles), ["cursor", "gemini-cli"])  # sorted, order-stable
        self.assertEqual(profiles["cursor"], {
            "vendor": "cli",
            "command": "cursor-agent",
            "args": [],
            "review_args": None,     # unset -> the reviewer role falls back to args
            "prompt_mode": "stdin",  # the default, made explicit on the way out
            "model": "composer-1",
            "model_arg": "--model",  # ditto: how the model actually reaches the CLI
        })
        self.assertEqual(profiles["gemini-cli"], {
            "vendor": "cli",
            "command": "gemini",
            "args": [],
            "review_args": None,
            "prompt_mode": "arg",
            "model": None,
            "model_arg": "--model",
        })
        # A round trip through the contract reparses to the same profiles.
        reparsed = self._with({name: dict(p) for name, p in profiles.items()})
        self.assertEqual(
            cfg.parse_config(reparsed).knobs.delegate_profiles,
            cfg.parse_config(data).knobs.delegate_profiles,
        )

    def test_profile_key_order_does_not_change_the_hash(self):
        one = self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
            "gemini-cli": {"vendor": "cli", "command": "gemini"},
        })
        two = self._with({
            "gemini-cli": {"vendor": "cli", "command": "gemini"},
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
        })
        self.assertEqual(
            cfg.config_hash(cfg.parse_config(one)), cfg.config_hash(cfg.parse_config(two))
        )

    def test_profiles_change_the_config_hash(self):
        base = cfg.parse_config(copy.deepcopy(VALID))
        changed = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
        }))
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_prompt_mode_changes_the_config_hash(self):
        stdin = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent"},
        }))
        arg = cfg.parse_config(self._with({
            "cursor": {"vendor": "cli", "command": "cursor-agent", "prompt_mode": "arg"},
        }))
        self.assertNotEqual(cfg.config_hash(stdin), cfg.config_hash(arg))


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
