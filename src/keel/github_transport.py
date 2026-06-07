"""GitHub transport selection and normalized operation capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from .runtime import CapabilityReport

OPERATIONS: tuple[str, ...] = (
    "issue_read",
    "issue_write",
    "pr_read",
    "pr_write",
    "pr_merge",
    "check_runs",
    "raw_actions_logs",
    "labels",
    "comments",
    "reviews",
    "files",
)

NORMALIZED_FIELDS: tuple[str, ...] = (
    "issue_labels",
    "pr_state",
    "draft_state",
    "mergeable_state",
    "check_runs",
    "comments",
    "reviews",
    "files",
    "merge_operations",
)


@dataclass(frozen=True)
class GitHubTransport:
    """Selected GitHub access path plus normalized operation support."""

    name: str
    available: bool
    capabilities: dict[str, bool]
    degraded: tuple[str, ...] = ()
    reason: str = ""

    def supports(self, operation: str) -> bool:
        return self.capabilities.get(operation, False)

    def as_dict(self) -> dict:
        return {
            "transport": self.name,
            "available": self.available,
            "capabilities": dict(self.capabilities),
            "degraded": list(self.degraded),
            "normalized_fields": list(NORMALIZED_FIELDS),
            "reason": self.reason,
        }

    def render(self) -> str:
        lines = [
            "github transport:",
            f"  selected: {self.name}",
            f"  available: {'yes' if self.available else 'no'}",
        ]
        if self.reason:
            lines.append(f"  reason: {self.reason}")
        if self.degraded:
            lines.append(f"  degraded: {', '.join(self.degraded)}")
        return "\n".join(lines)


def resolve(report: CapabilityReport) -> GitHubTransport:
    """Resolve the preferred GitHub transport from runtime capabilities.

    Local authenticated `gh` wins. When `gh` is unavailable, host-provided GitHub
    MCP/API access can still support read/comment/list operations, while operations with
    known gaps are surfaced as degraded capabilities.
    """

    if report.available("gh") and report.available("gh-auth"):
        return GitHubTransport(
            "gh",
            True,
            _caps(True),
            reason="authenticated GitHub CLI",
        )
    if report.available("github-mcp"):
        capabilities = _caps(True)
        degraded = ("pr_merge", "check_runs", "raw_actions_logs")
        for name in degraded:
            capabilities[name] = False
        return GitHubTransport(
            "mcp",
            True,
            capabilities,
            degraded=degraded,
            reason="GitHub MCP/API capability reported by runtime",
        )
    return GitHubTransport(
        "none",
        False,
        _caps(False),
        degraded=OPERATIONS,
        reason="no authenticated GitHub transport detected",
    )


def _caps(value: bool) -> dict[str, bool]:
    return {name: value for name in OPERATIONS}
