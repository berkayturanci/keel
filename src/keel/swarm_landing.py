"""Keel Swarm Landing — Orthogonal batch landing and drift self-healing merge engine.

Thin I/O execution layer for evaluating branch disjointness, merging orthogonal diff trees
under atomic merge locks, and automatically rebasing / healing drifted sequential clusters.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from .lock import merge_lock, resource_path
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
            hunks.append(
                {
                    "ours": "".join(ours_lines),
                    "theirs": "".join(theirs_lines),
                }
            )
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
    if not is_safe_declarative_chunk(ours_lines) or not is_safe_declarative_chunk(theirs_lines):
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
    res = run(["git", "rebase", base_branch], repo_root)
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
            continue_res = run(["git", "-c", "core.editor=true", "rebase", "--continue"], repo_root)
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
    if not res.ok:
        run(["git", "merge", "--abort"], repo_root)
        return False
    return True


class EvidenceCheck(NamedTuple):
    """One cluster's review-evidence answer.

    ``head_sha`` is the commit the verdicts were pinned to, carried out of the
    check so the merge can re-confirm it locally inside the lock instead of
    trusting a pin taken minutes and several API calls earlier.
    """

    ok: bool
    reason: str
    head_sha: str | None = None


class _Unset:
    """Sentinel: ``evidence_checker`` was not supplied at all.

    Defaulting to ``None`` made skipping review an *omission* — the same
    "exception lives in a driver's judgement call" failure the config knob
    exists to prevent, relocated from config to a call site. Every caller now
    states its choice, and ``None`` is a typed opt-out rather than a default.
    """


_UNSET = _Unset()


def _pin_drifted(
    repo_root: Path,
    branch_name: str,
    pinned_sha: str | None,
    runner: SubprocessRunner | None,
) -> str | None:
    """Re-confirm the branch tip inside the lock; a reason string when it moved.

    The evidence check runs before the lock (it is network-bound), so minutes
    and several API calls can pass before the merge. This local re-read costs
    nothing and closes that window. ``None`` pinned sha means the caller opted
    out of the gate, so there is nothing to confirm.
    """
    if pinned_sha is None:
        return None
    run = runner or default_runner
    res = run(["git", "rev-parse", branch_name], repo_root)
    tip = str(getattr(res, "stdout", "") or getattr(res, "output", "")).strip()
    if not res.ok or not tip:
        return "cannot re-read the branch tip before merging"
    if tip != pinned_sha:
        return f"branch tip moved to {tip[:12]} after the review check pinned {pinned_sha[:12]}"
    return None


def _restore_pin(
    repo_root: Path,
    branch_name: str,
    pinned_sha: str | None,
    runner: SubprocessRunner | None,
) -> str:
    """Rewind a branch the landing rebase rewrote back to its reviewed commit.

    Returns a phrase describing what happened, for the hold reason — the
    operator must know the branch was touched either way.
    """
    if pinned_sha is None:
        return "the branch was left rewritten (no pinned commit to restore)"
    run = runner or default_runner
    # update-ref, not `reset --hard`: a hard reset acts on whatever is checked
    # out, and one call earlier in this loop leaves HEAD on the base branch.
    # Naming the ref makes rewinding the wrong branch impossible.
    res = run(["git", "update-ref", f"refs/heads/{branch_name}", pinned_sha], repo_root)
    if getattr(res, "ok", False):
        return f"the branch was restored to the reviewed commit {pinned_sha[:12]}"
    return (
        f"the branch is still rewritten and could not be reset to "
        f"{pinned_sha[:12]} — reset it by hand"
    )


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
    evidence_checker: Callable[[str], EvidenceCheck] | None | _Unset = _UNSET,
    base_branch: str | _Unset = _UNSET,
) -> SwarmLandingResult:
    """Execute orthogonal batch landing or adaptive sequential funneling for a wave.

    ``evidence_checker`` receives a cluster branch name and answers whether the
    ship review-evidence contract holds for that branch's PR (ok, reason). When
    supplied, a cluster whose evidence does not verify is **held** — reported,
    never merged — so the independent-review layer is structural in swarm
    exactly as it is in ship s10 (#828). Passing ``None`` skips the check — a
    typed opt-out for pure planning callers and the config opt-out
    (``knobs.swarm_review_evidence: false``, logged by the CLI). Omitting the
    argument raises: review must never be skipped by oversight.
    """
    if isinstance(evidence_checker, _Unset):
        raise TypeError(
            "land_wave_clusters requires an explicit evidence_checker; pass "
            "None to opt out of the #828 review-evidence gate deliberately"
        )
    if isinstance(base_branch, _Unset):
        raise TypeError(
            "land_wave_clusters requires an explicit base_branch: verifying "
            "against the configured base while merging into a different one "
            "is the mismatch this parameter exists to prevent"
        )
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
    held: list[tuple[str, str]] = []

    state = load_swarm_state(plan.swarm_id, root=root_path)

    if dry_run:
        # A preview that ignores the gate systematically over-promises: it is
        # what a driver reads to decide whether to attempt the wave. The checks
        # are read-only, so run them here too and report what *would* hold.
        for c in target_wave.clusters:
            if evidence_checker is not None:
                raw = evidence_checker(f"swarm/{plan.swarm_id}/{c.cluster_id}")
                ok = bool(raw[0]) if isinstance(raw, tuple) and raw else False
                if not ok:
                    why = raw[1] if isinstance(raw, tuple) and len(raw) > 1 else "unusable answer"
                    held.append((c.cluster_id, f"would hold: {why}"))
                    continue
            landed.append(c.cluster_id)
        return SwarmLandingResult(
            swarm_id=plan.swarm_id,
            wave_index=wave_index,
            mode=decision.mode,
            landed_clusters=tuple(landed),
            healed_clusters=(),
            failed_clusters=(),
            status="success" if not held else "partial_failure",
            held_clusters=tuple(held),
        )

    # Live landing protected by atomic merge lock
    lock_path = resource_path(root_path / ".keel" / "state" / "locks", "merge")
    # Evidence checks are read-only (gh + rev-parse) but network-bound, so they
    # run *before* the lock: holding the global merge lock for N clusters x API
    # latency would block every concurrent `keel merge` for no reason.
    pinned: dict[str, str | None] = {}
    if evidence_checker is not None:
        cleared = []
        for c in target_wave.clusters:
            raw = evidence_checker(f"swarm/{plan.swarm_id}/{c.cluster_id}")
            # A pass without a pinned sha cannot be re-confirmed at merge time,
            # so it is not a usable answer — accepting it would silently drop
            # the drift window the lock-time re-read exists to close. Anything
            # that is not a three-field answer holds, like every other
            # unexpected state on this path.
            if isinstance(raw, EvidenceCheck):
                answer = raw
            elif isinstance(raw, tuple) and len(raw) == 3:
                answer = EvidenceCheck(*raw)
            else:
                answer = EvidenceCheck(
                    False,
                    "evidence checker returned an unusable answer "
                    f"({type(raw).__name__}); it must report (ok, reason, head_sha)",
                )
            evidence_ok, reason = answer.ok, answer.reason
            if evidence_ok and not answer.head_sha:
                evidence_ok = False
                reason = (
                    "evidence checker passed without a pinned commit, so the "
                    "merge could not re-confirm what it verified"
                )
            if evidence_ok:
                pinned[c.cluster_id] = answer.head_sha
                cleared.append(c)
            else:
                held.append((c.cluster_id, reason))
                if state:
                    state = update_worker_state(
                        state,
                        c.cluster_id,
                        step="s10",
                        status="held",
                        details=f"review evidence: {reason}",
                    )
    else:
        cleared = list(target_wave.clusters)

    # Persist the pre-lock holds before contending for the lock: if acquiring
    # it raises (a concurrent keel merge is the expected case), the operator
    # still gets the reasons, which were the whole point.
    if state and held:
        save_swarm_state(state, root=root_path)

    with merge_lock(lock_path):
        for c in cleared:
            branch_name = f"swarm/{plan.swarm_id}/{c.cluster_id}"
            # Applies to both arms: the evidence check ran outside the lock,
            # and on the funnel path the rebase itself voids the pin, so the
            # re-read has to happen before either one touches the branch.
            drift = _pin_drifted(root_path, branch_name, pinned.get(c.cluster_id), runner)
            if drift is not None:
                held.append((c.cluster_id, drift))
                if state:
                    state = update_worker_state(
                        state,
                        c.cluster_id,
                        step="s10",
                        status="held",
                        details=f"pin drift: {drift}",
                    )
                continue
            if decision.mode == "direct_batch":
                ok = merge_cluster_branch(
                    root_path, branch_name, base_branch=base_branch, runner=runner
                )
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
                    root_path,
                    branch_name,
                    base_branch=base_branch,
                    runner=runner,
                    resolver=resolver,
                )
                if rebase_ok:
                    # The heal rewrote the branch: new SHAs, and on the
                    # self-healed path new *content* the resolver authored.
                    # The pin taken before the rebase is void, so re-check
                    # against the new tip — landing here would otherwise bless
                    # bytes nobody reviewed, the exact bypass #828 closes on
                    # the direct-batch path.
                    # A rebase always rewrites SHAs, so re-pinning to the old
                    # head can never pass — that would make funnel mode, which
                    # exists precisely because the base moved, a permanent
                    # no-op. What matters is whether any *content* decision was
                    # made: git reports "clean_rebase" when it replayed the
                    # reviewed commits with no conflict, and
                    # "self_healed_rebase" when the resolver authored bytes
                    # nobody reviewed. Only the latter breaks the guarantee.
                    if evidence_checker is not None and reason != "clean_rebase":
                        # The rebase already rewrote the branch before we could
                        # judge it. Leaving it rewritten would strand the
                        # cluster: every later run would compare the new tip
                        # against the unchanged reviewed head and hold forever.
                        # Restore the pinned commit so the next run starts from
                        # the reviewed state, and say so in the reason.
                        restored = _restore_pin(
                            root_path, branch_name, pinned.get(c.cluster_id), runner
                        )
                        held.append(
                            (
                                c.cluster_id,
                                f"landing rebase resolved conflicts ({reason}), so "
                                f"content nobody reviewed would land; {restored} — "
                                "rebase and re-review the PR before landing",
                            )
                        )
                        if state:
                            state = update_worker_state(
                                state,
                                c.cluster_id,
                                step="s10",
                                status="held",
                                details=f"post-rebase content: {reason}",
                            )
                        continue
                    healed.append(c.cluster_id)
                    merge_ok = merge_cluster_branch(
                        root_path, branch_name, base_branch=base_branch, runner=runner
                    )
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

    # A held cluster is not landed, so it can never leave the wave "success";
    # it is also not a failure of the code itself, so it degrades the status
    # exactly like a failed cluster without being reported as one.
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
        healed_clusters=tuple(healed),
        failed_clusters=tuple(failed),
        status=overall_status,
        held_clusters=tuple(held),
    )
