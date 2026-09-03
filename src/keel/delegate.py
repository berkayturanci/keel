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

#: Roles a delegate can be dispatched for. ``chair`` is the jury's summariser; it reads
#: reviews and writes nothing, so it sits with the read-only three.
ROLES = ("implement", "fix", "review", "gate", "chair")

#: Roles invoked read-only / findings-only. Everything not listed here is tool-enabled.
READ_ONLY_ROLES = ("review", "gate", "chair")

#: Reasoning-effort levels, lowest first. Mapped per vendor by :func:`plan_run`.
EFFORTS = ("low", "medium", "high")

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

#: Tools a read-only ``claude`` invocation refuses. Anything that can write a file, edit a
#: notebook, or shell out.
CLAUDE_DISALLOWED_TOOLS = "Edit,Write,NotebookEdit,Bash"

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
    #: True when the role's policy is read-only. For a profile this is the operator's
    #: ``review_args``, not a guarantee keel can enforce — see the module docstring.
    read_only: bool = False
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
    """Resolve ``name`` / ``vendor:model`` against profiles, the registry, the built-ins.

    Precedence is **project profile > registry > built-in**, the order
    :func:`keel.providers.plan_probes` documents. For every valid configuration the two
    agree by construction: ``plan_probes`` drops a registry entry whose name is already
    taken, and :func:`keel.providers.registry_clashes` reports it as an error, so a name
    resolves to exactly one provider. The explicit ordering here is what makes the stated
    precedence true rather than incidental if a clashing entry ever reaches dispatch.

    The model half of the token is validated by :func:`keel.agents.is_safe_model_token`
    before anything else looks at it: it can arrive from a ``delegate-model:`` issue
    label, which is a lower-trust source than the operator-authored command beside it, and
    it ends up on an argv or in a URL path. Refused, never escaped.
    """
    name, model = agents.split_delegate((token or "").strip())
    if not name:
        raise DelegateError("bad-provider", "--provider is empty; pass name or vendor:model")
    if model is not None and not agents.is_safe_model_token(model):
        raise DelegateError(
            "bad-model",
            f"model {model!r} is not a safe token ([A-Za-z0-9._-], no leading dash)",
        )
    profiles = {} if config is None else config.knobs.delegate_profiles
    if name in profiles:
        provider = _profile_provider(config, name)
        return Resolution(provider, profiles[name], model)
    registry = providers_mod.Registry(path="") if registry is None else registry
    for entry in registry.providers:
        if entry.name == name:
            return Resolution(entry, None, model)
    for entry in providers_mod.builtin_providers():
        if entry.name == name:
            return Resolution(entry, None, model)
    known = ", ".join(sorted({*profiles, *registry.names(), *agents.BUILTIN_DELEGATE_VENDORS}))
    raise DelegateError("unknown-provider", f"unknown provider {name!r}; known: {known}")


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
    if effective is not None and not agents.is_safe_model_token(effective):
        raise DelegateError(
            "bad-model",
            f"model {effective!r} is not a safe token ([A-Za-z0-9._-], no leading dash)",
        )
    read_only = role in READ_ONLY_ROLES
    transport = _transport_of(provider)
    applied = _apply_effort(provider.vendor, transport, effort, effective)
    effective, warnings = applied.model, applied.warnings
    argv: tuple[str, ...] = ()
    request: dict[str, Any] | None = None
    stdin_mode: str | None = None
    if transport == "cli":
        argv, stdin_mode = _builtin_argv(provider, read_only=read_only, model=effective)
    elif transport == "profile":
        argv, stdin_mode, profile_warnings = _profile_argv(
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
        effort=effort,
        effort_applied=applied.applied,
        warnings=warnings,
        attribution=_attribution(provider, profile, effective),
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
) -> tuple[tuple[str, ...], str]:
    """The argv + stdin framing for one of the three built-in agent CLIs."""
    command = provider.command or provider.name
    if provider.name == "claude":
        argv = [command, "-p"]
        if read_only:
            argv += ["--output-format", "text"]
        if model:
            argv += ["--model", model]
        if read_only:
            argv += ["--disallowed-tools", CLAUDE_DISALLOWED_TOOLS]
        argv += ["--dangerously-skip-permissions"]
        return tuple(argv), STDIN_TEXT
    if provider.name == "codex":
        sandbox = "read-only" if read_only else "workspace-write"
        argv = [command, "exec", "-s", sandbox, "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
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
) -> tuple[tuple[str, ...], str | None, tuple[str, ...]]:
    """The argv + framing for an operator-configured binary (profile or registry entry)."""
    command = provider.command
    if not command:
        raise DelegateError("bad-provider", f"provider {provider.name!r} names no command")
    if profile is not None:
        role_args = profile.role_args(review=read_only)
        prompt_mode = profile.prompt_mode
    else:
        role_args = provider.review_args if read_only else ()
        prompt_mode = DEFAULT_PROMPT_MODE
    argv = [command, *role_args]
    if model and provider.model_arg:
        argv += [provider.model_arg, model]
    warnings: tuple[str, ...] = ()
    if read_only and not role_args:
        warnings = (
            f"{provider.name}: no read-only invocation is configured (review_args), so the "
            f"{provider.name!r} command runs with its default permissions — keel cannot "
            "enforce read-only for an arbitrary CLI; re-check the worktree afterwards",
        )
    stdin_mode = STDIN_TEXT if prompt_mode == DEFAULT_PROMPT_MODE else None
    return tuple(argv), stdin_mode, warnings


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
