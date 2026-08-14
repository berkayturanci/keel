"""Keel Swarm Landing — Orthogonal batch landing and drift self-healing merge engine.

Thin I/O execution layer for evaluating branch disjointness, merging orthogonal diff trees
under atomic merge locks, and automatically rebasing / healing drifted sequential clusters.
"""

from __future__ import annotations

from pathlib import Path

from .lock import merge_lock
from .swarm import (
    SwarmLandingResult,
    SwarmPlan,
    SwarmWave,
    evaluate_wave_landing_mode,
    load_swarm_state,
    save_swarm_state,
    update_worker_state,
)
from .swarm_runtime import SubprocessRunner, default_runner


def rebase_and_heal_cluster_branch(
    repo_root: Path,
    branch_name: str,
    base_branch: str = "main",
    runner: SubprocessRunner | None = None,
) -> tuple[bool, str]:
    """Rebase a cluster branch onto base branch, with fail-soft abort on conflict."""
    run = runner or default_runner
    # Checkout branch
    run(["git", "checkout", branch_name], repo_root)
    # Attempt rebase
    res = run(["git", "rebase", f"origin/{base_branch}"], repo_root)
    if res.ok:
        return True, "clean_rebase"

    # Fail soft: abort rebase cleanly so git workspace remains in valid state
    run(["git", "rebase", "--abort"], repo_root)
    return False, "conflict_detected"


def merge_cluster_branch(
    repo_root: Path,
    branch_name: str,
    base_branch: str = "main",
    runner: SubprocessRunner | None = None,
) -> bool:
    """Merge a cluster branch into base branch."""
    run = runner or default_runner
    run(["git", "checkout", base_branch], repo_root)
    cmd = ["git", "merge", "--no-ff", branch_name, "-m", f"Merge branch {branch_name}"]
    res = run(cmd, repo_root)
    return res.ok


def land_wave_clusters(
    plan: SwarmPlan,
    wave_index: int,
    project_yaml: str,
    *,
    root: str | Path = ".",
    dry_run: bool = True,
    pr_diff_map: dict[str, list[str] | tuple[str, ...]] | None = None,
    runner: SubprocessRunner | None = None,
) -> SwarmLandingResult:
    """Execute orthogonal batch landing or adaptive sequential funneling for a wave."""
    root_path = Path(root).resolve()
    target_wave: SwarmWave | None = None
    for w in plan.waves:
        if w.wave_index == wave_index:
            target_wave = w
            break

    if target_wave is None:
        return SwarmLandingResult(
            swarm_id=plan.swarm_id,
            wave_index=wave_index,
            mode="none",
            landed_clusters=(),
            healed_clusters=(),
            failed_clusters=(),
            status="failed",
        )

    # Evaluate actual diff files vs predicted
    diff_map = pr_diff_map or {}
    decision = evaluate_wave_landing_mode(target_wave, diff_map)

    landed: list[str] = []
    healed: list[str] = []
    failed: list[str] = []

    state = load_swarm_state(plan.swarm_id, root=root_path)

    if dry_run:
        # Dry run simulation
        for c in target_wave.clusters:
            landed.append(c.cluster_id)
        return SwarmLandingResult(
            swarm_id=plan.swarm_id,
            wave_index=wave_index,
            mode=decision.mode,
            landed_clusters=tuple(landed),
            healed_clusters=(),
            failed_clusters=(),
            status="success",
        )

    # Live landing protected by atomic merge lock
    lock_path = root_path / ".keel" / "state" / "merge.lock"
    with merge_lock(lock_path):
        for c in target_wave.clusters:
            branch_name = f"swarm/{plan.swarm_id}/{c.cluster_id}"
            if decision.mode == "direct_batch":
                ok = merge_cluster_branch(root_path, branch_name, runner=runner)
                if ok:
                    landed.append(c.cluster_id)
                    if state:
                        state = update_worker_state(
                            state, c.cluster_id, step="s10", status="merged"
                        )
                else:
                    failed.append(c.cluster_id)
                    if state:
                        state = update_worker_state(
                            state, c.cluster_id, step="s10", status="failed", details="merge failed"
                        )
            else:
                # Sequential funnel with rebase & heal
                rebase_ok, reason = rebase_and_heal_cluster_branch(
                    root_path, branch_name, runner=runner
                )
                if rebase_ok:
                    healed.append(c.cluster_id)
                    merge_ok = merge_cluster_branch(root_path, branch_name, runner=runner)
                    if merge_ok:
                        landed.append(c.cluster_id)
                        if state:
                            state = update_worker_state(
                                state, c.cluster_id, step="s10", status="merged"
                            )
                    else:
                        failed.append(c.cluster_id)
                        if state:
                            state = update_worker_state(
                                state,
                                c.cluster_id,
                                step="s10",
                                status="failed",
                                details="post-rebase merge failed",
                            )
                else:
                    failed.append(c.cluster_id)
                    if state:
                        state = update_worker_state(
                            state,
                            c.cluster_id,
                            step="s10",
                            status="failed",
                            details=f"rebase conflict: {reason}",
                        )

    if state:
        save_swarm_state(state, root=root_path)

    overall_status = (
        "success"
        if len(failed) == 0 and len(landed) > 0
        else ("partial_failure" if len(landed) > 0 else "failed")
    )

    return SwarmLandingResult(
        swarm_id=plan.swarm_id,
        wave_index=wave_index,
        mode=decision.mode,
        landed_clusters=tuple(landed),
        healed_clusters=tuple(healed),
        failed_clusters=tuple(failed),
        status=overall_status,
    )
