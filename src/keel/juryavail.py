"""Can the cross-vendor panel actually be staffed here? (#1066)

On a tier whose ``knobs.team.review.by_tier`` names ``jury``, s7 dispatches the panel and
its ballots *are* the review — #1014 round 3 deliberately made it so no operator flag can
take the panel back off. That is right while the panel can run. When it cannot — an agent
CLI is not installed, is unauthenticated, or the account is out of quota — the tier has no
way forward at all: the only review it has is one this machine cannot convene.

This module is the pure half of the answer. The machine-dependent half is already written:
:func:`keel.providerprobe.collect` is what ``keel doctor --providers`` prints, and it
reports, per provider, whether it is usable *here, right now* and why not. Reusing it is
deliberate — a second prober would be a second answer to a question keel already answers,
and the two would drift.

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
class Availability:
    """The probe's verdict on this tier's panel, ready to publish."""

    #: Distinct vendors the panel needs before it can be a *cross-vendor* panel. This is
    #: ``team.jury.min_vendors``, the same floor the verdict is later held to.
    required_vendors: int
    #: Vendors the probe found usable here, in the probe's own (deterministic) order.
    available_vendors: tuple[str, ...]
    #: Every provider the probe could not use, with the reason it reported.
    unavailable: tuple[Seat, ...]
    #: ``fallback`` | ``block`` — the configured allowance, already defaulted.
    policy: str = JURY_ON_UNAVAILABLE_DEFAULT

    @property
    def staffable(self) -> bool:
        """Can this machine convene a panel spanning ``required_vendors`` vendors?"""
        return len(self.available_vendors) >= self.required_vendors

    @property
    def decision(self) -> str:
        """:data:`DECISION_AVAILABLE`, or the policy when the panel cannot be staffed."""
        return DECISION_AVAILABLE if self.staffable else self.policy

    @property
    def reason(self) -> str:
        """One sentence a reader can act on, naming the seats that were unavailable."""
        if self.staffable:
            return (
                f"jury panel staffable: {len(self.available_vendors)} vendor(s) available "
                f"({', '.join(self.available_vendors)}), {self.required_vendors} required"
            )
        listed = ", ".join(f"{seat.provider} ({seat.reason})" for seat in self.unavailable)
        return (
            f"jury panel not staffable: {len(self.available_vendors)} vendor(s) available "
            f"({', '.join(self.available_vendors) or 'none'}), {self.required_vendors} "
            f"required; unavailable: {listed or 'none probed'}"
        )

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
            "reason": self.reason,
        }


def assess(
    report: Mapping[str, Any] | None,
    *,
    min_vendors: int = DEFAULT_MIN_VENDORS,
    policy: str | None = None,
) -> Availability:
    """Read a ``keel doctor --providers`` report into a panel verdict.

    ``report`` is :func:`keel.providerprobe.build_report`'s document. Every row is a
    candidate seat: a panel spans *vendors*, so two entries that shell out to the same CLI
    are one opinion (the same rule :func:`keel.providers.distinct_vendors` states), and a
    hosted API with its key set is as real a panelist as a CLI on ``PATH``.

    Total by construction. A missing or malformed report yields *no* available vendors and
    *no* named seats, which reads as "not staffable" — the conservative answer, and the
    one that then goes through the project's own configured allowance rather than being
    quietly decided here.
    """
    rows = report.get("providers") if isinstance(report, Mapping) else None
    rows = rows if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) else ()
    available: list[str] = []
    unavailable: list[Seat] = []
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
    )


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
        "reviewing with a bench the policy did not ask for. Install or authenticate the "
        "missing providers (keel doctor --providers), or set on_unavailable: fallback to "
        "let a host bench of the same size review instead."
    )


def _text(value: Any) -> str | None:
    """A non-blank string, or ``None`` — so a blank field reads as unset."""
    return value.strip() if isinstance(value, str) and value.strip() else None
