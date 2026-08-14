"""Keel Swarm Runtime — Isolated multi-worktree execution & cluster orchestration.

Thin I/O execution layer for running parallel swarm workers in isolated Git worktrees,
dispatching keel ship jobs, handling worker state persistence, and managing fail-soft
rebalancing across waves.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .runner import CommandResult
from .swarm import (
    SwarmPlan,
    SwarmRunResult,
    SwarmRunState,
    SwarmWorkerStatus,
    rebalance_swarm_plan,
    save_swarm_state,
    update_worker_state,
)

SubprocessRunner = Callable[[list[str], Path], CommandResult]


def default_runner(cmd: list[str], cwd: Path) -> CommandResult:
    """Run a subprocess command in cwd and return a CommandResult."""
    try:
        proc = subprocess.run(  # nosec B603
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
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
) -> dict[str, Any]:
    """Execute a single cluster worker pipeline (keel ship) within its worktree."""
    run = runner or default_runner
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
    if dry_run:
        cmd.append("--dry-run")
    if extra_args:
        cmd.extend(extra_args)

    target_cwd = worktree_dir if worktree_dir.exists() else root
    res = run(cmd, target_cwd)

    return {
        "issue": issue,
        "role": role,
        "ok": res.ok,
        "code": res.code,
        "output": res.stdout or res.output,
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
) -> SwarmRunResult:
    """Execute the waves and clusters of a SwarmPlan with fail-soft isolation."""
    root_path = Path(root).resolve()
    workers_list: list[SwarmWorkerStatus] = []

    # Initialize workers
    for w in plan.waves:
        for c in w.clusters:
            issue_num = c.issues[0] if c.issues else 0
            workers_list.append(
                SwarmWorkerStatus(
                    cluster_id=c.cluster_id,
                    issue=issue_num,
                    role=c.role,
                    step="s0",
                    status="queued",
                    updated_at=datetime.datetime.now(datetime.UTC).isoformat(),
                )
            )

    state = SwarmRunState(
        swarm_id=plan.swarm_id,
        total_workers=len(workers_list),
        active_wave=1,
        workers=tuple(workers_list),
        started_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    save_swarm_state(state, root=root_path)

    passed_count = 0
    failed_count = 0
    wave_results: list[dict[str, Any]] = []
    current_plan = plan

    for wave in current_plan.waves:
        state = SwarmRunState(
            swarm_id=state.swarm_id,
            total_workers=state.total_workers,
            active_wave=wave.wave_index,
            workers=state.workers,
            started_at=state.started_at,
        )

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
        for c in cluster_tasks:
            state = update_worker_state(
                state, c.cluster_id, step="s4", status="running", details="executing ship pipeline"
            )
        save_swarm_state(state, root=root_path)

        def _worker_fn(cluster: Any) -> tuple[str, dict[str, Any]]:
            c_id = cluster.cluster_id
            issue_n = cluster.issues[0] if cluster.issues else 0
            wt_path = build_worktree_path(plan.swarm_id, c_id, root=root_path)

            if create_worktrees and not dry_run:
                branch_name = f"swarm/{plan.swarm_id}/{c_id}"
                create_swarm_worktree(root_path, wt_path, branch_name, runner=runner)

            res = execute_cluster_worker(
                project_yaml=project_yaml,
                issue=issue_n,
                root=root_path,
                worktree_dir=wt_path,
                dry_run=dry_run,
                role=cluster.role,
                runner=runner,
            )

            if create_worktrees and not dry_run:
                remove_swarm_worktree(root_path, wt_path, runner=runner)

            return c_id, res

        # Run wave clusters in parallel thread pool
        pool_workers = min(max_workers, len(cluster_tasks)) if len(cluster_tasks) > 0 else 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as executor:
            future_to_cluster = {
                executor.submit(_worker_fn, cluster): cluster for cluster in cluster_tasks
            }
            for future in concurrent.futures.as_completed(future_to_cluster):
                c_id, worker_res = future.result()
                wave_record["cluster_results"][c_id] = worker_res
                issue_val = worker_res.get("issue", 0)

                if worker_res.get("ok", False):
                    passed_count += 1
                    state = update_worker_state(
                        state, c_id, step="s10", status="passed", details="pipeline completed"
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

    state = SwarmRunState(
        swarm_id=state.swarm_id,
        total_workers=state.total_workers,
        active_wave=state.active_wave,
        workers=state.workers,
        started_at=state.started_at,
        completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    save_swarm_state(state, root=root_path)

    return SwarmRunResult(
        swarm_id=plan.swarm_id,
        status=overall_status,
        total_workers=len(workers_list),
        passed_count=passed_count,
        failed_count=failed_count,
        dry_run=dry_run,
        wave_results=tuple(wave_results),
    )
