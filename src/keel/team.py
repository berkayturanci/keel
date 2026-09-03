"""``knobs.team`` — the per-role / per-tier provider policy (#1014).

Before this module keel could name *an* implementer (``--delegate``,
``knobs.implementer_agents``) and *one* reviewer vendor for all N reviewers
(``--review-delegate``). It could not express the team a real engineering org runs:
*this role implements with provider X at effort E, every implementation gets one gate
review from a different vendor, tier-2 gets two reviewers from two vendors, tier-3
convenes the jury as the review panel.*

``knobs.team`` is that policy, and this module is its pure half:

* :class:`Seat` — one occupied chair: a provider, optionally a model and an effort.
* :class:`TeamPolicy` — the parsed ``knobs.team`` block.
* :func:`parse_team` / :func:`canonical` — YAML in, typed policy out, and back to the
  canonical dict that feeds ``config_hash`` and the published contract.
* :func:`team_issues` — the semantic validation ``keel validate`` runs.
* :func:`resolve_assignment` — the deterministic answer to *who runs this ship*, which
  ``keel plan``/``keel ship`` render as ``assignment`` so any host runs the same team.

A ``provider`` names an entry the provider registry resolves — a built-in vendor, a
``knobs.delegate_profiles`` entry, or a machine-level ``~/.keel/providers.yaml`` entry —
with two reserved spellings:

``subagent:<name>``  the pre-#1014 Claude-subagent semantics ``implementer_agents``
                     values carried, kept explicit so a seat can no longer be read as
                     both a vendor and a subagent name;
``implementer``      valid in ``fix.provider`` and ``gate.distinct_from`` only: *the
                     provider that implemented this change*, whatever it resolved to.

Pure and deterministic: no wall-clock, no randomness, no I/O, and — at module scope —
no keel imports at all. The provider vocabulary lives next to dispatch in
:mod:`keel.agents` and :mod:`keel.delegate`, which both import :mod:`keel.config`, so
:func:`team_issues` reaches for it through a local import rather than closing a cycle
(the same pattern as :func:`keel.config._validate_delegate_profiles`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Prefix that keeps the pre-#1014 Claude-subagent semantics of ``implementer_agents``.
#: ``subagent:backend-developer`` is a subagent of the host agent, not a vendor keel
#: dispatches to — which is exactly the ambiguity #1014 opened against.
SUBAGENT_PREFIX = "subagent:"

#: Reserved provider value: *whoever implemented this change*. Valid in ``fix.provider``
#: and ``gate.distinct_from``; anywhere else it would name a provider that does not exist.
IMPLEMENTER = "implementer"

#: ``review.by_tier.<n>: jury`` — the cross-vendor panel **is** the review for that tier.
JURY_PANEL = "jury"

#: Risk tiers ``review.by_tier`` may key on, as **strings**. A JSON-Schema property name
#: is a string, and YAML reads a bare ``1:`` as an integer key, so the keys are quoted
#: (``"1":``) and :func:`team_issues` says so when they are not.
TIERS = ("1", "2", "3")

#: How an enabled jury gates. ``gating`` blocks the merge on a blocking verdict;
#: ``advisory`` reports and never gates.
JURY_MODES = ("gating", "advisory")

#: Distinct vendors a jury needs before its verdict can gate, when the policy is silent.
DEFAULT_MIN_VENDORS = 2

#: Default agent when neither the policy nor a flag names one: the host agent driving
#: the run. Mirrors :data:`keel.agents.HOST_DEFAULT`, which cannot be imported here.
HOST_DEFAULT = "claude"

#: Risk tier at which a cross-vendor review panel stops being optional: from tier-2 up,
#: ``evidence_require_distinct_vendors`` defaults to true (an explicit ``false`` in
#: config still wins). One vendor reviewing twice is one opinion twice.
DISTINCT_VENDOR_TIER = 2


@dataclass(frozen=True)
class Seat:
    """One chair on the team: who sits in it, on which model, at which effort."""

    provider: str
    model: str | None = None
    effort: str | None = None
    #: ``gate`` only: the seat this one may not duplicate (``implementer``).
    distinct_from: str | None = None

    @property
    def kind(self) -> str:
        """``subagent`` | ``alias`` | ``provider`` — how the provider value is read."""
        if self.provider.startswith(SUBAGENT_PREFIX):
            return "subagent"
        if self.provider == IMPLEMENTER:
            return "alias"
        return "provider"

    @property
    def name(self) -> str:
        """The bare name: a subagent seat without its prefix, anything else verbatim."""
        if self.kind == "subagent":
            return self.provider[len(SUBAGENT_PREFIX) :]
        return self.provider

    def as_dict(self, *, source: str | None = None, slot: str | None = None) -> dict[str, Any]:
        """JSON-stable seat record; ``source``/``slot`` are the assignment's annotations."""
        record: dict[str, Any] = {
            "provider": self.provider,
            "name": self.name,
            "kind": self.kind,
            "model": self.model,
            "effort": self.effort,
        }
        if self.distinct_from is not None:
            record["distinct_from"] = self.distinct_from
        if source is not None:
            record["source"] = source
        if slot is not None:
            record["slot"] = slot
        return record


@dataclass(frozen=True)
class TeamPolicy:
    """A parsed ``knobs.team`` block. ``configured`` is False when there is none."""

    configured: bool = False
    implement: Seat | None = None
    implement_by_role: Mapping[str, Seat] = field(default_factory=dict)
    gate: Seat | None = None
    #: ``review.default`` — seats, or the literal ``"jury"``.
    review: tuple[Seat, ...] | str | None = None
    #: Tier (``"1"``/``"2"``/``"3"``) -> reviewer seats, or the literal ``"jury"``.
    review_by_tier: Mapping[str, tuple[Seat, ...] | str] = field(default_factory=dict)
    jury_mode: str | None = None
    jury_min_vendors: int | None = None
    fix: Seat | None = None

    def review_for(self, tier: int | None) -> tuple[tuple[Seat, ...] | str | None, str | None]:
        """Reviewer seats (or ``"jury"``) for ``tier``, plus the config path they came from."""
        key = str(tier)
        if key in self.review_by_tier:
            return self.review_by_tier[key], f"team.review.by_tier.{key}"
        if self.review is not None:
            return self.review, "team.review.default"
        return None, None


def _text(value: Any) -> str | None:
    """A non-blank string, or ``None`` — so a blank field reads as unset."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _seat(raw: Any) -> Seat | None:
    """One seat mapping -> a :class:`Seat`; ``None`` when it names no provider.

    Shape errors are the schema's job and meaning is :func:`team_issues`'; this only has
    to be total, because :func:`keel.config.parse_config` builds the policy *after*
    validation and a caller must never get a half-parsed seat.
    """
    if not isinstance(raw, Mapping):
        return None
    provider = _text(raw.get("provider"))
    if provider is None:
        return None
    return Seat(
        provider=provider,
        model=_text(raw.get("model")),
        effort=_text(raw.get("effort")),
        distinct_from=_text(raw.get("distinct_from")),
    )


def _seats(raw: Any) -> tuple[Seat, ...] | str | None:
    """A reviewer list, the ``"jury"`` literal, or ``None`` when neither."""
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, Sequence) or isinstance(raw, (bytes, bytearray)):
        return None
    seats = tuple(seat for seat in (_seat(entry) for entry in raw) if seat is not None)
    return seats


def parse_team(raw: Any) -> TeamPolicy:
    """Parse a ``knobs.team`` block (``None``/malformed -> an unconfigured policy)."""
    if not isinstance(raw, Mapping):
        return TeamPolicy()
    implement = raw.get("implement") if isinstance(raw.get("implement"), Mapping) else {}
    by_role_raw = implement.get("by_role") if isinstance(implement.get("by_role"), Mapping) else {}
    by_role = {}
    for role in sorted(by_role_raw, key=str):
        seat = _seat(by_role_raw[role])
        if isinstance(role, str) and seat is not None:
            by_role[role] = seat
    review = raw.get("review") if isinstance(raw.get("review"), Mapping) else {}
    by_tier: dict[str, tuple[Seat, ...] | str] = {}
    by_tier_raw = review.get("by_tier") if isinstance(review.get("by_tier"), Mapping) else {}
    for tier in sorted(by_tier_raw, key=str):
        seats = _seats(by_tier_raw[tier])
        if isinstance(tier, str) and seats is not None:
            by_tier[tier] = seats
    default_review = _seats(review.get("default")) if "default" in review else None
    jury = raw.get("jury") if isinstance(raw.get("jury"), Mapping) else {}
    min_vendors = jury.get("min_vendors")
    return TeamPolicy(
        configured=True,
        implement=_seat(implement.get("default")),
        implement_by_role=by_role,
        gate=_seat(raw.get("gate")),
        review=default_review,
        review_by_tier=by_tier,
        jury_mode=_text(jury.get("mode")),
        jury_min_vendors=min_vendors if isinstance(min_vendors, int) else None,
        fix=_seat(raw.get("fix")),
    )


def _seat_canonical(seat: Seat) -> dict[str, Any]:
    record = {"provider": seat.provider, "model": seat.model, "effort": seat.effort}
    if seat.distinct_from is not None:
        record["distinct_from"] = seat.distinct_from
    return record


def canonical(policy: TeamPolicy) -> dict[str, Any]:
    """``{"team": {...}}`` for a configured policy, ``{}`` otherwise.

    Empty means **absent**, not ``{}``: an added optional field must not rotate
    ``config_hash`` for the projects that never used it, which is the same treatment
    :func:`keel.config.delegate_profiles_dict` gives ``delegate_profiles``. The flip
    side is the guarantee #1014 asks for — ``config_hash`` changes *iff* ``team`` does.
    """
    if not policy.configured:
        return {}
    team: dict[str, Any] = {}
    implement: dict[str, Any] = {}
    if policy.implement is not None:
        implement["default"] = _seat_canonical(policy.implement)
    if policy.implement_by_role:
        implement["by_role"] = {
            role: _seat_canonical(seat) for role, seat in sorted(policy.implement_by_role.items())
        }
    if implement:
        team["implement"] = implement
    if policy.gate is not None:
        team["gate"] = _seat_canonical(policy.gate)
    review: dict[str, Any] = {}
    if policy.review is not None:
        review["default"] = (
            policy.review
            if isinstance(policy.review, str)
            else [_seat_canonical(seat) for seat in policy.review]
        )
    if policy.review_by_tier:
        review["by_tier"] = {
            tier: value if isinstance(value, str) else [_seat_canonical(s) for s in value]
            for tier, value in sorted(policy.review_by_tier.items())
        }
    if review:
        team["review"] = review
    jury = {}
    if policy.jury_mode is not None:
        jury["mode"] = policy.jury_mode
    if policy.jury_min_vendors is not None:
        jury["min_vendors"] = policy.jury_min_vendors
    if jury:
        team["jury"] = jury
    if policy.fix is not None:
        team["fix"] = _seat_canonical(policy.fix)
    return {"team": team}


def legacy_seats(
    implementer_agents: Mapping[str, str],
    *,
    provider_names: Iterable[str] = (),
) -> dict[str, Seat]:
    """Map the deprecated ``knobs.implementer_agents`` onto ``team.implement.by_role``.

    ``implementer_agents`` values were documented as vendor strings in
    ``docs/keel/models.md`` and as Claude subagent names in ``ship.md`` s4, and nothing
    said which — #1014's opening complaint. The migration reads them the only way that
    keeps both documented meanings working: a value that names a provider keel can
    resolve *is* that provider; anything else is a host subagent and gets the explicit
    ``subagent:`` prefix it always meant.
    """
    known = set(provider_names)
    seats: dict[str, Seat] = {}
    for role, value in sorted(implementer_agents.items()):
        name = _text(value)
        if name is None:
            continue
        # Split the same way `--delegate` does before deciding this is a subagent name.
        # `docs/keel/models.md` documents `frontend: anthropic-api:claude-3-7-sonnet-…`
        # as a legal value, and treating the whole string as one opaque name turned it
        # into `subagent:anthropic-api:claude-…` — a host subagent that does not exist,
        # instead of the hosted-API vendor plus its model.
        seat = seat_from_token(name)
        if seat.kind == "subagent" or seat.provider in known:
            seats[role] = seat
        else:
            seats[role] = Seat(provider=f"{SUBAGENT_PREFIX}{name}")
    return seats


def seat_from_token(token: str) -> Seat:
    """A ``--delegate``/``--review-delegate`` token -> a seat.

    ``vendor:model`` splits on the first colon, exactly as
    :func:`keel.agents.split_delegate` does — except for ``subagent:<name>``, whose
    colon separates a *kind* from a name and never a model.
    """
    value = (token or "").strip()
    if value.startswith(SUBAGENT_PREFIX):
        return Seat(provider=value)
    provider, sep, model = value.partition(":")
    return Seat(provider=provider, model=model if (sep and model) else None)


def _implement_seat(
    policy: TeamPolicy,
    *,
    role: str | None,
    legacy: Mapping[str, Seat] | None,
    host_agent: str,
) -> tuple[Seat, str]:
    """The implementer and the config path it came from (policy > legacy > host)."""
    if role is not None and role in policy.implement_by_role:
        return policy.implement_by_role[role], f"team.implement.by_role.{role}"
    if policy.implement is not None:
        return policy.implement, "team.implement.default"
    if role is not None and legacy and role in legacy:
        return legacy[role], f"knobs.implementer_agents.{role} (deprecated)"
    return Seat(provider=host_agent), "host"


def _review_seats(
    policy: TeamPolicy,
    *,
    tier: int | None,
    default_count: int,
    reviewer_override: int | None,
    host_agent: str,
    jury_disabled: bool = False,
) -> tuple[tuple[Seat, ...], str, str, list[str]]:
    """Reviewer seats, the panel, the source, and any warnings.

    Precedence: a tier whose policy is ``jury`` empties the reviewer bench (the panel
    *is* the review); otherwise ``--reviewers`` wins over the policy's seat count, which
    wins over the tier-derived default.

    ``jury_disabled`` is ``--no-jury``, and it takes the panel back off the jury. Without
    that, "no jury" on a ``jury`` tier would mean *no review at all* — zero host slots and
    no panel, so the evidence gate would require no review evidence whatsoever. keel's own
    contract already promises the opposite in as many words
    (``test_gates.no_jury_preserves_review_and_test_gates``), and keel's CI passes
    ``--no-jury`` on every run, so this is the difference between a flag that skips the
    panel and a flag that skips the review.
    """
    configured, source = policy.review_for(tier)
    warnings: list[str] = []
    if configured == JURY_PANEL and jury_disabled:
        warnings.append(
            f"{source} makes the jury the review panel, but the jury is disabled for this "
            "run; the tier's host reviewers are staffed instead — --no-jury skips the "
            "panel, never the review"
        )
        configured, source = None, None
    if configured == JURY_PANEL:
        if reviewer_override is not None:
            warnings.append(
                f"--reviewers {reviewer_override} ignored: {source} makes the jury the "
                "review panel, so there are no host reviewer slots to size"
            )
        return (), JURY_PANEL, source, warnings
    seats = configured if isinstance(configured, tuple) else ()
    tier_source = "risk-tier" if tier is not None else "unresolved"
    if reviewer_override is not None:
        count, source = reviewer_override, "override"
    elif seats:
        count = len(seats)
    else:
        count, source = default_count, tier_source
    resolved = tuple(
        seats[index] if index < len(seats) else Seat(provider=host_agent) for index in range(count)
    )
    padded = max(0, count - len(seats))
    if padded and any(seat.provider == host_agent for seat in seats):
        # Comparing provider names, not vendors: resolving a name to its vendor needs the
        # registry, which this module deliberately cannot reach. A repeated *name* is
        # already a repeated vendor, so this catches the case that matters — a bench
        # `--reviewers 3` grew by re-seating the host that is already slot A, which
        # `require_distinct_vendors` will then reject at the evidence gate.
        warnings.append(
            f"{padded} reviewer slot(s) padded with the host agent {host_agent!r}, which "
            "is already seated; those reviewers cannot return distinct vendor provenance "
            "— name the extra seats in knobs.team.review, or lower --reviewers"
        )
    if seats and len(seats) > count:
        warnings.append(
            f"{len(seats)} reviewer seat(s) configured but only {count} slot(s) are "
            "staffed; the surplus seats are not dispatched"
        )
    return resolved, "reviewers", source or tier_source, warnings


#: The most reviewer seats a tier may name — keel's reviewer vocabulary is A/B/C, and
#: :func:`keel.ship.reviewer_focuses` has focus coverage for exactly those three.
MAX_REVIEW_SEATS = 3

#: Slot letters, by seat count. Deliberately not ``A``/``B``/``C`` for two seats: keel
#: merges the B focus into A at that count, so the second reviewer is slot **C**. Mirrors
#: :func:`keel.ship.reviewer_focuses`, which cannot be imported here (``ship`` imports
#: this module); ``tests/test_team.py`` asserts the two agree for every valid count.
_SLOT_LABELS = {0: (), 1: ("A",), 2: ("A", "C"), 3: ("A", "B", "C")}


def slot_labels(count: int) -> tuple[str, ...]:
    """Slot letters for ``count`` reviewer seats, matching ``ship.reviewer_focuses``.

    Total for any non-negative count, including counts keel's own vocabulary does not
    have a focus for. It is a labelling function: running short here turned an
    out-of-range reviewer count into an ``IndexError`` from the middle of the resolver
    rather than the documented ``ValueError`` the caller raises for it.
    """
    if count in _SLOT_LABELS:
        return _SLOT_LABELS[count]
    return tuple(chr(ord("A") + index) for index in range(max(0, count)))


def resolve_assignment(
    policy: TeamPolicy,
    *,
    tier: int | None = None,
    role: str | None = None,
    default_count: int = 2,
    reviewer_override: int | None = None,
    delegate: str | None = None,
    review_delegates: Sequence[str] = (),
    host_agent: str = HOST_DEFAULT,
    legacy: Mapping[str, Seat] | None = None,
    jury_disabled: bool = False,
) -> dict[str, Any]:
    """Who runs this ship: implementer, gate, reviewer slots, jury, fix.

    Deterministic for identical inputs, which is what lets ``keel plan`` and
    ``keel ship`` render the same team and any host run it. Per-run flags stay per-run
    overrides: ``--delegate`` replaces the implementer, and each ``--review-delegate``
    replaces one reviewer slot **positionally** — the first flag is slot A, the second
    slot B — so a two-vendor panel is expressible from the command line without a config
    change. A flag past the last slot is reported in ``warnings`` rather than silently
    dropped or silently growing the panel.
    """
    implementer, implementer_source = _implement_seat(
        policy, role=role, legacy=legacy, host_agent=host_agent
    )
    if delegate:
        implementer, implementer_source = seat_from_token(delegate), "flag:--delegate"
    seats, panel, review_source, warnings = _review_seats(
        policy,
        tier=tier,
        default_count=default_count,
        reviewer_override=reviewer_override,
        host_agent=host_agent,
        jury_disabled=jury_disabled,
    )
    labels = slot_labels(len(seats))
    sources = [review_source] * len(seats)
    overrides = [token for token in review_delegates if (token or "").strip()]
    for index, token in enumerate(overrides):
        if index >= len(seats):
            warnings.append(
                f"--review-delegate {token!r} names reviewer slot {index + 1}, but only "
                f"{len(seats)} reviewer slot(s) are staffed; it is not dispatched"
            )
            continue
        seats = seats[:index] + (seat_from_token(token),) + seats[index + 1 :]
        sources[index] = "flag:--review-delegate"
    gate = policy.gate
    gate_record = None
    if gate is not None:
        distinct_ok = gate.distinct_from != IMPLEMENTER or gate.name != implementer.name
        gate_record = gate.as_dict(source="team.gate")
        gate_record["distinct_ok"] = distinct_ok
        if not distinct_ok:
            warnings.append(
                f"team.gate.provider {gate.provider!r} is the resolved implementer and "
                "gate.distinct_from is 'implementer'; the gate review would be a second "
                "opinion from the first opinion"
            )
    fix_seat = policy.fix if policy.fix is not None else Seat(provider=IMPLEMENTER)
    fix_source = "team.fix" if policy.fix is not None else "default"
    resolved_fix = implementer if fix_seat.kind == "alias" else fix_seat
    fix_record = resolved_fix.as_dict(source=fix_source)
    fix_record["alias"] = IMPLEMENTER if fix_seat.kind == "alias" else None
    return {
        "configured": policy.configured,
        "role": role,
        "tier": tier,
        "implementer": implementer.as_dict(source=implementer_source),
        "gate": gate_record,
        "review_panel": panel,
        "reviewers": [
            seat.as_dict(source=sources[index], slot=labels[index])
            for index, seat in enumerate(seats)
        ],
        "reviewer_count": len(seats),
        "reviewer_source": review_source,
        "jury": {
            "mode": policy.jury_mode,
            "min_vendors": policy.jury_min_vendors or DEFAULT_MIN_VENDORS,
            "panel_is_review": panel == JURY_PANEL,
        },
        "fix": fix_record,
        "warnings": warnings,
    }


def require_distinct_vendors(setting: bool | None, tier: int | None) -> bool:
    """The effective ``evidence_require_distinct_vendors`` for a resolved tier.

    ``None`` is *unset* and is what makes the tier-derived default possible: from
    :data:`DISTINCT_VENDOR_TIER` up, a review panel that is really one vendor reviewing
    twice is one opinion twice, so the cross-vendor requirement is on. An explicit
    ``false`` in config is still honoured — a project that has decided otherwise has
    said so in a file a reviewer can read.
    """
    if setting is not None:
        return bool(setting)
    return tier is not None and tier >= DISTINCT_VENDOR_TIER


def _seat_paths(policy: TeamPolicy) -> list[tuple[str, Seat]]:
    """Every seat in the policy with the config path it sits at, in a stable order."""
    paths: list[tuple[str, Seat]] = []
    if policy.implement is not None:
        paths.append(("implement.default", policy.implement))
    paths.extend(
        (f"implement.by_role.{role}", seat)
        for role, seat in sorted(policy.implement_by_role.items())
    )
    if policy.gate is not None:
        paths.append(("gate", policy.gate))
    if isinstance(policy.review, tuple):
        paths.extend((f"review.default[{i}]", seat) for i, seat in enumerate(policy.review))
    for tier, value in sorted(policy.review_by_tier.items()):
        if isinstance(value, tuple):
            paths.extend((f"review.by_tier.{tier}[{i}]", seat) for i, seat in enumerate(value))
    if policy.fix is not None:
        paths.append(("fix", policy.fix))
    return paths


def team_issues(
    raw: Any,
    *,
    source: str,
    profiles: Mapping[str, str] | None = None,
    implementer_agents: Mapping[str, str] | None = None,
) -> list[str]:
    """Semantic errors for ``knobs.team`` (empty == valid).

    The schema owns the *shape*; this owns the *meaning* — which providers exist, which
    of them can honour an ``effort``, that the mandatory gate review really is a second
    opinion, and that a ``review.by_tier`` entry is either reviewer seats or the jury.

    ``implementer_agents`` is the deprecated per-role knob. It is read here because it
    still resolves implementers (:func:`legacy_seats`), so a ``gate`` declared
    ``distinct_from: implementer`` has to be checked against those seats too — otherwise
    ``implementer_agents: {core: codex}`` beside ``gate: {provider: codex}`` passes
    validation and the "mandatory second opinion" is the first opinion again.

    ``profiles`` maps a ``knobs.delegate_profiles`` name to its vendor. A machine-level
    ``~/.keel/providers.yaml`` entry is deliberately **not** consulted: validation has to
    give the same answer on every machine, and a policy that only validates where its
    author's home directory does is a policy the next operator cannot read.
    """
    if raw is None:
        return []
    # Local import on purpose: `agents` and `delegate` both import `keel.config`, which
    # imports this module for the policy type. Naming them at module scope would close a
    # real cycle, so the import moves instead of the vocabulary (the pattern
    # `keel.config._validate_delegate_profiles` already uses).
    from .agents import BUILTIN_DELEGATE_VENDORS
    from .delegate import EFFORTS, supports_effort

    if not isinstance(raw, Mapping):
        return []  # the schema already reported the wrong shape
    profiles = dict(profiles or {})
    known = {**{vendor: vendor for vendor in BUILTIN_DELEGATE_VENDORS}, **profiles}
    policy = parse_team(raw)
    errors: list[str] = []
    errors.extend(_tier_key_issues(raw, source=source))
    for path, seat in _seat_paths(policy):
        where = f"{source}.{path}"
        vendor = _seat_vendor(seat, path=path, known=known, where=where, errors=errors)
        if seat.effort is None:
            continue
        if seat.effort not in EFFORTS:
            errors.append(f"{where}: unknown effort {seat.effort!r}; valid: {', '.join(EFFORTS)}")
        elif vendor is None:
            continue  # the provider is already reported; do not pile on
        elif not supports_effort(vendor):
            errors.append(
                f"{where}: provider {seat.provider!r} ({vendor}) has no spelling for "
                f"reasoning effort, so effort {seat.effort!r} would be silently dropped "
                "— drop the field, or name a provider that can honour it"
            )
        elif vendor == "agy" and seat.model is None:
            errors.append(
                f"{where}: agy spells reasoning effort as a model suffix "
                f"(e.g. gemini-3.8-flash-high), so effort {seat.effort!r} needs a "
                "'model' beside it"
            )
    legacy = legacy_seats(implementer_agents or {}, provider_names=known)
    errors.extend(_gate_issues(policy, source=source, legacy=legacy))
    errors.extend(_review_issues(policy, source=source))
    return errors


def _seat_vendor(
    seat: Seat,
    *,
    path: str,
    known: Mapping[str, str],
    where: str,
    errors: list[str],
) -> str | None:
    """The vendor behind a seat, appending the error when the provider is not resolvable."""
    if seat.kind == "subagent":
        if not seat.name:
            errors.append(
                f"{where}: {SUBAGENT_PREFIX!r} needs a subagent name after it, "
                "e.g. 'subagent:backend-developer'"
            )
        return None
    if seat.kind == "alias":
        if path != "fix":
            errors.append(
                f"{where}: provider {IMPLEMENTER!r} means 'whoever implemented this "
                "change' and is only valid at team.fix.provider"
            )
        return None
    if seat.provider not in known:
        errors.append(
            f"{where}: unknown provider {seat.provider!r}; name a built-in vendor, a "
            f"knobs.delegate_profiles entry, or a host subagent as "
            f"'{SUBAGENT_PREFIX}{seat.provider}'. Known: {', '.join(sorted(known))}"
        )
        return None
    return known[seat.provider]


def _tier_key_issues(raw: Mapping[str, Any], *, source: str) -> list[str]:
    """``review.by_tier`` keys must be the quoted tier strings ``"1"``/``"2"``/``"3"``."""
    review = raw.get("review")
    by_tier = review.get("by_tier") if isinstance(review, Mapping) else None
    if not isinstance(by_tier, Mapping):
        return []
    errors = []
    for key in by_tier:
        if key in TIERS:
            continue
        errors.append(
            f"{source}.review.by_tier: {key!r} is not a risk tier; quote the key as "
            f'"1", "2" or "3" (YAML reads a bare 1: as an integer key, which a JSON '
            "schema cannot describe)"
        )
    return errors


def _gate_issues(
    policy: TeamPolicy,
    *,
    source: str,
    legacy: Mapping[str, Seat] | None = None,
) -> list[str]:
    """The mandatory second opinion must be able to *be* a second opinion."""
    gate = policy.gate
    if gate is None:
        return []
    if gate.distinct_from is not None and gate.distinct_from != IMPLEMENTER:
        return [
            f"{source}.gate: distinct_from {gate.distinct_from!r} is not a seat; the only "
            f"supported value is {IMPLEMENTER!r}"
        ]
    if gate.distinct_from != IMPLEMENTER:
        return []
    implementers = {
        seat.name: f"{source}.{path}"
        for path, seat in _seat_paths(policy)
        if path.startswith("implement")
    }
    # A role the policy does not name still resolves through the deprecated knob, so a
    # gate matching one of those seats is the same defect wearing an older spelling.
    for role, seat in sorted((legacy or {}).items()):
        implementers.setdefault(seat.name, f"knobs.implementer_agents.{role}")
    clash = implementers.get(gate.name)
    if clash is None:
        return []
    return [
        f"{source}.gate: provider {gate.provider!r} is also the implementer at "
        f"{clash}, and gate.distinct_from is {IMPLEMENTER!r} — a gate review "
        "from the vendor that wrote the change is not a second opinion"
    ]


def _review_issues(policy: TeamPolicy, *, source: str) -> list[str]:
    """A tier's review policy is reviewer seats or the ``jury`` literal, nothing else."""
    errors = []
    entries: list[tuple[str, tuple[Seat, ...] | str]] = [
        (f"review.by_tier.{tier}", value) for tier, value in sorted(policy.review_by_tier.items())
    ]
    if policy.review is not None:
        entries.append(("review.default", policy.review))  # seats or the jury literal
    for path, value in entries:
        if isinstance(value, str) and value != JURY_PANEL:
            errors.append(
                f"{source}.{path}: {value!r} is neither a list of reviewer seats nor "
                f"{JURY_PANEL!r} (the cross-vendor panel as the review)"
            )
        elif isinstance(value, tuple) and not value:
            errors.append(
                f"{source}.{path}: an empty reviewer list would leave the change with no "
                f"review; use {JURY_PANEL!r} for the panel, or name at least one seat"
            )
        elif isinstance(value, tuple) and len(value) > MAX_REVIEW_SEATS:
            errors.append(
                f"{source}.{path}: {len(value)} reviewer seats, but keel dispatches at "
                f"most {MAX_REVIEW_SEATS} (slots A/B/C, one focus each); use "
                f"{JURY_PANEL!r} for a wider panel"
            )
    if policy.jury_mode is not None and policy.jury_mode not in JURY_MODES:
        errors.append(
            f"{source}.jury.mode: unknown mode {policy.jury_mode!r}; valid: {', '.join(JURY_MODES)}"
        )
    return errors
