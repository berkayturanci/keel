"""Static capability vocabulary shared by config, extensions, and runtime detection."""

from __future__ import annotations

KNOWN_CAPABILITIES: tuple[str, ...] = (
    "shell",
    "git",
    "gh",
    "gh-auth",
    "github-mcp",
    "subagents",
    "parallel-subagents",
    "browser",
    "adb",
    "firebase",
    "filesystem-write",
    "worktree",
    "release-publish",
    "secret-access",
    "api-token",
    "production-adjacent",
    "private-setup",
)


def validate_names(names: tuple[str, ...] | list[str], *, source: str) -> list[str]:
    """Return errors for unknown capability names."""

    known = set(KNOWN_CAPABILITIES)
    errors: list[str] = []
    for name in names:
        if name not in known:
            errors.append(
                f"{source}: unknown capability {name!r}; valid: {', '.join(KNOWN_CAPABILITIES)}"
            )
    return errors


def build_capability_requirement(
    command: str,
    config,
    loaded: dict[str, list],
    *,
    pr: int | None = None,
):
    """Build the runtime capability requirement for a given command, config,
    and loaded extensions.
    """
    from . import gates, project_commands, runtime

    del pr
    req = runtime.CapabilityRequirement(
        required=config.knobs.required_capabilities,
        optional=config.knobs.optional_capabilities,
    )
    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError:
        return req
    if project_command := project_commands.get_project_command(config, command):
        req = req.merged(runtime.CapabilityRequirement(
            required=project_command.required_capabilities,
            optional=project_command.optional_capabilities,
        ))

    command_gate_commands = {
        "run-gates", "ship", "pr-loop", "wrap", "work-block", "overnight",
        "implement", "coverage", "deps-audit", "flake-audit",
    }
    if command in command_gate_commands and any(s.kind == "command" for s in specs):
        req = req.merged(runtime.CapabilityRequirement(required=("shell",)))
    worktree_commands = {
        "ship", "pr-loop", "wrap", "work-block", "overnight", "implement"
    }
    github_read_commands = {
        "morning", "review-cycle", "triage", "stale-prs", "regression", "review-all-day",
        "coverage", "deps-audit", "flake-audit", "ci-check",
    }
    if command in worktree_commands:
        req = req.merged(runtime.CapabilityRequirement(required=("git", "worktree"),
                                                       optional=("gh", "gh-auth")))
    elif command in github_read_commands:
        req = req.merged(runtime.CapabilityRequirement(optional=("gh", "gh-auth")))
    for spec in specs:
        if spec.required_capabilities or spec.optional_capabilities:
            req = req.merged(runtime.CapabilityRequirement(
                required=spec.required_capabilities,
                optional=spec.optional_capabilities,
            ))
    return req


def ci_check_capability_requirement(config):
    """Capability requirements for ci-check command."""
    from . import runtime
    optional = ["gh", "gh-auth"]
    if config.knobs.ci_workflows:
        optional.append("raw-actions-logs")
    return runtime.CapabilityRequirement(optional=tuple(optional))


def morning_capability_requirement(config):
    """Capability requirements for morning command."""
    from . import runtime
    required: list[str] = []
    optional: list[str] = ["gh", "gh-auth"]
    pack = config.policy_pack or {}
    health = pack.get("health_providers") if isinstance(pack.get("health_providers"), dict) else {}
    for provider in health.values():
        if not isinstance(provider, dict):
            continue
        required.extend(provider.get("required_capabilities") or ())
        optional.extend(provider.get("optional_capabilities") or ())
    return runtime.CapabilityRequirement(
        required=tuple(dict.fromkeys(required)),
        optional=tuple(dict.fromkeys(optional)),
    )


def scan_capability_requirement(command: str, config):
    """Capability requirements for scan commands (regression, review-all-day)."""
    from . import runtime
    del config
    if command == "regression":
        return runtime.CapabilityRequirement(
            required=("git", "worktree"),
            optional=("gh", "gh-auth", "github-mcp", "parallel-subagents"),
        )
    return runtime.CapabilityRequirement(
        required=("git",),
        optional=("gh", "gh-auth", "github-mcp", "parallel-subagents"),
    )

