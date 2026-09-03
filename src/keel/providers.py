"""The provider vocabulary keel can dispatch to — pure data model + registry (#1011).

keel advertises agent CLIs, hosted APIs, OpenAI-compatible endpoints, generic CLI
profiles and local Ollama models as delegates, but nothing in core could tell an
operator *which* of those are usable on this machine. This module owns the pure half
of that answer: the :class:`Provider` record, the machine-level **provider registry**
(``~/.keel/providers.yaml``), the name-clash rules, and :func:`plan_probes`, which
lists what a probe should look at. The probing itself — subprocess, PATH, HTTP — is
thin I/O in :mod:`keel.providerprobe`.

Three sources feed one list, in this precedence:

``profile``   a project ``knobs.delegate_profiles`` entry (checked into the repo);
``registry``  a machine-level entry the operator owns and never commits;
``builtin``   a vendor keel understands with no configuration at all.

Which providers are usable is a property of the **machine and the person**, not of
the project — one operator has ``claude``/``codex``/``agy`` logged in and no API key,
another has only ``XAI_API_KEY``. The registry is where those facts live so they do
not have to be committed to ``project.yaml``.

Name resolution is fail-closed and mirrors the existing built-in shadowing rule
(:func:`keel.config._validate_delegate_profiles`): a registry entry may not take the
name of a built-in vendor or of a project profile. The project profile keeps working
and **wins**; the clash is reported as a validation error naming both sources.

Pure and deterministic: no wall-clock, no randomness, no subprocess, no network. The
one file read is :func:`load_registry`, whose reader is injectable.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import agents
from . import config as cfg
from . import yaml_helper as yaml
from .api_delegate import OPENAI_COMPATIBLE, env_key_name

#: Env var naming an alternative registry file; otherwise ``~/.keel/providers.yaml``.
REGISTRY_ENV = "KEEL_PROVIDERS"

#: Path of the default registry, relative to the operator's home directory.
DEFAULT_REGISTRY_RELPATH = (".keel", "providers.yaml")

#: Transports a provider can be reached over. ``cli`` is a local coding-agent binary,
#: ``api`` a hosted HTTP endpoint keyed by an env var, ``local`` a model served on the
#: operator's own hardware.
TRANSPORTS = ("cli", "api", "local")

#: Documented read-only flag per built-in CLI vendor. Presence here is what
#: ``read_only_mode`` reports: keel cannot *enforce* read-only for an arbitrary CLI,
#: so the capability says "a documented flag exists", never "writes are impossible".
READ_ONLY_FLAGS: dict[str, str] = {
    "claude": "--disallowed-tools",
    "codex": "-s read-only",
    "agy": "--sandbox",
}

#: The flag every built-in CLI vendor spells model selection with.
DEFAULT_MODEL_ARG = "--model"

#: Ollama's local tag listing. A **hardcoded loopback constant**, like every other URL
#: keel dials: the probe never reaches an endpoint named by config or by the registry,
#: which is what keeps the SSRF story of this module trivial.
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

#: The command that serves the built-in ``ollama`` local vendor.
OLLAMA_COMMAND = "ollama"

#: Distinct review vendors a cross-vendor review panel needs. Two vendors reviewing is
#: the property `review-vendors` reports; one vendor twice is one opinion twice.
REVIEW_VENDOR_MINIMUM = 2

#: Characters an environment variable *name* may contain.
_ENV_NAME_OK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")

#: Registry keys a provider entry may carry. Anything else is ignored (fail-soft) —
#: an unknown key is far more likely a typo in a hand-written file than an intent.
_ENTRY_KEYS = frozenset(
    {
        "transport",
        "command",
        "endpoint",
        "api_key_env",
        "model",
        "model_arg",
        "effort",
        "review_args",
    }
)


@dataclass(frozen=True)
class Provider:
    """One provider keel could dispatch to, from any of the three sources."""

    name: str
    #: The delegate vendor recorded in attribution: a built-in vendor name
    #: (``claude``/``anthropic-api``/…), or ``cli``/``openai-compatible``/``local``
    #: for a profile or registry entry.
    vendor: str
    transport: str
    command: str | None = None
    endpoint: str | None = None
    #: The **name** of the env var holding the key — never the key.
    api_key_env: str | None = None
    model: str | None = None
    #: Vendor-specific reasoning-effort selector, carried through for dispatch (#1012).
    effort: str | None = None
    #: Flags that make this provider a reviewer rather than an implementer. Their
    #: presence is what ``read_only_mode`` reports for a profile/registry entry.
    review_args: tuple[str, ...] = ()
    #: ``builtin`` | ``profile`` | ``registry``.
    source: str = "builtin"
    #: How a model reaches a CLI (``<model_arg> <model>``); ``None`` when the provider
    #: exposes no model selection.
    model_arg: str | None = None

    def capabilities(self) -> dict[str, bool]:
        """What this provider can do — the three facts a dispatcher needs.

        ``tools``: can it run git/PR steps itself? Only a CLI can; an ``api`` or
        ``local`` provider generates text under keel's no-tools contract, where the
        orchestrator performs every mutation.

        ``read_only_mode``: is there a documented way to run it without write tools?
        A built-in CLI has a flag (:data:`READ_ONLY_FLAGS`); a profile or registry
        entry has it when the operator set ``review_args``. A provider with no tools
        at all has no such flag, and reports ``False`` — read ``tools`` first.

        ``model_selection``: can the caller choose the model?
        """
        return {
            "tools": self.transport == "cli",
            "read_only_mode": self._read_only_mode(),
            "model_selection": self.transport in ("api", "local")
            or bool(self.model_arg or self.model),
        }

    def _read_only_mode(self) -> bool:
        if self.source == "builtin":
            return self.name in READ_ONLY_FLAGS
        return bool(self.review_args)

    def as_dict(self) -> dict[str, object]:
        """JSON-stable record (no secrets: ``api_key_env`` is a name, never a value)."""
        return {
            "name": self.name,
            "vendor": self.vendor,
            "transport": self.transport,
            "source": self.source,
            "command": self.command,
            "endpoint": self.endpoint,
            "api_key_env": self.api_key_env,
            "model": self.model,
            "effort": self.effort,
            "review_args": list(self.review_args),
            "capabilities": self.capabilities(),
        }


@dataclass(frozen=True)
class Registry:
    """The machine-level provider registry, already parsed and fail-soft."""

    path: str
    present: bool = False
    providers: tuple[Provider, ...] = ()
    #: Fail-soft parse complaints. A malformed registry never raises: keel degrades to
    #: the built-ins and says why.
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def names(self) -> tuple[str, ...]:
        return tuple(provider.name for provider in self.providers)


def registry_path(*, env: Mapping[str, str] | None = None, home: str | Path | None = None) -> Path:
    """Where the registry lives: ``$KEEL_PROVIDERS``, else ``~/.keel/providers.yaml``.

    ``home`` is resolved from the **injected** environment before ``Path.home()`` so a
    test can place a registry without touching the real ``$HOME`` — and so an operator
    who moved ``HOME`` gets the registry their other tools see.
    """
    env = os.environ if env is None else env
    override = (env.get(REGISTRY_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    if home is None:
        home = env.get("HOME") or None
    base = Path(home).expanduser() if home is not None else Path.home()
    return base.joinpath(*DEFAULT_REGISTRY_RELPATH)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_registry(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    _read: Callable[[Path], str] = _read_text,
) -> Registry:
    """Load and parse the registry. **Never raises** — every failure is a warning.

    A missing file is not a failure: no registry means no machine-level providers,
    which is the state of every machine that has not opted in.
    """
    env = os.environ if env is None else env
    target = Path(path) if path is not None else registry_path(env=env)
    try:
        text = _read(target)
    except FileNotFoundError:
        return Registry(path=str(target), present=False)
    except OSError as exc:
        return Registry(
            path=str(target),
            present=True,
            warnings=(f"{target}: cannot be read ({exc})",),
        )
    try:
        data = yaml.load(text)
    except yaml.YAMLError as exc:
        return Registry(
            path=str(target),
            present=True,
            warnings=(f"{target}: is not valid YAML ({exc})",),
        )
    return parse_registry(data, path=str(target), env=env)


def parse_registry(
    data: Any,
    *,
    path: str,
    env: Mapping[str, str] | None = None,
) -> Registry:
    """Turn a loaded registry document into providers + fail-soft warnings (pure)."""
    env = os.environ if env is None else env
    if data is None:
        return Registry(path=path, present=True)
    if not isinstance(data, dict):
        return Registry(
            path=path,
            present=True,
            warnings=(f"{path}: expected a mapping with a 'providers:' key",),
        )
    entries = data.get("providers")
    if entries is None:
        return Registry(
            path=path,
            present=True,
            warnings=(f"{path}: has no 'providers:' mapping — nothing to register",),
        )
    if not isinstance(entries, dict):
        return Registry(
            path=path,
            present=True,
            warnings=(f"{path}: 'providers' must be a mapping of name -> entry",),
        )
    providers: list[Provider] = []
    warnings: list[str] = []
    for name in sorted(entries, key=str):
        provider, entry_warnings = _parse_entry(name, entries[name], path=path, env=env)
        warnings.extend(entry_warnings)
        if provider is not None:
            providers.append(provider)
    return Registry(
        path=path,
        present=True,
        providers=tuple(providers),
        warnings=tuple(warnings),
    )


def _parse_entry(
    name: Any,
    entry: Any,
    *,
    path: str,
    env: Mapping[str, str],
) -> tuple[Provider | None, list[str]]:
    """One registry entry -> a provider, or ``None`` plus the reasons it was skipped."""
    where = f"{path}: provider {name!r}"
    if not isinstance(name, str) or not name.strip():
        return None, [
            f"{path}: provider name {name!r} is not a non-empty string — quote the key "
            "(YAML reads a bare on/off/yes/no as a boolean and a bare number as an int)"
        ]
    if ":" in name:
        return None, [
            f"{where}: a name may not contain ':' — --delegate splits on the first colon "
            "to separate the provider from a per-run model, so this could never be selected"
        ]
    if not isinstance(entry, dict):
        return None, [f"{where}: entry must be a mapping of fields"]
    unknown = sorted(set(entry) - _ENTRY_KEYS)
    warnings = [f"{where}: ignoring unknown field(s) {', '.join(unknown)}"] if unknown else []
    transport = entry.get("transport")
    if transport not in TRANSPORTS:
        warnings.append(f"{where}: unknown transport {transport!r}; valid: {', '.join(TRANSPORTS)}")
        return None, warnings
    review_args, arg_warnings = _string_tuple(entry.get("review_args"), where=where)
    warnings.extend(arg_warnings)
    fields = {
        "name": name,
        "transport": transport,
        "model": _text(entry.get("model")),
        "effort": _text(entry.get("effort")),
        "review_args": review_args,
        "source": "registry",
    }
    if transport == "api":
        endpoint = _text(entry.get("endpoint"))
        issues = cfg.endpoint_issues(endpoint, where=where, env=env)
        remote_gate = any(cfg.ALLOW_REMOTE_ENDPOINT_ENV in issue for issue in issues)
        key_env = _text(entry.get("api_key_env"))
        issues.extend(_api_key_env_issues(key_env, where=where))
        if issues:
            warnings.extend(issues)
            if remote_gate:
                # Say it in the registry's own voice. The endpoint rules are the
                # project profile's, and their message says "not in this file" — which
                # in a home-directory registry reads as though some *other* file could
                # grant it. None can: the opt-in is environment-only precisely because
                # a file must not be able to widen its own reach.
                warnings.append(
                    f"{where}: not registered. Reaching your own remote endpoint needs "
                    f"{cfg.ALLOW_REMOTE_ENDPOINT_ENV}=1 exported in your shell — no "
                    "registry entry can grant itself that"
                )
            return None, warnings
        return (
            Provider(vendor=OPENAI_COMPATIBLE, endpoint=endpoint, api_key_env=key_env, **fields),
            warnings,
        )
    command = _text(entry.get("command"))
    if not command:
        warnings.append(
            f"{where}: transport {transport!r} requires a non-empty 'command' — the "
            "executable keel runs"
        )
        return None, warnings
    vendor = "cli" if transport == "cli" else "local"
    model_arg = _text(entry.get("model_arg")) or (DEFAULT_MODEL_ARG if transport == "cli" else None)
    return Provider(vendor=vendor, command=command, model_arg=model_arg, **fields), warnings


def _api_key_env_issues(key_env: str | None, *, where: str) -> list[str]:
    """Rules for the **name** of a registry entry's API-key env var.

    The denylist is shared with ``knobs.delegate_profiles``: a high-privilege system
    credential (``GITHUB_TOKEN``, ``AWS_*``, ``SSH_AUTH_SOCK``, …) may never become an
    ``Authorization`` header, wherever the entry was written.

    The project profile's *allowlist* is deliberately **not** applied here. That list
    exists because ``project.yaml`` is committed and reviewed by people other than its
    author — the threat model is a config an attacker influenced through a pull
    request. This file is not committed and not shared: it sits in the operator's own
    home directory at the same trust level as their shell profile, and #1011's own
    example (an operator whose only key is ``XAI_API_KEY``) is exactly the case an
    allowlist of seven vendor names would refuse. The probe reads presence only — it
    never reads the value, and never sends it anywhere.
    """
    if not key_env:
        return [
            f"{where}: transport 'api' requires 'api_key_env' — the *name* of the "
            "environment variable holding the key, never the key itself"
        ]
    if not _ENV_NAME_OK.issuperset(key_env) or key_env[0].isdigit():
        return [
            f"{where}: api_key_env {key_env!r} is not a valid environment variable name "
            "(letters, digits, underscore; not starting with a digit) — this field takes "
            "a name, not a key"
        ]
    if key_env.upper() in cfg.BLOCKED_ENV_KEY_NAMES or key_env.upper().startswith(
        cfg.BLOCKED_ENV_PREFIXES
    ):
        return [
            f"{where}: api_key_env {key_env!r} names a high-privilege system credential "
            "and may not be used as a provider key"
        ]
    return []


def _text(value: Any) -> str | None:
    """A non-blank string, or ``None`` (so a blank field reads as unset)."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_tuple(value: Any, *, where: str) -> tuple[tuple[str, ...], list[str]]:
    if value is None:
        return (), []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return (), [f"{where}: 'review_args' must be a list of strings — ignored"]
    return tuple(value), []


def builtin_providers() -> tuple[Provider, ...]:
    """Every vendor keel understands with no configuration at all."""
    providers = [
        Provider(
            name=vendor,
            vendor=vendor,
            transport="cli",
            command=vendor,
            model_arg=DEFAULT_MODEL_ARG,
            source="builtin",
        )
        for vendor in agents.CLI_VENDORS
    ]
    providers.extend(
        Provider(
            name=vendor,
            vendor=vendor,
            transport="local",
            command=OLLAMA_COMMAND,
            endpoint=OLLAMA_TAGS_URL,
            source="builtin",
        )
        for vendor in agents.LOCAL_VENDORS
    )
    providers.extend(
        Provider(
            name=vendor,
            vendor=vendor,
            transport="api",
            api_key_env=env_key_name(vendor),
            source="builtin",
        )
        for vendor in agents.API_VENDORS
    )
    return tuple(providers)


def profile_providers(config) -> tuple[Provider, ...]:
    """Providers from a project's ``knobs.delegate_profiles`` (empty when no config)."""
    if config is None:
        return ()
    providers = []
    for name in sorted(config.knobs.delegate_profiles):
        profile = config.knobs.delegate_profiles[name]
        api = profile.vendor == OPENAI_COMPATIBLE
        providers.append(
            Provider(
                name=name,
                vendor=profile.vendor,
                transport="api" if api else "cli",
                command=profile.command,
                endpoint=profile.endpoint,
                api_key_env=profile.api_key_env,
                model=profile.model,
                review_args=profile.role_args(review=True) if profile.review_args else (),
                source="profile",
                model_arg=None if api else (profile.model_arg or None),
            )
        )
    return tuple(providers)


def registry_clashes(registry: Registry, config) -> list[str]:
    """Name clashes between the registry and the built-ins / this project's profiles.

    A registry entry may not shadow either. The message names **both** sources, so an
    operator editing a file in ``$HOME`` learns which repository file it collided with
    rather than being told its own name is taken.
    """
    builtins = agents.BUILTIN_DELEGATE_VENDORS
    profiles = {} if config is None else config.knobs.delegate_profiles
    errors: list[str] = []
    for name in registry.names():
        if name in builtins:
            errors.append(
                f"{registry.path}: provider {name!r} shadows the built-in delegate vendor "
                f"{name!r}; built-ins always win and may not be redefined "
                f"({', '.join(builtins)}) — rename the registry entry"
            )
        elif name in profiles:
            errors.append(
                f"{registry.path}: provider {name!r} clashes with the project profile "
                f"knobs.delegate_profiles.{name}; the project profile wins — rename the "
                "registry entry"
            )
    return errors


def plan_probes(config, registry: Registry | None = None) -> tuple[Provider, ...]:
    """Everything a probe should look at, in a deterministic order.

    Built-ins first (dispatch order: CLI, local, hosted API), then this project's
    profiles, then the machine-level registry. A registry entry whose name clashes is
    **dropped** here rather than silently overriding: precedence is
    *project profile > registry > built-in*, and :func:`registry_clashes` reports the
    clash so the operator sees why the entry is missing.
    """
    registry = Registry(path="") if registry is None else registry
    providers = list(builtin_providers())
    profiles = profile_providers(config)
    providers.extend(profiles)
    taken = {provider.name for provider in providers}
    providers.extend(provider for provider in registry.providers if provider.name not in taken)
    return tuple(providers)


def tool_capable(providers: Iterable[Provider]) -> tuple[str, ...]:
    """Names of the providers that can run tools (i.e. drive git/PR steps themselves)."""
    return tuple(p.name for p in providers if p.capabilities()["tools"])


def distinct_vendors(providers: Iterable[Provider]) -> tuple[str, ...]:
    """Distinct vendors across ``providers``, in first-seen order.

    A review panel's independence is a property of *vendors*, not of provider entries:
    two profiles that both shell out to the same CLI are one vendor, and one opinion.
    """
    return tuple(dict.fromkeys(p.vendor for p in providers))


#: Characters a listed model id may contain. Wider than
#: :data:`keel.agents._MODEL_TOKEN_OK` on purpose — this parses a *listing* keel only
#: displays (``qwen2.5:7b``, ``anthropic/claude-opus-4-5``), and a token from here is
#: still re-validated by ``agents.is_safe_model_token`` before it can reach an argv.
_LISTED_MODEL_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/")

#: Cap on a parsed model listing. A CLI that decides to print its whole catalogue
#: (or something that is not a listing at all) must not turn a doctor row into a wall.
MAX_LISTED_MODELS = 100


def parse_model_lines(text: str) -> tuple[str, ...]:
    """Best-effort model ids out of a CLI listing (``agy models``).

    No agent CLI promises a machine-readable listing, so this reads the shape they all
    share — one model per line, possibly bulleted, possibly followed by columns of
    description — and keeps the first token of each line that could be a model id.
    Headers, rules and prose lines drop out because they carry characters no model id
    has. Pure, deterministic, and never raises: an unreadable listing yields ``()``,
    which reads as "this provider exposes no model list", not as an error.
    """
    seen: dict[str, None] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.endswith(":"):
            continue
        line = line.lstrip("-*\u2022 \t")
        if not line:
            continue
        token = line.split()[0]
        if not _LISTED_MODEL_OK.issuperset(token) or not any(c.isalnum() for c in token):
            continue
        seen.setdefault(token, None)
        if len(seen) >= MAX_LISTED_MODELS:
            break
    return tuple(seen)


def parse_tag_payload(data: Any) -> tuple[str, ...]:
    """Model names out of an Ollama ``/api/tags`` payload (``()`` when malformed)."""
    if not isinstance(data, dict):
        return ()
    models = data.get("models")
    if not isinstance(models, list):
        return ()
    names: dict[str, None] = {}
    for entry in models:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model")
        if isinstance(name, str) and name.strip():
            names.setdefault(name.strip(), None)
        if len(names) >= MAX_LISTED_MODELS:
            break
    return tuple(names)
