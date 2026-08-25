"""Thin, fail-soft ``git`` wrappers (argv, no shell).

These build the exact git command for each backbone operation and run it via the
injectable ``_run`` seam, so the command construction is unit-tested offline; live
behaviour is exercised opt-in against a real repo. Each returns a
:class:`keel.runner.CommandResult` (or a parsed value), never raising.
"""

from __future__ import annotations

import re

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
    return result.stdout.strip() if result.ok else None


def list_branches(*, cwd: str | None = None, _run=None) -> CommandResult:
    """List local + remote branch short names (one per line) as a ``CommandResult``.

    Returns the raw result (like :func:`worktree_list`) rather than a parsed
    fail-soft list, so a caller that needs to *distinguish a git error from an
    empty repo* — e.g. dry-run integrity verification, which must fail closed
    when it cannot observe — can inspect ``result.ok``. Parsing is the caller's.
    """
    return run_argv(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
        cwd=cwd,
        **_kw(_run),
    )


#: A 40- or 64-hex object name (SHA-1 / SHA-256). git may print a ``warning:`` to
#: stderr while still succeeding; reading ``stdout`` avoids the contamination, and
#: validating the shape is a second line of defence so a stray token never poses as a SHA.
_SHA_RE = re.compile(r"\A[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def rev_parse(ref: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Resolve ``ref`` to a full commit SHA; ``None`` when it cannot be resolved."""
    result = run_argv(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd, **_kw(_run))
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def merge_base(a: str, b: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Best common ancestor of ``a`` and ``b``; ``None`` when there is none/on error."""
    result = run_argv(["git", "merge-base", a, b], cwd=cwd, **_kw(_run))
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def rev_count(base: str, head: str, *, cwd: str | None = None, _run=None) -> int | None:
    """Commits in ``base..head`` (how far ``head`` is ahead of ``base``); ``None`` on error."""
    result = run_argv(["git", "rev-list", "--count", f"{base}..{head}"], cwd=cwd, **_kw(_run))
    if not result.ok:
        return None
    output = result.stdout.strip()
    if not output.isdigit():
        return None
    return int(output)


def changed_files(base: str, head: str, *, cwd: str | None = None, _run=None) -> list[str] | None:
    """Files changed between ``base`` and ``head`` (``base...head``).

    ``None`` when the git command failed — deliberately distinct from ``[]`` (the
    command ran and there were no changes), so a caller classifying risk or checking
    scope can tell "could not read the diff" apart from "the diff is empty" instead of
    treating an unreadable diff as a clean, empty one.
    """
    result = run_argv(["git", "diff", "--name-only", f"{base}...{head}"], cwd=cwd, **_kw(_run))
    if not result.ok:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def diff(base: str, head: str, *, cwd: str | None = None, _run=None) -> str | None:
    """The unified diff between ``base`` and ``head`` (``base...head``).

    ``None`` when the git command failed — distinct from ``""`` (the command ran and
    the diff is empty), so a review/gate caller can refuse to treat an unreadable diff
    as "nothing to review".
    """
    result = run_argv(["git", "diff", f"{base}...{head}"], cwd=cwd, **_kw(_run))
    return result.stdout if result.ok else None


def _kw(_run):
    """Pass ``_run`` through only when provided (so the default subprocess is used otherwise)."""
    return {"_run": _run} if _run is not None else {}
