"""Unit tests for agent dispatch + attribution."""

import unittest

from keel import agents
from keel import config as cfg

CONFIG = cfg.parse_config({
    "extends": "keel",
    "core_version": "^0.1",
    "base_branch": "main",
    "knobs": {
        "build_gate_cmd": "make test",
        "implementer_agents": {"mobile": "flutter-developer", "backend": "supabase-developer"},
    },
})

PROFILE_CONFIG = cfg.parse_config({
    "extends": "keel",
    "core_version": "^0.1",
    "base_branch": "main",
    "knobs": {
        "build_gate_cmd": "make test",
        "delegate_profiles": {
            "cursor": {
                "vendor": "cli",
                "command": "cursor-agent",
                "prompt_mode": "arg",
                "model": "composer-1",
            },
            "gemini-cli": {"vendor": "cli", "command": "gemini", "prompt_mode": "arg"},
        },
    },
})

#: Every pre-existing delegate form, so a profile-aware resolver cannot quietly
#: change what any of them mean.
LEGACY_DELEGATES = (
    ("claude", ("claude", None)),
    ("codex", ("codex", None)),
    ("agy", ("agy", None)),
    ("ollama:qwen2.5", ("ollama", "qwen2.5")),
    ("anthropic-api:claude-sonnet-5", ("anthropic-api", "claude-sonnet-5")),
    ("openai-api:gpt-5", ("openai-api", "gpt-5")),
    ("google-api:gemini-2.5-pro", ("google-api", "gemini-2.5-pro")),
)


class TestSplitDelegate(unittest.TestCase):
    def test_with_model(self):
        self.assertEqual(agents.split_delegate("ollama:qwen2.5"), ("ollama", "qwen2.5"))

    def test_without_model(self):
        self.assertEqual(agents.split_delegate("codex"), ("codex", None))

    def test_empty_model_after_colon(self):
        self.assertEqual(agents.split_delegate("ollama:"), ("ollama", None))


class TestResolveAgent(unittest.TestCase):
    def test_delegate_wins(self):
        self.assertEqual(
            agents.resolve_agent(CONFIG, role="mobile", delegate="codex"), "codex"
        )

    def test_role_mapping(self):
        self.assertEqual(agents.resolve_agent(CONFIG, role="mobile"), "flutter-developer")

    def test_unknown_role_falls_back_to_host(self):
        self.assertEqual(
            agents.resolve_agent(CONFIG, role="desktop", host_agent="agy"), "agy"
        )

    def test_default_host(self):
        self.assertEqual(agents.resolve_agent(CONFIG), "claude")


class TestModelBase(unittest.TestCase):
    def test_examples(self):
        cases = {
            "qwen2.5:7b": "qwen",
            "gemma2": "gemma",
            "llama3.1": "llama",
            "gpt-5.5": "gpt-5",
            "gpt-4o": "gpt-4o",
            "claude": "claude",
            "Qwen2.5": "qwen",  # lowercased
        }
        for model, expected in cases.items():
            self.assertEqual(agents.model_base(model), expected, model)

    def test_empty(self):
        self.assertEqual(agents.model_base(""), "")
        self.assertEqual(agents.model_base("   "), "")


class TestLabels(unittest.TestCase):
    def test_agent_label(self):
        self.assertEqual(agents.agent_label("ollama"), "agent:ollama")

    def test_model_label(self):
        self.assertEqual(agents.model_label("qwen2.5"), "model:qwen")

    def test_model_label_none_when_no_base(self):
        self.assertIsNone(agents.model_label("2.5"))  # strips to empty


class TestAttribution(unittest.TestCase):
    def test_vendor_only(self):
        a = agents.attribution("codex")
        self.assertEqual(a["agent_label"], "agent:codex")
        self.assertIsNone(a["model_label"])
        self.assertEqual(a["system"], "codex")

    def test_vendor_and_model(self):
        a = agents.attribution("ollama", "qwen2.5")
        self.assertEqual(a["agent_label"], "agent:ollama")
        self.assertEqual(a["model_label"], "model:qwen")
        self.assertEqual(a["system"], "ollama:qwen2.5")


class TestApiDelegate(unittest.TestCase):
    def test_known_api_vendors(self):
        for vendor in agents.API_VENDORS:
            self.assertTrue(agents.is_api_delegate(vendor))

    def test_non_api_vendors(self):
        for vendor in ("claude", "codex", "agy", "ollama", "api", "anthropic"):
            self.assertFalse(agents.is_api_delegate(vendor))

    def test_split_delegate_keeps_api_model(self):
        # The hosted-API value fits the existing first-colon split unchanged.
        self.assertEqual(
            agents.split_delegate("anthropic-api:claude-sonnet-5"),
            ("anthropic-api", "claude-sonnet-5"),
        )

    def test_api_attribution(self):
        a = agents.attribution("anthropic-api", "claude-sonnet-5")
        self.assertEqual(a["agent_label"], "agent:anthropic-api")
        self.assertEqual(a["model_label"], "model:claude-sonnet-5")
        self.assertEqual(a["system"], "anthropic-api:claude-sonnet-5")


class TestBuiltinVendors(unittest.TestCase):
    def test_builtin_set_is_the_documented_one(self):
        self.assertEqual(
            agents.BUILTIN_DELEGATE_VENDORS,
            ("claude", "codex", "agy", "ollama",
             "anthropic-api", "openai-api", "google-api"),
        )

    def test_composed_from_the_per_category_tuples(self):
        self.assertEqual(
            agents.BUILTIN_DELEGATE_VENDORS,
            agents.CLI_VENDORS + agents.LOCAL_VENDORS + agents.API_VENDORS,
        )


class TestResolveDelegateProfile(unittest.TestCase):
    def test_configured_profile_resolves(self):
        profile = agents.resolve_delegate_profile(PROFILE_CONFIG, "cursor")
        self.assertEqual(profile, cfg.DelegateProfile(vendor="cli", command="cursor-agent",
                                              prompt_mode="arg", model="composer-1"))

    def test_unknown_name_resolves_to_none(self):
        self.assertIsNone(agents.resolve_delegate_profile(PROFILE_CONFIG, "aider"))

    def test_no_profiles_configured(self):
        self.assertIsNone(agents.resolve_delegate_profile(CONFIG, "cursor"))

    def test_builtin_vendors_always_win(self):
        # Fail-closed: even if a same-named profile reached the resolver (config
        # validation rejects it), the built-in vendor is what runs.
        shadowing = cfg.ProjectConfig(
            extends=PROFILE_CONFIG.extends,
            core_version=PROFILE_CONFIG.core_version,
            base_branch=PROFILE_CONFIG.base_branch,
            knobs=cfg.Knobs(
                build_gate_cmd="make test",
                delegate_profiles={
                    name: cfg.DelegateProfile(vendor="cli", command="evil") for name in
                    agents.BUILTIN_DELEGATE_VENDORS
                },
            ),
        )
        for name in agents.BUILTIN_DELEGATE_VENDORS:
            with self.subTest(vendor=name):
                self.assertIsNone(agents.resolve_delegate_profile(shadowing, name))
                self.assertFalse(agents.is_profile_delegate(shadowing, name))

    def test_is_profile_delegate(self):
        self.assertTrue(agents.is_profile_delegate(PROFILE_CONFIG, "gemini-cli"))
        self.assertFalse(agents.is_profile_delegate(PROFILE_CONFIG, "codex"))
        self.assertFalse(agents.is_profile_delegate(PROFILE_CONFIG, "aider"))

    def test_profile_name_passes_through_resolve_agent(self):
        # A profile name is just a delegate token; precedence is unchanged.
        self.assertEqual(
            agents.resolve_agent(PROFILE_CONFIG, role="mobile", delegate="cursor"), "cursor"
        )


class TestProfileAttribution(unittest.TestCase):
    def test_profile_with_model(self):
        profile = agents.resolve_delegate_profile(PROFILE_CONFIG, "cursor")
        a = agents.profile_attribution("cursor", profile)
        self.assertEqual(a["agent_label"], "agent:cli")
        self.assertEqual(a["model_label"], "model:composer-1")
        self.assertEqual(a["system"], "cli:composer-1")
        self.assertEqual(a["delegate_profile"], "cursor")

    def test_profile_without_model_names_the_cli_that_ran(self):
        profile = agents.resolve_delegate_profile(PROFILE_CONFIG, "gemini-cli")
        a = agents.profile_attribution("gemini-cli", profile)
        self.assertEqual(a["agent_label"], "agent:cli")
        self.assertIsNone(a["model_label"])
        self.assertEqual(a["system"], "cli")
        # Without this, the closure comment could only say "cli".
        self.assertEqual(a["delegate_profile"], "gemini-cli")

    def test_per_run_model_beats_the_profile_model(self):
        """`--delegate cursor:MODEL` must attribute the model that actually ran.

        s4 documents per-run > profile > CLI default, and keel's rule is that
        attribution records the *effective* implementer. Reporting the configured
        model here would mislabel every run that overrode it.
        """
        profile = agents.resolve_delegate_profile(PROFILE_CONFIG, "cursor")
        self.assertEqual(profile.model, "composer-1")
        a = agents.profile_attribution("cursor", profile, "cursor-grok-4.5-high")
        self.assertEqual(a["agent_label"], "agent:cli")
        self.assertEqual(a["model_label"], "model:cursor-grok-4")
        self.assertEqual(a["system"], "cli:cursor-grok-4.5-high")
        self.assertEqual(a["delegate_profile"], "cursor")

    def test_per_run_model_supplies_one_when_the_profile_has_none(self):
        profile = agents.resolve_delegate_profile(PROFILE_CONFIG, "gemini-cli")
        self.assertIsNone(profile.model)
        a = agents.profile_attribution("gemini-cli", profile, "gemini-2.5-pro")
        self.assertEqual(a["model_label"], "model:gemini-2")
        self.assertEqual(a["system"], "cli:gemini-2.5-pro")

    def test_key_is_delegate_profile_not_profile(self):
        """`profile` is taken: the run record uses it for standard/compound.

        Merging this dict into a ship_run record under the shorter key would
        silently overwrite the workflow profile.
        """
        profile = agents.resolve_delegate_profile(PROFILE_CONFIG, "cursor")
        a = agents.profile_attribution("cursor", profile)
        self.assertEqual(a["delegate_profile"], "cursor")
        self.assertNotIn("profile", a)


class TestSafeModelToken(unittest.TestCase):
    """The model may come from an issue label, so it is argv-bound untrusted input."""

    def test_accepts_real_model_ids(self):
        for model in ("cursor-grok-4.5-high", "gpt-5.3-codex", "composer-2.5",
                      "qwen2.5", "claude-sonnet-5", "gemini_2.5"):
            with self.subTest(model=model):
                self.assertTrue(agents.is_safe_model_token(model))

    def test_rejects_argv_and_shell_hazards(self):
        for model in ("--version", "-m", "a b", "a;rm -rf /", "a|b", "a$(id)",
                      "a`id`", "a&b", "a>b", "a\nb", "a'b", 'a"b', "a/b", "a:b"):
            with self.subTest(model=model):
                self.assertFalse(agents.is_safe_model_token(model))

    def test_rejects_empty(self):
        self.assertFalse(agents.is_safe_model_token(""))
        self.assertFalse(agents.is_safe_model_token(None))


class TestExistingDelegateFormsUnchanged(unittest.TestCase):
    """Every pre-existing delegate form must resolve exactly as it did before #659."""

    def test_split_is_unchanged(self):
        for value, expected in LEGACY_DELEGATES:
            with self.subTest(delegate=value):
                self.assertEqual(agents.split_delegate(value), expected)

    def test_none_are_profiles(self):
        for value, (vendor, _) in LEGACY_DELEGATES:
            with self.subTest(delegate=value):
                self.assertFalse(agents.is_profile_delegate(PROFILE_CONFIG, vendor))

    def test_api_classification_is_unchanged(self):
        for value, (vendor, _) in LEGACY_DELEGATES:
            with self.subTest(delegate=value):
                self.assertEqual(agents.is_api_delegate(vendor), vendor.endswith("-api"))

    def test_attribution_is_unchanged(self):
        for value, (vendor, model) in LEGACY_DELEGATES:
            with self.subTest(delegate=value):
                a = agents.attribution(vendor, model)
                self.assertEqual(a["agent_label"], f"agent:{vendor}")
                self.assertEqual(a["system"], value)
                self.assertNotIn("profile", a)  # only profile runs carry that key

    def test_resolve_agent_is_unchanged(self):
        for value, _ in LEGACY_DELEGATES:
            with self.subTest(delegate=value):
                self.assertEqual(
                    agents.resolve_agent(PROFILE_CONFIG, role="mobile", delegate=value), value
                )


if __name__ == "__main__":
    unittest.main()
