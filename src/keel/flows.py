"""Canonical command-flow registry: the ordered phases of every keel command.

The ship command's shape is the fixed :data:`keel.model.BACKBONE` (s0–s12). The
other commands have their own shapes — a CI poll, an audit scan→report, a
work-block loop, a triage queue pass. This module is the single source of truth
for all of them, so any consumer (notably ``keel-visual``) can render or reason
about a command's structure without re-deriving it from the adapter prose.

Each flow is an ordered tuple of :class:`Phase`. A phase ``kind`` is one of:

* ``normal`` — an ordinary step
* ``gate``   — a pass/fail quality gate (e.g. the ship test gate)
* ``loop``   — a repeating/iterating phase (a fix loop, a per-item work block)
* ``merge``  — the literal merge
* ``report`` — a terminal summary/output phase

Pure data: no I/O. ``ship`` reuses the backbone verbatim (its gate/loop kinds are
kept exactly as the rest of keel models them — gates at s8/s10, loop at s9) so
nothing about the ship flow changes here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import BACKBONE

SCHEMA_VERSION = "keel.flows.v1"

PHASE_KINDS = ("normal", "gate", "loop", "merge", "report")


@dataclass(frozen=True)
class Phase:
    """One phase in a command's flow."""

    id: str
    name: str
    kind: str = "normal"


# Ship's own gate/loop/merge semantics, keyed by backbone step id. Kept identical
# to how the rest of keel (and keel-visual) already treats the backbone.
_SHIP_KINDS = {"s8": "gate", "s9": "loop", "s10": "merge"}


def _ship_flow() -> tuple[Phase, ...]:
    return tuple(Phase(step.id, step.name, _SHIP_KINDS.get(step.id, "normal")) for step in BACKBONE)


def _flow(*phases: tuple[str, str, str]) -> tuple[Phase, ...]:
    return tuple(Phase(pid, name, kind) for pid, name, kind in phases)


#: The canonical flow for each keel command, in execution order.
FLOWS: dict[str, tuple[Phase, ...]] = {
    "ship": _ship_flow(),
    "ci-check": _flow(
        ("poll", "poll", "normal"),
        ("report", "report", "report"),
    ),
    "coverage": _flow(
        ("orient", "orient", "normal"),
        ("areas", "areas", "normal"),
        ("baseline", "baseline", "normal"),
        ("head", "head", "normal"),
        ("delta", "delta", "normal"),
        ("hotspots", "hotspots", "normal"),
        ("post", "post", "report"),
    ),
    "deps-audit": _flow(
        ("orient", "orient", "normal"),
        ("tracking", "tracking", "normal"),
        ("scan", "scan", "normal"),
        ("drift", "drift", "normal"),
        ("report", "report", "normal"),
        ("post", "post", "report"),
    ),
    "flake-audit": _flow(
        ("orient", "orient", "normal"),
        ("evidence", "evidence", "normal"),
        ("aggregate", "aggregate", "normal"),
        ("classify", "classify", "normal"),
        ("dedupe", "dedupe", "normal"),
        ("report", "report", "normal"),
        ("open", "open", "report"),
    ),
    "implement": _flow(
        ("config", "config", "normal"),
        ("fetch", "fetch", "normal"),
        ("branch", "branch", "normal"),
        ("resolve", "resolve", "normal"),
        ("codename", "codename", "normal"),
        ("delegate", "delegate", "normal"),
        ("report", "report", "report"),
    ),
    "morning": _flow(
        ("config", "config", "normal"),
        ("deferrals", "deferrals", "normal"),
        ("shipped", "shipped", "normal"),
        ("health", "health", "normal"),
        ("enrichment", "enrichment", "normal"),
        ("window", "window", "normal"),
        ("output", "output", "report"),
    ),
    "overnight": _flow(
        ("config", "config", "normal"),
        ("preflight", "preflight", "normal"),
        ("queue", "queue", "normal"),
        ("loop", "work block", "loop"),
        ("report", "report", "report"),
    ),
    "pr-loop": _flow(
        ("config", "config", "normal"),
        ("find", "find", "normal"),
        ("open", "open", "normal"),
        ("read", "read", "normal"),
        ("categorize", "categorize", "normal"),
        ("fix", "fix", "loop"),
        ("review", "review", "normal"),
        ("post", "post", "normal"),
        ("recheck", "recheck", "normal"),
        ("collect", "collect", "normal"),
        ("handoff", "handoff", "report"),
    ),
    "regression": _flow(
        ("orient", "orient", "normal"),
        ("preflight", "preflight", "normal"),
        ("fanout", "fan out", "loop"),
        ("aggregate", "aggregate", "normal"),
        ("dedupe", "dedupe", "normal"),
        ("open", "open", "normal"),
        ("report", "report", "report"),
    ),
    "review-all-day": _flow(
        ("config", "config", "normal"),
        ("parse", "parse", "normal"),
        ("commits", "commits", "normal"),
        ("decide", "decide", "normal"),
        ("classify", "classify", "loop"),
        ("open", "open", "normal"),
        ("report", "report", "report"),
    ),
    "review-cycle": _flow(
        ("config", "config", "normal"),
        ("validate", "validate", "normal"),
        ("loop", "loop", "loop"),
        ("reviewers", "reviewers", "normal"),
        ("post", "post", "normal"),
        ("fixloop", "fix loop", "loop"),
        ("report", "report", "report"),
    ),
    "stale-prs": _flow(
        ("orient", "orient", "normal"),
        ("list", "list", "normal"),
        ("classify", "classify", "normal"),
        ("triage", "triage", "normal"),
        ("post", "post", "normal"),
        ("rebase", "rebase", "normal"),
        ("summary", "summary", "report"),
    ),
    "swarm": _flow(
        ("config", "config", "normal"),
        ("plan", "dag partition", "normal"),
        ("isolate", "worktrees", "normal"),
        ("execute", "parallel waves", "loop"),
        ("land", "batch landing", "merge"),
        ("report", "swarm report", "report"),
    ),
    "triage": _flow(
        ("config", "config", "normal"),
        ("find", "find", "normal"),
        ("tier", "tier", "normal"),
        ("classify", "classify", "loop"),
        ("rank", "rank", "normal"),
        ("apply", "apply", "normal"),
        ("summary", "summary", "report"),
    ),
    "work-block": _flow(
        ("config", "config", "normal"),
        ("snapshot", "snapshot", "normal"),
        ("loop", "work block", "loop"),
        ("report", "report", "report"),
    ),
    "wrap": _flow(
        ("config", "config", "normal"),
        ("sanity", "sanity", "normal"),
        ("gates", "gates", "gate"),
        ("commit", "commit", "normal"),
        ("push", "push", "normal"),
        ("recap", "recap", "report"),
    ),
}

#: The default flow when a command is unknown — the ship backbone.
DEFAULT_COMMAND = "ship"


def command_names() -> tuple[str, ...]:
    """Return every known command name, in a stable sorted order."""
    return tuple(sorted(FLOWS))


def flow_for(command: str) -> tuple[Phase, ...]:
    """Return the ordered phases for ``command`` (the ship backbone if unknown)."""
    return FLOWS.get(command, FLOWS[DEFAULT_COMMAND])


def is_known(command: str) -> bool:
    """Return whether ``command`` has a dedicated flow (vs falling back to ship)."""
    return command in FLOWS
