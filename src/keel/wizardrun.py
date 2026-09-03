"""Thin I/O: drive a ``--wizard`` run for ``keel ship`` / ``keel work-block`` (#1018).

The decisions are :mod:`keel.wizard`'s and stay pure. This module owns the three edges
that cannot be: the provider probe (:func:`keel.providerprobe.collect`), the terminal
(``input``, and the ``isatty`` check that decides whether there is one), and the parsed
``argparse`` namespace the resolved answers are written back onto. All three are
injectable, so the whole surface is unit-testable offline.

The **interactivity guard** is the contract ``ship.md`` has documented since the flag
existed: in any non-interactive context — watch mode, an overnight or background run,
a pipe — the wizard is a *logged no-op* and the command proceeds with the literal flags
as parsed. Never a hang waiting on a stdin nobody is typing into, never a rejection of
a run that was perfectly well specified on the command line.

``--wizard-answer KEY=VALUE`` is the third path: recorded answers, applied without
prompting, on a TTY or not. That is what makes a wizard run reproducible — and what
lets keel's own tests drive every question without a terminal.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from typing import Any

from . import providerprobe, wizard

#: Printed instead of prompting when there is no terminal and no recorded answers.
NON_INTERACTIVE = (
    "wizard: non-interactive context — logged no-op; proceeding with the flags as parsed"
)

#: Printed when the probe found nothing usable. Also a no-op: a wizard whose every
#: question would offer an empty list is worse than no wizard.
NO_PROVIDERS = (
    "wizard: no provider is available on this machine — logged no-op; run "
    "`keel doctor --providers` to see why"
)


def _default_ask(prompt: str, default: str) -> str:  # pragma: no cover - interactive I/O
    raw = input(f"{prompt}\n  answer [{default}]: ").strip()
    return raw or default


def _default_isatty() -> bool:  # pragma: no cover - reads the real stdin/stdout
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_option_wizard(
    args: argparse.Namespace,
    config: Any,
    *,
    command: str,
    _probe: Callable[[Any], dict[str, object]] | None = None,
    _ask: Callable[[str, str], str] | None = None,
    _isatty: Callable[[], bool] | None = None,
) -> int:
    """Run the option wizard and write its answers back onto ``args``.

    Returns ``0`` when the command may proceed (including every no-op path) and ``1``
    when the operator's own input was wrong — a malformed ``--wizard-answer``, or one
    naming a choice the wizard does not offer. Those fail closed on purpose: silently
    ignoring a misspelled answer would run a team the operator did not ask for.
    """
    if not getattr(args, "wizard", False):
        return 0
    # Resolved here, not in the signature: a default argument binds the function object
    # at import time, which makes the seam unpatchable from a test that wants to stub
    # the probe for a whole module rather than for one call.
    probe = _probe if _probe is not None else providerprobe.collect
    ask = _ask if _ask is not None else _default_ask
    isatty = _isatty if _isatty is not None else _default_isatty
    # stdout carries the JSON contract when --json is on, so the wizard's own prose
    # moves to stderr rather than corrupting a document a host is about to parse.
    stream = sys.stderr if getattr(args, "json", False) else sys.stdout
    answers, malformed = wizard.parse_answer_args(getattr(args, "wizard_answer", ()) or ())
    if malformed:
        for message in malformed:
            print(message, file=sys.stderr)
        return 1
    if not answers and not isatty():
        print(NON_INTERACTIVE, file=stream)
        return 0
    catalog = wizard.Catalog.from_report(probe(config))
    if not catalog.candidates:
        print(NO_PROVIDERS, file=stream)
        return 0
    policy = config.knobs.team
    for name in wizard.unavailable(policy, catalog):
        print(f"wizard: knobs.team names {name!r}, which is not usable here", file=stream)
    state = wizard.start(
        catalog,
        policy=policy,
        scope=wizard.SCOPE_RUN,
        review_comments=getattr(args, "review_comments", "inline") or "inline",
        jury=_jury_answer(args, policy),
        delegate=getattr(args, "delegate", None),
    )
    if answers:
        state, rejected = wizard.apply_answers(state, answers)
        if rejected:
            for message in rejected:
                print(f"wizard: {message}", file=sys.stderr)
            return 1
    else:
        state = wizard.run(state, ask, lambda message: print(f"wizard: {message}", file=stream))
    resolution = state.resolve()
    apply_resolution(args, resolution)
    print(f"keel {command} --wizard — resolved", file=stream)
    print(wizard.render(resolution), file=stream)
    return 0


def _jury_answer(args: argparse.Namespace, policy: Any) -> str:
    """Where the jury question starts: the flags, then ``knobs.team.jury.mode``, then off."""
    if getattr(args, "jury_advisory", False):
        return "advisory"
    if getattr(args, "jury", False):
        return "gating"
    if getattr(args, "no_jury", False):
        return wizard.JURY_OFF
    return policy.jury_mode or wizard.JURY_OFF


def apply_resolution(args: argparse.Namespace, resolution: wizard.Resolution) -> None:
    """Write the resolved answers back onto the parsed flags.

    Only attributes the command actually has are set. ``keel work-block`` takes
    ``--reviewers``/``--review-comments`` and hands the rest down to each child
    ``keel ship``, so its implementer and jury choices are *echoed* for the adapter to
    pass on rather than silently written onto a namespace with nowhere to put them.
    """
    seats = resolution.review if isinstance(resolution.review, tuple) else ()
    updates: Mapping[str, Any] = {
        "delegate": wizard.seat_token(resolution.implement),
        "review_delegate": [wizard.seat_token(seat) for seat in seats],
        "reviewers": len(seats) or None,
        "review_comments": resolution.review_comments,
        "jury": resolution.jury == "gating",
        "no_jury": resolution.jury == wizard.JURY_OFF,
        "jury_advisory": resolution.jury == "advisory",
    }
    for name, value in updates.items():
        if hasattr(args, name):
            setattr(args, name, value)
