"""Thin, fail-soft ``git`` wrappers (argv, no shell).

These build the exact git command for each backbone operation and run it via the
injectable ``_run`` seam, so the command construction is unit-tested offline; live
behaviour is exercised opt-in against a real repo. Each returns a
:class:`keel.runner.CommandResult` (or a parsed value), never raising.
"""

from __future__ import annotations

from .runner import CommandResult, run_argv


def fetch(remote: str, ref: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["git", "fetch", remote, ref, "--quiet"], cwd=cwd, **_kw(_run))


def worktree_add(
    base: str, branch: str, path: str, *, cwd: str | None = None, _run=None
) -> CommandResult:
    return run_argv(["git", "worktree", "add", "-b", branch, path, base], cwd=cwd, **_kw(_run))


def worktree_remove(path: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["git", "worktree", "remove", path, "--force"], cwd=cwd, **_kw(_run))


def worktree_list(*, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["git", "worktree", "list", "--porcelain"], cwd=cwd, **_kw(_run))


def current_branch(*, cwd: str | None = None, _run=None) -> str | None:
    result = run_argv(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, **_kw(_run))
    return result.output.strip() if result.ok else None


def rev_parse(ref: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Resolve ``ref`` to a full commit SHA; ``None`` when it cannot be resolved."""
    result = run_argv(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd, **_kw(_run))
    output = result.output.strip()
    return output if result.ok and output else None


def merge_base(a: str, b: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Best common ancestor of ``a`` and ``b``; ``None`` when there is none/on error."""
    result = run_argv(["git", "merge-base", a, b], cwd=cwd, **_kw(_run))
    output = result.output.strip()
    return output if result.ok and output else None


def rev_count(base: str, head: str, *, cwd: str | None = None, _run=None) -> int | None:
    """Commits in ``base..head`` (how far ``head`` is ahead of ``base``); ``None`` on error."""
    result = run_argv(
        ["git", "rev-list", "--count", f"{base}..{head}"], cwd=cwd, **_kw(_run)
    )
    if not result.ok:
        return None
    output = result.output.strip()
    if not output.isdigit():
        return None
    return int(output)


def changed_files(base: str, head: str, *, cwd: str | None = None, _run=None) -> list[str]:
    """Files changed between ``base`` and ``head`` (``base...head``); ``[]`` on error."""
    result = run_argv(["git", "diff", "--name-only", f"{base}...{head}"], cwd=cwd, **_kw(_run))
    if not result.ok:
        return []
    return [line for line in result.output.splitlines() if line.strip()]


def diff(base: str, head: str, *, cwd: str | None = None, _run=None) -> str:
    """The unified diff between ``base`` and ``head`` (``base...head``); ``""`` on error."""
    result = run_argv(["git", "diff", f"{base}...{head}"], cwd=cwd, **_kw(_run))
    return result.output if result.ok else ""


def _kw(_run):
    """Pass ``_run`` through only when provided (so the default subprocess is used otherwise)."""
    return {"_run": _run} if _run is not None else {}
