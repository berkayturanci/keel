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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import agents, classify, ship, workspace
from . import team as team_policy
from .config import ProjectConfig

#: Difficulty points by resolved risk tier. A tier-3 cluster is not merely riskier, it
#: is more *work*: it touches the globs the project declared load-bearing, so the change
#: has to be argued for as well as written.
_TIER_POINTS = {1: 0, 2: 2, 3: 4}

#: ``(at least this many predicted files, points)``, widest first. Predicted breadth is
#: the only size signal available before a line is written, and it is the one that decides
#: whether a cluster is one edit or a refactor.
_FILE_COUNT_POINTS = ((8, 3), (4, 2), (2, 1))

#: Label -> points. Only labels a human deliberately set count: ``priority:`` says how
#: much it matters (and so how much review attention it will draw), ``size:`` is the
#: estimate a human already made and is worth more than any heuristic here.
_LABEL_POINTS = {
    "priority:critical": 2,
    "priority:high": 1,
    "size:m": 1,
    "size:l": 2,
    "size:xl": 3,
}

#: Cap on the points a cluster earns for depending on already-scheduled work. Depth says
#: the cluster lands on a tree someone else just moved; past a few dependencies that is
#: the same problem, not a worse one.
MAX_DEPENDENCY_POINTS = 3

#: ``(highest score still in this band, band)``, lightest first; anything above the last
#: ceiling is the heaviest band. The bands themselves are :data:`keel.team.DIFFICULTY_BANDS`,
#: because a band's whole purpose is to name a ``knobs.team.by_difficulty`` bench.
_BAND_CEILINGS = ((2, "easy"), (5, "standard"))

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
class Difficulty:
    """How much work a cluster is, and the evidence that says so (#1017).

    Deliberately not the risk tier. A tier answers *how dangerous is this change* and is
    read off the files it touches; a band answers *how much work is it*, which is what
    decides whether the strong implementer is worth spending on it. A one-line fix to a
    tier-3 glob is dangerous and trivial; a twelve-file docs migration is safe and long.
    Keeping them apart is what lets ``knobs.team`` staff on one and gate on the other.
    """

    score: int
    band: str
    tier: int
    file_count: int
    dependency_depth: int
    #: ``(name, points)`` for every input that contributed, so a surprising band can be
    #: read back rather than guessed at.
    signals: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "tier": self.tier,
            "file_count": self.file_count,
            "dependency_depth": self.dependency_depth,
            "signals": [{"name": name, "points": points} for name, points in self.signals],
        }


@dataclass(frozen=True)
class SwarmCluster:
    """A unit of work within a Swarm Wave consisting of one or more related issues."""

    cluster_id: str
    issues: tuple[int, ...]
    role: str
    combined_scope: tuple[str, ...]
    depends_on_issues: tuple[int, ...] = ()
    #: How much work this cluster is (:func:`score_difficulty`).
    difficulty: Difficulty | None = None
    #: Who runs it (:func:`keel.team.resolve_assignment`) — lead, implementer, reviewers.
    assignment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "issues": list(self.issues),
            "role": self.role,
            "combined_scope": list(self.combined_scope),
            "depends_on_issues": list(self.depends_on_issues),
            "difficulty": None if self.difficulty is None else self.difficulty.to_dict(),
            "assignment": self.assignment,
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
    status: str = "queued"  # queued, running, passed, failed, merged, held
    updated_at: str = ""
    details: str = ""
    #: The team lead this worker reports through — the CTO/lead/worker hierarchy is only
    #: legible on the board if a worker record says which lead owns it (#1017).
    lead: str = ""
    #: The difficulty band the worker was staffed from, so a status board shows *why*
    #: this cluster drew this provider.
    difficulty: str = ""

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
            "lead": self.lead,
            "difficulty": self.difficulty,
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


@dataclass(frozen=True)
class LandingDecision:
    """Evaluation of whether a wave can directly land or requires sequential funneling."""

    mode: str  # "direct_batch" or "sequential_funnel"
    eligible: bool
    cluster_ids: tuple[str, ...]
    reason: str = "orthogonal_diffs"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "eligible": self.eligible,
            "cluster_ids": list(self.cluster_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SwarmLandingResult:
    """Outcome report for landing a swarm wave."""

    swarm_id: str
    wave_index: int
    mode: str
    landed_clusters: tuple[str, ...]
    healed_clusters: tuple[str, ...]
    failed_clusters: tuple[str, ...]
    status: str  # "success", "partial_failure", "failed"
    #: Clusters refused for landing because their review evidence did not
    #: verify — (cluster_id, reason) pairs. Held is not failed: the code is
    #: intact, the independent-review contract is simply not yet satisfied.
    held_clusters: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "wave_index": self.wave_index,
            "mode": self.mode,
            "landed_clusters": list(self.landed_clusters),
            "healed_clusters": list(self.healed_clusters),
            "failed_clusters": list(self.failed_clusters),
            "held_clusters": [list(pair) for pair in self.held_clusters],
            "status": self.status,
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

    # Both role vocabularies: `team.implement.by_role` (#1014) is where a role lives now,
    # and the deprecated `implementer_agents` still routes for a project that has not
    # migrated. Reading only the old one silently stopped narrowing the role for any
    # project that adopted `team` — including keel itself.
    known_roles: set[str] = set()
    if config:
        known_roles = {*config.knobs.implementer_agents, *config.knobs.team.implement_by_role}
    if known_roles and resolved_role not in known_roles:
        resolved_role = "core" if "core" in known_roles else resolved_role

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


def _normalized_paths_intersect(a: str, b: str) -> bool:
    """:func:`paths_intersect` for two paths already passed through ``_normalize_path``.

    The matching rules live here and nowhere else. ``paths_intersect`` normalizes and
    delegates; the swarm plan normalizes each scope once and calls this directly.
    """
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


def paths_intersect(path_a: str, path_b: str) -> bool:
    """True if path_a and path_b refer to the same file or overlapping glob/directory."""
    return _normalized_paths_intersect(_normalize_path(path_a), _normalize_path(path_b))


def scopes_intersect(scope_a: IssueScope, scope_b: IssueScope) -> tuple[str, ...]:
    """Return common overlapping paths between two issue scopes."""
    overlaps: set[str] = set()
    for fa in scope_a.predicted_files:
        for fb in scope_b.predicted_files:
            if paths_intersect(fa, fb):
                overlaps.add(fa if len(fa) <= len(fb) else fb)
    return tuple(sorted(overlaps))


def _normalized_files(scope: IssueScope) -> tuple[str, ...]:
    """The scope's predicted paths as the matcher sees them, normalized once."""
    return tuple(_normalize_path(f) for f in scope.predicted_files)


def _normalized_scopes_conflict(files_a: tuple[str, ...], files_b: tuple[str, ...]) -> bool:
    """:func:`scopes_have_conflict` over paths already normalized by ``_normalized_files``."""
    # Fast path: an identical path is a conflict under the ``a == b`` rule, and a set
    # intersection finds one without the pairwise loop. The empty string is dropped
    # because the matcher rejects it on either side — ``("",)`` vs ``("",)`` is not a
    # conflict, and must not become one here.
    common = set(files_a) & set(files_b)
    common.discard("")
    if common:
        return True
    for fa in files_a:
        for fb in files_b:
            if _normalized_paths_intersect(fa, fb):
                return True
    return False


def scopes_have_conflict(scope_a: IssueScope, scope_b: IssueScope) -> bool:
    """True if any predicted path of one scope overlaps any path of the other.

    Equivalent to ``bool(scopes_intersect(scope_a, scope_b))`` — the same matcher and
    the same normalization — but returns at the first overlap instead of collecting
    and sorting them all. ``build_swarm_plan`` asks this O(N²) times and only needs
    the boolean.
    """
    return _normalized_scopes_conflict(_normalized_files(scope_a), _normalized_files(scope_b))


def _file_count_points(count: int) -> int:
    """Points for a scope of ``count`` predicted files (the widest threshold it reaches)."""
    for threshold, points in _FILE_COUNT_POINTS:
        if count >= threshold:
            return points
    return 0


def difficulty_band(score: int) -> str:
    """The band a difficulty ``score`` falls in."""
    for ceiling, band in _BAND_CEILINGS:
        if score <= ceiling:
            return band
    return team_policy.DIFFICULTY_BANDS[-1]


def cluster_scopes(
    cluster: SwarmCluster, scopes: Mapping[int, IssueScope]
) -> tuple[IssueScope, ...]:
    """The issue scopes a cluster covers, in the cluster's own order.

    An issue with no scope in the map is skipped rather than faked: a scoreable cluster
    is one whose issues the planner actually analysed, and inventing an empty scope would
    quietly lower the band of a cluster whose largest issue went missing.
    """
    return tuple(scopes[issue] for issue in cluster.issues if issue in scopes)


def score_difficulty(
    scopes: Sequence[IssueScope],
    *,
    tier3_globs: tuple[str, ...] = (),
    docs_globs: tuple[str, ...] = (),
    allowlist_globs: tuple[str, ...] = (),
    dependency_depth: int = 0,
) -> Difficulty:
    """How much work one cluster of issues is — pure, and the same answer every run.

    Takes the cluster's scopes rather than one issue's, because a cluster is the unit a
    lead is handed and a cluster of three small issues is not three small pieces of work.
    Every input is already deterministic: the risk tier from ``knobs.tier3_globs``, the
    predicted blast radius the planner computed, labels a human set, and how much
    already-scheduled work this cluster lands on top of.
    """
    files = sorted({path for scope in scopes for path in scope.predicted_files})
    labels = sorted({label.lower() for scope in scopes for label in scope.labels})
    tier = classify.tier_for_files(
        files,
        tier3_globs=tier3_globs,
        docs_globs=docs_globs,
        allowlist_globs=allowlist_globs,
    )
    # The observed depth is recorded and the *points* are what the cap bites: a cluster
    # sitting on nine earlier issues and one sitting on three are not the same situation,
    # and reporting both as `depends-on:3` threw away the difference at the only place a
    # reader could have seen it. Capping the points still says "past a few dependencies
    # it is the same problem, not a worse one".
    depth = max(dependency_depth, 0)
    depth_points = min(depth, MAX_DEPENDENCY_POINTS)
    candidates: list[tuple[str, int]] = [
        (f"tier-{tier}", _TIER_POINTS.get(tier, 2)),
        (f"files:{len(files)}", _file_count_points(len(files))),
        *((label, _LABEL_POINTS[label]) for label in labels if label in _LABEL_POINTS),
        (f"depends-on:{depth}", depth_points),
    ]
    # Only what actually moved the score is recorded: a signal worth zero points is not
    # evidence, and a reader who has to filter them out is reading noise.
    signals = tuple((name, points) for name, points in candidates if points)
    score = sum(points for _name, points in signals)
    return Difficulty(
        score=score,
        band=difficulty_band(score),
        tier=tier,
        file_count=len(files),
        dependency_depth=depth,
        signals=signals,
    )


@dataclass(frozen=True)
class AssignmentOverrides:
    """Per-run staffing a batch runner passes down to every cluster it launches.

    The command-line half of the team contract: ``--delegate``, ``--review-delegate``,
    ``--effort``, ``--team`` and ``--reviewers`` as one value, so ``swarm-plan``,
    ``swarm-run`` and a work block hand the same object to the same resolver instead of
    each threading five arguments and dropping a different one.
    """

    delegate: str | None = None
    review_delegates: tuple[str, ...] = ()
    effort: str | None = None
    team_profile: str | None = None
    reviewers: int | None = None
    host_agent: str = team_policy.HOST_DEFAULT

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegate": self.delegate,
            "review_delegates": list(self.review_delegates),
            "effort": self.effort,
            "team": self.team_profile,
            "reviewers": self.reviewers,
            "host_agent": self.host_agent,
        }


def resolve_cluster_assignment(
    cluster: SwarmCluster,
    difficulty: Difficulty,
    *,
    config: ProjectConfig | None = None,
    overrides: AssignmentOverrides | None = None,
) -> dict[str, Any]:
    """Who runs this cluster: :func:`keel.team.resolve_assignment`, per cluster.

    The swarm does not get its own idea of who implements. It calls the resolver every
    other command calls, with *this cluster's* role, risk tier and difficulty band — so a
    cluster's lead can hand the assignment straight to ``keel ship`` and the child run
    resolves the same team from the same config.
    """
    settings = overrides or AssignmentOverrides()
    policy = config.knobs.team if config is not None else team_policy.TeamPolicy()
    assignment = team_policy.resolve_assignment(
        policy,
        tier=difficulty.tier,
        role=cluster.role,
        default_count=ship.reviewer_count(difficulty.tier),
        reviewer_override=settings.reviewers,
        delegate=settings.delegate,
        review_delegates=settings.review_delegates,
        host_agent=settings.host_agent,
        legacy=None if config is None else agents.legacy_team_seats(config),
        difficulty=difficulty.band,
        team_profile=settings.team_profile,
        effort=settings.effort,
    )
    # A role keel will not hand to a child is an operator-visible fact, not a silent
    # omission: `assignment.warnings` is where a lead is told to look before launching.
    assignment["warnings"] = [*assignment["warnings"], *safe_role(assignment["role"])[1]]
    return assignment


#: Characters a role may contribute to a child's argv. A role is read off an issue label,
#: which anyone with triage rights can write; `--role` hands it to a child `argparse`,
#: where a value opening with `-` is parsed as a flag rather than as the option's value.
#: `role:--live` on one issue would therefore break every child ship in the batch.
_SAFE_ROLE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def safe_role(role: str | None) -> tuple[str | None, tuple[str, ...]]:
    """``(role, warnings)`` — the role if it can be an argv token, else ``None`` and why.

    Dropped rather than escaped or quoted: there is no quoting that makes `--live` stop
    being a flag to `argparse`, and a role keel cannot pass is a role the child resolves
    from config on its own — a smaller loss than a batch that dies on argument parsing.
    The warning is deterministic so two runs of the same plan report it identically.
    """
    if role is None or not role:
        return None, ()
    if _SAFE_ROLE.match(role):
        return role, ()
    return None, (
        f"role {role!r} is not passed to the child ship: a role becomes a `--role` argv "
        "token, and this one is not [A-Za-z0-9][A-Za-z0-9._-]*. The child resolves its "
        "role from the issue's own labels instead",
    )


def ship_handoff_args(assignment: dict[str, Any] | None) -> tuple[str, ...]:
    """A resolved assignment as ``keel ship`` flags for the child run.

    One place, because every batch runner has the same job — a swarm lead launching its
    cluster's ships, a work block handing over the next issue — and a child that resolves
    its own team from config alone would silently drop the per-run overrides the operator
    passed to the parent. Only flags ``keel ship`` actually defines are emitted; a seat
    that is a host subagent rather than a provider is left to the adapter, which is the
    layer that knows how to spawn one.

    The vocabulary is :data:`keel.workblock.DELEGATION_FLAGS` — the same five a work block
    hands down — because the child is the same `keel ship` either way. The two differ only
    in *what* they carry: a work block passes the operator's flags through unresolved,
    while a lead has already resolved its cluster's bench and passes the seats that came
    out. ``--effort`` and ``--team`` ride along so the child's own resolution reproduces
    the parent's rather than re-deriving a different one from config alone.
    """
    if assignment is None:
        return ()
    args: list[str] = []
    implementer = assignment["implementer"]
    if implementer["kind"] == "provider":
        model = implementer["model"]
        args += ["--delegate", implementer["provider"] + (f":{model}" if model else "")]
    for reviewer in assignment["reviewers"]:
        if reviewer["kind"] == "provider":
            model = reviewer["model"]
            args += ["--review-delegate", reviewer["provider"] + (f":{model}" if model else "")]
    role, _warnings = safe_role(assignment["role"])
    if role:
        args += ["--role", role]
    if assignment["effort"]:
        args += ["--effort", assignment["effort"]]
    if assignment["team_profile"]:
        args += ["--team", assignment["team_profile"]]
    return tuple(args)


def worker_seed(cluster: SwarmCluster, *, updated_at: str = "") -> SwarmWorkerStatus:
    """The queued worker record for a cluster, staffed from the cluster's assignment.

    Pure, and separate from the runtime that persists it, because *which provider is
    running this cluster and which lead owns it* is a fact of the plan. The runtime used
    to seed every worker with the ``claude``/``default`` field defaults, so a swarm that
    had resolved a real team still reported the placeholder one on the board.
    """
    assignment = cluster.assignment
    implementer = None if assignment is None else assignment["implementer"]
    model = None if implementer is None else implementer["model"]
    return SwarmWorkerStatus(
        cluster_id=cluster.cluster_id,
        issue=cluster.issues[0] if cluster.issues else 0,
        role=cluster.role,
        agent="claude" if implementer is None else implementer["name"],
        model=model or "default",
        lead="" if assignment is None else assignment["lead"]["name"],
        difficulty="" if cluster.difficulty is None else cluster.difficulty.band,
        step="s0",
        status="queued",
        updated_at=updated_at,
    )


def build_swarm_plan(
    issue_scopes: list[IssueScope] | tuple[IssueScope, ...],
    *,
    swarm_id: str | None = None,
    config: ProjectConfig | None = None,
    overrides: AssignmentOverrides | None = None,
) -> SwarmPlan:
    """Deterministically partition candidate issues into orthogonal Waves and Clusters.

    Every cluster comes out scored (:func:`score_difficulty`) and staffed
    (:func:`resolve_cluster_assignment`). Scoring and staffing happen *after* the
    partition and never feed back into it, which is the property that makes the two
    independently reviewable: re-staffing a backlog — a new ``team.by_difficulty`` row, a
    ``--team`` profile, a ``--delegate`` — moves who runs a cluster and cannot move which
    wave it lands in.
    """
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

    tier3_globs = () if config is None else config.knobs.tier3_globs
    docs_globs = () if config is None else config.knobs.docs_gate_paths
    allowlist_globs = () if config is None else config.knobs.docs_only_allowlist

    # Sort issues for determinism
    sorted_scopes = sorted(issue_scopes, key=lambda s: s.issue)
    scope_by_id = {s.issue: s for s in sorted_scopes}

    # Compute pairwise conflict graph
    conflict_map: dict[int, list[int]] = {s.issue: [] for s in sorted_scopes}
    # Normalize every scope once, outside the O(N²) pair loop: the loop used to pay
    # for ``_normalize_path`` on both sides of every one of the |A|·|B| path pairs,
    # for every pair of issues — about half the per-pair cost. Kept as a list aligned
    # with ``sorted_scopes``, not a dict by issue number: two scopes carrying the same
    # issue number are two scopes, and keying by number would silently compare one
    # of them with the other's files.
    normalized = [_normalized_files(s) for s in sorted_scopes]
    for i, sa in enumerate(sorted_scopes):
        for j in range(i + 1, len(sorted_scopes)):
            sb = sorted_scopes[j]
            if _normalized_scopes_conflict(normalized[i], normalized[j]):
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
            cluster = SwarmCluster(
                cluster_id=f"cluster-{wave_idx}-{issue_num}",
                issues=(issue_num,),
                role=sc.role,
                combined_scope=sc.predicted_files,
                depends_on_issues=deps,
            )
            # Scored from the cluster's own issue list rather than from the issue this
            # loop happens to be on. The partition emits one issue per cluster today, so
            # the two are the same tuple — but the scorer's contract is "how much work is
            # *this cluster*", and a caller that hand-rolled the single-issue case would
            # be the thing to fix on the day clusters group.
            difficulty = score_difficulty(
                cluster_scopes(cluster, scope_by_id),
                tier3_globs=tier3_globs,
                docs_globs=docs_globs,
                allowlist_globs=allowlist_globs,
                dependency_depth=len(deps),
            )
            cluster = replace(cluster, difficulty=difficulty)
            clusters.append(
                replace(
                    cluster,
                    assignment=resolve_cluster_assignment(
                        cluster, difficulty, config=config, overrides=overrides
                    ),
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

    now_id = swarm_id or ("swarm-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S"))
    return SwarmPlan(
        swarm_id=now_id,
        total_issues=len(sorted_scopes),
        waves=tuple(waves),
        conflict_map=frozen_conflicts,
        issue_scopes=scope_by_id,
    )


def seat_label(seat: dict[str, Any] | None) -> str:
    """A resolved seat as ``provider:model@effort`` — the short form both renderers use."""
    if seat is None:
        return "unassigned"
    label = seat["name"]
    if seat["model"]:
        label += f":{seat['model']}"
    if seat["effort"]:
        label += f"@{seat['effort']}"
    return label


def cluster_staffing_lines(cluster: SwarmCluster) -> list[str]:
    """The difficulty and team rows for a cluster, or nothing when it has neither.

    Shared by both plan renderers so the tree and the table cannot describe the same
    cluster differently — the tree existing to be read *instead of* the table is exactly
    why they must agree.
    """
    rows: list[str] = []
    if cluster.difficulty is not None:
        d = cluster.difficulty
        rows.append(
            f"Difficulty: {d.band} (score {d.score}, tier {d.tier}, "
            f"{d.file_count} file(s), depth {d.dependency_depth})"
        )
    if cluster.assignment is not None:
        assignment = cluster.assignment
        reviewers = ", ".join(seat_label(seat) for seat in assignment["reviewers"])
        panel = reviewers or assignment["review_panel"]
        rows.append(
            f"Team: lead {seat_label(assignment['lead'])} → "
            f"implementer {seat_label(assignment['implementer'])}, review {panel}"
        )
    return rows


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
            scope_str = ", ".join(scope_prefix) + ("..." if len(c.combined_scope) > 3 else "")
            dep_str = (
                f" (depends on: {', '.join(f'#{d}' for d in c.depends_on_issues)})"
                if c.depends_on_issues
                else ""
            )
            lines.append(
                f"  • Cluster {c.cluster_id}: {issues_str} [{c.role}] → {scope_str}{dep_str}"
            )
            for detail in cluster_staffing_lines(c):
                lines.append(f"      {detail}")
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
        landing_label = "Direct Batch Landing" if w.eligible_direct_landing else "Sequential Funnel"
        lines.append(f"{mode_icon} Wave {w.wave_index} [{w.mode}] — {landing_label}")

        num_clusters = len(w.clusters)
        for i, c in enumerate(w.clusters):
            is_last_cluster = i == num_clusters - 1
            c_prefix = "└── " if is_last_cluster else "├── "
            c_indent = "    " if is_last_cluster else "│   "

            issues_str = ", ".join(f"#{num}" for num in c.issues)
            lines.append(f"{c_prefix}📦 Cluster {c.cluster_id} ({issues_str}) [{c.role}]")

            scope_items = list(c.combined_scope)
            children = [
                f"Scope: {', '.join(scope_items[:3])}{'...' if len(scope_items) > 3 else ''}",
                *cluster_staffing_lines(c),
            ]
            if c.depends_on_issues:
                dep_issues = ", ".join(f"#{d}" for d in c.depends_on_issues)
                children.append(f"⛓️ Depends on: {dep_issues}")
            # The connector is decided from the assembled list rather than from each
            # optional row in turn: every added row used to mean another place that had to
            # know whether something came after it, and one that guessed wrong drew a tree
            # with two last branches.
            for index, child in enumerate(children):
                branch = "└── " if index == len(children) - 1 else "├── "
                lines.append(f"{c_indent}{branch}{child}")
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
    # The lead and the band it staffed from sit beside the worker, because the board is
    # where an operator asks "who is running this, and why that provider?" — the answer
    # was in the plan JSON and nowhere a running swarm could be watched (#1017).
    cols_hdr = (
        f"│ {'Cluster':<16} │ {'Issue':<6} │ {'Role':<8} │ {'Step':<5} "
        f"│ {'Lead':<12} │ {'Band':<8} │ {'Agent / Model':<16} │ {'Status':<10} │"
    )
    width = len(cols_hdr) - 2
    lines = [
        "╭" + "─" * width + "╮",
        f"│ 🐝 Keel Swarm Live Status — {state.swarm_id:<{width - 30}} │",
        f"{hdr_info[:-1]}{' ' * (width - len(hdr_info) + 2)}│",
        "├" + "─" * width + "┤",
        cols_hdr,
        "├" + "─" * width + "┤",
    ]

    for w in state.workers:
        badge = status_badges.get(w.status, f"[{w.status.upper()}]")
        agent_str = f"{w.agent}:{w.model}"[:16]
        row_str = (
            f"│ {w.cluster_id:<16} │ #{w.issue:<5} │ {w.role:<8} │ {w.step:<5} "
            f"│ {w.lead[:12]:<12} │ {w.difficulty[:8]:<8} │ {agent_str:<16} │ {badge:<10} │"
        )
        lines.append(row_str)

    lines.append("╰" + "─" * width + "╯")
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
    # Atomic + durable, like the checkpoint and activity records. This was a bare
    # `write_text`: the same torn-file-on-interruption bug #872 fixed in its two
    # named files and never reached here, because each writer carried its own
    # copy of the dance instead of sharing one (#932).
    workspace.write_text_atomic(file_path, json.dumps(state.to_dict(), indent=2))
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
                lead=str(w.get("lead", "")),
                difficulty=str(w.get("difficulty", "")),
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
    # `replace` rather than a field-by-field rebuild: the rebuild had to name every
    # field, so each field added to the record (the lead and difficulty band a worker
    # reports, #1017) was silently reset to its default by the first status update.
    updated_workers = [
        replace(
            w,
            step=step if step is not None else w.step,
            status=status if status is not None else w.status,
            updated_at=datetime.datetime.now(datetime.UTC).isoformat(),
            details=details if details is not None else w.details,
        )
        if w.cluster_id == cluster_id
        else w
        for w in state.workers
    ]

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
        "✓" if result.status == "success" else ("⚠️" if result.status == "partial_failure" else "✗")
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


def evaluate_wave_landing_mode(
    wave: SwarmWave,
    pr_diff_map: dict[str, list[str] | tuple[str, ...]],
) -> LandingDecision:
    """Evaluate whether a wave can directly land or requires sequential funneling."""
    cluster_ids = tuple(c.cluster_id for c in wave.clusters)
    if len(cluster_ids) <= 1:
        return LandingDecision(
            mode="direct_batch",
            eligible=True,
            cluster_ids=cluster_ids,
            reason="single_cluster",
        )

    # Check pairwise disjointness of actual diff files
    has_conflict = False
    for i, c1 in enumerate(wave.clusters):
        diff1 = pr_diff_map.get(c1.cluster_id, c1.combined_scope)
        for c2 in wave.clusters[i + 1 :]:
            diff2 = pr_diff_map.get(c2.cluster_id, c2.combined_scope)
            for f1 in diff1:
                for f2 in diff2:
                    if paths_intersect(f1, f2):
                        has_conflict = True
                        break
                if has_conflict:
                    break
            if has_conflict:
                break
        if has_conflict:
            break

    if not has_conflict:
        return LandingDecision(
            mode="direct_batch",
            eligible=True,
            cluster_ids=cluster_ids,
            reason="orthogonal_diff_trees",
        )

    return LandingDecision(
        mode="sequential_funnel",
        eligible=False,
        cluster_ids=cluster_ids,
        reason="overlapping_diff_trees",
    )


def render_swarm_landing_result(result: SwarmLandingResult) -> str:
    """Render human-readable summary of a SwarmLandingResult."""
    status_icon = (
        "✓" if result.status == "success" else ("⚠️" if result.status == "partial_failure" else "✗")
    )
    lines = [
        f"keel swarm land — {result.swarm_id} (wave {result.wave_index})",
        f"  status  : {result.status} {status_icon}",
        f"  mode    : {result.mode}",
        f"  landed  : {', '.join(result.landed_clusters) if result.landed_clusters else 'none'}",
        f"  healed  : {', '.join(result.healed_clusters) if result.healed_clusters else 'none'}",
        f"  failed  : {', '.join(result.failed_clusters) if result.failed_clusters else 'none'}",
    ]
    if result.held_clusters:
        lines.append("  held    : review evidence missing — not landed")
        for cluster_id, reason in result.held_clusters:
            lines.append(f"    {cluster_id}: {reason}")
    return "\n".join(lines)
