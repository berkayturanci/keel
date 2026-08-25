"""Agent dispatch + attribution — the pure resolution logic.

The backbone dispatches agentic steps (implement / review / extensions) to a
configured agent: the **host agent** by default, a per-run **delegate** override,
or a per-role agent from ``knobs.implementer_agents``. A delegate is either a
built-in vendor (:data:`BUILTIN_DELEGATE_VENDORS`) or the name of a generic
``knobs.delegate_profiles`` entry — built-ins always win. Attribution records the
*effective* implementer as labels (``agent:<vendor>`` + a versionless
``model:<base>``), reusing the ship #2036 stripping algorithm.

All functions here are pure and deterministic — no subprocess, no network.
"""

from __future__ import annotations

from .config import DelegateProfile, ProjectConfig

#: Default host agent when nothing else is resolved.
HOST_DEFAULT = "claude"

#: Hosted-API delegate vendors (#548, ``google-api`` added in #666): the vendor's
#: real API keyed by an env token, no agent CLI installed. Same no-tools contract
#: as ``ollama:`` — the
#: orchestrator owns every git/PR step and delegates only code generation. The
#: vendor names match ai-jury's hosted-adapter vocabulary so the value fits the
#: existing first-colon ``vendor:model`` split unchanged.
API_VENDORS = ("anthropic-api", "openai-api", "google-api")

#: Agent-CLI delegate vendors keel drives as a subprocess. Hardcoded on purpose — not
#: to be confused with the generic ``cli`` *profile* vendor (issue #659), which is the
#: operator-configured escape hatch for every CLI that is not one of these three.
CLI_VENDORS = ("claude", "codex", "agy")

#: Local-model delegate vendors: no agent CLI, no hosted key, and no tools.
LOCAL_VENDORS = ("ollama",)

#: Every delegate name keel understands with no configuration at all. Name resolution
#: is **fail-closed**: a ``knobs.delegate_profiles`` entry may not shadow one of these,
#: and the attempt is a config error rather than a silent override (issue #659).
BUILTIN_DELEGATE_VENDORS = CLI_VENDORS + LOCAL_VENDORS + API_VENDORS


def split_delegate(value: str) -> tuple[str, str | None]:
    """Split ``ollama:qwen2.5`` -> ``("ollama", "qwen2.5")``; ``codex`` -> ``("codex", None)``."""
    vendor, sep, model = value.partition(":")
    return vendor, (model if (sep and model) else None)


def is_api_delegate(vendor: str) -> bool:
    """True when ``vendor`` is a hosted-API delegate (``anthropic-api``/``openai-api``)."""
    return vendor in API_VENDORS


def resolve_delegate_profile(config: ProjectConfig, name: str) -> DelegateProfile | None:
    """The configured delegate profile for ``name``, or ``None``.

    ``name`` is the bare ``--delegate`` token (``split_delegate``'s vendor part). A
    built-in vendor **always wins** and never resolves to a profile — config cannot
    redefine ``codex`` even if a same-named profile somehow reached this point
    (:func:`keel.config.parse_config` rejects that shadowing up front).
    """
    if name in BUILTIN_DELEGATE_VENDORS:
        return None
    return config.knobs.delegate_profiles.get(name)


def is_profile_delegate(config: ProjectConfig, name: str) -> bool:
    """True when ``--delegate <name>`` dispatches to a generic delegate profile."""
    return resolve_delegate_profile(config, name) is not None


def resolve_agent(
    config: ProjectConfig,
    *,
    role: str | None = None,
    delegate: str | None = None,
    host_agent: str = HOST_DEFAULT,
) -> str:
    """Resolve which agent runs a step.

    Precedence: explicit ``delegate`` > per-role ``implementer_agents`` mapping >
    ``host_agent`` default.
    """
    if delegate:
        return delegate
    if role and role in config.knobs.implementer_agents:
        return config.knobs.implementer_agents[role]
    return host_agent


#: Transports that run a model on the operator's own hardware. Named separately
#: because :mod:`keel.cost` prices the *tier* at zero rather than the model —
#: the one place pricing and attribution want different halves of the same
#: string. ``local`` is not a keel delegate; it appears in ids keel ingests.
LOCAL_TRANSPORTS = LOCAL_VENDORS + ("local",)

#: Prefixes that name a **transport** rather than a model. Derived from the
#: vendor tuples above, so a vendor added there is covered the day it lands
#: instead of on the day someone remembers this list.
_TRANSPORT_PREFIXES = frozenset(API_VENDORS + LOCAL_TRANSPORTS)


def strip_transport(model: str) -> str:
    """Drop a ``<transport>:`` prefix, leaving the vendor's own model id.

    Both ``ollama:qwen2.5:7b`` and ``anthropic-api:claude-opus-4-5`` carry a
    colon and only the second has the model on the right, so the colon has to be
    read by *what is on either side of it* — never by position (#955). Reading
    it positionally is what labelled every hosted-API run ``model:anthropic-api``.

    A colon that is not preceded by a transport belongs to the model: an Ollama
    ``:tag`` (``qwen2.5:7b``) or a Bedrock revision (``…-v1:0``). Those are left
    for the caller, which knows whether it wants the tag.
    """
    m = model.strip().lower()
    head, sep, tail = m.partition(":")
    if sep and tail and head in _TRANSPORT_PREFIXES:
        return tail
    return m


def model_base(model: str) -> str:
    """Strip a model id to a coarse, versionless base label (ship #2036 algorithm).

    Examples: ``qwen2.5:7b`` -> ``qwen``, ``gemma2`` -> ``gemma``,
    ``llama3.1`` -> ``llama``, ``gpt-5.5`` -> ``gpt-5``, ``gpt-4o`` -> ``gpt-4o``,
    ``anthropic-api:claude-opus-4-5`` -> ``claude-opus-4-5``.
    """
    m = strip_transport(model)
    if not m:
        return ""
    m = m.split(":", 1)[0]  # (1) drop any ollama :tag
    if "-" in m:
        # (3) hyphenated family: keep <word>-<major>, drop the .minor
        head, _, tail = m.partition("-")
        major = tail.split(".", 1)[0]
        return f"{head}-{major}"
    # (2) non-hyphenated family: drop the trailing numeric run (digits + dots)
    i = len(m)
    while i > 0 and (m[i - 1].isdigit() or m[i - 1] == "."):
        i -= 1
    return m[:i]


def agent_label(vendor: str) -> str:
    """The persistent ``agent:<vendor>`` label."""
    return f"agent:{vendor}"


def model_label(model: str) -> str | None:
    """The versionless ``model:<base>`` label, or ``None`` when no base is known."""
    base = model_base(model)
    return f"model:{base}" if base else None


def attribution(vendor: str, model: str | None = None) -> dict[str, str | None]:
    """Resolve the effective attribution for an implementer/reviewer.

    Returns ``{"agent_label", "model_label", "system"}`` where ``system`` is the
    full ``vendor`` or ``vendor:model`` string for the closure comment.
    """
    system = f"{vendor}:{model}" if model else vendor
    return {
        "agent_label": agent_label(vendor),
        "model_label": model_label(model) if model else None,
        "system": system,
    }


def profile_attribution(
    name: str,
    profile: DelegateProfile,
    model: str | None = None,
) -> dict[str, str | None]:
    """Attribution for a generic delegate profile (issue #659).

    ``agent:<vendor>`` (``agent:cli``) plus the **effective** model — the same shape as
    :func:`attribution` — with an extra key naming the entry, so the closure comment can
    say *which* CLI ran rather than just ``cli``.

    That key is ``delegate_profile``, **not** ``profile``: the ship run record already
    uses ``profile`` for the workflow profile (``standard``/``compound``), so merging
    this dict into the record under the shorter name would silently overwrite it.

    ``model`` is the per-run override from ``--delegate <profile>:<model>`` and wins
    over the profile's own ``model``, matching the precedence s4 documents. Without it
    the helper could only ever report the configured model, which would break keel's
    rule that attribution records the *effective* implementer whenever an operator
    picked a model per run.
    """
    record = attribution(profile.vendor, model or profile.model)
    record["delegate_profile"] = name
    return record


#: Characters a per-run model token may contain. Deliberately tight: the effective model
#: can arrive from ``--delegate <profile>:<model>`` or a ``delegate-model:<name>`` issue
#: label, which is a lower-trust source than the operator-authored ``command``, and it
#: ends up on a subprocess argv.
_MODEL_TOKEN_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def is_safe_model_token(model: str | None) -> bool:
    """True when ``model`` is safe to pass to a delegate CLI as an argument.

    A profile's ``command`` is operator-authored config, but the *model* beside it may
    come from an issue label, so it does not carry the same trust. Anything outside
    ``[A-Za-z0-9._-]`` — whitespace, quotes, shell metacharacters, a leading dash that
    would read as another flag — is rejected rather than escaped, because no legitimate
    model id needs it. Empty/``None`` is False: pass no model instead.
    """
    if not model:
        return False
    if model.startswith("-"):
        return False
    return _MODEL_TOKEN_OK.issuperset(model)
