"""Operator-facing progress snapshots for active or recent keel runs."""

from __future__ import annotations

from typing import Any

from . import checkpoint, ledger
from . import config as cfg

STATUS_SCHEMA_VERSION = "keel.progress-status.v1"

_WAIT_REASONS = {
    "s1": "needs-input",
    "s6": "ci",
    "s7": "review",
    "s8": "test",
    "s10": "merge-window",
    "s11": "capture",
    "s12": "closeout",
}


def status_contract_as_dict(config: cfg.ProjectConfig) -> dict[str, Any]:
    """Return the progress status contract for adapters and operators."""
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "command": "status",
        "source": {
            "checkpoint": checkpoint.checkpoint_contract_as_dict(config),
            "run_ledger": ledger.ledger_contract_as_dict(config),
        },
        "realtime": False,
        "snapshot_semantics": "last-safe-boundary",
        "consumer_neutral": True,
        "states": ["no-active-run", "active", "waiting", "interrupted", "completed"],
        "item_states": ["shipped", "blocked", "deferred", "skipped"],
    }


def build_status_snapshot(
    *,
    config: cfg.ProjectConfig,
    checkpoint_record: dict[str, Any] | None,
    ledger_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a concise, consumer-neutral progress snapshot."""
    history = _history(ledger_records)
    current = _current(checkpoint_record)
    state = _state(checkpoint_record, history)
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "status": state,
        "project": {
            "repo": config.repo,
            "base_branch": config.base_branch,
            "timezone": config.timezone,
        },
        "current": current,
        "history": history,
        "next": _next_item(checkpoint_record),
    }


def render_status(snapshot: dict[str, Any]) -> str:
    """Render status for humans in actionability order."""
    current = snapshot["current"]
    history = snapshot["history"]
    lines = [f"keel status — {snapshot['status']}"]
    if current:
        lines.append(f"  issue         : {current['issue'] or '-'}")
        lines.append(f"  step          : {current['step'] or '-'}")
        lines.append(f"  wait          : {current['wait_reason'] or '-'}")
        lines.append(f"  pr            : {current['pull_request'] or '-'}")
        lines.append(f"  branch        : {current['branch'] or '-'}")
    lines.append(f"  shipped       : {history['counts']['shipped']}")
    lines.append(f"  blocked       : {history['counts']['blocked']}")
    lines.append(f"  deferred      : {history['counts']['deferred']}")
    lines.append(f"  skipped       : {history['counts']['skipped']}")
    lines.append(f"  next          : {snapshot['next']['issue'] or '-'}")
    return "\n".join(lines)


def _state(checkpoint_record: dict[str, Any] | None, history: dict[str, Any]) -> str:
    if checkpoint_record is None:
        return "completed" if history["total"] else "no-active-run"
    run_state = checkpoint_record.get("state", {})
    if (
        run_state.get("merge") == "merged"
        and run_state.get("capture") not in {"not-started", "failed"}
        and run_state.get("close") == "closed"
    ):
        return "completed"
    stop_reason = checkpoint_record.get("state", {}).get("stop_reason")
    if stop_reason:
        return "interrupted"
    step = checkpoint_record.get("position", {}).get("current_step")
    if step in _WAIT_REASONS:
        return "waiting"
    return "active"


def _current(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    queue = record.get("queue", {})
    position = record.get("position", {})
    identifiers = record.get("identifiers", {})
    state = record.get("state", {})
    step = position.get("current_step")
    return {
        "run_id": record.get("run_id"),
        "command": record.get("command"),
        "target": record.get("target"),
        "issue": queue.get("active_issue"),
        "step": step,
        "completed_steps": list(position.get("completed_steps", [])),
        "pull_request": identifiers.get("pull_request"),
        "branch": identifiers.get("branch"),
        "worktree": identifiers.get("worktree"),
        "head_sha": identifiers.get("head_sha"),
        "wait_reason": state.get("stop_reason") or _WAIT_REASONS.get(step),
        "merge_state": state.get("merge"),
        "capture_state": state.get("capture"),
        "close_state": state.get("close"),
    }


def _next_item(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {"issue": None, "source": "no-active-run"}
    queue = record.get("queue", {})
    active = queue.get("active_issue")
    issues = queue.get("issues", [])
    if active in issues:
        active_index = issues.index(active)
        if active_index + 1 < len(issues):
            return {"issue": issues[active_index + 1], "source": "checkpoint.queue"}
    elif issues:
        return {"issue": issues[0], "source": "checkpoint.queue"}
    return {"issue": None, "source": "checkpoint.queue"}


def _history(records: list[dict[str, Any]]) -> dict[str, Any]:
    items = [_history_item(record) for record in records]
    counts = {"shipped": 0, "blocked": 0, "deferred": 0, "skipped": 0}
    for item in items:
        counts[item["state"]] += 1
    return {
        "total": len(items),
        "counts": counts,
        "items": items,
    }


def _history_item(record: dict[str, Any]) -> dict[str, Any]:
    assessment = record.get("assessment", {})
    merge = assessment.get("merge", {})
    capture = record.get("capture", {})
    state = _item_state(record)
    return {
        "run_id": record.get("run_id"),
        "issue": (record.get("issue") or {}).get("number"),
        "pull_request": (record.get("pull_request") or {}).get("number"),
        "state": state,
        "reason": merge.get("reason") or capture.get("reason"),
        "step": "s12" if state == "shipped" else None,
        "capture": capture.get("status"),
    }


def _item_state(record: dict[str, Any]) -> str:
    assessment = record.get("assessment", {})
    merge = assessment.get("merge", {})
    if merge.get("action") == "skip":
        return "skipped"
    if assessment.get("halted") or merge.get("action") == "defer":
        return "deferred"
    if record.get("verdict", {}).get("blocked") or merge.get("action") == "block":
        return "blocked"
    return "shipped"
