"""What a live swarm worker is, what it may do, and what it leaves behind (#1400).

A live ``swarm-run`` worker used to be a child ``keel ship`` — an assessment that never
writes code, commits or opens a pull request, in any mode — so a live swarm had nothing to
land. A live worker is now the cluster's **implementer seat**, dispatched by keel through
the same machinery ``keel delegate run`` uses for one issue (:mod:`keel.delegate`), in the
cluster's own worktree. keel then commits what the implementer wrote, runs the project's
implementation gates, pushes the cluster branch and opens one pull request for the cluster.

This module holds every *decision* in that flow, and nothing else:

- **Consent.** The parent obtains the operator's consent exactly the way every other live
  keel command does (:func:`keel.consent.build_consent_contract`), and
  :func:`delegate_consent` turns an approved contract into a :class:`ConsentDelegation`: who
  consented, which scopes, which run and clusters, when. Each worker is handed
  :func:`worker_scopes` — the parent's scopes, for a cluster the delegation names, and
  nothing for any other — and asks :func:`consent_refusal` before its first mutation, so a
  worker never performs a mutation outside the scopes the parent handed it.
- **The seat.** :func:`plan_implementer` resolves the cluster's implementer seat
  (``knobs.team.implement``, a bench, or ``--delegate``, already resolved by
  :func:`keel.team.resolve_assignment`) into a :class:`keel.delegate.RunPlan`, and refuses a
  seat keel cannot run as a worker: a host subagent, and a transport that cannot edit a
  worktree.
- **What is written.** The brief, the commit message and the pull request's title and body.

Pure and deterministic: no subprocess, no filesystem, no clock. The runtime
(:mod:`keel.swarm_runtime`) performs the mutations, behind :func:`consent_refusal`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from . import consent, delegate
from . import providers as providers_mod

#: Every mutation a live worker performs, in the order it performs them. The parent's
#: consent contract is built over exactly these, so the scopes the operator approves are
#: the scopes a worker needs — ``filesystem``, ``git`` and ``github`` — and no more.
WORKER_SIDE_EFFECTS = ("git_worktree", "file_edit", "git_commit", "git_push", "pull_request")

#: Where a live worker can stop, in order. A worker reports the stage it failed at; the
#: branch is pushed only past ``gates`` and the pull request opened only past ``push``.
STAGES = ("consent", "worktree", "implement", "commit", "gates", "push", "pull_request", "done")

#: The consent a parent may have read from its own environment. It is the parent's to
#: delegate, explicitly, and never reaches a worker's children through inheritance: the
#: implementer is an agent CLI that can run ``keel`` itself, and an inherited
#: ``KEEL_APPROVE_SCOPE`` would let it approve its own mutations.
CONSENT_ENV_VARS = ("KEEL_APPROVE_SCOPE", "KEEL_OPERATOR", "KEEL_CONSENT_MODE")

#: The gate phases a worker runs after its commit: the ones the s4 loop judges an
#: implementation by. The jury is a review, deferred with the rest of review to the steps
#: that land the cluster (#1287).
GATE_PHASES = "guard,test"

#: The only delegate transport that runs an agent with tools in a working directory.
#: ``api``/``ollama`` return text, and a generic ``profile`` is not known to edit files
#: (the rule ``/keel:ship`` s4 already applies), so a worker built on one would commit
#: nothing.
TOOL_TRANSPORT = "cli"


@dataclass(frozen=True)
class ConsentDelegation:
    """The operator's live consent, as the parent hands it to one swarm run's workers."""

    swarm_id: str
    clusters: tuple[str, ...]
    scopes: tuple[str, ...]
    operator: str
    source: str
    mode: str
    #: When the operator's consent was recorded — the parent's consent record timestamp.
    delegated_at: str
    #: The parent's own consent record, verbatim.
    consent_record: Mapping[str, Any] = field(default_factory=dict)

    def for_run(self, swarm_id: str, clusters: Iterable[str]) -> ConsentDelegation:
        """The same consent, delegated to one run's clusters — the scopes do not change."""
        return replace(self, swarm_id=swarm_id, clusters=tuple(clusters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "clusters": list(self.clusters),
            "scopes": list(self.scopes),
            "operator": self.operator,
            "source": self.source,
            "mode": self.mode,
            "delegated_at": self.delegated_at,
            "consent_record": dict(self.consent_record),
        }


def delegate_consent(
    contract: Mapping[str, Any], *, swarm_id: str, cluster_ids: Iterable[str]
) -> tuple[ConsentDelegation | None, str]:
    """``(delegation, "")`` for an approved live contract, else ``(None, reason)``.

    Refused, with the reason, when the contract is not approved (a scope is missing), when
    it approved nothing a worker could be handed (``consent_mode: agent`` leaves approval
    to a host agent, and ``swarm-run`` dispatches its workers itself), and when it does not
    say who consented: a delegation records its operator, so an anonymous one is not made.
    """
    ok, message = consent.assert_operator_consent(dict(contract))
    if not ok:
        return None, message
    record = contract.get("consent_record")
    scopes = tuple(contract.get("delegated_agent_scope", {}).get("approved_mutation_scopes", ()))
    if not isinstance(record, Mapping) or not scopes:
        return None, (
            f"operator consent is {contract.get('status')!r}, which approves no scope a "
            "worker can be handed: swarm-run --live dispatches its workers itself, so the "
            "operator approves the scopes explicitly (--approve-scope "
            f"{','.join(required_scopes())} --operator NAME, or KEEL_APPROVE_SCOPE with "
            "KEEL_OPERATOR under consent_mode: standing)"
        )
    operator = record.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        return None, (
            "operator consent names no operator, and a delegation records who consented: "
            "pass --operator NAME beside --approve-scope"
        )
    delegation = ConsentDelegation(
        swarm_id=swarm_id,
        clusters=tuple(cluster_ids),
        scopes=consent.normalize_scopes(scopes),
        operator=operator,
        source=str(record.get("source", "none")),
        mode=str(contract.get("mode", "explicit")),
        delegated_at=str(record.get("timestamp", "")),
        consent_record=dict(record),
    )
    return delegation, ""


def required_scopes() -> tuple[str, ...]:
    """The consent scopes a live worker needs: those :data:`WORKER_SIDE_EFFECTS` map to."""
    return consent.side_effect_scopes(WORKER_SIDE_EFFECTS)


def worker_scopes(delegation: ConsentDelegation, cluster_id: str) -> tuple[str, ...]:
    """The scopes one worker is handed: exactly the parent's, for a delegated cluster.

    A cluster the delegation does not name gets nothing, so a worker started for a cluster
    the operator's consent was not delegated to cannot mutate anything.
    """
    return delegation.scopes if cluster_id in delegation.clusters else ()


def may(scopes: Iterable[str], side_effect: str) -> tuple[bool, str]:
    """Whether a worker holding ``scopes`` may perform ``side_effect``, and why not."""
    held = tuple(scopes)
    missing = [s for s in consent.side_effect_scopes((side_effect,)) if s not in held]
    if not missing:
        return True, ""
    return False, (
        f"{side_effect} needs the {', '.join(missing)} consent scope, which this worker was "
        f"not handed (it holds: {', '.join(held) or 'none'}); a worker never widens the "
        "scopes the operator delegated"
    )


def consent_refusal(scopes: Iterable[str]) -> str:
    """Why a worker holding ``scopes`` may not start, or ``""`` when it may.

    Asked once, before the worker's first mutation, over every mutation it will make
    (:data:`WORKER_SIDE_EFFECTS`): a worker that could commit and push but not open its
    pull request would leave a pushed branch nobody asked for, so it does none of them.
    """
    held = tuple(scopes)
    for side_effect in WORKER_SIDE_EFFECTS:
        allowed, reason = may(held, side_effect)
        if not allowed:
            return reason
    return ""


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` without the parent's consent variables, for a worker's children."""
    return {key: value for key, value in environ.items() if key not in CONSENT_ENV_VARS}


def plan_implementer(
    assignment: Mapping[str, Any] | None,
    *,
    config: Any,
    registry: providers_mod.Registry | None,
    prompt_path: str,
    cwd: str,
    timeout: int = delegate.DEFAULT_TIMEOUT_S,
) -> tuple[delegate.RunPlan | None, str]:
    """The cluster's implementer seat as a delegate plan, or ``(None, reason)``.

    The seat is the one :func:`keel.team.resolve_assignment` resolved for the cluster —
    ``--delegate`` over a ``--team`` or difficulty bench over ``knobs.team.implement`` over
    the host default — so ``swarm-plan`` shows the seat ``swarm-run --live`` dispatches. It
    is resolved and planned exactly as ``keel delegate run --provider <seat> --role
    implement`` would, with the cluster's worktree as ``cwd``.
    """
    if not assignment:
        return None, "the cluster has no resolved assignment, so it has no implementer seat"
    seat = assignment["implementer"]
    if seat["kind"] != "provider":
        return None, (
            f"the implementer seat {seat['provider']!r} (from {seat.get('source')}) is a host "
            "subagent, which only an agent host can spawn; pass --delegate <provider> or name "
            "a provider in knobs.team.implement for a live swarm"
        )
    token = seat["provider"] + (f":{seat['model']}" if seat.get("model") else "")
    try:
        resolution = delegate.resolve_provider(config, registry, token)
        plan = delegate.plan_run(
            resolution.provider,
            "implement",
            prompt_path,
            cwd,
            timeout,
            seat.get("effort"),
            resolution.model,
            profile=resolution.profile,
        )
    except delegate.DelegateError as exc:
        return None, f"the implementer seat {token!r} cannot be planned ({exc.code}): {exc.message}"
    if plan.transport != TOOL_TRANSPORT:
        return None, (
            f"the implementer seat {token!r} runs over the {plan.transport!r} transport, "
            "which cannot edit a worktree; a live worker needs an agent CLI "
            "(claude, codex, agy) — pass --delegate"
        )
    return plan, ""


def _issue_line(number: int, scopes: Mapping[int, Any]) -> tuple[str, str]:
    scope = scopes.get(number)
    title = getattr(scope, "title", "") or ""
    body = getattr(scope, "body", "") or ""
    return title.strip(), body.strip()


def render_brief(
    cluster: Any,
    issue_scopes: Mapping[int, Any],
    *,
    swarm_id: str,
    branch: str,
    base_branch: str,
) -> str:
    """The implementer's brief for one cluster: its issues, its scope, and its limits."""
    scope = ", ".join(cluster.combined_scope) or "*"
    lines = [
        f"You are the implementer for cluster {cluster.cluster_id} of keel swarm {swarm_id}.",
        f"Your working directory is this cluster's own git worktree, on branch {branch}, cut "
        f"from {base_branch}.",
        "",
        "Implement the issue(s) below completely, in this working tree:",
        f"- Change only files inside this working tree, within the cluster's scope: {scope}.",
        "- Add or update tests for what you change, and leave the project's tests passing.",
        "- Do not commit, push, open a pull request, or run any command that changes the "
        "repository's history or its remote. keel commits your changes, runs the project's "
        "gates, pushes the branch and opens the pull request itself.",
        "- If an issue cannot be implemented as written, leave it unchanged and say why in "
        "your final message.",
    ]
    for number in cluster.issues:
        title, body = _issue_line(number, issue_scopes)
        lines += ["", f"## Issue #{number}: {title or '(title unavailable)'}", ""]
        lines.append(body or "(The issue body could not be read; work from the title.)")
    return "\n".join(lines) + "\n"


def _issue_refs(cluster: Any) -> str:
    return ", ".join(f"#{n}" for n in cluster.issues)


def commit_message(cluster: Any, *, swarm_id: str, plan: delegate.RunPlan) -> str:
    """The cluster commit: what it implements, and who wrote it."""
    system = plan.attribution.get("system") or plan.provider
    lines = [
        f"feat(swarm): implement {_issue_refs(cluster)} ({swarm_id}/{cluster.cluster_id})",
        "",
        f"Written by the implementer seat {system} through keel swarm-run --live.",
        "",
    ]
    lines += [f"Refs #{n}" for n in cluster.issues]
    return "\n".join(lines) + "\n"


def pull_request_title(cluster: Any, issue_scopes: Mapping[int, Any], *, swarm_id: str) -> str:
    """One pull request per cluster; a one-issue cluster is titled after its issue."""
    if len(cluster.issues) == 1:
        title, _body = _issue_line(cluster.issues[0], issue_scopes)
        if title:
            return f"{title} (#{cluster.issues[0]}, swarm {swarm_id}/{cluster.cluster_id})"
    return f"swarm {swarm_id}/{cluster.cluster_id}: implement {_issue_refs(cluster)}"


def pull_request_body(
    cluster: Any,
    *,
    swarm_id: str,
    branch: str,
    base_branch: str,
    commit: str,
    plan: delegate.RunPlan,
    seat_source: str | None,
    delegation: ConsentDelegation,
) -> str:
    """The cluster's pull request body: the work, the seat, the gates and the consent.

    ``Refs``, never ``Closes``: the pull request carries no review evidence yet, and an
    issue is closed by the steps that review and land the cluster, not by opening it.
    """
    system = plan.attribution.get("system") or plan.provider
    lines = [f"Implements cluster `{cluster.cluster_id}` of keel swarm `{swarm_id}`.", ""]
    lines += [f"Refs #{n}" for n in cluster.issues]
    lines += [
        "",
        f"- **Branch:** `{branch}`, cut from `{base_branch}`, at `{commit}`",
        f"- **Implementer:** `{system}` (seat from `{seat_source or 'unknown'}`)",
        f"- **Gates:** `keel run-gates --phases {GATE_PHASES} --defer-jury` passed in the "
        "cluster worktree at that commit",
        f"- **Consent:** delegated by `{delegation.operator}` ({delegation.source}, "
        f"{delegation.delegated_at}) for `{', '.join(delegation.scopes)}`",
        "",
        "This pull request carries no review evidence yet: review and landing are not part of "
        "`swarm-run`.",
    ]
    return "\n".join(lines) + "\n"
