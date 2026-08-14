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
