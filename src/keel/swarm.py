"""Keel Swarm — Deterministic static dependency analysis & conflict clustering.

Pure, stdlib-first dependency graph analysis for multi-agent parallel execution.
Partitions candidate issues into orthogonal (disjoint) Waves and independent Clusters,
enabling Direct Batch Landing for non-overlapping diff trees and adaptive merge
funneling with automated conflict recovery for dependent trees.

All functions here are pure and deterministic — no subprocess, no network.
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ProjectConfig

#: Regex to capture backticked file paths or common file paths in issue text
_PATH_BACKTICK_RE = re.compile(r"`([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_\-]+)`")
_PATH_GENERAL_RE = re.compile(
    r"(?:^|[\s(\[])([a-zA-Z0-9_\-./]+/(?:[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9_\-]+|[a-zA-Z0-9_\-]+/|\*))"
)


@dataclass(frozen=True)
class IssueScope:
    """Normalized predicted blast radius and role for a single backlog issue."""

    issue: int
    title: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    declared_files: tuple[str, ...] = ()
    predicted_files: tuple[str, ...] = ()
    role: str = "core"

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "title": self.title,
            "role": self.role,
            "labels": list(self.labels),
            "declared_files": list(self.declared_files),
            "predicted_files": list(self.predicted_files),
        }


@dataclass(frozen=True)
class SwarmCluster:
    """A unit of work within a Swarm Wave consisting of one or more related issues."""

    cluster_id: str
    issues: tuple[int, ...]
    role: str
    combined_scope: tuple[str, ...]
    depends_on_issues: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "issues": list(self.issues),
            "role": self.role,
            "combined_scope": list(self.combined_scope),
            "depends_on_issues": list(self.depends_on_issues),
        }


@dataclass(frozen=True)
class SwarmWave:
    """A parallel execution wave containing mutually independent or sequenced clusters."""

    wave_index: int
    mode: str  # "orthogonal_parallel" or "sequential_dependent"
    eligible_direct_landing: bool
    clusters: tuple[SwarmCluster, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "wave_index": self.wave_index,
            "mode": self.mode,
            "eligible_direct_landing": self.eligible_direct_landing,
            "clusters": [c.to_dict() for c in self.clusters],
        }


@dataclass(frozen=True)
class SwarmPlan:
    """Complete deterministic partition and execution plan for a swarm run."""

    swarm_id: str
    total_issues: int
    waves: tuple[SwarmWave, ...]
    conflict_map: dict[int, tuple[int, ...]] = field(default_factory=dict)
    issue_scopes: dict[int, IssueScope] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "total_issues": self.total_issues,
            "waves": [w.to_dict() for w in self.waves],
            "conflict_map": {str(k): list(v) for k, v in self.conflict_map.items()},
            "issue_scopes": {str(k): v.to_dict() for k, v in self.issue_scopes.items()},
        }


@dataclass(frozen=True)
class SwarmWorkerStatus:
    """Live state for an individual worker operating on a swarm cluster."""

    cluster_id: str
    issue: int
    role: str
    agent: str = "claude"
    model: str = "default"
    step: str = "s0"
    status: str = "queued"  # queued, running, passed, failed, merged
    updated_at: str = ""
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "issue": self.issue,
            "role": self.role,
            "agent": self.agent,
            "model": self.model,
            "step": self.step,
            "status": self.status,
            "updated_at": self.updated_at,
            "details": self.details,
        }


@dataclass(frozen=True)
class SwarmRunState:
    """State tracking for a live or completed swarm execution."""

    swarm_id: str
    total_workers: int
    active_wave: int = 1
    workers: tuple[SwarmWorkerStatus, ...] = ()
    started_at: str = ""
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "total_workers": self.total_workers,
            "active_wave": self.active_wave,
            "workers": [w.to_dict() for w in self.workers],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class SwarmRunResult:
    """Outcome summary for a complete or partial swarm execution."""

    swarm_id: str
    status: str  # "success", "partial_failure", "failed"
    total_workers: int
    passed_count: int
    failed_count: int
    dry_run: bool
    wave_results: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "status": self.status,
            "total_workers": self.total_workers,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "dry_run": self.dry_run,
            "wave_results": list(self.wave_results),
        }


def _normalize_path(p: str) -> str:
    cleaned = p.strip("`'\" \t\r\n.,;:()")
    cleaned = cleaned.replace("\\", "/").removeprefix("./").removeprefix("/")
    return posixpath.normpath(cleaned) if cleaned else ""


def extract_predicted_paths(text: str) -> list[str]:
    """Extract candidate file and directory paths from issue title or markdown body."""
    found: set[str] = set()
    for match in _PATH_BACKTICK_RE.finditer(text):
        found.add(_normalize_path(match.group(1)))
    for match in _PATH_GENERAL_RE.finditer(text):
        found.add(_normalize_path(match.group(1)))
    found.discard("")
    return sorted(found)


def extract_issue_scope(
    issue: int,
    *,
    title: str = "",
    body: str = "",
    labels: list[str] | tuple[str, ...] | None = None,
    declared_files: list[str] | tuple[str, ...] | None = None,
    config: ProjectConfig | None = None,
) -> IssueScope:
    """Extract and normalize predicted files and roles for an issue."""
    norm_labels = tuple(sorted(set(labels or ())))
    norm_declared = tuple(
        sorted(set(_normalize_path(f) for f in (declared_files or ()) if _normalize_path(f)))
    )

    predicted = set(norm_declared)
    combined_text = f"{title}\n{body}"
    predicted.update(extract_predicted_paths(combined_text))

    # Resolve role from labels or config
    resolved_role = "core"
    for label in norm_labels:
        if label.startswith("role:"):
            resolved_role = label.removeprefix("role:")
            break
        if label.startswith("area:"):
            resolved_role = label.removeprefix("area:")
            break

    if config and config.knobs.implementer_agents:
        if resolved_role not in config.knobs.implementer_agents:
            resolved_role = "core" if "core" in config.knobs.implementer_agents else resolved_role

    # Default directory hints based on role or labels if no specific files found
    if not predicted:
        if "visual" in resolved_role or any("visual" in lbl for lbl in norm_labels):
            predicted.add("keel-visual/*")
        elif "docs" in resolved_role or any("docs" in lbl for lbl in norm_labels):
            predicted.add("docs/*")
        elif "website" in resolved_role or any("website" in lbl for lbl in norm_labels):
            predicted.add("website/*")
        elif "cli" in resolved_role or any("cli" in lbl for lbl in norm_labels):
            predicted.add("src/keel/cli.py")
        else:
            predicted.add(f"scope/issue-{issue}/*")

    return IssueScope(
        issue=issue,
        title=title.strip(),
        body=body.strip(),
        labels=norm_labels,
        declared_files=norm_declared,
        predicted_files=tuple(sorted(predicted)),
        role=resolved_role,
    )


def paths_intersect(path_a: str, path_b: str) -> bool:
    """True if path_a and path_b refer to the same file or overlapping glob/directory."""
    a = _normalize_path(path_a)
    b = _normalize_path(path_b)
    if not a or not b:
        return False
    if a == b:
        return True
    if a == "*" or b == "*":
        return True
    # Directory prefix overlap
    a_dir = a if a.endswith("/") else a + "/"
    b_dir = b if b.endswith("/") else b + "/"
    if b.startswith(a_dir) or a.startswith(b_dir):
        return True
    # Glob matching
    if "*" in a and fnmatch.fnmatch(b, a):
        return True
    if "*" in b and fnmatch.fnmatch(a, b):
        return True
    return False


def scopes_intersect(scope_a: IssueScope, scope_b: IssueScope) -> tuple[str, ...]:
    """Return common overlapping paths between two issue scopes."""
    overlaps: set[str] = set()
    for fa in scope_a.predicted_files:
        for fb in scope_b.predicted_files:
            if paths_intersect(fa, fb):
                overlaps.add(fa if len(fa) <= len(fb) else fb)
    return tuple(sorted(overlaps))


def build_swarm_plan(
    issue_scopes: list[IssueScope] | tuple[IssueScope, ...],
    *,
    swarm_id: str | None = None,
    config: ProjectConfig | None = None,
) -> SwarmPlan:
    """Deterministically partition candidate issues into orthogonal Waves and Clusters."""
    if not issue_scopes:
        now_id = swarm_id or (
            "swarm-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        )
        return SwarmPlan(
            swarm_id=now_id,
            total_issues=0,
            waves=(),
            conflict_map={},
            issue_scopes={},
        )

    # Sort issues for determinism
    sorted_scopes = sorted(issue_scopes, key=lambda s: s.issue)
    scope_by_id = {s.issue: s for s in sorted_scopes}

    # Compute pairwise conflict graph
    conflict_map: dict[int, list[int]] = {s.issue: [] for s in sorted_scopes}
    for i, sa in enumerate(sorted_scopes):
        for sb in sorted_scopes[i + 1 :]:
            if scopes_intersect(sa, sb):
                conflict_map[sa.issue].append(sb.issue)
                conflict_map[sb.issue].append(sa.issue)

    frozen_conflicts: dict[int, tuple[int, ...]] = {
        k: tuple(sorted(v)) for k, v in conflict_map.items()
    }

    # Greedy wave partitioning (independent set partitioning)
    remaining_issues = [s.issue for s in sorted_scopes]
    waves: list[SwarmWave] = []
    wave_idx = 1

    assigned_prior_issues: set[int] = set()

    while remaining_issues:
        current_wave_issues: list[int] = []
        current_wave_scope_set: set[str] = set()

        for issue_num in list(remaining_issues):
            scope = scope_by_id[issue_num]
            # Check if this issue conflicts with anything already placed in current wave
            has_wave_conflict = False
            for placed_num in current_wave_issues:
                if issue_num in frozen_conflicts.get(placed_num, ()):
                    has_wave_conflict = True
                    break

            if not has_wave_conflict:
                current_wave_issues.append(issue_num)
                current_wave_scope_set.update(scope.predicted_files)
                remaining_issues.remove(issue_num)

        # Build clusters for this wave
        clusters: list[SwarmCluster] = []
        for issue_num in current_wave_issues:
            sc = scope_by_id[issue_num]
            # Find dependencies on previously assigned waves
            deps = tuple(
                sorted(
                    dep
                    for dep in frozen_conflicts.get(issue_num, ())
                    if dep in assigned_prior_issues
                )
            )
            clusters.append(
                SwarmCluster(
                    cluster_id=f"cluster-{wave_idx}-{issue_num}",
                    issues=(issue_num,),
                    role=sc.role,
                    combined_scope=sc.predicted_files,
                    depends_on_issues=deps,
                )
            )

        assigned_prior_issues.update(current_wave_issues)

        # Determine mode & direct landing eligibility
        is_orthogonal = len(current_wave_issues) > 0
        waves.append(
            SwarmWave(
                wave_index=wave_idx,
                mode="orthogonal_parallel" if is_orthogonal else "sequential_dependent",
                eligible_direct_landing=is_orthogonal,
                clusters=tuple(clusters),
            )
        )
        wave_idx += 1

    now_id = swarm_id or (
        "swarm-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    )
    return SwarmPlan(
        swarm_id=now_id,
        total_issues=len(sorted_scopes),
        waves=tuple(waves),
        conflict_map=frozen_conflicts,
        issue_scopes=scope_by_id,
    )


def render_swarm_plan_text(plan: SwarmPlan) -> str:
    """Render human-readable tabular text summary of the SwarmPlan."""
    lines: list[str] = [
        f"keel swarm plan — {plan.swarm_id}",
        f"  total issues : {plan.total_issues}",
        f"  total waves  : {len(plan.waves)}",
        "",
    ]
    for w in plan.waves:
        landing = (
            "eligible for direct batch landing"
            if w.eligible_direct_landing
            else "sequential merge funnel"
        )
        lines.append(f"Wave {w.wave_index} [{w.mode}] — {landing}:")
        for c in w.clusters:
            issues_str = ", ".join(f"#{i}" for i in c.issues)
            scope_prefix = c.combined_scope[:3]
            scope_str = ", ".join(scope_prefix) + (
                "..." if len(c.combined_scope) > 3 else ""
            )
            dep_str = (
                f" (depends on: {', '.join(f'#{d}' for d in c.depends_on_issues)})"
                if c.depends_on_issues
                else ""
            )
            lines.append(
                f"  • Cluster {c.cluster_id}: {issues_str} [{c.role}] → {scope_str}{dep_str}"
            )
        lines.append("")
    return "\n".join(lines).strip()


def render_swarm_plan_tree(plan: SwarmPlan) -> str:
    """Render a visual ASCII/Unicode DAG dependency tree of the SwarmPlan."""
    if plan.total_issues == 0:
        return f"keel swarm plan — {plan.swarm_id} (0 issues)"

    direct_count = sum(1 for w in plan.waves if w.eligible_direct_landing)
    hdr_a = f"│ 🐝 Keel Swarm Plan — {plan.swarm_id:<38} │"
    hdr_b = (
        f"│ Issues: {plan.total_issues:<3} │ Waves: {len(plan.waves):<3} "
        f"│ Direct Landing Waves: {direct_count:<2} │"
    )
    lines: list[str] = [
        "╭" + "─" * 62 + "╮",
        hdr_a,
        hdr_b,
        "╰" + "─" * 62 + "╯",
        "",
    ]

    for w in plan.waves:
        mode_icon = "⚡" if w.eligible_direct_landing else "⏳"
        landing_label = (
            "Direct Batch Landing" if w.eligible_direct_landing else "Sequential Funnel"
        )
        lines.append(f"{mode_icon} Wave {w.wave_index} [{w.mode}] — {landing_label}")

        num_clusters = len(w.clusters)
        for i, c in enumerate(w.clusters):
            is_last_cluster = i == num_clusters - 1
            c_prefix = "└── " if is_last_cluster else "├── "
            c_indent = "    " if is_last_cluster else "│   "

            issues_str = ", ".join(f"#{num}" for num in c.issues)
            lines.append(f"{c_prefix}📦 Cluster {c.cluster_id} ({issues_str}) [{c.role}]")

            scope_items = list(c.combined_scope)
            has_deps = bool(c.depends_on_issues)

            scope_branch = "├── " if has_deps else "└── "
            lines.append(
                f"{c_indent}{scope_branch}Scope: {', '.join(scope_items[:3])}"
                f"{'...' if len(scope_items) > 3 else ''}"
            )

            if has_deps:
                dep_issues = ", ".join(f"#{d}" for d in c.depends_on_issues)
                lines.append(f"{c_indent}└── ⛓️ Depends on: {dep_issues}")
        lines.append("")

    return "\n".join(lines).rstrip()


def render_swarm_status_dashboard(state: SwarmRunState | None) -> str:
    """Render a live terminal ASCII matrix status board of the swarm run."""
    if state is None:
        return "keel swarm status — no active or recent swarm run found."

    status_badges = {
        "queued": "[QUEUED ⏳]",
        "running": "[RUNNING ⚙️]",
        "passed": "[PASSED ✓]",
        "failed": "[FAILED ✗]",
        "merged": "[MERGED 🚢]",
    }

    start_str = state.started_at[:19] if state.started_at else "pending"
    hdr_info = (
        f"│ Active Wave: {state.active_wave:<3} │ Total Workers: {state.total_workers:<3} "
        f"│ Started: {start_str:<24} │"
    )
    cols_hdr = (
        f"│ {'Cluster':<16} │ {'Issue':<6} │ {'Role':<8} │ {'Step':<5} "
        f"│ {'Agent / Model':<16} │ {'Status':<10} │"
    )
    lines = [
        "╭" + "─" * 74 + "╮",
        f"│ 🐝 Keel Swarm Live Status — {state.swarm_id:<44} │",
        hdr_info,
        "├" + "─" * 74 + "┤",
        cols_hdr,
        "├" + "─" * 74 + "┤",
    ]

    for w in state.workers:
        badge = status_badges.get(w.status, f"[{w.status.upper()}]")
        agent_str = f"{w.agent}:{w.model}"[:16]
        row_str = (
            f"│ {w.cluster_id:<16} │ #{w.issue:<5} │ {w.role:<8} │ {w.step:<5} "
            f"│ {agent_str:<16} │ {badge:<10} │"
        )
        lines.append(row_str)

    lines.append("╰" + "─" * 74 + "╯")
    return "\n".join(lines)


def resolve_swarm_state_dir(root: str | Path = ".") -> Path:
    """Ensure and return the path to `.keel/state/swarm/` directory."""
    p = Path(root) / ".keel" / "state" / "swarm"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_swarm_state(state: SwarmRunState, root: str | Path = ".") -> Path:
    """Persist a SwarmRunState JSON snapshot to `.keel/state/swarm/<swarm_id>.json`."""
    state_dir = resolve_swarm_state_dir(root)
    file_path = state_dir / f"{state.swarm_id}.json"
    file_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return file_path


def load_swarm_state(swarm_id: str, root: str | Path = ".") -> SwarmRunState | None:
    """Load a SwarmRunState from disk if present."""
    state_dir = Path(root) / ".keel" / "state" / "swarm"
    file_path = state_dir / f"{swarm_id}.json"
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        workers = tuple(
            SwarmWorkerStatus(
                cluster_id=str(w.get("cluster_id", "")),
                issue=int(w.get("issue", 0)),
                role=str(w.get("role", "core")),
                agent=str(w.get("agent", "claude")),
                model=str(w.get("model", "default")),
                step=str(w.get("step", "s0")),
                status=str(w.get("status", "queued")),
                updated_at=str(w.get("updated_at", "")),
                details=str(w.get("details", "")),
            )
            for w in data.get("workers", [])
        )
        return SwarmRunState(
            swarm_id=str(data.get("swarm_id", swarm_id)),
            total_workers=int(data.get("total_workers", len(workers))),
            active_wave=int(data.get("active_wave", 1)),
            workers=workers,
            started_at=str(data.get("started_at", "")),
            completed_at=data.get("completed_at"),
        )
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def update_worker_state(
    state: SwarmRunState,
    cluster_id: str,
    *,
    step: str | None = None,
    status: str | None = None,
    details: str | None = None,
) -> SwarmRunState:
    """Return a new SwarmRunState with the specified worker's fields updated."""
    updated_workers = []
    for w in state.workers:
        if w.cluster_id == cluster_id:
            updated_workers.append(
                SwarmWorkerStatus(
                    cluster_id=w.cluster_id,
                    issue=w.issue,
                    role=w.role,
                    agent=w.agent,
                    model=w.model,
                    step=step if step is not None else w.step,
                    status=status if status is not None else w.status,
                    updated_at=datetime.datetime.now(datetime.UTC).isoformat(),
                    details=details if details is not None else w.details,
                )
            )
        else:
            updated_workers.append(w)

    return SwarmRunState(
        swarm_id=state.swarm_id,
        total_workers=state.total_workers,
        active_wave=state.active_wave,
        workers=tuple(updated_workers),
        started_at=state.started_at,
        completed_at=state.completed_at,
    )


def rebalance_swarm_plan(plan: SwarmPlan, failed_issue: int) -> SwarmPlan:
    """Dynamically recalculate a SwarmPlan when an issue fails during execution.

    Any subsequent wave clusters that depended on ``failed_issue`` will have
    the failed dependency omitted, while independent disjoint clusters
    proceed without interruption.
    """
    new_waves = []
    for w in plan.waves:
        new_clusters = []
        for c in w.clusters:
            if failed_issue in c.issues:
                continue
            new_clusters.append(c)
        if new_clusters:
            new_waves.append(
                SwarmWave(
                    wave_index=w.wave_index,
                    mode=w.mode,
                    eligible_direct_landing=w.eligible_direct_landing,
                    clusters=tuple(new_clusters),
                )
            )

    return SwarmPlan(
        swarm_id=plan.swarm_id,
        total_issues=sum(len(c.issues) for w in new_waves for c in w.clusters),
        waves=tuple(new_waves),
        conflict_map=plan.conflict_map,
        issue_scopes=plan.issue_scopes,
    )


def render_swarm_run_result(result: SwarmRunResult) -> str:
    """Render a human-readable text summary of a SwarmRunResult."""
    status_icon = (
        "✓"
        if result.status == "success"
        else ("⚠️" if result.status == "partial_failure" else "✗")
    )
    lines = [
        f"keel swarm run — {result.swarm_id}",
        f"  status        : {result.status} {status_icon}",
        f"  total workers : {result.total_workers}",
        f"  passed        : {result.passed_count}",
        f"  failed        : {result.failed_count}",
        f"  dry-run       : {'true' if result.dry_run else 'false'}",
        f"  total waves   : {len(result.wave_results)}",
    ]
    return "\n".join(lines)

