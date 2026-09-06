"""Agent dispatch + attribution — the pure resolution logic.

The backbone dispatches agentic steps (implement / review / extensions) to a
configured agent: the **host agent** by default, a per-run **delegate** override,
or a per-role seat from ``knobs.team.implement.by_role``. *Which* seat that is, is
:func:`keel.team.resolve_assignment`'s single answer — this module owns the delegate
vocabulary that feeds it and the attribution written afterwards, not a second copy of
the precedence rule (#1099). :func:`legacy_team_seats` is the bridge: it reads the
deprecated ``knobs.implementer_agents`` as ``by_role`` seats so the one resolver can
fall back to it. A delegate is either a built-in vendor
(:data:`BUILTIN_DELEGATE_VENDORS`) or the name of a generic ``knobs.delegate_profiles``
entry — built-ins always win. Attribution records the *effective* implementer as labels
(``agent:<vendor>`` + a versionless ``model:<base>``), reusing the ship #2036 stripping
algorithm.

All functions here are pure and deterministic — no subprocess, no network.
"""

from __future__ import annotations

from . import team
from .config import DELEGATE_PROFILE_VENDORS, DelegateProfile, ProjectConfig

# The vendor vocabulary itself lives in the leaf :mod:`keel.vocab`, so the *validating*
# half of keel (``keel.team``, ``keel.config``) can read it without importing dispatch
# (#1050). Re-exported here under the original names: ``agents.CLI_VENDORS`` and friends
# are what the rest of the package, the docs and the tests have always read.
from .vocab import API_VENDORS as API_VENDORS
from .vocab import BUILTIN_DELEGATE_VENDORS as BUILTIN_DELEGATE_VENDORS
from .vocab import CLI_VENDORS as CLI_VENDORS
from .vocab import LOCAL_VENDORS as LOCAL_VENDORS

#: The module's public surface, in definition order (#1070). It is declared because the
#: ``X as X`` re-exports above are read from *other* modules — a use CodeQL's
#: ``py/unused-import`` cannot see, since it counts same-module uses only. A name listed
#: in ``__all__`` is used by definition, so the declaration answers the scanner with the
#: language's own statement of intent rather than with a dismissal. Being a real
#: declaration it has to be the *whole* surface, not the re-exports alone;
#: ``tests/test_reexport_surface.py`` holds it to that in both directions.
__all__ = [
    "API_VENDORS",
    "BUILTIN_DELEGATE_VENDORS",
    "CLI_VENDORS",
    "LOCAL_VENDORS",
    "HOST_DEFAULT",
    "split_delegate",
    "known_vendors",
    "is_api_delegate",
    "resolve_delegate_profile",
    "is_profile_delegate",
    "provider_names",
    "legacy_team_seats",
    "known_roles",
    "LOCAL_TRANSPORTS",
    "strip_transport",
    "model_base",
    "agent_label",
    "model_label",
    "attribution_labels",
    "attribution",
    "attribution_from_implementer",
    "profile_attribution",
    "is_safe_model_token",
]

#: Default host agent when nothing else is resolved.
HOST_DEFAULT = "claude"


def split_delegate(value: str) -> tuple[str, str | None]:
    """Split ``ollama:qwen2.5`` -> ``("ollama", "qwen2.5")``; ``codex`` -> ``("codex", None)``."""
    vendor, sep, model = value.partition(":")
    return vendor, (model if (sep and model) else None)


def known_vendors(config: ProjectConfig | None = None) -> frozenset[str]:
    """Every vendor slug keel's attribution vocabulary can legitimately produce.

    The built-in vendors, the host default, the profile vendors a
    ``knobs.delegate_profiles`` entry may declare (``cli`` /
    ``openai-compatible``), and — when a config is supplied — the configured
    profile *names*, because ``--delegate <name>`` is spelled with the name.

    Callers use this to refuse a vendor keel could never have produced. Without
    a config the set is the configuration-free vocabulary, which is why the
    ledger-writing check only warns: a record may predate the current config.
    """
    names = {*BUILTIN_DELEGATE_VENDORS, *DELEGATE_PROFILE_VENDORS, HOST_DEFAULT}
    if config is not None:
        names.update(config.knobs.delegate_profiles)
        names.update(profile.vendor for profile in config.knobs.delegate_profiles.values())
    return frozenset(names)


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


def provider_names(config: ProjectConfig) -> frozenset[str]:
    """Every provider name this project can select without a machine-level registry."""
    return frozenset({*BUILTIN_DELEGATE_VENDORS, *config.knobs.delegate_profiles})


def legacy_team_seats(config: ProjectConfig) -> dict[str, team.Seat]:
    """``knobs.implementer_agents`` read as ``team.implement.by_role`` seats (#1014).

    The deprecated knob stays accepted; this is where its values acquire the meaning the
    schema never stated. A value that names a provider this project can select is that
    provider; anything else is the Claude subagent ``ship.md`` s4 always treated it as,
    and gets the explicit ``subagent:`` prefix.
    """
    return team.legacy_seats(config.knobs.implementer_agents, provider_names=provider_names(config))


def known_roles(config: ProjectConfig) -> frozenset[str]:
    """Every role name this project's routing can be keyed on, in **either** vocabulary.

    ``team.implement.by_role`` (#1014) is where a role lives now, and the deprecated
    ``knobs.implementer_agents`` still routes for a project that has not migrated — so a
    role may be spelled in either, and the set of role names is the union of both key
    sets. Reading only the old one silently stopped narrowing the role for any project
    that had adopted ``team``, including keel itself; #1014 had to correct that in two
    files at once, and this is the one place it is now stated, so a third vocabulary — or
    the day the deprecated knob is finally dropped — is one edit and not a search (#1107).

    It sits beside :func:`legacy_team_seats`, the other bridge from the deprecated knob
    into the current vocabulary, because :mod:`keel.team` — where the rest of the team
    policy lives — takes exactly one keel import (the leaf :mod:`keel.vocab`) and cannot
    read a :class:`~keel.config.ProjectConfig` without :mod:`keel.config` importing it
    back (#1050).

    The rule is *which spellings name a role*, and only that: a caller that needs an
    order imposes its own. The ``implement`` contract sorts the result for its
    ``routing_keys`` because the contract's ordering is the contract's business.
    """
    return frozenset({*config.knobs.implementer_agents, *config.knobs.team.implement_by_role})


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


def attribution_labels(config: ProjectConfig | None = None) -> tuple[str, ...]:
    """Every ``agent:*`` / ``model:*`` label keel's attribution vocabulary can write.

    Sorted and deduplicated, for ``keel doctor``'s ``policy_labels`` check (#1021): a
    label keel applies must already exist on the repository or GitHub rejects the call,
    and the attribution pair is applied by name just like the policy pack's own
    vocabularies.

    The set is the built-in vendors plus the host default and — when a config is given —
    each ``knobs.delegate_profiles`` entry's **vendor** (``agent:cli``, the label
    :func:`profile_attribution` writes; the profile *name* goes in ``delegate_profile``,
    never in a label) and the model that entry pins.

    ``model:*`` is only enumerable that far. The effective model can arrive from
    ``--delegate <vendor>:<model>`` or a ``delegate-model:`` issue label, so the labels
    minted from those are unbounded and no check can list them ahead of time.
    """
    vendors = {*BUILTIN_DELEGATE_VENDORS, HOST_DEFAULT}
    models: set[str] = set()
    if config is not None:
        for profile in config.knobs.delegate_profiles.values():
            vendors.add(profile.vendor)
            if profile.model:
                models.add(profile.model)
    labels = {agent_label(vendor) for vendor in vendors}
    labels.update(label for label in map(model_label, models) if label)
    return tuple(sorted(labels))


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


def attribution_from_implementer(implementer: str | None) -> dict[str, str | None] | None:
    """Attribution for a ledger ``actors.implementer`` value, or ``None`` when unset.

    The ledger records the effective implementer as ``vendor`` or ``vendor:model``
    (issue #1013 — never the delegate-profile name, which goes in
    ``delegate_profile``). Splitting it here rather than at each call site is what
    keeps the PR labels, the provenance comment and the evidence cross-check reading
    the *same* vocabulary from the *same* string instead of three hand-written ones.
    """
    if not isinstance(implementer, str) or not implementer.strip():
        return None
    vendor, model = split_delegate(implementer.strip())
    vendor = vendor.strip().lower()
    if not vendor:
        return None
    return attribution(vendor, model)


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
