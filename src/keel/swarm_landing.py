"""Keel Swarm Landing — Orthogonal batch landing and drift self-healing merge engine.

Thin I/O execution layer for evaluating branch disjointness, merging orthogonal diff trees
under atomic merge locks, and automatically rebasing / healing drifted sequential clusters.
"""

from __future__ import annotations

from collections.abc import Callable
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


def parse_conflict_hunks(text: str) -> list[dict[str, str]]:
    """Parse standard git conflict markers (<<<<<<<, =======, >>>>>>>) into hunks."""
    lines = text.splitlines(keepends=True)
    hunks: list[dict[str, str]] = []
    in_conflict = False
    in_theirs = False
    ours_lines: list[str] = []
    theirs_lines: list[str] = []

    for line in lines:
        if line.startswith("<<<<<<<"):
            in_conflict = True
            in_theirs = False
            ours_lines = []
            theirs_lines = []
        elif in_conflict and line.startswith("======="):
            in_theirs = True
        elif in_conflict and line.startswith(">>>>>>>"):
            in_conflict = False
            hunks.append({
                "ours": "".join(ours_lines),
                "theirs": "".join(theirs_lines),
            })
        elif in_conflict:
            if in_theirs:
                theirs_lines.append(line)
            else:
                ours_lines.append(line)

    return hunks


def is_safe_declarative_chunk(lines: list[str]) -> bool:
    """Check if lines consist entirely of safe declarative items."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("import ", "from ", "#", "//", "/*", "*")):
            continue
        if stripped.startswith(("- ", "* ")):
            continue
        if (stripped.startswith('"') or stripped.startswith("'")) and stripped.endswith(
            (",", ";", '",', "',")
        ):
            continue
        return False
    return True


def resolve_adjacent_conflict(ours: str, theirs: str) -> str | None:
    """Smart resolution for adjacent non-conflicting additions (e.g. imports or lists).

    Returns ``None`` when the hunk is not safe to resolve without a human. The
    caller writes whatever this returns and stages it, so refusing is the only
    way a person gets to look.

    An empty side does **not** mean "take the other one". It is what git prints
    for a delete-versus-modify conflict, so accepting the non-empty side there
    silently restores something the other branch deleted (#798). Both sides go
    through the same declarative gate, empty or not.
    """
    ours_lines = [line for line in ours.splitlines() if line.strip()]
    theirs_lines = [line for line in theirs.splitlines() if line.strip()]
    if not is_safe_declarative_chunk(ours_lines) or not is_safe_declarative_chunk(
        theirs_lines
    ):
        return None
    if not ours_lines:
        return theirs
    if not theirs_lines:
        return ours
    if set(ours_lines).isdisjoint(set(theirs_lines)):
        combined = [*ours.splitlines(), *theirs.splitlines()]
        trailing = "\n" if (ours.endswith("\n") or theirs.endswith("\n")) else ""
        return "\n".join(combined) + trailing
    return None


def resolve_conflict_content(content: str) -> str | None:
    """Attempt deterministic self-healing on conflict-marked text. Returns resolved text or None."""
    if "<<<<<<<" not in content or ">>>>>>>" not in content:
        return content

    lines = content.splitlines(keepends=True)
    resolved_lines: list[str] = []
    in_conflict = False
    in_theirs = False
    ours_lines: list[str] = []
    theirs_lines: list[str] = []

    for line in lines:
        if line.startswith("<<<<<<<"):
            in_conflict = True
            in_theirs = False
            ours_lines = []
            theirs_lines = []
        elif in_conflict and line.startswith("======="):
            in_theirs = True
        elif in_conflict and line.startswith(">>>>>>>"):
            in_conflict = False
            ours_text = "".join(ours_lines)
            theirs_text = "".join(theirs_lines)
            resolved = resolve_adjacent_conflict(ours_text, theirs_text)
            if resolved is None:
                return None
            resolved_lines.append(resolved)
        elif in_conflict:
            if in_theirs:
                theirs_lines.append(line)
            else:
                ours_lines.append(line)
        else:
            resolved_lines.append(line)

    return "".join(resolved_lines)


def rebase_and_heal_cluster_branch(
    repo_root: Path,
    branch_name: str,
    base_branch: str = "main",
    runner: SubprocessRunner | None = None,
    resolver: Callable[[str], str | None] | None = None,
) -> tuple[bool, str]:
    """Rebase a cluster branch onto base branch, with intelligent self-healing on conflict."""
    run = runner or default_runner
    # Checkout branch
    run(["git", "checkout", branch_name], repo_root)
    # Attempt rebase
    res = run(["git", "rebase", f"origin/{base_branch}"], repo_root)
    if res.ok:
        return True, "clean_rebase"

    # Inspect status for unmerged conflict paths
    status_res = run(["git", "status", "--porcelain"], repo_root)
    conflict_files: list[str] = []
    raw_status = getattr(status_res, "output", getattr(status_res, "stdout", ""))
    for line in str(raw_status).splitlines():
        if line.startswith("UU ") or line.startswith("AA ") or line.startswith("UD "):
            conflict_files.append(line[3:].strip())

    if conflict_files:
        healed_all = True
        resolve_fn = resolver or resolve_conflict_content
        for rel_path in conflict_files:
            file_path = repo_root / rel_path
            if file_path.exists():
                try:
                    text = file_path.read_text(encoding="utf-8")
                    resolved = resolve_fn(text)
                    if resolved is not None:
                        file_path.write_text(resolved, encoding="utf-8")
                        run(["git", "add", rel_path], repo_root)
                    else:
                        healed_all = False
                        break
                except (OSError, UnicodeDecodeError):
                    healed_all = False
                    break
            else:
                healed_all = False
                break

        if healed_all:
            continue_res = run(
                ["git", "-c", "core.editor=true", "rebase", "--continue"], repo_root
            )
            if continue_res.ok:
                return True, "self_healed_rebase"

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
    resolver: Callable[[str], str | None] | None = None,
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
                    root_path, branch_name, runner=runner, resolver=resolver
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
