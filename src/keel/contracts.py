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
from .extensions import Extension

SCHEMA_VERSION = "keel.command-contract.v1"

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
) -> dict[str, Any]:
    """Build the stable adapter contract shared by ``plan --json`` and dry-run commands."""
    declared_side_effects = command_side_effects(command, requirement, loaded)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "mode": "dry-run" if dry_run else "live",
        "dry_run": dry_run,
        "no_mutations": dry_run,
        "project": project_as_dict(config),
        "graph": command_graph(command),
        "backbone_plan": orchestrator.plan_as_dict(plan),
        "gates": [gate_as_dict(spec) for spec in gates.plan_gates(config, loaded)],
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


def command_side_effects(
    command: str,
    requirement: runtime.CapabilityRequirement,
    loaded: dict[str, list[Extension]],
) -> tuple[str, ...]:
    """Return command side effects plus project capability-derived consent effects."""
    effects: list[str] = list(_BASE_SIDE_EFFECTS.get(command, ()))
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
        },
    }


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
