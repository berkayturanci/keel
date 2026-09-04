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

Pure and deterministic: no wall-clock, no randomness, no I/O, and exactly one keel
import — the leaf :mod:`keel.vocab`, which owns the provider and effort vocabulary
:func:`team_issues` validates against. That vocabulary used to live next to dispatch in
:mod:`keel.agents` / :mod:`keel.delegate`, which both import :mod:`keel.config`, which
imports this module for the policy type; validation therefore reached it through a
function-local import. Moving the vocabulary instead of the import is what removed the
cycle rather than hiding it (#1050).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .vocab import BUILTIN_DELEGATE_VENDORS, EFFORTS, supports_effort

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

#: What happens on a jury-panel tier when the panel cannot be staffed *here* (#1066).
#: ``fallback`` seats a host bench of the same size in its place; ``block`` refuses the
#: run. A **configured allowance, not a flag**: #1014 round 3 settled that an operator's
#: preference may not take the panel off, and this does not reopen it — availability is
#: measured by :func:`keel.juryavail.assess` from the probe keel already runs, never
#: asserted on a command line.
JURY_ON_UNAVAILABLE = ("fallback", "block")

#: The allowance a project that never names one gets. ``fallback`` is the sensible answer
#: for the single-maintainer case the panel would otherwise wall off; a project whose
#: product claim *is* cross-vendor review says ``block`` and keeps today's strictness.
JURY_ON_UNAVAILABLE_DEFAULT = "fallback"

#: ``reviewer_source`` on a bench seated because the panel could not be. Named rather than
#: derived from the tier's config path so a reader of the published contract can tell a
#: fallback bench from a tier that simply never had a panel.
JURY_FALLBACK_SOURCE = "jury-fallback"

#: ``by_difficulty`` bands, lightest first. A band names the bench that staffs work of
#: that weight; :func:`keel.swarm.score_difficulty` decides which band a cluster is, and
#: the same resolver seats it. Unlike a tier — which is *how risky the change is* and is
#: read off the files it touches — a band is *how much work it is*, which is what decides
#: whether the strong implementer is worth spending on it (#1017).
DIFFICULTY_BANDS = ("easy", "standard", "hard")

#: Distinct vendors a jury needs before its verdict can gate, when the policy is silent.
DEFAULT_MIN_VENDORS = 2

#: Default agent when neither the policy nor a flag names one: the host agent driving
#: the run. Mirrors :data:`keel.agents.HOST_DEFAULT`, which cannot be imported here.
HOST_DEFAULT = "claude"

#: Vendors that spell reasoning effort as a **model suffix** rather than as its own
#: argument (``gemini-3.8-flash-high``). An effort on such a seat with no ``model``
#: beside it has nothing to attach to, so :func:`team_issues` rejects the pair — and
#: :mod:`keel.wizard` reads the same tuple rather than re-deriving the rule, so the
#: wizard cannot offer an effort it would then be told to take back.
EFFORT_MODEL_SUFFIX_VENDORS = ("agy",)


def effort_needs_model(vendor: str | None) -> bool:
    """True when an ``effort`` on this vendor's seat requires a ``model`` beside it."""
    return vendor in EFFORT_MODEL_SUFFIX_VENDORS


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
class Bench:
    """A named bench: who leads it, who implements on it, who reviews, at what effort.

    One type, two tables. ``team.by_difficulty`` picks a bench from a cluster's *scored
    difficulty*; ``team.profiles`` lets an operator pick one by name (``--team``). They
    are the same thing — "staff this piece of work from this bench" — so they resolve
    through one code path rather than two that can disagree.
    """

    lead: Seat | None = None
    implement: Seat | None = None
    #: Reviewer seats, or the literal ``"jury"``; ``None`` leaves the tier's policy alone.
    review: tuple[Seat, ...] | str | None = None
    #: Effort for this bench's implementer, when the seat does not name one itself.
    effort: str | None = None


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
    #: ``team.jury.on_unavailable`` — what a jury-panel tier does when the panel cannot be
    #: staffed here. ``None`` is unset and resolves to
    #: :data:`JURY_ON_UNAVAILABLE_DEFAULT`; kept tri-state at the config boundary so an
    #: explicit ``fallback`` stays distinguishable from silence for ``config_hash``.
    jury_on_unavailable: str | None = None
    fix: Seat | None = None
    #: The seat that coordinates a batch of ships — the team lead a swarm cluster or a
    #: work block reports through. Defaults to the host agent driving the run.
    lead: Seat | None = None
    #: Difficulty band (:data:`DIFFICULTY_BANDS`) -> the bench that staffs work of that
    #: weight.
    by_difficulty: Mapping[str, Bench] = field(default_factory=dict)
    #: Operator-selectable benches (``--team <profile>``).
    profiles: Mapping[str, Bench] = field(default_factory=dict)

    def benches_for(
        self, *, difficulty: str | None = None, profile: str | None = None
    ) -> tuple[tuple[Bench, str], ...]:
        """Benches that apply to this run, most specific first, each with its config path.

        A named ``--team`` profile is an operator saying *this bench, for this batch*, so
        it outranks the band the scorer derived. They are not exclusive: each field
        resolves down the list on its own, so a profile that names only reviewers still
        lets the band's implementer stand instead of silently dropping it.
        """
        found: list[tuple[Bench, str]] = []
        if profile is not None and profile in self.profiles:
            found.append((self.profiles[profile], f"team.profiles.{profile}"))
        if difficulty is not None and difficulty in self.by_difficulty:
            found.append((self.by_difficulty[difficulty], f"team.by_difficulty.{difficulty}"))
        return tuple(found)

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


def _bench(raw: Any) -> Bench | None:
    """One ``by_difficulty``/``profiles`` entry -> a :class:`Bench`, or ``None``.

    An entry that names nothing at all is ``None`` rather than an empty bench, so an
    accidental ``hard: {}`` does not read as *a bench that overrides everything with
    nothing* — it reads as absent, and the role/tier policy stands.
    """
    if not isinstance(raw, Mapping):
        return None
    bench = Bench(
        lead=_seat(raw.get("lead")),
        implement=_seat(raw.get("implement")),
        review=_seats(raw.get("review")) if "review" in raw else None,
        effort=_text(raw.get("effort")),
    )
    if bench == Bench():
        return None
    return bench


def _benches(raw: Any) -> dict[str, Bench]:
    """A ``by_difficulty``/``profiles`` mapping -> named benches, in a stable order."""
    if not isinstance(raw, Mapping):
        return {}
    benches: dict[str, Bench] = {}
    for name in sorted(raw, key=str):
        bench = _bench(raw[name])
        if isinstance(name, str) and bench is not None:
            benches[name] = bench
    return benches


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
        jury_on_unavailable=_text(jury.get("on_unavailable")),
        fix=_seat(raw.get("fix")),
        lead=_seat(raw.get("lead")),
        by_difficulty=_benches(raw.get("by_difficulty")),
        profiles=_benches(raw.get("profiles")),
    )


def _seat_canonical(seat: Seat) -> dict[str, Any]:
    record = {"provider": seat.provider, "model": seat.model, "effort": seat.effort}
    if seat.distinct_from is not None:
        record["distinct_from"] = seat.distinct_from
    return record


def _bench_canonical(bench: Bench) -> dict[str, Any]:
    record: dict[str, Any] = {}
    if bench.lead is not None:
        record["lead"] = _seat_canonical(bench.lead)
    if bench.implement is not None:
        record["implement"] = _seat_canonical(bench.implement)
    if bench.review is not None:
        record["review"] = (
            bench.review
            if isinstance(bench.review, str)
            else [_seat_canonical(seat) for seat in bench.review]
        )
    if bench.effort is not None:
        record["effort"] = bench.effort
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
    # Absent when unset, like every other optional field here: a project that never names
    # `on_unavailable` keeps the `config_hash` it had before the setting existed (#1066).
    if policy.jury_on_unavailable is not None:
        jury["on_unavailable"] = policy.jury_on_unavailable
    if jury:
        team["jury"] = jury
    if policy.fix is not None:
        team["fix"] = _seat_canonical(policy.fix)
    if policy.lead is not None:
        team["lead"] = _seat_canonical(policy.lead)
    for key, benches in (("by_difficulty", policy.by_difficulty), ("profiles", policy.profiles)):
        if benches:
            team[key] = {name: _bench_canonical(b) for name, b in sorted(benches.items())}
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
    benches: Sequence[tuple[Bench, str]] = (),
) -> tuple[Seat, str]:
    """The implementer and the config path it came from (bench > policy > legacy > host).

    A bench outranks ``by_role`` because it is the more specific statement: the role says
    *what part of the system this is*, the bench says *what this particular piece of work
    costs*, and "the hard ones go to the strong implementer" is only expressible if the
    second wins. ``--delegate`` still outranks both — that is the caller's job, above.
    """
    for bench, source in benches:
        if bench.implement is not None:
            return bench.implement, f"{source}.implement"
    if role is not None and role in policy.implement_by_role:
        return policy.implement_by_role[role], f"team.implement.by_role.{role}"
    if policy.implement is not None:
        return policy.implement, "team.implement.default"
    if role is not None and legacy and role in legacy:
        return legacy[role], f"knobs.implementer_agents.{role} (deprecated)"
    return Seat(provider=host_agent), "host"


def jury_on_unavailable(setting: str | None) -> str:
    """The effective ``knobs.team.jury.on_unavailable`` (#1066).

    ``None`` — unset — is :data:`JURY_ON_UNAVAILABLE_DEFAULT`. An unrecognised value cannot
    get past ``keel validate`` (:func:`team_issues` rejects it) but resolves to the default
    rather than raising: this is read on the resolution path, and a config that reached it
    must still resolve to *some* policy. The setting stays tri-state at the config boundary
    so an explicit ``fallback`` remains distinguishable from silence, which is what
    ``config_hash`` reads.
    """
    return setting if setting in JURY_ON_UNAVAILABLE else JURY_ON_UNAVAILABLE_DEFAULT


def _panel_falls_back(availability: Mapping[str, Any] | None) -> bool:
    """True when a measured probe says the panel is unstaffable *and* the policy allows it.

    ``None`` — no probe ran — is False: the panel stands. Nothing here decides the policy;
    ``decision`` was already resolved by :meth:`keel.juryavail.Availability.decision`, so
    this module keeps exactly one reading of the operator's configured allowance.
    """
    if not isinstance(availability, Mapping):
        return False
    return availability.get("decision") == JURY_ON_UNAVAILABLE[0]


def _availability_reason(availability: Mapping[str, Any] | None) -> str:
    """The probe's own sentence, so the warning names the seats rather than summarising."""
    reason = availability.get("reason") if isinstance(availability, Mapping) else None
    return reason if isinstance(reason, str) and reason.strip() else "the probe reported no detail"


def _review_seats(
    policy: TeamPolicy,
    *,
    tier: int | None,
    default_count: int,
    reviewer_override: int | None,
    host_agent: str,
    jury_disabled: bool = False,
    jury_advisory: bool = False,
    benches: Sequence[tuple[Bench, str]] = (),
    jury_availability: Mapping[str, Any] | None = None,
) -> tuple[tuple[Seat, ...], str, str, list[str]]:
    """Reviewer seats, the panel, the source, and any warnings.

    Precedence: a tier whose policy is ``jury`` empties the reviewer bench (the panel
    *is* the review); otherwise ``--reviewers`` wins over the policy's seat count, which
    wins over the tier-derived default.

    ``jury_availability`` is the one exception, and it is not a preference (#1066). It is
    :func:`keel.juryavail.assess`'s verdict on whether this machine can convene the panel
    at all, measured from the same probe ``keel doctor --providers`` prints. When it says
    the panel is unstaffable and ``team.jury.on_unavailable`` is ``fallback``, the tier
    resolves onto the **tier's own** seat count, staffed from the host, and the record says
    so. The seat count and the evidence requirement do not move; only who sits does — which
    is why ``--reviewers`` stays ignored on a fallback bench exactly as it is ignored while
    the panel sits. A flag that was inert on a staffable panel and *lowered* the tier's
    requirement the moment the probe failed would make a failed probe a policy change.
    Under ``block`` the panel stays the panel and the run never reaches here:
    :func:`keel.providerprobe.jury_availability` refuses at the probe.

    **The bench is a pure function of config + tier + role + the explicit ``--reviewers``
    and ``--review-delegate`` overrides, and of nothing else.** In particular it does not
    depend on the jury flags. It cannot: every surface accepts them since #1043, but
    nothing makes a *run* pass them uniformly — keel's CI passes ``--no-jury`` to
    ``evidence-verify`` on every run while passing it to neither ``ship`` nor ``plan``.
    A bench that moved with that flag would have ``plan`` requiring
    a jury verdict from zero reviewers while ``evidence-verify`` demanded three host
    verdicts of the same PR, which is the same contract disagreement in a new place.

    So ``jury_disabled`` / ``jury_advisory`` are recorded, never applied: on a tier whose
    review policy is the panel, the panel **is** the review, and a per-run flag does not
    get to remove the only review that tier has. :func:`keel.ship.resolve_jury` keeps the
    verdict required for the same reason.
    """
    configured, source = policy.review_for(tier)
    for bench, bench_source in benches:
        if bench.review is not None:
            configured, source = bench.review, f"{bench_source}.review"
            break
    warnings: list[str] = []
    fell_back = configured == JURY_PANEL and _panel_falls_back(jury_availability)
    if fell_back:
        # The panel is this tier's review and this machine cannot convene it. Fall through
        # to the ordinary path with no configured seats, which *is* "a tier without a
        # panel": the tier's own count, staffed from the host. Reached only from a
        # measured probe, and the reason travels in `warnings` and in the assignment's
        # `jury.availability` block so no reader has to re-derive it.
        warnings.append(
            f"{source} makes the jury the review for this tier, but the panel cannot be "
            f"staffed here; knobs.team.jury.on_unavailable is 'fallback', so a host bench "
            f"of {default_count} seat(s) reviews instead — the same count and the same "
            f"evidence, different reviewers, and they share one vendor so this review "
            f"carries no cross-vendor independence claim. "
            f"{_availability_reason(jury_availability)}"
        )
        if reviewer_override is not None:
            # The same flag, ignored the same way, whether or not the panel could sit.
            # `--reviewers` is inert on a panel tier — the panel *is* the review — and a
            # fallback may not turn that inert flag into a live one: a bench sized by
            # `--reviewers 2` would publish a two-verdict evidence requirement where the
            # tier asks for three, so a probe failure would have *lowered* the tier's
            # policy. The fallback changes who sat, never how many.
            warnings.append(
                f"--reviewers {reviewer_override} ignored: {source} makes the jury the "
                f"review panel, and the host bench standing in for it is the tier's own "
                f"{default_count} seat(s) — a fallback changes who reviews, not how many"
            )
        reviewer_override = None
        configured = ()
    if configured == JURY_PANEL:
        if reviewer_override is not None:
            warnings.append(
                f"--reviewers {reviewer_override} ignored: {source} makes the jury the "
                "review panel, so there are no host reviewer slots to size"
            )
        ignored = [
            flag
            for flag, passed in (("--no-jury", jury_disabled), ("--jury-advisory", jury_advisory))
            if passed
        ]
        if ignored:
            warnings.append(
                f"{' and '.join(ignored)} does not apply: this tier's review is the jury "
                f"panel ({source}). The panel is the review, so its verdict stays required"
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
    if fell_back:
        # Named, not inherited from the tier's config path: a reader of the published
        # contract must be able to tell a bench seated because the panel could not be
        # from a tier that simply never had one.
        source = JURY_FALLBACK_SOURCE
    resolved = tuple(
        seats[index] if index < len(seats) else Seat(provider=host_agent) for index in range(count)
    )
    padded = max(0, count - len(seats))
    if fell_back:
        # The pad warning's advice ("name the extra seats in knobs.team.review") is wrong
        # here: this tier *did* name its reviewers — it named the panel. The fallback
        # warning above already carries the one fact that advice was protecting.
        padded = 0
    if padded > 1 or (padded and any(seat.provider == host_agent for seat in seats)):
        # Two conditions, because there are two ways the pad duplicates. The host may
        # already be a configured seat (`[claude, codex]` + `--reviewers 3`), or it may
        # not be and simply get seated twice (`[codex]` + `--reviewers 3` ->
        # `[codex, claude, claude]`): the second is a duplicate between two *padded*
        # slots, which a check against the configured seats alone never sees.
        #
        # Comparing provider names, not vendors: resolving a name to its vendor needs the
        # registry, which this module deliberately cannot reach. A repeated *name* is
        # already a repeated vendor, and `require_distinct_vendors` rejects it at the
        # evidence gate long after the run.
        seated = any(seat.provider == host_agent for seat in seats)
        where = "which is already seated" if seated else "filling more than one slot"
        warnings.append(
            f"{padded} reviewer slot(s) padded with the host agent {host_agent!r}, "
            f"{where}; those reviewers cannot return distinct vendor provenance — name "
            "the extra seats in knobs.team.review, or lower --reviewers"
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


def _effective_effort(
    seat: Seat, *, bench_effort: str | None, flag_effort: str | None
) -> str | None:
    """The effort this seat actually runs at: ``--effort`` > the seat's own > the bench's.

    The flag wins because it is the operator speaking about *this run*; the seat wins over
    the bench because a seat that names a provider **and** an effort is one statement, and
    the bench's ``effort`` is the default for seats that did not bother.
    """
    return flag_effort or seat.effort or bench_effort


def _seat_at_effort(seat: Seat, effort: str | None) -> Seat:
    """``seat`` running at ``effort`` — the same object when nothing changes."""
    if effort == seat.effort:
        return seat
    return Seat(
        provider=seat.provider,
        model=seat.model,
        effort=effort,
        distinct_from=seat.distinct_from,
    )


def _lead_seat(
    policy: TeamPolicy,
    *,
    benches: Sequence[tuple[Bench, str]],
    host_agent: str,
) -> tuple[Seat, str]:
    """Who coordinates this batch of work: bench > ``team.lead`` > the host agent.

    Always a seat, never ``None``: an unconfigured project still has a lead — the agent
    driving the run — and a swarm coordinator that had to special-case "no lead" would
    grow a second answer to a question this one already answers.
    """
    for bench, source in benches:
        if bench.lead is not None:
            return bench.lead, f"{source}.lead"
    if policy.lead is not None:
        return policy.lead, "team.lead"
    return Seat(provider=host_agent), "host"


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
    jury_advisory: bool = False,
    difficulty: str | None = None,
    team_profile: str | None = None,
    effort: str | None = None,
    jury_availability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Who runs this ship: implementer, gate, reviewer slots, jury, fix.

    Deterministic for identical inputs, which is what lets ``keel plan`` and
    ``keel ship`` render the same team and any host run it. Per-run flags stay per-run
    overrides: ``--delegate`` replaces the implementer, and each ``--review-delegate``
    replaces one reviewer slot **positionally** — the first flag is slot A, the second
    slot B — so a two-vendor panel is expressible from the command line without a config
    change. A flag past the last slot is reported in ``warnings`` rather than silently
    dropped or silently growing the panel.

    ``difficulty`` and ``team_profile`` select a :class:`Bench` (see
    :meth:`TeamPolicy.benches_for`), which is how a batch runner says *this cluster is
    hard, give it the strong implementer at high effort; the easy ones go to the cheap
    model*. They are inputs to this one resolver rather than a second one beside it, so
    ``keel ship``, ``keel plan`` and ``keel swarm-plan`` cannot disagree about who runs a
    given issue.
    """
    benches = policy.benches_for(difficulty=difficulty, profile=team_profile)
    implementer, implementer_source = _implement_seat(
        policy, role=role, legacy=legacy, host_agent=host_agent, benches=benches
    )
    if delegate:
        implementer, implementer_source = seat_from_token(delegate), "flag:--delegate"
    bench_effort = next((b.effort for b, _ in benches if b.effort is not None), None)
    implementer = _seat_at_effort(
        implementer, _effective_effort(implementer, bench_effort=bench_effort, flag_effort=effort)
    )
    lead, lead_source = _lead_seat(policy, benches=benches, host_agent=host_agent)
    seats, panel, review_source, warnings = _review_seats(
        policy,
        tier=tier,
        default_count=default_count,
        reviewer_override=reviewer_override,
        host_agent=host_agent,
        jury_disabled=jury_disabled,
        jury_advisory=jury_advisory,
        benches=benches,
        jury_availability=jury_availability,
    )
    if team_profile is not None and team_profile not in policy.profiles:
        warnings.append(
            f"--team {team_profile!r} names no knobs.team.profiles entry; this run is "
            f"staffed from the configured policy instead. Known: "
            f"{', '.join(sorted(policy.profiles)) or 'none'}"
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
        "difficulty": difficulty,
        "team_profile": team_profile,
        "bench": [source for _bench, source in benches],
        "lead": lead.as_dict(source=lead_source),
        "implementer": implementer.as_dict(source=implementer_source),
        "effort": implementer.effort,
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
            # What the tier's policy asked for, before availability had its say. Without
            # it a fallback run's assignment is indistinguishable from a tier that never
            # configured a panel — which is the silent downgrade #1066 exists to refuse.
            "panel_configured": review_source == JURY_FALLBACK_SOURCE or panel == JURY_PANEL,
            "on_unavailable": jury_on_unavailable(policy.jury_on_unavailable),
            # `None` until something measured it: `keel plan --no-probe`-shaped callers
            # and every non-panel tier resolve without a probe, and an absent measurement
            # must not read as "we checked and it was fine".
            "availability": (
                dict(jury_availability) if isinstance(jury_availability, Mapping) else None
            ),
        },
        "fix": fix_record,
        "warnings": warnings,
    }


def require_distinct_vendors(setting: bool | None) -> bool:
    """The effective ``evidence_require_distinct_vendors``.

    **Opt-in** (#1065). ``None`` is *unset* and resolves to ``False``: the knob asserts
    that the required verdicts came from *independent* opinions, and that is a claim only
    the project can make. It is a property a cross-vendor panel provides, not one every
    high-tier review has to carry — a person with a single agent CLI installed must still
    be able to land a TIER-3 change without configuring anything. A project that wants
    the independence claim enforced says so, and the requirement then lives in a file a
    reviewer can read rather than in a default nobody chose.

    The setting is still tri-state at the config boundary (``None`` unset, ``True``,
    ``False``) so an explicit ``false`` stays distinguishable from silence, which is what
    ``config_hash`` and the wizard read.
    """
    return bool(setting)


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
    if policy.lead is not None:
        paths.append(("lead", policy.lead))
    paths.extend(_bench_seat_paths("by_difficulty", policy.by_difficulty))
    paths.extend(_bench_seat_paths("profiles", policy.profiles))
    return paths


def _bench_seat_paths(key: str, benches: Mapping[str, Bench]) -> list[tuple[str, Seat]]:
    """Every seat of every bench in one table, with its config path."""
    paths: list[tuple[str, Seat]] = []
    for name, bench in sorted(benches.items()):
        where = f"{key}.{name}"
        if bench.lead is not None:
            paths.append((f"{where}.lead", bench.lead))
        if bench.implement is not None:
            paths.append((f"{where}.implement", bench.implement))
        if isinstance(bench.review, tuple):
            paths.extend((f"{where}.review[{i}]", seat) for i, seat in enumerate(bench.review))
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
    if not isinstance(raw, Mapping):
        return []  # the schema already reported the wrong shape
    profiles = dict(profiles or {})
    known = {**{vendor: vendor for vendor in BUILTIN_DELEGATE_VENDORS}, **profiles}
    policy = parse_team(raw)
    errors: list[str] = []
    errors.extend(_tier_key_issues(raw, source=source))
    errors.extend(_band_key_issues(raw, source=source))
    for path, seat in _seat_paths(policy):
        where = f"{source}.{path}"
        vendor = _seat_vendor(seat, path=path, known=known, where=where, errors=errors)
        if seat.effort is None:
            continue
        errors.extend(_effort_issues(seat.effort, seat=seat, vendor=vendor, where=where))
    legacy = legacy_seats(implementer_agents or {}, provider_names=known)
    errors.extend(_bench_effort_issues(policy, source=source, known=known, legacy=legacy))
    errors.extend(_gate_issues(policy, source=source, legacy=legacy))
    errors.extend(_review_issues(policy, source=source))
    return errors


def _effort_issues(
    effort: str,
    *,
    seat: Seat,
    vendor: str | None,
    where: str,
    applies_to: str | None = None,
) -> list[str]:
    """The ways an ``effort`` is not honourable by ``seat``, worded the same everywhere.

    Extracted so a bench-level ``effort`` (:func:`_bench_effort_issues`) is judged by
    exactly the rules a seat-level one is, in exactly the same words. Two copies of these
    three sentences is how ``by_difficulty.hard.effort`` came to bypass the checks
    ``implement.default.effort`` has passed since #1014.

    ``applies_to`` names the seat the effort would land on, for the bench case where the
    effort and the seat it breaks are written in different places.

    These are #1014's three rules and only those, so the two callers cannot drift. The
    subagent rule is deliberately *not* here: it applies to a bench effort alone, and
    :func:`_bench_effort_issues` says why.
    """
    tail = "" if applies_to is None else f" (applied to the implementer at {applies_to})"
    if effort not in EFFORTS:
        return [f"{where}: unknown effort {effort!r}; valid: {', '.join(EFFORTS)}{tail}"]
    if vendor is None:
        return []  # an unresolvable provider is already reported; do not pile on
    if not supports_effort(vendor):
        return [
            f"{where}: provider {seat.provider!r} ({vendor}) has no spelling for "
            f"reasoning effort, so effort {effort!r} would be silently dropped "
            f"— drop the field, or name a provider that can honour it{tail}"
        ]
    if effort_needs_model(vendor) and seat.model is None:
        return [
            f"{where}: agy spells reasoning effort as a model suffix "
            f"(e.g. gemini-3.8-flash-high), so effort {effort!r} needs a "
            f"'model' beside it{tail}"
        ]
    return []


def _bench_effort_issues(
    policy: TeamPolicy,
    *,
    source: str,
    known: Mapping[str, str],
    legacy: Mapping[str, Seat],
) -> list[str]:
    """A bench ``effort`` has to be honourable by every implementer it could land on.

    A seat's own ``effort`` sits next to the provider it applies to, so an operator
    reading one line sees both halves. A bench's does not: ``by_difficulty.hard.effort``
    lands on whichever implementer resolves for that band, which may be written in
    another file's worth of config — action at a distance, and exactly the case where a
    silently-dropped effort is invisible. So it is checked against every seat it could
    reach, by the same rules and in the same words.

    Which seats those are follows the resolution order. A bench naming its own
    ``implement`` seat can only land there. One that does not falls through to the
    role/default/legacy implementers, and any of them is reachable. The host-agent
    fallback is deliberately not checked: the host is a per-run flag, and a policy that
    only validates against one operator's default is the kind of rule #1014 refused.

    A **subagent** target is an error here and not at seat level, and the difference is
    the point. ``fix: {provider: "subagent:x", effort: high}`` pairs the two on one line:
    the operator saw both halves and #1014 chose to tolerate it. A bench effort lands on
    a seat written somewhere else entirely, so the same pairing is one nobody ever read.
    """
    errors: list[str] = []
    for table, benches in (("by_difficulty", policy.by_difficulty), ("profiles", policy.profiles)):
        for name, bench in sorted(benches.items()):
            if bench.effort is None:
                continue
            where = f"{source}.{table}.{name}.effort"
            for seat_path, seat in _bench_effort_targets(
                policy, table=table, bench=bench, legacy=legacy
            ):
                # A seat naming its own effort never receives the bench's, so a bench
                # effort it could not honour is not a defect — the seat's wins.
                if seat.effort is not None:
                    continue
                if seat.kind == "subagent":
                    errors.append(
                        f"{where}: {seat.provider!r} is a host subagent, which has no "
                        f"reasoning-effort dial keel can set, so effort {bench.effort!r} "
                        "would be silently dropped — drop the field, or name a provider "
                        f"that can honour it (applied to the implementer at {seat_path})"
                    )
                    continue
                errors.extend(
                    _effort_issues(
                        bench.effort,
                        seat=seat,
                        vendor=known.get(seat.provider),
                        where=where,
                        applies_to=seat_path,
                    )
                )
    return errors


def _bench_effort_targets(
    policy: TeamPolicy,
    *,
    table: str,
    bench: Bench,
    legacy: Mapping[str, Seat],
) -> list[tuple[str, Seat]]:
    """Implementer seats a bench's ``effort`` could land on, with their config paths.

    *Reachable*, not merely *present*. One run resolves exactly one difficulty band and
    at most one ``--team`` profile, so two entries of the same table never apply
    together: a ``by_difficulty.hard`` effort can never meet ``by_difficulty.easy``'s
    implementer. Checking against siblings reported errors for combinations no run can
    produce, which is how a validator teaches people to ignore it.

    What is left is genuinely reachable. A bench naming its own ``implement`` seat can
    only land there. Otherwise the implementer comes from the *other* table (a ``--team``
    profile supplying the seat while the band supplies the effort, or the reverse), or
    from the role/default/legacy seats every run falls through to.
    """
    if bench.implement is not None:
        return [("this bench's own implement seat", bench.implement)]
    other = "profiles" if table == "by_difficulty" else "by_difficulty"
    targets = [
        (path, seat)
        for path, seat in _seat_paths(policy)
        if path.startswith("implement")
        or (path.startswith(f"{other}.") and path.endswith(".implement"))
    ]
    targets.extend(
        (f"knobs.implementer_agents.{role}", seat) for role, seat in sorted(legacy.items())
    )
    return targets


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


def _band_key_issues(raw: Mapping[str, Any], *, source: str) -> list[str]:
    """``by_difficulty`` keys must be difficulty bands the scorer can produce.

    A typo here is silent otherwise: ``medium:`` beside ``easy:``/``hard:`` never matches
    anything the scorer emits, so the bench an operator wrote is simply never staffed and
    the run looks like the table was ignored.
    """
    by_difficulty = raw.get("by_difficulty")
    if not isinstance(by_difficulty, Mapping):
        return []
    return [
        f"{source}.by_difficulty: {key!r} is not a difficulty band; valid: "
        f"{', '.join(DIFFICULTY_BANDS)}"
        for key in by_difficulty
        if key not in DIFFICULTY_BANDS
    ]


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
        if path.startswith("implement") or path.endswith(".implement")
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
    for key, benches in (("by_difficulty", policy.by_difficulty), ("profiles", policy.profiles)):
        entries.extend(
            (f"{key}.{name}.review", bench.review)
            for name, bench in sorted(benches.items())
            if bench.review is not None
        )
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
    elif policy.jury_mode == "advisory":
        # "The panel is the review" plus "the panel does not gate" is a tier with no
        # enforceable review at all: there are no host reviewer slots to fall back on, and
        # an advisory verdict is not required evidence. Refused here rather than
        # discovered as a merge that sailed through the tier the project marked strictest.
        panels = sorted(path for path, value in entries if value == JURY_PANEL)
        if panels:
            errors.append(
                f"{source}.jury.mode: 'advisory' leaves {', '.join(panels)} with no "
                "enforceable review — that tier has no host reviewers, so an advisory "
                "panel requires nothing. Use 'gating' for a jury panel, or name reviewer "
                "seats for that tier"
            )
    if policy.jury_on_unavailable is not None and policy.jury_on_unavailable not in (
        JURY_ON_UNAVAILABLE
    ):
        errors.append(
            f"{source}.jury.on_unavailable: unknown policy "
            f"{policy.jury_on_unavailable!r}; valid: {', '.join(JURY_ON_UNAVAILABLE)}"
        )
    return errors
