"""Keel Swarm Review Runtime — each cluster pull request reviewed by its own seats (#1423).

The thin I/O half of ``keel swarm-review``. For each cluster of a wave of the plan
``swarm-run`` persisted, it finds the cluster's pull request, reads its head, diff and
review contract, plans the cluster's reviewer seats (:mod:`keel.swarm_review`, where every
decision is made), and — live — runs each seat read-only in its own temporary checkout of
the head, reads each answer, re-reads the head, and hands every verdict that parsed —
approvals and change requests — to ``keel review`` to post, pinned to the head the seats
reviewed.

Every seat runs through :mod:`keel.delegaterun` in the read-only ``review`` role, under the
implementer's lockdown environment (:func:`keel.swarm_worker.implementer_env`: no forge
token, no ``gh`` login, no git credential helper, no network transport). Its checkout is a
detached worktree at the head under ``.keel/worktrees/<swarm_id>/<cluster_id>.review-<slot>``,
removed when the seat ends, whichever way; one a crash leaves behind is a leftover
``keel swarm-status --clean`` removes.

Each live cluster's outcome is recorded in its worker record of the run state
(:func:`record_review`, #1440), which ``keel swarm-status`` shows; ``swarm-land`` does not
read it.

The outside world — the pull-request lookup, the reads, the seat itself and the post — is
injected (:class:`ReviewIO`), so the loop is tested against recorded answers.
"""

from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from . import swarm_review, swarm_runtime, swarm_worker
from .delegate import RunPlan
from .swarm import (
    SwarmCluster,
    SwarmPlan,
    load_swarm_state,
    save_swarm_state,
    update_worker_state,
)
from .swarm_landing import FindPullRequest
from .swarm_review import ClusterReview, PullRequestFacts, ReviewSeat, SeatVerdict
from .swarm_runtime import Implementer, SubprocessRunner, default_runner

#: ``(pull request, with_diff)`` -> what keel reads before any seat runs; raises on failure.
ReadPullRequest = Callable[[int, bool], PullRequestFacts]
#: ``pull request`` -> its head right now; raises when it cannot be read.
ReadHead = Callable[[int], str]
#: ``issue`` -> ``(title, body)``, empty strings when it cannot be read.
ReadIssue = Callable[[int], tuple[str, str]]
#: ``(pull request, head, bundle, run id)`` -> ``""`` once posted, else why not.
PostVerdicts = Callable[[int, str, list[dict[str, Any]], str], str]


def _default_seat(plan: RunPlan, env: dict[str, str]) -> dict[str, Any]:
    """``keel delegate run``'s executor with the seat's locked-down environment — the one a
    live implementer runs through, resolved at call time."""
    return swarm_runtime._default_implement(plan, env)


@dataclass(frozen=True)
class ReviewIO:
    """The seams ``swarm-review`` reaches the outside world through."""

    find_pull_request: FindPullRequest
    read_pull_request: ReadPullRequest
    read_head: ReadHead
    read_issue: ReadIssue
    post_verdicts: PostVerdicts
    #: Runs one seat's plan with its environment: ``keel delegate run``'s executor.
    run_seat: Implementer = _default_seat
    #: The git commands keel itself runs (fetch, worktree add/remove, the tamper reads).
    runner: SubprocessRunner | None = None
    remote: str = "origin"


def review_name(cluster_id: str, slot: str) -> str:
    return f"{cluster_id}.review-{slot}"


def review_checkout_path(swarm_id: str, cluster_id: str, slot: str, root: str | Path) -> Path:
    """A seat's checkout of the head, beside the run's worker worktrees."""
    return swarm_runtime.build_worktree_path(swarm_id, review_name(cluster_id, slot), root)


def review_brief_path(swarm_id: str, cluster_id: str, slot: str, root: str | Path) -> Path:
    """A seat's brief, beside the run's state and outside every checkout."""
    return swarm_runtime.build_brief_path(swarm_id, review_name(cluster_id, slot), root)


def _ensure_commit(root: Path, sha: str, pr: int, remote: str, run: SubprocessRunner) -> str:
    """``""`` once ``sha`` is in the local repository, else why it is not.

    A head the cluster's worker pushed from this repository is already here; one pushed
    since (a fix on the pull request) is fetched from the pull request's own ref.
    """
    probe = ["git", "cat-file", "-e", f"{sha}^{{commit}}"]
    if run(probe, root).ok:
        return ""
    fetched = run(["git", "fetch", "--no-tags", remote, f"refs/pull/{pr}/head"], root)
    if run(probe, root).ok:
        return ""
    why = fetched.output.strip()[:200] or f"exit {fetched.code}"
    return f"the head {sha} is not in this repository and could not be fetched ({why})"


def _run_seat(
    seat: ReviewSeat,
    brief: str,
    *,
    root: Path,
    swarm_id: str,
    cluster_id: str,
    facts: PullRequestFacts,
    io: ReviewIO,
    run: SubprocessRunner,
) -> tuple[SeatVerdict, list[str]]:
    """One seat, in its own checkout of the head, removed afterwards whatever happened."""
    # Only an eligible seat runs, and an eligible seat is planned with its checkout.
    plan = cast(RunPlan, seat.plan)
    checkout = Path(str(plan.cwd))
    warnings: list[str] = []
    created = False
    try:
        added = run(["git", "worktree", "add", "--detach", str(checkout), facts.head_sha], root)
        created = added.ok and checkout.exists()
        if not created:
            why = added.output.strip()[:200] or f"exit {added.code}"
            return swarm_review.failed_verdict(
                seat,
                f"its checkout of the head could not be made at {checkout} ({why}); a previous "
                "run may have left it — `keel swarm-status <project.yaml> --clean` removes it",
            ), warnings
        before = swarm_runtime._git_snapshot(run, checkout)
        if before is None:
            return swarm_review.failed_verdict(
                seat, f"the git setup of {checkout} could not be read"
            ), warnings
        brief_file = Path(plan.prompt_path)
        brief_file.parent.mkdir(parents=True, exist_ok=True)
        brief_file.write_text(brief, encoding="utf-8")
        gh_config = swarm_runtime.build_gh_config_path(
            swarm_id, review_name(cluster_id, seat.slot), root
        )
        result = io.run_seat(
            plan, swarm_worker.implementer_env(os.environ, gh_config_dir=str(gh_config))
        )
        if findings := swarm_runtime._tampered(run, checkout, before):
            return swarm_review.failed_verdict(
                seat,
                swarm_worker.tamper_reason(findings, during="the reviewer ran"),
                tampered=True,
            ), warnings
        return swarm_review.read_seat_verdict(
            seat, result, head_sha=facts.head_sha, pr_title=facts.title
        ), warnings
    finally:
        if created and not swarm_runtime.remove_swarm_worktree(root, checkout, runner=run):
            warnings.append(
                f"the review checkout {checkout} could not be removed; "
                "`keel swarm-status <project.yaml> --clean` retries it"
            )


def _run_seats(
    seats: tuple[ReviewSeat, ...],
    briefs: dict[str, str],
    *,
    max_workers: int,
    **kwargs: Any,
) -> tuple[tuple[SeatVerdict, ...], list[str]]:
    """Every eligible seat, at most ``max_workers`` at once; results in seat order."""
    eligible = [s for s in seats if s.eligible]
    results: dict[str, tuple[SeatVerdict, list[str]]] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(max_workers, len(eligible)))
    ) as pool:
        futures = {
            pool.submit(_run_seat, seat, briefs[seat.slot], **kwargs): seat for seat in eligible
        }
        for future in concurrent.futures.as_completed(futures):
            seat = futures[future]
            try:
                results[seat.slot] = future.result()
            except Exception as exc:  # noqa: BLE001 - one seat must not end the review
                results[seat.slot] = (
                    swarm_review.failed_verdict(
                        seat, f"the seat raised {type(exc).__name__}: {exc}"
                    ),
                    [],
                )
    verdicts = tuple(results[s.slot][0] for s in eligible)
    warnings = [w for s in eligible for w in results[s.slot][1]]
    return verdicts, warnings


def _review_cluster(
    cluster: SwarmCluster,
    *,
    swarm_id: str,
    recorded: int | None,
    dry_run: bool,
    io: ReviewIO,
    root: Path,
    config: Any,
    registry: Any,
    host_agent: str,
    seat_timeout: int,
    max_workers: int,
) -> ClusterReview:
    report = ClusterReview(cluster.cluster_id, swarm_review.ERROR)
    found = io.find_pull_request(
        swarm_runtime.cluster_branch(swarm_id, cluster.cluster_id), recorded
    )
    if found.number is None:
        status = swarm_review.MERGED if found.merged else swarm_review.SKIPPED
        return swarm_review.with_status(report, status, found.reason)
    number = found.number
    report = replace(report, pull_request=number)
    try:
        facts = io.read_pull_request(number, not dry_run)
    except Exception as exc:  # noqa: BLE001 - one cluster fails, the wave goes on
        return swarm_review.with_status(
            report, swarm_review.ERROR, f"PR #{number} could not be read: {exc}"
        )
    required = swarm_review.required_count(facts.contract)
    assignment = cluster.assignment or {}
    seats = swarm_review.review_seats(
        assignment,
        config=config,
        registry=registry,
        implementer_vendor=swarm_review.seat_vendor(
            assignment.get("implementer"), config=config, registry=registry, host_agent=host_agent
        ),
        brief_path=lambda slot: str(review_brief_path(swarm_id, cluster.cluster_id, slot, root)),
        checkout_path=lambda slot: str(
            review_checkout_path(swarm_id, cluster.cluster_id, slot, root)
        ),
        timeout=seat_timeout,
    )
    report = replace(
        report, head_sha=facts.head_sha, tier=facts.tier, required=required, seats=seats
    )
    refusal = swarm_review.cluster_refusal(
        seats,
        panel=str(assignment.get("review_panel") or "reviewers"),
        required=required,
        require_distinct=swarm_review.distinct_vendors_required(facts.contract),
    )
    if refusal:
        return swarm_review.with_status(report, swarm_review.REFUSED, refusal)
    if dry_run:
        return swarm_review.with_status(
            report,
            swarm_review.PLANNED,
            f"a live run reviews PR #{number} at {facts.head_sha} with "
            f"{sum(1 for s in seats if s.eligible)} seat(s)",
        )

    run = io.runner or default_runner
    if why := _ensure_commit(root, facts.head_sha, number, io.remote, run):
        return swarm_review.with_status(report, swarm_review.ERROR, why)
    issues = [(n, *io.read_issue(n)) for n in cluster.issues]
    briefs = {
        seat.slot: swarm_review.render_review_brief(
            swarm_id=swarm_id,
            cluster_id=cluster.cluster_id,
            pull_request=number,
            facts=facts,
            seat=seat,
            focus=swarm_review.focus_for(facts.contract, seat.slot, index),
            issues=issues,
        )
        for index, seat in enumerate(seats)
        if seat.eligible
    }
    verdicts, warnings = _run_seats(
        seats,
        briefs,
        max_workers=max_workers,
        root=root,
        swarm_id=swarm_id,
        cluster_id=cluster.cluster_id,
        facts=facts,
        io=io,
        run=run,
    )
    report = replace(report, verdicts=verdicts, warnings=tuple(warnings))
    if why := swarm_review.posting_decision(verdicts, required=required):
        return swarm_review.with_status(report, swarm_review.HELD, why)
    try:
        current = io.read_head(number)
    except Exception as exc:  # noqa: BLE001 - an unreadable head posts nothing
        return swarm_review.with_status(
            report, swarm_review.HELD, f"the head could not be re-read ({exc}); nothing is posted"
        )
    if why := swarm_review.head_moved(facts.head_sha, current):
        return swarm_review.with_status(report, swarm_review.HELD, why)
    items = swarm_review.posted_items(verdicts)
    why = io.post_verdicts(
        number, facts.head_sha, items, swarm_review.review_run_id(swarm_id, cluster.cluster_id)
    )
    if why:
        return swarm_review.with_status(
            report, swarm_review.HELD, f"keel review did not post: {why}"
        )
    status = swarm_review.posted_status(verdicts)
    reason = f"{len(items)} verdict(s) posted on PR #{number}, pinned to {facts.head_sha}"
    if status == swarm_review.POSTED_CHANGES_REQUESTED:
        rejecting = ", ".join(v.slot for v in verdicts if v.outcome == swarm_review.REQUEST_CHANGES)
        reason += (
            f"; seat(s) {rejecting} request changes, so keel merge holds it on "
            "review-verdict-not-approved until the findings are addressed and the head is "
            "reviewed again"
        )
    return swarm_review.with_status(report, status, reason)


def record_review(root: Path, swarm_id: str, review: ClusterReview, *, now: str) -> str:
    """Write ``review`` into its cluster's worker record in the run state (#1440); ``""``
    once written, else why it was not.

    The file ``swarm-run`` writes, through the same atomic writer. Re-read just before the
    write and written once per cluster, as each cluster ends, so a run that stops part-way
    keeps the clusters it reviewed, and a record replaces the cluster's previous one whole.
    Like ``swarm-land``, it takes no lock: do not run it beside a ``swarm-run`` of the same
    swarm, whose writes would race it.
    """
    state = load_swarm_state(swarm_id, root=root)
    if state is None:
        return f"the run state of {swarm_id} could not be read; the review is not recorded"
    if not any(w.cluster_id == review.cluster_id for w in state.workers):
        return f"the run state of {swarm_id} has no worker {review.cluster_id} to record it on"
    record = swarm_review.review_record(
        review,
        run_id=swarm_review.review_run_id(swarm_id, review.cluster_id),
        reviewed_at=now,
    )
    save_swarm_state(update_worker_state(state, review.cluster_id, review=record), root=root)
    return ""


def review_wave_clusters(
    plan: SwarmPlan,
    wave_index: int,
    *,
    dry_run: bool,
    io: ReviewIO,
    root: str | Path = ".",
    config: Any = None,
    registry: Any = None,
    host_agent: str = "claude",
    seat_timeout: int = 1800,
    max_workers: int = 3,
) -> swarm_review.SwarmReviewResult:
    """Review every cluster of one wave of ``plan``, in order.

    A dry run reads each pull request and plans its seats, and runs, checks out and posts
    nothing. A cluster that raises is a failed cluster; the next one is reviewed all the
    same. The run's state file is only read, for the pull request each worker recorded.
    """
    root_path = Path(root).resolve()
    wave = next((w for w in plan.waves if w.wave_index == wave_index), None)
    if wave is None:
        return swarm_review.SwarmReviewResult(
            plan.swarm_id,
            wave_index,
            dry_run,
            warnings=(f"the plan has no wave {wave_index}",),
        )
    state = load_swarm_state(plan.swarm_id, root=root_path)
    recorded = {w.cluster_id: w.pull_request for w in state.workers} if state else {}
    reviews: list[ClusterReview] = []
    warnings: list[str] = []
    for cluster in wave.clusters:
        try:
            reviewed = _review_cluster(
                cluster,
                swarm_id=plan.swarm_id,
                recorded=recorded.get(cluster.cluster_id),
                dry_run=dry_run,
                io=io,
                root=root_path,
                config=config,
                registry=registry,
                host_agent=host_agent,
                seat_timeout=seat_timeout,
                max_workers=max_workers,
            )
        except Exception as exc:  # noqa: BLE001 - one cluster must not end the wave
            reviewed = ClusterReview(
                cluster.cluster_id,
                swarm_review.ERROR,
                f"reviewing it raised {type(exc).__name__}: {exc}",
            )
        reviews.append(reviewed)
        # A dry run records nothing; a live one records each cluster as it ends.
        if not dry_run and (
            why := record_review(root_path, plan.swarm_id, reviewed, now=swarm_runtime._now())
        ):
            warnings.append(f"{cluster.cluster_id}: {why}")
    if not dry_run:
        swarm_runtime.remove_empty_swarm_dirs(root_path, plan.swarm_id)
    return swarm_review.SwarmReviewResult(
        plan.swarm_id, wave_index, dry_run, tuple(reviews), tuple(warnings)
    )
