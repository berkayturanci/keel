"""Thin, fail-soft ``gh`` (GitHub CLI) wrappers (argv, no shell).

Like :mod:`keel.git`, these build the exact ``gh`` command for each backbone
operation and run it via the injectable ``_run`` seam. Command construction is
unit-tested offline; live behaviour is opt-in.
"""

from __future__ import annotations

from .runner import CommandResult, run_argv


def open_pr(
    title: str, body: str, base: str, head: str, *, cwd: str | None = None, _run=None
) -> CommandResult:
    return run_argv(
        ["gh", "pr", "create", "--title", title, "--body", body, "--base", base, "--head", head],
        cwd=cwd, **_kw(_run),
    )


def ci_conclusion(pr: int | str, *, cwd: str | None = None, _run=None) -> str | None:
    """Return the PR's check-rollup state (e.g. SUCCESS/FAILURE/PENDING), or ``None``."""
    result = run_argv(
        ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup",
         "--jq", "[.statusCheckRollup[].conclusion] | unique | join(\",\")"],
        cwd=cwd, **_kw(_run),
    )
    if not result.ok:
        return None
    return result.output.strip() or None


def merged_prs(
    *, search: str | None = None, limit: int = 100, cwd: str | None = None, _run=None
) -> CommandResult:
    """List recently-merged PR numbers as a JSON array (``[{"number": N}, ...]``).

    Thin I/O for ``capture-verify`` transport derivation: the authoritative
    merged-PR set is read from the host instead of trusting the agent's args.
    ``search`` narrows the set (e.g. ``"merged:>=2026-06-01"``). Fail-soft —
    the caller inspects ``result.ok`` and degrades gracefully when offline.
    """
    argv = ["gh", "pr", "list", "--state", "merged", "--limit", str(limit), "--json", "number"]
    if search:
        argv += ["--search", search]
    return run_argv(argv, cwd=cwd, **_kw(_run))


def pr_merge_snapshot(pr: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(
        [
            "gh", "pr", "view", str(pr),
            "--json", "headRefOid,mergeStateStatus,statusCheckRollup",
        ],
        cwd=cwd, **_kw(_run),
    )


def merge_pr(
    pr: int | str, *, method: str = "squash", cwd: str | None = None, _run=None
) -> CommandResult:
    return run_argv(["gh", "pr", "merge", str(pr), f"--{method}"], cwd=cwd, **_kw(_run))


def comment(pr: int | str, body: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["gh", "pr", "comment", str(pr), "--body", body], cwd=cwd, **_kw(_run))


def post_issue_comment(
    owner_repo: str,
    issue_or_pr: int | str,
    body: str,
    *,
    cwd: str | None = None,
    _run=None,
) -> CommandResult:
    return run_argv(
        [
            "gh",
            "api",
            f"repos/{owner_repo}/issues/{issue_or_pr}/comments",
            "-X",
            "POST",
            "-f",
            f"body={body}",
        ],
        cwd=cwd,
        **_kw(_run),
    )


def edit_issue_comment(
    owner_repo: str,
    comment_id: int | str,
    body: str,
    *,
    cwd: str | None = None,
    _run=None,
) -> CommandResult:
    return run_argv(
        [
            "gh",
            "api",
            f"repos/{owner_repo}/issues/comments/{comment_id}",
            "-X",
            "PATCH",
            "-f",
            f"body={body}",
        ],
        cwd=cwd,
        **_kw(_run),
    )


def close_issue(issue: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["gh", "issue", "close", str(issue)], cwd=cwd, **_kw(_run))


def issue_facts(issue: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    """Fetch an issue's ``title`` and ``labels`` as JSON for ``keel guard``.

    Thin I/O for blocker evaluation: the issue facts are read from the host
    rather than trusting agent-supplied args. Fail-soft — the caller inspects
    ``result.ok`` and falls back to offline args when offline.
    """
    return run_argv(
        ["gh", "issue", "view", str(issue), "--json", "title,labels"],
        cwd=cwd, **_kw(_run),
    )


def _kw(_run):
    return {"_run": _run} if _run is not None else {}
