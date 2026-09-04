"""How keel invokes a delegate — the pure planner behind ``keel delegate run`` (#1012).

Nothing in keel dispatched a delegate. ``api_delegate.generate()`` had no caller; the
``claude``/``codex``/``agy`` argv shapes, the stdin framing, the Ollama endpoint, the
timeouts and the JSON return contract lived only as prose in ``ship.md`` s4/s7, so every
host agent re-invented them and they drifted. This module is the single answer to *how do
I invoke provider P for role R*, and :mod:`keel.delegaterun` is the only thing that runs
the answer.

The split is keel's usual one. Everything here is **pure**: it takes a
:class:`keel.providers.Provider` (from #1011's registry) plus a role, and returns a frozen
:class:`RunPlan` — an argv, or an HTTP request description, plus the stdin framing, the
read-only verdict, the attribution record and any warnings. No subprocess, no network, no
clock, no filesystem. That is what makes "a ``review`` run never carries a write-enabling
flag" a property a unit test can assert per vendor instead of a sentence in a markdown
file nobody can execute.

**Role policy.** ``review``/``gate``/``chair`` are read-only: the delegate reads a diff
and returns findings, and every mutation stays with the orchestrator. ``implement``/``fix``
are tool-enabled. keel can *document* a read-only invocation for the three built-in CLIs
and *offer* one (``review_args``) for a configured profile; it cannot **enforce** read-only
for an arbitrary binary, which is why :attr:`RunPlan.read_only` reports the role's policy
and a warning names the case where nothing backs it.

**Why the prompt is never in argv.** Every transport that runs a binary delivers the
prompt on stdin. A prompt carries the diff and the brief; an argv is world-readable in
``ps`` for the life of the process, and a large diff can exceed ``ARG_MAX`` outright. The
one exception is a profile that declares ``prompt_mode: arg`` — an operator-authored
choice for CLIs whose usage requires it (``cursor-agent``'s is
``agent [options] [command] [prompt...]``), where the plan carries ``stdin_mode: None`` and
the executor appends the prompt as the final argument.

**agy's framing.** ai-jury (``src/ai_jury/adapters.py``, read-only reference) verified two
invocations against the shipped CLI: ``--print=<prompt>``, which takes the prompt as the
flag's *value* because ``--print --model X`` swallows the next flag; and
``--input-format stream-json --output-format stream-json``, which reads one NDJSON frame
per line on stdin. keel takes the **stream-json** one. Three reasons, in order: the prompt
stays out of ``ps`` and out of ``ARG_MAX`` (ai-jury's #287); it sidesteps the ``--print``
arity trap entirely rather than working around it; and — decisively for this module — a
*pure* planner is handed a prompt **path**, never the prompt text, so it could not build a
``--print=<prompt>`` argv at all without reading a file and stopping being pure. The
NDJSON reply is parsed back by :func:`parse_stream_json`.

Pure and deterministic: no wall-clock, no randomness, no I/O. Failures raise
:class:`DelegateError`, which carries a machine-readable ``code`` the executor turns into
the fail-soft ``error_code`` of the JSON contract — a traceback never reaches an operator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import agents
from . import providers as providers_mod
from .api_delegate import DEFAULT_MAX_TOKENS, OPENAI_COMPATIBLE
from .config import DEFAULT_PROMPT_MODE, DelegateProfile

# The effort vocabulary lives in the leaf :mod:`keel.vocab` so ``keel.team`` can validate
# a seat's ``effort`` without importing the executor (#1050). ``EFFORT_VENDORS`` and
# ``supports_effort`` are re-exported under the original names, which are what the rest
# of the package and the tests read.
from .vocab import EFFORT_VENDORS as EFFORT_VENDORS
from .vocab import EFFORTS
from .vocab import supports_effort as supports_effort

#: Roles a delegate can be dispatched for. ``chair`` is the jury's summariser; it reads
#: reviews and writes nothing, so it sits with the read-only three.
ROLES = ("implement", "fix", "review", "gate", "chair")

#: Roles invoked read-only / findings-only. Everything not listed here is tool-enabled.
READ_ONLY_ROLES = ("review", "gate", "chair")

#: How the executor reaches a provider. ``cli`` is one of the three built-in agent CLIs,
#: ``profile`` any other binary (a project ``delegate_profiles`` entry or a registry
#: ``cli``/``local`` entry), ``api`` a hosted or OpenAI-compatible HTTP endpoint, and
#: ``ollama`` the built-in local vendor served on loopback.
TRANSPORTS = ("cli", "profile", "api", "ollama")

#: Prompt framing on the child's stdin. ``None`` means "no stdin": the executor appends
#: the prompt as the final argv element (a profile's ``prompt_mode: arg``).
STDIN_TEXT = "text"
STDIN_STREAM_JSON = "stream-json"

#: Wall-clock seconds a delegate run may take when the caller names none. Delegated
#: implementation is measured in tens of minutes, not in the 120 s a git call gets.
DEFAULT_TIMEOUT_S = 1800

#: Ollama's local generation endpoint. A **hardcoded loopback constant**, exactly like
#: :data:`keel.providers.OLLAMA_TAGS_URL` and every other URL keel dials: the executor
#: never reaches an endpoint named by config or by the registry.
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"

#: agy's NDJSON stdin/stdout framing. See the module docstring for why keel uses it
#: rather than ``--print=<prompt>``.
AGY_STREAM_ARGS = ("--input-format", "stream-json", "--output-format", "stream-json")

#: The only tools a read-only ``claude`` invocation may use. An **allow-list**: a denylist
#: of write tools has to be extended every time the CLI grows one, and is wrong in the
#: window before someone notices. Reading a diff needs no more than these three — ``Glob``
#: is how the current CLI lists a directory, so there is no separate listing tool to name.
CLAUDE_ALLOWED_TOOLS = "Read,Grep,Glob"

#: How ``codex`` spells reasoning effort. Not a flag on the shipped CLI (0.152.1) — it is
#: a config override, and an unknown key is rejected under ``--strict-config``, which is
#: how this spelling was verified rather than assumed.
CODEX_EFFORT_CONFIG = "model_reasoning_effort"

#: ``anthropic-api`` extended-thinking budget per effort level, in tokens.
ANTHROPIC_THINKING_BUDGET = {"low": 2048, "medium": 8192, "high": 32768}

#: ``google-api`` ``thinkingBudget`` per effort level, in tokens. Gemini's floor is lower
#: than Claude's, so ``low`` is 1024 rather than 2048.
GOOGLE_THINKING_BUDGET = {"low": 1024, "medium": 8192, "high": 32768}

#: Room left for the answer *above* a thinking budget. Anthropic requires
#: ``max_tokens > thinking.budget_tokens``; a budget of 32768 against the default 16384
#: cap is rejected by the API before a token is generated.
THINKING_HEADROOM_TOKENS = 4096

#: Suffixes agy spells reasoning effort with, e.g. ``gemini-3.8-flash-high``.
_EFFORT_SUFFIXES = tuple(f"-{level}" for level in EFFORTS)


class DelegateError(Exception):
    """A run that cannot be planned. ``code`` is the JSON contract's ``error_code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Resolution:
    """Which provider a ``--provider`` token names, and the model it carries."""

    provider: providers_mod.Provider
    #: The project ``delegate_profiles`` entry behind a ``profile``-source provider.
    #: :class:`keel.providers.Provider` keeps only ``review_args``, so the implementer's
    #: ``args`` and the ``prompt_mode`` are reachable only through the profile itself.
    profile: DelegateProfile | None = None
    #: The per-run model from ``vendor:model``, already validated. ``None`` when the
    #: token named no model.
    model: str | None = None


@dataclass(frozen=True)
class Effort:
    """How one ``--effort`` request landed on one vendor.

    Returned as a record rather than a tuple because the *budget* is needed twice and in
    two shapes: inside the vendor's payload fragment, and again as the number
    ``max_tokens`` must exceed. Re-deriving it by digging back into the fragment means
    one parser per vendor spelling, with branches nothing can reach.
    """

    model: str | None
    payload: dict[str, Any]
    #: Thinking-token budget the fragment asks for, ``None`` when it asks for none.
    budget: int | None
    applied: bool
    warnings: tuple[str, ...]
    #: Extra argv fragment, for a vendor that spells effort on its command line.
    argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunPlan:
    """One fully-resolved delegate invocation. Frozen, JSON-stable, never executed here."""

    provider: str
    vendor: str
    role: str
    transport: str
    prompt_path: str
    model: str | None = None
    cwd: str | None = None
    timeout: int = DEFAULT_TIMEOUT_S
    #: The command line, **without** the prompt. Empty for ``api``/``ollama``.
    argv: tuple[str, ...] = ()
    #: The HTTP call description for ``api``/``ollama``; ``None`` for the argv transports.
    request: dict[str, Any] | None = None
    #: ``text`` | ``stream-json`` | ``None`` (append the prompt as the last argv element).
    stdin_mode: str | None = None
    #: True when the role's policy is read-only.
    read_only: bool = False
    #: True when something actually **backs** that policy: a built-in CLI's documented
    #: read-only invocation, an operator's ``review_args``, or a transport with no tools
    #: at all. False means the role says read-only and nothing enforces it — the caller
    #: must decide whether to run at all. Split from :attr:`read_only` because a single
    #: flag cannot distinguish "reviewer" from "reviewer holding the implementer's write
    #: flags", and the second is the one that edits the checkout.
    read_only_backed: bool = False
    effort: str | None = None
    effort_applied: bool = False
    warnings: tuple[str, ...] = ()
    attribution: dict[str, str | None] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-stable record (carries no secret: ``api_key_env`` is a name)."""
        return {
            "provider": self.provider,
            "vendor": self.vendor,
            "role": self.role,
            "transport": self.transport,
            "prompt_path": self.prompt_path,
            "model": self.model,
            "cwd": self.cwd,
            "timeout": self.timeout,
            "argv": list(self.argv),
            "request": dict(self.request) if self.request is not None else None,
            "stdin_mode": self.stdin_mode,
            "read_only": self.read_only,
            "read_only_backed": self.read_only_backed,
            "effort": self.effort,
            "effort_applied": self.effort_applied,
            "warnings": list(self.warnings),
            "attribution": dict(self.attribution),
        }


def resolve_provider(
    config,
    registry: providers_mod.Registry | None,
    token: str,
) -> Resolution:
    """Resolve ``name`` / ``vendor:model`` against the built-ins, profiles, the registry.

    Precedence is **built-in > project profile > registry**. A built-in vendor always
    wins and can never be redefined — the invariant
    :func:`keel.agents.resolve_delegate_profile` states and
    :func:`keel.config.parse_config` enforces up front, and the same rule
    :func:`keel.providers.registry_clashes` applies to a machine-level entry. Resolving a
    profile or a registry entry first would make ``claude`` mean whatever a file in
    ``$HOME`` said it meant, which is exactly the shadowing those two checks exist to
    refuse; dispatch must not be the one place the rule is inverted.

    The model half of the token is validated before anything else looks at it: it can
    arrive from a ``delegate-model:`` issue label, which is a lower-trust source than the
    operator-authored command beside it. **Which** rule applies depends on where the model
    lands — see :func:`model_token_issue`.
    """
    name, model = agents.split_delegate((token or "").strip())
    if not name:
        raise DelegateError("bad-provider", "--provider is empty; pass name or vendor:model")
    profiles = {} if config is None else config.knobs.delegate_profiles
    registry = providers_mod.Registry(path="") if registry is None else registry
    resolution = _lookup(name, config, profiles, registry)
    if resolution is None:
        known = ", ".join(sorted({*profiles, *registry.names(), *agents.BUILTIN_DELEGATE_VENDORS}))
        raise DelegateError("unknown-provider", f"unknown provider {name!r}; known: {known}")
    _check_model(resolution.provider, model)
    return Resolution(resolution.provider, resolution.profile, model)


def _lookup(name, config, profiles, registry: providers_mod.Registry) -> Resolution | None:
    """The provider ``name`` means, in precedence order. ``None`` when nothing claims it."""
    for entry in providers_mod.builtin_providers():
        if entry.name == name:
            return Resolution(entry, None, None)
    if name in profiles:
        return Resolution(_profile_provider(config, name), profiles[name], None)
    for entry in registry.providers:
        if entry.name == name:
            return Resolution(entry, None, None)
    return None


#: Characters a model may contain when it travels in a **JSON request body**. Wider than
#: :func:`keel.agents.is_safe_model_token` by ``:`` and ``/`` because real ids need both —
#: an Ollama tag (``qwen2.5-coder:32b``) and a gateway's namespaced id
#: (``deepseek/deepseek-r1``). Neither character can do anything in a JSON string value:
#: the body is built by :func:`json.dumps`, so there is no argv to split and no URL path
#: to retarget. A leading dash and ``..`` stay refused so the same token can never be
#: mistaken for a flag or a path if it is later logged, echoed, or reused.
_BODY_MODEL_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-")


def is_safe_body_model_token(model: str | None) -> bool:
    """True when ``model`` is safe as a JSON body field (``ollama``/hosted/compatible)."""
    if not model or model.startswith("-") or ".." in model:
        return False
    return _BODY_MODEL_OK.issuperset(model)


def model_reaches_argv(provider: providers_mod.Provider) -> bool:
    """Does this provider's model end up on a command line or in a URL path?

    Two places demand the strict ``[A-Za-z0-9._-]`` token, and only two: a **subprocess
    argv** (``cli``/``profile``), where a stray character could read as another flag; and
    ``google-api``'s **URL path**, the one vendor that interpolates the model into its
    endpoint, where a ``/`` or a ``?`` could retarget the request or smuggle query
    parameters onto a URL that also carries an API key header.

    Everywhere else the model is a JSON string value, and applying the argv rule there is
    not caution but breakage: it refuses ``ollama:qwen2.5-coder:32b`` and
    ``openrouter:deepseek/deepseek-r1``, two ids this repository's own documentation tells
    operators to use.
    """
    return _transport_of(provider) in ("cli", "profile") or provider.vendor == "google-api"


def model_token_issue(provider: providers_mod.Provider, model: str | None) -> str | None:
    """Why ``model`` may not be used with ``provider``, or ``None`` when it may."""
    if model is None:
        return None
    if model_reaches_argv(provider):
        if not agents.is_safe_model_token(model):
            return (
                f"model {model!r} is not a safe token for {provider.name!r}: it reaches a "
                "command line or a URL path, so only [A-Za-z0-9._-] with no leading dash "
                "is accepted"
            )
        return None
    if not is_safe_body_model_token(model):
        return (
            f"model {model!r} is not a safe token for {provider.name!r}: a request-body "
            "model accepts [A-Za-z0-9._:/-] with no leading dash and no '..'"
        )
    return None


def _check_model(provider: providers_mod.Provider, model: str | None) -> None:
    issue = model_token_issue(provider, model)
    if issue is not None:
        raise DelegateError("bad-model", issue)


def _profile_provider(config, name: str) -> providers_mod.Provider:
    """The one :class:`keel.providers.Provider` this project's profile ``name`` maps to."""
    for provider in providers_mod.profile_providers(config):
        if provider.name == name:
            return provider
    raise DelegateError(  # pragma: no cover - profile_providers covers every profile key
        "unknown-provider", f"profile {name!r} produced no provider record"
    )


def plan_run(
    provider: providers_mod.Provider,
    role: str,
    prompt_path: str,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    effort: str | None = None,
    model: str | None = None,
    *,
    profile: DelegateProfile | None = None,
) -> RunPlan:
    """Plan one delegate invocation. Pure; raises :class:`DelegateError` on bad input.

    ``model`` is the per-run override (``--model``, or the ``vendor:model`` half of the
    provider token) and wins over the provider's configured ``model``, matching the
    precedence ship.md s4 documents. ``profile`` supplies the project profile behind a
    ``profile``-source provider — see :attr:`Resolution.profile`.
    """
    if role not in ROLES:
        raise DelegateError("bad-role", f"unknown role {role!r}; valid: {', '.join(ROLES)}")
    if effort is not None and effort not in EFFORTS:
        raise DelegateError("bad-effort", f"unknown effort {effort!r}; valid: {', '.join(EFFORTS)}")
    if timeout <= 0:
        raise DelegateError(
            "bad-timeout", f"timeout must be a positive number of seconds: {timeout!r}"
        )
    if not prompt_path:
        raise DelegateError("no-prompt", "--prompt-file is required")
    effective = model or provider.model
    _check_model(provider, effective)
    read_only = role in READ_ONLY_ROLES
    transport = _transport_of(provider)
    effort, warnings = _effective_effort(provider, effort)
    applied = _apply_effort(provider.vendor, transport, effort, effective)
    effective = applied.model
    warnings = warnings + applied.warnings
    argv: tuple[str, ...] = ()
    request: dict[str, Any] | None = None
    stdin_mode: str | None = None
    backed = read_only
    if transport == "cli":
        argv, stdin_mode = _builtin_argv(
            provider, read_only=read_only, model=effective, effort_args=applied.argv
        )
    elif transport == "profile":
        argv, stdin_mode, backed, profile_warnings = _profile_argv(
            provider, profile, read_only=read_only, model=effective
        )
        warnings = warnings + profile_warnings
    else:
        request = _request(provider, transport, model=effective, timeout=timeout, effort=applied)
    return RunPlan(
        provider=provider.name,
        vendor=provider.vendor,
        role=role,
        transport=transport,
        prompt_path=prompt_path,
        model=effective,
        cwd=cwd,
        timeout=timeout,
        argv=argv,
        request=request,
        stdin_mode=stdin_mode,
        read_only=read_only,
        read_only_backed=backed,
        effort=effort,
        effort_applied=applied.applied,
        warnings=warnings,
        attribution=_attribution(provider, profile, effective),
    )


def _effective_effort(
    provider: providers_mod.Provider, effort: str | None
) -> tuple[str | None, tuple[str, ...]]:
    """``--effort`` if given, else the provider's own configured default.

    ``Provider.effort`` exists so an operator can say "this entry is my high-effort seat"
    once, in the registry or the profile, instead of on every dispatch. A per-run
    ``--effort`` still wins. An unrecognised configured value is a **warning, not an
    error**: the registry is fail-soft everywhere else in #1011, and a typo in a file in
    ``$HOME`` should not make every run of an otherwise usable provider fail.
    """
    if effort is not None or not provider.effort:
        return effort, ()
    if provider.effort in EFFORTS:
        return provider.effort, ()
    return None, (
        f"{provider.name}: ignoring configured effort {provider.effort!r}; "
        f"valid: {', '.join(EFFORTS)}",
    )


def _transport_of(provider: providers_mod.Provider) -> str:
    """Which executor path a provider record lands on.

    :class:`keel.providers.Provider` records the *shape* of a provider (``cli``/``api``/
    ``local``); this is the finer question of which code runs it. The built-in ``ollama``
    vendor gets its own HTTP path, while a registry ``local`` entry names an arbitrary
    binary and is run exactly like a configured CLI — keel does not dial an address a
    file names, so a local entry's only reachable surface is its command.
    """
    if provider.transport == "api":
        return "api"
    if provider.vendor == "ollama":
        return "ollama"
    if provider.source == "builtin":
        return "cli"
    return "profile"


def _builtin_argv(
    provider: providers_mod.Provider,
    *,
    read_only: bool,
    model: str | None,
    effort_args: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], str]:
    """The argv + stdin framing for one of the three built-in agent CLIs.

    **claude's read-only invocation is an allow-list, and carries no permission bypass.**
    The first cut paired ``--disallowed-tools Edit,Write,NotebookEdit,Bash`` with
    ``--dangerously-skip-permissions``, on the reasoning that a denylist wins over the
    bypass. That is a denylist of four names against a tool surface that grows with every
    release — ``WebFetch``, an MCP server's tools, whatever ships next — and it hands the
    bypass to everything it failed to enumerate. ``--allowed-tools`` inverts the default:
    anything not named is refused, so a new tool is refused on the day it appears rather
    than on the day someone remembers this list. With no tool outside the read set
    reachable there is nothing left for a permission prompt to ask about, so the bypass is
    dropped as well. Flag spelling verified against ``claude --help``
    (``--allowedTools, --allowed-tools <tools...>``).

    ``agy`` keeps ``--sandbox`` plus ``--dangerously-skip-permissions``: it exposes no
    allow-list, the sandbox is the only read-only mechanism it documents, and without the
    skip flag an unattended reviewer stops at an approval prompt. This is the same pairing
    ai-jury's ``privilege.enforce_read_only`` uses for the vendor, and the reason
    :attr:`RunPlan.read_only_backed` reports what backs the promise rather than asserting
    that writes are impossible.
    """
    command = provider.command or provider.name
    if provider.name == "claude":
        argv = [command, "-p"]
        if read_only:
            argv += ["--output-format", "text"]
        if model:
            argv += ["--model", model]
        if read_only:
            argv += ["--allowed-tools", CLAUDE_ALLOWED_TOOLS]
        else:
            argv += ["--dangerously-skip-permissions"]
        return tuple(argv), STDIN_TEXT
    if provider.name == "codex":
        sandbox = "read-only" if read_only else "workspace-write"
        argv = [command, "exec", "-s", sandbox, "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        argv += list(effort_args)
        return tuple(argv), STDIN_TEXT
    # agy — the only remaining built-in CLI vendor (agents.CLI_VENDORS).
    argv = [command]
    if read_only:
        argv += ["--sandbox"]
    argv += ["--dangerously-skip-permissions", *AGY_STREAM_ARGS]
    if model:
        argv += ["--model", model]
    return tuple(argv), STDIN_STREAM_JSON


def _profile_argv(
    provider: providers_mod.Provider,
    profile: DelegateProfile | None,
    *,
    read_only: bool,
    model: str | None,
) -> tuple[tuple[str, ...], str | None, bool, tuple[str, ...]]:
    """The argv + framing + read-only backing for an operator-configured binary.

    The dangerous case is a profile that has ``args`` and no ``review_args``:
    :meth:`keel.config.DelegateProfile.role_args` **falls back to ``args``**, which is the
    implementer's flag set — ``aider``'s ``--yes-always``, ``cursor-agent``'s ``--force``.
    A reviewer invoked with those can edit the checkout it was asked to read.

    So the question asked here is *"did the operator configure a read-only invocation?"* —
    ``profile.review_args is None`` — and **not** *"is the argv empty?"*. The first cut
    asked the second, so exactly the dangerous case produced a full implementer argv,
    ``read_only: true``, and no warning at all: the fallback made ``role_args`` non-empty,
    which read as "configured". A profile whose ``review_args`` is an explicit empty list
    is a deliberate choice — "this CLI needs no flags to be a reviewer" — and is backed.
    """
    command = provider.command
    if not command:
        raise DelegateError("bad-provider", f"provider {provider.name!r} names no command")
    if profile is not None:
        role_args = profile.role_args(review=read_only)
        configured = profile.review_args is not None
        prompt_mode = profile.prompt_mode
    else:
        # A registry entry has no implementer `args` to fall back to, so an empty tuple
        # here really is "nothing configured" rather than a fallback in disguise.
        role_args = provider.review_args if read_only else ()
        configured = bool(provider.review_args)
        prompt_mode = DEFAULT_PROMPT_MODE
    argv = [command, *role_args]
    if model and provider.model_arg:
        argv += [provider.model_arg, model]
    warnings: tuple[str, ...] = ()
    backed = read_only and configured
    if read_only and not configured:
        shared = " it is running with the implementer's own args" if role_args else ""
        warnings = (
            f"{provider.name}: no read-only invocation is configured (review_args), so"
            f"{shared or ' the command runs with its default permissions'} — keel cannot "
            "enforce read-only for an arbitrary CLI. Set review_args, treat this "
            "reviewer's output as advisory, and re-check the worktree is clean afterwards",
        )
    stdin_mode = STDIN_TEXT if prompt_mode == DEFAULT_PROMPT_MODE else None
    return tuple(argv), stdin_mode, backed, warnings


def _request(
    provider: providers_mod.Provider,
    transport: str,
    *,
    model: str | None,
    timeout: int,
    effort: Effort,
) -> dict[str, Any]:
    """The HTTP call description for the ``api`` and ``ollama`` transports."""
    if not model:
        raise DelegateError(
            "no-model",
            f"provider {provider.name!r} generates text over HTTP and names no model; "
            "pass --model or provider:model",
        )
    if transport == "ollama":
        # Hardcoded loopback constant, never provider.endpoint: the tag listing #1011
        # probes and the generation endpoint are two paths on the same fixed origin.
        return {
            "vendor": "ollama",
            "model": model,
            "endpoint": OLLAMA_GENERATE_URL,
            "timeout": timeout,
        }
    max_tokens = DEFAULT_MAX_TOKENS
    if effort.budget is not None:
        # Anthropic rejects max_tokens <= thinking.budget_tokens before generating a
        # token, and Gemini's answer shares the output cap with its thinking.
        max_tokens = max(max_tokens, effort.budget + THINKING_HEADROOM_TOKENS)
    return {
        "vendor": provider.vendor,
        "model": model,
        "endpoint": provider.endpoint,
        "api_key_env": provider.api_key_env,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "extra_payload": effort.payload,
    }


def _apply_effort(vendor: str, transport: str, effort: str | None, model: str | None) -> Effort:
    """Map ``--effort`` onto the vendor's own spelling.

    Every vendor spells reasoning effort differently and several cannot spell it at all;
    an unsupported request is a warning plus ``applied: False``, never a silent no-op — a
    caller that asked for ``high`` and got the default must be able to see that from the
    JSON it parses.
    """
    if effort is None:
        return Effort(model, {}, None, False, ())
    if vendor == "agy":
        return _agy_effort(effort, model)
    if vendor == "codex":
        # A config override rather than a flag: `codex exec` takes `-c key=value`, and
        # `model_reasoning_effort` is the key it recognises (verified by round-tripping a
        # real and a bogus key through `--strict-config`).
        return Effort(model, {}, None, True, (), ("-c", f"{CODEX_EFFORT_CONFIG}={effort}"))
    if vendor == "anthropic-api":
        budget = ANTHROPIC_THINKING_BUDGET[effort]
        payload = {"thinking": {"type": "enabled", "budget_tokens": budget}}
        return Effort(model, payload, budget, True, ())
    if vendor in ("openai-api", OPENAI_COMPATIBLE):
        return Effort(model, {"reasoning_effort": effort}, None, True, ())
    if vendor == "google-api":
        budget = GOOGLE_THINKING_BUDGET[effort]
        payload = {"generationConfig": {"thinkingConfig": {"thinkingBudget": budget}}}
        return Effort(model, payload, budget, True, ())
    return Effort(
        model,
        {},
        None,
        False,
        (
            f"--effort {effort} is not supported by {vendor} over the {transport} "
            "transport; the run used the provider's default reasoning effort",
        ),
    )


def _agy_effort(effort: str, model: str | None) -> Effort:
    """agy spells effort as a model suffix (``gemini-3.8-flash-high``), not as a flag."""
    if not model:
        return Effort(
            model,
            {},
            None,
            False,
            (
                f"--effort {effort} needs a model for agy, which spells effort as a "
                "model suffix; pass --model or provider:model",
            ),
        )
    for suffix in _EFFORT_SUFFIXES:
        if model.endswith(suffix):
            if suffix == f"-{effort}":
                return Effort(model, {}, None, True, ())
            return Effort(
                model,
                {},
                None,
                True,
                (
                    f"model {model!r} already selects effort {suffix[1:]!r}; the model's "
                    f"own suffix wins over --effort {effort}",
                ),
            )
    return Effort(f"{model}-{effort}", {}, None, True, ())


def _attribution(
    provider: providers_mod.Provider,
    profile: DelegateProfile | None,
    model: str | None,
) -> dict[str, str | None]:
    """The attribution record, computed by :mod:`keel.agents` so a host cannot drift.

    A configured provider also records **which** entry ran under ``delegate_profile``,
    never ``profile``: the ship run record already means the workflow profile
    (``standard``/``compound``) by that name, and writing the CLI's name there would
    silently overwrite it.
    """
    if profile is not None:
        return agents.profile_attribution(provider.name, profile, model)
    record = agents.attribution(provider.vendor, model)
    if provider.source != "builtin":
        record["delegate_profile"] = provider.name
    return record


def stream_json_frame(prompt: str) -> str:
    """One NDJSON user frame for agy's ``--input-format stream-json`` stdin.

    Shape ported from ai-jury's ``AgyAdapter._stdin_for``, verified there against
    agy 1.1.22.
    """
    return json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}) + "\n"


def parse_stream_json(raw: str) -> str:
    """The response text out of agy's NDJSON stdout, falling back to the raw stream.

    Falls back rather than returning empty: a truncated stream must surface as output an
    operator can read, not as a silent abstention. An empty review counts as a review,
    and a review read as an approval is the expensive failure (ai-jury #625).
    """
    response, saw_result = None, False
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("event") == "result":
            saw_result = True
            result = event.get("result")
            if isinstance(result, dict):
                response = result.get("response")
    if saw_result and isinstance(response, str):
        return response
    return raw


def parse_ollama_response(data: Any) -> str | None:
    """The completion out of an Ollama ``/api/generate`` payload (``None`` if malformed)."""
    if not isinstance(data, dict):
        return None
    text = data.get("response")
    return text if isinstance(text, str) and text else None


def ollama_payload(model: str, prompt: str) -> dict[str, Any]:
    """The ``/api/generate`` body. ``stream: false`` — keel wants one document, not NDJSON."""
    return {"model": model, "prompt": prompt, "stream": False}
