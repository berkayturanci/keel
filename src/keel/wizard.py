"""The pure question/answer planner behind every keel `--wizard` (#1018).

`ship.md` has promised an interactive picker "built from a best-effort tool/model
probe" since the adapter was written, but no core code existed for it, so every host
improvised the choices — different questions, different defaults, and options naming
providers that are not installed on the operator's machine.

This module is that picker's pure half. It takes two inputs and never performs I/O:

* the **provider probe report** — the exact document ``keel doctor --providers --json``
  prints (:func:`keel.providerprobe.build_report`), which is the single source of truth
  for what is usable here;
* the current **team policy** (:class:`keel.team.TeamPolicy`, ``knobs.team``), which
  supplies every default so a wizard run with no answers reproduces today's behaviour.

Out of it comes a :class:`Resolution`: the implementer/gate/reviewer seats, the jury
mode and the review-comments mode, rendered either as the literal ``keel ship`` flag
set (so the adapter passes flags on, exactly as ``ship.md``'s worked example does) or
as a ``knobs.team`` block ``keel init --wizard`` writes.

Two properties are load-bearing:

* **A provider the probe did not mark available is never offered, and can never be
  selected.** Every question is closed over :class:`Catalog`, and
  :meth:`Question.normalize` refuses a value outside its own choices — so an answer
  file, an injected seam and a typed answer are all held to the same list.
* **Deterministic.** No wall-clock, no randomness, no I/O, no default that depends on
  the order a dict happened to iterate in. Identical inputs give identical questions,
  identical defaults and an identical resolution, which is what lets a host replay a
  wizard run from its recorded answers.

The interactive half is :func:`run`, which is pure given its ``ask``/``notify`` seams;
the CLI supplies the real ``input``-based ones and the ``isatty`` guard.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import team
from .team import Seat

#: JSON-stable schema id of :meth:`Resolution.as_dict`.
SCHEMA_VERSION = "keel.wizard.v1"

#: Which wizard is being run. ``run`` resolves per-run flags for ``keel ship`` /
#: ``keel work-block``; ``config`` resolves the ``knobs.team`` block ``keel init``
#: writes. The questions differ because the *artefacts* differ: a run has one reviewer
#: bench (``--reviewers`` / ``--review-delegate`` are per-slot flags and the risk tier
#: is not known until s1 classifies the diff), while a config names one bench **per
#: tier**. Same planner, same catalogue, same defaults.
SCOPE_RUN = "run"
SCOPE_CONFIG = "config"
SCOPES = (SCOPE_RUN, SCOPE_CONFIG)

#: First question: take every default, or answer the rest. Mirrors the Quick-start vs
#: Customize fast path ``ship.md`` documents.
QUICK_START = "quick-start"
CUSTOMIZE = "customize"

#: :attr:`Candidate.source` of a machine-level ``~/.keel/providers.yaml`` entry. Reachable
#: per run, never nameable in a committed policy — see :func:`committable`.
REGISTRY_SOURCE = "registry"

#: Answer meaning "leave this unset" — no model override, no reasoning effort, no gate
#: review. Spelled once so a caller cannot confuse it with a provider named ``none``:
#: a provider by that name would still be offered under its own name, and the sentinel
#: is only ever compared against, never dispatched.
NONE = "none"

#: The jury is off. ``gating``/``advisory`` are :data:`keel.team.JURY_MODES`.
JURY_OFF = "off"
JURY_ANSWERS = (*team.JURY_MODES, JURY_OFF)

#: How review findings are posted, matching ``keel ship --review-comments``.
REVIEW_COMMENT_MODES = ("inline", "summary")

#: Reviewer seats a tier gets when nothing else says otherwise — one at tier 1, two at
#: tier 2, three at tier 3, clamped to the providers that are actually available.
DEFAULT_BENCH = {"1": 1, "2": 2, "3": 3}

#: Re-asks a single question gets before the planner stops arguing and takes the
#: default. A wizard that can loop forever on a stubborn ``ask`` seam is a hang, and a
#: hang is the one thing ``--wizard`` promises never to be.
MAX_ATTEMPTS = 3


def _effort_capable(vendor: str) -> bool:
    """Can ``vendor`` express a reasoning-effort request in its own spelling?

    Local import for the same reason :func:`keel.team.team_issues` uses one:
    :mod:`keel.delegate` imports :mod:`keel.config`, which imports :mod:`keel.team`,
    so naming it at module scope would drag the whole config graph into
    :mod:`keel.scaffold` — a module whose entire job is to run before a config exists.
    """
    from .delegate import supports_effort

    return supports_effort(vendor)


def _efforts() -> tuple[str, ...]:
    """keel's vendor-neutral effort vocabulary (see :func:`_effort_capable`)."""
    from .delegate import EFFORTS

    return tuple(EFFORTS)


@dataclass(frozen=True)
class Candidate:
    """One provider the probe reported as **available**, ready to be offered."""

    name: str
    vendor: str
    transport: str
    source: str
    #: Models the provider listed for itself (``agy models``, Ollama ``/api/tags``).
    models: tuple[str, ...] = ()
    #: Can it drive git/PR steps itself? Only a ``cli`` transport can.
    tools: bool = False

    @property
    def effort(self) -> bool:
        """True when a reasoning effort can be asked for on this seat."""
        return _effort_capable(self.vendor)

    def detail(self) -> str:
        """The one-line description shown beside this provider in a question."""
        parts = [self.transport, self.source]
        parts.append("tools" if self.tools else "no tools")
        if self.models:
            parts.append(f"{len(self.models)} model(s)")
        return " · ".join(parts)


@dataclass(frozen=True)
class Catalog:
    """Every available provider, in the probe's order. Empty means "nothing usable"."""

    candidates: tuple[Candidate, ...] = ()

    @classmethod
    def from_report(cls, report: Any) -> Catalog:
        """Build a catalogue from a ``keel doctor --providers`` document.

        Fail-soft on purpose: the report can arrive from a file an operator wrote or
        from another keel's ``--json`` output, so a malformed row is skipped rather
        than raising. A row that is not marked ``available`` is never a candidate —
        that single rule is what makes an unavailable provider unofferable.
        """
        rows = report.get("providers") if isinstance(report, Mapping) else None
        candidates = []
        for row in rows if isinstance(rows, list) else ():
            candidate = _candidate(row)
            if candidate is not None:
                candidates.append(candidate)
        return cls(tuple(candidates))

    def names(self) -> tuple[str, ...]:
        return tuple(candidate.name for candidate in self.candidates)

    def get(self, name: str | None) -> Candidate | None:
        for candidate in self.candidates:
            if candidate.name == name:
                return candidate
        return None

    def has(self, name: str | None) -> bool:
        return self.get(name) is not None

    def spread(self) -> tuple[Candidate, ...]:
        """Candidates re-ordered so distinct vendors come first.

        A default bench of two seats should be two *vendors* where the machine has
        two — one vendor reviewing twice is one opinion twice, which is the same rule
        :data:`keel.providers.REVIEW_VENDOR_MINIMUM` states for the jury.
        """
        seen: dict[str, Candidate] = {}
        rest: list[Candidate] = []
        for candidate in self.candidates:
            if candidate.vendor in seen:
                rest.append(candidate)
            else:
                seen[candidate.vendor] = candidate
        return (*seen.values(), *rest)


def _candidate(row: Any) -> Candidate | None:
    """One probe row -> a :class:`Candidate`, or ``None`` when it is not offerable."""
    if not isinstance(row, Mapping) or not row.get("available"):
        return None
    name = row.get("name")
    vendor = row.get("vendor")
    if not isinstance(name, str) or not name.strip():
        return None
    capabilities = row.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, Mapping) else {}
    models = row.get("models")
    return Candidate(
        name=name.strip(),
        vendor=vendor.strip() if isinstance(vendor, str) and vendor.strip() else name.strip(),
        transport=_word(row.get("transport"), "cli"),
        source=_word(row.get("source"), "builtin"),
        models=tuple(m.strip() for m in models if isinstance(m, str) and m.strip())
        if isinstance(models, list)
        else (),
        tools=bool(capabilities.get("tools")),
    )


def _word(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


@dataclass(frozen=True)
class Choice:
    """One offered option: its literal answer value and a one-line description."""

    value: str
    detail: str = ""


@dataclass(frozen=True)
class Question:
    """One question, closed over the values it will accept."""

    key: str
    prompt: str
    choices: tuple[Choice, ...]
    default: str
    help: str = ""
    #: Comma-separated answer (a reviewer bench); otherwise exactly one value.
    multi: bool = False
    max_values: int = 1

    def values(self) -> tuple[str, ...]:
        return tuple(choice.value for choice in self.choices)

    def ordered(self) -> tuple[Choice, ...]:
        """Choices with the default first — the shape ``ship.md`` asks every question for."""
        default = [choice for choice in self.choices if choice.value == self.default]
        return (*default, *(choice for choice in self.choices if choice.value != self.default))

    def text(self) -> str:
        """The full prompt block: the question, its help, then the options."""
        lines = [self.prompt if not self.help else f"{self.prompt} — {self.help}"]
        for choice in self.ordered():
            marker = " (default)" if choice.value == self.default else ""
            detail = f" — {choice.detail}" if choice.detail else ""
            lines.append(f"    {choice.value}{marker}{detail}")
        if self.multi:
            lines.append(f"    (comma-separate up to {self.max_values})")
        return "\n".join(lines)

    def normalize(self, raw: str | None) -> tuple[str | None, str | None]:
        """``(value, error)`` — exactly one is ``None``. A blank answer is the default."""
        text = (raw or "").strip()
        if not text:
            return self.default, None
        tokens = [t.strip() for t in text.split(",")] if self.multi else [text]
        tokens = [token for token in tokens if token]
        if not tokens:
            return self.default, None
        allowed = self.values()
        unknown = [token for token in tokens if token not in allowed]
        if unknown:
            return None, (
                f"{self.key}: {unknown[0]!r} is not on offer here; choose from {', '.join(allowed)}"
            )
        if len(tokens) > self.max_values:
            return None, f"{self.key}: at most {self.max_values} value(s), got {len(tokens)}"
        if len(tokens) > 1 and team.JURY_PANEL in tokens:
            return None, (
                f"{self.key}: {team.JURY_PANEL!r} is the whole panel and cannot be "
                "combined with named seats"
            )
        return ",".join(tokens), None


@dataclass(frozen=True)
class State:
    """A wizard mid-flight: what is on offer, what the defaults are, what was answered."""

    catalog: Catalog
    policy: team.TeamPolicy = field(default_factory=team.TeamPolicy)
    scope: str = SCOPE_RUN
    #: Defaults carried in from the parsed flags, so the wizard starts where the
    #: command line already is.
    review_comments: str = "inline"
    jury: str = JURY_OFF
    delegate: str | None = None
    answers: Mapping[str, str] = field(default_factory=dict)

    def with_answer(self, key: str, value: str) -> State:
        return State(
            catalog=self.catalog,
            policy=self.policy,
            scope=self.scope,
            review_comments=self.review_comments,
            jury=self.jury,
            delegate=self.delegate,
            answers={**self.answers, key: value},
        )

    def questions(self) -> tuple[Question, ...]:
        """Every question this scope asks, given the answers so far."""
        return _walk(self)[0]

    def next_question(self) -> Question | None:
        """The first question still unanswered, or ``None`` when the wizard is done."""
        for question in self.questions():
            if question.key not in self.answers:
                return question
        return None

    def resolve(self) -> Resolution:
        """The resolved seats/flags for the answers so far (unanswered = default)."""
        return _walk(self)[1]


def committable(catalog: Catalog) -> Catalog:
    """Only the providers a **committed** ``knobs.team`` may name.

    ``keel validate`` resolves a policy against the built-in vendors and the project's own
    ``knobs.delegate_profiles`` — never the machine-level ``~/.keel/providers.yaml``,
    because a policy that validates only on its author's laptop is not a policy
    (``docs/keel/configuration.md#team``). A registry provider stays reachable *per run*
    through ``--delegate``, so the run wizard still offers it; the config wizard must not,
    or the file it writes would fail the very next ``keel validate``.
    """
    return Catalog(tuple(c for c in catalog.candidates if c.source != REGISTRY_SOURCE))


def start(
    catalog: Catalog,
    *,
    policy: team.TeamPolicy | None = None,
    scope: str = SCOPE_RUN,
    review_comments: str = "inline",
    jury: str = JURY_OFF,
    delegate: str | None = None,
) -> State:
    """A fresh :class:`State`. ``jury``/``review_comments``/``delegate`` are the parsed flags."""
    if scope not in SCOPES:
        raise ValueError(f"unknown wizard scope {scope!r}; valid: {', '.join(SCOPES)}")
    return State(
        catalog=committable(catalog) if scope == SCOPE_CONFIG else catalog,
        policy=policy if policy is not None else team.TeamPolicy(),
        scope=scope,
        review_comments=review_comments if review_comments in REVIEW_COMMENT_MODES else "inline",
        jury=jury if jury in JURY_ANSWERS else JURY_OFF,
        delegate=delegate,
    )


@dataclass(frozen=True)
class Resolution:
    """What the wizard decided: seats, panel, and the flags that express them."""

    scope: str
    implement: Seat
    gate: Seat | None = None
    #: ``run`` scope: the bench for this run, or :data:`keel.team.JURY_PANEL`.
    review: tuple[Seat, ...] | str = ()
    #: ``config`` scope: tier -> bench (or the jury panel).
    review_by_tier: Mapping[str, tuple[Seat, ...] | str] = field(default_factory=dict)
    jury: str = JURY_OFF
    review_comments: str = "inline"
    quick_start: bool = True

    def flags(self) -> tuple[str, ...]:
        """The literal ``keel ship`` / ``keel work-block`` flag set, in a stable order."""
        flags = ["--delegate", seat_token(self.implement)]
        if isinstance(self.review, tuple) and self.review:
            flags += ["--reviewers", str(len(self.review))]
            for seat in self.review:
                flags += ["--review-delegate", seat_token(seat)]
        flags += ["--review-comments", self.review_comments]
        flags += {
            "gating": ["--jury"],
            "advisory": ["--jury-advisory"],
            JURY_OFF: ["--no-jury"],
        }[self.jury]
        return tuple(flags)

    def team_block(self) -> dict[str, Any]:
        """The ``knobs.team`` block, in the shape :mod:`keel.team` parses (#1014)."""
        block: dict[str, Any] = {"implement": {"default": _seat_block(self.implement)}}
        if self.gate is not None:
            block["gate"] = {**_seat_block(self.gate), "distinct_from": team.IMPLEMENTER}
        by_tier = {
            tier: value if isinstance(value, str) else [_seat_block(s) for s in value]
            for tier, value in sorted(self.review_by_tier.items())
        }
        if by_tier:
            block["review"] = {"by_tier": by_tier}
        if self.jury != JURY_OFF:
            block["jury"] = {"mode": self.jury, "min_vendors": team.DEFAULT_MIN_VENDORS}
        block["fix"] = {"provider": team.IMPLEMENTER}
        return block

    def as_dict(self) -> dict[str, Any]:
        """JSON-stable echo of everything the wizard resolved."""
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": self.scope,
            "quick_start": self.quick_start,
            "flags": list(self.flags()),
            "implement": self.implement.as_dict(),
            "gate": None if self.gate is None else self.gate.as_dict(),
            "review": (
                self.review
                if isinstance(self.review, str)
                else [seat.as_dict() for seat in self.review]
            ),
            "review_by_tier": {
                tier: value if isinstance(value, str) else [s.as_dict() for s in value]
                for tier, value in sorted(self.review_by_tier.items())
            },
            "jury": self.jury,
            "review_comments": self.review_comments,
            "team": self.team_block(),
        }


def seat_token(seat: Seat) -> str:
    """``provider`` or ``provider:model`` — the spelling ``--delegate`` takes."""
    return f"{seat.provider}:{seat.model}" if seat.model else seat.provider


def _seat_block(seat: Seat) -> dict[str, Any]:
    block: dict[str, Any] = {"provider": seat.provider}
    if seat.model:
        block["model"] = seat.model
    if seat.effort:
        block["effort"] = seat.effort
    return block


def _default_implement(state: State) -> Seat:
    """The implementer to start from: the flag, then the policy, then what is available.

    A default that names a provider this machine cannot reach is *not* a default — it
    would put an unusable option in front of the operator as the recommended one. It
    degrades to the first available candidate instead.
    """
    if state.delegate:
        seat = team.seat_from_token(state.delegate)
        if state.catalog.has(seat.provider):
            return seat
    if state.policy.implement is not None and state.catalog.has(state.policy.implement.provider):
        return state.policy.implement
    return Seat(provider=state.catalog.names()[0])


def _default_gate(state: State, implementer: str) -> Seat | None:
    """The gate seat, dropped when it is unavailable or would be the implementer."""
    gate = state.policy.gate
    if gate is None or not state.catalog.has(gate.provider) or gate.provider == implementer:
        return None
    return gate


def _default_bench(state: State, tier: str, implementer: str) -> tuple[Seat, ...] | str:
    """The reviewer bench for ``tier``: the policy's, filtered to what is available."""
    configured = state.policy.review_by_tier.get(tier)
    if configured is None:
        configured = state.policy.review
    if configured == team.JURY_PANEL:
        return team.JURY_PANEL
    if isinstance(configured, tuple):
        seats = tuple(seat for seat in configured if state.catalog.has(seat.provider))
        if seats:
            return seats
    count = min(DEFAULT_BENCH[tier], len(state.catalog.candidates))
    spread = state.catalog.spread()
    preferred = [c for c in spread if c.name != implementer] + [
        c for c in spread if c.name == implementer
    ]
    return tuple(Seat(provider=candidate.name) for candidate in preferred[:count])


def _bench_answer(bench: tuple[Seat, ...] | str) -> str:
    if isinstance(bench, str):
        return bench
    return ",".join(seat.provider for seat in bench)


def _seats_from(value: str, models: Mapping[str, str | None]) -> tuple[Seat, ...] | str:
    if value == team.JURY_PANEL:
        return team.JURY_PANEL
    return tuple(Seat(provider=name, model=models.get(name)) for name in value.split(","))


def _policy_models(policy: team.TeamPolicy) -> dict[str, str | None]:
    """Provider -> the model the policy seats it on, across every reviewer bench.

    A reviewer question answers with provider *names* only, so a bench the operator
    keeps unchanged would otherwise lose the model its policy pinned. First seat wins;
    the walk order is sorted, so the answer does not depend on dict iteration order.
    """
    models: dict[str, str | None] = {}
    for _, seat in _policy_seats(policy):
        if seat.model and seat.provider not in models:
            models[seat.provider] = seat.model
    return models


def _provider_choices(catalog: Catalog, *, skip: str | None = None) -> tuple[Choice, ...]:
    return tuple(
        Choice(candidate.name, candidate.detail())
        for candidate in catalog.candidates
        if candidate.name != skip
    )


class WizardError(Exception):
    """The wizard cannot run at all: the probe offered nothing to choose between."""


def _walk(state: State) -> tuple[tuple[Question, ...], Resolution]:
    """The one traversal: it emits the questions *and* the resolution they resolve to.

    Every value is read through the same ``ask`` closure, so a question's default and
    the value used when it is unanswered are the same expression — the two can never
    drift, which is what makes a quick-start run and an all-defaults customized run
    identical by construction.
    """
    if not state.catalog.candidates:
        raise WizardError(
            "no provider is available on this machine — run `keel doctor --providers` "
            "to see why; the wizard has nothing it could offer"
        )
    questions: list[Question] = []
    asking = True

    def ask(
        key: str,
        prompt: str,
        choices: tuple[Choice, ...],
        default: str,
        *,
        help: str = "",
        multi: bool = False,
        max_values: int = 1,
    ) -> str:
        question = Question(
            key=key,
            prompt=prompt,
            choices=choices,
            default=default,
            help=help,
            multi=multi,
            max_values=max_values,
        )
        if asking:
            questions.append(question)
            return state.answers.get(key, default)
        return default

    mode = ask(
        "mode",
        "Start style",
        (
            Choice(QUICK_START, "take every default below and ask nothing else"),
            Choice(CUSTOMIZE, "answer each question"),
        ),
        QUICK_START,
        help="quick-start resolves every option to its default",
    )
    # Quick-start still *computes* every value below — it just stops asking. The
    # resolution is therefore the same object either way, which is the property that
    # lets `--wizard` promise it "cannot produce a config the grammar could not".
    asking = mode == CUSTOMIZE

    implement_default = _default_implement(state)
    provider = ask(
        "implement.provider",
        "Implementer provider",
        _provider_choices(state.catalog),
        implement_default.provider,
        help="who writes the change at s4",
    )
    candidate = state.catalog.get(provider)
    if candidate is None:
        # Only reachable when a caller seats an answer directly on `State` instead of
        # going through `normalize`/`apply_answers`. The offer stands: an answer that
        # is not on it resolves to the default rather than to an unusable provider.
        provider = implement_default.provider
        candidate = state.catalog.get(provider)
    model = implement_default.model if provider == implement_default.provider else None
    if candidate.models:
        model_answer = ask(
            "implement.model",
            "Implementer model",
            (
                Choice(NONE, f"the provider's own default for {provider}"),
                *(Choice(name) for name in candidate.models),
            ),
            model if model in candidate.models else NONE,
            help=f"models {provider} lists for itself",
        )
        model = None if model_answer == NONE else model_answer
    effort = implement_default.effort if provider == implement_default.provider else None
    # A vendor that spells effort as a model suffix has nowhere to put one without a
    # model, and `keel validate` says so (`team.effort_needs_model`). Asking anyway
    # would let the wizard write a config keel then refuses to load — the one thing a
    # scaffolder must never do.
    if candidate.effort and not (team.effort_needs_model(candidate.vendor) and model is None):
        effort_answer = ask(
            "implement.effort",
            "Implementer reasoning effort",
            (Choice(NONE, "no effort request"), *(Choice(name) for name in _efforts())),
            effort if effort in _efforts() else NONE,
            help=f"{provider} can express this in its own spelling",
        )
        effort = None if effort_answer == NONE else effort_answer
    elif team.effort_needs_model(candidate.vendor):
        # A policy default that carried an effort loses it along with the model it
        # was a suffix of; keeping it would write exactly the pair keel rejects.
        effort = None
    implement = Seat(provider=provider, model=model, effort=effort)

    gate_default = _default_gate(state, provider)
    gate_answer = ask(
        "gate.provider",
        "Gate reviewer",
        (
            Choice(NONE, "no mandatory second opinion"),
            *_provider_choices(state.catalog, skip=provider),
        ),
        gate_default.provider if gate_default is not None else NONE,
        help="one mandatory second opinion, never the seat that wrote the change",
    )
    gate = (
        None if gate_answer == NONE else Seat(provider=gate_answer, distinct_from=team.IMPLEMENTER)
    )

    jury = ask(
        "jury",
        "Cross-vendor jury",
        (
            Choice("gating", "a blocking verdict blocks the merge"),
            Choice("advisory", "the panel reports and never gates"),
            Choice(JURY_OFF, "no jury on this run"),
        ),
        state.jury if state.jury in JURY_ANSWERS else JURY_OFF,
        help="the cross-vendor panel",
    )

    bench_choices = _provider_choices(state.catalog)
    if jury != JURY_OFF:
        bench_choices = (
            Choice(team.JURY_PANEL, "the cross-vendor panel *is* the review"),
            *bench_choices,
        )
    max_seats = min(team.MAX_REVIEW_SEATS, len(state.catalog.candidates))
    seat_models = _policy_models(state.policy)
    review: tuple[Seat, ...] | str = ()
    review_by_tier: dict[str, tuple[Seat, ...] | str] = {}
    if state.scope == SCOPE_RUN:
        review = _seats_from(
            ask(
                "review",
                "Reviewers for this run",
                bench_choices,
                _bench_answer(_default_bench(state, "2", provider)),
                help="one seat per reviewer slot (A, B, C)",
                multi=True,
                max_values=max_seats,
            ),
            seat_models,
        )
    else:
        for tier in team.TIERS:
            review_by_tier[tier] = _seats_from(
                ask(
                    f"review.{tier}",
                    f"Reviewers for tier {tier}",
                    bench_choices,
                    _bench_answer(_default_bench(state, tier, provider)),
                    help=f"knobs.team.review.by_tier.{quoted(tier)}",
                    multi=True,
                    max_values=max_seats,
                ),
                seat_models,
            )

    review_comments = ask(
        "review_comments",
        "Review comment posting",
        (
            Choice("inline", "one comment per finding, on the diff"),
            Choice("summary", "one rolled-up review comment"),
        ),
        state.review_comments,
        help="how findings reach the pull request",
    )
    resolution = Resolution(
        scope=state.scope,
        implement=implement,
        gate=gate,
        review=review,
        review_by_tier=review_by_tier,
        jury=jury,
        review_comments=review_comments,
        quick_start=mode == QUICK_START,
    )
    return tuple(questions), resolution


def quoted(tier: str) -> str:
    """A tier key as ``knobs.team`` spells it — quoted, because YAML reads bare ``1:`` as int."""
    return f'"{tier}"'


def apply_answers(state: State, answers: Mapping[str, str]) -> tuple[State, tuple[str, ...]]:
    """Feed recorded answers in, without prompting. Returns the state and any errors.

    This is the non-interactive path: ``--wizard-answer key=value`` on the command
    line, or a replayed run. An answer for a question this scope never asks — or one
    naming a provider the probe did not offer — is reported and *not* applied, which
    is the same wall the interactive path puts up.
    """
    errors: list[str] = []
    remaining = dict(answers)
    while remaining:
        question = state.next_question()
        if question is None:
            break
        if question.key not in remaining:
            state = state.with_answer(question.key, question.default)
            continue
        value, error = question.normalize(remaining.pop(question.key))
        if error is not None:
            errors.append(error)
            value = question.default
        state = state.with_answer(question.key, value)
    for key in sorted(remaining):
        errors.append(f"{key}: not a question this wizard asks")
    return state, tuple(errors)


def run(
    state: State,
    ask: Callable[[str, str], str],
    notify: Callable[[str], None],
) -> State:
    """Ask every remaining question through ``ask``. Pure given its seams.

    ``ask(prompt, default)`` returns the operator's raw answer (blank ⇒ the default),
    exactly the seam :func:`keel.scaffold.wizard` already uses. An answer outside the
    question's choices is refused through ``notify`` and asked again, at most
    :data:`MAX_ATTEMPTS` times — after that the default stands, because a wizard that
    argues forever is the hang ``--wizard`` promises never to be.
    """
    question = state.next_question()
    while question is not None:
        value = question.default
        for _ in range(MAX_ATTEMPTS):
            candidate, error = question.normalize(ask(question.text(), question.default))
            if error is None:
                value = candidate
                break
            notify(error)
        else:
            notify(f"{question.key}: keeping the default {question.default!r}")
        state = state.with_answer(question.key, value)
        question = state.next_question()
    return state


def render(resolution: Resolution) -> str:
    """The operator-facing echo: the resolved flag set, then the seats behind it."""
    lines = [f"  flags : {' '.join(resolution.flags())}"]
    seats = [f"implement={seat_token(resolution.implement)}"]
    if resolution.implement.effort:
        seats.append(f"effort={resolution.implement.effort}")
    if resolution.gate is not None:
        seats.append(f"gate={seat_token(resolution.gate)} (distinct from the implementer)")
    if isinstance(resolution.review, str):
        seats.append(f"review={resolution.review}")
    elif resolution.review:
        seats.append("review=" + ",".join(seat_token(s) for s in resolution.review))
    for tier, bench in sorted(resolution.review_by_tier.items()):
        rendered = bench if isinstance(bench, str) else ",".join(seat_token(s) for s in bench)
        seats.append(f"review[{tier}]={rendered}")
    lines.append(f"  seats : {' · '.join(seats)}")
    return "\n".join(lines)


def parse_answer_args(values: Iterable[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """``KEY=VALUE`` strings -> an answer mapping plus the ones that were not pairs."""
    answers: dict[str, str] = {}
    errors: list[str] = []
    for raw in values:
        for item in str(raw).split(";"):
            text = item.strip()
            if not text:
                continue
            key, sep, value = text.partition("=")
            if not sep or not key.strip():
                errors.append(f"--wizard-answer {text!r} is not KEY=VALUE")
                continue
            answers[key.strip()] = value.strip()
    return answers, tuple(errors)


def unavailable(policy: team.TeamPolicy, catalog: Catalog) -> tuple[str, ...]:
    """Providers the policy names that this machine cannot reach, in a stable order.

    Advisory, never fatal: a shared ``knobs.team`` legitimately names seats other
    machines fill. Saying so once at the top of a wizard run is how an operator learns
    why a configured default is not the offered one.
    """
    names: list[str] = []
    for _, seat in _policy_seats(policy):
        if seat.kind != "provider" or catalog.has(seat.provider) or seat.provider in names:
            continue
        names.append(seat.provider)
    return tuple(names)


def _policy_seats(policy: team.TeamPolicy) -> Sequence[tuple[str, Seat]]:
    seats: list[tuple[str, Seat]] = []
    if policy.implement is not None:
        seats.append(("implement.default", policy.implement))
    seats.extend(sorted(policy.implement_by_role.items()))
    if policy.gate is not None:
        seats.append(("gate", policy.gate))
    benches: list[tuple[str, tuple[Seat, ...] | str]] = sorted(policy.review_by_tier.items())
    if policy.review is not None:
        benches.append(("review.default", policy.review))
    for path, bench in benches:
        if isinstance(bench, tuple):
            seats.extend((path, seat) for seat in bench)
    return seats
