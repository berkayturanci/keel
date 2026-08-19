"""Load + validate a keel ``project.yaml`` into a typed, immutable config.

Pure and deterministic: parsing the same YAML always yields the same
``ProjectConfig`` and the same :func:`config_hash`. The only I/O is reading the
file in :func:`load_config`; everything else operates on plain data so it is
trivially unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import jsonschema_min
from . import yaml_helper as yaml
from .capabilities import validate_names

# Keep both names coming from `model` — the one module with no intra-package imports.
# Taking DEFAULT_GATE_TIMEOUT_S from `gates` instead closes a config -> gates -> config
# cycle (gates names config in its TYPE_CHECKING imports). SLOTS: source of truth for
# the named slots; DEFAULT_GATE_TIMEOUT_S: shared with the gate planner and runner.
from .model import DEFAULT_GATE_TIMEOUT_S, DEFAULT_JURY_TIMEOUT_S, SLOTS

SCHEMA_PATH = Path(__file__).parent / "schema" / "project.schema.json"

DEFAULT_EXTENSIONS_DIR = ".keel/extensions"

#: Vendors a ``knobs.delegate_profiles`` entry may declare. ``cli`` drives a local
#: coding-agent CLI (#659); ``openai-compatible`` reaches any OpenAI-shaped hosted API
#: — OpenRouter, Groq, DeepSeek, Together, LiteLLM, vLLM — from config (#666).
DELEGATE_PROFILE_VENDORS = ("cli", "openai-compatible")

#: Vendors whose profile must name an executable.
_COMMAND_VENDORS = ("cli",)
#: Vendors whose profile must name an endpoint + the env var holding its key.
_ENDPOINT_VENDORS = ("openai-compatible",)

#: Hosts an ``openai-compatible`` endpoint may use without an explicit opt-in.
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")

#: High-privilege system credentials that delegate profiles may not target as API keys.
BLOCKED_ENV_KEY_NAMES = frozenset({
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
    "SSH_AUTH_SOCK",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "SLACK_TOKEN",
    "DISCORD_TOKEN",
})

BLOCKED_ENV_PREFIXES = (
    "GITHUB_",
    "GH_",
    "AWS_",
    "NPM_",
    "PYPI_",
    "SSH_",
)

#: Environment opt-in for a **non-loopback** endpoint. It lives in the environment and
#: deliberately **not** in ``project.yaml``: the threat model here is an
#: attacker-influenced config, so the switch that permits reaching a remote host must
#: sit outside the surface an attacker would control. Ported from ai-jury's
#: ``JURY_ALLOW_REMOTE_ENDPOINT`` (same reasoning, same default-closed posture).
ALLOW_REMOTE_ENDPOINT_ENV = "KEEL_ALLOW_REMOTE_ENDPOINT"

#: How a ``cli`` profile's prompt reaches the command. ``stdin`` stays the default
#: (positional-arg passing hangs some CLIs); ``arg`` is the opt-in for CLIs whose usage
#: makes the prompt a positional argument (e.g. ``cursor-agent``).
DELEGATE_PROMPT_MODES = ("stdin", "arg")
DEFAULT_PROMPT_MODE = "stdin"

#: Flag a ``cli`` profile's command takes the model on. Near-universal across coding-agent
#: CLIs (``cursor-agent``, ``gemini``, Aider all spell it ``--model``), but configurable
#: because "arbitrary CLI" is the whole point and nothing guarantees the spelling.
DEFAULT_MODEL_ARG = "--model"

__all__ = ["SLOTS", "DEFAULT_EXTENSIONS_DIR", "DELEGATE_PROFILE_VENDORS",
           "LOOPBACK_HOSTS", "ALLOW_REMOTE_ENDPOINT_ENV",
           "DELEGATE_PROMPT_MODES", "DEFAULT_PROMPT_MODE", "DEFAULT_MODEL_ARG",
           "Automation", "DelegateProfile",
           "Knobs", "ProjectConfig", "ConfigError", "load_config", "parse_config",
           "validate_data", "load_schema", "config_hash", "delegate_profiles_dict"]


class ConfigError(ValueError):
    """Raised when a project config fails schema validation."""

    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid keel config {source}:\n  - {joined}")


def load_schema() -> dict:
    """Load the bundled JSON Schema for ``project.yaml``."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_data(data: Any, schema: dict | None = None) -> list[str]:
    """Return schema-validation errors for raw config data (empty == valid)."""
    return jsonschema_min.validate(data, schema if schema is not None else load_schema())


@dataclass(frozen=True)
class DelegateProfile:
    """A named generic-delegate vendor, referenced as ``--delegate <name>``.

    Turns provider support into configuration: a ``cli`` profile names a local
    coding-agent CLI (``command``) and how its prompt is delivered (``prompt_mode``),
    so ``cursor-agent``/``gemini``/Aider/Goose become config entries rather than code
    changes. ``command`` is operator-authored config with the same trust level as
    ``build_gate_cmd`` — it is never taken from PR content or agent output.
    """

    vendor: str
    command: str | None = None
    #: Fixed flags the command always needs, e.g. ``["-p", "--force"]`` for
    #: ``cursor-agent`` (print mode + non-interactive approval). ``command`` is one
    #: executable, so without this an operator would have to smuggle flags into it as a
    #: string keel would then treat as a filename.
    args: tuple[str, ...] = ()
    #: Flags for the **reviewer** role, when they must differ from ``args``. s7 asks a
    #: reviewer for findings only, but ``args`` typically carries the implementer's
    #: write-enabling flags (``--force`` approves edits non-interactively). Falls back to
    #: ``args`` when unset — and keel cannot *enforce* read-only for an arbitrary CLI, so
    #: this is the operator's lever, not a guarantee. See :meth:`role_args`.
    review_args: tuple[str, ...] | None = None
    prompt_mode: str = DEFAULT_PROMPT_MODE
    model: str | None = None
    #: How the effective model reaches the command: ``<model_arg> <model>``. Without it
    #: the documented model precedence would be unimplementable for an arbitrary CLI —
    #: attribution would record a model that was never actually selected.
    model_arg: str = DEFAULT_MODEL_ARG
    #: ``openai-compatible`` only: the OpenAI-shaped chat-completions URL. Validated
    #: by :func:`endpoint_issues` — loopback by default, remote behind an env opt-in.
    endpoint: str | None = None
    #: ``openai-compatible`` only: the **name** of the env var holding the API key.
    #: Never the key. Profile config is serialised into the command contract and
    #: hashed into ``config_hash``, so a value here would be published.
    api_key_env: str | None = None

    def role_args(self, *, review: bool = False) -> tuple[str, ...]:
        """Flags for this role: ``review_args`` for a reviewer when set, else ``args``."""
        if review and self.review_args is not None:
            return self.review_args
        return self.args


@dataclass(frozen=True)
class Knobs:
    """Per-project values consumed by the (otherwise neutral) backbone steps."""

    build_gate_cmd: str
    lint_cmd: str | None = None
    implementer_agents: dict[str, str] = field(default_factory=dict)
    #: Profile name -> generic delegate vendor config. Never shadows a built-in vendor
    #: (``claude``/``codex``/``agy``/``ollama``/``*-api``); that is a validation error.
    delegate_profiles: dict[str, DelegateProfile] = field(default_factory=dict)
    tier3_globs: tuple[str, ...] = ()
    ci_workflows: dict[str, str] = field(default_factory=dict)
    docs_gate_paths: tuple[str, ...] = ()
    docs_only_allowlist: tuple[str, ...] = ()
    sot_doc: str | None = None
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    evidence_gate_label: str = "keel:ship"
    evidence_require_distinct_vendors: bool = False
    #: Swarm landings enforce the same per-PR review-evidence contract as ship
    #: s10. Turning this off is the explicit, logged opt-out #828 requires: the
    #: exception lives in config where a reviewer can see it, never in a
    #: driver's judgement call under time pressure.
    swarm_review_evidence: bool = True
    #: Wall-clock seconds a command gate may run before it is killed. Raise this on a
    #: slow host; a single slower gate can override it with ``timeout:`` frontmatter.
    gate_timeout_s: int = DEFAULT_GATE_TIMEOUT_S
    #: Wall-clock seconds the ``jury`` built-in may run. Separate from gate_timeout_s:
    #: a cross-vendor panel and a test suite have unrelated runtimes.
    jury_timeout_s: int = DEFAULT_JURY_TIMEOUT_S


@dataclass(frozen=True)
class Automation:
    """Trusted unattended-run consent defaults."""

    approved_scopes: tuple[str, ...] = ()
    operator: str | None = None


@dataclass(frozen=True)
class ProjectConfig:
    """A resolved, immutable keel project config."""

    extends: str
    core_version: str
    base_branch: str
    knobs: Knobs
    owner: str | None = None
    repo: str | None = None
    platform: str | None = None
    timezone: str | None = None
    merge_window: str | None = None
    merge_window_mode: str = "freeze"
    consent_mode: str = "explicit"
    gates: tuple[str, ...] = ()
    extensions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    extensions_dir: str = DEFAULT_EXTENSIONS_DIR
    policy_pack: dict[str, Any] = field(default_factory=dict)
    automation: Automation = field(default_factory=Automation)

    def slot(self, name: str) -> tuple[str, ...]:
        """Extension files registered for a named slot (``()`` if none)."""
        if name not in SLOTS:
            raise KeyError(f"unknown slot {name!r}; valid slots: {', '.join(SLOTS)}")
        return self.extensions.get(name, ())


def _build(data: dict) -> ProjectConfig:
    k = data["knobs"]
    knobs = Knobs(
        build_gate_cmd=k["build_gate_cmd"],
        lint_cmd=k.get("lint_cmd"),
        implementer_agents=dict(k.get("implementer_agents", {})),
        delegate_profiles={
            name: DelegateProfile(
                vendor=profile["vendor"],
                command=profile.get("command"),
                args=tuple(profile.get("args", ())),
                # An explicit null round-trips as "unset" — distinct from [], which
                # means "the reviewer takes no flags at all".
                review_args=(
                    tuple(profile["review_args"])
                    if profile.get("review_args") is not None
                    else None
                ),
                prompt_mode=profile.get("prompt_mode", DEFAULT_PROMPT_MODE),
                model=profile.get("model"),
                model_arg=profile.get("model_arg") or DEFAULT_MODEL_ARG,
                endpoint=profile.get("endpoint"),
                api_key_env=profile.get("api_key_env"),
            )
            for name, profile in k.get("delegate_profiles", {}).items()
        },
        tier3_globs=tuple(k.get("tier3_globs", [])),
        ci_workflows=dict(k.get("ci_workflows", {})),
        docs_gate_paths=tuple(k.get("docs_gate_paths", [])),
        docs_only_allowlist=tuple(k.get("docs_only_allowlist", [])),
        sot_doc=k.get("sot_doc"),
        required_capabilities=tuple(k.get("required_capabilities", [])),
        optional_capabilities=tuple(k.get("optional_capabilities", [])),
        evidence_gate_label=k.get("evidence_gate_label", "keel:ship"),
        evidence_require_distinct_vendors=bool(k.get("evidence_require_distinct_vendors", False)),
        swarm_review_evidence=bool(k.get("swarm_review_evidence", True)),
        gate_timeout_s=int(k.get("gate_timeout_s", DEFAULT_GATE_TIMEOUT_S)),
        jury_timeout_s=int(k.get("jury_timeout_s", DEFAULT_JURY_TIMEOUT_S)),
    )
    extensions = {slot: tuple(files) for slot, files in data.get("extensions", {}).items()}
    automation_data = data.get("automation", {})
    automation_scopes = tuple(dict.fromkeys(automation_data.get("approved_scopes", [])))
    return ProjectConfig(
        extends=data["extends"],
        core_version=data["core_version"],
        base_branch=data["base_branch"],
        knobs=knobs,
        owner=data.get("owner"),
        repo=data.get("repo"),
        platform=data.get("platform"),
        timezone=data.get("timezone"),
        merge_window=data.get("merge_window"),
        merge_window_mode=data.get("merge_window_mode", "freeze"),
        consent_mode=data.get("consent_mode", "explicit"),
        gates=tuple(data.get("gates", [])),
        extensions=extensions,
        extensions_dir=data.get("extensions_dir", DEFAULT_EXTENSIONS_DIR),
        policy_pack=json.loads(json.dumps(data.get("policy_pack", {}), sort_keys=True)),
        automation=Automation(
            approved_scopes=tuple(sorted(automation_scopes)),
            operator=automation_data.get("operator"),
        ),
    )


def parse_config(data: Any, *, source: str = "<dict>", schema: dict | None = None) -> ProjectConfig:
    """Validate raw data and build a :class:`ProjectConfig` (raises on error)."""
    if not isinstance(data, dict):
        raise ConfigError(source, [f"$: expected an object (got {type(data).__name__})"])
    errors = validate_data(data, schema)
    if isinstance(data, dict) and isinstance(data.get("knobs"), dict):
        knobs = data["knobs"]
        errors.extend(validate_names(
            tuple(knobs.get("required_capabilities", [])),
            source=f"{source}: knobs.required_capabilities",
        ))
        errors.extend(validate_names(
            tuple(knobs.get("optional_capabilities", [])),
            source=f"{source}: knobs.optional_capabilities",
        ))
        errors.extend(_validate_delegate_profiles(
            knobs.get("delegate_profiles", {}),
            source=f"{source}: knobs.delegate_profiles",
        ))
    if isinstance(data, dict) and isinstance(data.get("policy_pack"), dict):
        for path, names in _policy_capability_fields(data["policy_pack"]):
            errors.extend(validate_names(tuple(names), source=f"{source}: {path}"))
    if errors:
        raise ConfigError(source, errors)
    return _build(data)


def load_config(path: str | Path) -> ProjectConfig:
    """Read + validate a ``project.yaml`` from disk."""
    path = Path(path)
    try:
        data = yaml.load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(str(path), [f"YAML syntax error: {exc}"]) from exc
    return parse_config(data, source=str(path))


def config_hash(config: ProjectConfig) -> str:
    """Stable SHA-256 over the canonicalised config (cache key / determinism)."""
    payload = json.dumps(_canonical(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_env_var_name(value: str) -> bool:
    """Cheap shape check that ``api_key_env`` is a *name*, not a pasted secret."""
    return bool(value) and not value[0].isdigit() and all(
        ch.isalnum() or ch == "_" for ch in value
    )


def endpoint_issues(endpoint: Any, *, where: str, env=None) -> list[str]:
    """Validate an ``openai-compatible`` endpoint URL. Empty list == acceptable.

    A config-supplied URL is the one genuinely new risk in #666: every other keel
    delegate talks to a hardcoded constant, which is why their SSRF story is trivial.
    Letting config name the host makes ``project.yaml`` a request-forgery primitive
    pointed wherever it says, including cloud-metadata addresses like
    ``169.254.169.254``. Ported from ai-jury's ``_endpoint_issues`` rather than
    reinvented — same decisions, same default-closed posture:

    * a non-``http``/``https`` scheme is refused, which blocks ``file://``, ``ftp://``
      and the other SSRF primitives;
    * a malformed URL is a config error, not a stack trace out of ``keel validate``;
    * a **non-loopback** host is refused unless the operator sets
      :data:`ALLOW_REMOTE_ENDPOINT_ENV` in the environment. The opt-in is env-only on
      purpose: an attacker who can edit config must not be able to grant it.

    Plaintext ``http://`` to a permitted remote host is allowed but noted in the
    message, since the prompt (and the diff in it) would cross the network in clear.
    """
    env = os.environ if env is None else env
    if not isinstance(endpoint, str) or not endpoint.strip():
        return [f"{where}: vendor 'openai-compatible' requires a non-empty 'endpoint'"]
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
    except ValueError:
        # urlsplit raises on e.g. "http://[::1" — by definition not a usable endpoint.
        return [f"{where}: endpoint {endpoint!r} is not a valid URL"]
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return [
            f"{where}: endpoint scheme {parsed.scheme or '(none)'!r} is not allowed; "
            "use http or https"
        ]
    if host in LOOPBACK_HOSTS:
        return []
    if not env.get(ALLOW_REMOTE_ENDPOINT_ENV):
        return [
            f"{where}: endpoint host {host or '(none)'!r} is not loopback; a remote "
            "model server (including internal and cloud-metadata addresses) is refused "
            f"by default. Set {ALLOW_REMOTE_ENDPOINT_ENV}=1 in the environment — not in "
            "this file — to allow a trusted remote endpoint"
        ]
    return []


def _validate_delegate_profiles(profiles: Any, *, source: str) -> list[str]:
    """Return semantic errors for ``knobs.delegate_profiles`` (empty == valid).

    The schema owns the *shape* (object of objects, `vendor` required, field types);
    this owns the *meaning*: which vendors exist, what each vendor requires, and the
    fail-closed rule that a profile may never shadow a built-in delegate vendor.
    """
    # Local import on purpose: ``agents`` imports this module for ``ProjectConfig``, so
    # naming it at module scope would close a real config <-> agents cycle. The vendor
    # vocabulary belongs next to the dispatch logic in ``agents``, so the import moves
    # instead of the constant (same pattern as ``runtime._api_token_capability``).
    from .agents import BUILTIN_DELEGATE_VENDORS

    errors: list[str] = []
    if not isinstance(profiles, dict):
        return errors  # the schema already reported the wrong shape
    for name, profile in profiles.items():
        where = f"{source}.{name}"
        # A YAML mapping key is not necessarily a string: SafeLoader resolves an
        # unquoted ``on:``/``2:``/``~:`` to bool/int/None, and the JSON schema validates
        # property *values* only, never key types. So this has to be the first check —
        # everything below assumes ``str`` methods, and reaching them with a bool raised
        # an uncaught AttributeError out of ``keel validate``.
        if not isinstance(name, str):
            errors.append(
                f"{source}: delegate profile name {name!r} is {type(name).__name__}, not a "
                "string — quote the key (YAML reads a bare on/off/yes/no/true/false as a "
                "boolean and a bare number as an int)"
            )
            continue
        if name in BUILTIN_DELEGATE_VENDORS:
            errors.append(
                f"{where}: profile name {name!r} shadows a built-in delegate vendor; "
                f"built-ins always win and may not be redefined "
                f"({', '.join(BUILTIN_DELEGATE_VENDORS)}) — rename the profile"
            )
        elif name in DELEGATE_PROFILE_VENDORS:
            # `delegate_profile` exists to say *which* CLI ran. A profile named after its
            # own vendor makes every attribution field read "cli", which is exactly the
            # ambiguity the field was added to remove.
            errors.append(
                f"{where}: profile name {name!r} is a delegate vendor name and would make "
                "attribution ambiguous — agent:cli, system 'cli' and delegate_profile "
                "'cli' would all say the same nothing. Name it after the CLI, e.g. "
                "'cursor'"
            )
        # A name that can never be selected is a config error, not a silent dead entry:
        # ``--delegate`` is split on the first colon, so a name containing one resolves
        # to a different (missing) profile, and an empty name reads as no delegate at all.
        if not name.strip():
            errors.append(
                f"{source}: a delegate profile name may not be empty or blank — "
                "an empty --delegate reads as no delegate at all"
            )
        elif ":" in name:
            errors.append(
                f"{where}: profile name {name!r} may not contain ':' — --delegate splits "
                "on the first colon to separate the profile from a per-run model, so this "
                "name could never be selected"
            )
        if not isinstance(profile, dict) or "vendor" not in profile:
            continue  # shape + required-field errors are the schema's job
        vendor = profile["vendor"]
        if vendor not in DELEGATE_PROFILE_VENDORS:
            errors.append(
                f"{where}: unknown delegate vendor {vendor!r}; "
                f"valid: {', '.join(DELEGATE_PROFILE_VENDORS)}"
            )
        elif vendor in _COMMAND_VENDORS and not profile.get("command"):
            errors.append(
                f"{where}: vendor {vendor!r} requires a non-empty 'command' — the "
                "executable keel runs (e.g. cursor-agent)"
            )
        elif vendor in _ENDPOINT_VENDORS:
            errors.extend(endpoint_issues(profile.get("endpoint"), where=where))
            key_env = profile.get("api_key_env")
            if not key_env or not isinstance(key_env, str) or not key_env.strip():
                errors.append(
                    f"{where}: vendor {vendor!r} requires 'api_key_env' — the *name* of "
                    "the environment variable holding the key. Never the key itself: "
                    "profile config is serialised into the command contract and hashed "
                    "into config_hash, so a value here would be published"
                )
            elif not _is_env_var_name(key_env):
                errors.append(
                    f"{where}: api_key_env {key_env!r} is not a valid environment "
                    "variable name (letters, digits, underscore; not starting with a "
                    "digit) — this field takes a name, not a key"
                )
            elif (
                key_env.upper() in BLOCKED_ENV_KEY_NAMES
                or any(key_env.upper().startswith(p) for p in BLOCKED_ENV_PREFIXES)
            ):
                errors.append(
                    f"{where}: api_key_env {key_env!r} refers to a sensitive system "
                    "credential and is refused for security"
                )
        # A field that does not apply to this vendor is a config error, not a
        # silently-ignored key: an operator who sets `endpoint` on a `cli` profile has
        # a mistaken model of what will run, and the schema cannot catch it because
        # both fields are legal *somewhere*.
        for field_name, owners in (("command", _COMMAND_VENDORS),
                                   ("endpoint", _ENDPOINT_VENDORS),
                                   ("api_key_env", _ENDPOINT_VENDORS)):
            if profile.get(field_name) and vendor in DELEGATE_PROFILE_VENDORS \
                    and vendor not in owners:
                errors.append(
                    f"{where}: {field_name!r} does not apply to vendor {vendor!r} "
                    f"(only {', '.join(owners)}) — it would be silently ignored"
                )
        prompt_mode = profile.get("prompt_mode", DEFAULT_PROMPT_MODE)
        if prompt_mode not in DELEGATE_PROMPT_MODES:
            errors.append(
                f"{where}: invalid prompt_mode {prompt_mode!r}; "
                f"valid: {', '.join(DELEGATE_PROMPT_MODES)}"
            )
    return errors


def _policy_capability_fields(value: Any, path: str = "policy_pack") -> list[tuple[str, list]]:
    fields: list[tuple[str, list]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if (
                key in {"required_capabilities", "optional_capabilities"}
                and isinstance(child, list)
            ):
                fields.append((child_path, child))
            else:
                fields.extend(_policy_capability_fields(child, child_path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            fields.extend(_policy_capability_fields(child, f"{path}[{i}]"))
    return fields


def delegate_profiles_dict(config: ProjectConfig) -> dict:
    """``{"delegate_profiles": {...}}``, or ``{}`` when none are configured.

    Shared by :func:`_canonical` and ``contracts.project_as_dict`` so the hashed form
    and the published contract cannot drift apart. Empty means **absent**, not ``{}``:
    an added optional field must not change ``config_hash`` for projects that never
    used it.
    """
    profiles = config.knobs.delegate_profiles
    if not profiles:
        return {}
    return {
        "delegate_profiles": {
            name: {
                "vendor": profile.vendor,
                "command": profile.command,
                "args": list(profile.args),
                "review_args": (
                    list(profile.review_args) if profile.review_args is not None else None
                ),
                "prompt_mode": profile.prompt_mode,
                "model": profile.model,
                "model_arg": profile.model_arg,
                "endpoint": profile.endpoint,
                "api_key_env": profile.api_key_env,
            }
            for name, profile in sorted(profiles.items())
        }
    }


def _canonical(config: ProjectConfig) -> dict:
    return {
        "extends": config.extends,
        "core_version": config.core_version,
        "base_branch": config.base_branch,
        "owner": config.owner,
        "repo": config.repo,
        "platform": config.platform,
        "timezone": config.timezone,
        "merge_window": config.merge_window,
        "merge_window_mode": config.merge_window_mode,
        "consent_mode": config.consent_mode,
        "gates": list(config.gates),
        "extensions_dir": config.extensions_dir,
        "extensions": {k: list(v) for k, v in sorted(config.extensions.items())},
        "policy_pack": config.policy_pack,
        "automation": {
            "approved_scopes": list(config.automation.approved_scopes),
            "operator": config.automation.operator,
        },
        "knobs": {
            "build_gate_cmd": config.knobs.build_gate_cmd,
            "lint_cmd": config.knobs.lint_cmd,
            "implementer_agents": dict(sorted(config.knobs.implementer_agents.items())),
            # Omitted entirely when empty: emitting "delegate_profiles": {} would rotate
            # config_hash for every project that has never configured one, which is the
            # normal treatment for an added optional field.
            **delegate_profiles_dict(config),
            "tier3_globs": list(config.knobs.tier3_globs),
            "ci_workflows": dict(sorted(config.knobs.ci_workflows.items())),
            "docs_gate_paths": list(config.knobs.docs_gate_paths),
            "docs_only_allowlist": list(config.knobs.docs_only_allowlist),
            "sot_doc": config.knobs.sot_doc,
            "required_capabilities": list(config.knobs.required_capabilities),
            "optional_capabilities": list(config.knobs.optional_capabilities),
            "evidence_gate_label": config.knobs.evidence_gate_label,
            "evidence_require_distinct_vendors": config.knobs.evidence_require_distinct_vendors,
            "swarm_review_evidence": config.knobs.swarm_review_evidence,
            "gate_timeout_s": config.knobs.gate_timeout_s,
            "jury_timeout_s": config.knobs.jury_timeout_s,
        },
    }
