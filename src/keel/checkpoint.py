"""Resumable checkpoint helpers for keel work runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config as cfg
from . import model

CHECKPOINT_SCHEMA_VERSION = "keel.checkpoint.v1"
DEFAULT_CHECKPOINT_PATH = ".keel/state/checkpoint.json"
RECORD_TYPE_RUN_CHECKPOINT = "run_checkpoint"

COMMANDS = ("ship", "work-block", "overnight")
STEP_IDS = tuple(step.id for step in model.BACKBONE)
MERGE_STATES = ("not-started", "pending", "merged", "failed", "skipped")
CAPTURE_STATES = ("not-started", "applied", "deferred", "skipped", "failed")
CLOSE_STATES = ("not-started", "closed", "failed")
LIVE_PR_STATES = ("unknown", "missing", "open", "merged", "closed")
LIVE_WORKTREE_STATES = ("unknown", "present", "missing")

_IDEMPOTENT_STEPS = {
    "s0": "load-config",
    "s1": "select-or-reconcile-issue",
    "s2": "ensure-branch-and-worktree",
    "s3": "rerun-guards",
    "s4": "continue-implementation",
    "s5": "reclassify-diff",
    "s6": "recheck-ci",
    "s7": "rerun-review",
    "s8": "rerun-test",
    "s9": "continue-fixloop",
    "s10": "revalidate-merge-state",
    "s11": "run-or-verify-capture",
    "s12": "run-or-verify-close",
}


class CheckpointError(ValueError):
    """Raised when a checkpoint cannot be decoded as the stable schema."""


def checkpoint_contract_as_dict(config: cfg.ProjectConfig) -> dict[str, Any]:
    """Return the project-neutral checkpoint storage and resume contract."""
    path, source = configured_checkpoint_path(config)
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "format": "json",
        "path": path,
        "path_source": source,
        "missing_handling": "no-checkpoint",
        "write_owner": ["ship", "work-block", "overnight"],
        "resume_command": "resume",
        "checkpoint_command": "checkpoint",
        "steps": [
            {
                "step_id": step.id,
                "step_name": step.name,
                "safe_boundary": step.id in _IDEMPOTENT_STEPS,
                "resume_action": _IDEMPOTENT_STEPS.get(step.id),
            }
            for step in model.BACKBONE
        ],
        "consumer_neutral": True,
    }


def configured_checkpoint_path(config: cfg.ProjectConfig) -> tuple[str, str]:
    """Return the configured checkpoint path and source."""
    pack = config.policy_pack or {}
    reports = pack.get("reports") if isinstance(pack.get("reports"), dict) else {}
    value = reports.get("checkpoint")
    if isinstance(value, str) and value.strip():
        return value, "policy_pack.reports.checkpoint"
    return DEFAULT_CHECKPOINT_PATH, "default"


def resolve_path(root: str | Path, config: cfg.ProjectConfig) -> Path:
    """Resolve the checkpoint path under ``root`` and reject escapes."""
    raw, _ = configured_checkpoint_path(config)
    path = Path(raw)
    if path.is_absolute():
        raise CheckpointError("checkpoint path must be relative to the project root")
    root_path = Path(root).resolve()
    resolved = (root_path / path).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise CheckpointError("checkpoint path escapes the project root") from exc
    return resolved


def build_checkpoint_record(
    *,
    run_id: str,
    command: str,
    current_step: str,
    base_branch: str,
    target: str | None = None,
    issue_queue: list[int] | None = None,
    active_issue: int | None = None,
    branch: str | None = None,
    worktree: str | None = None,
    pull_request: int | None = None,
    head_sha: str | None = None,
    completed_steps: list[str] | None = None,
    last_gate: str | None = None,
    last_review: str | None = None,
    last_check: str | None = None,
    merge_state: str = "not-started",
    capture_state: str = "not-started",
    close_state: str = "not-started",
    stop_reason: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic checkpoint record."""
    record = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_RUN_CHECKPOINT,
        "run_id": run_id,
        "command": command,
        "target": target,
        "queue": {
            "issues": list(issue_queue or ()),
            "active_issue": active_issue,
        },
        "position": {
            "current_step": current_step,
            "completed_steps": list(completed_steps or ()),
        },
        "identifiers": {
            "base_branch": base_branch,
            "branch": branch,
            "worktree": worktree,
            "pull_request": pull_request,
            "head_sha": head_sha,
        },
        "state": {
            "last_gate": last_gate,
            "last_review": last_review,
            "last_check": last_check,
            "merge": merge_state,
            "capture": capture_state,
            "close": close_state,
            "stop_reason": stop_reason,
        },
        "resume": {
            "safe_boundary": current_step in _IDEMPOTENT_STEPS,
            "action": _IDEMPOTENT_STEPS.get(current_step),
            "must_reconcile_live_state": True,
            "repeat_policy": {
                "merge": "never-repeat-if-live-pr-merged",
                "comments": "idempotent-anchor-or-skip",
                "worktree": "refuse-unrelated-existing-path",
            },
        },
    }
    validate_checkpoint(record)
    return record


def encode_checkpoint(record: dict[str, Any]) -> str:
    """Encode one checkpoint as stable JSON."""
    validate_checkpoint(record)
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def parse_checkpoint(text: str) -> dict[str, Any]:
    """Parse and validate one checkpoint record."""
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckpointError("invalid JSON") from exc
    validate_checkpoint(record)
    return record


def read_checkpoint(path: str | Path) -> dict[str, Any] | None:
    """Read a checkpoint; a missing checkpoint means there is no resumable run."""
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return None
    return parse_checkpoint(checkpoint_path.read_text(encoding="utf-8"))


def write_checkpoint(path: str | Path, record: dict[str, Any]) -> None:
    """Write one validated checkpoint, replacing the previous resume point."""
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(encode_checkpoint(record), encoding="utf-8")


def resume_plan_as_dict(
    record: dict[str, Any] | None,
    *,
    live_pr_state: str = "unknown",
    live_worktree_state: str = "unknown",
) -> dict[str, Any]:
    """Return a deterministic dry-run resume plan after live-state reconciliation."""
    if live_pr_state not in LIVE_PR_STATES:
        raise CheckpointError("unsupported live_pr_state")
    if live_worktree_state not in LIVE_WORKTREE_STATES:
        raise CheckpointError("unsupported live_worktree_state")
    if record is None:
        return {
            "status": "no-checkpoint",
            "can_resume": False,
            "reason": "checkpoint file is missing",
            "next_step": None,
            "resume_action": None,
            "reconcile": _live_state(live_pr_state, live_worktree_state),
            "warnings": [],
        }
    validate_checkpoint(record)
    warnings: list[str] = []
    state = record["state"]
    identifiers = record["identifiers"]
    current_step = record["position"]["current_step"]
    status = "ready"
    can_resume = True
    reason = "resume from the recorded safe boundary"
    next_step = current_step
    action = record["resume"]["action"]

    if live_pr_state == "merged" or state["merge"] == "merged":
        if state["capture"] in {"not-started", "failed"}:
            status = "needs-capture"
            next_step = "s11"
            action = _IDEMPOTENT_STEPS["s11"]
            reason = "merge is complete; resume at capture without repeating merge"
        elif state["close"] in {"not-started", "failed"}:
            status = "needs-close"
            next_step = "s12"
            action = _IDEMPOTENT_STEPS["s12"]
            reason = "capture is complete or skipped; resume at close"
        else:
            status = "complete"
            can_resume = False
            next_step = None
            action = None
            reason = "merge, capture, and close are already complete"
    elif live_pr_state == "closed" and identifiers.get("pull_request"):
        status = "ambiguous"
        can_resume = False
        reason = "checkpoint references a pull request that live state reports closed"
        warnings.append("reopen the PR or reconcile the checkpoint before resuming")
    elif live_worktree_state == "missing" and identifiers.get("worktree"):
        status = "ambiguous"
        can_resume = False
        reason = "checkpoint references a worktree that live state reports missing"
        warnings.append("recreate or reconcile the recorded worktree before resuming")
    elif live_pr_state == "missing" and identifiers.get("pull_request"):
        status = "ambiguous"
        can_resume = False
        reason = "checkpoint references a pull request that live state reports missing"
        warnings.append("verify whether the PR was deleted or the checkpoint is stale")
    elif current_step == "s6":
        status = "waiting-on-ci"
        reason = "resume by rechecking CI before review, test, or merge"
    elif identifiers.get("pull_request"):
        status = "pr-open"
        reason = "resume against the recorded pull request after refreshing live state"

    return {
        "status": status,
        "can_resume": can_resume,
        "reason": reason,
        "next_step": next_step,
        "resume_action": action,
        "checkpoint": record,
        "reconcile": _live_state(live_pr_state, live_worktree_state),
        "warnings": warnings,
    }


COVERAGE_STATES = ("covered", "missing", "stale-step")


def covering_checkpoint(
    record: dict[str, Any] | None,
    run_id: str,
    expected_step: str,
) -> dict[str, Any]:
    """Decide whether a checkpoint covers ``run_id`` at ``expected_step``.

    Pure and deterministic — no I/O. ``record`` is the current checkpoint
    (or ``None`` when no checkpoint file exists); ``expected_step`` must be a
    backbone step id. A run is *covered* when its checkpoint is for the same
    ``run_id`` and the run actually progressed to ``expected_step`` (the
    recorded ``current_step`` is at or past it, or the step is in
    ``completed_steps``). The result distinguishes:

    * ``covered`` — a current checkpoint for this run reached ``expected_step``;
    * ``missing`` — no checkpoint, or a checkpoint for a different run;
    * ``stale-step`` — a checkpoint for this run that has not reached the step.
    """
    if expected_step not in STEP_IDS:
        raise CheckpointError("expected_step must be a backbone step id")
    expected_index = STEP_IDS.index(expected_step)
    if record is None:
        return {
            "status": "missing",
            "covered": False,
            "run_id": run_id,
            "expected_step": expected_step,
            "checkpoint_run_id": None,
            "checkpoint_step": None,
            "reason": f"no current checkpoint for run {run_id} at step {expected_step}",
        }
    validate_checkpoint(record)
    checkpoint_run_id = record.get("run_id")
    position = record["position"]
    current_step = position["current_step"]
    completed = position.get("completed_steps", [])
    base = {
        "run_id": run_id,
        "expected_step": expected_step,
        "checkpoint_run_id": checkpoint_run_id,
        "checkpoint_step": current_step,
    }
    if checkpoint_run_id != run_id:
        return {
            **base,
            "status": "missing",
            "covered": False,
            "reason": (
                f"no current checkpoint for run {run_id} at step {expected_step} "
                f"(checkpoint is for run {checkpoint_run_id})"
            ),
        }
    reached = expected_step in completed or STEP_IDS.index(current_step) >= expected_index
    if not reached:
        return {
            **base,
            "status": "stale-step",
            "covered": False,
            "reason": (
                f"no current checkpoint for run {run_id} at step {expected_step} "
                f"(run is at {current_step})"
            ),
        }
    return {
        **base,
        "status": "covered",
        "covered": True,
        "reason": f"checkpoint for run {run_id} reached step {expected_step}",
    }


def find_orphans(
    *,
    live_branches: list[str] | None = None,
    live_pull_requests: list[int] | None = None,
    checkpoint_record: dict[str, Any] | None = None,
    ledger_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return live branches/PRs with no covering checkpoint or ledger record.

    Pure and deterministic — no I/O. An *orphan* is a branch or pull request
    that exists on the git/transport side but is referenced by neither the
    current checkpoint nor any ledger record, i.e. keel has no covering state
    for it (the GAP-13 hazard from the git side). Advisory only.
    """
    known_branches, known_prs = _known_references(checkpoint_record, ledger_records)
    orphan_branches = [
        branch for branch in (live_branches or []) if branch not in known_branches
    ]
    orphan_prs = [pr for pr in (live_pull_requests or []) if pr not in known_prs]
    return {
        "branches": orphan_branches,
        "pull_requests": orphan_prs,
        "orphan_count": len(orphan_branches) + len(orphan_prs),
        "known_branches": sorted(known_branches),
        "known_pull_requests": sorted(known_prs),
    }


def _known_references(
    checkpoint_record: dict[str, Any] | None,
    ledger_records: list[dict[str, Any]] | None,
) -> tuple[set[str], set[int]]:
    branches: set[str] = set()
    prs: set[int] = set()
    if checkpoint_record is not None:
        identifiers = checkpoint_record.get("identifiers", {})
        branch = identifiers.get("branch")
        if isinstance(branch, str) and branch:
            branches.add(branch)
        pull_request = identifiers.get("pull_request")
        if isinstance(pull_request, int):
            prs.add(pull_request)
    for record in ledger_records or []:
        git = record.get("git") if isinstance(record.get("git"), dict) else {}
        branch = git.get("branch")
        if isinstance(branch, str) and branch:
            branches.add(branch)
        pr_entry = record.get("pull_request")
        if isinstance(pr_entry, dict):
            number = pr_entry.get("number")
            if isinstance(number, int):
                prs.add(number)
    return branches, prs


def validate_checkpoint(record: Any) -> None:
    """Validate the stable checkpoint shape."""
    if not isinstance(record, dict):
        raise CheckpointError("checkpoint must be an object")
    if record.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointError("unsupported schema_version")
    if record.get("record_type") != RECORD_TYPE_RUN_CHECKPOINT:
        raise CheckpointError("unsupported record_type")
    if record.get("command") not in COMMANDS:
        raise CheckpointError("unsupported command")
    queue = record.get("queue")
    if not isinstance(queue, dict) or not isinstance(queue.get("issues"), list):
        raise CheckpointError("queue must be an object with issues")
    position = record.get("position")
    if not isinstance(position, dict) or position.get("current_step") not in _IDEMPOTENT_STEPS:
        raise CheckpointError("unsupported current_step")
    completed = position.get("completed_steps")
    if not isinstance(completed, list) or any(step not in STEP_IDS for step in completed):
        raise CheckpointError("unsupported completed_steps")
    identifiers = record.get("identifiers")
    if not isinstance(identifiers, dict) or "base_branch" not in identifiers:
        raise CheckpointError("identifiers must include base_branch")
    state = record.get("state")
    if not isinstance(state, dict):
        raise CheckpointError("state must be an object")
    if state.get("merge") not in MERGE_STATES:
        raise CheckpointError("unsupported merge state")
    if state.get("capture") not in CAPTURE_STATES:
        raise CheckpointError("unsupported capture state")
    if state.get("close") not in CLOSE_STATES:
        raise CheckpointError("unsupported close state")
    resume = record.get("resume")
    if not isinstance(resume, dict):
        raise CheckpointError("resume must be an object")
    if resume.get("action") != _IDEMPOTENT_STEPS[position["current_step"]]:
        raise CheckpointError("resume action does not match current_step")
    if not isinstance(resume.get("repeat_policy"), dict):
        raise CheckpointError("resume repeat_policy must be an object")


def _live_state(live_pr_state: str, live_worktree_state: str) -> dict[str, str]:
    return {
        "pull_request": live_pr_state,
        "worktree": live_worktree_state,
        "source": "live-git-github-state-or-adapter-supplied-dry-run-state",
    }
