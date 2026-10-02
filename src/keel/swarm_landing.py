"""Keel Swarm Landing — a wave's clusters land as their pull requests, through keel merge.

Each cluster's pull request is merged by the code path ``keel merge`` runs (#1287): the
merge window, the merge lock, the CI rollup, the evidence gate, the gates-pass pin, the
head-pinned squash and the post-merge drift check apply to it exactly as to any other
pull request. Nothing here merges, rebases or checks out a branch locally — the base
advances on the host and each pull request closes as merged — so the operator's
checkout is left where it was.

Once a cluster's pull request merges, its issues are closed the way ``/keel:ship`` closes
them at s11–s12 (#1422): the closure comment, rendered by
:func:`keel.closure.render_closure_comment` from the run ledger's ``ship_run`` record, on the
pull request and on each issue, then each issue closed. A failure there is a warning: the
merge already happened and is never undone, and the next cluster still lands.

This module is the per-wave loop and the reading of the answers it gets. The three things
it does to the outside world — find a cluster's pull request, hand one to keel merge, and
close a landed cluster's issues — are injected by the CLI, so the loop is tested against
recorded answers.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from .swarm import (
    ClusterClosure,
    SwarmCluster,
    SwarmLandingResult,
    SwarmPlan,
    SwarmWave,
    evaluate_wave_landing_mode,
    load_swarm_state,
    render_dependent_wave_refusal,
    save_swarm_state,
    update_worker_state,
)
from .swarm_runtime import SubprocessRunner, cluster_branch, default_runner

#: A cluster's outcome at landing.
LANDED = "landed"
HELD = "held"
FAILED = "failed"


class PullRequestLookup(NamedTuple):
    """The pull request a cluster lands through, or why there is none to land."""

    number: int | None
    reason: str = ""
    #: The cluster's pull request has merged already: nothing is left to land or review.
    merged: bool = False


class ClusterMerge(NamedTuple):
    """What ``keel merge`` did with one cluster's pull request.

    ``outcome`` is :data:`LANDED`, :data:`HELD` or :data:`FAILED`; ``warning`` is set
    when the merge landed but needs a look (``keel merge`` found drift).
    """

    outcome: str
    reason: str
    warning: str = ""
    #: The head ``keel merge`` verified and merged — the one every pin was taken against.
    #: Empty when the payload names none (a dry run that stopped early, a refusal).
    head_sha: str = ""
    #: The heads ``head_sha`` descends from by capture commits alone, which the evidence
    #: gate and the gates-pass counted for it (``keel merge``'s ``covered_heads``).
    covered_heads: tuple[str, ...] = ()


#: ``(branch, recorded pull request)`` -> the pull request to land.
FindPullRequest = Callable[[str, int | None], PullRequestLookup]
#: ``(pull request, cluster id, dry run)`` -> what keel merge did with it.
MergePullRequest = Callable[[int, str, bool], ClusterMerge]
#: ``(cluster, pull request, merge)`` -> how the landed cluster's issues were closed (#1422).
CloseCluster = Callable[[SwarmCluster, int, ClusterMerge], ClusterClosure]

#: What a live ``swarm-land`` does, for its operator consent (#1422): ``keel merge``'s own
#: side effects, then the closure — a comment on the pull request and on each issue, and the
#: issue closed. The comment and the close need ``github``, which the merge needs already.
LANDING_SIDE_EFFECTS: tuple[str, ...] = ("git_worktree", "merge", "comments", "issue_close")

#: The ``command`` of the ``ship_run`` record a landing appends after the merge.
LANDING_COMMAND = "swarm-land"
#: What that record says about the merge: it happened, through keel merge. Read as a merge
#: by :func:`keel.closeorder.record_attests_merge`, so closing the issue is not premature.
LANDING_MERGE_REASON = "merged by keel swarm-land through keel merge"


_ALREADY_MERGED = "already merged"


def _open_pull_request(reply: Mapping[str, Any], *, branch: str, base_branch: str) -> str:
    """Why the pull request in ``reply`` cannot be landed for ``branch``; ``""`` when it can."""
    number = reply.get("number")
    state = reply.get("state")
    if state == "MERGED":
        return f"PR #{number} is {_ALREADY_MERGED} — this cluster landed in an earlier run"
    if state != "OPEN":
        return (
            f"PR #{number} is {str(state).lower() or 'not open'}; reopen it, or open a new "
            f"one for {branch}"
        )
    head = reply.get("headRefName")
    if head is not None and head != branch:
        return f"PR #{number} is for branch {head}, not the cluster's branch {branch}"
    base = reply.get("baseRefName")
    if base is not None and base != base_branch:
        return f"PR #{number} targets {base}, not the configured base branch {base_branch}"
    return ""


def pull_request_from_view(
    recorded: int, reply: object, *, branch: str, base_branch: str
) -> PullRequestLookup:
    """The pull request ``swarm-run`` recorded for a cluster, checked against ``gh pr view``.

    The record says which pull request the worker opened; the host says whether it is
    still the open one for this branch and base. Anything else holds the cluster.
    """
    if not isinstance(reply, Mapping) or reply.get("number") != recorded:
        return PullRequestLookup(None, f"PR #{recorded} (recorded by swarm-run) could not be read")
    why = _open_pull_request(reply, branch=branch, base_branch=base_branch)
    if not why:
        return PullRequestLookup(recorded)
    return PullRequestLookup(None, why, merged=reply.get("state") == "MERGED")


def pull_request_from_list(reply: object, *, branch: str) -> PullRequestLookup:
    """The one open pull request ``gh pr list --head <branch>`` names, or why there is none.

    The fallback for a cluster whose run recorded no pull request (a state file from
    before #1287, or a pull request opened by hand).
    """
    prs = [p for p in reply if isinstance(p, Mapping)] if isinstance(reply, list) else []
    open_prs = [p for p in prs if p.get("state") == "OPEN" and isinstance(p.get("number"), int)]
    if len(open_prs) > 1:
        numbers = ", ".join(f"#{p['number']}" for p in open_prs)
        return PullRequestLookup(
            None,
            f"ambiguous: {len(open_prs)} open PRs for {branch} ({numbers}) — close the "
            "strays before landing",
        )
    if open_prs:
        return PullRequestLookup(open_prs[0]["number"])
    merged = [p for p in prs if p.get("state") == "MERGED" and isinstance(p.get("number"), int)]
    if merged:
        return PullRequestLookup(
            None,
            f"PR #{merged[0]['number']} is {_ALREADY_MERGED} — this cluster landed earlier",
            merged=True,
        )
    return PullRequestLookup(
        None,
        f"no open pull request for {branch} — swarm-run --live opens one per cluster",
    )


def merge_outcome(code: int, payload: Mapping[str, Any] | None) -> ClusterMerge:
    """Read ``keel merge``'s exit code and payload as one cluster's landing outcome.

    ``None`` means ``keel merge`` refused before it reached the pull request — consent,
    a missing ``git``/``gh``, the config — and printed why on stderr. A payload with a
    ``merge_output`` is one where the merge call was made: when that failed, the
    cluster *failed*; every other refusal *holds* it with ``keel merge``'s own reason.
    """
    if payload is None:
        return ClusterMerge(
            HELD, "keel merge refused before reaching the pull request; its reason is on stderr"
        )
    reason = str(payload.get("reason") or "no reason given")
    if code == 0:
        return ClusterMerge(LANDED, reason, **_merged_head(payload))
    if code == 3:
        return ClusterMerge(
            LANDED,
            reason,
            f"PR #{payload.get('pull_request')} merged, but keel merge detected drift — "
            "run keel verify-merge on it",
            **_merged_head(payload),
        )
    if "merge_output" in payload:
        return ClusterMerge(FAILED, f"keel merge: {reason}: {payload['merge_output']}"[:300])
    return ClusterMerge(HELD, f"keel merge: {reason}")


def _merged_head(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The head ``keel merge``'s evidence gate verified — the head it merged — and the heads
    that gate counted for it, as :class:`ClusterMerge` fields."""
    block = payload.get("evidence")
    block = block if isinstance(block, Mapping) else {}
    head = block.get("head_sha")
    covered = block.get("covered_heads")
    return {
        "head_sha": head if isinstance(head, str) else "",
        "covered_heads": tuple(str(sha) for sha in covered) if isinstance(covered, list) else (),
    }


def landing_record(
    prior: Mapping[str, Any],
    *,
    head_sha: str,
    reviewers: Sequence[str],
    run_context: Mapping[str, Any],
) -> dict[str, Any]:
    """The ``ship_run`` record of a cluster ``swarm-land`` merged (#1422).

    ``/keel:ship`` renders its closure comment from the ``ship_run`` record it appends at
    s11, and ``evidence-verify`` holds the posted comment to that record's render. A landing
    does the same with ``prior`` — the record whose gates-pass ``keel merge`` accepted for
    ``head_sha``, which a live worker wrote when it opened the pull request (#1420) — and
    changes only what the landing knows:

    - ``command`` is :data:`LANDING_COMMAND`, and ``assessment.merge`` is ``merge`` with
      :data:`LANDING_MERGE_REASON`: the pull request merged. Every other assessment field
      stays as recorded — the landing assessed no tier, window or CI of its own.
    - ``git.head_sha`` is the head keel merge merged.
    - ``actors.reviewers`` are the reviewers whose verdicts the evidence gate counted for
      that head, when any are named; otherwise what the record already said.
    - ``run_context`` is this landing's: its host agent, transport and operator consent.

    The gates, the changed files, the implementer, the issue and the capture block are
    carried as recorded, so the record still passes for the head
    (:func:`keel.ledger.gates_pass_for_head`) and its attribution still matches the
    labels. Pure: ``prior`` is copied, never changed.
    """
    record = copy.deepcopy(dict(prior))
    record["command"] = LANDING_COMMAND
    git = record.get("git")
    record["git"] = {**(git if isinstance(git, dict) else {}), "head_sha": head_sha}
    assessment = record.get("assessment")
    record["assessment"] = {
        **(assessment if isinstance(assessment, dict) else {}),
        "merge": {"action": "merge", "reason": LANDING_MERGE_REASON},
    }
    if reviewers:
        actors = record.get("actors")
        record["actors"] = {
            **(actors if isinstance(actors, dict) else {}),
            "reviewers": list(reviewers),
        }
    record["run_context"] = copy.deepcopy(dict(run_context))
    return record


def is_landing_record(record: Mapping[str, Any]) -> bool:
    """Whether ``record`` is a landing's own record — one :func:`landing_record` built.

    A landing that finds the record for the merged head is already its own renders from it
    rather than appending a second one, so a closure retried for the same merge posts the
    same comment again (edited in place) instead of a new one.
    """
    return record.get("command") == LANDING_COMMAND


def _output(res: object) -> str:
    return str(getattr(res, "stdout", "") or getattr(res, "output", "")).strip()


def _read_head(repo_root: Path, runner: SubprocessRunner | None) -> tuple[str, str] | None:
    """Where the checkout is: ``("branch", name)``, ``("detached", sha)`` or ``None``."""
    run = runner or default_runner
    res = run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], repo_root)
    if res.ok and _output(res):
        return ("branch", _output(res))
    res = run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], repo_root)
    if res.ok and _output(res):
        return ("detached", _output(res))
    return None


def _return_to(
    repo_root: Path, head: tuple[str, str] | None, runner: SubprocessRunner | None
) -> str | None:
    """Put the checkout back where the operator had it; a warning when that fails.

    Landing merges on the host and checks nothing out, so this is a postcondition, not
    an undo: the promise #1279 made — the operator ends where they started — is checked
    after every wave rather than assumed of every step keel merge takes.
    """
    if head is None or _read_head(repo_root, runner) == head:
        return None
    ref = head[1]
    run = runner or default_runner
    # A branch name checks that branch out; a sha detaches at it, as it started.
    # `--` so a file that happens to share the name is never read as a path.
    res = run(["git", "checkout", ref, "--"], repo_root)
    if res.ok:
        return None
    return (
        f"could not return the checkout to {ref} ({_output(res) or 'no output'}); "
        f"run `git checkout {ref}` by hand"
    )


def _close_landed(
    close_cluster: CloseCluster, cluster: SwarmCluster, number: int, merged: ClusterMerge
) -> ClusterClosure:
    """``close_cluster`` for one landed cluster; whatever it raises is a warning (#1422)."""
    try:
        return close_cluster(cluster, number, merged)
    except Exception as exc:  # noqa: BLE001 - the merge happened; closing it never undoes it
        return ClusterClosure(
            cluster.cluster_id,
            number,
            cluster.issues,
            warnings=(f"closing the issues raised {type(exc).__name__}: {exc}",),
        )


def _target_wave(plan: SwarmPlan, wave_index: int) -> SwarmWave | None:
    return next((w for w in plan.waves if w.wave_index == wave_index), None)


def land_wave_clusters(
    plan: SwarmPlan,
    wave_index: int,
    *,
    dry_run: bool,
    find_pull_request: FindPullRequest,
    merge_pull_request: MergePullRequest,
    root: str | Path = ".",
    runner: SubprocessRunner | None = None,
    close_cluster: CloseCluster | None = None,
) -> SwarmLandingResult:
    """Land one wave: each cluster's pull request, in order, through ``keel merge``.

    A ``sequential_dependent`` wave is refused before anything is looked up (#1276).
    Otherwise every cluster is independent of the others: its pull request is found
    (the number the run recorded, else the one open pull request for its branch) and
    handed to ``merge_pull_request`` — keel merge, which claims the merge lock for that
    one merge. A cluster with no pull request, or one keel merge refuses, is held with
    the reason; the next cluster is tried all the same. A dry run asks keel merge for
    its own dry run, so it reports what the live landing would do and merges nothing.

    ``runner`` is an injection seam for tests, like the ``_run`` seams elsewhere in keel:
    ``swarm-land`` never passes it. It runs the git commands that read where the checkout
    is and put it back (:func:`_read_head`, :func:`_return_to`); left out, they run through
    :func:`keel.swarm_runtime.default_runner`.

    ``close_cluster`` closes a landed cluster's issues (#1422): called once per cluster
    that merged, never for a held or failed one, and never in a dry run — there each cluster
    that would land reports, with an empty :class:`ClusterClosure`, what would be closed.
    What it could not do is a warning, as is anything it raises: the merge stands, and the
    next cluster lands all the same. Left out, nothing is closed and nothing is reported.

    The wave's ``mode`` is judged on its clusters' planned scopes. A ``pr_diff_map`` of
    real diffs used to be accepted here and no caller passed one; it could only relabel
    ``mode``, because every cluster lands through its own ``keel merge`` whatever the mode
    says, so it is gone (#1280).
    """
    root_path = Path(root).resolve()
    target_wave = _target_wave(plan, wave_index)
    if target_wave is None:
        return SwarmLandingResult(
            swarm_id=plan.swarm_id,
            wave_index=wave_index,
            mode="none",
            landed_clusters=(),
            failed_clusters=(),
            status="failed",
        )

    decision = evaluate_wave_landing_mode(target_wave, {})
    if decision.mode == "refused":
        # A dependent wave's branches predate the landing it depends on. It is refused
        # before any lookup or merge, dry run or live, so a preview reports exactly
        # what the live run would do (#1276).
        return SwarmLandingResult(
            swarm_id=plan.swarm_id,
            wave_index=wave_index,
            mode=decision.mode,
            landed_clusters=(),
            failed_clusters=(),
            status="failed",
            refused=render_dependent_wave_refusal(target_wave),
        )

    landed: list[str] = []
    failed: list[str] = []
    held: list[tuple[str, str]] = []
    warnings: list[str] = []
    prs: list[tuple[str, int]] = []
    closures: list[ClusterClosure] = []
    state = load_swarm_state(plan.swarm_id, root=root_path)
    # Only the pull request each worker opened is read from the run state. The review record
    # `swarm-review` leaves beside it (#1440) is a report for `swarm-status`, and is not read
    # here: whether a cluster is reviewed is `keel merge`'s evidence gate's question, asked of
    # the verdicts on the pull request at its current head — the only authority.
    recorded = {w.cluster_id: w.pull_request for w in state.workers} if state else {}
    home = _read_head(root_path, runner)

    def record(cluster_id: str, status: str, details: str, number: int | None = None) -> None:
        nonlocal state
        # Kept in memory only: a dry run never saves it (below).
        if state:
            state = update_worker_state(
                state,
                cluster_id,
                step="s10",
                status=status,
                details=details,
                pull_request=number,
            )

    try:
        for c in target_wave.clusters:
            branch = cluster_branch(plan.swarm_id, c.cluster_id)
            found = find_pull_request(branch, recorded.get(c.cluster_id))
            if found.number is None:
                held.append((c.cluster_id, found.reason))
                record(c.cluster_id, "held", found.reason)
                continue
            number = found.number
            prs.append((c.cluster_id, number))
            try:
                merged = merge_pull_request(number, c.cluster_id, dry_run)
            except Exception as exc:  # noqa: BLE001 - one cluster fails, the wave goes on
                merged = ClusterMerge(FAILED, f"keel merge raised {type(exc).__name__}: {exc}")
            if merged.outcome == LANDED:
                landed.append(c.cluster_id)
                record(c.cluster_id, "merged", f"PR #{number}: {merged.reason}", number)
                if merged.warning:
                    warnings.append(f"{c.cluster_id}: {merged.warning}")
                if close_cluster is None:
                    continue
                if dry_run:
                    closures.append(ClusterClosure(c.cluster_id, number, c.issues, dry_run=True))
                    continue
                closure = _close_landed(close_cluster, c, number, merged)
                closures.append(closure)
                warnings.extend(f"{c.cluster_id}: {warning}" for warning in closure.warnings)
            elif merged.outcome == FAILED:
                failed.append(c.cluster_id)
                record(c.cluster_id, "failed", f"PR #{number}: {merged.reason}", number)
            else:
                held.append((c.cluster_id, f"PR #{number}: {merged.reason}"))
                record(c.cluster_id, "held", f"PR #{number}: {merged.reason}", number)
    finally:
        warning = _return_to(root_path, home, runner)
        if warning:
            warnings.append(warning)

    # A dry run changes nothing, the run's state included.
    if state and not dry_run:
        save_swarm_state(state, root=root_path)

    # A held cluster is not landed, so it can never leave the wave "success"; it is
    # also not a failure of the merge itself, so it degrades the status exactly like a
    # failed cluster without being reported as one.
    not_landed = len(failed) + len(held)
    overall_status = (
        "success"
        if not_landed == 0 and len(landed) > 0
        else ("partial_failure" if len(landed) > 0 else "failed")
    )
    return SwarmLandingResult(
        swarm_id=plan.swarm_id,
        wave_index=wave_index,
        mode=decision.mode,
        landed_clusters=tuple(landed),
        failed_clusters=tuple(failed),
        status=overall_status,
        held_clusters=tuple(held),
        warnings=tuple(warnings),
        pull_requests=tuple(prs),
        closures=tuple(closures),
    )
