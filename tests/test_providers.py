"""Unit tests for the provider registry (pure) and the provider probe (thin I/O).

Every edge is injected: ``which``, the subprocess runner, the environment mapping, the
HTTP opener and the registry's file reader. Nothing here touches PATH, the network, or
the operator's home directory.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keel import api_delegate, providerprobe, providers
from keel import config as cfg
from keel.runner import CommandResult

#: The registry path the fixtures use, rendered the way the loader renders it.
#: `Registry.path` is `str(Path(...))`, which is `\\registry\\providers.yaml` on
#: Windows — asserting the POSIX spelling passed on three platforms and failed on the
#: fourth, which is exactly the kind of drift a literal in a test buys.
REGISTRY_PATH = str(Path("/registry/providers.yaml"))


def _config(profiles=None, **knobs):
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^1.0",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true", delegate_profiles=profiles or {}, **knobs),
    )


def _registry(text, *, env=None):
    """Parse ``text`` as a registry document through the real loader (injected read)."""
    return providers.load_registry(REGISTRY_PATH, env=env or {}, _read=lambda _path: text)


def _which(*present):
    found = set(present)
    return lambda name: f"/bin/{name}" if name in found else None


def _runner(table):
    """A fake ``run_argv``: ``{("cmd", "arg"): CommandResult}``, missing == exit 127."""

    def run(argv, **kwargs):
        del kwargs
        return table.get(tuple(argv), CommandResult(False, 127, "not found"))

    return run


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, *_args):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.opened = []

    def open(self, request, timeout=None):
        self.opened.append((request.full_url, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def _tags(*names):
    payload = json.dumps({"models": [{"name": name} for name in names]}).encode("utf-8")
    return _FakeOpener(_FakeResponse(payload))


class TestProviderRecord(unittest.TestCase):
    def test_builtin_cli_is_tool_capable_read_only_and_model_selecting(self):
        claude = next(p for p in providers.builtin_providers() if p.name == "claude")
        self.assertEqual(
            claude.capabilities(),
            {"tools": True, "read_only_mode": True, "model_selection": True},
        )

    def test_hosted_and_local_builtins_have_no_tools(self):
        planned = {p.name: p for p in providers.builtin_providers()}
        self.assertFalse(planned["anthropic-api"].capabilities()["tools"])
        self.assertFalse(planned["ollama"].capabilities()["tools"])
        # No tools at all means no read-only *flag* to document; `tools` is the field
        # that says a run could write, and it is already False.
        self.assertFalse(planned["ollama"].capabilities()["read_only_mode"])
        self.assertTrue(planned["anthropic-api"].capabilities()["model_selection"])

    def test_a_builtin_without_a_documented_flag_reports_no_read_only_mode(self):
        provider = providers.Provider(name="new-cli", vendor="new-cli", transport="cli")
        self.assertFalse(provider.capabilities()["read_only_mode"])
        self.assertFalse(provider.capabilities()["model_selection"])

    def test_review_args_are_what_a_non_builtin_read_only_mode_means(self):
        with_args = providers.Provider(
            name="cursor", vendor="cli", transport="cli", source="registry", review_args=("-p",)
        )
        without = providers.Provider(
            name="cursor", vendor="cli", transport="cli", source="registry"
        )
        self.assertTrue(with_args.capabilities()["read_only_mode"])
        self.assertFalse(without.capabilities()["read_only_mode"])

    def test_a_configured_model_alone_counts_as_model_selection(self):
        provider = providers.Provider(
            name="pinned", vendor="cli", transport="cli", source="registry", model="composer-2.5"
        )
        self.assertTrue(provider.capabilities()["model_selection"])

    def test_as_dict_carries_the_key_name_and_never_a_value(self):
        record = providers.Provider(
            name="anthropic-api",
            vendor="anthropic-api",
            transport="api",
            api_key_env="ANTHROPIC_API_KEY",
            effort="high",
            review_args=("-p",),
        ).as_dict()
        self.assertEqual(record["api_key_env"], "ANTHROPIC_API_KEY")
        self.assertEqual(record["effort"], "high")
        self.assertEqual(record["review_args"], ["-p"])
        self.assertIn("capabilities", record)


class TestRegistryPath(unittest.TestCase):
    def test_env_override_wins_and_expands_a_tilde(self):
        path = providers.registry_path(env={"KEEL_PROVIDERS": "~/elsewhere.yaml"})
        self.assertTrue(str(path).endswith("elsewhere.yaml"))
        self.assertNotIn("~", str(path))

    def test_blank_override_falls_back_to_the_home_default(self):
        path = providers.registry_path(env={"KEEL_PROVIDERS": "  ", "HOME": "/home/op"})
        self.assertEqual(path, Path("/home/op/.keel/providers.yaml"))

    def test_explicit_home_beats_the_environment(self):
        path = providers.registry_path(env={"HOME": "/home/op"}, home="/other")
        self.assertEqual(path, Path("/other/.keel/providers.yaml"))

    def test_without_home_in_the_environment_it_uses_the_real_one(self):
        path = providers.registry_path(env={})
        self.assertEqual(path, Path.home() / ".keel" / "providers.yaml")


class TestLoadRegistry(unittest.TestCase):
    def test_a_missing_file_is_an_empty_registry_not_an_error(self):
        registry = providers.load_registry("/nope/providers.yaml", env={})
        self.assertFalse(registry.present)
        self.assertEqual(registry.providers, ())
        self.assertEqual(registry.warnings, ())

    def test_an_unreadable_file_warns_and_never_raises(self):
        def boom(_path):
            raise PermissionError("denied")

        registry = providers.load_registry("/x/providers.yaml", env={}, _read=boom)
        self.assertTrue(registry.present)
        self.assertIn("cannot be read", registry.warnings[0])

    def test_malformed_yaml_warns_and_never_raises(self):
        registry = _registry("providers: [unclosed\n")
        self.assertIn("not valid YAML", registry.warnings[0])
        self.assertEqual(registry.providers, ())

    def test_the_default_path_and_reader_read_a_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".keel").mkdir()
            (home / ".keel" / "providers.yaml").write_text(
                "providers:\n  cursor:\n    transport: cli\n    command: cursor-agent\n",
                encoding="utf-8",
            )
            registry = providers.load_registry(env={"HOME": str(home)})
        self.assertEqual(registry.names(), ("cursor",))
        self.assertTrue(registry.present)

    def test_an_empty_document_registers_nothing(self):
        registry = _registry("")
        self.assertTrue(registry.present)
        self.assertEqual(registry.providers, ())
        self.assertEqual(registry.warnings, ())

    def test_a_non_mapping_document_warns(self):
        self.assertIn("expected a mapping", _registry("- a\n- b\n").warnings[0])

    def test_a_document_without_providers_warns(self):
        self.assertIn("no 'providers:' mapping", _registry("other: 1\n").warnings[0])

    def test_providers_that_is_not_a_mapping_warns(self):
        self.assertIn("must be a mapping", _registry("providers: [a]\n").warnings[0])


class TestRegistryEntries(unittest.TestCase):
    def test_a_cli_entry_carries_command_model_effort_and_review_args(self):
        registry = _registry(
            "providers:\n"
            "  cursor:\n"
            "    transport: cli\n"
            "    command: cursor-agent\n"
            "    model: composer-2.5\n"
            "    effort: high\n"
            "    review_args: ['-p']\n"
        )
        (cursor,) = registry.providers
        self.assertEqual(cursor.vendor, "cli")
        self.assertEqual(cursor.command, "cursor-agent")
        self.assertEqual(cursor.model, "composer-2.5")
        self.assertEqual(cursor.effort, "high")
        self.assertEqual(cursor.review_args, ("-p",))
        self.assertEqual(cursor.model_arg, providers.DEFAULT_MODEL_ARG)
        self.assertEqual(cursor.source, "registry")

    def test_model_arg_is_taken_from_the_entry_when_given(self):
        registry = _registry(
            "providers:\n  aider:\n    transport: cli\n    command: aider\n    model_arg: --llm\n"
        )
        self.assertEqual(registry.providers[0].model_arg, "--llm")

    def test_a_local_entry_needs_a_command_and_selects_no_model_flag(self):
        registry = _registry(
            "providers:\n  llamafile:\n    transport: local\n    command: llamafile\n"
        )
        (local,) = registry.providers
        self.assertEqual(local.vendor, "local")
        self.assertIsNone(local.model_arg)

    def test_an_api_entry_needs_an_endpoint_and_a_key_name(self):
        registry = _registry(
            "providers:\n"
            "  vllm:\n"
            "    transport: api\n"
            "    endpoint: http://127.0.0.1:8000/v1/chat/completions\n"
            "    api_key_env: VLLM_API_KEY\n"
        )
        (vllm,) = registry.providers
        self.assertEqual(vllm.vendor, api_delegate.OPENAI_COMPATIBLE)
        self.assertEqual(vllm.api_key_env, "VLLM_API_KEY")
        self.assertTrue(vllm.capabilities()["model_selection"])

    def test_an_operator_owned_key_name_outside_the_project_allowlist_is_accepted(self):
        # #1011's own example: an operator whose only key is XAI_API_KEY. The project
        # profile allowlist guards a *committed* file; this one is not committed.
        registry = _registry(
            "providers:\n"
            "  xai:\n"
            "    transport: api\n"
            "    endpoint: http://localhost:9000/v1/chat/completions\n"
            "    api_key_env: XAI_API_KEY\n"
        )
        self.assertEqual(registry.warnings, ())
        self.assertEqual(registry.providers[0].api_key_env, "XAI_API_KEY")

    def test_a_remote_endpoint_is_refused_without_the_environment_opt_in(self):
        document = (
            "providers:\n"
            "  groq:\n"
            "    transport: api\n"
            "    endpoint: https://api.groq.com/openai/v1/chat/completions\n"
            "    api_key_env: GROQ_API_KEY\n"
        )
        refused = _registry(document)
        self.assertEqual(refused.providers, ())
        self.assertIn("is not loopback", refused.warnings[0])

        allowed = _registry(document, env={cfg.ALLOW_REMOTE_ENDPOINT_ENV: "1"})
        self.assertEqual(allowed.names(), ("groq",))

    def test_the_remote_refusal_names_the_environment_opt_in(self):
        # The endpoint rules are the project profile's, and their wording ("not in
        # this file") reads oddly in a home-directory registry — so the registry adds
        # its own line naming the variable and where it has to be set.
        registry = _registry(
            "providers:\n"
            "  mine:\n"
            "    transport: api\n"
            "    endpoint: https://gateway.example.com/v1/chat/completions\n"
            "    api_key_env: KEEL_DELEGATE_KEY_MINE\n"
        )
        joined = "\n".join(registry.warnings)
        self.assertIn(cfg.ALLOW_REMOTE_ENDPOINT_ENV, joined)
        self.assertIn("not registered", joined)
        self.assertIn("exported in your shell", joined)

    def test_a_key_name_problem_does_not_blame_the_remote_opt_in(self):
        registry = _registry(
            "providers:\n"
            "  loopback:\n"
            "    transport: api\n"
            "    endpoint: http://127.0.0.1:8000/v1/chat/completions\n"
            "    api_key_env: GITHUB_TOKEN\n"
        )
        joined = "\n".join(registry.warnings)
        self.assertIn("high-privilege system credential", joined)
        self.assertNotIn(cfg.ALLOW_REMOTE_ENDPOINT_ENV, joined)

    def test_a_high_privilege_credential_may_not_be_a_provider_key(self):
        registry = _registry(
            "providers:\n"
            "  sneaky:\n"
            "    transport: api\n"
            "    endpoint: http://localhost:9000/v1/chat/completions\n"
            "    api_key_env: GITHUB_TOKEN\n"
        )
        self.assertEqual(registry.providers, ())
        self.assertIn("high-privilege system credential", registry.warnings[0])

    def test_a_key_env_that_is_not_a_variable_name_is_refused(self):
        registry = _registry(
            "providers:\n"
            "  typo:\n"
            "    transport: api\n"
            "    endpoint: http://localhost:9000/v1/chat/completions\n"
            "    api_key_env: sk-live-not-a-name\n"
        )
        self.assertEqual(registry.providers, ())
        self.assertIn("not a valid environment variable name", registry.warnings[0])

    def test_a_missing_key_env_is_refused(self):
        registry = _registry(
            "providers:\n"
            "  nokey:\n"
            "    transport: api\n"
            "    endpoint: http://localhost:9000/v1/chat/completions\n"
        )
        self.assertEqual(registry.providers, ())
        self.assertIn("requires 'api_key_env'", registry.warnings[0])

    def test_a_command_less_cli_entry_is_refused(self):
        registry = _registry("providers:\n  ghost:\n    transport: cli\n")
        self.assertEqual(registry.providers, ())
        self.assertIn("requires a non-empty 'command'", registry.warnings[0])

    def test_an_unknown_transport_is_refused(self):
        registry = _registry("providers:\n  psychic:\n    transport: telepathy\n")
        self.assertEqual(registry.providers, ())
        self.assertIn("unknown transport", registry.warnings[0])

    def test_a_non_string_name_is_refused_with_the_yaml_reason(self):
        registry = _registry("providers:\n  on:\n    transport: cli\n    command: x\n")
        self.assertEqual(registry.providers, ())
        self.assertIn("not a non-empty string", registry.warnings[0])

    def test_a_name_containing_a_colon_could_never_be_selected(self):
        registry = _registry("providers:\n  'a:b':\n    transport: cli\n    command: x\n")
        self.assertEqual(registry.providers, ())
        self.assertIn("may not contain ':'", registry.warnings[0])

    def test_an_entry_that_is_not_a_mapping_is_refused(self):
        registry = _registry("providers:\n  weird: 3\n")
        self.assertIn("must be a mapping of fields", registry.warnings[0])

    def test_unknown_fields_are_reported_and_ignored(self):
        registry = _registry(
            "providers:\n"
            "  cursor:\n"
            "    transport: cli\n"
            "    command: cursor-agent\n"
            "    temprature: 0.5\n"
        )
        self.assertEqual(registry.names(), ("cursor",))
        self.assertIn("ignoring unknown field(s) temprature", registry.warnings[0])

    def test_review_args_that_are_not_a_list_of_strings_are_ignored(self):
        registry = _registry(
            "providers:\n"
            "  cursor:\n"
            "    transport: cli\n"
            "    command: cursor-agent\n"
            "    review_args: '-p'\n"
        )
        self.assertEqual(registry.providers[0].review_args, ())
        self.assertIn("must be a list of strings", registry.warnings[0])

    def test_blank_strings_read_as_unset(self):
        registry = _registry(
            "providers:\n"
            "  cursor:\n"
            "    transport: cli\n"
            "    command: cursor-agent\n"
            "    model: '   '\n"
            "    effort: 7\n"
        )
        self.assertIsNone(registry.providers[0].model)
        self.assertIsNone(registry.providers[0].effort)

    def test_entries_are_parsed_in_a_deterministic_order(self):
        registry = _registry(
            "providers:\n"
            "  zed:\n    transport: cli\n    command: zed\n"
            "  aider:\n    transport: cli\n    command: aider\n"
        )
        self.assertEqual(registry.names(), ("aider", "zed"))


class TestClashesAndPlan(unittest.TestCase):
    def test_a_registry_entry_may_not_shadow_a_builtin_vendor(self):
        registry = _registry("providers:\n  codex:\n    transport: cli\n    command: codex\n")
        errors = providers.registry_clashes(registry, None)
        self.assertEqual(len(errors), 1)
        self.assertIn(REGISTRY_PATH, errors[0])
        self.assertIn("built-in delegate vendor 'codex'", errors[0])

    def test_a_clash_with_a_project_profile_names_both_sources(self):
        registry = _registry("providers:\n  cursor:\n    transport: cli\n    command: other\n")
        config = _config({"cursor": cfg.DelegateProfile(vendor="cli", command="cursor-agent")})
        (error,) = providers.registry_clashes(registry, config)
        self.assertIn(REGISTRY_PATH, error)
        self.assertIn("knobs.delegate_profiles.cursor", error)
        self.assertIn("project profile wins", error)

    def test_a_distinct_name_is_no_clash(self):
        registry = _registry("providers:\n  aider:\n    transport: cli\n    command: aider\n")
        self.assertEqual(providers.registry_clashes(registry, _config()), [])

    def test_the_plan_is_builtins_then_profiles_then_registry(self):
        registry = _registry(
            "providers:\n"
            "  aider:\n    transport: cli\n    command: aider\n"
            "  claude:\n    transport: cli\n    command: impostor\n"
        )
        config = _config({"cursor": cfg.DelegateProfile(vendor="cli", command="cursor-agent")})
        names = [p.name for p in providers.plan_probes(config, registry)]
        self.assertEqual(names[:7], ["claude", "codex", "agy", "ollama", *sorted_api()])
        self.assertEqual(names[7:], ["cursor", "aider"])
        # The shadowing entry is dropped rather than silently overriding the built-in.
        claude = next(p for p in providers.plan_probes(config, registry) if p.name == "claude")
        self.assertEqual(claude.source, "builtin")

    def test_a_project_profile_wins_over_a_same_named_registry_entry(self):
        registry = _registry("providers:\n  cursor:\n    transport: cli\n    command: impostor\n")
        config = _config({"cursor": cfg.DelegateProfile(vendor="cli", command="cursor-agent")})
        cursor = next(p for p in providers.plan_probes(config, registry) if p.name == "cursor")
        self.assertEqual(cursor.source, "profile")
        self.assertEqual(cursor.command, "cursor-agent")

    def test_without_a_registry_the_plan_is_the_builtins_and_the_profiles(self):
        self.assertEqual(len(providers.plan_probes(None)), len(providers.builtin_providers()))

    def test_profiles_carry_review_args_endpoint_and_model_arg(self):
        config = _config(
            {
                "cursor": cfg.DelegateProfile(
                    vendor="cli",
                    command="cursor-agent",
                    args=("-p", "--force"),
                    review_args=("-p",),
                    model_arg="--llm",
                ),
                "plain": cfg.DelegateProfile(vendor="cli", command="plain", model_arg=""),
                "router": cfg.DelegateProfile(
                    vendor=api_delegate.OPENAI_COMPATIBLE,
                    endpoint="http://localhost:1/v1/chat/completions",
                    api_key_env="OPENROUTER_API_KEY",
                    model="qwen",
                ),
            }
        )
        planned = {p.name: p for p in providers.profile_providers(config)}
        self.assertEqual(planned["cursor"].review_args, ("-p",))
        self.assertEqual(planned["cursor"].model_arg, "--llm")
        self.assertTrue(planned["cursor"].capabilities()["read_only_mode"])
        self.assertEqual(planned["plain"].review_args, ())
        self.assertIsNone(planned["plain"].model_arg)
        self.assertFalse(planned["plain"].capabilities()["model_selection"])
        self.assertEqual(planned["router"].transport, "api")
        self.assertIsNone(planned["router"].model_arg)

    def test_tool_capable_and_distinct_vendors(self):
        plan = providers.plan_probes(None)
        self.assertEqual(providers.tool_capable(plan), ("claude", "codex", "agy"))
        self.assertEqual(len(providers.distinct_vendors(plan)), len(plan))
        twice = (plan[0], plan[0])
        self.assertEqual(providers.distinct_vendors(twice), ("claude",))


def sorted_api():
    from keel import agents

    return list(agents.API_VENDORS)


class TestModelListingParsers(unittest.TestCase):
    def test_reads_one_model_per_line_through_bullets_and_columns(self):
        text = (
            "# available models\n"
            "\n"
            "Models:\n"
            "- gemini-3.8-flash-high    fast, cheap\n"
            "* qwen2.5:7b\n"
            "anthropic/claude-opus-4-5\n"
            "-----\n"
            "...\n"
            "(note) a prose line whose first token is not model-shaped\n"
            "gemini-3.8-flash-high\n"
        )
        self.assertEqual(
            providers.parse_model_lines(text),
            ("gemini-3.8-flash-high", "qwen2.5:7b", "anthropic/claude-opus-4-5"),
        )

    def test_empty_output_is_no_listing_not_an_error(self):
        self.assertEqual(providers.parse_model_lines(""), ())
        self.assertEqual(providers.parse_model_lines("   \n"), ())

    def test_a_runaway_listing_is_capped(self):
        text = "\n".join(f"model-{i}" for i in range(providers.MAX_LISTED_MODELS + 20))
        self.assertEqual(len(providers.parse_model_lines(text)), providers.MAX_LISTED_MODELS)

    def test_tag_payload_reads_name_then_model(self):
        payload = {
            "models": [
                {"name": "qwen2.5:7b"},
                {"model": "llama3.1:8b"},
                {"name": "   "},
                "not-a-dict",
            ]
        }
        self.assertEqual(providers.parse_tag_payload(payload), ("qwen2.5:7b", "llama3.1:8b"))

    def test_a_malformed_tag_payload_yields_nothing(self):
        self.assertEqual(providers.parse_tag_payload(["nope"]), ())
        self.assertEqual(providers.parse_tag_payload({"models": "nope"}), ())

    def test_a_runaway_tag_payload_is_capped(self):
        payload = {"models": [{"name": f"m{i}"} for i in range(providers.MAX_LISTED_MODELS + 5)]}
        self.assertEqual(len(providers.parse_tag_payload(payload)), providers.MAX_LISTED_MODELS)


class TestProbeCli(unittest.TestCase):
    def test_a_cli_on_path_that_answers_version_is_available(self):
        plan = [p for p in providers.builtin_providers() if p.name == "claude"]
        (result,) = providerprobe.probe_providers(
            plan,
            _which=_which("claude"),
            _run=_runner({("claude", "--version"): CommandResult(True, 0, "", stdout="2.1.0\n")}),
            _env={},
        )
        self.assertTrue(result.available)
        self.assertIn("/bin/claude", result.reason)
        self.assertIn("2.1.0", result.reason)

    def test_a_cli_that_answers_nothing_still_reports_its_path(self):
        plan = [p for p in providers.builtin_providers() if p.name == "claude"]
        (result,) = providerprobe.probe_providers(
            plan,
            _which=_which("claude"),
            _run=_runner({("claude", "--version"): CommandResult(True, 0, "  \n")}),
            _env={},
        )
        self.assertTrue(result.available)
        self.assertEqual(result.reason, "/bin/claude")

    def test_a_cli_not_on_path_is_unavailable_with_a_reason(self):
        plan = [p for p in providers.builtin_providers() if p.name == "codex"]
        (result,) = providerprobe.probe_providers(plan, _which=_which(), _run=_runner({}), _env={})
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "codex not found on PATH")

    def test_a_present_but_broken_cli_is_not_reported_as_usable(self):
        plan = [p for p in providers.builtin_providers() if p.name == "codex"]
        (result,) = providerprobe.probe_providers(
            plan,
            _which=_which("codex"),
            _run=_runner({("codex", "--version"): CommandResult(False, 3, "boom")}),
            _env={},
        )
        self.assertFalse(result.available)
        self.assertIn("exit 3", result.reason)

    def test_a_hanging_cli_is_time_boxed(self):
        seen = {}

        def run(argv, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return CommandResult(False, 124, "timed out", timed_out=True)

        plan = [p for p in providers.builtin_providers() if p.name == "agy"]
        (result,) = providerprobe.probe_providers(plan, _which=_which("agy"), _run=run, _env={})
        self.assertFalse(result.available)
        self.assertIn(f"timed out after {providerprobe.PROBE_TIMEOUT_S}s", result.reason)
        self.assertEqual(seen["timeout"], providerprobe.PROBE_TIMEOUT_S)

    def test_agy_reports_its_model_list(self):
        plan = [p for p in providers.builtin_providers() if p.name == "agy"]
        (result,) = providerprobe.probe_providers(
            plan,
            _which=_which("agy"),
            _run=_runner(
                {
                    ("agy", "--version"): CommandResult(True, 0, "", stdout="1.1.25\n"),
                    ("agy", "models"): CommandResult(
                        True, 0, "", stdout="gemini-3.8-flash-high\ngemini-3.7-flash-low\n"
                    ),
                }
            ),
            _env={},
        )
        self.assertEqual(result.models, ("gemini-3.8-flash-high", "gemini-3.7-flash-low"))

    def test_a_failing_model_listing_is_not_an_unavailable_provider(self):
        plan = [p for p in providers.builtin_providers() if p.name == "agy"]
        (result,) = providerprobe.probe_providers(
            plan,
            _which=_which("agy"),
            _run=_runner({("agy", "--version"): CommandResult(True, 0, "", stdout="1.1.25")}),
            _env={},
        )
        self.assertTrue(result.available)
        self.assertEqual(result.models, ())

    def test_only_agy_is_asked_for_a_model_list(self):
        calls = []

        def run(argv, **kwargs):
            del kwargs
            calls.append(tuple(argv))
            return CommandResult(True, 0, "", stdout="1.0")

        plan = [p for p in providers.builtin_providers() if p.name == "claude"]
        providerprobe.probe_providers(plan, _which=_which("claude"), _run=run, _env={})
        self.assertEqual(calls, [("claude", "--version")])

    def test_a_command_less_provider_says_so_instead_of_crashing(self):
        plan = [providers.Provider(name="ghost", vendor="cli", transport="cli")]
        (result,) = providerprobe.probe_providers(plan, _which=_which(), _run=_runner({}), _env={})
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "no command configured")

    def test_a_seam_that_raises_becomes_a_row_not_a_traceback(self):
        def boom(_name):
            raise RuntimeError("PATH exploded")

        plan = [p for p in providers.builtin_providers() if p.name == "claude"]
        (result,) = providerprobe.probe_providers(plan, _which=boom, _run=_runner({}), _env={})
        self.assertFalse(result.available)
        self.assertIn("probe failed: PATH exploded", result.reason)


class TestProbeApi(unittest.TestCase):
    def test_a_builtin_key_is_reported_by_name_never_by_value(self):
        plan = [p for p in providers.builtin_providers() if p.name == "anthropic-api"]
        (result,) = providerprobe.probe_providers(
            plan, _which=_which(), _run=_runner({}), _env={"ANTHROPIC_API_KEY": "sk-secret"}
        )
        self.assertTrue(result.available)
        self.assertIn("ANTHROPIC_API_KEY is set", result.reason)
        self.assertNotIn("sk-secret", result.reason)

    def test_a_missing_builtin_key_names_the_variable_to_set(self):
        plan = [p for p in providers.builtin_providers() if p.name == "openai-api"]
        (result,) = providerprobe.probe_providers(plan, _which=_which(), _run=_runner({}), _env={})
        self.assertFalse(result.available)
        self.assertIn("OPENAI_API_KEY is not set", result.reason)

    def test_a_profile_key_probe_names_the_endpoint_it_belongs_to(self):
        config = _config(
            {
                "router": cfg.DelegateProfile(
                    vendor=api_delegate.OPENAI_COMPATIBLE,
                    endpoint="http://localhost:1/v1/chat/completions",
                    api_key_env="OPENROUTER_API_KEY",
                )
            }
        )
        plan = providers.profile_providers(config)
        set_result, unset_result = (
            providerprobe.probe_providers(plan, _which=_which(), _run=_runner({}), _env=env)[0]
            for env in ({"OPENROUTER_API_KEY": "k"}, {"OPENROUTER_API_KEY": "  "})
        )
        self.assertTrue(set_result.available)
        self.assertIn("http://localhost:1/v1/chat/completions", set_result.reason)
        self.assertFalse(unset_result.available)

    def test_an_api_provider_without_a_key_name_says_so(self):
        plan = [providers.Provider(name="nameless", vendor="x", transport="api")]
        (result,) = providerprobe.probe_providers(plan, _which=_which(), _run=_runner({}), _env={})
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "no api_key_env configured")

    def test_the_api_probe_makes_no_request(self):
        opener = _FakeOpener(error=AssertionError("the api probe must not dial anything"))
        plan = [p for p in providers.builtin_providers() if p.transport == "api"]
        results = providerprobe.probe_providers(
            plan, _which=_which(), _run=_runner({}), _env={}, _opener=opener
        )
        self.assertEqual(opener.opened, [])
        self.assertEqual(len(results), 3)


class TestProbeLocal(unittest.TestCase):
    def _ollama(self):
        return [p for p in providers.builtin_providers() if p.name == "ollama"]

    def test_the_cli_and_the_server_together_make_ollama_available(self):
        opener = _tags("qwen2.5:7b", "llama3.1:8b")
        (result,) = providerprobe.probe_providers(
            self._ollama(), _which=_which("ollama"), _run=_runner({}), _env={}, _opener=opener
        )
        self.assertTrue(result.available)
        self.assertEqual(result.models, ("qwen2.5:7b", "llama3.1:8b"))
        self.assertIn("2 model(s)", result.reason)
        self.assertEqual(opener.opened, [(providers.OLLAMA_TAGS_URL, providerprobe.HTTP_TIMEOUT_S)])

    def test_an_unreachable_server_is_unavailable_with_the_url(self):
        opener = _FakeOpener(error=OSError("connection refused"))
        (result,) = providerprobe.probe_providers(
            self._ollama(), _which=_which("ollama"), _run=_runner({}), _env={}, _opener=opener
        )
        self.assertFalse(result.available)
        self.assertIn(providers.OLLAMA_TAGS_URL, result.reason)
        self.assertIn("connection refused", result.reason)

    def test_a_non_json_answer_is_unavailable(self):
        opener = _FakeOpener(_FakeResponse(b"<html>nope</html>"))
        (result,) = providerprobe.probe_providers(
            self._ollama(), _which=_which("ollama"), _run=_runner({}), _env={}, _opener=opener
        )
        self.assertFalse(result.available)
        self.assertIn("not valid JSON", result.reason)

    def test_a_serving_machine_without_the_cli_says_which_half_is_missing(self):
        (result,) = providerprobe.probe_providers(
            self._ollama(), _which=_which(), _run=_runner({}), _env={}, _opener=_tags("qwen")
        )
        self.assertFalse(result.available)
        self.assertIn("ollama not found on PATH", result.reason)
        self.assertIn("answers", result.reason)

    def test_the_default_opener_is_the_shared_non_redirecting_one(self):
        opener = _tags("qwen")
        with patch.object(api_delegate, "build_http_only_opener", return_value=opener) as built:
            providerprobe.probe_providers(
                self._ollama(), _which=_which("ollama"), _run=_runner({}), _env={}
            )
        built.assert_called_once_with()
        self.assertEqual([url for url, _ in opener.opened], [providers.OLLAMA_TAGS_URL])

    def test_a_registry_local_entry_is_probed_by_command_alone(self):
        registry = _registry(
            "providers:\n  llamafile:\n    transport: local\n    command: llamafile\n"
        )
        opener = _FakeOpener(error=AssertionError("no endpoint from a file is ever dialed"))
        found, missing = (
            providerprobe.probe_providers(
                registry.providers, _which=which, _run=_runner({}), _env={}, _opener=opener
            )[0]
            for which in (_which("llamafile"), _which())
        )
        self.assertTrue(found.available)
        self.assertEqual(found.reason, "/bin/llamafile")
        self.assertFalse(missing.available)
        self.assertEqual(opener.opened, [])


class TestReport(unittest.TestCase):
    def test_collect_assembles_probe_registry_and_clashes(self):
        document = (
            "providers:\n"
            "  aider:\n    transport: cli\n    command: aider\n"
            "  codex:\n    transport: cli\n    command: codex\n"
            "  broken:\n    transport: telepathy\n"
        )
        report = providerprobe.collect(
            _config(),
            registry_path=REGISTRY_PATH,
            _which=_which("claude", "aider"),
            _run=_runner(
                {
                    ("claude", "--version"): CommandResult(True, 0, "", stdout="2.1.0"),
                    ("aider", "--version"): CommandResult(True, 0, "", stdout="0.9"),
                }
            ),
            _env={},
            _opener=_FakeOpener(error=OSError("refused")),
            _read=lambda _path: document,
        )
        self.assertEqual(report["registry_path"], REGISTRY_PATH)
        self.assertTrue(report["registry_present"])
        self.assertEqual(report["available"], 2)
        self.assertEqual(report["total"], len(providers.builtin_providers()) + 1)
        self.assertTrue(any("unknown transport" in w for w in report["warnings"]))
        self.assertTrue(any("built-in delegate vendor 'codex'" in e for e in report["errors"]))
        names = [row["name"] for row in report["providers"]]
        self.assertEqual(names[-1], "aider")

    def test_collect_reads_the_registry_from_disk_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "providers.yaml"
            path.write_text(
                "providers:\n  aider:\n    transport: cli\n    command: aider\n", encoding="utf-8"
            )
            report = providerprobe.collect(
                None,
                registry_path=str(path),
                _which=_which(),
                _run=_runner({}),
                _env={},
                _opener=_FakeOpener(error=OSError("refused")),
            )
        self.assertEqual(report["providers"][-1]["name"], "aider")
        self.assertEqual(report["available"], 0)

    def test_the_report_is_json_serialisable_and_lists_every_provider(self):
        report = providerprobe.collect(
            None,
            registry_path="/nope/providers.yaml",
            _which=_which(),
            _run=_runner({}),
            _env={},
            _opener=_FakeOpener(error=OSError("refused")),
        )
        text = json.dumps(report, sort_keys=True)
        self.assertIn('"schema_version": "keel.providers.v1"', text)
        self.assertFalse(report["registry_present"])
        self.assertEqual(report["errors"], [])


if __name__ == "__main__":
    unittest.main()
