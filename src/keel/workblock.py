"""Shared work-block contract for daytime and overnight queue runs."""

from __future__ import annotations

from collections.abc import Sequence
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
#: The staffing flags a work block / overnight session accepts and hands down.
#: Before #1017 a batch passed only ``operator_consent.delegated_agent_scope``, so a run
#: launched with ``--delegate codex --effort high`` produced children that re-resolved
#: from config and ran the default team — the operator's choice reached the parent and
#: died there.
DELEGATION_FLAGS = ("--delegate", "--review-delegate", "--effort", "--team", "--reviewers")

#: The child handoff every queued issue is dispatched through. Written out in full so the
#: adapter prose and the published contract cannot describe different handoffs.
CHILD_HANDOFF_TEMPLATE = (
    "/keel:ship <issue> [--delegate <provider[:model]>] [--review-delegate <provider>] "
    "[--effort <low|medium|high>] [--team <profile>] [--reviewers <n>]"
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


def child_ship_args(
    *,
    delegate: str | None = None,
    review_delegates: Sequence[str] = (),
    effort: str | None = None,
    team_profile: str | None = None,
    reviewer_override: int | None = None,
) -> tuple[str, ...]:
    """The staffing flags to append to every child ship handoff — set values only.

    Deterministic and ordered, because the child handoff line is quoted verbatim into the
    session report: a set that reordered itself between two issues would read as two
    different teams having run.
    """
    args: list[str] = []
    if delegate:
        args += ["--delegate", delegate]
    for reviewer in review_delegates:
        if reviewer:
            args += ["--review-delegate", reviewer]
    if effort:
        args += ["--effort", effort]
    if team_profile:
        args += ["--team", team_profile]
    if reviewer_override is not None:
        args += ["--reviewers", str(reviewer_override)]
    return tuple(args)


def delegation_as_dict(
    *,
    delegate: str | None = None,
    review_delegates: Sequence[str] = (),
    effort: str | None = None,
    team_profile: str | None = None,
    reviewer_override: int | None = None,
) -> dict[str, Any]:
    """What this block hands to each child ship, and what it must report afterwards."""
    return {
        "flags": list(DELEGATION_FLAGS),
        "child_handoff_template": CHILD_HANDOFF_TEMPLATE,
        "propagate_to_every_child_ship": True,
        "record_effective_values_in_session_report": True,
        "effective": {
            "delegate": delegate,
            "review_delegates": [reviewer for reviewer in review_delegates if reviewer],
            "effort": effort,
            "team": team_profile,
            "reviewers": reviewer_override,
        },
        "child_args": list(
            child_ship_args(
                delegate=delegate,
                review_delegates=review_delegates,
                effort=effort,
                team_profile=team_profile,
                reviewer_override=reviewer_override,
            )
        ),
    }


def contract_as_dict(
    *,
    config: cfg.ProjectConfig,
    mode: str,
    transport: dict[str, Any] | None = None,
    delegation: dict[str, Any] | None = None,
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
            "child_inherits_team_assignment": True,
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
        "delegation": delegation if delegation is not None else delegation_as_dict(),
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
