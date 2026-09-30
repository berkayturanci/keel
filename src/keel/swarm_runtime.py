"""Keel Swarm Runtime — Isolated multi-worktree execution & cluster orchestration.

Thin I/O execution layer for running parallel swarm workers in isolated Git worktrees,
handling worker state persistence, and managing fail-soft rebalancing across waves.

A dry run's worker is a ``keel ship --dry-run`` assessment per cluster. A live run's worker
(#1400) is the cluster's implementer seat, dispatched through :mod:`keel.delegaterun` in
the cluster's own worktree, followed by a commit, the project's gates, a push and one pull
request — every mutation behind the consent scopes the parent delegated
(:mod:`keel.swarm_worker`, where each of those decisions is made).
"""

from __future__ import annotations

import concurrent.futures
import datetime
import functools
import hashlib
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import delegaterun, github, runner, swarm_worker
from .delegate import RunPlan
from .runner import CommandResult
from .swarm import (
    SwarmCluster,
    SwarmPlan,
    SwarmRunResult,
    SwarmRunState,
    SwarmWorkerStatus,
    pull_request_number,
    rebalance_swarm_plan,
    save_swarm_state,
    ship_handoff_args,
    tail_child_output,
    update_worker_state,
    worker_seed,
)

SubprocessRunner = Callable[[list[str], Path], CommandResult]
#: Runs one implementer plan with the given child environment; returns the delegate result.
Implementer = Callable[[RunPlan, dict[str, str]], dict[str, Any]]
#: ``(argv, cwd)`` -> the push's result. The worker builds the argv — its hardened ``git
#: push --no-verify <url> <commit>:<ref>`` (:func:`keel.swarm_worker.keel_git`), to the URL
#: it read before the implementer ran — and the seat runs it in keel's own process, with the
#: operator's environment, which is where the credentials are.
Pusher = Callable[[list[str], Path], CommandResult]
#: ``(title, body, base, head, cwd)`` -> ``gh pr create``'s result.
PullRequestOpener = Callable[[str, str, str, str, Path], CommandResult]

#: The runner's own wall-clock limit, for the short git commands swarm and canary run
#: through it (worktree add/remove, checkout, merge). A worker's child ``keel ship`` is
#: not one of them: it runs the gate suite, and gets the swarm's worker timeout (#1279).
DEFAULT_RUNNER_TIMEOUT_S = 300


def default_runner(
    cmd: list[str],
    cwd: Path,
    timeout_s: int = DEFAULT_RUNNER_TIMEOUT_S,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run a subprocess command in cwd and return a CommandResult.

    A command still running after ``timeout_s`` seconds is killed and reported
    ``timed_out`` with ``code=124``, the gate runner's code for the same case.
    ``env`` replaces the child's environment when given (``None`` inherits it).
    """
    try:
        proc = subprocess.run(  # nosec B603
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return CommandResult(
            ok=(proc.returncode == 0),
            code=proc.returncode,
            output=proc.stdout or "",
            timed_out=False,
            stdout=proc.stdout or "",
            stderr="",
        )
    except subprocess.TimeoutExpired as exc:
        out = (
            exc.output
            if isinstance(exc.output, str)
            else (exc.stdout if isinstance(exc.stdout, str) else "")
        )
        return CommandResult(
            ok=False,
            code=124,
            output=out or "",
            timed_out=True,
            stdout=out or "",
            stderr="",
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(
            ok=False,
            code=1,
            output=str(exc),
            timed_out=False,
            stdout="",
            stderr=str(exc),
        )


def build_worktree_path(swarm_id: str, cluster_id: str, root: str | Path = ".") -> Path:
    """Return the isolated worktree filesystem path for a specific swarm cluster worker."""
    return Path(root) / ".keel" / "worktrees" / swarm_id / cluster_id


def build_brief_path(swarm_id: str, cluster_id: str, root: str | Path = ".") -> Path:
    """Where a live worker's implementer brief is written: beside the run's state file,
    outside the cluster's worktree, so the brief is never committed with the work."""
    return Path(root) / ".keel" / "state" / "swarm" / swarm_id / f"{cluster_id}.brief.md"


def build_gh_config_path(swarm_id: str, cluster_id: str, root: str | Path = ".") -> Path:
    """The ``GH_CONFIG_DIR`` a live worker's implementer runs with: a directory beside the
    brief, outside the worktree, that holds no ``gh`` login (#1400)."""
    return Path(root) / ".keel" / "state" / "swarm" / swarm_id / f"{cluster_id}.no-gh-login"


def cluster_branch(swarm_id: str, cluster_id: str) -> str:
    """The branch a cluster's worktree is cut on, and the head of its pull request."""
    return f"swarm/{swarm_id}/{cluster_id}"


def _default_implement(plan: RunPlan, env: dict[str, str]) -> dict[str, Any]:
    """``keel delegate run``'s executor, with the worker's scrubbed environment."""
    return delegaterun.execute(plan, _run=functools.partial(runner.run_argv, env=env))


def _default_push(argv: list[str], cwd: Path) -> CommandResult:
    """The worker's push, with the operator's environment (``env`` not passed)."""
    return runner.run_argv(argv, cwd=str(cwd))


def _default_open_pr(title: str, body: str, base: str, head: str, cwd: Path) -> CommandResult:
    return github.open_pr(title, body, base, head, cwd=str(cwd))


@dataclass(frozen=True)
class LiveRun:
    """What a live run's parent hands its workers: the operator's consent, delegated, and
    each cluster's planned implementer seat. The three callables are the I/O seams."""

    consent: swarm_worker.ConsentDelegation
    #: ``cluster_id`` -> the cluster's implementer :class:`keel.delegate.RunPlan`.
    dispatches: Mapping[str, RunPlan]
    #: ``issue`` -> its scope, for the brief and the pull request's title.
    issue_scopes: Mapping[int, Any] = field(default_factory=dict)
    #: ``cluster_id`` -> where its implementer seat came from (``assignment`` source).
    seat_sources: Mapping[str, str] = field(default_factory=dict)
    remote: str = "origin"
    implement: Implementer = _default_implement
    push: Pusher = _default_push
    open_pr: PullRequestOpener = _default_open_pr


def create_swarm_worktree(
    repo_root: Path,
    worktree_path: Path,
    branch_name: str,
    base_branch: str = "main",
    runner: SubprocessRunner | None = None,
) -> bool:
    """Create an isolated git worktree for a swarm cluster worker."""
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run = runner or default_runner
    cmd = [
        "git",
        "worktree",
        "add",
        "-B",
        branch_name,
        str(worktree_path),
        base_branch,
    ]
    res = run(cmd, repo_root)
    return res.ok


def remove_swarm_worktree(
    repo_root: Path,
    worktree_path: Path,
    runner: SubprocessRunner | None = None,
) -> bool:
    """Remove a previously created isolated git worktree."""
    run = runner or default_runner
    cmd = ["git", "worktree", "remove", "--force", str(worktree_path)]
    res = run(cmd, repo_root)
    if not res.ok and worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)
    return True


def execute_cluster_worker(
    project_yaml: str,
    issue: int,
    root: Path,
    worktree_dir: Path,
    *,
    dry_run: bool = True,
    role: str = "core",
    extra_args: list[str] | None = None,
    runner: SubprocessRunner | None = None,
    timeout_s: int = DEFAULT_RUNNER_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute a single cluster worker pipeline (keel ship) within its worktree.

    ``timeout_s`` bounds the child when keel runs it (no ``runner`` passed): the child
    runs the project's gate suite even in a dry run, so the runner's own 300 s cut off
    a suite the project allows longer (#1279). A passed ``runner`` owns its own bound.
    """
    run = runner or functools.partial(default_runner, timeout_s=timeout_s)
    cmd = [
        sys.executable,
        "-m",
        "keel",
        "ship",
        project_yaml,
        "--root",
        str(worktree_dir if worktree_dir.exists() else root),
        "--issue",
        str(issue),
        "--json",
    ]
    # `keel ship` gates every live-only path on `--live`; leaving out `--dry-run` does
    # not turn it on, so a live swarm's children ran the dry assessment (#1269).
    cmd.append("--dry-run" if dry_run else "--live")
    if extra_args:
        cmd.extend(extra_args)

    target_cwd = worktree_dir if worktree_dir.exists() else root
    res = run(cmd, target_cwd)
    output = res.stdout or res.output
    if res.timed_out:
        # Said last, where the tail the swarm keeps of the output still holds it: a
        # killed child printed no verdict, and its partial output read like a failure
        # of the change rather than of the budget (#1279).
        prefix = f"{output.rstrip()}\n" if output.strip() else ""
        output = (
            f"{prefix}keel ship timed out after {timeout_s}s (exit 124) and returned no "
            "verdict; raise the limit with swarm-run --worker-timeout if the gate suite "
            "legitimately needs longer."
        )

    return {
        "issue": issue,
        "role": role,
        "ok": res.ok,
        "code": res.code,
        "timed_out": res.timed_out,
        "output": output,
    }


def _last_line(result: CommandResult) -> str:
    """The last line a successful command printed; ``""`` for a failed one."""
    lines = (result.stdout or result.output or "").strip().splitlines() if result.ok else []
    return lines[-1].strip() if lines else ""


def execute_live_cluster_worker(
    cluster: SwarmCluster,
    *,
    swarm_id: str,
    root: Path,
    worktree_dir: Path,
    project_yaml: str,
    base_branch: str,
    live: LiveRun,
    runner: SubprocessRunner | None = None,
    timeout_s: int = DEFAULT_RUNNER_TIMEOUT_S,
) -> dict[str, Any]:
    """Implement one cluster live: its implementer seat, a commit, the gates, a push, a PR.

    Before its first mutation the worker checks that the scopes the parent delegated to
    *this* cluster cover every mutation it will make
    (:func:`keel.swarm_worker.consent_refusal`), and does nothing when they do not. It then
    stops at the first stage that fails — so a failed implementer or a red gate leaves the
    branch unpushed and no pull request opened, and the result says which stage stopped it
    and why. ``timeout_s`` bounds the gate run and the git commands; the implementer runs
    under its own plan's timeout.

    The worker's children — the implementer, git, the gates — run without the parent's
    consent variables or forge tokens (:func:`keel.swarm_worker.worker_env`): consent
    reaches a worker as the delegation it is handed, never through inheritance. The
    implementer also runs without the forge (:func:`keel.swarm_worker.implementer_env`): no
    ``gh`` login, no git credential helper and no network transport, so only keel's own
    push and pull request — made here, in keel's process, after the implementer has exited
    — reach the remote.

    The worktree shares the operator's repository, so the implementer could also write its
    config and hooks, which keel's own credentialed steps would then run. The worker reads
    the push URL and the git setup before the implementer runs, stops at ``tamper`` when
    that setup changed — after the implementer, and again after the gates — and runs its
    own git steps with no hooks, fsmonitor or signing program
    (:func:`keel.swarm_worker.keel_git`), pushing to the URL it read rather than a remote's
    name.
    """
    env = swarm_worker.worker_env(os.environ)
    run = runner or functools.partial(default_runner, timeout_s=timeout_s, env=env)
    scopes = swarm_worker.worker_scopes(live.consent, cluster.cluster_id)
    branch = cluster_branch(swarm_id, cluster.cluster_id)
    plan = live.dispatches[cluster.cluster_id]
    system = plan.attribution.get("system") or plan.provider
    record: dict[str, Any] = {
        "issue": cluster.issues[0] if cluster.issues else 0,
        "issues": list(cluster.issues),
        "role": cluster.role,
        "branch": branch,
        "scopes": list(scopes),
        "implementer": system,
        "commit": None,
        "pr_url": None,
    }

    def stop(stage: str, reason: str, code: int = 1) -> dict[str, Any]:
        return {**record, "ok": False, "code": code, "stage": stage, "output": reason}

    if why := swarm_worker.consent_refusal(scopes):
        return stop("consent", why)
    created = create_swarm_worktree(root, worktree_dir, branch, base_branch=base_branch, runner=run)
    if not created or not worktree_dir.exists():
        return stop("worktree", f"failed to create isolated worktree at {worktree_dir}")
    # Read before the implementer runs, from configuration it has not touched yet: where
    # keel will push, the commit the work must descend from, and the git setup keel's own
    # steps will run under.
    start = _last_line(run(["git", "rev-parse", "HEAD"], worktree_dir))
    push_url = _last_line(run(["git", "remote", "get-url", "--push", live.remote], root))
    if not push_url:
        return stop("push", f"the push URL of remote {live.remote!r} could not be read")
    before = _git_snapshot(run, worktree_dir)
    if before is None or not start:
        return stop("worktree", f"the git setup of {worktree_dir} could not be read")

    brief = Path(plan.prompt_path)
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(
        swarm_worker.render_brief(
            cluster, live.issue_scopes, swarm_id=swarm_id, branch=branch, base_branch=base_branch
        ),
        encoding="utf-8",
    )
    gh_config = build_gh_config_path(swarm_id, cluster.cluster_id, root)
    result = live.implement(
        plan, swarm_worker.implementer_env(os.environ, gh_config_dir=str(gh_config))
    )
    if not result.get("ok"):
        return stop(
            "implement",
            f"the implementer {system} failed ({result.get('error_code')}): {result.get('error')}",
        )

    if findings := _tampered(run, worktree_dir, before):
        return stop("tamper", swarm_worker.tamper_reason(findings, during="the implementer ran"))
    # Every git step from here is keel's own, and runs with no hooks (an empty directory
    # made only now, so nothing could be planted in it), no fsmonitor and no signing program.
    with tempfile.TemporaryDirectory(prefix="keel-no-hooks-") as no_hooks:
        return _commit_gate_push_open(
            cluster,
            record,
            stop,
            git=functools.partial(swarm_worker.keel_git, hooks_dir=no_hooks),
            run=run,
            before=before,
            start=start,
            push_url=push_url,
            swarm_id=swarm_id,
            worktree_dir=worktree_dir,
            project_yaml=project_yaml,
            base_branch=base_branch,
            branch=branch,
            plan=plan,
            live=live,
        )


def _hook_digests(hooks_dir: Path) -> dict[str, str]:
    """Each file under ``hooks_dir`` -> the digest of what it holds (through a symlink)."""
    paths = sorted(hooks_dir.rglob("*")) if hooks_dir.is_dir() else []
    return {
        path.relative_to(hooks_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _git_snapshot(run: SubprocessRunner, worktree_dir: Path) -> swarm_worker.GitSnapshot | None:
    """The worktree's git setup as keel's own steps would meet it, or ``None`` when git
    could not say. Read with plain ``git`` — ``rev-parse`` and ``config`` run no hook."""
    where = run(
        ["git", "rev-parse", "--absolute-git-dir", "--git-common-dir", "--git-path", "hooks"],
        worktree_dir,
    )
    config = run(["git", "config", "--list", "--show-origin", "--show-scope"], worktree_dir)
    paths = (where.stdout or where.output).splitlines() if where.ok else []
    if len(paths) != 3 or not config.ok:
        return None
    # `--git-common-dir` and `--git-path` may answer relative to the worktree.
    git_dir, common_dir, hooks_dir = (str(worktree_dir / p) for p in paths)
    return swarm_worker.GitSnapshot(
        git_dir=git_dir,
        common_dir=common_dir,
        hooks_dir=hooks_dir,
        config=tuple((config.stdout or config.output).splitlines()),
        hooks=_hook_digests(Path(hooks_dir)),
    )


def _tampered(
    run: SubprocessRunner, worktree_dir: Path, before: swarm_worker.GitSnapshot
) -> tuple[str, ...]:
    """What changed in the worktree's git setup since ``before``; unreadable is a change."""
    after = _git_snapshot(run, worktree_dir)
    if after is None:
        return ("the git setup could no longer be read",)
    return swarm_worker.tamper_findings(before, after)


def _commit_gate_push_open(
    cluster: SwarmCluster,
    record: dict[str, Any],
    stop: Callable[..., dict[str, Any]],
    *,
    git: Callable[[list[str]], list[str]],
    run: SubprocessRunner,
    before: swarm_worker.GitSnapshot,
    start: str,
    push_url: str,
    swarm_id: str,
    worktree_dir: Path,
    project_yaml: str,
    base_branch: str,
    branch: str,
    plan: RunPlan,
    live: LiveRun,
) -> dict[str, Any]:
    """The worker's steps after the implementer: commit, gates, push, pull request."""
    system = plan.attribution.get("system") or plan.provider
    status = run(git(["status", "--porcelain"]), worktree_dir)
    if not status.ok:
        return stop("commit", f"git status failed in {worktree_dir}: {status.output.strip()}")
    if status.output.strip():
        message = swarm_worker.commit_message(cluster, swarm_id=swarm_id, plan=plan)
        for args in (["add", "-A"], ["commit", "--no-verify", "-m", message]):
            step = run(git(args), worktree_dir)
            if not step.ok:
                return stop("commit", f"git {args[0]} failed: {step.output.strip()}")
    head = _last_line(run(git(["rev-parse", "HEAD"]), worktree_dir))
    if not head or head == start:
        return stop(
            "implement",
            f"the implementer {system} changed nothing in the worktree, so there is no work "
            "to commit or push",
        )
    record["commit"] = head
    # A seat may commit its own work, but not replace the branch's history: what keel
    # pushes must descend from the commit the worktree was cut at.
    if not run(git(["merge-base", "--is-ancestor", start, head]), worktree_dir).ok:
        return stop(
            "tamper",
            f"{head} does not descend from {start}, the commit the worktree was cut at; the "
            "branch's history was replaced, so nothing is pushed",
        )

    gates = run(
        [
            sys.executable,
            "-m",
            "keel",
            "run-gates",
            project_yaml,
            "--root",
            str(worktree_dir),
            "--phases",
            swarm_worker.GATE_PHASES,
            "--defer-jury",
        ],
        worktree_dir,
    )
    if not gates.ok:
        return stop(
            "gates",
            f"the project's gates failed at {head}; the branch is not pushed:\n"
            f"{(gates.stdout or gates.output).strip()}",
            code=gates.code,
        )

    # The gates ran the implementer's code, which could have written the git setup too.
    if findings := _tampered(run, worktree_dir, before):
        return stop("tamper", swarm_worker.tamper_reason(findings, during="the gates ran"))

    # To the URL read before the implementer ran, never to the remote's name.
    pushed = live.push(
        git(["push", "--no-verify", push_url, f"{head}:refs/heads/{branch}"]), worktree_dir
    )
    if not pushed.ok:
        return stop("push", f"git push {live.remote} {branch} failed: {pushed.output.strip()}")

    opened = live.open_pr(
        swarm_worker.pull_request_title(cluster, live.issue_scopes, swarm_id=swarm_id),
        swarm_worker.pull_request_body(
            cluster,
            swarm_id=swarm_id,
            branch=branch,
            base_branch=base_branch,
            commit=head,
            plan=plan,
            seat_source=live.seat_sources.get(cluster.cluster_id),
            delegation=live.consent,
        ),
        base_branch,
        branch,
        worktree_dir,
    )
    if not opened.ok:
        return stop(
            "pull_request",
            f"{branch} is pushed, but gh pr create failed: {opened.output.strip()}",
        )
    url = _last_line(opened)
    return {
        **record,
        "ok": True,
        "code": 0,
        "stage": "done",
        "pr_url": url,
        "output": f"pull request opened: {url}",
    }


def run_swarm_orchestration(
    plan: SwarmPlan,
    project_yaml: str,
    *,
    root: str | Path = ".",
    dry_run: bool = True,
    max_workers: int = 4,
    runner: SubprocessRunner | None = None,
    create_worktrees: bool = True,
    base_branch: str,
    timeout_s: int = DEFAULT_RUNNER_TIMEOUT_S,
    live: LiveRun | None = None,
) -> SwarmRunResult:
    """Execute the waves and clusters of a SwarmPlan with fail-soft isolation.

    ``base_branch`` is required, as it is for ``land_wave_clusters``: the worktrees
    are branched from it and the wave is later landed onto it, and a default of
    ``main`` branched a ``develop`` project's clusters off the wrong history (#1262).

    ``timeout_s`` is each worker's gate budget — the child ``keel ship`` of a dry run, the
    ``keel run-gates`` of a live one; ``swarm-run`` passes
    :func:`keel.swarm.worker_timeout_s` or its ``--worker-timeout`` (#1279).

    A live run (``dry_run=False``) needs ``live``: the operator's consent as the parent
    delegated it, and each cluster's planned implementer seat (#1400). Without it nothing
    starts — there is no live worker that runs without delegated consent — and a live
    worker always gets its own worktree, so ``create_worktrees`` must be on.
    """
    if not dry_run and (live is None or not create_worktrees):
        raise ValueError(
            "a live swarm run needs the operator's delegated consent and a worktree per "
            "cluster (#1400)"
        )
    root_path = Path(root).resolve()
    workers_list: list[SwarmWorkerStatus] = []

    # Initialize workers from the plan's own staffing, so the board reports the team the
    # planner resolved rather than the record's placeholder defaults (#1017). A live
    # worker's record also says which consent scopes it was handed (#1400).
    for w in plan.waves:
        for c in w.clusters:
            seed = worker_seed(c, updated_at=datetime.datetime.now(datetime.UTC).isoformat())
            if live is not None:
                seed = replace(seed, scopes=swarm_worker.worker_scopes(live.consent, c.cluster_id))
            workers_list.append(seed)

    state = SwarmRunState(
        swarm_id=plan.swarm_id,
        total_workers=len(workers_list),
        active_wave=1,
        workers=tuple(workers_list),
        started_at=datetime.datetime.now(datetime.UTC).isoformat(),
        consent=None if live is None else live.consent.to_dict(),
    )
    save_swarm_state(state, root=root_path)

    passed_count = 0
    failed_count = 0
    wave_results: list[dict[str, Any]] = []
    current_plan = plan

    # Waves are followed by their index, not by position: a failure makes
    # rebalance_swarm_plan drop a wave, and a position counter over the shrunken
    # plan then stepped past the next, unrelated wave without running it (#1268).
    last_wave: int | None = None
    while True:
        remaining = [w for w in current_plan.waves if last_wave is None or w.wave_index > last_wave]
        if not remaining:
            break
        wave = remaining[0]
        last_wave = wave.wave_index
        state = replace(state, active_wave=wave.wave_index)

        cluster_tasks = list(wave.clusters)
        if not cluster_tasks:
            continue

        wave_record: dict[str, Any] = {
            "wave_index": wave.wave_index,
            "mode": wave.mode,
            "eligible_direct_landing": wave.eligible_direct_landing,
            "cluster_results": {},
        }

        # Mark clusters in this wave as running
        running = "implementing the cluster" if live is not None else "executing ship pipeline"
        for c in cluster_tasks:
            state = update_worker_state(
                state, c.cluster_id, step="s4", status="running", details=running
            )
        save_swarm_state(state, root=root_path)

        def _worker_fn(cluster: Any) -> tuple[str, dict[str, Any]]:
            c_id = cluster.cluster_id
            wt_path = build_worktree_path(plan.swarm_id, c_id, root=root_path)

            # A live worker implements its cluster in a worktree of its own, cut and
            # removed here; the worktree goes, the committed branch stays (#1400).
            if create_worktrees and not dry_run:
                try:
                    return c_id, execute_live_cluster_worker(
                        cluster,
                        swarm_id=plan.swarm_id,
                        root=root_path,
                        worktree_dir=wt_path,
                        project_yaml=project_yaml,
                        base_branch=base_branch,
                        live=live,
                        runner=runner,
                        timeout_s=timeout_s,
                    )
                finally:
                    if wt_path.exists():
                        remove_swarm_worktree(root_path, wt_path, runner=runner)

            # A dry run's worker is an assessment in the operator's checkout.
            return c_id, execute_cluster_worker(
                project_yaml=project_yaml,
                issue=cluster.issues[0] if cluster.issues else 0,
                root=root_path,
                worktree_dir=wt_path,
                dry_run=True,
                role=cluster.role,
                # The lead hands its cluster's team to the child ship. Without this the
                # child re-resolved from config alone and dropped both the difficulty
                # bench and the operator's per-run overrides.
                extra_args=list(ship_handoff_args(cluster.assignment)),
                runner=runner,
                timeout_s=timeout_s,
            )

        # Run wave clusters in parallel thread pool
        # Without a worktree each child runs in the operator's own checkout (a dry run
        # creates none), and its gate suite is not written to share a tree with a
        # sibling's: `.coverage`, `.pytest_cache`, build output. Such children run one
        # at a time; only isolated workers run in parallel (#1288).
        isolated = create_worktrees and not dry_run
        pool_workers = min(max_workers, len(cluster_tasks)) if isolated and cluster_tasks else 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as executor:
            future_to_cluster = {
                executor.submit(_worker_fn, cluster): cluster for cluster in cluster_tasks
            }
            for future in concurrent.futures.as_completed(future_to_cluster):
                try:
                    c_id, worker_res = future.result()
                except Exception as exc:  # noqa: BLE001 - one worker must not end the run
                    # A worker that raised (a malformed assignment, an OSError making
                    # its path) is a failed cluster, not a failed run. Left unguarded,
                    # the raise discarded the other workers' results and froze them
                    # as `running` in the state file (#1271).
                    cluster = future_to_cluster[future]
                    c_id = cluster.cluster_id
                    # The traceback goes to stderr: the failure may be keel's own bug,
                    # and the one-line reason alone would hide where it came from.
                    sys.stderr.write(
                        f"swarm worker {c_id} raised:\n" + "".join(traceback.format_exception(exc))
                    )
                    worker_res = {
                        "issue": cluster.issues[0] if cluster.issues else 0,
                        "role": cluster.role,
                        "ok": False,
                        "code": 1,
                        "output": f"worker raised {type(exc).__name__}: {exc}",
                    }
                # Bounded once, here, where every origin of `output` — the child's
                # stdout, a worktree failure, a raised worker — meets both places it is
                # kept: this wave record and, on failure, the state file's `details`
                # (#1280).
                worker_res = {**worker_res, "output": tail_child_output(worker_res["output"])}
                wave_record["cluster_results"][c_id] = worker_res
                issue_val = worker_res.get("issue", 0)

                if worker_res.get("ok", False):
                    passed_count += 1
                    # A live worker stops at an open pull request, which CI (s6) and review
                    # take from there; a dry assessment ran the whole backbone.
                    state = update_worker_state(
                        state,
                        c_id,
                        step="s10" if live is None else "s6",
                        status="passed",
                        details="pipeline completed" if live is None else worker_res["output"],
                        # The pull request `swarm-land` merges (#1287); a dry run opens none.
                        pull_request=pull_request_number(str(worker_res.get("pr_url") or "")),
                    )
                else:
                    failed_count += 1
                    state = update_worker_state(
                        state,
                        c_id,
                        step="s4",
                        status="failed",
                        details=worker_res.get("output", ""),
                    )
                    # Dynamically rebalance subsequent waves if needed
                    current_plan = rebalance_swarm_plan(current_plan, issue_val)

                save_swarm_state(state, root=root_path)

        wave_results.append(wave_record)

    # Finalize state
    overall_status = (
        "success"
        if failed_count == 0 and passed_count > 0
        else ("partial_failure" if passed_count > 0 else "failed")
    )
    if passed_count == 0 and failed_count == 0:
        overall_status = "success"

    state = replace(state, completed_at=datetime.datetime.now(datetime.UTC).isoformat())
    save_swarm_state(state, root=root_path)

    return SwarmRunResult(
        swarm_id=plan.swarm_id,
        status=overall_status,
        total_workers=len(workers_list),
        passed_count=passed_count,
        failed_count=failed_count,
        dry_run=dry_run,
        wave_results=tuple(wave_results),
        consent=state.consent,
    )
