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

KNOWN_CAPABILITIES: tuple[str, ...] = (
    "shell",
    "git",
    "gh",
    "gh-auth",
    "github-mcp",
    "subagents",
    "parallel-subagents",
    "browser",
    "filesystem-write",
    "worktree",
    "release-publish",
    "secret-access",
    "production-adjacent",
    "private-setup",
)

MUTATING_CAPABILITIES: tuple[str, ...] = (
    "filesystem-write",
    "worktree",
    "release-publish",
    "secret-access",
    "production-adjacent",
)


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
        Capability("github-mcp", _truthy(env.get("KEEL_GITHUB_MCP")),
                   "KEEL_GITHUB_MCP", "environment"),
        Capability("subagents", _truthy(env.get("KEEL_SUBAGENTS")),
                   "KEEL_SUBAGENTS", "environment"),
        Capability("parallel-subagents", _truthy(env.get("KEEL_PARALLEL_SUBAGENTS")),
                   "KEEL_PARALLEL_SUBAGENTS", "environment"),
        Capability("browser", _truthy(env.get("KEEL_BROWSER")),
                   "KEEL_BROWSER", "environment"),
        Capability("filesystem-write", filesystem_write,
                   "root writable" if filesystem_write else "root not writable", "filesystem"),
        Capability("worktree", git is not None and filesystem_write,
                   "requires git and writable root", "derived"),
        Capability("release-publish", _truthy(env.get("KEEL_RELEASE_PUBLISH")),
                   "KEEL_RELEASE_PUBLISH", "environment"),
        Capability("secret-access", _truthy(env.get("KEEL_SECRET_ACCESS")),
                   "KEEL_SECRET_ACCESS", "environment"),
        Capability("production-adjacent", _truthy(env.get("KEEL_PRODUCTION_ADJACENT")),
                   "KEEL_PRODUCTION_ADJACENT", "environment"),
        Capability("private-setup", _truthy(env.get("KEEL_PRIVATE_SETUP")),
                   "KEEL_PRIVATE_SETUP", "environment"),
    )
    return CapabilityReport(caps)


def evaluate(requirement: CapabilityRequirement, report: CapabilityReport) -> CapabilityEvaluation:
    """Check required and optional capabilities against a report."""

    missing_required = tuple(name for name in requirement.required if not report.available(name))
    missing_optional = tuple(name for name in requirement.optional if not report.available(name))
    return CapabilityEvaluation(requirement, missing_required, missing_optional)


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


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _can_write(root: Path) -> bool:
    if not root.exists():
        return False
    return os.access(root, os.W_OK)


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return tuple(out)
