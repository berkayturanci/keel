"""Runtime capability detection and requirement evaluation.

Capabilities describe what the current execution environment can do. They are runtime
facts, not project policy: whether local tools exist, whether GitHub access is available,
and whether live mutation classes are possible. The detector is injectable so tests stay
offline and deterministic.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import capabilities

KNOWN_CAPABILITIES = capabilities.KNOWN_CAPABILITIES


@dataclass(frozen=True)
class Capability:
    """One detected runtime capability."""

    name: str
    available: bool
    detail: str
    source: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass(frozen=True)
class CapabilityReport:
    """All capabilities detected for a run."""

    capabilities: tuple[Capability, ...]

    def get(self, name: str) -> Capability:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return Capability(name, False, "unknown capability", "unknown")

    def available(self, name: str) -> bool:
        return self.get(name).available

    def as_dict(self) -> dict:
        return {"capabilities": [cap.as_dict() for cap in self.capabilities]}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    def render(self) -> str:
        lines = ["keel capabilities"]
        for cap in self.capabilities:
            status = "yes" if cap.available else "no"
            lines.append(f"  {cap.name:<18} {status:<3}  {cap.detail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CapabilityRequirement:
    """Capabilities needed by a command or extension."""

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    def merged(self, other: CapabilityRequirement) -> CapabilityRequirement:
        return CapabilityRequirement(
            required=_unique((*self.required, *other.required)),
            optional=_unique((*self.optional, *other.optional)),
        )

    def as_dict(self) -> dict:
        return {"required": list(self.required), "optional": list(self.optional)}


@dataclass(frozen=True)
class CapabilityEvaluation:
    """A requirement checked against a capability report."""

    requirement: CapabilityRequirement
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_required

    def as_dict(self) -> dict:
        return {
            "required": list(self.requirement.required),
            "optional": list(self.requirement.optional),
            "missing_required": list(self.missing_required),
            "missing_optional": list(self.missing_optional),
            "ok": self.ok,
        }

    def render(self) -> str:
        lines = [
            "runtime capabilities:",
            f"  required: {', '.join(self.requirement.required) or '-'}",
            f"  optional: {', '.join(self.requirement.optional) or '-'}",
        ]
        if self.missing_required:
            lines.append(f"  missing required: {', '.join(self.missing_required)}")
        if self.missing_optional:
            lines.append(f"  degraded optional: {', '.join(self.missing_optional)}")
        return "\n".join(lines)


def detect(
    root: str | Path = ".",
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., object] | None = None,
) -> CapabilityReport:
    """Detect capabilities for the current runtime.

    Environment overrides intentionally use generic keel names so projects can surface
    host-agent capabilities without hardcoding one consumer's tooling into core.
    """

    env = os.environ if env is None else env
    if run is None:
        from .runner import run_argv

        run = run_argv
    root_path = Path(root)
    sh = which("sh")
    git = which("git")
    gh = which("gh")
    adb = _tool_capability("adb", env_name="KEEL_ADB", env=env, which=which)
    firebase = _tool_capability("firebase", env_name="KEEL_FIREBASE", env=env, which=which)
    filesystem_write = _can_write(root_path)
    gh_auth = False
    gh_auth_detail = "gh not available"
    if gh:
        result = run(["gh", "auth", "status"], cwd=str(root_path), timeout=10)
        gh_auth = bool(getattr(result, "ok", False))
        gh_auth_detail = "authenticated" if gh_auth else "gh auth status failed"

    caps = (
        Capability("shell", sh is not None, sh or "sh not found", "PATH"),
        Capability("git", git is not None, git or "git not found", "PATH"),
        Capability("gh", gh is not None, gh or "gh not found", "PATH"),
        Capability("gh-auth", gh_auth, gh_auth_detail, "gh auth status"),
        Capability(
            "github-mcp", _truthy(env.get("KEEL_GITHUB_MCP")), "KEEL_GITHUB_MCP", "environment"
        ),
        Capability(
            "subagents", _truthy(env.get("KEEL_SUBAGENTS")), "KEEL_SUBAGENTS", "environment"
        ),
        Capability(
            "parallel-subagents",
            _truthy(env.get("KEEL_PARALLEL_SUBAGENTS")),
            "KEEL_PARALLEL_SUBAGENTS",
            "environment",
        ),
        Capability("browser", _truthy(env.get("KEEL_BROWSER")), "KEEL_BROWSER", "environment"),
        adb,
        firebase,
        Capability(
            "filesystem-write",
            filesystem_write,
            "root writable" if filesystem_write else "root not writable",
            "filesystem",
        ),
        Capability(
            "worktree",
            git is not None and filesystem_write,
            "requires git and writable root",
            "derived",
        ),
        Capability(
            "release-publish",
            _truthy(env.get("KEEL_RELEASE_PUBLISH")),
            "KEEL_RELEASE_PUBLISH",
            "environment",
        ),
        Capability(
            "secret-access",
            _truthy(env.get("KEEL_SECRET_ACCESS")),
            "KEEL_SECRET_ACCESS",
            "environment",
        ),
        _api_token_capability(env),
        Capability(
            "production-adjacent",
            _truthy(env.get("KEEL_PRODUCTION_ADJACENT")),
            "KEEL_PRODUCTION_ADJACENT",
            "environment",
        ),
        Capability(
            "private-setup",
            _truthy(env.get("KEEL_PRIVATE_SETUP")),
            "KEEL_PRIVATE_SETUP",
            "environment",
        ),
    )
    return CapabilityReport(caps)


def evaluate(requirement: CapabilityRequirement, report: CapabilityReport) -> CapabilityEvaluation:
    """Check required and optional capabilities against a report."""

    missing_required = tuple(name for name in requirement.required if not report.available(name))
    missing_optional = tuple(name for name in requirement.optional if not report.available(name))
    return CapabilityEvaluation(requirement, missing_required, missing_optional)


def validate_names(names: tuple[str, ...] | list[str], *, source: str) -> list[str]:
    """Return errors for unknown capability names."""

    return capabilities.validate_names(names, source=source)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _tool_capability(
    name: str,
    *,
    env_name: str,
    env: Mapping[str, str],
    which: Callable[[str], str | None],
) -> Capability:
    if _truthy(env.get(env_name)):
        return Capability(name, True, env_name, "environment")
    path = which(name)
    return Capability(name, path is not None, path or f"{name} not found", "PATH")


def _api_token_capability(env: Mapping[str, str]) -> Capability:
    """``api-token``: a hosted-API delegate key is present in the environment.

    The detail names the env vars found (never their values); the per-vendor
    dispatch check is :func:`keel.api_delegate.has_api_token`.
    """
    from .api_delegate import present_key_names

    names = present_key_names(_env=env)
    detail = (
        ", ".join(names)
        if names
        else "no vendor API key (ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY)"
    )
    return Capability("api-token", bool(names), detail, "environment")


def _can_write(root: Path) -> bool:
    if not root.exists():
        return False
    return os.access(root, os.W_OK)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def build_capability_requirement(
    command: str,
    config,
    loaded: dict[str, list],
    *,
    pr: int | None = None,
) -> CapabilityRequirement:
    """Build the runtime capability requirement for a given command, config,
    and loaded extensions.
    """
    from . import gates, project_commands

    del pr
    req = CapabilityRequirement(
        required=config.knobs.required_capabilities,
        optional=config.knobs.optional_capabilities,
    )
    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError:
        return req
    if project_command := project_commands.get_project_command(config, command):
        req = req.merged(
            CapabilityRequirement(
                required=project_command.required_capabilities,
                optional=project_command.optional_capabilities,
            )
        )

    command_gate_commands = {
        "run-gates",
        "ship",
        "pr-loop",
        "wrap",
        "work-block",
        "overnight",
        "implement",
        "coverage",
        "deps-audit",
        "flake-audit",
    }
    if command in command_gate_commands and any(s.kind == "command" for s in specs):
        req = req.merged(CapabilityRequirement(required=("shell",)))
    worktree_commands = {"ship", "pr-loop", "wrap", "work-block", "overnight", "implement"}
    github_read_commands = {
        "morning",
        "review-cycle",
        "triage",
        "stale-prs",
        "regression",
        "review-all-day",
        "coverage",
        "deps-audit",
        "flake-audit",
        "ci-check",
    }
    if command in worktree_commands:
        req = req.merged(
            CapabilityRequirement(required=("git", "worktree"), optional=("gh", "gh-auth"))
        )
    elif command in github_read_commands:
        req = req.merged(CapabilityRequirement(optional=("gh", "gh-auth")))
    for spec in specs:
        if spec.required_capabilities or spec.optional_capabilities:
            req = req.merged(
                CapabilityRequirement(
                    required=spec.required_capabilities,
                    optional=spec.optional_capabilities,
                )
            )
    return req


def ci_check_capability_requirement(config) -> CapabilityRequirement:
    """Capability requirements for ci-check command."""
    optional = ["gh", "gh-auth"]
    if config.knobs.ci_workflows:
        optional.append("raw-actions-logs")
    return CapabilityRequirement(optional=tuple(optional))


def morning_capability_requirement(config) -> CapabilityRequirement:
    """Capability requirements for morning command."""
    required: list[str] = []
    optional: list[str] = ["gh", "gh-auth"]
    pack = config.policy_pack or {}
    health = pack.get("health_providers") if isinstance(pack.get("health_providers"), dict) else {}
    for provider in health.values():
        if not isinstance(provider, dict):
            continue
        required.extend(provider.get("required_capabilities") or ())
        optional.extend(provider.get("optional_capabilities") or ())
    return CapabilityRequirement(
        required=tuple(dict.fromkeys(required)),
        optional=tuple(dict.fromkeys(optional)),
    )


def scan_capability_requirement(command: str, config) -> CapabilityRequirement:
    """Capability requirements for scan commands (regression, review-all-day)."""
    del config
    if command == "regression":
        return CapabilityRequirement(
            required=("git", "worktree"),
            optional=("gh", "gh-auth", "github-mcp", "parallel-subagents"),
        )
    return CapabilityRequirement(
        required=("git",),
        optional=("gh", "gh-auth", "github-mcp", "parallel-subagents"),
    )
