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
that could not produce one. A machine with ``claude`` and ``codex`` on ``PATH`` and no
``jury`` is **not** staffable, however healthy keel's own inventory looks: the panel s7
would dispatch cannot run.

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

from .team import DEFAULT_MIN_VENDORS, JURY_ON_UNAVAILABLE_DEFAULT, jury_on_unavailable

#: The binary a jury-panel tier's s7 actually dispatches. Not a delegate keel runs itself:
#: keel does not depend on ai-jury, and every path through this module stays total when it
#: is absent — absent simply means the panel cannot sit here.
JURY_RUNNER_COMMAND = "jury"

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


class JuryUnavailableError(RuntimeError):
    """``on_unavailable: block`` and the panel cannot be staffed — the run refuses.

    Raised out of the one place every review-aware surface resolves its team, and caught
    centrally in :func:`keel.cli.main`, so all six of them refuse identically rather than
    six near-copies of the same check drifting apart.
    """


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
    consumed here. ``doctor`` is ai-jury's own ``jury --doctor --json`` document when the
    binary produced a readable one; a runner that ran but could not report its panel is
    still *usable*, and the verdict then reads keel's own delegate inventory instead.
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
    fallback for an older or quieter runner. Either way a panel spans *vendors*, so two rows
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


def panel_sat(
    *, min_vendors: int = DEFAULT_MIN_VENDORS, policy: str | None = None
) -> dict[str, Any]:
    """The panel demonstrably sat: a head-pinned jury verdict is on the pull request (#1066).

    A verification surface must not answer "was the panel available" by asking *its own*
    machine. ``keel evidence-verify`` and ``keel merge`` run wherever CI puts them, and a
    change juried on a workstation and checked on a bare runner would otherwise have its
    required evidence quietly rewritten — the panel item dropped, three host verdicts
    demanded that nobody was ever asked to post. The ballots are already on the pull
    request; that is the measurement, and it outranks anything this host can observe.

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


def shipped(record: Mapping[str, Any] | None, *, head_sha: str | None) -> dict[str, Any] | None:
    """The panel decision the ship that produced **this head** measured, or ``None``.

    Read out of that run's ``ship_run`` ledger entry at ``run_context.jury_panel``, which
    :func:`keel.ledger.build_ship_run_record` writes for exactly this purpose. Total: a
    record from before the field existed, or one whose run resolved no panel, reads as
    ``None`` and leaves the caller to probe as it did before.

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
    panel at this head is unaffected either way — its ballots are on the pull request, and
    :func:`panel_sat` answers before this is ever consulted.

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


def _matches_head(record: Mapping[str, Any], head_sha: str | None) -> bool:
    """Was this ledger record written for exactly ``head_sha``? A blank head never matches.

    The rule :func:`keel.ledger.gates_pass_for_head` already holds the merge gate to: an
    unknown head must not be authorized by a record from some other commit.
    """
    if not isinstance(head_sha, str) or not head_sha.strip():
        return False
    git = record.get("git")
    recorded = git.get("head_sha") if isinstance(git, Mapping) else None
    return isinstance(recorded, str) and recorded == head_sha


def refusal_message(availability: Mapping[str, Any], *, source: str) -> str:
    """The message an ``on_unavailable: block`` run refuses with.

    It names the unavailable seats, because "the panel is unavailable" without them sends
    the operator to ``keel doctor --providers`` to learn what this run already measured.
    """
    unavailable = availability.get("unavailable")
    unavailable = unavailable if isinstance(unavailable, Sequence) else ()
    seats = [
        f"  - {seat.get('provider')}: {seat.get('reason')}"
        for seat in unavailable
        if isinstance(seat, Mapping)
    ]
    listed = "\n".join(seats) or "  - (no provider was probed)"
    vendors = availability.get("available_vendors")
    if not isinstance(vendors, Sequence) or isinstance(vendors, (str, bytes)):
        vendors = []
    return (
        f"{source} makes the cross-vendor jury the review for this tier, and the panel "
        f"cannot be staffed here: {len(vendors)} vendor(s) available "
        f"({', '.join(str(v) for v in vendors) or 'none'}), "
        f"{availability.get('required_vendors')} required.\n"
        f"Unavailable:\n{listed}\n"
        "knobs.team.jury.on_unavailable is 'block', so this run refuses rather than "
        "reviewing with a bench the policy did not ask for. Install or authenticate what "
        f"is missing — the panel runner answers `{JURY_RUNNER_COMMAND} --doctor` and keel's "
        "own delegates answer `keel doctor --providers` — or set on_unavailable: fallback "
        "to let a host bench of the same size review instead."
    )


def _text(value: Any) -> str | None:
    """A non-blank string, or ``None`` — so a blank field reads as unset."""
    return value.strip() if isinstance(value, str) and value.strip() else None
