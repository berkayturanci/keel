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
import posixpath
import re
from dataclasses import dataclass, field
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
