"""Structured command contracts for adapters and parity tests.

The contract is intentionally plain JSON-compatible data. Agent adapters can read it before
mutating work starts, compare required capabilities with the current runtime, and execute the
same command graph without re-deriving keel behavior from prose.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import config as cfg
from . import consent, gates, github_transport, install, model, orchestrator, runtime
from . import ship as ship_decisions
from .extensions import Extension
from .project_commands import get_project_command, list_project_commands

SCHEMA_VERSION = "keel.command-contract.v1"

_SHIP_V2_OVERRIDES: dict[str, str] = {
    "s4": "compound",
    "s7": "compound",
    "s9": "compound",
    "s11": "compound",
}

_STEP_RE = re.compile(
    r"^#{2,3}\s+(?P<id>(?:Step\s+[0-9A-Za-z.]+|s\d+))\s*(?:[\u2014-]\s*)?"
    r"(?P<name>.*)$"
)

_BASE_SIDE_EFFECTS: dict[str, tuple[str, ...]] = {
    "ship": ("git_worktree", "git_branch", "file_edit", "git_push", "pull_request", "comments",
             "reviews", "merge",
             "issue_close", "capture"),
    "ship-v2": ("git_worktree", "git_branch", "file_edit", "git_push", "pull_request",
                "comments", "reviews", "merge",
                "issue_close", "capture"),
    "pr-loop": ("file_edit", "git_commit", "git_push", "comments", "reviews", "check_runs",
                "merge"),
    "review-cycle": ("file_edit", "comments", "reviews", "git_commit", "git_push"),
    "morning": ("issue_read", "pr_read", "report_write"),
    "wrap": ("git_commit", "git_push", "pull_request", "session_recap"),
    "overnight": ("git_branch", "git_push", "pull_request", "comments", "reviews", "merge",
                  "deferral_queue", "session_report"),
    "implement": ("git_worktree", "git_branch", "file_edit", "git_commit", "git_push",
                  "pull_request", "comments"),
    "ci-check": ("check_runs",),
    "triage": ("labels", "comments"),
    "stale-prs": ("comments", "git_checkout", "git_push"),
    "regression": ("git_worktree", "issue_write", "comments"),
    "review-all-day": ("git_checkout", "issue_write", "comments"),
    "coverage": ("git_worktree", "git_checkout", "comments", "labels", "issue_write"),
    "deps-audit": ("comments", "issue_write"),
    "flake-audit": ("issue_write", "comments"),
}

_DEFAULT_FEEDBACK_WORKFLOWS: dict[str, dict[str, Any]] = {
    "pr-loop": {
        "posting_mode": "summary",
        "posting_owner": "orchestrator",
        "reviewer_isolation": {
            "shared_with_ship": True,
            "codename_prefix": "PR-LOOP",
            "no_cross_reading": True,
        },
        "inputs": {
            "auto_detect_current_branch": True,
            "explicit_pr_targets": True,
            "reads_review_comments": True,
            "reads_issue_conversation_comments": True,
        },
        "ci": {
            "recheck_after_push": True,
            "green_required_to_exit": True,
            "degrade_when_logs_unavailable": True,
        },
        "fix_loop": {
            "budget": 3,
            "self_review_before_push": True,
            "reviewer_fanout_after_each_push": True,
        },
        "completion": {
            "marker": None,
            "merge": "handoff",
            "summary_comment": True,
        },
    },
    "review-cycle": {
        "posting_mode": "inline",
        "posting_owner": "orchestrator",
        "reviewer_isolation": {
            "shared_with_ship": True,
            "codename_prefix": "REVIEW-CYCLE",
            "no_cross_reading": True,
        },
        "inputs": {
            "multi_pr": True,
            "sequential_pr_processing": True,
        },
        "review": {
            "parallel_reviewers_within_pr": True,
            "partial_reviewer_failures_degrade": True,
            "severity_histogram_source_of_truth": True,
        },
        "fix_loop": {
            "budget": 3,
            "enabled": True,
        },
        "completion": {
            "marker": "review-cycle-complete",
            "marker_after_summary": True,
            "merge": "never",
            "formal_approval": "never",
        },
    },
}


def available_commands() -> tuple[str, ...]:
    """Every packaged adapter command that can expose a structured contract."""
    return tuple(name.removesuffix(".md") for name in install.adapter_names())


def command_graph(command: str) -> list[dict[str, Any]]:
    """Return the command's step graph as JSON-compatible records.

    ``ship`` and ``ship-v2`` use the fixed keel backbone as the canonical graph. Other
    commands expose their adapter step headings, so adapters can still reason about their
    command-local sequence without parsing Markdown themselves.
    """
    if command in {"ship", "ship-v2"}:
        return [
            {
                "step_id": step.id,
                "step_name": step.name,
                "agentic": step.agentic,
                "slot": step.slot,
                "source": "backbone",
                "profile_step": _SHIP_V2_OVERRIDES.get(step.id, "standard")
                if command == "ship-v2" else "standard",
            }
            for step in model.BACKBONE
        ]
    steps = _adapter_steps(command)
    return steps if steps else []


def build_command_contract(
    *,
    command: str,
    config: cfg.ProjectConfig,
    loaded: dict[str, list[Extension]],
    plan: tuple[orchestrator.PlanItem, ...],
    requirement: runtime.CapabilityRequirement,
    evaluation: runtime.CapabilityEvaluation,
    transport: github_transport.GitHubTransport,
    extension_problems: tuple[str, ...] = (),
    dry_run: bool = True,
    approved_consent_scopes: tuple[str, ...] = (),
    operator: str | None = None,
    target: str | None = None,
    reviewer_override: int | None = None,
    review_tier: int | None = None,
    review_comments: str = "inline",
    jury: bool = False,
    no_jury: bool = False,
    jury_advisory: bool = False,
) -> dict[str, Any]:
    """Build the stable adapter contract shared by ``plan --json`` and dry-run commands."""
    declared_side_effects = command_side_effects(command, config, requirement, loaded)
    graph = command_graph(command)
    if not graph and (project_command := get_project_command(config, command)):
        graph = [{
            "step_id": f"project-command:{project_command.name}",
            "step_name": project_command.name,
            "agentic": bool(project_command.agent_role),
            "slot": None,
            "source": "project_command",
        }]
    contract = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "mode": "dry-run" if dry_run else "live",
        "dry_run": dry_run,
        "no_mutations": dry_run,
        "project": project_as_dict(config),
        "workflow_profile": workflow_profile(command),
        "graph": graph,
        "backbone_plan": orchestrator.plan_as_dict(plan),
        "gates": [gate_as_dict(spec) for spec in gates.plan_gates(config, loaded)],
        "project_commands": [command.as_dict() for command in list_project_commands(config)],
        "extension_hooks": extension_hooks_as_dict(config, loaded),
        "extension_problems": list(extension_problems),
        "required_capabilities": list(requirement.required),
        "optional_capabilities": list(requirement.optional),
        "capabilities": evaluation.as_dict(),
        "github_transport": transport.as_dict(),
        "side_effects": {
            "declared": list(declared_side_effects),
            "mutates_in_dry_run": False,
        },
        "operator_consent": consent.build_consent_contract(
            command=command,
            side_effects=declared_side_effects,
            dry_run=dry_run,
            approved_scopes=approved_consent_scopes,
            operator=operator,
            target=target,
        ),
    }
    if command == "morning":
        contract["morning_contract"] = morning_contract_as_dict(
            config=config,
            evaluation=evaluation,
            transport=transport,
        )
    if command in {"wrap", "overnight"}:
        contract["session_contract"] = session_contract_as_dict(
            command=command,
            config=config,
            transport=transport,
        )
    if command in {"ship", "ship-v2", "pr-loop", "review-cycle", "overnight"}:
        contract["review_merge_contract"] = ship_decisions.resolve_review_contract(
            tier=review_tier,
            reviewer_override=reviewer_override,
            review_comments=review_comments,
            gates=config.gates,
            policy_pack=config.policy_pack,
            jury=jury,
            no_jury=no_jury,
            jury_advisory=jury_advisory,
        )
    if command in {"pr-loop", "review-cycle"}:
        contract["feedback_workflow"] = feedback_workflow_as_dict(config, command)
    return contract


def workflow_profile(command: str) -> dict[str, Any]:
    """First-class workflow profile metadata for command variants."""
    if command == "pr-loop":
        return {
            "name": "pr-loop",
            "profile": "feedback-loop",
            "inherits": "ship.s6-s9",
            "first_class_variant": True,
            "shared_primitives": [
                "linked_worktree_preflight",
                "github_transport",
                "reviewer_isolation",
                "ci_recheck",
                "fixloop",
                "summary_comment",
                "operator_consent",
            ],
            "step_overrides": {
                "merge": {
                    "mode": "handoff",
                    "reason": "pr-loop exits after feedback and CI are satisfied.",
                },
            },
        }
    if command == "review-cycle":
        return {
            "name": "review-cycle",
            "profile": "review-feedback",
            "inherits": "ship.s7-s9",
            "first_class_variant": True,
            "shared_primitives": [
                "multi_pr_targets",
                "reviewer_isolation",
                "posting_mode",
                "severity_histogram",
                "fixloop",
                "completion_marker",
                "operator_consent",
            ],
            "step_overrides": {
                "merge": {
                    "mode": "never",
                    "reason": "review-cycle never merges or posts formal approval.",
                },
            },
        }
    if command == "implement":
        return {
            "name": "implement",
            "profile": "standalone-step",
            "inherits": "ship.s4",
            "first_class_variant": True,
            "shared_primitives": [
                "issue_target",
                "branch",
                "worktree",
                "implementer_routing",
                "operator_consent",
                "handoff",
            ],
            "step_overrides": {},
        }
    if command == "ci-check":
        return {
            "name": "ci-check",
            "profile": "standalone-diagnostic",
            "inherits": None,
            "first_class_variant": True,
            "shared_primitives": [
                "github_transport",
                "check_runs",
                "latest_run_context",
                "log_diagnostics",
                "read_only",
                "routing_recommendation",
            ],
            "step_overrides": {},
        }
    if command == "morning":
        return {
            "name": "morning",
            "profile": "daily-brief",
            "inherits": None,
            "first_class_variant": True,
            "shared_primitives": [
                "date_window",
                "deferral_queue",
                "shipped_since",
                "github_summary",
                "health_providers",
                "priority_sources",
                "ranked_focus",
                "report_output",
            ],
            "step_overrides": {},
        }
    if command == "wrap":
        return {
            "name": "wrap",
            "profile": "session-wrap",
            "inherits": None,
            "first_class_variant": True,
            "shared_primitives": [
                "linked_worktree_preflight",
                "base_branch_guard",
                "configured_gates",
                "conventional_commit",
                "ready_pr_create",
                "session_recap",
                "deferral_queue",
                "operator_consent",
            ],
            "step_overrides": {},
        }
    if command == "overnight":
        return {
            "name": "overnight",
            "profile": "session-overnight",
            "inherits": "ship",
            "first_class_variant": True,
            "shared_primitives": [
                "merge_window",
                "ship_handoff",
                "priority_queue",
                "per_issue_worktree",
                "no_night_merge",
                "blocker_policy",
                "session_report",
                "deferral_queue",
                "stop_conditions",
                "operator_consent",
            ],
            "step_overrides": {},
        }
    if command == "ship-v2":
        return {
            "name": "ship-v2",
            "profile": "compound",
            "inherits": "ship",
            "first_class_variant": True,
            "shared_primitives": [
                "select",
                "branch",
                "worktree",
                "guard",
                "classify",
                "ci",
                "test",
                "merge_window",
                "merge_lock",
                "merge",
                "capture_marker",
                "close",
            ],
            "step_overrides": {
                "s4": {
                    "step": "implement",
                    "mode": "compound",
                    "reason": "compound implement and PR-quality pass",
                },
                "s7": {
                    "step": "review",
                    "mode": "compound",
                    "reason": "persona and diff-aware reviewer fan-out",
                },
                "s9": {
                    "step": "fixloop",
                    "mode": "compound",
                    "reason": "structured PR-feedback resolution",
                },
                "s11": {
                    "step": "capture",
                    "mode": "compound",
                    "reason": "durable-learning capture",
                },
            },
        }
    if command == "ship":
        return {
            "name": "ship",
            "profile": "standard",
            "inherits": None,
            "first_class_variant": True,
            "shared_primitives": [
                "select",
                "branch",
                "guard",
                "implement",
                "classify",
                "ci",
                "review",
                "test",
                "fixloop",
                "merge",
                "capture",
                "close",
            ],
            "step_overrides": {},
        }
    return {
        "name": command,
        "profile": "adapter",
        "inherits": None,
        "first_class_variant": False,
        "shared_primitives": [],
        "step_overrides": {},
    }


def command_side_effects(
    command: str,
    config: cfg.ProjectConfig,
    requirement: runtime.CapabilityRequirement,
    loaded: dict[str, list[Extension]],
) -> tuple[str, ...]:
    """Return command side effects plus project capability-derived consent effects."""
    effects: list[str] = list(_BASE_SIDE_EFFECTS.get(command, ()))
    if project_command := get_project_command(config, command):
        effects.extend(project_command.side_effects)
    effects.extend(consent.capability_side_effects(requirement.required))
    effects.extend(consent.capability_side_effects(requirement.optional))
    for extensions in loaded.values():
        for ext in extensions:
            effects.extend(consent.capability_side_effects(ext.required_capabilities))
            effects.extend(consent.capability_side_effects(ext.optional_capabilities))
    return tuple(dict.fromkeys(effects))


def project_as_dict(config: cfg.ProjectConfig) -> dict[str, Any]:
    """Resolved project config summary safe for adapter planning."""
    return {
        "config_hash": cfg.config_hash(config),
        "extends": config.extends,
        "core_version": config.core_version,
        "base_branch": config.base_branch,
        "owner": config.owner,
        "repo": config.repo,
        "platform": config.platform,
        "timezone": config.timezone,
        "merge_window": config.merge_window,
        "merge_window_mode": config.merge_window_mode,
        "extensions_dir": config.extensions_dir,
        "gates": list(config.gates),
        "extensions": {slot: list(files) for slot, files in sorted(config.extensions.items())},
        "policy_pack": config.policy_pack,
        "knobs": {
            "build_gate_cmd": config.knobs.build_gate_cmd,
            "lint_cmd": config.knobs.lint_cmd,
            "implementer_agents": dict(sorted(config.knobs.implementer_agents.items())),
            "tier3_globs": list(config.knobs.tier3_globs),
            "ci_workflows": dict(sorted(config.knobs.ci_workflows.items())),
            "docs_gate_paths": list(config.knobs.docs_gate_paths),
            "docs_only_allowlist": list(config.knobs.docs_only_allowlist),
            "sot_doc": config.knobs.sot_doc,
            "required_capabilities": list(config.knobs.required_capabilities),
            "optional_capabilities": list(config.knobs.optional_capabilities),
        },
    }


def gate_as_dict(spec: gates.GateSpec) -> dict[str, Any]:
    """Render a planned gate without losing its capability declarations."""
    return asdict(spec)


def extension_hooks_as_dict(
    config: cfg.ProjectConfig, loaded: dict[str, list[Extension]]
) -> dict[str, list[dict[str, Any]]]:
    """Render loaded extension hooks grouped by backbone slot."""
    return {
        slot: [
            {
                "id": ext.id,
                "slot": ext.slot,
                "kind": ext.kind,
                "mode": ext.mode,
                "agent": ext.agent,
                "on_fail": ext.on_fail,
                "anchorable": ext.anchorable,
                "source": ext.source,
                "has_run": ext.run is not None,
                "has_prompt": ext.prompt is not None or bool(ext.body.strip()),
                "required_capabilities": list(ext.required_capabilities),
                "optional_capabilities": list(ext.optional_capabilities),
            }
            for ext in loaded.get(slot, [])
        ]
        for slot in model.SLOTS
    }


def ship_result_as_dict(
    *,
    changed_files: list[str],
    outcomes: list[gates.GateOutcome],
    verdict,
    assessment,
) -> dict[str, Any]:
    """Normalized deterministic result record for ``keel ship --json``."""
    return {
        "changed_files": list(changed_files),
        "changed_file_count": len(changed_files),
        "gate_outcomes": [
            {
                "gate": outcome.gate,
                "ok": outcome.ok,
                "skipped": outcome.skipped,
                "error": outcome.error,
                "findings": [_finding_as_dict(finding) for finding in outcome.findings],
            }
            for outcome in outcomes
        ],
        "verdict": {
            "blocked": verdict.blocked,
            "counts": dict(verdict.counts),
            "findings": [_finding_as_dict(finding) for finding in verdict.findings],
        },
        "assessment": {
            "tier": assessment.tier,
            "reviewers": assessment.reviewers,
            "window_open": assessment.window_open,
            "ci_ok": assessment.ci_ok,
            "merge": {
                "action": assessment.merge.action,
                "reason": assessment.merge.reason,
            },
            "halted": assessment.halted,
            "bypassed_window": assessment.bypassed_window,
            "review_merge_contract": assessment.review_contract,
        },
    }


def standalone_result_as_dict(
    *,
    command: str,
    config: cfg.ProjectConfig,
    target: str | None = None,
    delegate: str | None = None,
    transport: github_transport.GitHubTransport | None = None,
    evaluation: runtime.CapabilityEvaluation | None = None,
) -> dict[str, Any]:
    """Deterministic dry-run result records for standalone non-ship commands."""
    if command == "implement":
        issue_id = _target_identifier(target)
        return {
            "command": command,
            "target": target,
            "base_branch": config.base_branch,
            "branch_pattern": f"feature/issue-{issue_id}-<slug>",
            "worktree_path_pattern": f"worktrees/issue-{issue_id}",
            "implementer": {
                "source": "delegate" if delegate else "project-routing-or-host",
                "selected": delegate,
                "routing_keys": sorted(config.knobs.implementer_agents),
            },
            "handoff": {
                "opens_pr": True,
                "merges": False,
                "next_commands": ["ship", "pr-loop"],
            },
        }
    if command == "ci-check":
        resolved = transport.as_dict() if transport is not None else {}
        return {
            "command": command,
            "target": target,
            "base_branch": config.base_branch,
            "ci_workflows": dict(sorted(config.knobs.ci_workflows.items())),
            "latest_run_context": {
                "limit": 3,
                "selected": "newest available run",
                "history": "previous runs used for flake or infra classification",
            },
            "diagnostics": {
                "read_only": True,
                "log_tail": "available when the selected GitHub transport exposes logs",
                "classifications": ["real-failure", "flake", "infra-or-quota"],
                "proposed_fix_count": 1,
            },
            "github_transport": resolved,
            "routing": {
                "never_direct_merge": True,
                "recommendations": ["review-cycle", "pr-loop", "ship", "flake-audit"],
            },
        }
    if command == "morning":
        return {
            "command": command,
            "target": target,
            "base_branch": config.base_branch,
            "brief": morning_contract_as_dict(
                config=config,
                evaluation=evaluation,
                transport=transport,
            ),
            "execution": {
                "runs_project_health_commands": False,
                "writes_reports": False,
                "live_work_owner": "adapter-or-extension-after-consent",
            },
        }
    if command in {"wrap", "overnight"}:
        return {
            "command": command,
            "target": target,
            "base_branch": config.base_branch,
            "session": session_contract_as_dict(
                command=command,
                config=config,
                transport=transport,
            ),
            "execution": {
                "runs_gates": False,
                "creates_prs": False,
                "merges": False,
                "writes_reports": False,
                "live_work_owner": "adapter-after-consent",
            },
        }
    if command in {"pr-loop", "review-cycle"}:
        workflow = feedback_workflow_as_dict(config, command)
        return {
            "command": command,
            "target": target,
            "base_branch": config.base_branch,
            "feedback_workflow": workflow,
            "execution": {
                "commits": False,
                "pushes": False,
                "posts_comments": False,
                "merges": False,
                "live_work_owner": "adapter-after-consent",
            },
        }
    return {"command": command, "target": target}


def feedback_workflow_as_dict(config: cfg.ProjectConfig, command: str) -> dict[str, Any]:
    """Return command-specific feedback-loop policy with project overrides applied."""
    defaults = _DEFAULT_FEEDBACK_WORKFLOWS.get(command, {})
    policy = _feedback_workflow_policy(config).get(command, {})
    return _deep_merge(defaults, policy)


def session_contract_as_dict(
    *,
    command: str,
    config: cfg.ProjectConfig,
    transport: github_transport.GitHubTransport | None = None,
) -> dict[str, Any]:
    """Project-neutral session workflow contract for ``wrap`` and ``overnight``."""
    pack = config.policy_pack or {}
    reports = pack.get("reports") if isinstance(pack.get("reports"), dict) else {}
    github = transport.as_dict() if transport is not None else {}
    base = {
        "timezone": config.timezone,
        "merge_window": config.merge_window,
        "merge_window_mode": config.merge_window_mode,
        "base_branch": config.base_branch,
        "github_transport": github,
        "reports": _report_destinations(reports),
        "deferral_queue": _deferral_queue_as_dict(reports),
        "project_policy_sources": {
            "gates": list(config.gates),
            "extensions": {slot: list(files) for slot, files in sorted(config.extensions.items())},
            "risk_rules": [rule.get("id") for rule in pack.get("risk_rules", [])
                           if isinstance(rule, dict)],
            "source_of_truth_doc": config.knobs.sot_doc,
        },
    }
    if command == "wrap":
        base["wrap"] = {
            "workspace_preflight": {
                "must_run_from_linked_worktree": True,
                "abort_on_base_branch": True,
                "git_dir_rule": "main worktree returns .git; linked worktree uses .git/worktrees",
            },
            "quality_gates": {
                "runner": "keel run-gates",
                "changed_file_policy_source": "policy_pack + extension hooks",
            },
            "commit": {
                "format": "conventional-commits",
                "supports_closes_issue": True,
            },
            "pull_request": {
                "ready_not_draft": True,
                "base_branch": config.base_branch,
                "requires_pr_write": True,
                "body_sections": ["Summary", "Docs Impact", "Test Plan"],
            },
            "recap": {
                "report_key": "session",
                "path": reports.get("session") or reports.get("wrap"),
                "status": "configured" if reports.get("session") or reports.get("wrap")
                else "unconfigured",
            },
        }
    elif command == "overnight":
        base["overnight"] = {
            "mode_source": {
                "command": "keel window",
                "shared_with_ship": True,
                "timezone": config.timezone,
                "merge_window": config.merge_window,
            },
            "merge_policy": {
                "day": "ship may merge reviewed CI-green PRs inside the configured window",
                "night": "no merge outside the configured window except true blockers",
                "blocker_source": "ship blocker heuristics + project policy extensions",
                "cannot_weaken_core_window": True,
            },
            "queue": {
                "source": "project policy or adapter query",
                "tiers": ["T0-blocker", "T1-open-prs", "T2-coverage", "T3-ci-quality",
                          "T4-modernization", "T5-docs-backlog"],
            },
            "ship_handoff": {
                "command": "ship",
                "passes_operator_consent_scope": True,
                "per_issue_worktree": True,
            },
            "report": {
                "night_key": "overnight",
                "day_key": "session",
                "night_path": reports.get("overnight") or reports.get("morning"),
                "day_path": reports.get("session"),
                "status": "configured" if any(
                    reports.get(key) for key in ("overnight", "morning", "session")
                ) else "unconfigured",
            },
            "stop_conditions": [
                "merge-window-close",
                "time-budget-exhausted",
                "max-items-reached",
                "hard-blocker",
                "three-consecutive-unresolved-ci-failures",
                "user-cancelled",
            ],
        }
    return base


def morning_contract_as_dict(
    *,
    config: cfg.ProjectConfig,
    evaluation: runtime.CapabilityEvaluation | None = None,
    transport: github_transport.GitHubTransport | None = None,
) -> dict[str, Any]:
    """Project-neutral morning briefing contract for adapters and dry-run output."""
    pack = config.policy_pack or {}
    reports = pack.get("reports") if isinstance(pack.get("reports"), dict) else {}
    health = pack.get("health_providers") if isinstance(pack.get("health_providers"), dict) else {}
    github = transport.as_dict() if transport is not None else {}
    github_status = "available" if github.get("transport") not in {None, "none"} else "degraded"
    return {
        "timezone": config.timezone,
        "merge_window": config.merge_window,
        "base_branch": config.base_branch,
        "sections": [
            {
                "id": "deferrals",
                "source": "shared_queue",
                "status": "configured" if reports.get("deferrals") else "unconfigured",
                "path": reports.get("deferrals"),
            },
            {
                "id": "shipped_since_last_brief",
                "source": "github",
                "transport": github.get("transport"),
                "status": github_status,
            },
            {
                "id": "github_status",
                "source": "github",
                "transport": github.get("transport"),
                "status": github_status,
            },
            {
                "id": "project_health",
                "source": "policy_pack.health_providers",
                "status": "configured" if health else "unconfigured",
            },
            {
                "id": "ranked_focus",
                "source": "policy_pack + github",
                "status": "configured" if _priority_sources(config, reports) else "unconfigured",
            },
        ],
        "health_providers": [
            _health_provider_as_dict(name, provider, evaluation)
            for name, provider in sorted(health.items())
            if isinstance(provider, dict)
        ],
        "priority_sources": _priority_sources(config, reports),
        "reports": _report_destinations(reports),
        "deferral_queue": _deferral_queue_as_dict(reports),
        "missing_optional_policy": "unavailable-not-success",
    }


def _health_provider_as_dict(
    name: str,
    provider: dict[str, Any],
    evaluation: runtime.CapabilityEvaluation | None,
) -> dict[str, Any]:
    required = tuple(provider.get("required_capabilities") or ())
    optional = tuple(provider.get("optional_capabilities") or ())
    missing_required = _missing_capabilities(required, evaluation)
    missing_optional = _missing_capabilities(optional, evaluation)
    status = "available"
    if missing_required:
        status = "blocked"
    elif missing_optional:
        status = "unavailable"
    elif provider.get("command") is None and provider.get("kind") == "project-command":
        status = "unavailable"
    return {
        "name": name,
        "kind": provider.get("kind"),
        "command": provider.get("command"),
        "reports": list(provider.get("reports") or ()),
        "required_capabilities": list(required),
        "optional_capabilities": list(optional),
        "missing_required_capabilities": list(missing_required),
        "missing_optional_capabilities": list(missing_optional),
        "status": status,
    }


def _missing_capabilities(
    names: tuple[str, ...],
    evaluation: runtime.CapabilityEvaluation | None,
) -> tuple[str, ...]:
    if evaluation is None:
        return ()
    missing = set(evaluation.missing_required) | set(evaluation.missing_optional)
    return tuple(name for name in names if name in missing)


def _priority_sources(config: cfg.ProjectConfig, reports: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if config.knobs.sot_doc:
        sources.append({"id": "source_of_truth", "path": config.knobs.sot_doc})
    if reports.get("priorities"):
        sources.append({"id": "priorities_report", "path": reports["priorities"]})
    if config.knobs.ci_workflows:
        sources.append({"id": "ci_workflows", "names": sorted(config.knobs.ci_workflows)})
    return sources


def _report_destinations(reports: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "path": value,
            "write_status": "skipped-in-dry-run",
        }
        for key, value in sorted(reports.items())
    }


def _deferral_queue_as_dict(reports: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "policy_pack.reports.deferrals",
        "path": reports.get("deferrals"),
        "status": "configured" if reports.get("deferrals") else "unconfigured",
        "shared_with": ["ship", "overnight", "wrap", "morning"],
    }


def _feedback_workflow_policy(config: cfg.ProjectConfig) -> dict[str, dict[str, Any]]:
    pack = config.policy_pack or {}
    policy = pack.get("workflow_policies")
    if not isinstance(policy, dict):
        return {}
    return {
        name: value
        for name, value in policy.items()
        if isinstance(name, str) and isinstance(value, dict)
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            merged[key] = _deep_merge(value, {})
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    return merged


def _target_identifier(target: str | None) -> str:
    if not target:
        return "selected-issue"
    match = re.search(r"#?(\d+)", target)
    if match:
        return match.group(1)
    return re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-") or "selected-issue"


def _adapter_steps(command: str) -> list[dict[str, Any]]:
    path = Path(install.ADAPTERS) / f"{command}.md"
    if not path.exists():
        return []
    steps: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _STEP_RE.match(line)
        if not match:
            continue
        raw_id = match.group("id").strip()
        step_id = raw_id.lower().replace(" ", "-").replace(".", "-")
        steps.append({
            "step_id": step_id,
            "step_name": match.group("name").strip() or raw_id,
            "agentic": "agent" in match.group("name").lower(),
            "slot": None,
            "source": "adapter",
        })
    return steps


def _finding_as_dict(finding) -> dict[str, Any]:
    return {
        "severity": finding.severity,
        "message": finding.message,
        "source": finding.source,
        "path": finding.path,
        "line": finding.line,
        "anchorable": finding.anchorable,
    }
