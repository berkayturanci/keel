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
        blob = (
            config.knobs.build_gate_cmd
            + " "
            + (config.knobs.lint_cmd or "")
            + " "
            + " ".join(config.knobs.tier3_globs)
            + " "
            + " ".join(config.knobs.implementer_agents.values())
        ).lower()
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
            "risk_rules": [
                {
                    "id": "data-migration",
                    "paths": ["migrations/**"],
                    "required_gates": ["migration-check"],
                    "review_additions": ["Check rollback safety."],
                    "docs_required": True,
                }
            ],
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
        self.assertEqual(config.policy_pack["test_groups"]["app"]["command"], "./tools/test-app")
        self.assertEqual(config.policy_pack["scan"]["areas"]["app"], ["src/app/**"])
        self.assertEqual(
            config.policy_pack["project_commands"]["device-smoke"]["command"],
            ".keel/commands/device-smoke",
        )
        self.assertEqual(config.policy_pack["capture"]["learning"]["mode"], "create-learning")

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
        self.assertEqual(
            cfg.config_hash(cfg.parse_config(copy.deepcopy(VALID))),
            cfg.config_hash(cfg.parse_config(data)),
        )

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


class TestOpenAICompatibleEndpointGuard(unittest.TestCase):
    """A config-supplied URL is a request-forgery primitive (#666).

    Ported from ai-jury's `_endpoint_issues`: loopback by default, remote only
    behind an environment opt-in, scheme allowlisted, malformed URL a clean error.
    """

    def _issues(self, endpoint, env=None):
        return cfg.endpoint_issues(endpoint, where="p.router", env=env or {})

    def test_loopback_is_allowed_without_any_opt_in(self):
        for endpoint in (
            "http://localhost:11434/v1",
            "http://127.0.0.1:8000/v1",
            "http://[::1]:8000/v1",
            "https://localhost/v1",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self._issues(endpoint), [])

    def test_a_remote_host_is_refused_by_default(self):
        issues = self._issues("https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(len(issues), 1)
        self.assertIn("is not loopback", issues[0])
        self.assertIn(cfg.ALLOW_REMOTE_ENDPOINT_ENV, issues[0])

    def test_cloud_metadata_is_refused_even_with_remote_opt_in(self):
        # Cloud metadata and link-local addresses are refused unconditionally for SSRF protection.
        for host in (
            "169.254.169.254",
            "169.254.0.1",
            "metadata.google.internal",
            "instance-data",
            "metadata",
        ):
            with self.subTest(host=host):
                issues = self._issues(
                    f"http://{host}/latest/meta-data/",
                    {cfg.ALLOW_REMOTE_ENDPOINT_ENV: "1"},
                )
                self.assertEqual(len(issues), 1)
                self.assertIn("cloud-metadata or link-local address", issues[0])

    def test_the_opt_in_lives_in_the_environment_not_in_config(self):
        for endpoint in (
            "https://openrouter.ai/api/v1/chat/completions",
            "http://8.8.8.8/v1/chat/completions",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertNotEqual(self._issues(endpoint), [])
                self.assertEqual(self._issues(endpoint, {cfg.ALLOW_REMOTE_ENDPOINT_ENV: "1"}), [])

    def test_non_http_schemes_are_refused(self):
        for endpoint in ("file:///etc/passwd", "ftp://host/x", "gopher://host/", "//host/x"):
            with self.subTest(endpoint=endpoint):
                issues = self._issues(endpoint, {cfg.ALLOW_REMOTE_ENDPOINT_ENV: "1"})
                self.assertEqual(len(issues), 1)
                self.assertIn("not allowed", issues[0])

    def test_a_scheme_check_precedes_the_host_check(self):
        # file:// has no host; reporting "not loopback" would be the wrong reason.
        issues = self._issues("file:///etc/passwd")
        self.assertIn("scheme", issues[0])

    def test_a_malformed_url_is_a_config_error_not_a_traceback(self):
        issues = self._issues("http://[::1")
        self.assertEqual(len(issues), 1)
        self.assertIn("not a valid URL", issues[0])

    def test_missing_or_blank_endpoint_is_reported(self):
        for endpoint in ("", "   ", None, 7):
            with self.subTest(endpoint=endpoint):
                self.assertIn("requires a non-empty 'endpoint'", self._issues(endpoint)[0])


class TestOpenAICompatibleKeyEnv(unittest.TestCase):
    """`api_key_env` takes a NAME. A value here would be published (#666)."""

    def _with(self, profiles):
        data = copy.deepcopy(VALID)
        data["knobs"]["delegate_profiles"] = profiles
        return data

    def _profile(self, **over):
        base = {
            "vendor": "openai-compatible",
            "endpoint": "http://localhost:1/v1",
            "api_key_env": "OPENAI_API_KEY",
        }
        base.update(over)
        return self._with({"router": base})

    def test_missing_api_key_env_rejected(self):
        data = self._profile()
        del data["knobs"]["delegate_profiles"]["router"]["api_key_env"]
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(data)
        self.assertIn("requires 'api_key_env'", str(ctx.exception))

    def test_a_pasted_key_is_rejected_by_shape(self):
        """The mistake worth catching: a secret where a variable name belongs.

        Real keys carry '-' or '.' (sk-proj-..., sk-ant-api03-...), which are not
        legal in an environment variable name, so the shape check refuses them.
        """
        for pasted in ("sk-proj-abc123", "sk-ant-api03-xyz", "ghp_abc.def", "1KEY"):
            with self.subTest(value=pasted):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(self._profile(api_key_env=pasted))
                self.assertIn("takes a name, not a key", str(ctx.exception))

    def test_an_allowlisted_key_name_is_accepted(self):
        for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "KEEL_DELEGATE_KEY_ACME"):
            with self.subTest(name=name):
                cfg.parse_config(self._profile(api_key_env=name))

    def test_an_arbitrary_env_var_name_is_refused(self):
        """#865 asked for an allowlist; only the denylist shipped (#929).

        `MY_KEY_2` and `_PRIVATE` are well-formed variable names and were
        accepted, which is the hole: the field names the variable whose *value*
        becomes an Authorization header, so a well-formed name is not the
        question — whether it was created to hold a model-API key is.
        """
        for name in (
            "MY_KEY_2",
            "_PRIVATE",
            "VAULT_TOKEN",
            "KUBECONFIG",
            "DATABASE_URL",
            "STRIPE_SECRET_KEY",
            "AZURE_CLIENT_SECRET",
        ):
            with self.subTest(name=name):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(self._profile(api_key_env=name))
                self.assertIn("not an allowed delegate key", str(ctx.exception))

    def test_sensitive_credentials_rejected(self):
        for sensitive in (
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "github_token",
            "AWS_SECRET_ACCESS_KEY",
            "NPM_TOKEN",
            "PYPI_TOKEN",
            "SSH_AUTH_SOCK",
            "SLACK_TOKEN",
        ):
            with self.subTest(value=sensitive):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(self._profile(api_key_env=sensitive))
                self.assertIn("refers to a sensitive system credential", str(ctx.exception))

    def test_the_key_name_is_published_but_a_key_would_be_too(self):
        # The contract carries api_key_env verbatim, which is exactly why it must
        # be a name: this dict is emitted publicly and hashed into config_hash.
        serialised = contracts.project_as_dict(cfg.parse_config(self._profile()))["knobs"][
            "delegate_profiles"
        ]["router"]
        self.assertEqual(serialised["api_key_env"], "OPENAI_API_KEY")
        self.assertEqual(serialised["endpoint"], "http://localhost:1/v1")


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
        config = cfg.parse_config(
            self._with(
                {
                    "cursor": {
                        "vendor": "cli",
                        "command": "cursor-agent",
                        "prompt_mode": "arg",
                        "model": "composer-1",
                    },
                }
            )
        )
        profile = config.knobs.delegate_profiles["cursor"]
        self.assertEqual(
            profile,
            cfg.DelegateProfile(
                vendor="cli", command="cursor-agent", prompt_mode="arg", model="composer-1"
            ),
        )

    def test_prompt_mode_defaults_to_stdin(self):
        # The existing "pipe via stdin" guidance stays the norm; `arg` is the opt-in.
        config = cfg.parse_config(
            self._with(
                {
                    "gemini-cli": {"vendor": "cli", "command": "gemini"},
                }
            )
        )
        profile = config.knobs.delegate_profiles["gemini-cli"]
        self.assertEqual(profile.prompt_mode, cfg.DEFAULT_PROMPT_MODE)
        self.assertEqual(profile.prompt_mode, "stdin")
        self.assertIsNone(profile.model)

    def test_explicit_null_model_parses(self):
        config = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent", "model": None},
                }
            )
        )
        self.assertIsNone(config.knobs.delegate_profiles["cursor"].model)

    def test_openai_compatible_full_profile_parses(self):
        parsed = cfg.parse_config(
            self._with(
                {
                    "local": {
                        "vendor": "openai-compatible",
                        "endpoint": "http://localhost:11434/v1/chat/completions",
                        "api_key_env": "VLLM_API_KEY",
                        "model": "qwen2.5",
                    },
                }
            )
        )
        profile = parsed.knobs.delegate_profiles["local"]
        self.assertEqual(profile.vendor, "openai-compatible")
        self.assertEqual(profile.endpoint, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(profile.api_key_env, "VLLM_API_KEY")
        self.assertIsNone(profile.command)

    def test_unknown_vendor_rejected(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(self._with({"router": {"vendor": "bedrock"}}))
        message = str(ctx.exception)
        self.assertIn("unknown delegate vendor 'bedrock'", message)
        self.assertIn("valid: cli, openai-compatible", message)

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
            cfg.parse_config(
                self._with(
                    {
                        "cursor": {
                            "vendor": "cli",
                            "command": "cursor-agent",
                            "prompt_mode": "pipe",
                        },
                    }
                )
            )
        message = str(ctx.exception)
        self.assertIn("invalid prompt_mode 'pipe'", message)
        self.assertIn("valid: stdin, arg", message)

    def test_shadowing_a_builtin_vendor_rejected(self):
        for name in ("claude", "codex", "agy", "ollama", "anthropic-api", "openai-api"):
            with self.subTest(name=name):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(
                        self._with(
                            {
                                name: {"vendor": "cli", "command": "whatever"},
                            }
                        )
                    )
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
            cfg.parse_config(
                self._with(
                    {
                        "cursor:pro": {"vendor": "cli", "command": "cursor-agent"},
                    }
                )
            )
        message = str(ctx.exception)
        self.assertIn("may not contain ':'", message)
        self.assertIn("could never be selected", message)

    def test_blank_name_rejected(self):
        for name in ("", "   "):
            with self.subTest(name=name):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.parse_config(
                        self._with(
                            {
                                name: {"vendor": "cli", "command": "cursor-agent"},
                            }
                        )
                    )
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
                    cfg.parse_config(
                        self._with(
                            {
                                key: {"vendor": "cli", "command": "cursor-agent"},
                            }
                        )
                    )
                message = str(ctx.exception)
                self.assertIn(f"is {kind}, not a string", message)
                self.assertIn("quote the key", message)

    def test_args_carry_standing_flags(self):
        """A real CLI needs standing flags; `command` is one executable, not a shell line.

        The proposal's own field report drove `cursor-agent -p --model X --force`.
        Without `args` an operator would have to fold `-p --force` into `command`,
        which keel would then treat as a single filename.
        """
        parsed = cfg.parse_config(
            self._with(
                {
                    "cursor": {
                        "vendor": "cli",
                        "command": "cursor-agent",
                        "args": ["-p", "--force"],
                    },
                    "plain": {"vendor": "cli", "command": "gemini"},
                }
            )
        )
        profiles = parsed.knobs.delegate_profiles
        self.assertEqual(profiles["cursor"].args, ("-p", "--force"))
        self.assertEqual(profiles["plain"].args, ())

    def test_args_change_the_config_hash(self):
        base = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent"},
                }
            )
        )
        changed = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"]},
                }
            )
        )
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_review_args_separate_the_reviewer_from_the_implementer(self):
        """s7 wants findings only, but `args` carries write-enabling implementer flags.

        `cursor-agent`'s `--force` approves edits non-interactively — exactly wrong for a
        reviewer. keel cannot enforce read-only on an arbitrary CLI, so this is the
        operator's lever; `role_args` is where the choice is made.
        """
        parsed = cfg.parse_config(
            self._with(
                {
                    "cursor": {
                        "vendor": "cli",
                        "command": "cursor-agent",
                        "args": ["-p", "--force"],
                        "review_args": ["-p"],
                    },
                    "shared": {"vendor": "cli", "command": "gemini", "args": ["-p"]},
                }
            )
        )
        profiles = parsed.knobs.delegate_profiles
        self.assertEqual(profiles["cursor"].role_args(), ("-p", "--force"))
        self.assertEqual(profiles["cursor"].role_args(review=True), ("-p",))
        # Unset falls back rather than emptying, so existing profiles keep working.
        self.assertIsNone(profiles["shared"].review_args)
        self.assertEqual(profiles["shared"].role_args(review=True), ("-p",))

    def test_empty_review_args_is_not_the_same_as_unset(self):
        parsed = cfg.parse_config(
            self._with(
                {
                    "bare": {"vendor": "cli", "command": "x", "args": ["-p"], "review_args": []},
                }
            )
        )
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
        parsed = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"]},
                }
            )
        )
        self.assertEqual(
            cfg._canonical(parsed)["knobs"]["delegate_profiles"],
            contracts.project_as_dict(parsed)["knobs"]["delegate_profiles"],
        )

    def test_review_args_change_the_config_hash(self):
        base = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent", "args": ["-p"]},
                }
            )
        )
        changed = cfg.parse_config(
            self._with(
                {
                    "cursor": {
                        "vendor": "cli",
                        "command": "cursor-agent",
                        "args": ["-p"],
                        "review_args": [],
                    },
                }
            )
        )
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_profile_named_after_its_vendor_rejected(self):
        """A profile called `cli` makes every attribution field say the same nothing."""
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(
                self._with(
                    {
                        "cli": {"vendor": "cli", "command": "cursor-agent"},
                    }
                )
            )
        message = str(ctx.exception)
        self.assertIn("would make attribution ambiguous", message)
        self.assertIn("e.g. 'cursor'", message)

    def test_model_arg_defaults_and_overrides(self):
        parsed = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent"},
                    "weird": {"vendor": "cli", "command": "weirdcli", "model_arg": "-m"},
                }
            )
        )
        profiles = parsed.knobs.delegate_profiles
        # Without a way to spell model selection, the documented precedence would be
        # unimplementable for an arbitrary CLI.
        self.assertEqual(profiles["cursor"].model_arg, "--model")
        self.assertEqual(profiles["weird"].model_arg, "-m")

    def test_model_arg_changes_the_config_hash(self):
        base = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent"},
                }
            )
        )
        changed = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent", "model_arg": "-m"},
                }
            )
        )
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
            cfg.parse_config(
                self._with(
                    {
                        "cursor": {"vendor": "cli", "command": "cursor-agent", "proxy": "http://x"},
                    }
                )
            )
        self.assertIn("unknown property 'proxy'", str(ctx.exception))

    def test_a_field_belonging_to_another_vendor_is_rejected_not_ignored(self):
        """`endpoint` is legal on openai-compatible, so the schema cannot catch this.

        An operator who sets it on a `cli` profile has a wrong model of what will
        run, and a silently-ignored key is how that survives to the first real run.
        """
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(
                self._with(
                    {
                        "cursor": {
                            "vendor": "cli",
                            "command": "cursor-agent",
                            "endpoint": "http://localhost:1/v1",
                        },
                    }
                )
            )
        message = str(ctx.exception)
        self.assertIn("'endpoint' does not apply to vendor 'cli'", message)
        self.assertIn("silently ignored", message)

    def test_command_on_an_endpoint_vendor_is_rejected_too(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.parse_config(
                self._with(
                    {
                        "router": {
                            "vendor": "openai-compatible",
                            "command": "curl",
                            "endpoint": "http://localhost:1/v1",
                            "api_key_env": "K",
                        },
                    }
                )
            )
        self.assertIn("'command' does not apply to vendor 'openai-compatible'", str(ctx.exception))

    def test_round_trips_through_the_project_contract(self):
        data = self._with(
            {
                "gemini-cli": {"vendor": "cli", "command": "gemini", "prompt_mode": "arg"},
                "cursor": {"vendor": "cli", "command": "cursor-agent", "model": "composer-1"},
            }
        )
        serialised = contracts.project_as_dict(cfg.parse_config(data))["knobs"]
        profiles = serialised["delegate_profiles"]
        self.assertEqual(list(profiles), ["cursor", "gemini-cli"])  # sorted, order-stable
        self.assertEqual(
            profiles["cursor"],
            {
                "vendor": "cli",
                "command": "cursor-agent",
                "args": [],
                "review_args": None,  # unset -> the reviewer role falls back to args
                "prompt_mode": "stdin",  # the default, made explicit on the way out
                "model": "composer-1",
                "model_arg": "--model",  # ditto: how the model actually reaches the CLI
                "endpoint": None,  # cli profiles carry no endpoint
                "api_key_env": None,
            },
        )
        self.assertEqual(
            profiles["gemini-cli"],
            {
                "vendor": "cli",
                "command": "gemini",
                "args": [],
                "review_args": None,
                "prompt_mode": "arg",
                "model": None,
                "model_arg": "--model",
                "endpoint": None,
                "api_key_env": None,
            },
        )
        # A round trip through the contract reparses to the same profiles.
        reparsed = self._with({name: dict(p) for name, p in profiles.items()})
        self.assertEqual(
            cfg.parse_config(reparsed).knobs.delegate_profiles,
            cfg.parse_config(data).knobs.delegate_profiles,
        )

    def test_profile_key_order_does_not_change_the_hash(self):
        one = self._with(
            {
                "cursor": {"vendor": "cli", "command": "cursor-agent"},
                "gemini-cli": {"vendor": "cli", "command": "gemini"},
            }
        )
        two = self._with(
            {
                "gemini-cli": {"vendor": "cli", "command": "gemini"},
                "cursor": {"vendor": "cli", "command": "cursor-agent"},
            }
        )
        self.assertEqual(
            cfg.config_hash(cfg.parse_config(one)), cfg.config_hash(cfg.parse_config(two))
        )

    def test_profiles_change_the_config_hash(self):
        base = cfg.parse_config(copy.deepcopy(VALID))
        changed = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent"},
                }
            )
        )
        self.assertNotEqual(cfg.config_hash(base), cfg.config_hash(changed))

    def test_prompt_mode_changes_the_config_hash(self):
        stdin = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent"},
                }
            )
        )
        arg = cfg.parse_config(
            self._with(
                {
                    "cursor": {"vendor": "cli", "command": "cursor-agent", "prompt_mode": "arg"},
                }
            )
        )
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


class TestSwarmReviewEvidenceKnob(unittest.TestCase):
    """#828: the swarm review gate defaults on; the opt-out is explicit config."""

    def test_defaults_on(self):
        from keel import config as cfg

        knobs = cfg.Knobs(build_gate_cmd="true")
        self.assertTrue(knobs.swarm_review_evidence)

    def test_yaml_opt_out_parses(self):
        import tempfile

        from keel import config as cfg

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write(
                "extends: keel\n"
                "core_version: '^0.7'\n"
                "base_branch: main\n"
                "knobs:\n"
                "  build_gate_cmd: 'true'\n"
                "  swarm_review_evidence: false\n"
            )
            path = tf.name
        try:
            config = cfg.load_config(path)
            self.assertFalse(config.knobs.swarm_review_evidence)
        finally:
            import os

            os.unlink(path)

    def test_load_config_malformed_yaml_raises_config_error(self):
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
            tf.write("extends: [unclosed list\n")
            path = tf.name
        try:
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.load_config(path)
            self.assertIn("YAML syntax error", ctx.exception.errors[0])
            self.assertEqual(ctx.exception.source, path)
        finally:
            import os

            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
