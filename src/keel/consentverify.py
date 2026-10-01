"""Pure consent-boundary reconciliation: observed PR side effects vs approved scopes.

keel's consent scopes (see :mod:`keel.consent`) gate the CLI *contract* that an
agent renders before a live run — they do not gate the side effects themselves.
Every real mutation (git push, ``gh pr create``/``comment``/``merge``, label
writes) is executed by the agent directly and never passes a consent check, and
the consent ``status``/``scopes`` recorded on the ledger are whatever the agent
passed. There is no deterministic process that checks the side effects actually
*observed* on a PR against the scopes that were *approved*.

This module is that process, in its lowest-friction core-pure form: a post-hoc
reconcile. Given the side effects observed on a PR (the PR exists, comments were
posted, it was merged, labels were written) and the approved consent scopes from
the ledger's consent record, it maps each observed effect to its required scopes
(reusing :func:`keel.consent.side_effect_scopes` — no parallel vocabulary) and
flags any observed mutation not covered by an approved scope.

Two verdicts, fail-closed only on a real boundary breach:

* **advisory** — no consent record exists to reconcile against (a pre-consent or
  agent-self-reported PR). Back-compat: nothing to check, so nothing fails.
* **pass** / **fail** — a consent record exists. ``fail`` when an observed effect
  requires a scope the record never approved; ``pass`` otherwise.

Pure data in / structured report out: no network, subprocess, clock, or random.
The CLI does the I/O (transport observation of PR state, comments, merged,
labels; ledger consent record) and feeds the booleans/scopes here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import consent, ledger

SCHEMA_VERSION = "keel.consent-verify.v1"

VERDICT_ADVISORY = "advisory"
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"

#: Where a pull request's consent was read from: the ship run that opened it, or the
#: consent a live ``swarm-run`` delegated to the worker that opened it (#1400).
CONSENT_SOURCE_SHIP_RUN = ledger.RECORD_TYPE_SHIP_RUN
CONSENT_SOURCE_DELEGATION = ledger.RECORD_TYPE_CONSENT_DELEGATION
#: The record kinds consent-verify reads.
LEDGER_KINDS: tuple[str, ...] = (CONSENT_SOURCE_SHIP_RUN, CONSENT_SOURCE_DELEGATION)

# Observed-effect flag name -> the consent.side_effect vocabulary entry it maps
# to. Each side-effect resolves through ``consent.side_effect_scopes`` so the
# required-scope set always tracks the canonical consent vocabulary.
_EFFECT_SIDE_EFFECTS: dict[str, str] = {
    # The PR existing at all means a branch was pushed and a PR opened: git push
    # (scope ``git``) plus the gh pr create (scope ``github``).
    "pr_exists": "git_push",
    "pr_created": "pull_request",
    "comment": "comments",
    "merged": "merge",
    "label": "labels",
}

# ``pr_exists`` expands to two side effects (push + open) because a PR's mere
# existence implies both a ``git`` push and a ``github`` create.
_EFFECT_EXTRA_SIDE_EFFECTS: dict[str, tuple[str, ...]] = {
    "pr_exists": ("pull_request",),
}

OBSERVED_EFFECT_KINDS: tuple[str, ...] = tuple(_EFFECT_SIDE_EFFECTS)


@dataclass(frozen=True)
class ObservedEffects:
    """The mutating side effects observed on one PR (each defaults to absent).

    ``pr_exists`` is the baseline: a PR that exists implies a branch push and a
    ``gh pr create``. ``merged``/``commented``/``labeled`` layer on the heavier
    mutations. All offline-supplyable so tests are deterministic.
    """

    pr_exists: bool = False
    commented: bool = False
    merged: bool = False
    labeled: bool = False
    #: The pull request's current head commit, when known. Not an effect: a swarm
    #: cluster's delegated consent covers the commit its worker pushed and nothing else, so
    #: it applies only while the head is still that commit (#1400).
    head_sha: str | None = None

    def as_kinds(self) -> tuple[str, ...]:
        """Return the observed effect-kind names in a stable order."""
        kinds: list[str] = []
        if self.pr_exists:
            kinds.append("pr_exists")
        if self.commented:
            kinds.append("comment")
        if self.merged:
            kinds.append("merged")
        if self.labeled:
            kinds.append("label")
        return tuple(kinds)


def required_scopes_for_effect(effect_kind: str) -> tuple[str, ...]:
    """Return the consent scopes an observed ``effect_kind`` requires.

    Resolves through :func:`keel.consent.side_effect_scopes`, so the mapping
    reuses the canonical scope vocabulary rather than inventing a parallel one.
    Raises ``ValueError`` for an unknown effect kind so a typo can never silently
    map to "no scopes required" (which would wrongly pass reconciliation).
    """
    side_effect = _EFFECT_SIDE_EFFECTS.get(effect_kind)
    if side_effect is None:
        raise ValueError(
            f"unknown observed effect {effect_kind!r}; valid: {', '.join(OBSERVED_EFFECT_KINDS)}"
        )
    side_effects = (side_effect, *_EFFECT_EXTRA_SIDE_EFFECTS.get(effect_kind, ()))
    return consent.side_effect_scopes(side_effects)


def scope_effect_table() -> dict[str, list[str]]:
    """Return the deterministic observed-effect -> required-scope mapping table.

    Surfaced in the command contract and docs so operators can audit exactly how
    each observed mutation is scored without reading the code.
    """
    return {kind: list(required_scopes_for_effect(kind)) for kind in OBSERVED_EFFECT_KINDS}


def reconcile(
    observed: ObservedEffects,
    approved_scopes: list[str] | tuple[str, ...] | None,
    *,
    has_consent_record: bool,
) -> dict[str, Any]:
    """Reconcile observed PR side effects against approved consent scopes.

    ``observed`` are the mutations seen on the PR. ``approved_scopes`` are the
    scopes the ledger's consent record approved. ``has_consent_record`` is
    whether a consent record exists at all for the PR — when ``False`` there is
    nothing to reconcile against, so the verdict is ``advisory`` (back-compat for
    pre-consent PRs) regardless of what was observed.

    Returns a structured report: the per-effect coverage, a flat list of uncovered
    mutations (each naming the effect and the missing scope), the verdict, and a
    summary. Pure — reads only its arguments.
    """
    approved = consent.normalize_scopes(approved_scopes or ())
    effects = []
    uncovered: list[dict[str, Any]] = []
    for kind in observed.as_kinds():
        required = required_scopes_for_effect(kind)
        missing = tuple(scope for scope in required if scope not in approved)
        covered = not missing
        effects.append(
            {
                "effect": kind,
                "required_scopes": list(required),
                "missing_scopes": list(missing),
                "covered": covered,
            }
        )
        if not covered and has_consent_record:
            uncovered.append(
                {
                    "effect": kind,
                    "required_scopes": list(required),
                    "missing_scopes": list(missing),
                    "message": (
                        f"mutation {kind} not covered by approved consent scopes "
                        f"(requires {', '.join(required)}; missing {', '.join(missing)})"
                    ),
                }
            )
    verdict = _verdict(has_consent_record=has_consent_record, uncovered=uncovered)
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": verdict,
        "ok": verdict != VERDICT_FAIL,
        "has_consent_record": has_consent_record,
        "approved_scopes": list(approved),
        "observed_effects": list(observed.as_kinds()),
        "effects": effects,
        "uncovered": uncovered,
        "summary": {
            "observed": len(effects),
            "covered": sum(1 for effect in effects if effect["covered"]),
            "uncovered": len(uncovered),
        },
    }


def _verdict(*, has_consent_record: bool, uncovered: list[dict[str, Any]]) -> str:
    if not has_consent_record:
        return VERDICT_ADVISORY
    return VERDICT_FAIL if uncovered else VERDICT_PASS


def consent_record_from_ledger(
    record: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    """Extract ``(has_record, approved_scopes)`` from a ship_run ledger record.

    The ledger stores consent under ``run_context.consent`` as a ``status`` plus
    the approved mutation ``scopes`` (see :func:`keel.ledger._run_context`). A
    consent record is considered to *exist* only when ``status`` is a non-blank
    string — a missing record, a missing/empty ``run_context``, or a blank status
    all degrade to "no record" so the verdict falls back to advisory rather than
    failing a pre-consent PR. Pure — no I/O.
    """
    if not isinstance(record, dict):
        return False, ()
    run_context = record.get("run_context")
    consent_block = run_context.get("consent") if isinstance(run_context, dict) else None
    if not isinstance(consent_block, dict):
        return False, ()
    status = consent_block.get("status")
    has_record = isinstance(status, str) and bool(status.strip())
    raw_scopes = consent_block.get("scopes")
    scopes = (
        tuple(str(scope) for scope in raw_scopes if str(scope).strip())
        if isinstance(raw_scopes, list)
        else ()
    )
    return has_record, scopes


def consent_for_pr(
    records: list[dict[str, Any]],
    pr_number: int,
    *,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """The consent recorded for pull request ``pr_number``, and where it was read from.

    ``records`` are the run ledger's ship runs and consent delegations. In order:

    1. The latest ship run for the pull request, when it carries a consent status — the
       path every ``keel ship`` run's pull request takes, unchanged.
    2. The latest ``pull_request`` delegation record naming the pull request — the consent
       a live ``swarm-run`` handed the worker that opened it (#1400) — **only while the
       pull request's head is still the commit that worker pushed** (``head_sha``, the
       pull request's current head). A head that moved, or one nobody supplied, is not
       work the delegation covered, so it gets no delegated consent, and
       ``delegation_refused`` says why.

    There is no match by branch name: anyone can open a pull request from a fork whose
    branch is called ``swarm/<id>/<cluster>``, and a name is not a provenance. A cluster
    pull request whose own record did not reach the ledger gets no delegated consent.

    Returns ``{"source", "has_record", "scopes", "delegation", "delegation_refused"}``:
    ``source`` is :data:`CONSENT_SOURCE_SHIP_RUN`, :data:`CONSENT_SOURCE_DELEGATION` or
    ``None`` (no consent recorded — the advisory path); ``delegation`` says who delegated
    what when that is the source. Pure.
    """
    ship_run = ledger.latest_ship_run_for_pr(records, pr_number)
    has_record, scopes = consent_record_from_ledger(ship_run)
    refused: str | None = None
    if not has_record:
        record = ledger.consent_delegation_for_pr(records, pr_number)
        refused = None if record is None else _head_refusal(record, head_sha)
        if record is not None and refused is None:
            delegation = _delegation_summary(record)
            return {
                "source": CONSENT_SOURCE_DELEGATION,
                "has_record": True,
                "scopes": list(delegation["scopes"]),
                "delegation": delegation,
                "delegation_refused": None,
            }
    return {
        "source": CONSENT_SOURCE_SHIP_RUN if has_record else None,
        "has_record": has_record,
        "scopes": list(scopes),
        "delegation": None,
        "delegation_refused": refused,
    }


def _head_refusal(record: dict[str, Any], head_sha: str | None) -> str | None:
    """Why the delegation in ``record`` does not cover a pull request at ``head_sha``."""
    pushed = record["pull_request"]["head_sha"]
    current = head_sha.strip() if isinstance(head_sha, str) else ""
    if not current:
        return (
            f"the pull request's current head is unknown, so it cannot be matched to the "
            f"commit the worker pushed ({pushed}); pass --head-sha offline"
        )
    if current != pushed:
        return (
            f"the pull request's head moved since the worker pushed it: the delegation "
            f"covers {pushed}, the head is {current}"
        )
    return None


def _delegation_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Who delegated what, for the cluster pull request ``record`` names."""
    pull_request = record["pull_request"]
    return {
        "swarm_id": record["swarm_id"],
        "cluster": pull_request["cluster"],
        "operator": record["operator"],
        "scopes": list(record["scopes"]),
        "source": record["source"],
        "mode": record["mode"],
        "delegated_at": record["delegated_at"],
        "pull_request": pull_request["number"],
        "pushed_head": pull_request["head_sha"],
    }
