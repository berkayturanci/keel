"""Unit tests for the pure delegate planner (:mod:`keel.delegate`, #1012).

Everything here is pure: a provider record plus a role in, a frozen :class:`RunPlan` out.
No subprocess, no network, no clock, no filesystem. That is what lets the load-bearing
claim of the issue — *a review-role run against a built-in CLI never passes a
write-enabling flag* — be asserted per vendor rather than written down in prose.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import unittest

from keel import agents, delegate, providers
from keel import config as cfg
from keel.api_delegate import DEFAULT_MAX_TOKENS, OPENAI_COMPATIBLE

PROMPT = "/tmp/brief.md"


def _config(profiles=None):
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^1.0",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true", delegate_profiles=profiles or {}),
    )


def _builtin(name):
    for provider in providers.builtin_providers():
        if provider.name == name:
            return provider
    raise AssertionError(f"no built-in provider {name!r}")  # pragma: no cover


def _plan(name, role, **kwargs):
    return delegate.plan_run(_builtin(name), role, PROMPT, **kwargs)


class ResolveProviderTest(unittest.TestCase):
    def test_a_bare_builtin_name_resolves_to_the_builtin_provider(self):
        resolution = delegate.resolve_provider(None, None, "codex")
        self.assertEqual(resolution.provider.name, "codex")
        self.assertEqual(resolution.provider.source, "builtin")
        self.assertIsNone(resolution.model)
        self.assertIsNone(resolution.profile)

    def test_the_token_carries_a_per_run_model(self):
        resolution = delegate.resolve_provider(None, None, "ollama:qwen2.5")
        self.assertEqual(resolution.provider.name, "ollama")
        self.assertEqual(resolution.model, "qwen2.5")

    def test_an_unsafe_model_token_is_refused_before_anything_looks_at_it(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.resolve_provider(None, None, "codex:--oops")
        self.assertEqual(caught.exception.code, "bad-model")

    def test_an_empty_provider_token_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.resolve_provider(None, None, "   ")
        self.assertEqual(caught.exception.code, "bad-provider")

    def test_an_unknown_name_names_what_is_known(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.resolve_provider(None, None, "cursor")
        self.assertEqual(caught.exception.code, "unknown-provider")
        self.assertIn("claude", caught.exception.message)

    def test_a_project_profile_resolves_and_carries_its_profile_record(self):
        profile = cfg.DelegateProfile(vendor="cli", command="cursor-agent", args=("-p",))
        resolution = delegate.resolve_provider(_config({"cursor": profile}), None, "cursor")
        self.assertEqual(resolution.provider.source, "profile")
        self.assertIs(resolution.profile, profile)

    def test_a_registry_entry_resolves_when_no_profile_claims_the_name(self):
        registry = providers.parse_registry(
            {"providers": {"aider": {"transport": "cli", "command": "aider"}}},
            path="/registry.yaml",
        )
        resolution = delegate.resolve_provider(None, registry, "aider")
        self.assertEqual(resolution.provider.source, "registry")
        self.assertIsNone(resolution.profile)

    def test_the_right_registry_entry_is_picked_out_of_several(self):
        registry = providers.parse_registry(
            {
                "providers": {
                    "aider": {"transport": "cli", "command": "aider"},
                    "zed": {"transport": "cli", "command": "zed-agent"},
                }
            },
            path="/registry.yaml",
        )
        self.assertEqual(
            delegate.resolve_provider(None, registry, "zed").provider.command, "zed-agent"
        )

    def test_a_registry_that_does_not_claim_the_name_leaves_the_builtin_alone(self):
        registry = providers.parse_registry(
            {"providers": {"aider": {"transport": "cli", "command": "aider"}}},
            path="/registry.yaml",
        )
        resolution = delegate.resolve_provider(None, registry, "codex")
        self.assertEqual(resolution.provider.source, "builtin")

    def test_the_right_profile_is_picked_out_of_several(self):
        config = _config(
            {
                "aider": cfg.DelegateProfile(vendor="cli", command="aider"),
                "cursor": cfg.DelegateProfile(vendor="cli", command="cursor-agent"),
            }
        )
        resolution = delegate.resolve_provider(config, None, "cursor")
        self.assertEqual(resolution.provider.command, "cursor-agent")

    def test_precedence_is_builtin_then_project_profile_then_registry(self):
        """A built-in vendor can never be redefined — not by config, not from $HOME.

        `config.parse_config` refuses a profile that shadows a built-in and
        `providers.registry_clashes` refuses a registry entry that does, so dispatch must
        not be the one place where `claude` means whatever a file in the operator's home
        directory said it meant.
        """
        registry = providers.parse_registry(
            {
                "providers": {
                    "claude": {"transport": "cli", "command": "registry-claude"},
                    "cursor": {"transport": "cli", "command": "registry-cursor"},
                    "aider": {"transport": "cli", "command": "registry-aider"},
                }
            },
            path="/registry.yaml",
        )
        config = _config({"cursor": cfg.DelegateProfile(vendor="cli", command="profile-cursor")})
        # the built-in wins over both …
        claude = delegate.resolve_provider(config, registry, "claude").provider
        self.assertEqual(claude.command, "claude")
        self.assertEqual(claude.source, "builtin")
        # … the project profile wins over the registry …
        self.assertEqual(
            delegate.resolve_provider(config, registry, "cursor").provider.command,
            "profile-cursor",
        )
        # … and the registry is reached only when nothing above claims the name.
        self.assertEqual(
            delegate.resolve_provider(config, registry, "aider").provider.command,
            "registry-aider",
        )

    def test_a_shadowing_registry_entry_cannot_redirect_a_builtin_vendor(self):
        registry = providers.parse_registry(
            {"providers": {"codex": {"transport": "cli", "command": "/tmp/evil"}}},
            path="/registry.yaml",
        )
        plan = delegate.plan_run(
            delegate.resolve_provider(None, registry, "codex").provider, "review", PROMPT
        )
        self.assertEqual(plan.argv[0], "codex")
        self.assertNotIn("/tmp/evil", plan.argv)


class RoleAndInputValidationTest(unittest.TestCase):
    def test_an_unknown_role_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            _plan("claude", "deploy")
        self.assertEqual(caught.exception.code, "bad-role")

    def test_an_unknown_effort_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            _plan("claude", "review", effort="maximum")
        self.assertEqual(caught.exception.code, "bad-effort")

    def test_a_non_positive_timeout_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            _plan("claude", "review", timeout=0)
        self.assertEqual(caught.exception.code, "bad-timeout")

    def test_an_empty_prompt_path_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.plan_run(_builtin("claude"), "review", "")
        self.assertEqual(caught.exception.code, "no-prompt")

    def test_an_unsafe_model_override_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            _plan("claude", "review", model="opus; rm -rf /")
        self.assertEqual(caught.exception.code, "bad-model")

    def test_an_http_provider_without_a_model_is_refused(self):
        with self.assertRaises(delegate.DelegateError) as caught:
            _plan("anthropic-api", "review")
        self.assertEqual(caught.exception.code, "no-model")

    def test_a_provider_record_with_no_command_is_refused(self):
        provider = providers.Provider(
            name="broken", vendor="cli", transport="cli", source="registry"
        )
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.plan_run(provider, "implement", PROMPT)
        self.assertEqual(caught.exception.code, "bad-provider")


class BuiltinCliArgvTest(unittest.TestCase):
    """Every built-in CLI vendor × every role."""

    def test_claude_read_only_roles_allow_only_the_read_tools(self):
        for role in delegate.READ_ONLY_ROLES:
            with self.subTest(role=role):
                plan = _plan("claude", role, model="opus-4.5")
                self.assertTrue(plan.read_only)
                self.assertEqual(plan.transport, "cli")
                self.assertEqual(plan.stdin_mode, delegate.STDIN_TEXT)
                self.assertEqual(
                    plan.argv,
                    (
                        "claude",
                        "-p",
                        "--output-format",
                        "text",
                        "--model",
                        "opus-4.5",
                        "--allowed-tools",
                        delegate.CLAUDE_ALLOWED_TOOLS,
                    ),
                )
                self.assertTrue(plan.read_only_backed)

    def test_claude_tool_roles_drop_the_denylist(self):
        for role in ("implement", "fix"):
            with self.subTest(role=role):
                plan = _plan("claude", role)
                self.assertFalse(plan.read_only)
                self.assertEqual(plan.argv, ("claude", "-p", "--dangerously-skip-permissions"))

    def test_codex_read_only_roles_use_the_read_only_sandbox(self):
        for role in delegate.READ_ONLY_ROLES:
            with self.subTest(role=role):
                plan = _plan("codex", role, model="gpt-5.5")
                self.assertEqual(
                    plan.argv,
                    (
                        "codex",
                        "exec",
                        "-s",
                        "read-only",
                        "--skip-git-repo-check",
                        "-m",
                        "gpt-5.5",
                    ),
                )
                self.assertEqual(plan.stdin_mode, delegate.STDIN_TEXT)

    def test_codex_tool_roles_use_the_workspace_write_sandbox(self):
        plan = _plan("codex", "implement")
        self.assertEqual(
            plan.argv, ("codex", "exec", "-s", "workspace-write", "--skip-git-repo-check")
        )

    def test_agy_read_only_roles_add_the_sandbox_flag(self):
        plan = _plan("agy", "review", model="gemini-3.8-flash")
        self.assertEqual(
            plan.argv,
            (
                "agy",
                "--sandbox",
                "--dangerously-skip-permissions",
                *delegate.AGY_STREAM_ARGS,
                "--model",
                "gemini-3.8-flash",
            ),
        )
        self.assertEqual(plan.stdin_mode, delegate.STDIN_STREAM_JSON)

    def test_agy_tool_roles_drop_the_sandbox_flag(self):
        plan = _plan("agy", "fix")
        self.assertEqual(
            plan.argv,
            ("agy", "--dangerously-skip-permissions", *delegate.AGY_STREAM_ARGS),
        )

    def test_the_prompt_never_reaches_a_builtin_argv(self):
        for name in agents.CLI_VENDORS:
            for role in delegate.ROLES:
                with self.subTest(vendor=name, role=role):
                    self.assertNotIn(PROMPT, _plan(name, role).argv)


class ReadOnlyIsNeverWriteEnablingTest(unittest.TestCase):
    """The acceptance criterion, asserted per vendor.

    Stated as *what the read-only argv must carry* and *what it must never carry*, rather
    than as a diff against the implementer argv: a future flag added to both would slip
    past the second and not past the first.

    ``claude`` no longer carries ``--dangerously-skip-permissions`` in a read-only run at
    all: with an allow-list of four read tools there is nothing left for a permission
    prompt to ask about, so the bypass buys nothing and is dropped. ``agy`` still does,
    because ``--sandbox`` is the only read-only mechanism it documents and an unattended
    reviewer otherwise stops at an approval prompt — which is why the plan reports
    ``read_only_backed`` rather than claiming writes are impossible.
    """

    #: Flags that hand a reviewer the ability to change the checkout. None may appear.
    _NEVER = (
        "workspace-write",
        "danger-full-access",
        "--force",
        "--yolo",
        "--yes-always",
        "--write",
        "--auto-edit",
    )

    #: Vendors for which even the permission bypass must be absent. ``agy`` is excused
    #: with a documented reason; nothing else is.
    _NO_BYPASS = ("claude", "codex")

    _REQUIRED = {
        "claude": ("--allowed-tools", delegate.CLAUDE_ALLOWED_TOOLS),
        "codex": ("-s", "read-only"),
        "agy": ("--sandbox",),
    }

    def test_no_read_only_builtin_argv_carries_a_write_enabling_flag(self):
        for vendor in agents.CLI_VENDORS:
            for role in delegate.READ_ONLY_ROLES:
                with self.subTest(vendor=vendor, role=role):
                    argv = _plan(vendor, role, model="m1").argv
                    for flag in self._NEVER:
                        self.assertNotIn(flag, argv)
                    for required in self._REQUIRED[vendor]:
                        self.assertIn(required, argv)
                    if vendor in self._NO_BYPASS:
                        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_every_read_only_builtin_run_reports_its_read_only_as_backed(self):
        for vendor in agents.CLI_VENDORS:
            for role in delegate.READ_ONLY_ROLES:
                with self.subTest(vendor=vendor, role=role):
                    plan = _plan(vendor, role)
                    self.assertTrue(plan.read_only)
                    self.assertTrue(plan.read_only_backed)

    def test_a_tool_enabled_run_is_never_reported_as_backed(self):
        for vendor in agents.CLI_VENDORS:
            with self.subTest(vendor=vendor):
                plan = _plan(vendor, "implement")
                self.assertFalse(plan.read_only)
                self.assertFalse(plan.read_only_backed)

    def test_every_builtin_vendor_has_a_documented_read_only_flag(self):
        # The planner's per-vendor mechanism must not drift from what #1011 advertises
        # through `Provider.capabilities()["read_only_mode"]`.
        for vendor in agents.CLI_VENDORS:
            with self.subTest(vendor=vendor):
                self.assertIn(vendor, providers.READ_ONLY_FLAGS)
                self.assertTrue(_builtin(vendor).capabilities()["read_only_mode"])

    def test_a_tool_enabled_role_differs_from_the_read_only_one(self):
        for vendor in agents.CLI_VENDORS:
            with self.subTest(vendor=vendor):
                self.assertNotEqual(_plan(vendor, "implement").argv, _plan(vendor, "review").argv)


class ProfileArgvTest(unittest.TestCase):
    def _resolve(self, profile, role, **kwargs):
        config = _config({"cursor": profile})
        resolution = delegate.resolve_provider(config, None, "cursor")
        return delegate.plan_run(
            resolution.provider, role, PROMPT, profile=resolution.profile, **kwargs
        )

    def test_an_implementer_run_uses_the_profiles_own_args(self):
        profile = cfg.DelegateProfile(
            vendor="cli", command="cursor-agent", args=("-p", "--force"), model="grok"
        )
        plan = self._resolve(profile, "implement")
        self.assertEqual(plan.transport, "profile")
        self.assertEqual(plan.argv, ("cursor-agent", "-p", "--force", "--model", "grok"))
        self.assertEqual(plan.stdin_mode, delegate.STDIN_TEXT)

    def test_a_reviewer_run_uses_review_args(self):
        profile = cfg.DelegateProfile(
            vendor="cli",
            command="cursor-agent",
            args=("-p", "--force"),
            review_args=("-p", "--read-only"),
        )
        plan = self._resolve(profile, "review")
        self.assertEqual(plan.argv, ("cursor-agent", "-p", "--read-only"))
        self.assertTrue(plan.read_only_backed)
        self.assertEqual(plan.warnings, ())

    def test_a_reviewer_run_without_review_args_warns_that_nothing_backs_read_only(self):
        profile = cfg.DelegateProfile(vendor="cli", command="cursor-agent")
        plan = self._resolve(profile, "gate")
        self.assertTrue(plan.read_only)
        self.assertFalse(plan.read_only_backed)
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("read-only", plan.warnings[0])

    def test_a_profile_with_implementer_args_and_no_review_args_is_not_backed(self):
        """The fail-open case, and the reason `read_only_backed` exists.

        `DelegateProfile.role_args(review=True)` falls back to `args` when `review_args`
        is None — so this profile plans a review with aider's *write-enabling* flags. The
        first cut gated the warning on `not role_args`, and the fallback made that
        non-empty: the run came back `read_only: true`, argv `--yes-always`, and no
        warning at all. The question is whether the operator configured a read-only
        invocation, never whether the resulting argv happens to be empty.
        """
        profile = cfg.DelegateProfile(
            vendor="cli", command="aider", args=("--yes-always", "--no-check-update")
        )
        for role in delegate.READ_ONLY_ROLES:
            with self.subTest(role=role):
                plan = self._resolve(profile, role)
                self.assertTrue(plan.read_only)
                self.assertFalse(plan.read_only_backed)
                self.assertIn("--yes-always", plan.argv)
                self.assertEqual(len(plan.warnings), 1)
                self.assertIn("implementer's own args", plan.warnings[0])

    def test_an_explicitly_empty_review_args_is_a_configured_choice_and_is_backed(self):
        profile = cfg.DelegateProfile(
            vendor="cli", command="aider", args=("--yes-always",), review_args=()
        )
        plan = self._resolve(profile, "review")
        self.assertEqual(plan.argv, ("aider",))
        self.assertTrue(plan.read_only_backed)
        self.assertEqual(plan.warnings, ())

    def test_a_registry_entry_without_review_args_is_not_backed_either(self):
        registry = providers.parse_registry(
            {"providers": {"aider": {"transport": "cli", "command": "aider"}}},
            path="/registry.yaml",
        )
        resolution = delegate.resolve_provider(None, registry, "aider")
        plan = delegate.plan_run(resolution.provider, "review", PROMPT)
        self.assertFalse(plan.read_only_backed)
        self.assertEqual(len(plan.warnings), 1)

    def test_prompt_mode_arg_asks_the_executor_for_no_stdin(self):
        profile = cfg.DelegateProfile(vendor="cli", command="cursor-agent", prompt_mode="arg")
        plan = self._resolve(profile, "implement")
        self.assertIsNone(plan.stdin_mode)

    def test_a_per_run_model_wins_over_the_profiles_own(self):
        profile = cfg.DelegateProfile(vendor="cli", command="aider", model="configured")
        plan = self._resolve(profile, "implement", model="per-run")
        self.assertEqual(plan.argv, ("aider", "--model", "per-run"))
        self.assertEqual(plan.attribution["model_label"], "model:per-run")

    def test_a_registry_cli_entry_reviews_with_its_review_args(self):
        registry = providers.parse_registry(
            {
                "providers": {
                    "aider": {
                        "transport": "cli",
                        "command": "aider",
                        "review_args": ["--dry-run"],
                    }
                }
            },
            path="/registry.yaml",
        )
        resolution = delegate.resolve_provider(None, registry, "aider")
        plan = delegate.plan_run(resolution.provider, "review", PROMPT)
        self.assertEqual(plan.transport, "profile")
        self.assertEqual(plan.argv, ("aider", "--dry-run"))
        self.assertTrue(plan.read_only_backed)
        self.assertEqual(plan.attribution["delegate_profile"], "aider")

    def test_a_registry_local_entry_runs_its_command_rather_than_dialing_an_address(self):
        registry = providers.parse_registry(
            {"providers": {"llamafile": {"transport": "local", "command": "llamafile"}}},
            path="/registry.yaml",
        )
        resolution = delegate.resolve_provider(None, registry, "llamafile")
        plan = delegate.plan_run(resolution.provider, "implement", PROMPT)
        self.assertEqual(plan.transport, "profile")
        self.assertIsNone(plan.request)


class HttpRequestTest(unittest.TestCase):
    def test_a_hosted_vendor_plans_a_request_and_no_argv(self):
        plan = _plan("anthropic-api", "implement", model="claude-opus-4-5")
        self.assertEqual(plan.transport, "api")
        self.assertEqual(plan.argv, ())
        self.assertEqual(plan.request["vendor"], "anthropic-api")
        self.assertEqual(plan.request["api_key_env"], "ANTHROPIC_API_KEY")
        self.assertEqual(plan.request["max_tokens"], DEFAULT_MAX_TOKENS)
        self.assertEqual(plan.request["extra_payload"], {})

    def test_ollama_targets_the_hardcoded_loopback_generate_endpoint(self):
        plan = _plan("ollama", "implement", model="qwen2.5")
        self.assertEqual(plan.transport, "ollama")
        self.assertEqual(plan.request["endpoint"], delegate.OLLAMA_GENERATE_URL)
        self.assertTrue(delegate.OLLAMA_GENERATE_URL.startswith("http://127.0.0.1:"))

    def test_an_openai_compatible_profile_carries_its_configured_endpoint_and_key_name(self):
        profile = cfg.DelegateProfile(
            vendor=OPENAI_COMPATIBLE,
            endpoint="http://localhost:8000/v1/chat/completions",
            api_key_env="VLLM_API_KEY",
            model="qwen",
        )
        resolution = delegate.resolve_provider(_config({"vllm": profile}), None, "vllm")
        plan = delegate.plan_run(resolution.provider, "review", PROMPT, profile=resolution.profile)
        self.assertEqual(plan.transport, "api")
        self.assertEqual(plan.request["endpoint"], "http://localhost:8000/v1/chat/completions")
        self.assertEqual(plan.request["api_key_env"], "VLLM_API_KEY")


class EffortTest(unittest.TestCase):
    def test_no_effort_asked_means_none_applied_and_no_warning(self):
        plan = _plan("agy", "review", model="gemini-3.8-flash")
        self.assertFalse(plan.effort_applied)
        self.assertEqual(plan.warnings, ())
        self.assertEqual(plan.model, "gemini-3.8-flash")

    def test_agy_spells_effort_as_a_model_suffix(self):
        for level in delegate.EFFORTS:
            with self.subTest(effort=level):
                plan = _plan("agy", "review", effort=level, model="gemini-3.8-flash")
                self.assertTrue(plan.effort_applied)
                self.assertEqual(plan.model, f"gemini-3.8-flash-{level}")
                self.assertIn(f"gemini-3.8-flash-{level}", plan.argv)

    def test_agy_does_not_double_suffix_a_model_that_already_selects_an_effort(self):
        plan = _plan("agy", "review", effort="high", model="gemini-3.8-flash-high")
        self.assertEqual(plan.model, "gemini-3.8-flash-high")
        self.assertTrue(plan.effort_applied)
        self.assertEqual(plan.warnings, ())

    def test_a_models_own_effort_suffix_wins_and_says_so(self):
        plan = _plan("agy", "review", effort="low", model="gemini-3.8-flash-high")
        self.assertEqual(plan.model, "gemini-3.8-flash-high")
        self.assertTrue(plan.effort_applied)
        self.assertIn("already selects effort", plan.warnings[0])

    def test_agy_without_a_model_cannot_carry_an_effort(self):
        plan = _plan("agy", "review", effort="high")
        self.assertFalse(plan.effort_applied)
        self.assertIn("needs a model", plan.warnings[0])

    def test_anthropic_effort_enables_thinking_and_raises_max_tokens_above_the_budget(self):
        for level in delegate.EFFORTS:
            with self.subTest(effort=level):
                plan = _plan("anthropic-api", "review", effort=level, model="claude-opus-4-5")
                budget = delegate.ANTHROPIC_THINKING_BUDGET[level]
                self.assertTrue(plan.effort_applied)
                self.assertEqual(
                    plan.request["extra_payload"],
                    {"thinking": {"type": "enabled", "budget_tokens": budget}},
                )
                self.assertGreater(plan.request["max_tokens"], budget)

    def test_openai_effort_is_reasoning_effort(self):
        plan = _plan("openai-api", "review", effort="medium", model="gpt-5.5")
        self.assertEqual(plan.request["extra_payload"], {"reasoning_effort": "medium"})

    def test_an_openai_compatible_profile_also_takes_reasoning_effort(self):
        profile = cfg.DelegateProfile(
            vendor=OPENAI_COMPATIBLE,
            endpoint="http://localhost:8000/v1/chat/completions",
            api_key_env="VLLM_API_KEY",
            model="qwen",
        )
        resolution = delegate.resolve_provider(_config({"vllm": profile}), None, "vllm")
        plan = delegate.plan_run(
            resolution.provider, "review", PROMPT, effort="low", profile=resolution.profile
        )
        self.assertEqual(plan.request["extra_payload"], {"reasoning_effort": "low"})

    def test_google_effort_is_a_nested_thinking_budget(self):
        for level in delegate.EFFORTS:
            with self.subTest(effort=level):
                plan = _plan("google-api", "review", effort=level, model="gemini-3-pro")
                budget = delegate.GOOGLE_THINKING_BUDGET[level]
                self.assertEqual(
                    plan.request["extra_payload"],
                    {"generationConfig": {"thinkingConfig": {"thinkingBudget": budget}}},
                )
                self.assertGreater(plan.request["max_tokens"], budget)

    def test_codex_spells_effort_as_a_config_override(self):
        for level in delegate.EFFORTS:
            with self.subTest(effort=level):
                plan = _plan("codex", "review", effort=level)
                self.assertTrue(plan.effort_applied)
                self.assertEqual(plan.warnings, ())
                self.assertEqual(plan.argv[-2:], ("-c", f"{delegate.CODEX_EFFORT_CONFIG}={level}"))

    def test_codex_effort_survives_beside_a_model_and_the_read_only_sandbox(self):
        plan = _plan("codex", "review", effort="high", model="gpt-5.5")
        self.assertEqual(
            plan.argv,
            (
                "codex",
                "exec",
                "-s",
                "read-only",
                "--skip-git-repo-check",
                "-m",
                "gpt-5.5",
                "-c",
                f"{delegate.CODEX_EFFORT_CONFIG}=high",
            ),
        )

    def test_vendors_that_cannot_express_effort_say_so_instead_of_ignoring_it(self):
        for name, kwargs in (
            ("claude", {}),
            ("ollama", {"model": "qwen2.5"}),
        ):
            with self.subTest(provider=name):
                plan = _plan(name, "review", effort="high", **kwargs)
                self.assertFalse(plan.effort_applied)
                self.assertIn("--effort high is not supported", plan.warnings[0])

    def test_a_profile_cli_cannot_express_effort_either(self):
        profile = cfg.DelegateProfile(vendor="cli", command="aider")
        resolution = delegate.resolve_provider(_config({"aider": profile}), None, "aider")
        plan = delegate.plan_run(
            resolution.provider, "implement", PROMPT, effort="low", profile=resolution.profile
        )
        self.assertFalse(plan.effort_applied)
        self.assertEqual(len(plan.warnings), 1)


class AttributionTest(unittest.TestCase):
    def test_a_builtin_records_the_vendor_and_the_versionless_model(self):
        plan = _plan("codex", "implement", model="gpt-5.5")
        self.assertEqual(
            plan.attribution,
            {"agent_label": "agent:codex", "model_label": "model:gpt-5", "system": "codex:gpt-5.5"},
        )

    def test_a_profile_records_which_entry_ran_under_delegate_profile(self):
        profile = cfg.DelegateProfile(vendor="cli", command="cursor-agent", model="composer-2.5")
        resolution = delegate.resolve_provider(_config({"cursor": profile}), None, "cursor")
        plan = delegate.plan_run(
            resolution.provider, "implement", PROMPT, profile=resolution.profile
        )
        self.assertEqual(plan.attribution["agent_label"], "agent:cli")
        self.assertEqual(plan.attribution["delegate_profile"], "cursor")
        # never `profile`: the run record already means the workflow profile by that name.
        self.assertNotIn("profile", plan.attribution)

    def test_attribution_comes_from_agents_so_it_cannot_drift(self):
        plan = _plan("anthropic-api", "review", model="claude-opus-4-5")
        self.assertEqual(plan.attribution, agents.attribution("anthropic-api", "claude-opus-4-5"))


class RunPlanShapeTest(unittest.TestCase):
    def test_a_plan_is_frozen(self):
        plan = _plan("claude", "review")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.role = "implement"

    def test_as_dict_is_json_stable_and_carries_no_secret(self):
        plan = _plan("anthropic-api", "review", effort="low", model="claude-opus-4-5")
        record = plan.as_dict()
        self.assertEqual(record["transport"], "api")
        self.assertEqual(record["argv"], [])
        self.assertEqual(record["request"]["api_key_env"], "ANTHROPIC_API_KEY")
        self.assertTrue(record["read_only"])

    def test_every_planned_transport_is_a_declared_one(self):
        for name in agents.BUILTIN_DELEGATE_VENDORS:
            with self.subTest(provider=name):
                plan = _plan(name, "review", model="m1")
                self.assertIn(plan.transport, delegate.TRANSPORTS)


class ModelTokenRulesTest(unittest.TestCase):
    """Which model tokens are accepted depends on **where the model lands**.

    The strict `[A-Za-z0-9._-]` rule exists for an argv and for `google-api`'s URL path.
    Applying it everywhere refused `ollama:qwen2.5-coder:32b` and
    `openrouter:deepseek/deepseek-r1` — ids this repository's own `docs/keel/models.md`
    tells operators to use — so the command was unusable for two whole transports.
    """

    #: Every model id this repo's docs put in front of an operator.
    DOCUMENTED = (
        ("ollama:qwen2.5-coder:32b", "qwen2.5-coder:32b"),
        ("ollama:deepseek-r1:14b", "deepseek-r1:14b"),
        ("ollama:qwen2.5", "qwen2.5"),
        ("anthropic-api:claude-3-7-sonnet-20250219", "claude-3-7-sonnet-20250219"),
        ("openai-api:gpt-4o", "gpt-4o"),
        ("google-api:gemini-2.5-pro", "gemini-2.5-pro"),
    )

    def test_every_documented_builtin_example_resolves(self):
        for token, expected in self.DOCUMENTED:
            with self.subTest(token=token):
                resolution = delegate.resolve_provider(None, None, token)
                self.assertEqual(resolution.model, expected)
                plan = delegate.plan_run(resolution.provider, "implement", PROMPT, model=expected)
                self.assertEqual(plan.model, expected)

    def _openai_compatible(self):
        profile = cfg.DelegateProfile(
            vendor=OPENAI_COMPATIBLE,
            endpoint="http://localhost:8000/v1/chat/completions",
            api_key_env="OPENROUTER_API_KEY",
        )
        return _config({"openrouter": profile})

    def test_every_documented_openai_compatible_example_resolves(self):
        config = self._openai_compatible()
        for model in (
            "deepseek/deepseek-r1",
            "qwen/qwen-2.5-coder-32b-instruct",
            "meta-llama/llama-3.3-70b-instruct",
        ):
            with self.subTest(model=model):
                resolution = delegate.resolve_provider(config, None, f"openrouter:{model}")
                self.assertEqual(resolution.model, model)
                plan = delegate.plan_run(
                    resolution.provider,
                    "implement",
                    PROMPT,
                    model=model,
                    profile=resolution.profile,
                )
                self.assertEqual(plan.request["model"], model)

    def test_a_body_model_still_refuses_a_leading_dash_and_traversal(self):
        for bad in ("-r1", "a/../../b", "qwen 2.5", "qwen;rm -rf /", "qwen$(id)"):
            with self.subTest(model=bad):
                self.assertFalse(delegate.is_safe_body_model_token(bad))
        self.assertFalse(delegate.is_safe_body_model_token(None))
        self.assertFalse(delegate.is_safe_body_model_token(""))

    def test_a_slash_is_refused_where_the_model_reaches_an_argv(self):
        for name in ("claude", "codex", "agy"):
            with self.subTest(provider=name):
                self.assertTrue(delegate.model_reaches_argv(_builtin(name)))
                with self.assertRaises(delegate.DelegateError) as caught:
                    delegate.resolve_provider(None, None, f"{name}:vendor/model")
                self.assertEqual(caught.exception.code, "bad-model")

    def test_a_slash_is_refused_for_google_api_whose_model_is_in_the_url_path(self):
        self.assertTrue(delegate.model_reaches_argv(_builtin("google-api")))
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.resolve_provider(None, None, "google-api:models/gemini-3-pro")
        self.assertEqual(caught.exception.code, "bad-model")

    def test_a_slash_is_accepted_where_the_model_is_only_a_json_body_field(self):
        for name in ("anthropic-api", "openai-api", "ollama"):
            with self.subTest(provider=name):
                self.assertFalse(delegate.model_reaches_argv(_builtin(name)))

    def test_a_body_model_that_breaks_the_wider_rule_is_still_refused_at_resolve(self):
        for token in ("ollama:-r1", "anthropic-api:a/../../b", "openai-api:gpt 4o"):
            with self.subTest(token=token):
                with self.assertRaises(delegate.DelegateError) as caught:
                    delegate.resolve_provider(None, None, token)
                self.assertEqual(caught.exception.code, "bad-model")
                self.assertIn("request-body", caught.exception.message)

    def test_a_profile_model_reaching_an_argv_keeps_the_strict_rule(self):
        config = _config({"aider": cfg.DelegateProfile(vendor="cli", command="aider")})
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.resolve_provider(config, None, "aider:vendor/model")
        self.assertEqual(caught.exception.code, "bad-model")

    def test_a_configured_model_is_validated_too_not_just_the_token(self):
        provider = providers.Provider(
            name="x", vendor="cli", transport="cli", command="x", model="bad model"
        )
        with self.assertRaises(delegate.DelegateError) as caught:
            delegate.plan_run(provider, "implement", PROMPT)
        self.assertEqual(caught.exception.code, "bad-model")


class ProviderConfiguredEffortTest(unittest.TestCase):
    """`Provider.effort` is the per-entry default #1011 carried through for dispatch.

    It went unread, so an operator who wrote `effort: high` on their registry seat got the
    vendor's default on every run and nothing said otherwise.
    """

    def _registry(self, effort):
        return providers.parse_registry(
            {
                "providers": {
                    "seat": {
                        "transport": "api",
                        "endpoint": "http://localhost:8000/v1/chat/completions",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "model": "deepseek/deepseek-r1",
                        "effort": effort,
                    }
                }
            },
            path="/registry.yaml",
        )

    def _plan(self, configured, **kwargs):
        resolution = delegate.resolve_provider(None, self._registry(configured), "seat")
        self.assertEqual(resolution.provider.effort, configured)
        return delegate.plan_run(resolution.provider, "review", PROMPT, **kwargs)

    def test_a_configured_effort_is_the_default_when_none_is_passed(self):
        plan = self._plan("high")
        self.assertEqual(plan.effort, "high")
        self.assertTrue(plan.effort_applied)
        self.assertEqual(plan.request["extra_payload"], {"reasoning_effort": "high"})

    def test_a_per_run_effort_wins_over_the_configured_one(self):
        plan = self._plan("high", effort="low")
        self.assertEqual(plan.effort, "low")
        self.assertEqual(plan.request["extra_payload"], {"reasoning_effort": "low"})

    def test_an_unrecognised_configured_effort_warns_rather_than_failing_the_run(self):
        plan = self._plan("maximum")
        self.assertIsNone(plan.effort)
        self.assertFalse(plan.effort_applied)
        self.assertEqual(plan.request["extra_payload"], {})
        self.assertIn("ignoring configured effort", plan.warnings[0])

    def test_a_configured_effort_reaches_an_argv_vendor_too(self):
        # A registry entry cannot name a built-in vendor, so this is the record shape
        # rather than a resolvable name: what matters is that the default is consumed
        # wherever the vendor can express it.
        provider = providers.Provider(
            name="agy", vendor="agy", transport="cli", command="agy", effort="high"
        )
        plan = delegate.plan_run(provider, "review", PROMPT, model="gemini-3")
        self.assertEqual(plan.model, "gemini-3-high")
        self.assertTrue(plan.effort_applied)


class ResolutionOrderIsStatedOnceTest(unittest.TestCase):
    """The docs and the core must not restate the order in contradictory words.

    Round 1 fixed the code and two documents; the adapter source, its three generated
    copies, `providers.plan_probes` and `configuration.md` kept the inverted order. Every
    one of them was a separate restatement, which is why fixing one did not fix the rest.
    """

    _STALE = re.compile(r"profile\s*>\s*(?:machine\s+)?registry\s*>\s*built-in")
    #: The qualifiers vary by audience ("machine registry" in a guide, plain "registry" in
    #: a docstring); the ORDER is what may not vary.
    _STATED = re.compile(
        r"built-in(?:\s+vendor)?\s*>\s*project\s+profile\s*>\s*(?:machine\s+)?registry"
    )
    _PATHS = (
        "src/keel/providers.py",
        "src/keel/delegate.py",
        "docs/keel/configuration.md",
        "docs/keel/models.md",
        "src/keel/adapters/commands/ship.md",
    )

    def _read(self, name):
        return pathlib.Path(__file__).resolve().parents[1].joinpath(name).read_text("utf-8")

    def test_nothing_states_the_inverted_order(self):
        offenders = [name for name in self._PATHS if self._STALE.search(self._read(name))]
        self.assertEqual([], offenders)

    def test_every_statement_of_the_order_matches_the_resolver(self):
        for name in self._PATHS:
            with self.subTest(path=name):
                self.assertRegex(self._read(name), self._STATED)


class ParsersTest(unittest.TestCase):
    def test_the_agy_stdin_frame_is_one_ndjson_user_message(self):
        frame = delegate.stream_json_frame("hello")
        self.assertTrue(frame.endswith("\n"))
        import json

        event = json.loads(frame)
        self.assertEqual(event["event"], "user")
        self.assertEqual(event["message"], {"role": "user", "content": "hello"})

    def test_stream_json_returns_the_result_frames_response(self):
        raw = "\n".join(
            [
                '{"event": "start"}',
                "not json",
                "",
                '{"event": "result", "result": {"response": "the review"}}',
            ]
        )
        self.assertEqual(delegate.parse_stream_json(raw), "the review")

    def test_a_stream_without_a_result_frame_falls_back_to_the_raw_text(self):
        self.assertEqual(delegate.parse_stream_json("plain output"), "plain output")

    def test_a_result_frame_with_no_response_string_falls_back_too(self):
        raw = '{"event": "result", "result": "not a dict"}'
        self.assertEqual(delegate.parse_stream_json(raw), raw)

    def test_an_empty_stream_is_empty(self):
        self.assertEqual(delegate.parse_stream_json(""), "")

    def test_the_ollama_payload_never_streams(self):
        self.assertEqual(
            delegate.ollama_payload("qwen", "hi"),
            {"model": "qwen", "prompt": "hi", "stream": False},
        )

    def test_the_ollama_response_parser_is_fail_soft(self):
        self.assertEqual(delegate.parse_ollama_response({"response": "text"}), "text")
        self.assertIsNone(delegate.parse_ollama_response({"response": ""}))
        self.assertIsNone(delegate.parse_ollama_response({"response": 7}))
        self.assertIsNone(delegate.parse_ollama_response(["nope"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
