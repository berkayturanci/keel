"""Can the cross-vendor panel actually be staffed here? (#1066)

On a tier whose ``knobs.team.review.by_tier`` names ``jury``, s7 dispatches the panel and
its ballots *are* the review — #1014 round 3 deliberately made it so no operator flag can
take the panel back off. That is right while the panel can run. When it cannot — an agent
CLI is not installed, is unauthenticated, or the account is out of quota — the tier has no
way forward at all: the only review it has is one this machine cannot convene.

This module is the pure half of the answer. The question it has to answer is narrower than
"are some agent CLIs installed": s7 does not convene a panel out of keel's delegate
inventory, it runs the **``jury`` binary** (``src/keel/adapters/commands/ship.md``), and
that binary holds its own configured panel. So the probe asks the runner first —
``jury --doctor --json``, ai-jury's own readiness document, which reports both that the
binary is there and which of *its* agents are usable — and only falls back to
:func:`keel.providerprobe.collect` (what ``keel doctor --providers`` prints) for a runner
whose document named no agents. A machine with ``claude`` and ``codex`` on ``PATH`` and no
``jury`` is **not** staffable, however healthy keel's own inventory looks: the panel s7
would dispatch cannot run.

That document is also the binary's *identity*, and no document is no identity (#1068):
a ``jury`` on ``PATH`` that exits 0 without one has not established that it is ai-jury, so
it is unusable and keel's inventory cannot make it staffable. The proxy stands in for a
panel ai-jury declined to enumerate, never for a panel runner nobody established is there.

Two answers to one question is what this ordering avoids. keel's delegate inventory is a
proxy for the panel, ai-jury's is the panel; when the panel can speak for itself it is the
authority, and the record says which inventory the verdict was read from.

Three things the design holds to, all of them from the issue:

* **Availability is measured, never asserted.** There is no flag that says "the panel is
  fine". What may not take the panel off is an operator's *preference*; availability is a
  fact about the world, and it is allowed to change the outcome precisely because it is
  recorded.
* **The policy is a configured allowance, not an automatic behaviour.**
  ``knobs.team.jury.on_unavailable`` is ``fallback`` (the sensible default for a solo
  project) or ``block`` (today's strictness, for a project whose product claim *is*
  cross-vendor review).
* **Never a silent downgrade.** ai-jury #682 exists because a panel that quietly collapsed
  to one vendor still reported success. So :meth:`Availability.as_dict` carries which
  seats were unavailable and why, all the way into the published assignment, the review
  contract, the run ledger and the closure comment. The fallback changes *who sat*, never
  *how many*: the seat count and the evidence requirement are the tier's, not the panel's.

Pure and deterministic: the report goes in, the verdict comes out, and nothing here
touches PATH, a subprocess, or the clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .team import (
    DEFAULT_MIN_VENDORS,
    JURY_ON_UNAVAILABLE_DEFAULT,
    jury_on_unavailable,
)
from .team import JURY_RUNNER_COMMAND as JURY_RUNNER_COMMAND
from .team import JuryUnavailableError as JuryUnavailableError
from .team import refusal_message as refusal_message

#: The module's public surface, in definition order (#1070). It is declared because the
#: ``X as X`` re-exports above are read from *other* modules — a use CodeQL's
#: ``py/unused-import`` cannot see, since it counts same-module uses only. A name listed
#: in ``__all__`` is used by definition, so the declaration answers the scanner with the
#: language's own statement of intent rather than with a dismissal. Being a real
#: declaration it has to be the *whole* surface, not the re-exports alone;
#: ``tests/test_reexport_surface.py`` holds it to that in both directions.
__all__ = [
    "JURY_RUNNER_COMMAND",
    "JuryUnavailableError",
    "refusal_message",
    "JURY_RUNNER_VENDOR",
    "INVENTORY_RUNNER",
    "INVENTORY_PROVIDERS",
    "DECISION_AVAILABLE",
    "DECISION_FALLBACK",
    "DECISION_BLOCK",
    "Seat",
    "Runner",
    "RUNNER_UNPROBED",
    "Availability",
    "assess",
    "SOURCE_PROBE",
    "SOURCE_PULL_REQUEST",
    "SOURCE_RUN_LEDGER",
    "SOURCE_CLOSURE_COMMENT",
    "panel_sat",
    "recorded",
    "shipped",
    "is_ship_run_for_head",
    "states_panel",
    "pin",
    "is_pinnable_head",
]

#: Re-exported from :mod:`keel.team`, which owns them because :func:`_review_seats` — the
#: one place a bench is resolved, and so the one place a blocked panel can refuse the work
#: it is actually about to review — cannot import this module: the import runs the other
#: way. They keep their names here because this is the module the feature is named for and
#: where a reader looks for them; ``keel.juryavail.JuryUnavailableError`` and
#: ``keel.team.JuryUnavailableError`` are one class, not two.
#:
#: ``JURY_RUNNER_COMMAND`` is the binary a jury-panel tier's s7 actually dispatches. Not a
#: delegate keel runs itself: keel does not depend on ai-jury, and every path through this
#: module stays total when it is absent — absent simply means the panel cannot sit here.

#: The vendor the runner seat is attributed to, so a reader of ``unavailable`` can tell the
#: missing *panel* apart from a missing *panelist*.
JURY_RUNNER_VENDOR = "ai-jury"

#: Where a verdict's vendor inventory was read from — recorded, because the two sources do
#: not have to agree and a reader must not have to guess which one spoke.
INVENTORY_RUNNER = f"{JURY_RUNNER_COMMAND} --doctor"
INVENTORY_PROVIDERS = "keel doctor --providers"

#: The panel is staffable — nothing changes, the ballots are the review.
DECISION_AVAILABLE = "available"
#: The panel is not staffable and the policy allows a host bench in its place.
DECISION_FALLBACK = "fallback"
#: The panel is not staffable and the policy refuses the run.
DECISION_BLOCK = "block"


@dataclass(frozen=True)
class Seat:
    """One provider the panel could have used, and why it cannot."""

    provider: str
    vendor: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "vendor": self.vendor, "reason": self.reason}


@dataclass(frozen=True)
class Runner:
    """The ``jury`` CLI itself: can s7 dispatch it here, and what panel does it hold?

    Produced by :func:`keel.providerprobe.probe_jury_runner` — the thin-I/O half — and
    consumed here. ``doctor`` is ai-jury's own ``jury --doctor --json`` document, which is
    both the panel it holds and the way the binary identifies itself: without a readable
    one the probe reports ``usable=False`` (#1068). A document that identifies ai-jury but
    names no ``agents`` still means *usable*, and the verdict then reads keel's own
    delegate inventory for the vendor count instead.
    """

    usable: bool
    reason: str
    doctor: Mapping[str, Any] | None = None

    @property
    def panel_rows(self) -> tuple[Any, ...] | None:
        """The agents ai-jury reports for its own panel, or ``None`` when it reported none."""
        return _rows(self.doctor, "agents")

    def as_dict(self) -> dict[str, Any]:
        return {"command": JURY_RUNNER_COMMAND, "usable": self.usable, "reason": self.reason}


#: What an unprobed runner reads as. Fail-closed on purpose: the whole point of #1066
#: round 2 is that a panel nobody established could run must not be reported staffable, so
#: "we did not ask" and "we asked and it is fine" cannot share an answer.
RUNNER_UNPROBED = Runner(False, f"the {JURY_RUNNER_COMMAND} runner was not probed")


@dataclass(frozen=True)
class Availability:
    """The probe's verdict on this tier's panel, ready to publish."""

    #: Distinct vendors the panel needs before it can be a *cross-vendor* panel. This is
    #: ``team.jury.min_vendors``, the same floor the verdict is later held to.
    required_vendors: int
    #: Vendors the probe found usable here, in the probe's own (deterministic) order.
    available_vendors: tuple[str, ...]
    #: Every seat the panel could not use, with the reason it reported. The ``jury`` runner
    #: itself is one of them when it is the thing that is missing.
    unavailable: tuple[Seat, ...]
    #: ``fallback`` | ``block`` — the configured allowance, already defaulted.
    policy: str = JURY_ON_UNAVAILABLE_DEFAULT
    #: The ``jury`` binary s7 dispatches. Unprobed reads as unusable, never as fine.
    runner: Runner = RUNNER_UNPROBED
    #: Which inventory the vendor counts came from — the runner's own, or keel's.
    inventory: str = INVENTORY_PROVIDERS

    @property
    def staffable(self) -> bool:
        """Can this machine convene a panel spanning ``required_vendors`` vendors?

        Both halves, because s7 needs both: the runner that dispatches the panel, and
        enough distinct vendors for it to *be* a cross-vendor panel. Agent CLIs on ``PATH``
        with no ``jury`` to convene them is not a panel, it is an inventory.
        """
        return self.runner.usable and len(self.available_vendors) >= self.required_vendors

    @property
    def decision(self) -> str:
        """:data:`DECISION_AVAILABLE`, or the policy when the panel cannot be staffed."""
        return DECISION_AVAILABLE if self.staffable else self.policy

    @property
    def reason(self) -> str:
        """One sentence a reader can act on, naming the seats that were unavailable."""
        counted = (
            f"{len(self.available_vendors)} vendor(s) available "
            f"({', '.join(self.available_vendors) or 'none'}), {self.required_vendors} "
            f"required (per {self.inventory})"
        )
        if self.staffable:
            return f"jury panel staffable: {counted}, dispatched by {self.runner.reason}"
        listed = ", ".join(f"{seat.provider} ({seat.reason})" for seat in self.unavailable)
        # Named apart from the vendor shortfall, because the numbers alone mislead: a
        # machine with two agent CLIs and no `jury` reads as "2 of 2 available" while the
        # panel s7 would dispatch cannot run at all.
        why = (
            f"the {JURY_RUNNER_COMMAND} runner s7 dispatches is not usable here; {counted}"
            if not self.runner.usable
            else counted
        )
        return f"jury panel not staffable: {why}; unavailable: {listed or 'none probed'}"

    def as_dict(self) -> dict[str, Any]:
        """JSON-stable record — the shape the assignment and the contract publish."""
        return {
            "probed": True,
            "staffable": self.staffable,
            "decision": self.decision,
            "on_unavailable": self.policy,
            "required_vendors": self.required_vendors,
            "available_vendors": list(self.available_vendors),
            "unavailable": [seat.as_dict() for seat in self.unavailable],
            "runner": self.runner.as_dict(),
            "inventory": self.inventory,
            # This record was measured *here*. A verification surface republishing what the
            # ship measured says so instead (:data:`SOURCE_PULL_REQUEST` /
            # :data:`SOURCE_RUN_LEDGER`), because "we checked" and "we were told" are not
            # the same claim.
            "source": SOURCE_PROBE,
            "reason": self.reason,
        }


def assess(
    report: Mapping[str, Any] | None,
    *,
    runner: Runner = RUNNER_UNPROBED,
    min_vendors: int = DEFAULT_MIN_VENDORS,
    policy: str | None = None,
) -> Availability:
    """Read the panel runner — and, failing that, keel's provider report — into a verdict.

    ``runner`` is :func:`keel.providerprobe.probe_jury_runner`'s answer about the ``jury``
    binary s7 actually dispatches. It gates the whole verdict: no runner, no panel, whatever
    keel's delegate inventory says. It defaults to :data:`RUNNER_UNPROBED` — unusable — so a
    caller that forgets to probe it gets the conservative answer rather than a staffable
    panel nobody checked.

    The vendor inventory is the runner's own when ``jury --doctor --json`` reported one:
    ai-jury is the authority on the panel it would convene, and keel's delegate list is only
    a proxy for it. ``report`` — :func:`keel.providerprobe.build_report`'s document — is the
    fallback for a runner that identified itself and named no agents — never for one that
    produced no document at all, which is not established to be ai-jury and reaches here
    as ``usable=False``. Either way a panel spans *vendors*, so two rows
    that shell out to the same CLI are one opinion (the rule
    :func:`keel.providers.distinct_vendors` states), and a hosted API with its key set is as
    real a panelist as a CLI on ``PATH``.

    Total by construction. A missing or malformed inventory yields *no* available vendors and
    *no* named seats, which reads as "not staffable" — the conservative answer, and the
    one that then goes through the project's own configured allowance rather than being
    quietly decided here.
    """
    rows, inventory = _inventory(runner, report)
    available: list[str] = []
    unavailable: list[Seat] = []
    if not runner.usable:
        # First in the list, because it is the first thing to fix: a reader who sees
        # `codex not found on PATH` and installs codex has not made the panel runnable.
        unavailable.append(Seat(JURY_RUNNER_COMMAND, JURY_RUNNER_VENDOR, runner.reason))
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = _text(row.get("name")) or _text(row.get("vendor")) or "(unnamed provider)"
        vendor = _text(row.get("vendor")) or name
        if row.get("available"):
            if vendor not in available:
                available.append(vendor)
        else:
            unavailable.append(Seat(name, vendor, _text(row.get("reason")) or "no reason reported"))
    return Availability(
        required_vendors=max(1, min_vendors),
        available_vendors=tuple(available),
        unavailable=tuple(unavailable),
        policy=jury_on_unavailable(policy),
        runner=runner,
        inventory=inventory,
    )


def _inventory(runner: Runner, report: Mapping[str, Any] | None) -> tuple[tuple[Any, ...], str]:
    """``(rows, where they came from)`` — the runner's own panel, or keel's providers."""
    rows = runner.panel_rows
    if rows is not None:
        return rows, INVENTORY_RUNNER
    return _rows(report, "providers") or (), INVENTORY_PROVIDERS


def _rows(document: Mapping[str, Any] | None, key: str) -> tuple[Any, ...] | None:
    """``document[key]`` as a tuple of rows, or ``None`` when it is not a list of them."""
    rows = document.get(key) if isinstance(document, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    return tuple(rows)


#: Where a published availability record came from. A *probe* measured this machine; the
#: other two are a verification surface reading what the ship measured, which is not the
#: same claim and must not be published as if it were.
SOURCE_PROBE = "probe"
SOURCE_PULL_REQUEST = "pull-request"
SOURCE_RUN_LEDGER = "run-ledger"
#: The run's own record, read back off the closure comment it posted — the same statement
#: as :data:`SOURCE_RUN_LEDGER`, from the copy that travels with the pull request (#1068).
SOURCE_CLOSURE_COMMENT = "closure-comment"


def panel_sat(
    *, min_vendors: int = DEFAULT_MIN_VENDORS, policy: str | None = None
) -> dict[str, Any]:
    """The panel demonstrably sat: a head-pinned jury verdict is on the pull request (#1066).

    A verification surface must not answer "was the panel available" by asking *its own*
    machine. ``keel evidence-verify`` and ``keel merge`` run wherever CI puts them, and a
    change juried on a workstation and checked on a bare runner would otherwise have its
    required evidence quietly rewritten — the panel item dropped, three host verdicts
    demanded that nobody was ever asked to post. The ballots are already on the pull
    request; that outranks anything *this host* can observe.

    It is the **weakest** pin, and :func:`pin` — not this function — owns that order. It
    does not outrank the shipping run's own record of what it did, in either of the two
    places that record survives: the ``ship_run`` ledger (:func:`shipped`) and the closure
    comment rendered from it (:func:`recorded`). A posted verdict establishes that a panel
    sat for this head; it does not establish that this run's review *was* that panel.

    ``probed: False`` says plainly that nothing was measured here. Everything else keeps the
    shape :meth:`Availability.as_dict` publishes, so every reader downstream is unchanged.
    """
    return {
        "probed": False,
        "staffable": True,
        "decision": DECISION_AVAILABLE,
        "on_unavailable": jury_on_unavailable(policy),
        "required_vendors": max(1, min_vendors),
        "available_vendors": [],
        "unavailable": [],
        "runner": {"command": JURY_RUNNER_COMMAND, "usable": True, "reason": "the panel sat"},
        "inventory": SOURCE_PULL_REQUEST,
        "source": SOURCE_PULL_REQUEST,
        "reason": (
            "jury panel staffable: a head-pinned jury verdict is posted on the pull "
            "request, so the panel sat for this head; this surface did not re-probe"
        ),
    }


def recorded(
    decision: Any, *, min_vendors: int = DEFAULT_MIN_VENDORS, policy: str | None = None
) -> dict[str, Any] | None:
    """The panel decision this run published in its own closure comment (#1068 round 6).

    The same statement :func:`shipped` reads, from the copy that travels with the pull
    request. It exists because the stronger copy does not travel: the ``ship_run`` ledger
    lives under the gitignored ``.keel/state/``, so on a hosted ``evidence-verify`` or
    ``merge`` — the CI check, or any machine other than the one that shipped — there is no
    same-head record and the ledger pin cannot fire at all. The precedence it establishes
    held on the workstation that shipped and nowhere else, while a leftover
    ``keel.jury-verdict.v1`` answered for that run everywhere else.

    ``decision`` comes from :func:`keel.evidence.shipped_panel_decision`, which has already
    held it to a trusted author, an actual closure comment, and this exact head. Only
    ``available`` and ``fallback`` produce a record, exactly as in :func:`shipped`:
    ``block`` refused its run, so it is not a decision anything shipped under, and an
    unrecognised value is not a decision at all. Either reads as ``None``, and :func:`pin`
    returns that ``None`` as the answer rather than falling through — the same rule it
    holds a same-head ledger record to, for the same reason.

    The record is thinner than the ledger's — the comment carries the decision and the
    seats' prose, not the structured inventory — so it publishes no vendors and no seats
    and says where it came from. Every consumer reads ``decision``
    (:func:`keel.team._panel_falls_back`) and the ``reason`` sentence, both of which are
    here; nothing downstream needs the seat list to resolve a bench.

    ``probed: False``, like every pin: a surface that read a comment measured nothing.
    """
    if decision not in (DECISION_AVAILABLE, DECISION_FALLBACK):
        return None
    staffable = decision == DECISION_AVAILABLE
    outcome = (
        "the panel sat"
        if staffable
        else "the panel could not be staffed there and a host bench reviewed instead"
    )
    return {
        "probed": False,
        "staffable": staffable,
        "decision": decision,
        "on_unavailable": jury_on_unavailable(policy),
        "required_vendors": max(1, min_vendors),
        "available_vendors": [],
        "unavailable": [],
        "runner": {
            "command": JURY_RUNNER_COMMAND,
            "usable": staffable,
            "reason": outcome,
        },
        "inventory": SOURCE_CLOSURE_COMMENT,
        "source": SOURCE_CLOSURE_COMMENT,
        "reason": (
            f"jury panel {'staffable' if staffable else 'not staffable'}: the run that "
            f"produced this head recorded '{decision}' in the closure comment it posted "
            "on this pull request, so " + outcome + "; this surface did not re-probe"
        ),
    }


def shipped(record: Mapping[str, Any] | None, *, head_sha: str | None) -> dict[str, Any] | None:
    """The panel decision the ship that produced **this head** measured, or ``None``.

    Read out of that run's ``ship_run`` ledger entry at ``run_context.jury_panel``, which
    :func:`keel.ledger.build_ship_run_record` writes for exactly this purpose. Total: a
    record from before the field existed, or one whose run resolved no panel, reads as
    ``None``.

    What that ``None`` then means is :func:`pin`'s to decide and not this function's, and
    the two cases part there rather than here: a run that *was* asked and answered ``null``
    silences the lower sources, while a record that never carried the key
    (:func:`states_panel`) is not consulted at all and the lower sources get their turn.

    **Pinned to the exact head, the way the posted-verdict path is.** The record is selected
    by pull-request number (:func:`keel.ledger.latest_ship_run_for_pr`), and a pull request
    outlives its heads: a ship of an earlier head that fell back to a host bench would
    otherwise weaken the contract of the head being verified now, which is a stale run
    relaxing a live gate. So the record's ``git.head_sha`` must equal the head under
    verification, and anything else — an older head, a blank or absent head on either side,
    a malformed ``git`` block — reads as ``None``.

    ``None`` **fails closed**, which is why it is safe to be strict here. It does not waive
    the panel; it drops the pin, and the caller then measures this machine. Both ways that
    can land are the refusing one: a fallback-shipped change verified where the panel *can*
    be staffed is held to a panel it did not run, and a panel-shipped change verified on a
    bare runner is held to host verdicts nobody posted. A run that genuinely convened the
    panel at this head is unaffected either way — this record then says ``available`` and
    its ballots are on the pull request, so both sources agree.

    ``probed: False`` for the same reason :func:`panel_sat` sets it: this is a record being
    republished, not a measurement taken here. The ledger's own copy carries ``probed: True``
    because the *ship* did probe; repeating that claim on a surface that only read a file
    would be the one thing this module refuses to let a record do — claim a provenance it
    does not have.
    """
    context = record.get("run_context") if isinstance(record, Mapping) else None
    panel = context.get("jury_panel") if isinstance(context, Mapping) else None
    if not isinstance(panel, Mapping) or panel.get("decision") not in (
        DECISION_AVAILABLE,
        DECISION_FALLBACK,
    ):
        return None
    if not _matches_head(record, head_sha):
        return None
    return {**dict(panel), "probed": False, "source": SOURCE_RUN_LEDGER}


def is_ship_run_for_head(record: Mapping[str, Any] | None, *, head_sha: Any) -> bool:
    """Did a ``ship_run`` for **this exact head** leave a record? (#1068)

    Presence, not content: the run's ``run_context.jury_panel`` may say ``fallback``,
    ``block``, or ``None``, and this still answers ``True``. That separation is the
    whole point — :func:`pin` needs to know *whether the run left a record here* before it
    reads what the record says, because a record that says nothing about a panel is still
    a run that did not ship under one.
    """
    return isinstance(record, Mapping) and _matches_head(record, head_sha)


def states_panel(record: Mapping[str, Any] | None) -> bool:
    """Does this record carry the ``jury_panel`` **key** at all? (#1068 round 7)

    Not what it says — whether the run that wrote it had the word. The distinction is
    between a run that was *asked* about the panel and answered (even by answering
    ``None``: "this tier named no panel") and a record written before the field existed,
    which was never asked.

    :func:`keel.ledger._run_context` always writes the key, ``None`` included, so for every
    record this feature produces the answer is ``True`` and rank 3 of :func:`pin` is
    unchanged: a same-head record whose ``jury_panel`` is ``null`` is the run saying it did
    not ship under a panel, and it silences the lower sources. A ledger row from before
    #1066 has no such key — missing vocabulary, not a statement — and silencing on its
    behalf would put words in a run's mouth: on a workstation carrying one, a change the
    panel really did jury would have had its posted ballots ignored and
    ``review-verdict-1..3`` demanded by a probe of the local machine. Those rows fall
    through to the closure comment and then to the ballots, which is exactly what they did
    before #1066 existed.

    A record whose ``run_context`` is missing or unreadable has no key either, and reads the
    same way, for the same reason: absence of vocabulary, not a statement.
    """
    context = record.get("run_context") if isinstance(record, Mapping) else None
    return isinstance(context, Mapping) and "jury_panel" in context


def pin(
    record: Mapping[str, Any] | None,
    *,
    head_sha: Any,
    panel_verdict_posted: bool,
    closure_panel_decision: Any = None,
) -> dict[str, Any] | None:
    """**The single authority on "what did this run ship under".** (#1066, #1068)

    Every verification surface — ``keel evidence-verify``, ``keel merge``, and
    :func:`keel.cli._shipped_jury_availability`, which is only this function's thin-I/O
    wrapper — resolves that question here and nowhere else. It is one function rather than
    an order of ``if``-statements at a call site because the *precedence* between the two
    pins is itself a rule, and #1068 rounds 2–4 each found a rule written in one place and
    forgotten in its twin. There is one place now, and this docstring is it.

    ``None`` means "nothing pins this head": the caller measures its own machine, exactly
    as it did before either pin existed. That is the fail-closed answer, never a waiver —
    a probe can only add the panel requirement back or demand the tier's host verdicts.

    **Both run-record sources select the *latest* record for this head, and that direction
    is the rule** (#1068 round 7). The ledger source resolves through
    :func:`keel.ledger.latest_ship_run_for_pr`, which walks the chronologically appended
    records and keeps the **last** match; the closure source resolves through
    :func:`keel.evidence.shipped_panel_decision`, which walks ``pr_comments`` in GitHub's
    oldest-first order and keeps the **last** match. They are two copies of one statement —
    ranks 2 and 4 are the same sentence read off two artifacts — so if they disagreed about
    which run they were quoting, the precedence between them would be meaningless: the
    machine with the ledger would answer for the newest ship and the machine without it for
    the oldest. Round 6 had exactly that, and worse: the closure source was first-match
    *and* a run whose panel sat rendered no marker, so a commit shipped once under the
    fallback and then again on a machine that could staff the panel left one marker on the
    pull request saying ``fallback``, and CI — where there is no ledger — pinned the
    host-bench contract onto a panel-reviewed change and never asked for the panel's own
    verdict. :func:`keel.closure._jury_panel` now emits ``decision=available`` too, so every
    ship speaks and "latest wins" is well defined on both sides.
    ``tests/test_juryavail.py::TestBothRunRecordSourcesSelectTheLatest`` holds them to it.

    The order, and why it is this way round:

    1. **No head, no pin.** :func:`is_pinnable_head`. A pin removes requirements — it takes
       ``review-verdict-1..3`` off the required set outright — so it may only ever be taken
       against an exact commit. ``panel_verdict_posted`` is ignored here even when ``True``,
       because :func:`keel.evidence.panel_verdict_posted` reads a blank head as *no head
       filter*: right for counting evidence, wrong for a pin.
    2. **The run's own ledger record for this head wins** (:func:`is_ship_run_for_head`,
       then :func:`shipped`). The ledger records what *this run actually did*; a posted
       verdict records what somebody put on the pull request. At the same head the two can
       disagree, and then the ledger is the one that is evidence of the run: a ship that
       measured ``fallback`` seated three host reviewers and owes ``review-verdict-1..3``,
       and a leftover jury verdict at that head — from an earlier ship of the same commit,
       from a force-push back onto it, or from a collaborator who ran ``jury`` by hand —
       is not that run's review. Letting the verdict win dropped three required items on
       the strength of a comment nobody's run had promised.
    3. **A same-head record that says nothing about a panel still speaks — if it had the
       word.** :func:`shipped` returns ``None`` for a record whose ``run_context.jury_panel``
       is ``null``, malformed, or ``block``, and that ``None`` is returned as-is rather than
       falling through to a comment: this run left a record here and it does not say the run
       shipped under a panel, so nobody may say otherwise on its behalf.

       The one record that does *not* speak is one whose ``run_context`` never carried the
       key (:func:`states_panel`) — a ledger row written before #1066, or one whose
       ``run_context`` is unreadable. That is missing vocabulary, not a statement, and
       silencing the lower sources on its behalf would answer a question the run was never
       asked: a change the panel really did jury, verified on the workstation that still
       has that row, would have had its posted ballots ignored and ``review-verdict-1..3``
       demanded by a probe of *this* machine. Such a record falls through to rank 4 and then
       rank 5, which is what it did before #1066 existed. Every record this feature writes
       carries the key — :func:`keel.ledger._run_context` always writes it, ``None``
       included — so the fall-through applies to legacy rows and to nothing else.
    4. **Failing that, the run's own closure comment** (:func:`recorded`), whose decision
       :func:`keel.evidence.shipped_panel_decision` has already held to a trusted author,
       to an actual closure comment, and to this head. This rank is what makes rank 2 mean
       anything off the shipping workstation (#1068 round 6): the ledger lives under the
       gitignored ``.keel/state/``, so a hosted ``evidence-verify`` or ``merge`` has no
       same-head record at all and fell straight through to the verdict — the run's
       fallback was outranked by a leftover comment on every machine except the one that
       had no need of the rule. The closure comment is the *same statement* as the ledger
       record it was rendered from, in the one place that travels with the pull request,
       so it ranks with the ledger and above the verdict. Since round 7 it is silent only
       for a head no keel closure comment names — a run that shipped under a staffable panel
       records ``decision=available`` and pins the panel here rather than leaving rank 5 to
       infer it from ballots.

       Silent and refusing are different answers, and rank 4 gives both: no marker for this
       head is silence and rank 5 gets its turn, while a marker that says ``block`` or
       something unrecognised is a record that does not say the run shipped under a panel,
       and :func:`recorded` returns ``None`` as the answer for the same reason rank 3 does.
    5. **Only with no record of the run's own does a posted verdict pin**
       (:func:`panel_sat`). Head-pinned ballots prove a panel *sat* for this head; they do
       not prove this run's review **was** that panel, which is why they rank last.

    Every pin therefore runs in the same direction: the strongest available statement about
    *this run at this head*, falling back to measuring rather than to guessing.
    """
    if not is_pinnable_head(head_sha):
        return None
    if is_ship_run_for_head(record, head_sha=head_sha) and states_panel(record):
        return shipped(record, head_sha=head_sha)
    if closure_panel_decision is not None:
        return recorded(closure_panel_decision)
    return panel_sat() if panel_verdict_posted else None


def is_pinnable_head(head_sha: Any) -> bool:
    """Is ``head_sha`` a head a pin may be taken against? (#1068)

    **The one blank-head rule both panel pins read.** A pin republishes an earlier run's
    panel decision in place of measuring this machine, so it may only ever be taken against
    an exact commit: an unknown head must not be authorized by a record — or a comment —
    from some other one. :func:`keel.ledger.gates_pass_for_head` already holds the merge
    gate to this, and :func:`shipped` to the ledger pin; round 3 hardened those and left
    the posted-verdict pin reading :func:`keel.evidence._matches_head`, which treats a
    blank head as *unfiltered* and so counted any trusted jury marker on the pull request
    as this head's. Written twice, hardened once. It is written here now, and :func:`pin`
    — the one place the two sources are ranked — asks it before either is consulted.

    ``keel.evidence``'s own rule is deliberately the other one and stays that way: it
    filters *evidence items* inside a gate that, with no head resolved, runs head-agnostic
    throughout — every review verdict counts too. Nothing there removes a requirement. A
    pin does: it takes ``review-verdict-1..3`` off the required set entirely.
    """
    return isinstance(head_sha, str) and bool(head_sha.strip())


def _matches_head(record: Mapping[str, Any], head_sha: str | None) -> bool:
    """Was this ledger record written for exactly ``head_sha``? A blank head never matches."""
    if not is_pinnable_head(head_sha):
        return False
    git = record.get("git")
    recorded = git.get("head_sha") if isinstance(git, Mapping) else None
    return isinstance(recorded, str) and recorded == head_sha


def _text(value: Any) -> str | None:
    """A non-blank string, or ``None`` — so a blank field reads as unset."""
    return value.strip() if isinstance(value, str) and value.strip() else None
