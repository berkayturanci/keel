"""Keel Swarm Runtime — Isolated multi-worktree execution & cluster orchestration.

Thin I/O execution layer for running parallel swarm workers in isolated Git worktrees,
handling worker state persistence, and managing fail-soft rebalancing across waves.

A dry run's worker is a ``keel ship --dry-run`` assessment per cluster. A live run's worker
(#1400) is the cluster's implementer seat, dispatched through :mod:`keel.delegaterun` in
the cluster's own worktree, followed by a commit, the project's gates, a push and one pull
request, stamped with the ship-provenance comment a live ``keel ship`` run posts on its own
— every mutation behind the consent scopes the parent delegated
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
import threading
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
    load_swarm_state,
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
#: ``(owner_repo, number, body, cwd)`` -> the result of posting ``body`` on that pull request.
CommentPoster = Callable[[str, int, str, Path], CommandResult]

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


#: Called with each :data:`keel.swarm_worker.STAGES` entry as a live worker enters it.
StageReporter = Callable[[str], None]


def _no_stage(_stage: str) -> None:
    """The stage reporter of a worker nobody watches."""


def _now() -> str:
    """The current UTC time, ISO 8601 — the timestamps a worker record carries."""
    return datetime.datetime.now(datetime.UTC).isoformat()


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


def _default_post_comment(owner_repo: str, number: int, body: str, cwd: Path) -> CommandResult:
    """The call ``keel post-comment`` makes for a live ship run's provenance, with the
    operator's environment — so the comment's author is the account keel's ``gh`` is."""
    return github.post_issue_comment(owner_repo, number, body, cwd=str(cwd))


@dataclass(frozen=True)
class LiveRun:
    """What a live run's parent hands its workers: the operator's consent, delegated, and
    each cluster's planned implementer seat. The four callables are the I/O seams."""

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
    post_comment: CommentPoster = _default_post_comment


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
    """Remove a previously created isolated git worktree; ``True`` only when it is gone.

    ``git worktree remove --force`` first. Whatever it leaves on disk is removed with
    ``rmtree``, and when git refused, ``git worktree prune`` then drops the registration of
    a directory that is now gone — a registration left behind makes the next ``git
    worktree add`` of the same branch fail. The answer is the directory's own state
    afterwards: this used to return ``True`` unconditionally, even when nothing was
    removed (#1278).
    """
    run = runner or default_runner
    res = run(["git", "worktree", "remove", "--force", str(worktree_path)], repo_root)
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)
    if not res.ok:
        run(["git", "worktree", "prune"], repo_root)
    return not worktree_path.exists()


def prune_worktrees(repo_root: Path, runner: SubprocessRunner | None = None) -> str:
    """Run ``git worktree prune``; ``""``, or why it failed (#1278).

    It drops the registration of every worktree whose directory is gone — git's own
    ``gc`` does the same after ``gc.worktreePruneExpire`` — and touches no directory, no
    branch, and no worktree that is locked.
    """
    res = (runner or default_runner)(["git", "worktree", "prune"], repo_root)
    return "" if res.ok else (res.output.strip() or f"exit {res.code}")


def remove_empty_swarm_dirs(root: Path, swarm_id: str) -> None:
    """Remove ``.keel/worktrees/<swarm_id>/``, then ``.keel/worktrees/``, when each is empty.

    ``rmdir`` only — a directory with anything in it (a sibling worker's worktree, one kept
    for inspection) stays, and so does a symlink (#1278).
    """
    base = Path(root) / ".keel" / "worktrees"
    for directory in (base / swarm_id, base):
        try:
            # Refuses a directory with anything in it, a missing one, and a symlink.
            directory.rmdir()
        except OSError:
            continue


def settle_live_worktree(
    root: Path,
    worktree_path: Path,
    branch: str,
    disposal: swarm_worker.WorktreeDisposal,
    runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Carry out a :class:`keel.swarm_worker.WorktreeDisposal`, and say what happened.

    Returns the keys a worker's result gains: ``worktree`` (where it is still on disk, or
    ``None``), ``worktree_state`` (``removed``, ``kept``, ``remove-failed`` or ``none``),
    ``branch_deleted`` and ``warnings`` — so a removal that failed is recorded, not
    swallowed (#1278).
    """
    warnings: list[str] = []
    branch_deleted = False
    state = "kept" if disposal.worktree == "keep" else "none"
    if disposal.worktree == "remove":
        removed = remove_swarm_worktree(root, worktree_path, runner=runner)
        if not removed:
            warnings.append(
                f"the worktree {worktree_path} could not be removed; "
                "`keel swarm-status --clean` retries it"
            )
        elif disposal.delete_branch:
            deleted = (runner or default_runner)(["git", "branch", "-D", branch], root)
            branch_deleted = deleted.ok
            if not deleted.ok:
                warnings.append(
                    f"the branch {branch} could not be deleted: {deleted.output.strip()}"
                )
        state = "removed" if removed else "remove-failed"
    return {
        # A kept worktree, or one whose removal failed (`remove_swarm_worktree` answers
        # from the directory's own state, so it is still there).
        "worktree": str(worktree_path) if state in ("kept", "remove-failed") else None,
        "worktree_state": state,
        "branch_deleted": branch_deleted,
        "warnings": warnings,
    }


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
    progress: dict[str, bool] | None = None,
    on_stage: StageReporter = _no_stage,
) -> dict[str, Any]:
    """Implement one cluster live: its implementer seat, a commit, the gates, a push, a PR.

    ``progress``, when given, is filled in as the worker goes — ``worktree_created`` once
    its worktree exists, ``implementer_ran`` once the seat is started — so a caller can
    settle the worktree correctly even when the worker raises part-way (#1278).

    ``on_stage`` is called with each stage of :data:`keel.swarm_worker.STAGES` as the
    worker enters it, so ``keel swarm-status`` can show how far a worker has got while it
    runs (#1280); the stage it ends at is the result's ``stage``.

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
    push, pull request and provenance comment — made here, in keel's process, after the
    implementer has exited — reach the remote.

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
        "pushed": False,
        # Whether the pull request carries the ship-provenance comment that arms keel
        # merge's evidence gate; an open pull request without it is held at landing.
        "provenance_posted": False,
    }
    progress = {} if progress is None else progress

    def stop(stage: str, reason: str, code: int = 1) -> dict[str, Any]:
        return {**record, "ok": False, "code": code, "stage": stage, "output": reason}

    on_stage("consent")
    if why := swarm_worker.consent_refusal(scopes):
        return stop("consent", why)
    # A registration whose directory is gone — a crashed run's, after its directory was
    # deleted — makes `git worktree add -B` of the same branch fail, so it is dropped
    # first (#1278). A sibling's worktree still being added is locked, and prune skips it.
    on_stage("worktree")
    pruned = prune_worktrees(root, runner=run)
    created = create_swarm_worktree(root, worktree_dir, branch, base_branch=base_branch, runner=run)
    if not created or not worktree_dir.exists():
        # Most often a previous run of this --swarm-id left its worktree or branch behind.
        prune_note = f" (git worktree prune failed first: {pruned})" if pruned else ""
        return stop(
            "worktree",
            f"failed to create isolated worktree at {worktree_dir}{prune_note}; if a "
            f"previous run of this swarm left it behind, `keel swarm-status <project.yaml> "
            f"--swarm-id {swarm_id} --clean` removes it",
        )
    progress["worktree_created"] = True
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

    on_stage("implement")
    brief = Path(plan.prompt_path)
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(
        swarm_worker.render_brief(
            cluster, live.issue_scopes, swarm_id=swarm_id, branch=branch, base_branch=base_branch
        ),
        encoding="utf-8",
    )
    gh_config = build_gh_config_path(swarm_id, cluster.cluster_id, root)
    # From here the worktree holds what the seat did, and a failure keeps it (#1278).
    progress["implementer_ran"] = True
    result = live.implement(
        plan, swarm_worker.implementer_env(os.environ, gh_config_dir=str(gh_config))
    )
    if not result.get("ok"):
        return stop(
            "implement",
            f"the implementer {system} failed ({result.get('error_code')}): {result.get('error')}",
        )

    on_stage("tamper")
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
            on_stage=on_stage,
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
    on_stage: StageReporter,
) -> dict[str, Any]:
    """The worker's steps after the implementer: commit, gates, push, pull request, and the
    pull request's ship-provenance comment."""
    system = plan.attribution.get("system") or plan.provider
    on_stage("commit")
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

    on_stage("gates")
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
    on_stage("push")
    pushed = live.push(
        git(["push", "--no-verify", push_url, f"{head}:refs/heads/{branch}"]), worktree_dir
    )
    if not pushed.ok:
        return stop("push", f"git push {live.remote} {branch} failed: {pushed.output.strip()}")
    record["pushed"] = True

    on_stage("pull_request")
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
    # A keel-made pull request arms keel merge's evidence gate at creation, as a live
    # `keel ship` run's does: its branch matches no ship-branch pattern, and without the
    # stamp `swarm-land` is held on "evidence gate is not enforced" (found on the first
    # end-to-end live run). Posted by keel, with the operator's credentials, after the
    # implementer exited — like the push and the pull request.
    missing = _post_provenance(
        live,
        url,
        swarm_worker.ship_provenance_body(cluster, swarm_id=swarm_id, commit=head, plan=plan),
        worktree_dir,
    )
    done = {
        **record,
        "ok": True,
        "code": 0,
        "stage": "done",
        "pr_url": url,
        "provenance_posted": not missing,
        "output": f"pull request opened: {url}",
    }
    if missing:
        # Not a failed worker: the pull request is open and stays open. It is held at
        # landing until the stamp or its review verdicts are posted, so the run says so.
        done["output"] += f"\n{missing}"
        done["warnings"] = [missing]
    return done


def _post_provenance(live: LiveRun, url: str, body: str, cwd: Path) -> str:
    """Post ``body`` on the pull request at ``url``; ``""`` once posted, else the warning."""
    owner_repo = swarm_worker.pull_request_repo(url)
    number = pull_request_number(url)
    if owner_repo is None or number is None:
        why = "gh pr create printed no pull request URL keel can read"
    else:
        posted = live.post_comment(owner_repo, number, body, cwd)
        if posted.ok:
            return ""
        why = f"the comment post failed: {posted.output.strip() or 'no output'}"
    return swarm_worker.provenance_warning(url or "the pull request", why)


def _swarm_ids_under(base: Path, path: str) -> tuple[str, str] | None:
    """``(swarm_id, cluster_id)`` of a worktree path exactly two levels under ``base``."""
    try:
        parts = Path(path).resolve().relative_to(base).parts
    except ValueError:
        return None
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _run_record(swarm_id: str, root: Path) -> swarm_worker.SwarmRunRecord | None:
    """What ``swarm_id``'s state file says about its leftovers; ``None`` with no file."""
    state = load_swarm_state(swarm_id, root=root)
    if state is None:
        exists = (root / ".keel" / "state" / "swarm" / f"{swarm_id}.json").exists()
        # A state file keel cannot read may belong to a running run.
        return swarm_worker.SwarmRunRecord(unfinished=True) if exists else None
    return swarm_worker.SwarmRunRecord(
        unfinished=state.completed_at is None,
        pull_requests={
            w.cluster_id: w.pull_request for w in state.workers if w.pull_request is not None
        },
        pushed=frozenset(w.cluster_id for w in state.workers if w.pushed),
    )


def find_swarm_leftovers(
    root: str | Path,
    *,
    swarm_id: str | None = None,
    runner: SubprocessRunner | None = None,
) -> tuple[tuple[swarm_worker.SwarmLeftover, ...], str]:
    """Every worktree, directory and branch a swarm run left behind, and what to do (#1278).

    Looks only under keel's own paths — worktrees registered, or directories present,
    exactly at ``.keel/worktrees/<swarm_id>/<cluster_id>`` — and its own branch namespace,
    ``swarm/<swarm_id>/<cluster_id>``; ``swarm_id`` narrows it to one run.
    :func:`keel.swarm_worker.classify_leftovers` decides what may be removed. Returns
    ``(leftovers, "")``, or ``((), why)`` when git could not list them.
    """
    root_path = Path(root).resolve()
    run = runner or default_runner
    base = root_path / ".keel" / "worktrees"
    listed = run(["git", "worktree", "list", "--porcelain"], root_path)
    refs = run(["git", "for-each-ref", "--format=%(refname)", "refs/heads/swarm/"], root_path)
    if not listed.ok or not refs.ok:
        failed = listed if not listed.ok else refs
        return (), f"git could not list the worktrees or branches: {failed.output.strip()}"

    worktrees: list[tuple[str, str, str, bool]] = []
    entries = swarm_worker.parse_worktree_list(listed.stdout or listed.output)
    for entry in entries:
        ids = _swarm_ids_under(base, entry.path)
        if ids is not None:
            # In the platform's own spelling: git prints `C:/…` on Windows.
            worktrees.append((*ids, str(Path(entry.path).resolve()), entry.prunable))
    # Every worktree registered under `.keel/worktrees/`, of keel's shape or not: a
    # directory that is one, holds one or sits inside one is never taken for a leftover
    # directory. (The main worktree holds all of it, so only those below the base count.)
    resolved = (Path(entry.path).resolve() for entry in entries)
    registered = [p for p in resolved if base in p.parents]

    def unregistered(path: Path) -> bool:
        return not any(p == path or p in path.parents or path in p.parents for p in registered)

    directories: list[tuple[str, str, str]] = []
    runs_dirs = sorted(base.iterdir()) if base.is_dir() and not base.is_symlink() else []
    for run_dir in runs_dirs:
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        children = sorted(run_dir.iterdir())
        if not children:
            directories.append((run_dir.name, "", str(run_dir)))
        for child in children:
            if child.is_dir() and not child.is_symlink() and unregistered(child):
                directories.append((run_dir.name, child.name, str(child)))

    branches: dict[str, str] = {}  # branch -> its run
    for line in (refs.stdout or refs.output).splitlines():
        ids = swarm_worker.swarm_branch_ids(line.strip())
        if ids is not None:
            branches[line.strip()] = ids[0]
    if swarm_id is not None:
        worktrees = [w for w in worktrees if w[0] == swarm_id]
        directories = [d for d in directories if d[0] == swarm_id]
        branches = {b: sid for b, sid in branches.items() if sid == swarm_id}
    seen = {w[0] for w in worktrees} | {d[0] for d in directories} | set(branches.values())
    runs = {sid: record for sid in sorted(seen) if (record := _run_record(sid, root_path))}
    leftovers = swarm_worker.classify_leftovers(
        worktrees=worktrees,
        directories=directories,
        branches=branches,
        runs=runs,
        named=swarm_id,
    )
    return leftovers, ""


def clean_swarm_leftovers(
    root: str | Path,
    leftovers: tuple[swarm_worker.SwarmLeftover, ...],
    *,
    runner: SubprocessRunner | None = None,
) -> tuple[list[swarm_worker.SwarmLeftover], list[tuple[swarm_worker.SwarmLeftover, str]]]:
    """Remove every leftover marked ``remove``; ``(removed, [(failed, why)])`` (#1278).

    Registrations go with ``git worktree prune``, worktrees with
    :func:`remove_swarm_worktree`, unregistered directories with ``rmtree`` — before the
    branches, which git will not delete while a worktree has them checked out — and each
    run directory left empty with ``rmdir``. Nothing marked ``keep`` is touched.
    """
    root_path = Path(root).resolve()
    run = runner or default_runner
    todo = [x for x in leftovers if x.action == "remove"]
    removed: list[swarm_worker.SwarmLeftover] = []
    failed: list[tuple[swarm_worker.SwarmLeftover, str]] = []

    def settle(item: swarm_worker.SwarmLeftover, gone: bool, why: str) -> None:
        if gone:
            removed.append(item)
        else:
            failed.append((item, why))

    registrations = [x for x in todo if x.kind == "registration"]
    if registrations:
        why = prune_worktrees(root_path, runner=run)
        for item in registrations:
            settle(item, not why, f"git worktree prune failed: {why}")
    for item in todo:
        if item.kind == "worktree":
            gone = remove_swarm_worktree(root_path, Path(item.target), runner=run)
            settle(item, gone, "the directory is still there")
        elif item.kind == "directory" and item.cluster_id:
            shutil.rmtree(item.target, ignore_errors=True)
            settle(item, not Path(item.target).exists(), "the directory is still there")
    for item in todo:
        if item.kind == "branch":
            deleted = run(["git", "branch", "-D", item.target], root_path)
            settle(item, deleted.ok, deleted.output.strip() or f"exit {deleted.code}")
    for swarm_id in sorted({x.swarm_id for x in todo}):
        remove_empty_swarm_dirs(root_path, swarm_id)
    for item in todo:
        if item.kind == "directory" and not item.cluster_id:
            settle(item, not Path(item.target).exists(), "the directory is not empty")
    return removed, failed


def _disposal(ok: bool, progress: Mapping[str, bool]) -> swarm_worker.WorktreeDisposal:
    return swarm_worker.worktree_disposal(
        ok=ok,
        worktree_created=progress.get("worktree_created", False),
        implementer_ran=progress.get("implementer_ran", False),
    )


def _with_settlement(
    outcome: dict[str, Any], settled: dict[str, Any], branch: str
) -> dict[str, Any]:
    """A live worker's result with what became of its worktree; a kept worktree is named
    at the end of ``output``, where the tail the swarm keeps still holds it (#1278)."""
    output = outcome.get("output", "")
    if settled["worktree_state"] == "kept":
        output = (
            f"{output}\nthe worktree is kept for inspection at {settled['worktree']} (branch "
            f"{branch}); `keel swarm-status <project.yaml> --clean` removes it"
        )
    # The worker's own warnings (an unstamped pull request) come first, then the settle's:
    # a plain merge would let the settle's list replace the worker's.
    warnings = [*outcome.get("warnings", ()), *settled["warnings"]]
    return {**outcome, **settled, "output": output, "warnings": warnings}


def run_swarm_orchestration(
    plan: SwarmPlan,
    project_yaml: str,
    *,
    root: str | Path = ".",
    dry_run: bool = True,
    max_workers: int = 4,
    runner: SubprocessRunner | None = None,
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
    starts — there is no live worker that runs without delegated consent. A live worker
    always gets its own worktree, and a dry run's never does (#1288); the
    ``create_worktrees`` switch that used to sit beside ``dry_run`` could only ever agree
    with it, and is gone (#1280).

    ``runner`` is an injection seam for tests, like the ``_run`` seams elsewhere in keel:
    ``swarm-run`` never passes it. It replaces the subprocess runner of every git command,
    gate run and dry-run child ``keel ship`` a worker makes; left out, each runs through
    :func:`default_runner` under ``timeout_s``.

    Each worker's record carries its wave, when it started and ended, and — a live worker
    — the stage it is in, written as it enters it, so ``keel swarm-status`` shows how far a
    run has got while it is in flight (#1280). A worker is ``running`` from the moment it
    starts, not from the moment its wave does: a dry run's workers take turns.
    """
    if not dry_run and live is None:
        raise ValueError("a live swarm run needs the operator's delegated consent (#1400)")
    root_path = Path(root).resolve()
    workers_list: list[SwarmWorkerStatus] = []

    # Initialize workers from the plan's own staffing, so the board reports the team the
    # planner resolved rather than the record's placeholder defaults (#1017). A live
    # worker's record also says which consent scopes it was handed (#1400).
    for w in plan.waves:
        for c in w.clusters:
            seed = worker_seed(c, wave=w.wave_index, updated_at=_now())
            if live is not None:
                seed = replace(seed, scopes=swarm_worker.worker_scopes(live.consent, c.cluster_id))
            workers_list.append(seed)

    state = SwarmRunState(
        swarm_id=plan.swarm_id,
        total_workers=len(workers_list),
        active_wave=1,
        workers=tuple(workers_list),
        started_at=_now(),
        consent=None if live is None else live.consent.to_dict(),
    )
    save_swarm_state(state, root=root_path)

    # Live workers report from their own threads while the loop below records the ones that
    # finished, so every update of the run's state — and its save — is made under one lock.
    state_lock = threading.Lock()

    def report(cluster_id: str, **fields: Any) -> None:
        nonlocal state
        with state_lock:
            state = update_worker_state(state, cluster_id, **fields)
            save_swarm_state(state, root=root_path)

    def _enter_stage(cluster_id: str, stage: str) -> None:
        report(cluster_id, stage=stage)

    passed_count = 0
    failed_count = 0
    wave_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    current_plan = plan

    # Waves are followed by their index, not by position: a failure makes
    # rebalance_swarm_plan drop a wave, and a position counter over the shrunken
    # plan then stepped past the next, unrelated wave without running it (#1268).
    last_wave: int | None = None
    running = "implementing the cluster" if live is not None else "executing ship pipeline"
    while True:
        remaining = [w for w in current_plan.waves if last_wave is None or w.wave_index > last_wave]
        if not remaining:
            break
        wave = remaining[0]
        last_wave = wave.wave_index
        # No worker thread runs between waves, so this update needs no lock.
        state = replace(state, active_wave=wave.wave_index)

        cluster_tasks = list(wave.clusters)
        if not cluster_tasks:
            continue
        save_swarm_state(state, root=root_path)

        wave_record: dict[str, Any] = {
            "wave_index": wave.wave_index,
            "mode": wave.mode,
            "eligible_direct_landing": wave.eligible_direct_landing,
            "cluster_results": {},
        }

        def _worker_fn(cluster: Any) -> tuple[str, dict[str, Any]]:
            c_id = cluster.cluster_id
            wt_path = build_worktree_path(plan.swarm_id, c_id, root=root_path)
            # Running from when this worker starts, not when its wave did: a dry run's
            # workers run one at a time, and the rest are still queued (#1280).
            report(c_id, step="s4", status="running", details=running, started_at=_now())

            # A live worker implements its cluster in a worktree of its own, cut here and
            # settled here, whichever way the worker ends — a returned failure or a raise
            # (#1400, #1278): see `swarm_worker.worktree_disposal` for what stays.
            if not dry_run:
                branch = cluster_branch(plan.swarm_id, c_id)
                progress: dict[str, bool] = {}
                try:
                    outcome = execute_live_cluster_worker(
                        cluster,
                        swarm_id=plan.swarm_id,
                        root=root_path,
                        worktree_dir=wt_path,
                        project_yaml=project_yaml,
                        base_branch=base_branch,
                        live=live,
                        runner=runner,
                        timeout_s=timeout_s,
                        progress=progress,
                        on_stage=functools.partial(_enter_stage, c_id),
                    )
                except BaseException:
                    # What the seat touched is kept for inspection; nothing is lost to
                    # a crash in keel's own code.
                    settle_live_worktree(
                        root_path, wt_path, branch, _disposal(False, progress), runner
                    )
                    raise
                else:
                    settled = settle_live_worktree(
                        root_path,
                        wt_path,
                        branch,
                        _disposal(bool(outcome.get("ok")), progress),
                        runner,
                    )
                    return c_id, _with_settlement(outcome, settled, branch)

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
        pool_workers = min(max_workers, len(cluster_tasks)) if not dry_run else 1
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
                    # A raising live worker keeps what its seat touched (#1278).
                    kept = build_worktree_path(plan.swarm_id, c_id, root=root_path)
                    if not dry_run and kept.exists():
                        worker_res["worktree"] = str(kept)
                        worker_res["output"] += (
                            f"\nthe worktree is kept for inspection at {kept}; "
                            "`keel swarm-status <project.yaml> --clean` removes it"
                        )
                # Bounded once, here, where every origin of `output` — the child's
                # stdout, a worktree failure, a raised worker — meets both places it is
                # kept: this wave record and, on failure, the state file's `details`
                # (#1280).
                worker_res = {**worker_res, "output": tail_child_output(worker_res["output"])}
                wave_record["cluster_results"][c_id] = worker_res
                issue_val = worker_res.get("issue", 0)
                warnings.extend(f"{c_id}: {w}" for w in worker_res.get("warnings", ()))
                # Where the worker's worktree still is, and whether its branch was pushed:
                # what `keel swarm-status --clean` reads (#1278).
                left = {
                    "worktree": worker_res.get("worktree") or "",
                    "pushed": bool(worker_res.get("pushed")),
                }

                # The stage a live worker ended at: `done`, or the one that stopped it. A
                # dry run's worker, or one that raised, reports none, and keeps the last
                # stage it entered (#1280).
                ended = {"stage": worker_res.get("stage") or None, "finished_at": _now()}
                if worker_res.get("ok", False):
                    passed_count += 1
                    # A live worker stops at an open pull request, which CI (s6) and review
                    # take from there; a dry assessment ran the whole backbone.
                    report(
                        c_id,
                        step="s10" if live is None else "s6",
                        status="passed",
                        details="pipeline completed" if live is None else worker_res["output"],
                        # The pull request `swarm-land` merges (#1287); a dry run opens none.
                        pull_request=pull_request_number(str(worker_res.get("pr_url") or "")),
                        **left,
                        **ended,
                    )
                else:
                    failed_count += 1
                    report(
                        c_id,
                        step="s4",
                        status="failed",
                        details=worker_res.get("output", ""),
                        **left,
                        **ended,
                    )
                    # Dynamically rebalance subsequent waves if needed
                    current_plan = rebalance_swarm_plan(current_plan, issue_val)

        # Once the wave's workers are done, the run's directory goes when nothing is left
        # in it — it used to accumulate, one per run (#1278).
        if not dry_run:
            remove_empty_swarm_dirs(root_path, plan.swarm_id)
        wave_results.append(wave_record)

    # Finalize state
    overall_status = (
        "success"
        if failed_count == 0 and passed_count > 0
        else ("partial_failure" if passed_count > 0 else "failed")
    )
    if passed_count == 0 and failed_count == 0:
        overall_status = "success"

    state = replace(state, completed_at=_now())
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
        warnings=tuple(warnings),
    )
