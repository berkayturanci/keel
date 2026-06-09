"""Shared work-block contract for daytime and overnight queue runs."""

from __future__ import annotations

from typing import Any

from . import checkpoint, ledger
from . import config as cfg

WORK_BLOCK_SCHEMA_VERSION = "keel.work-block.v1"
OUTCOME_BUCKETS = (
    "shipped",
    "pr_open_not_merged",
    "deferred",
    "blocked",
    "skipped",
    "needs_input",
)
STOP_CONDITIONS = (
    "queue-exhausted",
    "max-items-reached",
    "time-budget-exhausted",
    "operator-pause",
    "consent-gap",
    "needs-input",
    "blocked-finding",
    "merge-window-close",
    "three-consecutive-unresolved-ci-failures",
    "user-cancelled",
)


def contract_as_dict(
    *,
    config: cfg.ProjectConfig,
    mode: str,
    transport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the consumer-neutral queue/work-block primitive contract."""
    if mode not in {"daytime", "overnight"}:
        raise ValueError("work-block mode must be daytime or overnight")
    github = transport or {}
    return {
        "schema_version": WORK_BLOCK_SCHEMA_VERSION,
        "mode": mode,
        "base_branch": config.base_branch,
        "timezone": config.timezone,
        "merge_window": config.merge_window,
        "merge_window_mode": config.merge_window_mode,
        "github_transport": github,
        "queue": {
            "accepted_inputs": ["explicit_issue_numbers", "queue_selector"],
            "explicit_issue_order": "as-provided",
            "selector_order": [
                "priority",
                "issue_number",
            ],
            "snapshot_once_per_session": True,
            "refresh_readiness_between_issues": True,
        },
        "per_issue": {
            "handoff_command": "ship",
            "isolated_branch_worktree": True,
            "child_inherits_operator_consent_scope": True,
            "child_honors_capture_contract": True,
            "child_appends_run_ledger": True,
            "child_uses_merge_lock": True,
            "child_rechecks_merge_window": True,
        },
        "failure_policy": {
            "branch_contamination_policy": "one issue cannot reuse another issue worktree",
            "non_ready_policy": "skip-or-stop-by-mode-policy",
            "continue_after_blocked": mode == "overnight",
            "daytime_operator_can_redirect_between_items": mode == "daytime",
        },
        "checkpoint": checkpoint.checkpoint_contract_as_dict(config),
        "run_ledger": ledger.ledger_contract_as_dict(config),
        "final_report": {
            "required": True,
            "outcome_buckets": list(OUTCOME_BUCKETS),
            "source": "child ship results + run ledger + checkpoint",
        },
        "stop_conditions": list(STOP_CONDITIONS),
        "shared_with": ["overnight"] if mode == "daytime" else ["work-block"],
    }
